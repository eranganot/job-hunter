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
    # The state production holds for one user: WhatsApp on, nothing to send with.
    conn.execute("UPDATE user_profiles SET notification_channel='whatsapp,email', email_address='x@e.com', "
                 "twilio_account_sid='', twilio_auth_token='', whatsapp_number='' WHERE user_id=?", (uid,))
    conn.commit()
    conn.close()
    return app, uid


def test_a_stored_channel_that_is_not_set_up_is_a_warning_and_the_rest_still_send(env, monkeypatch, caplog):
    app, uid = env
    monkeypatch.setattr(app, "send_web_push_to_user", lambda *a, **k: _PUSHED)
    sent_wa = []
    monkeypatch.setattr(app, "send_whatsapp", lambda *a: sent_wa.append(a))
    monkeypatch.setattr(app, "send_email", lambda **k: None)
    caplog.set_level(logging.DEBUG)

    res = app.deliver_notification(uid, "hello")

    assert res["whatsapp"].startswith("not set up:") and "Twilio" in res["whatsapp"]
    assert res["email"] == "sent" and res["push"].startswith("sent")
    assert sent_wa == []                                  # never called with empty credentials
    notify = [r for r in caplog.records if "[notify]" in r.getMessage() and "whatsapp" in r.getMessage()]
    assert notify and all(r.levelno == logging.WARNING for r in notify), \
        [(r.levelname, r.getMessage()) for r in notify]
    conn = database.get_db()
    logged = [r["details"] for r in conn.execute(
        "SELECT details FROM activity_log WHERE user_id=? AND event_type='notification'", (uid,))]
    conn.close()
    assert any("WHATSAPP" in d and "NOT SET UP" in d for d in logged)


def test_a_channel_that_is_set_up_but_refused_is_still_an_error(env, monkeypatch, caplog):
    """Paired with the test above: a real delivery failure must stay loud."""
    app, uid = env
    conn = database.get_db()
    conn.execute("UPDATE user_profiles SET twilio_account_sid='AC1', twilio_auth_token='tok', "
                 "whatsapp_number='+972500000000' WHERE user_id=?", (uid,))
    conn.commit()
    conn.close()
    monkeypatch.setattr(app, "send_web_push_to_user", lambda *a, **k: _PUSHED)
    monkeypatch.setattr(app, "send_email", lambda **k: None)

    def refused(*a):
        raise RuntimeError("Twilio refused the message: 21211 invalid number")
    monkeypatch.setattr(app, "send_whatsapp", refused)
    caplog.set_level(logging.DEBUG)

    res = app.deliver_notification(uid, "hello")

    assert res["whatsapp"].startswith("FAILED") and "21211" in res["whatsapp"]
    assert any(r.levelno == logging.ERROR and "[notify] Error on whatsapp" in r.getMessage()
               for r in caplog.records)


def test_an_email_skipped_by_the_caller_says_skipped_not_missing(env, monkeypatch):
    app, uid = env
    conn = database.get_db()
    conn.execute("UPDATE user_profiles SET notification_channel='email', email_address='' WHERE user_id=?", (uid,))
    conn.commit()
    conn.close()
    monkeypatch.setattr(app, "send_web_push_to_user", lambda *a, **k: _PUSHED)
    assert app.deliver_notification(uid, "hello", email=False)["email"].startswith("skipped")
