"""
entitlements.py - what a user's plan lets them do.

One module, because the alternative is `if role == "admin" or plan in (...)`
scattered across routes and a UI that disagrees with the server about who may
do what. Every gate in the app answers from here; the numbers live in plans.py.

The plans (plans.py, approved 2026-11-05):
    free / premium / expert - limits and features per plan
    admin                   - a ROLE, not a plan: checked separately, so Eran's
                              own access never depends on a plan column.

A plan can carry an end date (a promotion): users.plan_until (UTC
'YYYY-MM-DD HH:MM:SS') and users.plan_after (the plan it falls back to). Every
read honours the end date itself, so a promotion that has run out is over at
that second even if the hourly sweep that rewrites the row has not run yet -
it fails CLOSED, like an unknown plan name.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import plans

PLANS = plans.PLAN_NAMES
PAID_PLANS = plans.PAID_PLANS

try:
    from zoneinfo import ZoneInfo
    _LOCAL = ZoneInfo("Asia/Jerusalem")
except Exception:                      # pragma: no cover - tz database missing
    _LOCAL = timezone(timedelta(hours=2))

_FMT = "%Y-%m-%d %H:%M:%S"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(_FMT)


def _parse(value):
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:19].replace("T", " "), _FMT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _get(user, key):
    if not user:
        return None
    try:
        return user.get(key) if hasattr(user, "get") else user[key]
    except (KeyError, IndexError):
        return None


def _norm(raw) -> str:
    plan = (raw or "").strip().lower()
    return plan if plan in PLANS else "free"


def plan_of(user, now: datetime | None = None) -> str:
    """The user's plan right now, normalised. Anything unrecognised reads as
    'free'; a promotion past its end reads as the plan it falls back to.

    Fail CLOSED: an unknown or empty value must not open a paid feature. A
    typo in a plan name should cost someone a feature they can ask about, not
    silently hand out the thing that costs money to run.
    """
    if not user:
        return "free"
    until = _parse(_get(user, "plan_until"))
    if until is not None and (now or _utcnow()) >= until:
        return _norm(_get(user, "plan_after"))
    return _norm(_get(user, "plan"))


def is_admin(user) -> bool:
    if not user:
        return False
    role = _get(user, "role") or ""
    return role.strip().lower() == "admin"


def tier_of(user, now: datetime | None = None) -> str:
    """'admin' for the admin role, else the plan - the row of plans.py that applies."""
    return "admin" if is_admin(user) else plan_of(user, now)


def limit(user, key):
    """The user's limit or feature value for a plans.py key. None = unlimited."""
    return plans.row(tier_of(user)).get(key)


def can_auto_apply(user) -> bool:
    """May this user have the app apply on their behalf without asking?

    Per the approved matrix, auto-apply is not sold while it is parked: admin
    only, whatever the plan. Note this answers only "is it allowed to be ON" -
    whether the engine then does anything is a separate switch
    (APPLY_ENGINE_ENABLED), and the user's own toggle is a third. All three
    have to agree before a form is ever submitted.
    """
    return bool(plans.row(tier_of(user)).get("auto_apply"))


# ── usage counters ─────────────────────────────────────────────────────────

def period_key(meter: str, now: datetime | None = None) -> str:
    """The counting period for a meter, in Israel time (a user's "today")."""
    local = (now or _utcnow()).astimezone(_LOCAL)
    return local.strftime("%Y-%m-%d") if plans.METERS.get(meter) == "day" else local.strftime("%Y-%m")


def used(conn, user_id: int, meter: str, now: datetime | None = None) -> int:
    row = conn.execute(
        "SELECT uses FROM usage_counters WHERE user_id=? AND meter=? AND period=?",
        (user_id, meter, period_key(meter, now))).fetchone()
    return int(row[0]) if row else 0


def try_consume(conn, user, meter: str, n: int = 1, now: datetime | None = None):
    """Count `n` uses of `meter` if the user's plan allows them.

    Returns (allowed, used_after, limit). A refused use is not counted. An
    unlimited plan is counted too - that is the usage data pricing is set from.
    Read-then-write: two simultaneous requests can both pass at the last unit,
    which at this scale costs one extra use, never a 500.
    """
    if meter not in plans.METERS:
        raise ValueError("unknown meter %r" % meter)
    cap = limit(user, meter)
    uid = _get(user, "id")
    have = used(conn, uid, meter, now)
    if cap is not None and have + n > cap:
        return False, have, cap
    conn.execute(
        "INSERT INTO usage_counters (user_id, meter, period, uses, updated_date) VALUES (?,?,?,?,?) "
        "ON CONFLICT (user_id, meter, period) DO UPDATE SET "
        "uses = usage_counters.uses + excluded.uses, updated_date = excluded.updated_date",
        (uid, meter, period_key(meter, now), n, _stamp(now or _utcnow())))
    conn.commit()
    return True, have + n, cap


def usage_summary(conn, user, now: datetime | None = None) -> dict:
    """{meter: {used, limit, period}} for /api/me and the UI meters."""
    uid = _get(user, "id")
    return {m: {"used": used(conn, uid, m, now), "limit": limit(user, m), "period": per}
            for m, per in plans.METERS.items()}


# ── granting plans and promotions ──────────────────────────────────────────

def grant_plan(conn, user_id: int, plan: str, days: int | None = None, after: str = "free",
               reason: str = "admin", by_user_id: int | None = None,
               now: datetime | None = None) -> dict:
    """Put a user on `plan`, for `days` (a promotion that then falls back to
    `after`) or permanently (days=None). Every change is recorded in
    plan_events. Returns the new {plan, plan_until, plan_after, plan_source}."""
    plan = (plan or "").strip().lower()
    if plan not in PLANS:
        raise ValueError("unknown plan %r" % plan)
    after = _norm(after)
    now = now or _utcnow()
    until = _stamp(now + timedelta(days=days)) if days else None
    prev = conn.execute("SELECT plan FROM users WHERE id=?", (user_id,)).fetchone()
    if prev is None:
        raise ValueError("no user %s" % user_id)
    source = "promotion" if days else reason
    conn.execute(
        "UPDATE users SET plan=?, plan_until=?, plan_after=?, plan_source=? WHERE id=?",
        (plan, until, after if days else None, source, user_id))
    _event(conn, user_id, prev[0], plan, until, reason, by_user_id, now)
    conn.commit()
    return {"plan": plan, "plan_until": until, "plan_after": after if days else None,
            "plan_source": source}


def start_new_user_promotion(conn, user_id: int, now: datetime | None = None) -> dict:
    """Every new account starts on Premium free for 30 days, then Free."""
    return grant_plan(conn, user_id, plans.PROMOTION_PLAN, days=plans.PROMOTION_DAYS,
                      after="free", reason="new_user", now=now)


def _event(conn, user_id, from_plan, to_plan, until, reason, by_user_id, now):
    conn.execute(
        "INSERT INTO plan_events (user_id, from_plan, to_plan, plan_until, reason, by_user_id, created_date) "
        "VALUES (?,?,?,?,?,?,?)",
        (user_id, from_plan, to_plan, until, reason, by_user_id, _stamp(now)))


def expire_promotions(conn, now: datetime | None = None) -> list:
    """Move every user whose promotion has ended to its fallback plan.
    Returns [(user_id, from_plan, to_plan)]. Idempotent."""
    now = now or _utcnow()
    rows = conn.execute(
        "SELECT id, plan, plan_after FROM users WHERE plan_until IS NOT NULL AND plan_until <= ?",
        (_stamp(now),)).fetchall()
    moved = []
    for r in rows:
        to = _norm(r[2])
        conn.execute(
            "UPDATE users SET plan=?, plan_until=NULL, plan_after=NULL, plan_source=? "
            "WHERE id=? AND plan_until IS NOT NULL AND plan_until <= ?",
            (to, "promotion_ended", r[0], _stamp(now)))
        _event(conn, r[0], r[1], to, None, "promotion_ended", None, now)
        moved.append((r[0], r[1], to))
    conn.commit()
    return moved


def _notice_text(days_left: int, until: datetime, after: str) -> str:
    d = until.astimezone(_LOCAL)
    local = "%d %s" % (d.day, d.strftime("%b"))
    when = "tomorrow" if days_left <= 1 else "in %d days" % days_left
    return ("Your free Premium ends %s (%s). After that you'll be on the %s plan."
            % (when, local, plans.row(after)["label"]))


def due_notices(conn, now: datetime | None = None) -> list:
    """[(user_id, kind, plan_until, message)] for promotion-end notices that are
    due and not yet sent. A notice is due once the end is within its window;
    only the nearest unsent window is returned, so a user whose 7-day window
    was missed (e.g. a 3-day promotion) gets the 1-day one, not both."""
    now = now or _utcnow()
    horizon = _stamp(now + timedelta(days=max(plans.NOTICE_DAYS)))
    rows = conn.execute(
        "SELECT id, plan_until, plan_after FROM users "
        "WHERE plan_source='promotion' AND plan_until IS NOT NULL AND plan_until > ? AND plan_until <= ?",
        (_stamp(now), horizon)).fetchall()
    out = []
    for r in rows:
        until = _parse(r[1])
        if until is None:
            continue
        left = until - now
        windows = [d for d in sorted(plans.NOTICE_DAYS) if left <= timedelta(days=d)]
        if not windows:
            continue
        kind = "promotion_end_%dd" % windows[0]
        sent = conn.execute(
            "SELECT 1 FROM plan_notices WHERE user_id=? AND plan_until=? AND kind=?",
            (r[0], r[1], kind)).fetchone()
        if sent:
            continue
        days_left = max(1, int(-(-left.total_seconds() // 86400)))
        out.append((r[0], kind, r[1], _notice_text(days_left, until, _norm(r[2]))))
    return out


def run_lifecycle(get_db, push, now: datetime | None = None) -> dict:
    """The hourly sweep: send due promotion-end notices, then end the
    promotions that are over. `push(user_id, message)` sends the web push.
    A notice is recorded before it is sent, so a crash between the two costs
    one missed push, never a repeated one."""
    now = now or _utcnow()
    conn = get_db()
    try:
        notices = due_notices(conn, now)
        for uid, kind, until, _msg in notices:
            conn.execute(
                "INSERT INTO plan_notices (user_id, kind, plan_until, sent_date) VALUES (?,?,?,?)",
                (uid, kind, until, _stamp(now)))
        conn.commit()
        moved = expire_promotions(conn, now)
    finally:
        conn.close()
    for uid, _kind, _until, msg in notices:
        try:
            push(uid, msg)
        except Exception as e:
            print("[plans] WARNING promotion notice push failed for user %s: %s" % (uid, e))
    return {"notices": [(u, k) for u, k, _t, _m in notices], "moved": moved}


def plan_info(user, now: datetime | None = None) -> dict:
    """What /api/me tells the UI about the plan."""
    until = _parse(_get(user, "plan_until"))
    now = now or _utcnow()
    active = until is not None and until > now
    return {
        "tier": tier_of(user, now),
        "plan": plan_of(user, now),
        "label": plans.row(tier_of(user, now))["label"],
        "plan_until": _stamp(until) if active else None,
        "plan_after": _norm(_get(user, "plan_after")) if active else None,
        "plan_source": _get(user, "plan_source"),
        "days_left": int(-(-(until - now).total_seconds() // 86400)) if active else None,
        "limits": {k: v for k, v in plans.row(tier_of(user, now)).items() if k != "label"},
    }
