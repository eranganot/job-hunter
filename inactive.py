"""
inactive.py - users who stopped coming back (GTM row 26, the owner, 2026-11-16).

"For inactive users (two weeks without any significant activity): 1. reach out
and ask what happened; 2. stop the AI search for them (to reduce expenses)."
Measured that day: only 3 of the 6-9 accounts searched daily had made a
decision in 30 days, and scoring searches is 94% of the Gemini bill.

- Inactive = INACTIVE_DAYS (14) with none of: approving or passing on a job,
  an application, a search the user started, or resuming a paused search.
  Opening the app without doing anything is not activity. A new account's
  clock starts at sign-up.
- On the sweep: the daily search stops for that user (`users.search_paused_at`;
  the scheduler skips them) and ONE email goes out from Eran asking what
  happened, with a one-click "keep looking for me" link. The pause does not
  depend on the email: an unsubscribed user, or a deployment with the lifecycle
  emails off, is still paused.
- Coming back is automatic: opening the app, or the email's link, clears the
  pause and queues a search at once (app.py `_resume_search`).
- Nothing happens before START (JH_INACTIVE_FROM, default 2026-12-01, launch
  day): the owner wanted existing users reached from Day 1, not before.
- Admins and the test account are never paused.

The email is sent once per account (lifecycle_emails step "inactive14"); a
user who comes back and goes quiet again is paused again without a second mail.
"""
import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone

import lifecycle

INACTIVE_DAYS = 14
EMAIL_WINDOW_DAYS = 3
STEP = "inactive14"
ACTIVE_EVENTS = ("job_approved", "job_rejected", "bulk_approve", "bulk_reject", "job_applied",
                 "job_marked_applied", "search_requested", "search_resumed")
FMT = "%Y-%m-%d %H:%M:%S"


def start() -> datetime:
    raw = os.environ.get("JH_INACTIVE_FROM", "").strip() or "2026-12-01"
    try:
        return datetime.strptime(raw[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime(2026, 12, 1, tzinfo=timezone.utc)


def _parse(s):
    s = str(s or "").replace("T", " ")[:19]
    for fmt, n in ((FMT, 19), ("%Y-%m-%d", 10)):
        try:
            return datetime.strptime(s[:n], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def resume_token(user_id: int) -> str:
    return hmac.new(lifecycle._key(), b"resume:%d" % int(user_id), hashlib.sha256).hexdigest()[:32]


def resume_ok(user_id, token) -> bool:
    try:
        return hmac.compare_digest(resume_token(int(user_id)), str(token or ""))
    except (TypeError, ValueError):
        return False


def resume_url(base: str, user_id: int) -> str:
    return "%s/email/resume?u=%d&t=%s" % (base.rstrip("/"), int(user_id), resume_token(user_id))


def last_active(conn, user_id: int, created):
    marks = ",".join("?" for _ in ACTIVE_EVENTS)
    row = conn.execute("SELECT MAX(created_date) FROM activity_log WHERE user_id=? AND event_type IN (%s)" % marks,
                       (user_id, *ACTIVE_EVENTS)).fetchone()
    # A real application counts even without its event: marking a job applied by
    # hand logged nothing before 2026-11-25. A bulk "mark applied" does not count.
    app_row = conn.execute("SELECT MAX(applied_date) FROM jobs WHERE user_id=? AND status='applied' "
                           "AND applied_via IN ('manual','engine')", (user_id,)).fetchone()
    seen = [d for d in (_parse(row[0] if row else None), _parse(app_row[0] if app_row else None),
                        _parse(created)) if d]
    return max(seen) if seen else None


def compose(first: str, link: str):
    """(subject, body) of the one email, from Eran. Body without the footer."""
    hi = "Hi %s," % first if first else "Hi,"
    return ("Still looking%s?" % (", " + first if first else ""),
            "%s\n\nI'm Eran, I built Job Hunter (and Noa). You haven't been in for two weeks, so I've asked Noa "
            "to stop searching for you for now. No point filling your list while you're away.\n\n"
            "I'd really like to know what happened. Did you find a job? Were the picks off? Something else? "
            "Just hit reply, even one line helps. It comes straight to me.\n\n"
            "If you're still looking, one click and Noa picks up where she left off:\n\n%s\n\nThanks,\n%s"
            % (hi, link, lifecycle.ERAN_SIG))


def due(conn, now: datetime, is_test=lambda e: False):
    """[(user row, last active)] for everyone the sweep would pause at `now`:
    finished setup, not admin, not deleting, not already paused, not the test
    account, and no activity for INACTIVE_DAYS. Row = (id, name, email,
    created_date, lifecycle_opt_out). Shared by run() and preview()."""
    cutoff = now - timedelta(days=INACTIVE_DAYS)
    users = conn.execute(
        "SELECT u.id, u.name, u.email, u.created_date, COALESCE(u.lifecycle_opt_out, 0) "
        "FROM users u JOIN user_profiles p ON p.user_id = u.id "
        "WHERE COALESCE(u.is_active, 1)=1 AND LOWER(COALESCE(u.role,'user'))!='admin' "
        "AND u.delete_after IS NULL AND u.search_paused_at IS NULL "
        "AND COALESCE(p.onboarding_complete, 0)=1").fetchall()
    out = []
    for u in users:
        if is_test(u[2] or ""):
            continue
        last = last_active(conn, int(u[0]), u[3])
        if last and last <= cutoff:
            out.append((u, last))
    return out


def preview(conn, at: datetime | None = None, is_test=lambda e: False) -> dict:
    """Who the first sweep on/after `at` (default START) would pause, with no
    change to anything. No email addresses: the owner reads those in Admin -> Users.
    Someone who acts before `at` drops off the list by itself."""
    at = at or start()
    rows = []
    for u, last in due(conn, at, is_test):
        emailed = conn.execute("SELECT 1 FROM lifecycle_emails WHERE user_id=? AND step=?",
                               (int(u[0]), STEP)).fetchone()
        first = (u[1] or "").strip().split()[0] if (u[1] or "").strip() else ""
        apps = conn.execute("SELECT COALESCE(applied_via,'') , COUNT(*), MAX(applied_date) FROM jobs "
                            "WHERE user_id=? AND status='applied' GROUP BY COALESCE(applied_via,'')",
                            (int(u[0]),)).fetchall()
        rows.append({"id": int(u[0]), "first_name": first, "last_active": last.strftime("%Y-%m-%d"),
                     "applied": {(a[0] or "unknown"): {"n": a[1], "last": str(a[2] or "")[:10]} for a in apps},
                     "days_inactive": (at - last).days,
                     "email": "already sent" if emailed else ("opted out" if u[4] else "will send")})
    rows.sort(key=lambda r: -r["days_inactive"])
    return {"at": at.strftime("%Y-%m-%d"), "count": len(rows), "users": rows}


def run(get_db, send, base_url: str, now: datetime | None = None, is_test=lambda e: False,
        can_email: bool = True, track=None) -> dict:
    """Pause who is due, and email them once. `send(to, subject, body, sender, unsub_url)`.

    The pause happens on the first sweep that finds the user due; the email waits for
    11:30 Israel time like the other emails (the owner, 2026-11-25: not at 02:00 on
    Day 1). Someone who comes back before then is no longer paused and gets no email."""
    now = now or datetime.now(timezone.utc)
    out = {"paused": 0, "emailed": 0, "failed": 0}
    if now < start():
        return out
    stamp = now.strftime(FMT)
    conn = get_db()
    try:
        for u, last in due(conn, now, is_test):
            uid = int(u[0])
            conn.execute("UPDATE users SET search_paused_at=? WHERE id=?", (stamp, uid))
            conn.commit()
            out["paused"] += 1
            if track:
                track(uid, "search_paused", {"days_inactive": (now - last).days})

        local = now.astimezone(lifecycle._tz())
        if (local.hour, local.minute) < lifecycle.SEND_AT:
            return out
        # Paused within the last EMAIL_WINDOW_DAYS and never written to: a pause that
        # could not be mailed in time is not mailed weeks later.
        since = (now - timedelta(days=EMAIL_WINDOW_DAYS)).strftime(FMT)
        waiting = conn.execute(
            "SELECT u.id, u.name, u.email, COALESCE(u.lifecycle_opt_out, 0) FROM users u "
            "WHERE u.search_paused_at IS NOT NULL AND u.search_paused_at >= ? "
            "AND COALESCE(u.is_active, 1)=1 AND LOWER(COALESCE(u.role,'user'))!='admin' "
            "AND u.delete_after IS NULL AND NOT EXISTS "
            "(SELECT 1 FROM lifecycle_emails l WHERE l.user_id=u.id AND l.step=?)", (since, STEP)).fetchall()
        for u in waiting:
            uid, email = int(u[0]), u[2] or ""
            if not email or is_test(email):
                continue
            variant = "sent"
            if u[3] or not can_email:
                variant = "opted_out" if u[3] else "no_sender"
            else:
                first = (u[1] or "").strip().split()[0] if (u[1] or "").strip() else ""
                subject, body = compose(first, resume_url(base_url, uid))
                unsub = lifecycle.unsubscribe_url(base_url, uid)
                try:
                    send(email, subject, body + lifecycle.footer("eran", unsub), "eran", unsub)
                    out["emailed"] += 1
                except Exception as exc:
                    print("[inactive] email for user %s not sent: %s" % (uid, exc))
                    variant = "failed"
                    out["failed"] += 1
            conn.execute("INSERT INTO lifecycle_emails (user_id, step, variant, sent_at) VALUES (?,?,?,?)",
                         (uid, STEP, variant, stamp))
            conn.commit()
    finally:
        conn.close()
    return out
