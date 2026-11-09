"""Step 5 (2026-11-09): confirm your email, and reset a forgotten password.

Done when (EXECUTION_PLAN_NEXT.md): a new account receives and completes
verification; a reset link works once and then fails; both mails go to the
user's own address."""
import re

import pytest

from tests.test_routes import Client, stack  # noqa: F401


@pytest.fixture
def mails(stack, monkeypatch):
    sent = []
    monkeypatch.setattr(stack["app"], "send_email", lambda to, subject, body, **k: sent.append((to, subject, body)))
    import ratelimit
    ratelimit.reset_all()
    return sent


def _link(body, route):
    m = re.search(r"https?://[^\s]+%s\?token=([A-Za-z0-9_\-]+)" % route, body)
    assert m, body
    return m.group(1)


def _register(stack, email, pw="correct-horse-1"):
    c = Client(stack["port"])
    st, loc, _ = c.post_form("/register", {"name": "Dana Levi", "email": email, "password": pw, "password2": pw})
    assert st == 302, st
    return c


def _verified(stack, email):
    conn = stack["db"].get_db()
    try:
        return bool(conn.execute("SELECT email_verified_at FROM users WHERE email=?", (email,)).fetchone()[0])
    finally:
        conn.close()


def test_a_new_account_gets_a_confirmation_link_that_works_once(stack, mails):
    c = _register(stack, "verify-me@example.test")
    assert [m[0] for m in mails] == ["verify-me@example.test"], "the mail must go to the user's own address"
    assert not _verified(stack, "verify-me@example.test")
    import json
    me = json.loads(c.get("/api/me")[2])
    assert me["email_verified"] is False and me.get("auto_apply_live") is False
    tok = _link(mails[0][2], "/verify-email")
    st, loc, _ = c.get("/verify-email?token=" + tok)
    assert st == 302 and "verified=1" in loc
    assert _verified(stack, "verify-me@example.test")
    assert c.get("/verify-email?token=" + tok)[0] == 410        # single use
    assert c.get("/verify-email?token=nonsense")[0] == 410


def test_applications_wait_for_a_confirmed_email(stack, mails, monkeypatch):
    """Applications go out under the account's email."""
    monkeypatch.setenv("APPLY_ENGINE_ENABLED", "1")
    monkeypatch.setenv("APPLY_ENGINE_FOR_ALL", "1")
    _register(stack, "gate@example.test")
    conn = stack["db"].get_db()
    uid = conn.execute("SELECT id FROM users WHERE email='gate@example.test'").fetchone()[0]
    conn.close()
    app = stack["app"]
    assert app._engine_on_for(uid) is False
    app.auth.mark_email_verified(uid)
    assert app._engine_on_for(uid) is True


def test_a_reset_link_changes_the_password_once_and_signs_everyone_out(stack, mails):
    old = _register(stack, "forgot@example.test", "old-password-1")
    mails.clear()
    anon = Client(stack["port"])
    st, _l, page = anon.post_form("/forgot-password", {"email": "Forgot@Example.test"})
    assert st == 200 and b"If there" in page
    assert [m[0] for m in mails] == ["forgot@example.test"]
    tok = _link(mails[0][2], "/reset-password")
    assert anon.get("/reset-password?token=" + tok)[0] == 200
    st, _l, page = anon.post_form("/reset-password", {"token": tok, "password": "new-password-1", "password2": "nope-nope-1"})
    assert b"don&#x27;t match" in page or b"don't match" in page
    st, loc, _ = anon.post_form("/reset-password", {"token": tok, "password": "new-password-1", "password2": "new-password-1"})
    assert st == 302 and loc == "/login?notice=reset"
    assert old.get("/api/me")[0] in (401, 302), "the session that had the old password must end"
    assert anon.post_form("/reset-password", {"token": tok, "password": "x-password-2", "password2": "x-password-2"})[0] == 410
    fresh = Client(stack["port"])
    st, loc, _ = fresh.post_form("/login", {"email": "forgot@example.test", "password": "new-password-1"})
    assert st == 302 and loc != "/login"
    assert _verified(stack, "forgot@example.test"), "the link reached the inbox: the address is proven"


def test_forgot_password_says_the_same_for_unknown_addresses_and_sends_nothing(stack, mails):
    st, _l, page = Client(stack["port"]).post_form("/forgot-password", {"email": "nobody@example.test"})
    assert st == 200 and b"If there" in page and mails == []


def test_an_expired_reset_link_fails(stack, mails):
    _register(stack, "expired@example.test")
    mails.clear()
    Client(stack["port"]).post_form("/forgot-password", {"email": "expired@example.test"})
    tok = _link(mails[0][2], "/reset-password")
    conn = stack["db"].get_db()
    conn.execute("UPDATE auth_tokens SET expires_at='2000-01-01 00:00:00' WHERE purpose='reset'")
    conn.commit()
    conn.close()
    assert Client(stack["port"]).get("/reset-password?token=" + tok)[0] == 410


def test_forgot_password_is_rate_limited(stack, mails):
    c = Client(stack["port"])
    codes = [c.post_form("/forgot-password", {"email": "x%d@example.test" % i})[0] for i in range(6)]
    assert codes[:5] == [200] * 5 and codes[5] == 429


def test_google_sign_ins_are_already_confirmed(stack):
    user, is_new, err = stack["app"].auth.find_or_create_google_user("sub-123", "g@example.test", "G User")
    assert is_new and not err
    assert _verified(stack, "g@example.test")
