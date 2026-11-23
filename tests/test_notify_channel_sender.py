"""
The sender's half of JOB-HUNTER-M (2026-11-22): a stored channel that is not set
up is reported as "not set up" at WARNING, and the other channels still send.

Kept apart from test_notify_channel_setup.py on purpose: that file borrows the
`stack` fixture, which stubs app.deliver_notification for its whole module, so
these calls would have reached the stub (see test_notifications.py).
"""
import logging

import pytest

import db as database
import auth

_PUSHED = {"configured": True, "devices": 1, "sent": 1, "failed": 0, "errors": []}


# ── the sender ───────────────────────────────────────────────────────────────

@pytest.fixture
def env(tmp_path):
    import app
    database.set_db_path(str(tmp_path / "t.db"))
    database.init_db()
    auth.set_db_getter(database.get_db)
    auth.create_user("Alex", "t@e.com", "pw123456")
    conn = database.get_db()
    uid = conn.execute("SELECT id FROM users WHERE email=?", ("t@e.com",)).fetchone()["id"]
    # A channel selected with nothing to send with (JOB-HUNTER-M was WhatsApp;
    # WhatsApp is now hidden, so Telegram stands in for the same shape).
    conn.execute("UPDATE user_profiles SET notification_channel='telegram,email', email_address='x@e.com', "
                 "telegram_token='', telegram_chat_id='' WHERE user_id=?", (uid,))
    conn.commit()
    conn.close()
    return app, uid


def test_a_stored_channel_that_is_not_set_up_is_a_warning_and_the_rest_still_send(env, monkeypatch, caplog):
    app, uid = env
    monkeypatch.setattr(app, "send_web_push_to_user", lambda *a, **k: _PUSHED)
    sent_tg = []
    monkeypatch.setattr(app, "send_telegram", lambda *a: sent_tg.append(a))
    monkeypatch.setattr(app, "send_email", lambda **k: None)
    caplog.set_level(logging.DEBUG)

    res = app.deliver_notification(uid, "hello")

    assert res["telegram"].startswith("not set up:") and "not connected" in res["telegram"]
    assert res["email"] == "sent" and res["push"].startswith("sent")
    assert sent_tg == []                                  # never called with empty credentials
    notify = [r for r in caplog.records if "[notify]" in r.getMessage() and "telegram" in r.getMessage()]
    assert notify and all(r.levelno == logging.WARNING for r in notify), \
        [(r.levelname, r.getMessage()) for r in notify]
    conn = database.get_db()
    logged = [r["details"] for r in conn.execute(
        "SELECT details FROM activity_log WHERE user_id=? AND event_type='notification'", (uid,))]
    conn.close()
    assert any("TELEGRAM" in d and "NOT SET UP" in d for d in logged)


def test_a_channel_that_is_set_up_but_refused_is_still_an_error(env, monkeypatch, caplog):
    """Paired with the test above: a real delivery failure must stay loud."""
    app, uid = env
    conn = database.get_db()
    conn.execute("UPDATE user_profiles SET telegram_token='t', telegram_chat_id='c' WHERE user_id=?", (uid,))
    conn.commit()
    conn.close()
    monkeypatch.setattr(app, "send_web_push_to_user", lambda *a, **k: _PUSHED)
    monkeypatch.setattr(app, "send_email", lambda **k: None)

    def refused(*a):
        raise RuntimeError("Telegram refused the message: chat not found")
    monkeypatch.setattr(app, "send_telegram", refused)
    caplog.set_level(logging.DEBUG)

    res = app.deliver_notification(uid, "hello")

    assert res["telegram"].startswith("FAILED") and "chat not found" in res["telegram"]
    assert any(r.levelno == logging.ERROR and "[notify] Error on telegram" in r.getMessage()
               for r in caplog.records)


def test_an_email_skipped_by_the_caller_says_skipped_not_missing(env, monkeypatch):
    app, uid = env
    conn = database.get_db()
    conn.execute("UPDATE user_profiles SET notification_channel='email', email_address='' WHERE user_id=?", (uid,))
    conn.commit()
    conn.close()
    monkeypatch.setattr(app, "send_web_push_to_user", lambda *a, **k: _PUSHED)
    assert app.deliver_notification(uid, "hello", email=False)["email"].startswith("skipped")


def test_a_profile_that_already_holds_whatsapp_is_skipped_quietly(env, monkeypatch, caplog):
    """WhatsApp is hidden (owner, 2026-11-23): nothing is sent, nothing is logged
    as a problem, and the profile's other channels still send."""
    app, uid = env
    conn = database.get_db()
    conn.execute("UPDATE user_profiles SET notification_channel='whatsapp,email', twilio_account_sid='AC1', "
                 "twilio_auth_token='tok', whatsapp_number='+972500000000' WHERE user_id=?", (uid,))
    conn.commit()
    conn.close()
    monkeypatch.setattr(app, "send_web_push_to_user", lambda *a, **k: _PUSHED)
    sent_wa = []
    monkeypatch.setattr(app, "send_whatsapp", lambda *a: sent_wa.append(a))
    monkeypatch.setattr(app, "send_email", lambda **k: None)
    caplog.set_level(logging.DEBUG)

    res = app.deliver_notification(uid, "hello")

    assert res["whatsapp"] == "unavailable" and res["email"] == "sent"
    assert sent_wa == []
    assert not [r for r in caplog.records if "whatsapp" in r.getMessage().lower() and r.levelno >= logging.WARNING]


@pytest.mark.parametrize("own, expected", [("", "123:central-token"), ("999:own-bot", "999:own-bot")])
def test_telegram_goes_through_our_bot_unless_the_profile_has_its_own(env, monkeypatch, own, expected):
    """Job Hunter's bot (telegrambot.py, 2026-11-23); a legacy own-bot token keeps working."""
    app, uid = env
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:central-token")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "s")
    conn = database.get_db()
    conn.execute("UPDATE user_profiles SET notification_channel='telegram', telegram_chat_id='555', "
                 "telegram_token=? WHERE user_id=?", (own, uid))
    conn.commit()
    conn.close()
    monkeypatch.setattr(app, "send_web_push_to_user", lambda *a, **k: _PUSHED)
    calls = []
    monkeypatch.setattr(app, "send_telegram", lambda tok, chat, msg: calls.append((tok, chat)))
    assert app.deliver_notification(uid, "hello")["telegram"] == "sent"
    assert calls == [(expected, "555")]


def test_a_chat_id_without_any_bot_is_not_set_up(env, monkeypatch):
    app, uid = env
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    conn = database.get_db()
    conn.execute("UPDATE user_profiles SET notification_channel='telegram', telegram_chat_id='555', "
                 "telegram_token='' WHERE user_id=?", (uid,))
    conn.commit()
    conn.close()
    monkeypatch.setattr(app, "send_web_push_to_user", lambda *a, **k: _PUSHED)
    assert app.deliver_notification(uid, "hello")["telegram"].startswith("not set up:")
