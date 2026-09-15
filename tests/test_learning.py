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


# ── The location penalty must not fight the user's own preferred city ────────
#
# Found on staging 2026-09-15: a 97% Tel Aviv role carried the badge
# "Location you've passed on" while Tel Aviv was the user's FIRST preferred
# location. The comment above that branch claimed it "never fights their own
# preferred city"; nothing checked. The title branch two blocks up does guard
# against the user's own targets - the location branch never did.

def _loc_signals(disliked):
    """A hand-built signal bundle. Deliberately NOT the module's _signals(uid),
    which reads real rows - these tests are about the penalty's arithmetic, not
    about how signals are gathered."""
    return {"bad_companies": set(), "passed_companies": {},
            "disliked_title_tokens": set(), "disliked_locations": set(disliked)}


def test_a_job_in_a_preferred_location_is_not_demoted_for_its_location():
    from ai_analysis import compute_feedback_penalty
    job = {"company": "Acme", "title": "VP Product", "location": "Tel Aviv, Israel"}
    profile = {"locations": '["Tel Aviv", "Remote"]', "job_titles": '["VP Product"]'}
    penalty, reason = compute_feedback_penalty(job, _loc_signals({"tel aviv, israel"}), profile)
    assert penalty == 0, (penalty, reason)
    assert "Location" not in reason


def test_a_job_outside_the_preferred_locations_is_still_demoted():
    """The guard must not switch the penalty off altogether - a location the
    user keeps passing on, and never asked for, should still rank lower."""
    from ai_analysis import compute_feedback_penalty
    job = {"company": "Acme", "title": "VP Product", "location": "Haifa, Israel"}
    profile = {"locations": '["Tel Aviv"]', "job_titles": '["VP Product"]'}
    penalty, reason = compute_feedback_penalty(job, _loc_signals({"haifa"}), profile)
    assert penalty == 10, (penalty, reason)
    assert reason == "Location you've passed on"


def test_with_no_stated_locations_the_old_behaviour_stands():
    """A user who never said where they want to work has no preference for the
    guard to protect, so the signal is all we have."""
    from ai_analysis import compute_feedback_penalty
    job = {"company": "Acme", "title": "VP Product", "location": "Tel Aviv, Israel"}
    penalty, _r = compute_feedback_penalty(job, _loc_signals({"tel aviv"}), {"locations": None})
    assert penalty == 10
