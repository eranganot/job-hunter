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
    c.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT, role TEXT, signup_attr TEXT)")
    c.execute("CREATE TABLE activity_log (user_id INTEGER, event_type TEXT, details TEXT, created_date TEXT)")
    c.execute("CREATE TABLE jobs (user_id INTEGER, status TEXT, applied_via TEXT, applied_date TEXT)")
    c.execute("CREATE TABLE funnel_events (user_id INTEGER, step TEXT, created_at TEXT)")
    c.executemany("INSERT INTO users VALUES (?,?,?,?)", [
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
    c.executemany("INSERT INTO jobs VALUES (?,?,?,?)", [
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
    c.execute("INSERT INTO jobs VALUES (2,'applied','engine',?)", (t(1)[:10],))
    s = kpis.by_source(c, TEST, 30, now=NOW)
    got = {r["name"]: (r["signups"], r["activated"], r["applicants"]) for r in s["sources"]}
    assert got == {"facebook / group": (1, 1, 1), "direct": (1, 0, 0), "before tracking": (1, 0, 0)}
    var = {r["name"]: r["signups"] for r in s["noa_variants"]}
    assert var == {"b": 1, "a": 1, "before tracking": 1}
