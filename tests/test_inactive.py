"""tests/test_inactive.py - users who stopped coming back (GTM row 26, owner 2026-11-16)."""
import sqlite3
from datetime import datetime, timedelta, timezone

import inactive

NOW = datetime(2026, 12, 20, 9, 0, tzinfo=timezone.utc)
TEST = "test-user@job-hunter.test"


def t(days_ago):
    return (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")


def _db():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, email TEXT, role TEXT, is_active INTEGER DEFAULT 1,"
              " created_date TEXT, delete_after TEXT, lifecycle_opt_out INTEGER DEFAULT 0, search_paused_at TEXT)")
    c.execute("CREATE TABLE user_profiles (user_id INTEGER, onboarding_complete INTEGER)")
    c.execute("CREATE TABLE activity_log (user_id INTEGER, event_type TEXT, created_date TEXT)")
    c.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY, user_id INTEGER, status TEXT, applied_via TEXT, applied_date TEXT)")
    c.execute("CREATE TABLE lifecycle_emails (user_id INTEGER, step TEXT, variant TEXT, sent_at TEXT, UNIQUE(user_id, step))")
    rows = [(1, "Owner", "owner@x", "admin", t(200)), (2, "Dana Levi", "dana@x", "user", t(40)),
            (3, "Active", "act@x", "user", t(40)), (4, "Test", TEST, "user", t(40)),
            (5, "New", "new@x", "user", t(5)), (6, "Opted", "opt@x", "user", t(40)),
            (7, "Opener", "open@x", "user", t(40)), (8, "No setup", "ns@x", "user", t(40))]
    for i, n, e, r, cd in rows:
        c.execute("INSERT INTO users (id,name,email,role,created_date) VALUES (?,?,?,?,?)", (i, n, e, r, cd))
        c.execute("INSERT INTO user_profiles VALUES (?,?)", (i, 0 if i == 8 else 1))
    c.execute("UPDATE users SET lifecycle_opt_out=1 WHERE id=6")
    c.executemany("INSERT INTO activity_log VALUES (?,?,?)", [
        (2, "job_approved", t(20)), (3, "job_rejected", t(3)),
        (7, "jobs_searched", t(1)), (7, "notification", t(1))])      # the scheduled search is not HIS activity
    return c


def _run(c, now=NOW, **kw):
    mails = []
    out = inactive.run(lambda: _Keep(c), lambda to, subj, body, sender, unsub: mails.append((to, subj, body, sender)),
                       "https://job-hunter.work", now=now, is_test=lambda e: e == TEST, **kw)
    return out, mails


class _Keep:
    """The sweep closes its connection; the test keeps using the same in-memory db."""
    def __init__(self, c): self.c = c
    def execute(self, *a): return self.c.execute(*a)
    def commit(self): self.c.commit()
    def close(self): pass


def test_nothing_before_launch_day():
    c = _db()
    out, mails = _run(c, now=datetime(2026, 11, 30, 23, 0, tzinfo=timezone.utc))
    assert out == {"paused": 0, "emailed": 0, "failed": 0} and not mails
    assert c.execute("SELECT COUNT(*) FROM users WHERE search_paused_at IS NOT NULL").fetchone()[0] == 0


def test_who_is_paused_and_who_gets_the_one_email():
    c = _db()
    out, mails = _run(c)
    paused = {r[0] for r in c.execute("SELECT id FROM users WHERE search_paused_at IS NOT NULL")}
    # Dana (last decision 20 days ago), the opted-out user and the one who only "opened":
    # paused. Admin, test account, active, new and never-set-up: not.
    assert paused == {2, 6, 7} and out["paused"] == 3
    assert sorted(m[0] for m in mails) == ["dana@x", "open@x"] and out["emailed"] == 2     # opted-out: paused, no mail
    to, subj, body, sender = [m for m in mails if m[0] == "dana@x"][0]
    assert sender == "eran" and subj == "Still looking, Dana?"
    assert "Hi Dana," in body and "stop searching for you for now" in body and "Just hit reply" in body
    assert inactive.resume_url("https://job-hunter.work", 2) in body and "/email/stop?u=2" in body
    assert body.count("The human behind Noa") == 1
    # A second sweep changes nothing and sends nothing.
    out2, mails2 = _run(c)
    assert out2["paused"] == 0 and not mails2


def test_resuming_restarts_the_clock_and_the_email_is_once_per_account():
    c = _db()
    _run(c)
    c.execute("UPDATE users SET search_paused_at=NULL WHERE id=2")
    c.execute("INSERT INTO activity_log VALUES (2,'search_resumed',?)", (t(0),))
    out, mails = _run(c)
    assert out["paused"] == 0                                           # just came back: not paused again
    later = NOW + timedelta(days=15)
    out, mails = _run(c, now=later)
    assert c.execute("SELECT search_paused_at FROM users WHERE id=2").fetchone()[0]     # paused again
    assert "dana@x" not in [m[0] for m in mails]                                       # no second email


def test_the_resume_link_is_signed():
    assert inactive.resume_ok(2, inactive.resume_token(2))
    assert not inactive.resume_ok(3, inactive.resume_token(2)) and not inactive.resume_ok("x", "y")
    import lifecycle
    assert inactive.resume_token(2) != lifecycle.unsubscribe_token(2)   # a stop link cannot resume, or the reverse


def test_preview_lists_exactly_who_the_sweep_would_pause_and_changes_nothing():
    """Launch item E10: the owner reads the Day-1 list before it happens."""
    c = _db()
    p = inactive.preview(c, NOW, is_test=lambda e: e == TEST)
    assert p["at"] == "2026-12-20" and p["count"] == 3
    assert {u["id"] for u in p["users"]} == {2, 6, 7}
    dana = [u for u in p["users"] if u["id"] == 2][0]
    assert dana == {"id": 2, "first_name": "Dana", "last_active": t(20)[:10], "days_inactive": 20, "email": "will send", "applied": {}}
    assert [u for u in p["users"] if u["id"] == 6][0]["email"] == "opted out"
    assert all("@" not in str(v) for u in p["users"] for v in u.values())          # no addresses
    assert c.execute("SELECT COUNT(*) FROM users WHERE search_paused_at IS NOT NULL").fetchone()[0] == 0
    # The sweep then pauses the same people.
    _run(c)
    assert {r[0] for r in c.execute("SELECT id FROM users WHERE search_paused_at IS NOT NULL")} == {2, 6, 7}
