"""Ticket 2026-11-11: no new-user alert reached the admin when account 10 joined.
Every signup path - email/password AND Google - must alert the admin exactly once,
a returning Google user must not, and the alert must find the admin even when
ADMIN_EMAIL is unset (it was, on production, until 2026-11-11)."""
import io
import json
import urllib.request

import pytest

from tests.test_routes import Client, stack  # noqa: F401


@pytest.fixture
def alerts(stack, monkeypatch):  # noqa: F811
    app = stack["app"]
    seen = []
    monkeypatch.setattr(app, "notify_admin_new_user",
                        lambda new_user_email, new_user_name, provider="": seen.append((new_user_email, provider)))
    import ratelimit
    ratelimit.reset_all()
    return seen


def _fake_google(monkeypatch, app, email, sub):
    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if url == app.GOOGLE_TOKEN_URI:
            body = {"access_token": "t"}
        elif url == app.GOOGLE_USERINFO_URI:
            body = {"sub": sub, "email": email, "email_verified": True, "name": "Ran", "picture": ""}
        else:
            raise AssertionError("unexpected fetch " + url)
        r = io.BytesIO(json.dumps(body).encode())
        r.__enter__ = lambda *a: r
        r.__exit__ = lambda *a: False
        return r
    monkeypatch.setattr(app.urllib.request, "urlopen", fake_urlopen)


def _google_callback(stack, email, sub):  # noqa: F811
    c = Client(stack["port"])
    c.cookie = "g_state=s1; g_verifier=v1"
    return c.get("/auth/google/callback?code=c1&state=s1")


def test_password_signup_alerts_admin(stack, alerts):  # noqa: F811
    c = Client(stack["port"])
    status, _loc, _ = c.post_form("/register", {
        "name": "Pw", "email": "pw-signup@example.test",
        "password": "correct-horse-1", "password2": "correct-horse-1"})
    assert status == 302
    assert alerts == [("pw-signup@example.test", "password")]


def test_google_signup_alerts_admin_once(stack, alerts, monkeypatch):  # noqa: F811
    _fake_google(monkeypatch, stack["app"], "ran-like@example.test", "sub-ran")
    status, loc, _ = _google_callback(stack, "ran-like@example.test", "sub-ran")
    assert status == 302, loc
    assert alerts == [("ran-like@example.test", "google")]
    # the same person signing in again is not a new user
    status, _loc, _ = _google_callback(stack, "ran-like@example.test", "sub-ran")
    assert status == 302
    assert alerts == [("ran-like@example.test", "google")]


def test_admin_is_found_without_admin_email(stack, monkeypatch):  # noqa: F811
    app = stack["app"]
    conn = stack["db"].get_db()
    try:
        has_admin = conn.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0]
        if not has_admin:
            conn.execute("INSERT INTO users (name, email, password_hash, salt, created_date, role) "
                         "VALUES ('adm','fallback-admin@example.test','h','s','2026-11-11','admin')")
            conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(app, "ADMIN_EMAIL", "")
    admin_id, how = app.admin_alert_target()
    assert admin_id is not None and how.startswith("admin role")
    sent = []
    monkeypatch.setattr(app, "deliver_notification", lambda uid, msg, **kw: sent.append((uid, msg)) or {})
    app._notify_admin_new_user_impl(new_user_email="x@example.test", new_user_name="X", provider="google")
    assert sent and sent[0][0] == admin_id and "with Google" in sent[0][1]


def test_health_reports_admin_alert_target(stack):  # noqa: F811
    status, _loc, body = Client(stack["port"]).get("/api/health")
    assert status == 200
    aa = json.loads(body)["admin_alerts"]
    assert set(aa) == {"target", "via"} and "@" not in str(aa)


def test_every_account_insert_fires_the_new_user_hook():
    """No code path may create an account without the alert. Every INSERT INTO
    users outside tests and migration tooling must be in auth.py, and each
    function doing one must call _fire_user_created."""
    import ast
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    offenders = []
    for path in root.rglob("*.py"):
        rel = path.relative_to(root).as_posix()
        if rel.startswith(("tests/", "scripts/", "web/", "node_modules/")) or rel == "migrations.py":
            continue
        src = path.read_text(encoding="utf-8", errors="ignore")
        if "INSERT INTO users" not in src:
            continue
        if rel != "auth.py":
            offenders.append(rel)
            continue
        for fn in ast.walk(ast.parse(src)):
            if isinstance(fn, ast.FunctionDef):
                body = ast.get_source_segment(src, fn) or ""
                if "INSERT INTO users" in body and "_fire_user_created(" not in body:
                    offenders.append("auth.py:" + fn.name)
    assert offenders == []
