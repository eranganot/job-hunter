"""Owner alerts for launch (2026-11-24, launch items C4 + C7).

Checked by the scheduler every ~10 minutes; each alert fires once (per day,
or once ever for a milestone), remembered in app_flags:
  - AI calls today reach 50% / 80% of JH_LLM_GLOBAL_CALLS (the hard stop
    itself already alerts, in gemini.py) - the early warning;
  - real users reach 50: time to move Resend off the free plan (100 a day);
  - emails sent today reach 80 of Resend's 100 a day;
  - the sign-up brake turned someone away today.
Nothing here can fail a request or the scheduler: every check logs and moves on.
"""
from datetime import datetime, timezone

AI_STEPS = (50, 80)
USERS_MILESTONE = 50
EMAILS_WARN = 80
TEST_DOMAIN = "@job-hunter.test"
# Gemini 2.5 Flash list price for input tokens (most of our tokens are prompt):
# a floor for the estimate in the message, never used for any decision.
USD_PER_M_TOKENS = 0.30


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _seen(conn, key):
    return conn.execute("SELECT 1 FROM app_flags WHERE key=?", (key,)).fetchone() is not None


def _mark(conn, key):
    conn.execute("INSERT OR REPLACE INTO app_flags (key, value) VALUES (?, ?)", (key, _today()))
    conn.commit()


def count_email(conn):
    """+1 to today's sent-email count (called after Resend accepts a message)."""
    key = "emails_sent:" + _today()
    row = conn.execute("SELECT value FROM app_flags WHERE key=?", (key,)).fetchone()
    n = int(row[0]) + 1 if row and str(row[0]).isdigit() else 1
    conn.execute("INSERT OR REPLACE INTO app_flags (key, value) VALUES (?, ?)", (key, str(n)))
    conn.commit()
    return n


def emails_today(conn):
    row = conn.execute("SELECT value FROM app_flags WHERE key=?", ("emails_sent:" + _today(),)).fetchone()
    return int(row[0]) if row and str(row[0]).isdigit() else 0


def real_users(conn):
    row = conn.execute("SELECT COUNT(*) FROM users WHERE role != 'admin' AND is_active=1 "
                       "AND lower(email) NOT LIKE ?", ("%" + TEST_DOMAIN,)).fetchone()
    return int(row[0] or 0)


def waitlisted_today(conn):
    try:
        row = conn.execute("SELECT COUNT(*) FROM signup_waitlist WHERE created_at >= ?",
                           (_today(),)).fetchone()
        return int(row[0] or 0)
    except Exception as e:
        print("[ops-alerts] WARNING waitlist count failed: %s" % e)
        return 0


def due(conn, ai_usage):
    """[(flag key, message)] for every alert that should go out now."""
    out = []
    day = _today()
    g = (ai_usage or {}).get("global") or {}
    cap = ((ai_usage or {}).get("limits") or {}).get("global_calls") or 0
    calls, tokens = int(g.get("calls") or 0), int(g.get("tokens") or 0)
    if cap:
        for step in AI_STEPS:
            if calls * 100 >= cap * step and not _seen(conn, "ai_%d:%s" % (step, day)):
                out.append(("ai_%d:%s" % (step, day),
                            "AI use today is at %d%%: %s of %s calls (about $%.0f or more so far). "
                            "At 100%% every search stops until midnight UTC. To raise it: Railway -> web -> "
                            "Variables -> JH_LLM_GLOBAL_CALLS." % (calls * 100 // cap, f"{calls:,}", f"{cap:,}",
                                                                   tokens * USD_PER_M_TOKENS / 1e6)))
    n = real_users(conn)
    if n >= USERS_MILESTONE and not _seen(conn, "users_%d" % USERS_MILESTONE):
        out.append(("users_%d" % USERS_MILESTONE,
                    "Job Hunter has %d users. Time to move Resend off the free plan (100 emails a day): "
                    "resend.com -> Settings -> Billing -> Pro." % n))
    sent = emails_today(conn)
    if sent >= EMAILS_WARN and not _seen(conn, "emails_%d:%s" % (EMAILS_WARN, day)):
        out.append(("emails_%d:%s" % (EMAILS_WARN, day),
                    "%d emails sent today; Resend's free plan stops at 100 a day. "
                    "Upgrade: resend.com -> Settings -> Billing -> Pro." % sent))
    w = waitlisted_today(conn)
    if w and not _seen(conn, "waitlist:" + day):
        out.append(("waitlist:" + day,
                    "The sign-up brake turned someone away today (%d on the waitlist so far today). "
                    "Check JH_SIGNUP_DAILY_CAP / JH_SIGNUPS in Railway." % w))
    return out


def run(conn, ai_usage, send):
    """Send what is due; mark each one only after send() returns."""
    sent = []
    for key, msg in due(conn, ai_usage):
        try:
            send(msg)
        except Exception as e:
            print("[ops-alerts] ERROR could not send %s: %s" % (key, e))
            continue
        _mark(conn, key)
        sent.append(key)
    return sent
