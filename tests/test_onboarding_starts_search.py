"""Finishing onboarding starts the user's first search (2026-11-11).

Before this, the last onboarding step saved the profile and the schedule and
queued nothing: a new account landed on an empty deck until its scheduled hour
(or until it found the Search button)."""
import json

import pytest

from tests.test_routes import Client, stack, users  # noqa: F401


@pytest.fixture
def quiet(stack, monkeypatch):
    monkeypatch.setattr(stack["app"], "send_email", lambda *a, **k: None)
    import ratelimit
    ratelimit.reset_all()


def _signup(stack, email):
    c = Client(stack["port"])
    st, _loc, _b = c.post_form("/register", {"name": "New User", "email": email,
                                             "password": "pw-long-enough-1", "password2": "pw-long-enough-1"})
    assert st == 302
    db = stack["db"].get_db()
    try:
        uid = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()["id"]
    finally:
        db.close()
    return c, uid


def _search_runs(stack, uid):
    db = stack["db"].get_db()
    try:
        return db.execute("SELECT COUNT(*) AS n FROM job_runs WHERE user_id=? AND kind='search'",
                          (uid,)).fetchone()["n"]
    finally:
        db.close()


def _finish(c):
    return c.post_json("/api/save-schedule", {"schedule_frequency": "daily", "search_hour": 9,
                                              "onboarding_complete": 1})


def test_finishing_onboarding_queues_the_first_search(stack, quiet):
    c, uid = _signup(stack, "onb-search-1@example.test")
    assert c.post_json("/api/save-profile", {"job_titles": ["Product Manager"], "keywords": ["B2C"],
                                             "locations": ["Tel Aviv"]})[0] == 200
    assert _search_runs(stack, uid) == 0
    assert _finish(c)[0] == 200
    assert _search_runs(stack, uid) == 1


def test_the_reply_says_a_search_started(stack, quiet):
    c, _uid = _signup(stack, "onb-search-2@example.test")
    c.post_json("/api/save-profile", {"job_titles": ["Data Analyst"]})
    st, _l, body = _finish(c)
    assert st == 200 and json.loads(body)["first_search_started"] is True


def test_nothing_to_search_for_starts_nothing(stack, quiet):
    c, uid = _signup(stack, "onb-search-3@example.test")
    st, _l, body = _finish(c)                      # skipped every step: no titles
    assert st == 200 and json.loads(body)["first_search_started"] is False
    assert _search_runs(stack, uid) == 0


def test_replaying_onboarding_does_not_start_another(stack, quiet):
    c, uid = _signup(stack, "onb-search-4@example.test")
    c.post_json("/api/save-profile", {"job_titles": ["QA Engineer"]})
    _finish(c)
    db = stack["db"].get_db()
    db.execute("UPDATE job_runs SET status='done' WHERE user_id=?", (uid,))
    db.commit(); db.close()
    _finish(c)                                     # /app?onboarding=1 sends the same save
    assert _search_runs(stack, uid) == 1


def test_saving_the_schedule_later_starts_nothing(stack, users, quiet):
    """Settings uses the same route without onboarding_complete."""
    db = stack["db"].get_db()
    uid = db.execute("SELECT id FROM users WHERE email='bob@example.test'").fetchone()["id"]
    db.close()
    before = _search_runs(stack, uid)
    assert users["b"].post_json("/api/save-schedule", {"search_hour": 10})[0] == 200
    assert _search_runs(stack, uid) == before
