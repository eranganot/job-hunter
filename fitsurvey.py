"""fitsurvey.py - the product-fit question on Day 14 and Day 30 (GTM row 21).

"Free until product fit" needs fit written down, or the free period never
ends (panel review, 2026-11-13). The test: ask activated users "How would you
feel if you could no longer use Job Hunter?"; fit is 40% or more answering
"Very disappointed" (Sean Ellis). "I don't use it anymore" is recorded but
left out of the share, as the test does.

- Who: activated users (a first approve), never admins, the test account, or
  disabled accounts. Two rounds: d14 (days 14-29 after sign-up) and d30
  (days 30-44). One answer per user per round (fit_survey).
- Where: a card in the app while a round is due and unanswered ("Not now"
  stores 'skipped', so it is not asked again that round), and an email from
  Eran on the day itself at 11:30 Israel time (never late; not sent if the
  round is already answered), with one link per answer. The links are signed
  and open a confirm page with an optional comment; a GET changes nothing.
- Nothing before START (JH_FIT_FROM, default 2026-12-01, launch day). The
  email follows the lifecycle rules (LIFECYCLE_EMAILS, "stop these emails").
"""
import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone

import lifecycle

ROUNDS = {"d14": (14, 30), "d30": (30, 45)}          # round -> [first day, last day + 1)
# Worded positively (the owner, 26 Nov 2026); the keys - and so the measure - are
# Sean Ellis's: "very" is "very disappointed", the answer that counts toward fit.
ANSWERS = {"very": "I'd really miss it", "somewhat": "I'd miss it a bit",
           "not": "I wouldn't miss it", "no_longer": "I've stopped using it"}
SKIPPED = "skipped"
TARGET = 40
SOURCES = ("app", "email")
QUESTION = "If Job Hunter disappeared tomorrow, how would you feel?"
FMT = "%Y-%m-%d %H:%M:%S"


def start() -> datetime:
    raw = os.environ.get("JH_FIT_FROM", "").strip() or "2026-12-01"
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


def _signup(conn, user_id):
    r = conn.execute("SELECT MIN(created_at) FROM funnel_events WHERE user_id=? AND step='signup'",
                     (int(user_id),)).fetchone()
    t = _parse(r[0]) if r and r[0] else None
    if t is None:
        r = conn.execute("SELECT created_date FROM users WHERE id=?", (int(user_id),)).fetchone()
        t = _parse(r[0]) if r else None
    return t


def _activated(conn, user_id) -> bool:
    return bool(conn.execute("SELECT 1 FROM funnel_events WHERE user_id=? AND step='first_approve'",
                             (int(user_id),)).fetchone())


def answered(conn, user_id, rnd) -> bool:
    return bool(conn.execute("SELECT 1 FROM fit_survey WHERE user_id=? AND round=?", (int(user_id), rnd)).fetchone())


def due_round(conn, user, now=None):
    """The round to ask in the app now, or None. user: dict with id, role, email."""
    now = now or datetime.now(timezone.utc)
    if now < start() or str(user.get("role") or "") == "admin":
        return None
    uid = int(user["id"])
    if not _activated(conn, uid):
        return None
    t0 = _signup(conn, uid)
    if not t0:
        return None
    age = (now - t0).days
    for rnd, (lo, hi) in ROUNDS.items():
        if lo <= age < hi and not answered(conn, uid, rnd):
            return rnd
    return None


def record(conn, user_id, rnd, answer, source, comment="", now=None) -> bool:
    """Store an answer (or 'skipped'). The first answer of a round stands, but a
    later comment, or a real answer after a skip, is kept."""
    if rnd not in ROUNDS or source not in SOURCES or (answer not in ANSWERS and answer != SKIPPED):
        return False
    stamp = (now or datetime.now(timezone.utc)).strftime(FMT)
    comment = (comment or "").strip()[:2000]
    row = conn.execute("SELECT answer FROM fit_survey WHERE user_id=? AND round=?", (int(user_id), rnd)).fetchone()
    if row is None:
        conn.execute("INSERT INTO fit_survey (user_id, round, answer, comment, source, at) VALUES (?,?,?,?,?,?)",
                     (int(user_id), rnd, answer, comment or None, source, stamp))
    elif row[0] == SKIPPED and answer != SKIPPED:
        conn.execute("UPDATE fit_survey SET answer=?, comment=?, source=?, at=? WHERE user_id=? AND round=?",
                     (answer, comment or None, source, stamp, int(user_id), rnd))
    elif comment:
        conn.execute("UPDATE fit_survey SET comment=? WHERE user_id=? AND round=?", (comment, int(user_id), rnd))
    else:
        return False
    conn.commit()
    return True


def score(conn, people_ids) -> dict:
    """Each person's latest real answer (d30 over d14). Share 'very' of those who
    answered, leaving out 'skipped' and 'no_longer'."""
    latest = {}
    for r in conn.execute("SELECT user_id, round, answer FROM fit_survey WHERE answer != ? ORDER BY round",
                          (SKIPPED,)).fetchall():
        if int(r[0]) in people_ids:
            latest[int(r[0])] = r[2]                       # 'd14' < 'd30', so d30 wins
    counted = [a for a in latest.values() if a != "no_longer"]
    very = sum(1 for a in counted if a == "very")
    return {"value": round(100.0 * very / len(counted)) if counted else None, "n": very, "of": len(counted),
            "no_longer": sum(1 for a in latest.values() if a == "no_longer"), "target": TARGET}


# ── the email ───────────────────────────────────────────────────────────────
def token(user_id, rnd, answer) -> str:
    return hmac.new(lifecycle._key(), ("fit:%d:%s:%s" % (int(user_id), rnd, answer)).encode(),
                    hashlib.sha256).hexdigest()[:32]


def token_ok(user_id, rnd, answer, tok) -> bool:
    try:
        return (rnd in ROUNDS and answer in ANSWERS
                and hmac.compare_digest(token(int(user_id), rnd, answer), str(tok or "")))
    except (TypeError, ValueError):
        return False


def answer_url(base, user_id, rnd, answer) -> str:
    return "%s/email/fit?u=%d&r=%s&a=%s&t=%s" % (base.rstrip("/"), int(user_id), rnd, answer,
                                                 token(user_id, rnd, answer))


def compose(first, rnd, links) -> tuple:
    """(subject, body) from Eran. links = {answer: url}."""
    hi = "Hi %s," % first if first else "Hi,"
    when = "two weeks" if rnd == "d14" else "a month"
    subject = "One question, %s" % first if first else "One question"
    choices = "\n".join("  %s: %s" % (ANSWERS[a], links[a]) for a in ANSWERS)
    body = ("%s\n\nYou've been using Job Hunter for %s now, so I'd love to ask you one question. "
            "It takes one click:\n\n%s\n\n%s\n\n"
            "Your answer comes straight to me, and it decides what we build next. If you have a minute more, "
            "just reply and tell me what Noa does best for you, and what she should do better.\n\n%s"
            % (hi, when, QUESTION, choices, lifecycle.ERAN_SIG))
    return subject, body


def run(get_db, send, base_url, now=None, is_test=lambda e: False, can_email=True, track=None) -> dict:
    """Email activated users on day 14 and day 30 from 11:30 Israel time, once per
    round (lifecycle_emails step fit_d14 / fit_d30), never late."""
    now = now or datetime.now(timezone.utc)
    out = {"sent": 0, "skipped": 0, "failed": 0}
    if now < start():
        return out
    local = now.astimezone(lifecycle._tz())
    if (local.hour, local.minute) < lifecycle.SEND_AT:
        return out
    conn = get_db()
    try:
        users = conn.execute(
            "SELECT DISTINCT u.id, u.name, u.email, COALESCE(u.lifecycle_opt_out, 0) FROM users u "
            "JOIN funnel_events f ON f.user_id = u.id AND f.step='first_approve' "
            "WHERE COALESCE(u.is_active, 1)=1 AND LOWER(COALESCE(u.role,'user'))!='admin' "
            "AND u.delete_after IS NULL").fetchall()
        for u in users:
            uid, email = int(u[0]), u[2] or ""
            if not email or is_test(email):
                continue
            t0 = _signup(conn, uid)
            if not t0:
                continue
            day = (local.date() - t0.astimezone(lifecycle._tz()).date()).days
            rnd = {ROUNDS[r][0]: r for r in ROUNDS}.get(day)
            if not rnd:
                continue
            step = "fit_" + rnd
            if conn.execute("SELECT 1 FROM lifecycle_emails WHERE user_id=? AND step=?", (uid, step)).fetchone():
                continue
            variant = "sent"
            if answered(conn, uid, rnd):
                variant = "answered_in_app"
            elif u[3] or not can_email:
                variant = "opted_out" if u[3] else "no_sender"
            else:
                first = (u[1] or "").strip().split()[0] if (u[1] or "").strip() else ""
                subject, body = compose(first, rnd, {a: answer_url(base_url, uid, rnd, a) for a in ANSWERS})
                unsub = lifecycle.unsubscribe_url(base_url, uid)
                try:
                    send(email, subject, body + lifecycle.footer("eran", unsub), "eran", unsub)
                except Exception as exc:
                    print("[fit] email for user %s not sent: %s" % (uid, exc))
                    variant = "failed"
            if variant == "sent":
                out["sent"] += 1
                if track:
                    track(uid, "fit_survey_emailed", {"round": rnd})
            elif variant == "failed":
                out["failed"] += 1
            else:
                out["skipped"] += 1
            conn.execute("INSERT INTO lifecycle_emails (user_id, step, variant, sent_at) VALUES (?,?,?,?)",
                         (uid, step, variant, now.strftime(FMT)))
            conn.commit()
    finally:
        conn.close()
    return out
