"""
tests/test_admin_users.py - enabling and disabling accounts.

Public signup needs a way to switch an abusive account off. The endpoint
existed with no UI; building the button found that it let the admin disable
THEMSELVES, which is a one-click unrecoverable lockout: get_session_user
requires is_active=1, so the next request 302s to /login and the undo answers
401. One admin account, no second door. Proven by driving the route.
"""
import json

import pytest

from tests.test_routes import (Client, stack, users, _user_id)  # noqa: F401


def _active(db, email):
    conn = db.get_db()
    row = conn.execute("SELECT is_active FROM users WHERE email=?", (email,)).fetchone()
    conn.close()
    return row["is_active"]


def test_an_admin_cannot_disable_themselves(stack, users):
    db = stack["db"]
    admin_id = _user_id(db, "admin@example.test")
    status, _loc, body = users["admin"].post_json(f"/api/admin/users/{admin_id}/toggle", {})
    assert status == 400, "the admin was allowed to lock themselves out"
    assert json.loads(body).get("code") == "cannot_disable_self"
    assert _active(db, "admin@example.test") == 1


def test_the_admin_is_still_usable_afterwards(stack, users):
    """The half that matters: the refusal must leave the session alive. A guard
    that returns 400 AFTER writing is not a guard."""
    db = stack["db"]
    admin_id = _user_id(db, "admin@example.test")
    users["admin"].post_json(f"/api/admin/users/{admin_id}/toggle", {})
    status, _loc, _b = users["admin"].get("/api/me")
    assert status == 200, "the admin's session died despite the refusal"


def test_disabling_another_user_works_and_reports_the_new_state(stack, users):
    db = stack["db"]
    target = _user_id(db, "bob@example.test")
    status, _loc, body = users["admin"].post_json(f"/api/admin/users/{target}/toggle", {})
    assert status == 200
    assert json.loads(body)["is_active"] == 0, "the response must say what the state now IS"
    assert _active(db, "bob@example.test") == 0

    status, _loc, body = users["admin"].post_json(f"/api/admin/users/{target}/toggle", {})
    assert json.loads(body)["is_active"] == 1
    assert _active(db, "bob@example.test") == 1


def test_a_disabled_user_cannot_use_the_app(stack, users):
    """Otherwise the switch is decorative."""
    db = stack["db"]
    target = _user_id(db, "bob@example.test")
    users["admin"].post_json(f"/api/admin/users/{target}/toggle", {})
    try:
        status, location, _b = users["b"].get("/api/me")
        assert status != 200, "a disabled account was still served"
    finally:
        users["admin"].post_json(f"/api/admin/users/{target}/toggle", {})


def test_toggling_a_user_that_does_not_exist_is_a_404(stack, users):
    """It used to answer {"success": true} for any number at all, which would
    have made a typo in the UI look like it worked."""
    status, _loc, _b = users["admin"].post_json("/api/admin/users/999999/toggle", {})
    assert status == 404


def test_a_non_admin_cannot_toggle_anyone(stack, users):
    db = stack["db"]
    target = _user_id(db, "alice@example.test")
    status, _loc, _b = users["a"].post_json(f"/api/admin/users/{target}/toggle", {})
    assert status == 403
    assert _active(db, "alice@example.test") == 1


def test_the_selftest_probe_is_read_only_and_admin_only(stack, users):
    """The 8-week ops item. It has never been run because running it meant
    hand-crafting a request; it is about to get a button, so it gets a test."""
    status, _loc, _b = users["a"].get("/api/admin/apply-selftest")
    assert status in (302, 403), "a non-admin reached the apply diagnostics"

    status, _loc, body = users["admin"].get("/api/admin/apply-selftest")
    assert status == 200
    diag = json.loads(body)
    for key in ("playwright_importable", "apply_engine_enabled_env", "gemini_key_present"):
        assert key in diag, "the probe no longer reports %r" % key


# ── Every admin endpoint must REFUSE cleanly, not crash ─────────────────────
#
# Five call sites passed `status=403` to send_json(), whose parameter is `code`.
# Every one raised TypeError while composing the refusal, so a non-admin got a
# 500 and a traceback instead of a 403. The gate still held - the request never
# reached the sensitive code - but the app reported "I am broken" where it meant
# "you may not", and nothing had ever exercised these branches, which is exactly
# why it survived. Found 2026-09-15 by the first test to hit one.

ADMIN_GETS = [
    "/api/admin/apply-selftest",
    "/api/admin/apply-test",
    "/api/admin/queue-stats",
    "/api/admin/users",
]


@pytest.mark.parametrize("path", ADMIN_GETS)
def test_a_non_admin_is_refused_not_crashed(stack, users, path):
    status, location, _b = users["a"].get(path)
    assert status != 500, "%s answered 500 - the refusal path itself is broken" % path
    assert status in (302, 403), "%s answered %s" % (path, status)


@pytest.mark.parametrize("path", ADMIN_GETS)
def test_an_admin_is_not_refused(stack, users, path):
    """Paired: if the tests above passed because everything 403s, the endpoints
    would be uniformly broken and the guard worthless."""
    status, _loc, _b = users["admin"].get(path)
    assert status == 200, "%s refuses the admin too (%s)" % (path, status)


def test_apply_test_reports_a_missing_job_as_not_found(stack, users):
    """The same TypeError sat on this path: a bad job_id 500'd instead of 404."""
    status, _loc, _b = users["admin"].get("/api/admin/apply-test?job_id=999999")
    assert status == 404, "expected 404 for an unknown job, got %s" % status
