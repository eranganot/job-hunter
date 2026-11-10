"""
entitlements.py - what a user's plan lets them do.

One module, because the alternative is `if role == "admin" or plan in (...)`
scattered across routes and a UI that disagrees with the server about who may
do what. Every gate in the app answers from here; the numbers live in plans.py.

The plans (plans.py, approved 2026-11-05):
    free / premium / beta / expert - limits and features per plan
                              (beta = Premium's limits, no end date; 2026-11-09)
    admin                   - a ROLE, not a plan: checked separately, so the owner's
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

    Since 2026-11-08 every plan may (The owner: the users decide, in Settings; off
    until they do). The check stays at the chokepoint for a plan that drops it
    later. Note this answers only "is it allowed to be ON" -
    whether the engine then does anything is a separate switch
    (APPLY_ENGINE_ENABLED), and the user's own toggle is a third. All three
    have to agree before a form is ever submitted.
    """
    if not user:                      # no account, no applying on its behalf
        return False
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
    if _has_locked_jobs_table(conn):
        unlock_matches(conn, user_id, plan)
    return {"plan": plan, "plan_until": until, "plan_after": after if days else None,
            "plan_source": source}


def _has_locked_jobs_table(conn) -> bool:
    """search_batch arrives with migration 16; before it there is nothing to unlock."""
    try:
        conn.execute("SELECT search_batch FROM jobs WHERE 1=0").fetchall()
        return True
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return False


def start_new_user_plan(conn, user_id: int, now: datetime | None = None) -> dict:
    """Every new account joins on the Beta plan, with no end date (The owner,
    2026-11-09; it replaced the 30-day Premium promotion)."""
    return grant_plan(conn, user_id, plans.NEW_USER_PLAN, days=None, reason="beta", now=now)


def move_promotions_to_beta(conn, now: datetime | None = None) -> list:
    """Every non-admin account on a running PREMIUM promotion moves to Beta with
    no end date (The owner, 2026-11-09: "move them to Beta too"). Logged as
    'beta_migration'. Only Premium: Beta has Premium's limits, so moving an
    Expert promotion would be a downgrade. Promotions that already ended,
    admin-set plans and admins are left alone.
    Idempotent: a moved account is no longer a running promotion."""
    now = now or _utcnow()
    rows = conn.execute(
        "SELECT id FROM users WHERE plan_source='promotion' AND plan=? AND plan_until IS NOT NULL "
        "AND plan_until > ? AND LOWER(COALESCE(role, 'user')) != 'admin'",
        (plans.PROMOTION_PLAN, _stamp(now))).fetchall()
    for r in rows:
        grant_plan(conn, r[0], plans.NEW_USER_PLAN, days=None, reason="beta_migration", now=now)
    return [r[0] for r in rows]


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


def allowed(conn, user, meter: str, n: int = 1, now: datetime | None = None) -> tuple:
    """(ok, used, limit) without counting - for a route that must first know
    whether the work will happen (a search already in flight costs nothing)."""
    cap = limit(user, meter)
    have = used(conn, _get(user, "id"), meter, now)
    return (cap is None or have + n <= cap), have, cap


# Called with (reason, user) whenever a plan refusal is built - app.py records
# the funnel's "paywall" step here, so no route has to remember to.
ON_UPGRADE_REQUIRED = []


def upgrade_required(reason: str, user, **extra) -> dict:
    """The one body every plan refusal sends (HTTP 402), so the UI can show the
    same upgrade prompt wherever a limit is hit."""
    for fn in ON_UPGRADE_REQUIRED:
        try:
            fn(reason, user)
        except Exception as exc:   # a listener must never turn a 402 into a 500
            print("[entitlements] upgrade_required listener failed: %s" % exc)
    body = {"error": extra.pop("message", "This needs a higher plan."), "code": "upgrade_required",
            "reason": reason, "plan": tier_of(user)}
    body.update(extra)
    return body


# ── matches shown per search (the blurred rest) ────────────────────────────
#
# A search's new jobs beyond the plan's matches_per_search are stored with
# status 'locked'. Every queue view filters on status, so a locked job is
# invisible to them all; the routes that act on a job by id refuse a locked one
# (app.py). Only the COUNT of locked jobs is ever sent to the browser, so a
# blurred card cannot be read from the page's data. jobs.search_batch groups a
# search's jobs, so an upgrade can unlock per search.

_RANK = "COALESCE(match_score, 0) - COALESCE(feedback_penalty, 0)"


def lock_matches_beyond_limit(conn, user, after_id: int, batch: int) -> list:
    """Tag the jobs this search inserted (id > after_id, status 'new') with
    `batch`, keep the plan's top N by score, lock the rest. Returns the locked
    rows as [(id, url)]."""
    uid = _get(user, "id")
    rows = conn.execute(
        "SELECT id, url FROM jobs WHERE user_id=? AND id>? AND status='new' "
        "ORDER BY " + _RANK + " DESC, id ASC", (uid, after_id)).fetchall()
    if not rows:
        return []
    ids = [r[0] for r in rows]
    conn.execute("UPDATE jobs SET search_batch=? WHERE id IN (%s)" % ",".join("?" * len(ids)),
                 [batch] + ids)
    cap = limit(user, "matches_per_search")
    locked = [] if cap is None else [(r[0], r[1]) for r in rows[cap:]]
    if locked:
        conn.execute("UPDATE jobs SET status='locked' WHERE id IN (%s)" % ",".join("?" * len(locked)),
                     [i for i, _u in locked])
    conn.commit()
    return locked


def unlock_matches(conn, user_id: int, tier: str) -> int:
    """After a move to `tier`, unlock locked jobs up to the new per-search
    limit, best first, batch by batch. A downgrade never re-locks anything the
    user has already seen. Returns how many were unlocked."""
    cap = plans.row(tier).get("matches_per_search")
    if cap is None:
        n = conn.execute("UPDATE jobs SET status='new' WHERE user_id=? AND status='locked'",
                         (user_id,)).rowcount
        conn.commit()
        return max(n or 0, 0)
    opened = 0
    batches = [r[0] for r in conn.execute(
        "SELECT DISTINCT search_batch FROM jobs WHERE user_id=? AND status='locked'", (user_id,))]
    for b in batches:
        shown = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE user_id=? AND search_batch=? AND status!='locked'",
            (user_id, b)).fetchone()[0]
        room = cap - shown
        if room <= 0:
            continue
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM jobs WHERE user_id=? AND search_batch=? AND status='locked' "
            "ORDER BY " + _RANK + " DESC, id ASC", (user_id, b)).fetchall()[:room]]
        if ids:
            conn.execute("UPDATE jobs SET status='new' WHERE id IN (%s)" % ",".join("?" * len(ids)), ids)
            opened += len(ids)
    conn.commit()
    return opened


def locked_summary(conn, user_id: int) -> dict:
    """Counts only - never a title, company or link."""
    total = conn.execute("SELECT COUNT(*) FROM jobs WHERE user_id=? AND status='locked'",
                         (user_id,)).fetchone()[0]
    last = conn.execute(
        "SELECT search_batch, COUNT(*) FROM jobs WHERE user_id=? AND status='locked' "
        "GROUP BY search_batch ORDER BY search_batch DESC LIMIT 1", (user_id,)).fetchone()
    return {"count": int(total or 0),
            "latest_search": {"batch": last[0], "count": int(last[1])} if last else None}


def is_locked(conn, user_id: int, job_id: int) -> bool:
    row = conn.execute("SELECT status FROM jobs WHERE id=? AND user_id=?", (job_id, user_id)).fetchone()
    return bool(row) and row[0] == "locked"


# ── history kept (Step 6, the owner 2026-11-05: hidden, not deleted) ───────────
#
# Free keeps 30 days of history, Premium 12 months, Expert and admin all of it.
# Older history is HIDDEN at read time - nothing is deleted, so an upgrade
# brings it straight back and the export still holds everything. History means
# jobs already dealt with (applied, passed, deferred, expired) and the activity
# log; the open queue (new, approved) is current, never history.

HISTORY_STATUSES = ("applied", "rejected", "deferred", "expired")
HISTORY_DATE_SQL = "COALESCE(NULLIF(applied_date, ''), found_date, '')"


def history_cutoff(user, now: datetime | None = None):
    """'YYYY-MM-DD' - history older than this is hidden; None = keep all."""
    days = limit(user, "history_days")
    if days is None:
        return None
    return ((now or _utcnow()) - timedelta(days=days)).astimezone(_LOCAL).strftime("%Y-%m-%d")
