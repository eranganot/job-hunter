"""APPLY_ENGINE_ENABLED is on in production for the owner's testing, but only for
admins (2026-11-09: "for now I want it to be applicable only for me"). A user
who switched auto-apply on in Settings must not reach the engine until
APPLY_ENGINE_FOR_ALL is also set."""
import pytest

from tests.test_routes import stack, users  # noqa: F401


def _user(stack, email, role):
    conn = stack["db"].get_db()
    conn.execute("DELETE FROM users WHERE email=?", (email,))
    conn.execute("INSERT INTO users (email, password_hash, salt, name, role, email_verified_at) VALUES (?,?,?,?,?,?)", (email, "x", "s", "T", role, "2026-01-01"))
    uid = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()["id"]
    conn.commit()
    conn.close()
    return uid


@pytest.fixture
def app_mod(stack, monkeypatch):
    app = stack["app"]
    app._st = stack
    monkeypatch.setenv("APPLY_ENGINE_ENABLED", "1")
    monkeypatch.delenv("APPLY_ENGINE_FOR_ALL", raising=False)
    return app


def test_the_engine_is_admins_only_while_testing(app_mod):
    admin = _user(app_mod._st, "gate-admin@example.test", "admin")
    member = _user(app_mod._st, "gate-user@example.test", "user")
    assert app_mod._engine_on_for(admin) is True
    assert app_mod._engine_on_for({"id": admin, "role": "admin"}) is True
    assert app_mod._engine_on_for(member) is False, "a regular user reached the engine while it is admins-only"
    assert app_mod._engine_on_for({"id": member, "role": "user"}) is False


def test_run_job_apply_skips_a_regular_user(app_mod):
    member = _user(app_mod._st, "gate-user2@example.test", "user")
    out = app_mod.run_job_apply(member)
    assert out.get("skipped") == "apply_engine_disabled", out


def test_for_all_opens_it_and_the_master_switch_still_rules(app_mod, monkeypatch):
    member = _user(app_mod._st, "gate-user3@example.test", "user")
    monkeypatch.setenv("APPLY_ENGINE_FOR_ALL", "1")
    assert app_mod._engine_on_for(member) is True
    monkeypatch.delenv("APPLY_ENGINE_ENABLED")
    admin = _user(app_mod._st, "gate-admin3@example.test", "admin")
    assert app_mod._engine_on_for(admin) is False


def test_apply_route_tells_auto_from_manual():
    import apply_engine as ae
    assert ae.apply_route("https://boards.greenhouse.io/acme/jobs/1") == "auto"
    assert ae.apply_route("https://acme.com/careers/?gh_jid=12") == "auto"
    assert ae.apply_route("https://jobs.lever.co/acme/abc") == "auto"
    assert ae.apply_route("https://www.linkedin.com/jobs/view/123") == "board"
    assert ae.apply_route("https://acme.wd3.myworkdayjobs.com/en-US/x/job/1") == "account"
    assert ae.apply_route("https://careers.acme.com/jobs/1") == "site"
    assert ae.apply_route("") == "none"


def test_apply_selected_is_admins_only_and_runs_only_the_users_approved_jobs(stack, users, monkeypatch):
    app, db = stack["app"], stack["db"]
    monkeypatch.setenv("APPLY_ENGINE_ENABLED", "1")
    monkeypatch.delenv("APPLY_ENGINE_FOR_ALL", raising=False)
    started = []
    monkeypatch.setattr(app, "_apply_selected_bg", lambda uid, ids: started.append((uid, list(ids))))
    monkeypatch.setattr(app.entitlements, "can_auto_apply", lambda u: True)
    conn = db.get_db()
    admin = conn.execute("SELECT id FROM users WHERE email='admin@example.test'").fetchone()["id"]
    alice = conn.execute("SELECT id FROM users WHERE email='alice@example.test'").fetchone()["id"]
    conn.execute("UPDATE users SET role='admin' WHERE id=?", (admin,))
    conn.execute("UPDATE users SET email_verified_at='2026-01-01' WHERE id IN (?,?)", (admin, alice))
    ids = []
    for owner, status, ast in ((admin, "approved", None), (admin, "approved", "applying"),
                               (admin, "new", None), (alice, "approved", None), (admin, "approved", "failed")):
        conn.execute("INSERT INTO jobs (user_id, title, company, url, status, apply_status) VALUES (?,?,?,?,?,?)",
                     (owner, "PM", "Acme", "https://jobs.lever.co/acme/sel-%d" % len(ids), status, ast))
        ids.append(conn.execute("SELECT MAX(id) AS m FROM jobs").fetchone()["m"])
    conn.commit()
    conn.close()
    r = users["a"].post_json("/api/jobs/apply-selected", {"ids": ids})
    assert r[0] == 409, "a regular user started auto-apply while it is admins-only: %r" % (r,)
    r = users["admin"].post_json("/api/jobs/apply-selected", {"ids": ids})
    assert r[0] == 200, r
    assert started == [(admin, [ids[0], ids[4]])], started
