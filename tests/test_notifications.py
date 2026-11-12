"""deliver_notification must push on EVERY notification, even when no
email/Telegram/WhatsApp channel is configured (push is the primary channel)."""
import pytest
import db as database
import auth

_PUSHED = {"configured": True, "devices": 1, "sent": 1, "failed": 0, "errors": []}


@pytest.fixture
def env(tmp_path):
    import app  # import resets db path, so import before pointing at the temp DB
    database.set_db_path(str(tmp_path / "t.db"))
    database.init_db()
    auth.set_db_getter(database.get_db)
    auth.create_user("Alex", "t@e.com", "pw123456")
    conn = database.get_db()
    uid = conn.execute("SELECT id FROM users WHERE email=?", ("t@e.com",)).fetchone()["id"]
    conn.execute("UPDATE user_profiles SET notification_channel='none' WHERE user_id=?", (uid,))
    conn.commit()
    conn.close()
    return app, uid


def test_push_fires_with_no_channels(env, monkeypatch):
    app, uid = env
    calls = []
    monkeypatch.setattr(app, "send_web_push_to_user", lambda u, m, suf="", url=None, title="Job Hunter": calls.append((u, m, suf)) or _PUSHED)
    app.deliver_notification(uid, "Search Complete - 3 new jobs", "/dashboard#new")
    assert calls, "push must fire even with no channels configured"
    assert calls[0][0] == uid and "Search Complete" in calls[0][1] and calls[0][2] == "/dashboard#new"


def test_push_fires_alongside_email(env, monkeypatch):
    app, uid = env
    conn = database.get_db()
    conn.execute(
        "UPDATE user_profiles SET notification_channel='email', email_address='x@e.com' WHERE user_id=?",
        (uid,),
    )
    conn.commit()
    conn.close()
    pushes, emails = [], []
    monkeypatch.setattr(app, "send_web_push_to_user", lambda u, m, suf="", url=None, title="Job Hunter": pushes.append(u) or _PUSHED)
    monkeypatch.setattr(app, "send_email", lambda **k: emails.append(k))
    app.deliver_notification(uid, "Applied to 2 jobs", "/dashboard#applied")
    assert pushes == [uid]      # push fired
    assert len(emails) == 1     # and email still fired as fallback


def test_every_selected_channel_fires_not_just_the_first(env, monkeypatch):
    """The settings UI stores several channels as one comma-joined string
    because that is what the sender already reads. If deliver_notification ever
    stopped splitting, the UI would keep offering three checkboxes while only
    the first one delivered - and nothing would say so."""
    app, uid = env
    conn = database.get_db()
    conn.execute(
        "UPDATE user_profiles SET notification_channel='telegram,email', "
        "email_address='x@e.com', telegram_token='tok', telegram_chat_id='42' "
        "WHERE user_id=?", (uid,),
    )
    conn.commit()
    conn.close()
    pushes, emails, tgs = [], [], []
    monkeypatch.setattr(app, "send_web_push_to_user", lambda u, m, suf="", url=None, title="Job Hunter": pushes.append(u) or _PUSHED)
    monkeypatch.setattr(app, "send_email", lambda **k: emails.append(k))
    monkeypatch.setattr(app, "send_telegram", lambda t, c, m: tgs.append((t, c)))
    app.deliver_notification(uid, "3 new jobs", "/app")
    assert pushes == [uid]
    assert len(emails) == 1, "email leg did not fire"
    assert tgs == [("tok", "42")], "telegram leg did not fire"


def test_whitespace_around_a_channel_does_not_silently_drop_it(env, monkeypatch):
    """A human editing the column by hand writes 'telegram, email'."""
    app, uid = env
    conn = database.get_db()
    conn.execute(
        "UPDATE user_profiles SET notification_channel=' telegram , email ', "
        "email_address='x@e.com', telegram_token='tok', telegram_chat_id='42' "
        "WHERE user_id=?", (uid,),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(app, "send_web_push_to_user", lambda u, m, suf="", url=None, title="Job Hunter": _PUSHED)
    emails, tgs = [], []
    monkeypatch.setattr(app, "send_email", lambda **k: emails.append(k))
    monkeypatch.setattr(app, "send_telegram", lambda t, c, m: tgs.append(t))
    app.deliver_notification(uid, "3 new jobs", "/app")
    assert len(emails) == 1 and tgs == ["tok"]


def test_the_real_sender_is_installed_and_not_someone_elses_stub():
    """An order-independent guard on a fault that was invisible in every
    per-file run.

    tests/test_routes.py's `stack` fixture stubs app.deliver_notification to
    keep registration offline. It assigned the stub to the MODULE, and Python
    caches modules, so the stub outlived the fixture and every later test in
    the process saw nothing delivered. The four tests above then asserted
    against a function that had been replaced - they failed while the code they
    test was perfectly fine, which is the worst way for a suite to be wrong.

    Checked by identity rather than behaviour: a stub that returns None looks
    exactly like a delivery with no configured channel.
    """
    import app
    assert app.deliver_notification.__module__ == "app", (
        "app.deliver_notification has been replaced by another test's stub and "
        "not restored - every notification assertion after this point is void")
    assert app.deliver_notification.__name__ == "deliver_notification"
    assert getattr(app.notify_admin_new_user, "__name__", "") == "notify_admin_new_user"


def test_each_channel_reports_what_really_happened(env, monkeypatch):
    """2026-11-08: Telegram and WhatsApp swallowed their errors and the log said
    "Sent OK"; push returned nothing. Now every outcome comes back and is logged."""
    app, uid = env
    conn = database.get_db()
    conn.execute("UPDATE user_profiles SET notification_channel='telegram,email', "
                 "email_address='x@e.com', telegram_token='t', telegram_chat_id='c' WHERE user_id=?", (uid,))
    conn.commit()
    conn.close()
    monkeypatch.setattr(app, "send_web_push_to_user", lambda u, m, suf="", url=None, title="Job Hunter": {
        "configured": True, "devices": 1, "sent": 0, "failed": 1, "errors": ["410 Gone"]})

    def refused(*a):
        raise RuntimeError("Telegram refused the message: chat not found")
    monkeypatch.setattr(app, "send_telegram", refused)
    monkeypatch.setattr(app, "send_email", lambda **k: None)
    res = app.deliver_notification(uid, "hello")
    assert res["push"].startswith("FAILED") and "410" in res["push"]
    assert res["telegram"].startswith("FAILED") and "chat not found" in res["telegram"]
    assert res["email"] == "sent"
    conn = database.get_db()
    logged = [r["details"] for r in conn.execute(
        "SELECT details FROM activity_log WHERE user_id=? AND event_type='notification'", (uid,)).fetchall()]
    conn.close()
    assert any("TELEGRAM" in d and "FAILED" in d for d in logged)
    assert any("PUSH" in d and "FAILED" in d for d in logged)
    assert not any("TELEGRAM" in d and "Sent OK" in d for d in logged)


def test_telegram_raises_when_telegram_says_no(env, monkeypatch):
    app, _uid = env
    import io, json as _j

    class R(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(app.urllib.request, "urlopen", lambda *a, **k: R(_j.dumps({"ok": False, "description": "Unauthorized"}).encode()))
    with pytest.raises(RuntimeError, match="refused"):
        app.send_telegram("bad", "c", "hi")
    with pytest.raises(RuntimeError, match="missing"):
        app.send_telegram("", "", "hi")


def test_push_reports_missing_keys_and_no_devices(env, monkeypatch):
    app, uid = env
    monkeypatch.setattr(app, "VAPID_PUBLIC_KEY", "")
    r = app.send_web_push_to_user(uid, "x")
    assert r["configured"] is False and "VAPID" in r["errors"][0]
