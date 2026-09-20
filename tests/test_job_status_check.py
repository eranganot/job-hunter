"""
tests/test_job_status_check.py - "is this posting still open?"

Phase 4's gap table listed /api/jobs/<id>/check-status as a legacy feature to
port. It is not a port: **the endpoint had no implementation**. It validated
that the job existed and had a URL, then fell out of the branch - no response,
no return, and the database connection left open. A caller got a bare 404 with
an empty body on a perfectly valid job, so the legacy dashboard's "verify if
still open" button has never worked. Proven by driving the route, 2026-09-20.

Built on apply_engine.check_url_alive, which already handles a 200-OK page that
reads as closed and a parked domain - and is what the overnight link sweeper
uses, so a job checked by hand and the same job swept later cannot disagree.
"""
import json

import pytest

from tests.test_routes import (Client, stack, users,          # noqa: F401
                               _new_job, _job_row, _user_id)  # noqa: F401


@pytest.fixture
def alive(stack, monkeypatch):
    """check_url_alive is the only thing here that would touch the network."""
    import apply_engine
    def _set(value):
        monkeypatch.setattr(apply_engine, "check_url_alive", lambda url, timeout=8: value)
    return _set


def test_an_open_posting_is_reported_open_and_marked_verified(stack, users, alive):
    alive(True)
    uid = _user_id(stack["db"], "alice@example.test")
    job_id = _new_job(stack["db"], uid)
    status, _loc, body = users["a"].post_json(f"/api/jobs/{job_id}/check-status", {})
    assert status == 200, "the endpoint answered %s (it used to answer a bare 404)" % status
    payload = json.loads(body)
    assert payload["open"] is True
    row = _job_row(stack["db"], job_id)
    assert row["url_verified"] == 1
    assert row["status_check"] == "open"
    assert row["status_checked_date"], "the check was not dated"


def test_a_dead_posting_is_flagged_but_NOT_thrown_away(stack, users, alive):
    """A manual check is the user asking a question, not asking for the job to
    be deleted. Only the sweeper retires a dead link."""
    alive(False)
    uid = _user_id(stack["db"], "alice@example.test")
    job_id = _new_job(stack["db"], uid)
    status, _loc, body = users["a"].post_json(f"/api/jobs/{job_id}/check-status", {})
    assert status == 200
    assert json.loads(body)["open"] is False
    row = _job_row(stack["db"], job_id)
    assert row["url_verified"] == 0
    assert row["status"] == "new", "a manual check rejected the job"
    assert row["rejected_by"] is None


def test_a_job_with_no_url_is_a_clean_400(stack, users, alive):
    alive(True)
    uid = _user_id(stack["db"], "alice@example.test")
    job_id = _new_job(stack["db"], uid)
    conn = stack["db"].get_db()
    conn.execute("UPDATE jobs SET url='' WHERE id=?", (job_id,))
    conn.commit(); conn.close()
    status, _loc, _b = users["a"].post_json(f"/api/jobs/{job_id}/check-status", {})
    assert status == 400


def test_another_users_job_is_not_checkable(stack, users, alive):
    alive(True)
    victim = _new_job(stack["db"], _user_id(stack["db"], "alice@example.test"))
    status, _loc, _b = users["b"].post_json(f"/api/jobs/{victim}/check-status", {})
    assert status == 404
    assert _job_row(stack["db"], victim)["status_check"] is None


def test_a_network_failure_is_reported_not_swallowed(stack, users, monkeypatch):
    """If the checker itself blows up, say so. Reporting "closed" would tell the
    user their job is gone when the truth is that we could not look."""
    import apply_engine
    def boom(url, timeout=8):
        raise OSError("name resolution failed")
    monkeypatch.setattr(apply_engine, "check_url_alive", boom)
    uid = _user_id(stack["db"], "alice@example.test")
    job_id = _new_job(stack["db"], uid)
    status, _loc, _b = users["a"].post_json(f"/api/jobs/{job_id}/check-status", {})
    assert status == 502, "a checker crash was reported as %s" % status
    row = _job_row(stack["db"], job_id)
    assert row["url_verified"] is None, "an unreachable check wrote a verdict anyway"


def test_it_requires_a_session(stack):
    status, _loc, _b = Client(stack["port"]).post_json("/api/jobs/1/check-status", {})
    assert status in (302, 401, 403)
