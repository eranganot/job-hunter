"""tests/test_followup.py - "Did they get back to you?" 10 days after an application (GTM row 12)."""
import sqlite3
from datetime import datetime, timedelta, timezone

import followup
import lifecycle
from tests.test_routes import Client, stack, users  # noqa: F401

NOW = datetime(2026, 12, 20, 10, 0, tzinfo=timezone.utc)          # 12:00 Israel time
TEST = "test-user@job-hunter.test"


def t(days_ago, now=NOW):
    return (now - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")


def _db():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, email TEXT, role TEXT, is_active INTEGER DEFAULT 1,"
              " delete_after TEXT, lifecycle_opt_out INTEGER DEFAULT 0)")
    c.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY, user_id INTEGER, title TEXT, company TEXT, status TEXT,"
              " applied_via TEXT, applied_date TEXT, outcome TEXT, outcome_at TEXT, followup_at TEXT)")
    c.executemany("INSERT INTO users (id,name,email,role) VALUES (?,?,?,?)", [
        (1, "Dana Levi", "dana@x", "user"), (2, "Owner", "owner@x", "admin"), (3, "Test", TEST, "user"),
        (4, "Opted", "opt@x", "user")])
    c.execute("UPDATE users SET lifecycle_opt_out=1 WHERE id=4")
    c.executemany("INSERT INTO jobs (id,user_id,title,company,status,applied_via,applied_date) VALUES (?,?,?,?,?,?,?)", [
        (10, 1, "Senior PM", "Lumen", "applied", "manual", t(10.5)),     # due
        (11, 1, "PM Growth", "Orbitly", "applied", "engine", t(12)),     # due (inside the window)
        (12, 1, "PM Pay", "Kesef", "applied", "bulk", t(10.5)),          # bulk: not an application
        (13, 1, "Old", "Gone", "applied", "manual", t(20)),              # past the window: never late
        (14, 1, "Fresh", "New", "applied", "manual", t(3)),              # too early
        (15, 2, "Admin", "A", "applied", "manual", t(11)),               # admin: never
        (16, 3, "Test", "T", "applied", "manual", t(11)),                # test account: marked, not sent
        (17, 4, "Opted", "O", "applied", "manual", t(11))])              # opted out: marked, not sent
    return c


class _Keep:
    def __init__(self, c): self.c = c
    def execute(self, *a): return self.c.execute(*a)
    def commit(self): self.c.commit()
    def close(self): pass


def _run(c, now=NOW):
    mails = []
    out = followup.run(lambda: _Keep(c), lambda to, s, b, sender, unsub: mails.append((to, s, b, sender)),
                       "https://job-hunter.work", now=now, is_test=lambda e: e == TEST)
    return out, mails


def test_one_email_per_user_for_every_application_on_its_tenth_day():
    c = _db()
    out, mails = _run(c)
    assert out == {"emailed": 1, "jobs": 2, "skipped": 2, "failed": 0}
    (to, subj, body, sender), = mails
    assert to == "dana@x" and sender == "noa" and subj == "Did they get back to you?"
    assert "Senior PM at Lumen" in body and "PM Growth at Orbitly" in body and "Kesef" not in body
    assert body.startswith("Hi Dana,") and lifecycle.NOA_SIG in body and "Stop them here" in body
    assert body.count("/email/outcome?j=10&o=") == 3
    asked = {r[0] for r in c.execute("SELECT id FROM jobs WHERE followup_at IS NOT NULL")}
    assert asked == {10, 11, 16, 17}
    assert _run(c)[1] == []                                          # never twice


def test_nothing_before_launch_day_or_before_1130_israel_time():
    c = _db()
    assert _run(c, now=datetime(2026, 11, 30, 10, 0, tzinfo=timezone.utc))[1] == []
    assert _run(c, now=NOW.replace(hour=9, minute=0))[1] == []       # 11:00 Israel


def test_a_single_application_names_the_company():
    subj, body = followup.compose("", [(5, "Senior PM", "Lumen")], "https://x")
    assert subj == "Did Lumen get back to you?" and body.startswith("Hi,\n")
    assert "since you applied to Senior PM at Lumen" in body


def test_answers_are_signed_and_saved_on_post_only(stack, users):  # noqa: F811
    import db as database
    conn = database.get_db()
    try:
        uid = conn.execute("SELECT id FROM users WHERE email='admin@example.test'").fetchone()[0]
        cur = conn.execute("INSERT INTO jobs (user_id, title, company, url, status, applied_via, found_date) "
                           "VALUES (?,?,?,?,?,?,datetime('now'))", (uid, "PM", "Acme", "https://x.test/fu", "applied", "manual"))
        conn.commit()
        jid = cur.lastrowid
    finally:
        conn.close()
    anon = Client(stack["port"])
    url = "/email/outcome?j=%d&o=interview&t=%s" % (jid, followup.token(jid, "interview"))
    st, _l, page = anon.get(url)
    assert st == 200 and b"PM at Acme" in page and b"interview" in page
    assert _outcome(jid) is None                                     # GET changes nothing
    assert anon.post_form(url, {})[0] == 200
    assert _outcome(jid) == "interview"
    assert anon.post_form("/email/outcome?j=%d&o=rejected&t=%s" % (jid, followup.token(jid, "interview")), {})[0] == 400
    assert anon.get("/email/outcome?j=%d&o=hired&t=x" % jid)[0] == 400


def _outcome(jid):
    import db as database
    c = database.get_db()
    try:
        return c.execute("SELECT outcome FROM jobs WHERE id=?", (jid,)).fetchone()[0]
    finally:
        c.close()
