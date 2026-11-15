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


def test_noas_pushes_carry_her_name_and_picture_and_account_pushes_the_app_icon(env, monkeypatch):
    """GTM row 6 (2026-11-15): a push from Noa shows Noa, an account or admin push shows Job Hunter.
    The server says who it is from; the service worker picks the picture."""
    import inspect, os, struct
    app, uid = env
    src = inspect.getsource(app.send_web_push_to_user)
    titles = []
    monkeypatch.setattr(app, "send_web_push_to_user",
                        lambda u, m, suf="", url=None, title="Job Hunter": titles.append(title) or _PUSHED)
    app.deliver_notification(uid, "Today's 7 are in.")                       # job news
    app.deliver_notification(uid, "New User Alert", from_noa=False)          # admin / account news
    assert titles == ["Noa", "Job Hunter"]
    assert '"from": "noa" if title == "Noa" else "app"' in src
    # admin alerts and the channel check are not Noa's
    for fn in (app._notify_admin_new_user_impl,):
        assert "from_noa=False" in inspect.getsource(fn)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for where in ("web/public", "web_bundle"):
        sw = open(os.path.join(root, where, "sw.js"), encoding="utf-8").read()
        assert 'icon: data.from === "noa" ? "/app/noa-192.png" : "/app/icon-192.png"' in sw, where
        assert 'badge: "/app/badge-96.png"' in sw and 'badge: "/app/icon-192.png"' not in sw, where
        for name, size in (("noa-192.png", (192, 192)), ("badge-96.png", (96, 96))):
            with open(os.path.join(root, where, name), "rb") as f:
                head = f.read(26)
            assert head[:8] == b"\x89PNG\r\n\x1a\n" and struct.unpack(">II", head[16:24]) == size, (where, name)
            assert head[25] == 6, (where, name)          # colour type 6 = RGBA: the badge needs transparency


def test_a_search_that_finds_nothing_sends_nothing():
    """The early exit of run_job_search still pushed "No new jobs found this run" after #130."""
    import inspect
    import app
    src = inspect.getsource(app.run_job_search)
    assert "No new jobs found this run" not in src.replace('still pushed "No new jobs found this run"', "")


def test_the_push_test_comes_from_noa():
    """The owner pressed the test after row 6 and saw the app icon: correct for an account push, but
    the test is how a user checks what THEIR pushes will look like, and those are Noa's."""
    import inspect
    import app
    src = inspect.getsource(app)
    at = src.index('if path == "/api/push/test":')
    block = src[at:at + 700]
    assert 'title="Noa"' in block and "It's Noa. Push is working" in block
