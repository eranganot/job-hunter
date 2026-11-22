"""tests/test_probes.py - the three daily-search probe accounts (GTM row 17g, 2026-11-22)."""
import json
import sqlite3
from datetime import date, datetime, timezone

import kpis
import probes
import testaccount

NOW = datetime(2026, 11, 25, 9, 0, tzinfo=timezone.utc)


class _Keep:
    """ensure() closes its connections; the test keeps using one in-memory db."""
    def __init__(self, c): self.c = c
    def execute(self, *a): return self.c.execute(*a)
    def commit(self): self.c.commit()
    def close(self): pass


def _db():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, email TEXT, role TEXT DEFAULT 'user',"
              " is_active INTEGER DEFAULT 1, search_paused_at TEXT, email_verified INTEGER DEFAULT 0)")
    c.execute("CREATE TABLE user_profiles (user_id INTEGER PRIMARY KEY, job_titles TEXT, keywords TEXT, locations TEXT,"
              " cv_summary TEXT, cv_analyzed INTEGER, schedule_frequency TEXT DEFAULT 'weekly', search_hour INTEGER DEFAULT 11,"
              " weekdays_only INTEGER, onboarding_complete INTEGER DEFAULT 0, notification_channel TEXT, experience_years INTEGER)")
    c.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY, user_id INTEGER, title TEXT, company TEXT, url TEXT,"
              " location TEXT, found_date TEXT, url_verified INTEGER, status_check TEXT)")
    c.execute("CREATE TABLE activity_log (user_id INTEGER, event_type TEXT, created_date TEXT)")
    return c


def _ensure(c, today):
    made = []

    def create(name, email, pw):
        cur = c.execute("INSERT INTO users (name, email) VALUES (?,?)", (name, email))
        c.execute("INSERT INTO user_profiles (user_id) VALUES (?)", (cur.lastrowid,))
        made.append(email)
        return cur.lastrowid, None

    def verify(uid):
        c.execute("UPDATE users SET email_verified=1 WHERE id=?", (uid,))

    return probes.ensure(lambda: _Keep(c), create, verify, today), made


def test_probes_are_created_once_with_a_daily_profile(monkeypatch):
    monkeypatch.delenv("JH_PROBES_UNTIL", raising=False)
    c = _db()
    out, made = _ensure(c, date(2026, 11, 22))
    assert out["created"] == ["qa", "design", "customer"] and out["active"]
    assert made == ["probe-qa@job-hunter.test", "probe-design@job-hunter.test", "probe-customer@job-hunter.test"]
    again, made2 = _ensure(c, date(2026, 11, 23))
    assert again["created"] == [] and made2 == []               # idempotent: no second account
    p = c.execute("SELECT p.*, u.is_active, u.email_verified FROM user_profiles p JOIN users u ON u.id=p.user_id "
                  "WHERE u.email='probe-qa@job-hunter.test'").fetchone()
    assert p["schedule_frequency"] == "daily" and p["search_hour"] == probes.SEARCH_HOUR
    assert p["onboarding_complete"] == 1 and p["is_active"] == 1 and p["email_verified"] == 1
    assert "QA Automation Engineer" in json.loads(p["job_titles"]) and p["cv_summary"]


def test_switched_off_on_day_one_and_on_again_if_the_date_moves(monkeypatch):
    monkeypatch.delenv("JH_PROBES_UNTIL", raising=False)
    c = _db()
    _ensure(c, date(2026, 11, 30))
    off, _ = _ensure(c, date(2026, 12, 1))                       # Day 1 (Israel date): off
    assert not off["active"]
    assert c.execute("SELECT COUNT(*) FROM users WHERE is_active=1").fetchone()[0] == 0
    monkeypatch.setenv("JH_PROBES_UNTIL", "2026-12-15")
    on, _ = _ensure(c, date(2026, 12, 2))
    assert on["active"] and c.execute("SELECT COUNT(*) FROM users WHERE is_active=1").fetchone()[0] == 3


def test_none_created_after_the_end_date(monkeypatch):
    monkeypatch.delenv("JH_PROBES_UNTIL", raising=False)
    c = _db()
    out, made = _ensure(c, date(2026, 12, 3))
    assert out["created"] == [] and made == []


def test_an_admin_on_a_probe_address_is_never_touched(monkeypatch):
    monkeypatch.delenv("JH_PROBES_UNTIL", raising=False)
    c = _db()
    c.execute("INSERT INTO users (id, name, email, role) VALUES (9, 'X', 'probe-qa@job-hunter.test', 'admin')")
    c.execute("INSERT INTO user_profiles (user_id, schedule_frequency) VALUES (9, 'weekly')")
    _ensure(c, date(2026, 12, 3))                                # after the end: would switch it off
    r = c.execute("SELECT u.is_active, p.schedule_frequency FROM users u JOIN user_profiles p ON p.user_id=u.id "
                  "WHERE u.id=9").fetchone()
    assert r["is_active"] == 1 and r["schedule_frequency"] == "weekly"


def test_probes_are_test_addresses_and_not_people():
    assert testaccount.is_test_address("probe-design@job-hunter.test")
    assert testaccount.is_test_address(testaccount.TEST_EMAIL)
    assert not testaccount.is_test_address("someone@job-hunter.work")
    c = _db()
    c.executemany("INSERT INTO users (id, email, role) VALUES (?,?,?)", [
        (1, "owner@x", "admin"), (2, "dana@x", "user"), (3, testaccount.TEST_EMAIL, "user"),
        (4, "probe-qa@job-hunter.test", "user")])
    assert kpis.people(c, testaccount.TEST_EMAIL) == {2}


def test_supply_counts_new_roles_per_family_per_day():
    c = _db()
    rows = [
        # day 1: two QA roles on the employer's own page, one on a job board, one duplicate
        (1, "QA Automation Engineer", "Monday.com", "https://monday.com/careers/1", "2026-11-25 07:00:00"),
        (1, "Senior QA Engineer", "Wix", "https://boards.greenhouse.io/wix/jobs/2", "2026-11-25 07:10:00"),
        (2, "QA Automation Engineer", "Monday.com", "https://monday.com/careers/1", "2026-11-25 08:00:00"),
        (1, "QA Engineer", "Acme", "https://www.linkedin.com/jobs/view/3", "2026-11-25 07:20:00"),
        # day 2: a design role
        (3, "Product Designer", "Fiverr", "https://www.fiverr.com/careers/4", "2026-11-24 07:00:00"),
    ]
    for uid, title, comp, url, found in rows:
        c.execute("INSERT INTO jobs (user_id, title, company, url, found_date) VALUES (?,?,?,?,?)",
                  (uid, title, comp, url, found))
    out = probes.supply(c, days=3, now=NOW)
    today, yday = out["days"][0], out["days"][1]
    assert today["day"] == "2026-11-25" and yday["day"] == "2026-11-24"
    assert today["families"]["qa"] == {"found": 3, "postable": 2}     # LinkedIn counted as found, not postable
    assert yday["families"]["design"] == {"found": 1, "postable": 1}
    assert today["families"]["design"] == {"found": 0, "postable": 0}
    assert [p["key"] for p in out["probes"]] == ["qa", "design", "customer"]
    assert out["min_roles"] == 5 and out["until"] == "2026-12-01"
