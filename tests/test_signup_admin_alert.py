"""Ticket 2026-11-11: no new-user alert reached the admin when account 10 joined.
Every signup path - email/password AND Google - must alert the admin exactly once,
and a returning Google user must not."""
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
                        lambda new_user_email, new_user_name: seen.append(new_user_email))
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
    assert alerts == ["pw-signup@example.test"]


def test_google_signup_alerts_admin_once(stack, alerts, monkeypatch):  # noqa: F811
    _fake_google(monkeypatch, stack["app"], "ran-like@example.test", "sub-ran")
    status, loc, _ = _google_callback(stack, "ran-like@example.test", "sub-ran")
    assert status == 302, loc
    assert alerts == ["ran-like@example.test"]
    # the same person signing in again is not a new user
    status, _loc, _ = _google_callback(stack, "ran-like@example.test", "sub-ran")
    assert status == 302
    assert alerts == ["ran-like@example.test"]
