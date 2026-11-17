"""followup.py - "Did they get back to you?" 10 days after an application (GTM row 12).

Ten days after a real application (applied_via 'engine' or 'manual'; never a
bulk "mark applied"), Noa asks whether the employer answered. One email per
user per day at most, listing every application that reached its tenth day;
each one has three answers (interview / rejected / no answer yet) that are
stored on the job (jobs.outcome, jobs.outcome_at) - the first data on what
happens after an application. A reply works too: a person reads every one.

- Sent from 11:30 Israel time, like the other emails; an application whose
  window (days 10 to 13) has passed is never asked about late.
- Each job is asked about once (jobs.followup_at, also set when skipped).
- Same rules as lifecycle.py: only with LIFECYCLE_EMAILS on, never the test
  account, an admin, a disabled account or one being deleted; "stop these
  emails" (lifecycle_opt_out) stops these too.
- Nothing before START (JH_FOLLOWUP_FROM, default 2026-12-01, launch day), as
  with the inactivity email.
"""
import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone

import lifecycle

AFTER_DAYS = 10
WINDOW_DAYS = 4                      # days 10..13; older are never asked late
REAL_VIA = ("engine", "manual")
OUTCOMES = {"interview": "They asked me to an interview",
            "rejected": "They said no",
            "no_reply": "No answer yet"}
FMT = "%Y-%m-%d %H:%M:%S"


def start() -> datetime:
    raw = os.environ.get("JH_FOLLOWUP_FROM", "").strip() or "2026-12-01"
    try:
        return datetime.strptime(raw[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime(2026, 12, 1, tzinfo=timezone.utc)


def token(job_id: int, outcome: str) -> str:
    return hmac.new(lifecycle._key(), ("outcome:%d:%s" % (int(job_id), outcome)).encode(),
                    hashlib.sha256).hexdigest()[:32]


def token_ok(job_id, outcome, tok) -> bool:
    try:
        return outcome in OUTCOMES and hmac.compare_digest(token(int(job_id), outcome), str(tok or ""))
    except (TypeError, ValueError):
        return False


def answer_url(base: str, job_id: int, outcome: str) -> str:
    return "%s/email/outcome?j=%d&o=%s&t=%s" % (base.rstrip("/"), int(job_id), outcome, token(job_id, outcome))


def record(conn, job_id: int, outcome: str, now: datetime | None = None) -> bool:
    if outcome not in OUTCOMES:
        return False
    cur = conn.execute("UPDATE jobs SET outcome=?, outcome_at=? WHERE id=?",
                       (outcome, (now or datetime.now(timezone.utc)).strftime(FMT), int(job_id)))
    conn.commit()
    return bool(getattr(cur, "rowcount", 0))


def _job_line(title, company):
    return "%s at %s" % (title, company) if company else title


def compose(first: str, jobs: list, base: str):
    """(subject, body) for one user. jobs = [(id, title, company)]."""
    hi = "Hi %s," % first if first else "Hi,"
    if len(jobs) == 1:
        jid, title, company = jobs[0]
        subject = "Did %s get back to you?" % (company or "they")
        intro = "It's been %d days since you applied to %s. Did they get back to you?" % (
            AFTER_DAYS, _job_line(title, company))
    else:
        subject = "Did they get back to you?"
        intro = "It's been %d days since you applied to these %d jobs. Did any of them get back to you?" % (
            AFTER_DAYS, len(jobs))
    blocks = []
    for jid, title, company in jobs:
        links = "\n".join("  %s: %s" % (label, answer_url(base, jid, key)) for key, label in OUTCOMES.items())
        blocks.append(("%s\n%s" % (_job_line(title, company), links)) if len(jobs) > 1 else links)
    body = ("%s\n\n%s\n\nTell me with a click and I'll keep track for you:\n\n%s\n\n"
            "No answer after %d days is common. If it's a role you really want, a short, polite note to the "
            "recruiter is fine.\n\n"
            "Or just reply and tell me how it went. A person on the team reads every reply.\n\n%s"
            % (hi, intro, "\n\n".join(blocks), AFTER_DAYS, lifecycle.NOA_SIG))
    return subject, body


def run(get_db, send, base_url: str, now: datetime | None = None, is_test=lambda e: False,
        can_email: bool = True, track=None) -> dict:
    """Ask about every application on its tenth day. `send(to, subject, body, sender, unsub_url)`."""
    now = now or datetime.now(timezone.utc)
    out = {"emailed": 0, "jobs": 0, "skipped": 0, "failed": 0}
    if now < start():
        return out
    local = now.astimezone(lifecycle._tz())
    if (local.hour, local.minute) < lifecycle.SEND_AT:
        return out
    newest = (now - timedelta(days=AFTER_DAYS)).strftime(FMT)
    oldest = (now - timedelta(days=AFTER_DAYS + WINDOW_DAYS)).strftime(FMT)
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT j.id, j.title, j.company, j.user_id, u.name, u.email, COALESCE(u.lifecycle_opt_out, 0) "
            "FROM jobs j JOIN users u ON u.id = j.user_id "
            "WHERE j.status='applied' AND j.applied_via IN (?,?) AND j.followup_at IS NULL "
            "AND j.outcome IS NULL AND j.applied_date >= ? AND j.applied_date <= ? "
            "AND COALESCE(u.is_active, 1)=1 AND LOWER(COALESCE(u.role,'user'))!='admin' "
            "AND u.delete_after IS NULL ORDER BY j.user_id, j.applied_date",
            (*REAL_VIA, oldest, newest)).fetchall()
        by_user = {}
        for r in rows:
            by_user.setdefault(int(r[3]), {"name": r[4], "email": r[5] or "", "opt_out": r[6], "jobs": []})["jobs"].append(
                (int(r[0]), r[1] or "the job", r[2] or ""))
        stamp = now.strftime(FMT)
        for uid, u in by_user.items():
            ids = [j[0] for j in u["jobs"]]
            mark = lambda: [conn.execute("UPDATE jobs SET followup_at=? WHERE id=?", (stamp, i)) for i in ids]
            if is_test(u["email"]) or u["opt_out"] or not can_email or not u["email"]:
                mark(); conn.commit()
                out["skipped"] += len(ids)
                continue
            first = (u["name"] or "").strip().split()[0] if (u["name"] or "").strip() else ""
            subject, body = compose(first, u["jobs"], base_url)
            unsub = lifecycle.unsubscribe_url(base_url, uid)
            try:
                send(u["email"], subject, body + lifecycle.footer("noa", unsub), "noa", unsub)
            except Exception as exc:
                print("[followup] email for user %s not sent: %s" % (uid, exc))
                out["failed"] += 1
            else:
                out["emailed"] += 1
                out["jobs"] += len(ids)
                if track:
                    track(uid, "followup_email_sent", {"jobs": len(ids)})
            mark(); conn.commit()           # asked once, sent or failed: never twice
    finally:
        conn.close()
    return out
