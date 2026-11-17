"""tests/test_kpis.py - the launch numbers (owner-approved definitions, 2026-11-16)."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import kpis

NOW = datetime(2026, 11, 30, 12, 0, tzinfo=timezone.utc)
TEST = "test-user@job-hunter.test"


def t(days_ago, hours=0):
    return (NOW - timedelta(days=days_ago, hours=hours)).strftime("%Y-%m-%d %H:%M:%S")


def _db():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT, role TEXT, signup_attr TEXT, is_active INTEGER DEFAULT 1)")
    c.execute("CREATE TABLE activity_log (user_id INTEGER, event_type TEXT, details TEXT, created_date TEXT)")
    c.execute("CREATE TABLE jobs (user_id INTEGER, status TEXT, applied_via TEXT, applied_date TEXT, found_date TEXT)")
    c.execute("CREATE TABLE funnel_events (user_id INTEGER, step TEXT, created_at TEXT)")
    c.executemany("INSERT INTO users (id, email, role, signup_attr) VALUES (?,?,?,?)", [
        (1, "owner@x", "admin", None), (2, "a@x", "user", json.dumps({"utm_source": "facebook", "utm_medium": "group", "noa": "b"})),
        (3, "b@x", "user", json.dumps({"noa": "a"})), (4, TEST, "user", None), (5, "old@x", "user", None)])
    return c


def test_rates_count_people_and_leave_out_admin_and_test_account():
    c = _db()
    c.executemany("INSERT INTO activity_log VALUES (?,?,?,?)", [
        (1, "job_approved", "", t(1)),                      # admin: never counted
        (4, "job_approved", "", t(1)),                      # test account: never counted
        (2, "job_approved", "", t(1)), (2, "job_rejected", "", t(1)), (2, "bulk_reject", "Passed on 2 job(s) at once", t(2)),
        (3, "job_approved", "", t(3)), (3, "jobs_searched", "", t(3)),
        (2, "job_approved", "", t(9)),                      # the week before
    ])
    c.executemany("INSERT INTO jobs (user_id, status, applied_via, applied_date) VALUES (?,?,?,?)", [
        (2, "applied", "manual", t(1)[:10]), (3, "applied", "bulk", t(1)[:10]),   # bulk is not an application
        (3, "applied", "engine", t(2).replace(" ", "T")), (1, "applied", "engine", t(1)[:10])])
    k = kpis.today(c, TEST, now=NOW)["kpis"]
    assert k["weekly_active_swipers"] == {"value": 2, "prev": 1}
    assert k["approve_rate"]["value"] == round(100 * (0.25 + 1.0) / 2) and k["approve_rate"]["prev"] == 100
    assert k["applied_rate"]["value"] == 100                 # 1 of 1 and 1 of 1
    assert k["active_applicants"]["value"] == 2


def test_activation_time_to_picks_and_day7_return():
    c = _db()
    c.executemany("INSERT INTO funnel_events VALUES (?,?,?)", [
        (2, "signup", t(5)), (2, "first_search", t(5, -0.5)), (2, "first_approve", t(4)),    # activated in 24h
        (3, "signup", t(3)), (3, "first_search", t(3, -1.5)),                                 # not activated
        (5, "signup", t(1)),                                                                  # < 48h old: not judged yet
        (4, "signup", t(5)), (4, "first_approve", t(5)),                                      # test account
    ])
    k = kpis.today(c, TEST, now=NOW)["kpis"]
    assert k["activation_48h"]["value"] == 50 and (k["activation_48h"]["n"], k["activation_48h"]["of"]) == (1, 2)
    assert k["time_to_first_picks"]["value"] == 60           # median of 30 and 90 minutes
    c2 = _db()
    c2.executemany("INSERT INTO funnel_events VALUES (?,?,?)", [(2, "signup", t(12)), (3, "signup", t(10))])
    c2.executemany("INSERT INTO activity_log VALUES (?,?,?,?)", [
        (2, "job_approved", "", t(5)),                       # 7 days after signing up: back
        (3, "job_approved", "", t(9))])                      # 1 day after: not a return
    r = kpis.today(c2, TEST, now=NOW)["kpis"]["day7_return"]
    assert (r["value"], r["n"], r["of"]) == (50, 1, 2)


def test_sources_and_noa_variants():
    c = _db()
    c.executemany("INSERT INTO funnel_events VALUES (?,?,?)", [
        (2, "signup", t(3)), (2, "first_approve", t(3)), (3, "signup", t(4)), (5, "signup", t(6)), (4, "signup", t(2))])
    c.execute("INSERT INTO jobs (user_id, status, applied_via, applied_date) VALUES (2,'applied','engine',?)", (t(1)[:10],))
    s = kpis.by_source(c, TEST, 30, now=NOW)
    got = {r["name"]: (r["signups"], r["activated"], r["applicants"]) for r in s["sources"]}
    assert got == {"facebook / group": (1, 1, 1), "direct": (1, 0, 0), "before tracking": (1, 0, 0)}
    var = {r["name"]: r["signups"] for r in s["noa_variants"]}
    assert var == {"b": 1, "a": 1, "before tracking": 1}


def test_disabled_accounts_are_not_people():
    """Owner, 2026-11-17: test sign-ups he disabled must leave every count."""
    c = _db()
    c.execute("UPDATE users SET is_active=0 WHERE id=3")
    assert kpis.people(c, TEST) == {2, 5}
    c.executemany("INSERT INTO funnel_events VALUES (?,?,?)", [(2, "signup", t(3)), (3, "signup", t(4))])
    assert [r["signups"] for r in kpis.by_source(c, TEST, 30, now=NOW)["sources"]] == [1]


def test_first_applications_and_new_matches_a_day():
    """Row 12: first real application (median days from sign-up), and matches found
    per person per day before the cap, with the share of days under it."""
    import sqlite3
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, role TEXT, email TEXT, is_active INTEGER DEFAULT 1)")
    c.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY, user_id INTEGER, status TEXT, applied_via TEXT,"
              " applied_date TEXT, found_date TEXT)")
    c.execute("CREATE TABLE funnel_events (user_id INTEGER, step TEXT, created_at TEXT)")
    c.execute("CREATE TABLE activity_log (user_id INTEGER, event_type TEXT, details TEXT, created_date TEXT)")
    now = datetime(2026, 12, 20, 12, 0, tzinfo=timezone.utc)
    ts = lambda d: (now - timedelta(days=d)).strftime("%Y-%m-%d %H:%M:%S")
    c.executemany("INSERT INTO users (id, role, email) VALUES (?,?,?)", [(1, "user", "a@x"), (2, "user", "b@x")])
    c.executemany("INSERT INTO funnel_events VALUES (?,?,?)", [(1, "signup", ts(10)), (2, "signup", ts(3))])
    c.executemany("INSERT INTO jobs (user_id, status, applied_via, applied_date) VALUES (?,?,?,?)", [
        (1, "applied", "manual", ts(4)), (1, "applied", "engine", ts(2)),    # first one this week, 6 days in
        (2, "applied", "bulk", ts(1))])                                      # bulk is not an application
    for d, n in ((1, 9), (2, 3)):                                            # user 1: 9 then 3 matches
        c.executemany("INSERT INTO jobs (user_id, status, found_date) VALUES (1,'new',?)", [(ts(d),)] * n)
    out = kpis.today(c, "test@x", now=now, cap=7)["kpis"]
    assert out["first_applications"]["value"] == 1 and out["first_applications"]["median_days"] == 6.0
    nm = out["new_matches_per_day"]
    assert nm["value"] == 6.0 and nm["under_cap_pct"] == 50 and nm["of"] == 2 and nm["cap"] == 7
