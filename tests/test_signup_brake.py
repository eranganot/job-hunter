"""C5 (2026-11-24): the sign-up brake. JH_SIGNUPS=paused or a reached
JH_SIGNUP_DAILY_CAP refuses NEW accounts with a polite message and puts the
person on the waitlist; existing users and the admin always get in."""
import uuid

from tests.test_routes import stack  # noqa: F401


def _mail():
    return "brake-%s@example.com" % uuid.uuid4().hex[:8]


def _waitlisted(stack, email):
    conn = stack["db"].get_db()
    try:
        return conn.execute("SELECT reason FROM signup_waitlist WHERE email=?", (email,)).fetchone()
    finally:
        conn.close()


def test_open_by_default(stack, monkeypatch):
    import auth
    monkeypatch.delenv("JH_SIGNUPS", raising=False)
    monkeypatch.delenv("JH_SIGNUP_DAILY_CAP", raising=False)
    uid, err = auth.create_user("A", _mail(), "password123")
    assert uid and err is None


def test_paused_refuses_and_waitlists(stack, monkeypatch):
    import auth
    existing = _mail()
    auth.create_user("Old", existing, "password123")
    monkeypatch.setenv("JH_SIGNUPS", "paused")
    m = _mail()
    uid, err = auth.create_user("New", m, "password123")
    assert uid is None and err == auth.SIGNUP_WAITLIST_MESSAGE
    assert _waitlisted(stack, m)[0] == "paused"
    # an existing address still gets the normal answer, not the waitlist
    assert auth.create_user("Old", existing, "password123")[1] == "An account with that email already exists."
    # an existing user still signs in
    assert auth.authenticate(existing, "password123")[0]


def test_admin_always_gets_in(stack, monkeypatch):
    import auth, os
    monkeypatch.setenv("JH_SIGNUPS", "paused")
    conn = stack["db"].get_db()
    try:
        assert auth._signup_blocked(conn, os.environ["ADMIN_EMAIL"].lower()) is None
    finally:
        conn.close()


def test_daily_cap(stack, monkeypatch):
    import auth
    monkeypatch.setenv("JH_SIGNUPS", "open")
    conn = stack["db"].get_db()
    try:
        today = auth.signups_today(conn)
    finally:
        conn.close()
    monkeypatch.setenv("JH_SIGNUP_DAILY_CAP", str(today + 1))
    assert auth.create_user("One", _mail(), "password123")[0]
    m = _mail()
    uid, err = auth.create_user("Two", m, "password123")
    assert uid is None and err == auth.SIGNUP_WAITLIST_MESSAGE
    assert _waitlisted(stack, m)[0] == "cap"
    monkeypatch.setenv("JH_SIGNUP_DAILY_CAP", "junk")      # bad value = no cap, never a crash
    assert auth.create_user("Three", _mail(), "password123")[0]


def test_google_new_refused_linked_still_signs_in(stack, monkeypatch):
    import auth
    monkeypatch.delenv("JH_SIGNUPS", raising=False)
    linked = _mail()
    u, new, err = auth.find_or_create_google_user("sub-" + linked, linked, "L")
    assert u and new
    monkeypatch.setenv("JH_SIGNUPS", "paused")
    u2, new2, err2 = auth.find_or_create_google_user("sub-" + linked, linked, "L")
    assert u2 and not new2 and err2 is None
    m = _mail()
    u3, new3, err3 = auth.find_or_create_google_user("sub-" + m, m, "N")
    assert u3 is None and not new3 and err3 == auth.SIGNUP_WAITLIST_MESSAGE
    assert _waitlisted(stack, m)[0] == "paused"


def test_health_block(stack, monkeypatch):
    monkeypatch.setenv("JH_SIGNUPS", "paused")
    monkeypatch.setenv("JH_SIGNUP_DAILY_CAP", "40")
    h = stack["app"]._signups_health()
    assert h["mode"] == "paused" and h["daily_cap"] == 40
    assert isinstance(h["today"], int) and isinstance(h["waitlist"], int)
