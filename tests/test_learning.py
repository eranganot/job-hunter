"""Tests for feedback decay (#18) and location capture/penalty (#17).

Uses the real schema via db.init_db() on a temp file so the rejected_patterns
.location migration column exists.
"""
import datetime as _dt
import pytest
import db as database
from ai_analysis import compute_feedback_penalty


@pytest.fixture
def uid(tmp_path):
    database.set_db_path(str(tmp_path / "t.db"))
    database.init_db()
    conn = database.get_db()
    conn.execute(
        "INSERT INTO users (name,email,password_hash,salt) VALUES (?,?,?,?)",
        ("T", "t@e.com", "h", "s"),
    )
    conn.commit()
    u = conn.execute("SELECT id FROM users WHERE email=?", ("t@e.com",)).fetchone()[0]
    conn.close()
    return u


def _add(uid, company, title="PM", notes="", location=None, age_days=0):
    created = (_dt.datetime.now() - _dt.timedelta(days=age_days)).isoformat()
    conn = database.get_db()
    conn.execute(
        "INSERT INTO rejected_patterns (user_id,company,title,notes,location,created_date) VALUES (?,?,?,?,?,?)",
        (uid, company, title, notes, location, created),
    )
    conn.commit()
    conn.close()


def _signals(uid):
    conn = database.get_db()
    try:
        return database.get_feedback_signals(conn, uid)
    finally:
        conn.close()


class TestDecay:
    def test_recent_passes_weigh_more_than_old(self, uid):
        _add(uid, "FreshCo", age_days=1)
        _add(uid, "FreshCo", age_days=2)
        _add(uid, "StaleCo", age_days=400)
        pc = _signals(uid)["passed_companies"]
        assert pc["freshco"] > 1.5
        assert pc["staleco"] < 0.2

    def test_old_pass_no_longer_penalizes_but_recent_does(self, uid):
        _add(uid, "StaleCo", age_days=400)
        pen, _ = compute_feedback_penalty({"company": "StaleCo", "title": "PM"}, _signals(uid))
        assert pen == 0
        _add(uid, "HotCo", age_days=1)
        _add(uid, "HotCo", age_days=1)
        pen2, _ = compute_feedback_penalty({"company": "HotCo", "title": "PM"}, _signals(uid))
        assert pen2 >= 20


class TestLocation:
    def test_location_only_penalizes_with_location_reason(self, uid):
        _add(uid, "A", notes="Wrong seniority level", location="Berlin, Germany", age_days=1)
        _add(uid, "B", notes="Wrong seniority level", location="Berlin, Germany", age_days=1)
        assert not _signals(uid)["disliked_locations"]
        _add(uid, "C", notes="Wrong location", location="Berlin, Germany", age_days=1)
        _add(uid, "D", notes="Wrong location", location="Berlin, Germany", age_days=1)
        sig = _signals(uid)
        assert any("berlin" in l for l in sig["disliked_locations"])
        pen, reason = compute_feedback_penalty(
            {"company": "Z", "title": "PM", "location": "Berlin, Germany"}, sig
        )
        assert pen >= 10 and "Location" in reason
