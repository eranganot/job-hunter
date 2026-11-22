"""tests/test_fitsurvey.py - the product-fit question on day 14 and day 30 (GTM row 21)."""
import sqlite3
from datetime import datetime, timedelta, timezone

import fitsurvey
import lifecycle

NOW = datetime(2026, 12, 20, 10, 0, tzinfo=timezone.utc)          # 12:00 Israel time
TEST = "test-user@job-hunter.test"


def t(days_ago):
    return (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")


def _db():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, email TEXT, role TEXT, is_active INTEGER DEFAULT 1,"
              " delete_after TEXT, lifecycle_opt_out INTEGER DEFAULT 0, created_date TEXT)")
    c.execute("CREATE TABLE funnel_events (user_id INTEGER, step TEXT, created_at TEXT)")
    c.execute("CREATE TABLE fit_survey (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, round TEXT, answer TEXT,"
              " comment TEXT, source TEXT, at TEXT, UNIQUE(user_id, round))")
    c.execute("CREATE TABLE lifecycle_emails (user_id INTEGER, step TEXT, variant TEXT, sent_at TEXT, UNIQUE(user_id, step))")
    rows = [(1, "Dana Levi", "dana@x", "user", 14), (2, "Not activated", "na@x", "user", 14),
            (3, "Owner", "owner@x", "admin", 14), (4, "Test", TEST, "user", 14), (5, "Month", "m@x", "user", 30),
            (6, "Answered", "ans@x", "user", 14), (7, "Early", "early@x", "user", 13)]
    for i, n, e, r, d in rows:
        c.execute("INSERT INTO users (id,name,email,role) VALUES (?,?,?,?)", (i, n, e, r))
        c.execute("INSERT INTO funnel_events VALUES (?,?,?)", (i, "signup", t(d)))
        if i != 2:
            c.execute("INSERT INTO funnel_events VALUES (?,?,?)", (i, "first_approve", t(d - 0.5)))
    fitsurvey.record(c, 6, "d14", "very", "app", now=NOW)
    return c


class _Keep:
    def __init__(self, c): self.c = c
    def execute(self, *a): return self.c.execute(*a)
    def commit(self): self.c.commit()
    def close(self): pass


def test_who_is_asked_in_the_app():
    c = _db()
    due = lambda uid, role="user": fitsurvey.due_round(c, {"id": uid, "role": role}, NOW)
    assert due(1) == "d14" and due(5) == "d30"
    assert due(2) is None and due(3, "admin") is None and due(6) is None and due(7) is None
    assert fitsurvey.due_round(c, {"id": 1, "role": "user"}, datetime(2026, 11, 30, tzinfo=timezone.utc)) is None


def test_skip_then_answer_and_a_later_comment():
    c = _db()
    assert fitsurvey.record(c, 1, "d14", "skipped", "app", now=NOW)
    assert fitsurvey.due_round(c, {"id": 1, "role": "user"}, NOW) is None       # not asked again this round
    assert fitsurvey.record(c, 1, "d14", "somewhat", "email", now=NOW)         # a real answer replaces the skip
    assert not fitsurvey.record(c, 1, "d14", "very", "email", now=NOW)         # the first answer stands
    assert fitsurvey.record(c, 1, "d14", "very", "email", "Noa saves me hours", now=NOW)
    assert c.execute("SELECT answer, comment, source FROM fit_survey WHERE user_id=1").fetchone() == \
        ("somewhat", "Noa saves me hours", "email")
    assert not fitsurvey.record(c, 1, "d99", "very", "app") and not fitsurvey.record(c, 1, "d30", "maybe", "app")


def test_score_is_very_of_those_still_using_it_latest_answer_per_person():
    c = _db()
    for uid, rnd, a in ((1, "d14", "not"), (1, "d30", "very"), (5, "d30", "somewhat"), (7, "d14", "no_longer"),
                        (4, "d14", "very")):
        fitsurvey.record(c, uid, rnd, a, "app", now=NOW)
    s = fitsurvey.score(c, {1, 5, 6, 7})                                     # 4 (test account) not a person
    assert s == {"value": 67, "n": 2, "of": 3, "no_longer": 1, "target": 40}


def test_the_email_goes_on_the_day_from_eran_once_and_not_after_an_answer():
    c = _db()
    mails = []
    run = lambda now=NOW: fitsurvey.run(lambda: _Keep(c), lambda to, s, b, sender, unsub: mails.append((to, s, b, sender)),
                                        "https://job-hunter.work", now=now, is_test=lambda e: e == TEST)
    assert run(NOW.replace(hour=9))["sent"] == 0                             # 11:00 Israel: not yet
    out = run()
    assert sorted(m[0] for m in mails) == ["dana@x", "m@x"] and out["sent"] == 2
    assert out["skipped"] == 1                                               # user 6 answered in the app
    dana = [m for m in mails if m[0] == "dana@x"][0]
    assert dana[1] == "One question, Dana" and dana[3] == "eran" and "two weeks" in dana[2]
    assert fitsurvey.QUESTION in dana[2] and lifecycle.ERAN_SIG in dana[2]
    assert dana[2].count("/email/fit?u=1&r=d14&a=") == 4
    assert "a month" in [m for m in mails if m[0] == "m@x"][0][2]
    mails.clear()
    assert run()["sent"] == 0 and not mails                                  # once per round


def test_links_are_signed_per_user_round_and_answer():
    tok = fitsurvey.token(1, "d14", "very")
    assert fitsurvey.token_ok(1, "d14", "very", tok)
    assert not fitsurvey.token_ok(2, "d14", "very", tok) and not fitsurvey.token_ok(1, "d30", "very", tok)
    assert not fitsurvey.token_ok(1, "d14", "not", tok) and not fitsurvey.token_ok("x", "d14", "very", tok)


from tests.test_routes import Client, stack, users  # noqa: E402,F401


def test_routes_refuse_an_undue_answer_and_save_a_signed_email_answer(stack, users):  # noqa: F811
    import db as database
    st, _l, body = users["b"].post_json("/api/fit-survey", {"round": "d14", "answer": "very"})
    assert st == 400                                                          # nothing due (before launch day)
    st, _l, me = users["b"].get("/api/me")
    assert b'"fit_survey_due": null' in me
    conn = database.get_db()
    try:
        uid = conn.execute("SELECT id FROM users ORDER BY id DESC LIMIT 1").fetchone()[0]
    finally:
        conn.close()
    anon = Client(stack["port"])
    url = "/email/fit?u=%d&r=d14&a=very&t=%s" % (uid, fitsurvey.token(uid, "d14", "very"))
    st, _l, page = anon.get(url)
    assert st == 200 and b"Very disappointed" in page and b"textarea" in page
    conn = database.get_db()
    try:
        assert conn.execute("SELECT COUNT(*) FROM fit_survey WHERE user_id=?", (uid,)).fetchone()[0] == 0
    finally:
        conn.close()
    assert anon.post_form(url, {"comment": "Saves me hours"})[0] == 200
    conn = database.get_db()
    try:
        assert tuple(conn.execute("SELECT answer, comment, source FROM fit_survey WHERE user_id=?", (uid,)).fetchone()) \
            == ("very", "Saves me hours", "email")
    finally:
        conn.close()
    assert anon.post_form("/email/fit?u=%d&r=d14&a=not&t=bad" % uid, {})[0] == 400
