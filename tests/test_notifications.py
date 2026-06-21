"""deliver_notification must push on EVERY notification, even when no
email/Telegram/WhatsApp channel is configured (push is the primary channel)."""
import pytest
import db as database
import auth


@pytest.fixture
def env(tmp_path):
    import app  # import resets db path, so import before pointing at the temp DB
    database.set_db_path(str(tmp_path / "t.db"))
    database.init_db()
    auth.set_db_getter(database.get_db)
    auth.create_user("Eran", "t@e.com", "pw123456")
    conn = database.get_db()
    uid = conn.execute("SELECT id FROM users WHERE email=?", ("t@e.com",)).fetchone()["id"]
    conn.execute("UPDATE user_profiles SET notification_channel='none' WHERE user_id=?", (uid,))
    conn.commit()
    conn.close()
    return app, uid


def test_push_fires_with_no_channels(env, monkeypatch):
    app, uid = env
    calls = []
    monkeypatch.setattr(app, "send_web_push_to_user", lambda u, m, suf="": calls.append((u, m, suf)))
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
    monkeypatch.setattr(app, "send_web_push_to_user", lambda u, m, suf="": pushes.append(u))
    monkeypatch.setattr(app, "send_email", lambda **k: emails.append(k))
    app.deliver_notification(uid, "Applied to 2 jobs", "/dashboard#applied")
    assert pushes == [uid]      # push fired
    assert len(emails) == 1     # and email still fired as fallback
