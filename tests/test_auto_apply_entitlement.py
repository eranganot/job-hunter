"""
tests/test_auto_apply_entitlement.py - the paid gate, at the chokepoint.

The auto-apply entitlement was checked in two places, both of them upstream and
both of them the places a developer exercises by hand: saving the settings
toggle, and the apply triggered by approving a job. `run_job_apply` - which the
nightly scheduler and /api/run-apply both go through, and which is the only
code that actually submits anything - checked the user's FLAG and never their
PLAN.

The flag and the plan come apart without anyone touching them. Migration 10
added `users.plan TEXT DEFAULT 'free'` and left `auto_apply_enabled` exactly as
it was, so on the day it ran every existing account became a free account with
the paid feature still switched on. A downgrade does the same thing later. The
approve path already had an explicit branch for this case ("flag set,
entitlement gone") - the chokepoint did not.

Proven 2026-09-20 against a plan='free' user with the flag set and
APPLY_ENGINE_ENABLED=1: can_auto_apply returned False and run_job_apply
returned {"applied": 1}. It had been invisible because APPLY_ENGINE_ENABLED is
off in production - that is, invisible right up until the switch this launch is
about to throw.

These tests pin the gate to the chokepoint. They deliberately do NOT go through
a route: the whole failure was that the route-level checks were not the thing
doing the work.
"""
import pytest

from tests.test_routes import stack  # noqa: F401


def _make_user(database, email, plan, auto_apply, role="user"):
    conn = database.get_db()
    conn.execute(
        "INSERT INTO users (email, password_hash, salt, name, plan, role) "
        "VALUES (?,?,?,?,?,?)", (email, "x", "s", email.split("@")[0], plan, role))
    uid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO user_profiles (user_id, auto_apply_enabled) VALUES (?,?)",
                 (uid, 1 if auto_apply else 0))
    conn.execute(
        "INSERT INTO jobs (user_id, title, company, url, status) "
        "VALUES (?,?,?,?,'approved')",
        (uid, "PM", "Acme", "https://example.test/gate/%s" % email))
    conn.commit()
    conn.close()
    return uid


@pytest.fixture
def engine_on(stack, monkeypatch):
    """Kill switch ON and the network stubbed.

    With APPLY_ENGINE_ENABLED unset, run_job_apply returns at line one and every
    assertion below would pass without proving anything - which is precisely the
    reason this bug survived: production has the switch off.
    """
    monkeypatch.setenv("APPLY_ENGINE_ENABLED", "1")
    import apply_engine
    monkeypatch.setattr(apply_engine, "apply_to_job",
                        lambda *a, **k: {"success": False, "status": "manual_required",
                                         "error": "stub", "failure_type": "manual"},
                        raising=False)
    return stack


def test_a_free_user_with_the_flag_set_is_not_applied_for(engine_on):
    """The exact post-migration-10 shape: plan free, flag still on."""
    uid = _make_user(engine_on["db"], "free-gate@example.test", "free", True)
    out = engine_on["app"].run_job_apply(uid)
    assert out["skipped"] == "not_entitled", (
        "a free-plan user's approved queue reached the apply engine: %r" % (out,))
    assert out["applied"] == 0


def test_an_empty_plan_is_not_a_paid_plan(engine_on):
    """Fail closed. A row written before m0010, or by hand, has no plan at all."""
    uid = _make_user(engine_on["db"], "noplan-gate@example.test", None, True)
    assert engine_on["app"].run_job_apply(uid)["skipped"] == "not_entitled"


def test_a_premium_user_is_applied_for(engine_on):
    """The gate has to let the paying case through, or it is just an outage."""
    uid = _make_user(engine_on["db"], "premium-gate@example.test", "premium", True)
    out = engine_on["app"].run_job_apply(uid)
    assert out.get("skipped") != "not_entitled", out
    assert out["applied"] >= 1, out


def test_admin_is_applied_for_regardless_of_plan(engine_on):
    """Admin access must never depend on a plan column someone has to set."""
    uid = _make_user(engine_on["db"], "adminrole-gate@example.test", "free", True,
                     role="admin")
    out = engine_on["app"].run_job_apply(uid)
    assert out.get("skipped") != "not_entitled", out


def test_the_flag_still_wins_when_it_is_off(engine_on):
    """A paid user who turned it off is 'auto_apply_disabled', not 'not_entitled'.

    The two reasons must stay distinguishable: one is a setting the user chose,
    the other is a bill. They are read by support and by the logs.
    """
    uid = _make_user(engine_on["db"], "premium-off@example.test", "premium", False)
    assert engine_on["app"].run_job_apply(uid)["skipped"] == "auto_apply_disabled"
