"""The resettable test account (testaccount.py): admin-only, really brand-new
after every reset, and never mailed."""
import re

import pytest

from tests.test_routes import Client, stack, users  # noqa: F401


def _reset(c):
    st, _loc, body = c.post_form("/admin/test-account/reset", {})
    return st, (body.decode() if isinstance(body, bytes) else body)


def _creds(body):
    codes = re.findall(r"<code>([^<]+)</code>", body)
    return codes[0], codes[1]


def test_only_an_admin_can_see_or_reset_it(stack, users):
    assert users["b"].get("/admin/test-account")[0] == 403
    assert _reset(users["b"])[0] == 403
    anon = Client(stack["port"])
    assert anon.get("/admin/test-account")[0] == 302
    assert anon.post_form("/admin/test-account/reset", {})[0] == 302
    db = stack["db"].get_db()
    try:
        assert db.execute("SELECT COUNT(*) AS n FROM users WHERE email LIKE '%.test' "
                          "AND email LIKE 'test-user@%'").fetchone()["n"] == 0
    finally:
        db.close()


def test_reset_creates_a_brand_new_confirmed_account_that_can_sign_in(stack, users):
    st, body = _reset(users["admin"])
    assert st == 200
    email, password = _creds(body)
    assert email == "test-user@job-hunter.test"
    c = Client(stack["port"])
    assert c.post_form("/login", {"email": email, "password": password})[0] == 302
    st, _l, me = c.get("/api/me")
    import json
    assert st == 200 and not json.loads(me).get("onboarding_complete")
    db = stack["db"].get_db()
    try:
        r = db.execute("SELECT id, role, email_verified_at FROM users WHERE email=?", (email,)).fetchone()
        assert r["role"] == "user" and r["email_verified_at"]
        # created here, not through /register: it is not in the signup funnel
        assert db.execute("SELECT COUNT(*) AS n FROM funnel_events WHERE user_id=? AND step='signup'",
                          (r["id"],)).fetchone()["n"] == 0
    finally:
        db.close()


def test_a_second_reset_wipes_what_the_first_run_left(stack, users):
    _st, body = _reset(users["admin"])
    email, pw1 = _creds(body)
    c = Client(stack["port"])
    c.post_form("/login", {"email": email, "password": pw1})
    c.post_json("/api/save-profile", {"job_titles": ["Product Manager"]})
    c.post_json("/api/save-schedule", {"onboarding_complete": 1})
    db = stack["db"].get_db()
    uid1 = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()["id"]
    assert db.execute("SELECT COUNT(*) AS n FROM job_runs WHERE user_id=?", (uid1,)).fetchone()["n"] == 1
    db.close()

    _st, body = _reset(users["admin"])
    _email, pw2 = _creds(body)
    assert pw2 != pw1
    db = stack["db"].get_db()
    try:
        rows = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchall()
        assert len(rows) == 1 and rows[0]["id"] != uid1
        assert db.execute("SELECT COUNT(*) AS n FROM job_runs WHERE user_id=?", (uid1,)).fetchone()["n"] == 0
        p = db.execute("SELECT onboarding_complete, job_titles FROM user_profiles WHERE user_id=?",
                       (rows[0]["id"],)).fetchone()
        assert not p["onboarding_complete"] and not (p["job_titles"] or "").strip("[] ")
    finally:
        db.close()
    assert c.get("/api/me")[0] in (302, 401)            # the old session died with the account
    # and the new account starts its first search again when setup ends
    c2 = Client(stack["port"])
    c2.post_form("/login", {"email": email, "password": pw2})
    c2.post_json("/api/save-profile", {"job_titles": ["QA Engineer"]})
    st, _l, body = c2.post_json("/api/save-schedule", {"onboarding_complete": 1})
    import json
    assert json.loads(body)["first_search_started"] is True


def test_mail_to_a_test_address_is_refused(stack, monkeypatch):
    app = stack["app"]
    monkeypatch.setattr(app, "RESEND_API_KEY", "re_test")
    with pytest.raises(RuntimeError, match="test address"):
        app.send_email("test-user@job-hunter.test", "x", "y")
