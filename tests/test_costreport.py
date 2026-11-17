"""tests/test_costreport.py - running cost per active user (owner, 2026-11-16)."""
import sqlite3
from datetime import datetime, timezone

import costreport

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)


def _db():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT, role TEXT, is_active INTEGER DEFAULT 1)")
    c.execute("CREATE TABLE llm_usage (day TEXT, user_id INTEGER, purpose TEXT, model TEXT, calls INTEGER,"
              " prompt_tokens INTEGER, output_tokens INTEGER, total_tokens INTEGER)")
    c.execute("CREATE TABLE activity_log (user_id INTEGER, event_type TEXT, created_date TEXT)")
    c.executemany("INSERT INTO users (id, email, role) VALUES (?,?,?)", [(1, "owner@x", "admin"), (2, "a@x", "user"),
                                                       (3, "test-user@job-hunter.test", "user"), (4, "idle@x", "user")])
    return c


def test_output_is_billed_with_thinking_tokens():
    # 1M prompt + 1M output, of which the rest of total is thinking: billed as output.
    assert round(costreport.gemini_usd(1_000_000, 2_000_000, "gemini-2.5-flash"), 2) == 2.80
    assert round(costreport.gemini_usd(1_000_000, 2_000_000, "gemini-2.5-flash-lite"), 2) == 0.50


def test_per_active_user_counts_deciders_not_the_test_account(monkeypatch):
    monkeypatch.setenv("JH_HOSTING_USD_MONTH", "15")
    c = _db()
    c.executemany("INSERT INTO llm_usage VALUES (?,?,?,?,?,?,?,?)", [
        ("2026-11-10", 2, "search_scoring", "gemini-2.5-flash", 10, 1_000_000, 0, 1_200_000),
        ("2026-11-12", 3, "search_scoring", "gemini-2.5-flash", 5, 1_000_000, 0, 1_000_000),
        ("2026-11-12", None, "check_job_status", "gemini-9-ultra", 1, 1_000_000, 0, 1_000_000),
        ("2026-09-01", 2, "search_scoring", "gemini-2.5-flash", 99, 9_000_000, 0, 9_000_000),  # outside 30 days
    ])
    c.executemany("INSERT INTO activity_log VALUES (?,?,?)", [
        (1, "job_approved", "2026-11-15 08:00:00"), (2, "job_rejected", "2026-11-14 08:00:00"),
        (2, "bulk_approve", "2026-11-14 09:00:00"), (3, "job_approved", "2026-11-15 08:00:00"),
        (4, "jobs_searched", "2026-11-15 08:00:00"),                                     # a search is not a decision
    ])
    r = costreport.report(c, 30, now=NOW, test_user_ids=[3],
                          paid_sources={"x": {"label": "X", "month_used": 10, "free_allowance": 200}})
    # 0.30 + 0.2*2.50 = 0.80; test 0.30; unknown model priced as Flash 0.30
    assert r["gemini"]["usd"] == 1.40 and r["gemini"]["test_account_usd"] == 0.30
    assert r["gemini"]["unpriced"] == ["gemini-9-ultra"]
    assert r["hosting"] == {"set": True, "month_usd": 15.0, "usd": 15.0}
    assert r["paid_sources"]["usd"] == 0.0 and r["complete"] is True
    assert r["active_users"] == 2 and r["active_admins"] == 1                   # owner + user 2
    assert r["total_usd"] == 16.40 and r["per_active_user_usd"] == 8.20
    # per active user per day (row 12): user 1 on the 15th, user 2 on the 14th (twice) = 2 user-days
    assert r["active_user_days"] == 2 and r["per_active_user_day_usd"] == 8.2


def test_unset_hosting_and_a_source_past_its_allowance_are_said_not_guessed(monkeypatch):
    monkeypatch.delenv("JH_HOSTING_USD_MONTH", raising=False)
    r = costreport.report(_db(), 30, now=NOW,
                          paid_sources={"h": {"label": "Hirebase", "month_used": 501, "free_allowance": 500}})
    assert r["hosting"]["set"] is False and r["hosting"]["usd"] is None
    assert r["paid_sources"] == {"usd": None, "over_allowance": ["Hirebase"]}
    assert r["complete"] is False and r["per_active_user_usd"] is None        # nobody active: no division
