"""
tests/test_entitlements.py - who may turn auto-apply on.

Eran: "auto-apply should be with toggle so the user could turn it on/off if he
likes (and more... the toggle should be clickable only for paying users (and
admin))."

The important half is the half that is not the button. A disabled control is a
hint to an honest user; the POST behind it is three lines of curl. So the gate
lives on the server, and these tests attack it the way a user who wanted the
feature for free would.
"""
import json

import pytest

import entitlements
from tests.test_routes import (Client, _new_job, _job_row, _user_id,  # noqa: F401
                               stack, users)                          # noqa: F401


# ── The helper itself, which everything else trusts ─────────────────────────

@pytest.mark.parametrize("user, expected", [
    ({"role": "admin", "plan": "free"},    True),   # admin never depends on a plan row
    ({"role": "admin", "plan": None},      True),
    ({"role": "user",  "plan": "premium"}, True),
    ({"role": "user",  "plan": "expert"},  True),
    ({"role": "user",  "plan": "free"},    False),
    ({"role": "user",  "plan": ""},        False),
    ({"role": "user",  "plan": None},      False),
    ({"role": "user",  "plan": "  Premium "}, True),   # stored casing must not decide
    ({"role": "user",  "plan": "gold"},    False),     # unknown plan fails CLOSED
    ({"role": "",      "plan": "free"},    False),
    (None,                                 False),
])
def test_can_auto_apply(user, expected):
    assert entitlements.can_auto_apply(user) is expected


def test_an_unknown_plan_never_opens_a_paid_feature():
    """A typo in a plan name should cost someone a feature they can ask about,
    not silently hand out the thing that costs real money to run."""
    for bogus in ("gold", "PRO", "premium_v2", "admin", "0", "free "):
        assert entitlements.plan_of({"role": "user", "plan": bogus}) in ("free", "premium", "expert")
        if bogus not in ("free ",):
            assert entitlements.can_auto_apply({"role": "user", "plan": bogus}) is False


# ── The gate, over real HTTP ────────────────────────────────────────────────

def _set_plan(database, email, plan):
    conn = database.get_db()
    conn.execute("UPDATE users SET plan=? WHERE email=?", (plan, email))
    conn.commit()
    conn.close()


def _auto_apply_flag(database, email):
    conn = database.get_db()
    row = conn.execute(
        "SELECT p.auto_apply_enabled FROM user_profiles p JOIN users u ON u.id=p.user_id "
        "WHERE u.email=?", (email,)).fetchone()
    conn.close()
    return row["auto_apply_enabled"] if row else None


def test_a_free_user_cannot_switch_auto_apply_on(stack, users):
    """The attack the disabled button does not stop."""
    _set_plan(stack["db"], "alice@example.test", "free")
    status, _loc, body = users["a"].post_json("/api/save-schedule", {"auto_apply_enabled": 1})
    assert status == 403, f"a free account switched auto-apply on (status {status})"
    assert json.loads(body).get("code") == "upgrade_required"
    assert not _auto_apply_flag(stack["db"], "alice@example.test")


def test_a_paid_user_can(stack, users):
    _set_plan(stack["db"], "alice@example.test", "premium")
    status, _loc, _b = users["a"].post_json("/api/save-schedule", {"auto_apply_enabled": 1})
    assert status == 200
    assert _auto_apply_flag(stack["db"], "alice@example.test")
    _set_plan(stack["db"], "alice@example.test", "free")


def test_admin_can_without_a_paid_plan(stack, users):
    _set_plan(stack["db"], "admin@example.test", "free")
    status, _loc, _b = users["admin"].post_json("/api/save-schedule", {"auto_apply_enabled": 1})
    assert status == 200, "admin must not need a plan row to be right"
    assert _auto_apply_flag(stack["db"], "admin@example.test")


def test_turning_it_off_is_never_gated(stack, users):
    """Nobody should need a subscription to stop the robot. A downgraded user
    who could not switch it off would be the worst possible failure here."""
    _set_plan(stack["db"], "alice@example.test", "premium")
    users["a"].post_json("/api/save-schedule", {"auto_apply_enabled": 1})
    _set_plan(stack["db"], "alice@example.test", "free")      # subscription lapses
    status, _loc, _b = users["a"].post_json("/api/save-schedule", {"auto_apply_enabled": 0})
    assert status == 200, "a downgraded user was trapped with auto-apply ON"
    assert not _auto_apply_flag(stack["db"], "alice@example.test")


def test_the_rest_of_the_schedule_still_saves_for_a_free_user(stack, users):
    """The gate must refuse one field, not the request."""
    _set_plan(stack["db"], "alice@example.test", "free")
    status, _loc, _b = users["a"].post_json(
        "/api/save-schedule", {"search_hour": 9, "schedule_frequency": "weekly", "search_day_of_week": 3})
    assert status == 200
    conn = stack["db"].get_db()
    row = conn.execute(
        "SELECT p.search_hour, p.search_day_of_week FROM user_profiles p "
        "JOIN users u ON u.id=p.user_id WHERE u.email=?", ("alice@example.test",)).fetchone()
    conn.close()
    assert row["search_hour"] == 9 and row["search_day_of_week"] == 3


def test_a_stale_flag_does_not_make_the_app_apply(stack, users):
    """A row set while the user was paying, or before this gate existed, must
    not still trigger an application after a downgrade. The flag is checked at
    the point of use, not only at the point of setting."""
    _set_plan(stack["db"], "alice@example.test", "premium")
    users["a"].post_json("/api/save-schedule", {"auto_apply_enabled": 1})
    _set_plan(stack["db"], "alice@example.test", "free")

    fired = []
    app_module = stack["app"]
    original = app_module._trigger_apply_bg
    app_module._trigger_apply_bg = lambda uid, jid: fired.append((uid, jid))
    try:
        job_id = _new_job(stack["db"], _user_id(stack["db"], "alice@example.test"))
        status, _loc, _b = users["a"].post_json(f"/api/jobs/{job_id}/approve", {})
        assert status == 200
    finally:
        app_module._trigger_apply_bg = original
    assert fired == [], "approving applied on behalf of a user whose plan does not allow it"


def test_me_reports_the_plan_so_the_ui_can_agree_with_the_server(stack, users):
    status, _loc, body = users["a"].get("/api/me")
    assert status == 200
    assert "plan" in json.loads(body), "/api/me does not carry the plan - the UI would have to guess"
