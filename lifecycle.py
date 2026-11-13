"""lifecycle.py - the welcome / day 1 / day 3 / day 7 emails (launch Stage 2,
item 7; the owner's decisions 2026-11-12).

- Templated and automatic, personalised with the first name, and each one
  reacts to where the user is (setup unfinished / picks waiting / approving).
- Noa sends welcome, day 1 and day 7 (NOA_FROM). Day 3 is a personal note
  from Eran (ERAN_FROM, e.g. "Eran from Job Hunter <eran@job-hunter.work>").
- Day-N emails go out at 11:30 Israel time on the Nth day after signup (the
  daily search runs at 11:00 by default, so "today's picks are in" is true).
  A step whose day has passed is skipped, never sent late.
- Only accounts created after this feature shipped (app_flags
  'lifecycle_since'); never the test account, the admin, an account pending
  deletion, or anyone who pressed "stop these emails".
- Off unless LIFECYCLE_EMAILS=1, so the owner approves the copy first.

Every step is recorded once in lifecycle_emails (sent or skipped, with the
variant), so a restart or a second instance never sends twice.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone

STEPS = ("welcome", "day1", "day3", "day7")
DAY_OF = {"day1": 1, "day3": 3, "day7": 7}
SEND_AT = (11, 30)                     # Israel time
WELCOME_AFTER = timedelta(hours=1)     # only if setup is still unfinished by then
WELCOME_UNTIL = timedelta(hours=24)

# The owner's sign-offs (2026-11-13): Noa signs like a letter; Eran as "The human behind Noa".
NOA_SIG = "Your personal AI headhunter agent at Job Hunter,\nNoa"
ERAN_SIG = "Eran Ganot\nThe human behind Noa"


def enabled() -> bool:
    return os.environ.get("LIFECYCLE_EMAILS", "").strip().lower() in ("1", "true", "yes", "on")


# ── time ────────────────────────────────────────────────────────────────────
def _tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("Asia/Jerusalem")
    except (ImportError, KeyError):
        return timezone(timedelta(hours=2))


def _parse(value):
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        return datetime.strptime(str(value)[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def due_step(created, now: datetime, done: set):
    """The one step due for an account created at `created` (UTC), given the
    steps already recorded. Returns (step, 'send'|'skip') or None."""
    if created is None:
        return None
    local_now = now.astimezone(_tz())
    signup_day = created.astimezone(_tz()).date()
    if "welcome" not in done:
        if now - created >= WELCOME_UNTIL:
            return ("welcome", "skip")
        if now - created >= WELCOME_AFTER:
            return ("welcome", "send")
    for step, n in DAY_OF.items():
        if step in done:
            continue
        target = signup_day + timedelta(days=n)
        if local_now.date() > target:
            return (step, "skip")            # missed its day: never late
        if local_now.date() == target and (local_now.hour, local_now.minute) >= SEND_AT:
            return (step, "send")
        return None                          # steps go in order
    return None


# ── unsubscribe ─────────────────────────────────────────────────────────────
def _key() -> bytes:
    return ("lifecycle:" + (os.environ.get("JH_ENCRYPTION_KEY") or os.environ.get("JH_HEALTH_KEY") or "jh")).encode()


def unsubscribe_token(user_id: int) -> str:
    return hmac.new(_key(), b"stop:%d" % int(user_id), hashlib.sha256).hexdigest()[:32]


def unsubscribe_ok(user_id, token) -> bool:
    try:
        return hmac.compare_digest(unsubscribe_token(int(user_id)), str(token or ""))
    except (TypeError, ValueError):
        return False


def unsubscribe_url(base: str, user_id: int) -> str:
    return "%s/email/stop?u=%d&t=%s" % (base.rstrip("/"), int(user_id), unsubscribe_token(user_id))


# ── what we know about the user ─────────────────────────────────────────────
_RANK = "COALESCE(match_score, 0) - COALESCE(feedback_penalty, 0)"


def user_state(conn, user_id: int) -> dict:
    one = lambda sql, *a: (conn.execute(sql, (user_id,) + a).fetchone() or [0])[0] or 0
    prof = conn.execute("SELECT onboarding_complete, cv_path FROM user_profiles WHERE user_id=?", (user_id,)).fetchone()
    top = conn.execute("SELECT title, company FROM jobs WHERE user_id=? AND status='new' "
                       "ORDER BY " + _RANK + " DESC, id DESC LIMIT 3", (user_id,)).fetchall()
    return {
        "setup_done": bool(prof and prof[0]),
        "has_cv": bool(prof and prof[1]),
        "to_swipe": int(one("SELECT COUNT(*) FROM jobs WHERE user_id=? AND status='new'")),
        "picked": int(one("SELECT COUNT(*) FROM jobs WHERE user_id=? AND released_on IS NOT NULL")),
        "approved": int(one("SELECT COUNT(*) FROM jobs WHERE user_id=? AND status IN ('approved','applied')")),
        # Real applications only: never the bulk "mark applied" (CLAUDE.md: keep wording honest).
        "applied": int(one("SELECT COUNT(*) FROM jobs WHERE user_id=? AND status='applied' "
                           "AND COALESCE(applied_via,'') IN ('engine','manual')")),
        "activated": bool(one("SELECT COUNT(*) FROM funnel_events WHERE user_id=? AND step='first_approve'")),
        "top": [(r[0] or "", r[1] or "") for r in top],
    }


# ── the emails ──────────────────────────────────────────────────────────────
def _s(n, word, plural=None):
    return "%d %s" % (n, word if n == 1 else (plural or word + "s"))


def compose(step: str, first: str, st: dict, link: str):
    """(variant, sender, subject, body) - body without the footer - or None to
    skip. `first` is the first name or ''. sender is 'noa' or 'eran'."""
    hi = "Hi %s," % first if first else "Hi,"
    name = first or "you"
    picks = "\n".join("  - %s at %s" % (t, c) if c else "  - %s" % t for t, c in st.get("top", []))

    if step == "welcome":
        if st["setup_done"]:
            return None                          # the first-matches email is their welcome
        return ("setup", "noa", "%s, I can't start without you" % (first or "Hey"),
                "%s\n\nYou signed up an hour ago, and I still don't know what to look for.\n\n"
                "Give me two minutes: upload your CV and I'll take it from there. I read it, work out "
                "what fits you, and start bringing you hand-picked jobs today.\n\n%s\n\n%s"
                % (hi, link, NOA_SIG))

    if step == "day1":
        if not st["setup_done"]:
            return ("setup", "noa", "Your job search hasn't started yet",
                    "%s\n\nI'm ready, but I'm still waiting on your CV. Without it I'd be guessing, and "
                    "you deserve better than guesses.\n\nIt takes two minutes:\n\n%s\n\n%s" % (hi, link, NOA_SIG))
        if st["to_swipe"] > 0:
            return ("waiting", "noa", "%s I picked for you %s waiting"
                    % (_s(st["to_swipe"], "job"), "is" if st["to_swipe"] == 1 else "are"),
                    "%s\n\nI've lined up %s that fit you%s\n\n%s\n\nSwipe right on the ones you like, left on "
                    "the rest. Every swipe teaches me what you want, so tomorrow's picks get closer.\n\n%s\n\n%s"
                    % (hi, _s(st["to_swipe"], "job"), ". The best of them:" if picks else ".",
                       picks, link, NOA_SIG))
        if st["picked"] == 0:
            return ("empty", "noa", "Let's widen the net, %s" % name,
                    "%s\n\nI haven't found anything that fits what you asked for yet. That usually means the "
                    "titles are narrower than the market.\n\nAdd a title or two (the same role one level up or "
                    "down works well) and I'll search again:\n\n%s\n\n%s" % (hi, link, NOA_SIG))
        return ("active", "noa", "Nice start, %s" % name,
                "%s\n\nYou've already been through your picks. %s\n\nOpen any job you approved and I'll "
                "prepare what you need to apply: a cover letter and your CV tailored to that role.\n\n"
                "Your next picks come in with today's search.\n\n%s\n\n%s"
                % (hi, "You approved %s so far." % _s(st["approved"], "job") if st["approved"] else
                   "Nothing grabbed you yet, and that's fine: every pass teaches me.", link, NOA_SIG))

    if step == "day3":
        if st["activated"]:
            return ("active", "eran", "Quick question from Eran",
                    "%s\n\nI'm Eran, I built Job Hunter (and Noa). You've been using it for a few days, and I'd "
                    "love to hear how it's going.\n\nWhat's one thing Noa got right, and one thing she got "
                    "wrong? Just hit reply. It comes straight to me, and I read every one.\n\nThanks,\n%s"
                    % (hi, ERAN_SIG))
        return ("stuck", "eran", "Quick question from Eran",
                "%s\n\nI'm Eran, I built Job Hunter (and Noa). I noticed you haven't approved a job yet, and "
                "I'd really like to know why.\n\nWas it the matches, the setup, or something else? Just hit "
                "reply, even one line helps. It comes straight to me.\n\nThanks,\n%s" % (hi, ERAN_SIG))

    if step == "day7":
        if not st["setup_done"]:
            return ("setup", "noa", "Last nudge from me",
                    "%s\n\nThis is the last time I'll ask. If you're still looking, upload your CV and I'll start "
                    "bringing you hand-picked jobs every day:\n\n%s\n\nIf now isn't the time, no hard feelings. "
                    "I'll be here.\n\n%s" % (hi, link, NOA_SIG))
        if st["approved"] > 0:
            done = "you approved %s" % _s(st["approved"], "job")
            if st["applied"]:
                done += " and applied to %s" % _s(st["applied"], "of them", "of them")
            return ("active", "noa", "Your first week with me",
                    "%s\n\nOne week in. I brought you %s, and you were busy: %s.\n\n%s\n\n"
                    "Keep going. Today's picks are in:\n\n%s\n\n%s"
                    % (hi, _s(st["picked"], "pick"), done,
                       "%s still waiting for you." % _s(st["to_swipe"], "job") if st["to_swipe"] else
                       "Every swipe sharpens the next picks.", link, NOA_SIG))
        return ("quiet", "noa", "Should I keep looking, %s?" % name,
                "%s\n\nI've kept picking for you this week%s, but you haven't approved any yet.\n\n"
                "If they're off, tell me what to change: update your job titles or locations in Settings and "
                "I'll adjust tomorrow's picks.\n\n%s\n\n%s"
                % (hi, " and %s waiting" % ("one is" if st["to_swipe"] == 1 else "%d are" % st["to_swipe"])
                   if st["to_swipe"] else "", link, NOA_SIG))
    return None


def footer(sender: str, unsub: str) -> str:
    return ("\n\n--\nYou're getting this because you signed up for Job Hunter. "
            "Don't want these tips and check-ins? Stop them here: %s" % unsub)


# ── the sweep ───────────────────────────────────────────────────────────────
def since(conn):
    r = conn.execute("SELECT value FROM app_flags WHERE key='lifecycle_since'").fetchone()
    return r[0] if r else None


def run(get_db, send, base_url: str, track=None, now: datetime | None = None,
        is_test=lambda e: False, senders=None) -> dict:
    """Send what is due. `send(to, subject, body, sender, unsub_url)` raises on
    failure. `senders` = {'noa': bool, 'eran': bool}: a step whose sender is
    not configured is recorded as skipped. Returns counts."""
    now = now or datetime.now(timezone.utc)
    senders = senders or {"noa": True, "eran": True}
    out = {"sent": 0, "skipped": 0, "failed": 0}
    conn = get_db()
    try:
        start = since(conn)
        if not start:
            return out
        users = conn.execute(
            "SELECT id, name, email, created_date FROM users WHERE created_date >= ? "
            "AND COALESCE(is_active, 1)=1 AND LOWER(COALESCE(role,'user'))!='admin' "
            "AND delete_after IS NULL AND COALESCE(lifecycle_opt_out, 0)=0", (start,)).fetchall()
        for u in users:
            uid, email = u[0], u[2]
            if not email or is_test(email):
                continue
            done = {r[0] for r in conn.execute("SELECT step FROM lifecycle_emails WHERE user_id=?", (uid,))}
            due = due_step(_parse(u[3]), now, done)
            if not due:
                continue
            step, action = due
            variant, msg = "missed", None
            if action == "send":
                first = (u[1] or "").strip().split()[0] if (u[1] or "").strip() else ""
                msg = compose(step, first, user_state(conn, uid), base_url.rstrip("/") + "/app")
                variant = msg[0] if msg else "not_needed"
                if msg and not senders.get(msg[1]):
                    variant, msg = "no_sender", None
            if msg:
                unsub = unsubscribe_url(base_url, uid)
                try:
                    send(email, msg[2], msg[3] + footer(msg[1], unsub), msg[1], unsub)
                except Exception as exc:          # recorded below as failed, never retried
                    print("[lifecycle] %s for user %s not sent: %s" % (step, uid, exc))
                    variant = "failed:" + msg[0]
                    out["failed"] += 1
                else:
                    out["sent"] += 1
                    if track:
                        track(uid, "lifecycle_email_sent", {"step": step, "variant": msg[0], "sender": msg[1]})
            else:
                out["skipped"] += 1
            conn.execute("INSERT INTO lifecycle_emails (user_id, step, variant, sent_at) VALUES (?,?,?,?)",
                         (uid, step, variant, now.strftime("%Y-%m-%d %H:%M:%S")))
            conn.commit()
    finally:
        conn.close()
    return out


# ── previews (admin) ────────────────────────────────────────────────────────
_TOP = [("Senior Product Manager", "Lumen Health"), ("Product Manager, Growth", "Orbitly"),
        ("Product Manager - Payments", "Kesef Labs")]
_BASE = {"setup_done": True, "has_cv": True, "to_swipe": 0, "picked": 7, "approved": 0, "applied": 0,
         "activated": False, "top": []}
SAMPLES = [
    ("welcome", "setup", {"setup_done": False, "picked": 0}),
    ("day1", "setup", {"setup_done": False, "picked": 0}),
    ("day1", "waiting", {"to_swipe": 7, "top": _TOP}),
    ("day1", "empty", {"picked": 0}),
    ("day1", "active", {"approved": 3, "activated": True}),
    ("day3", "active", {"approved": 5, "activated": True}),
    ("day3", "stuck", {}),
    ("day7", "setup", {"setup_done": False, "picked": 0}),
    ("day7", "active", {"picked": 28, "approved": 9, "applied": 2, "activated": True, "to_swipe": 4}),
    ("day7", "quiet", {"picked": 28, "to_swipe": 12}),
]


def previews(first: str, link: str, unsub: str = "https://job-hunter.work/email/stop?u=0&t=preview") -> list:
    """Every variant, rendered for `first`: [{step, variant, sender, subject, body}]."""
    out = []
    for step, variant, over in SAMPLES:
        msg = compose(step, first, dict(_BASE, **over), link)
        assert msg and msg[0] == variant, (step, variant, msg and msg[0])
        out.append({"step": step, "variant": variant, "sender": msg[1], "subject": msg[2],
                    "body": msg[3] + footer(msg[1], unsub)})
    return out
