"""
Job Hunter's own Telegram bot (telegrambot.py, 2026-11-23), driven through the
real Handler: a one-time connect link, Telegram's webhook (secret header), and
a notification-only chat (only /start <code> and /stop act).
"""
import http.client
import json
from datetime import datetime, timedelta

import pytest

import telegrambot
from tests.test_routes import stack, users, _user_id  # noqa: F401

SECRET = "test-webhook-secret-abc123"


@pytest.fixture
def bot(monkeypatch):
    """The bot configured, Telegram replaced by a recorder."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:central-token")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "jobhunter_bot")
    sent = []
    monkeypatch.setattr(telegrambot, "send_message", lambda chat_id, text: sent.append((chat_id, text)))
    telegrambot._last_hint.clear()
    return sent


def _hook(port, update, secret=SECRET):
    body = json.dumps(update).encode()
    hdrs = {"Content-Type": "application/json", "Content-Length": str(len(body))}
    if secret is not None:
        hdrs["X-Telegram-Bot-Api-Secret-Token"] = secret
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        c.request("POST", "/telegram/webhook", body=body, headers=hdrs)
        r = c.getresponse()
        return r.status, r.read()
    finally:
        c.close()


def _msg(chat_id, text, chat_type="private"):
    return {"update_id": 1, "message": {"message_id": 1, "text": text,
                                        "chat": {"id": chat_id, "type": chat_type}}}


def _profile(db, email):
    conn = db.get_db()
    try:
        r = conn.execute("SELECT p.telegram_chat_id, p.telegram_token, p.notification_channel FROM user_profiles p "
                         "JOIN users u ON u.id=p.user_id WHERE u.email=?", (email,)).fetchone()
        return (r[0] or "", r[1] or "", r[2] or "")
    finally:
        conn.close()


def _set(db, email, **cols):
    conn = db.get_db()
    try:
        uid = _user_id(db, email)
        conn.execute("UPDATE user_profiles SET %s WHERE user_id=?" % ", ".join("%s=?" % k for k in cols),
                     list(cols.values()) + [uid])
        conn.commit()
    finally:
        conn.close()


def _connect_code(user):
    status, _loc, body = user.post_json("/api/telegram/connect", {})
    assert status == 200, body
    url = json.loads(body)["url"]
    assert url.startswith("https://t.me/jobhunter_bot?start=")
    code = url.split("start=", 1)[1]
    assert 20 <= len(code) <= 64 and all(c.isalnum() or c in "_-" for c in code)
    return code


def test_start_with_the_code_connects_the_account_and_noa_says_so(stack, users, bot):
    _set(stack["db"], "alice@example.test", telegram_chat_id="", telegram_token="", notification_channel="email")
    code = _connect_code(users["a"])
    status, _ = _hook(stack["port"], _msg(555, "/start " + code))
    assert status == 200
    chat, token, chans = _profile(stack["db"], "alice@example.test")
    assert chat == "555" and token == "" and chans.split(",") == ["email", "telegram"]
    assert bot == [(555, telegrambot.CONNECTED_TEXT)]
    st = json.loads(users["a"].get("/api/telegram/status")[2])
    assert st == {"connected": True, "own_bot": False, "available": True}


def test_a_used_code_does_not_work_twice(stack, users, bot):
    code = _connect_code(users["a"])
    _hook(stack["port"], _msg(555, "/start " + code))
    _set(stack["db"], "bob@example.test", telegram_chat_id="", telegram_token="")
    _hook(stack["port"], _msg(777, "/start " + code))
    assert bot[-1] == (777, telegrambot.EXPIRED_TEXT)
    assert _profile(stack["db"], "bob@example.test")[0] == ""


def test_an_expired_code_is_refused(stack, users, bot):
    code = _connect_code(users["a"])
    conn = stack["db"].get_db()
    try:
        conn.execute("UPDATE telegram_connect_codes SET expires_at=?",
                     ((datetime.utcnow() - timedelta(minutes=1)).isoformat(" "),))
        conn.commit()
    finally:
        conn.close()
    _set(stack["db"], "alice@example.test", telegram_chat_id="", telegram_token="")
    _hook(stack["port"], _msg(555, "/start " + code))
    assert bot[-1] == (555, telegrambot.EXPIRED_TEXT)
    assert _profile(stack["db"], "alice@example.test")[0] == ""


@pytest.mark.parametrize("secret", [None, "", "wrong-secret"])
def test_a_webhook_call_without_the_secret_is_refused_and_changes_nothing(stack, users, bot, secret):
    _set(stack["db"], "alice@example.test", telegram_chat_id="", telegram_token="")
    code = _connect_code(users["a"])
    status, _ = _hook(stack["port"], _msg(666, "/start " + code), secret=secret)
    assert status == 403
    assert _profile(stack["db"], "alice@example.test")[0] == "" and bot == []


def test_the_chat_is_notification_only(stack, users, bot):
    """Anything but /start and /stop is ignored, with one fixed line a day at most."""
    code = _connect_code(users["a"])
    _hook(stack["port"], _msg(555, "/start " + code))
    before = _profile(stack["db"], "alice@example.test")
    for text in ("hi Noa", "can you apply for me?", "/help"):
        assert _hook(stack["port"], _msg(555, text))[0] == 200
    assert bot[1:] == [(555, telegrambot.NOTIFY_ONLY_TEXT)]        # once, not three times
    assert _profile(stack["db"], "alice@example.test") == before


def test_groups_are_ignored(stack, users, bot):
    assert _hook(stack["port"], _msg(-100123, "/stop", chat_type="group"))[0] == 200
    assert bot == []


def test_stop_disconnects_and_turns_the_channel_off(stack, users, bot):
    _set(stack["db"], "alice@example.test", notification_channel="email")
    code = _connect_code(users["a"])
    _hook(stack["port"], _msg(555, "/start " + code))
    _hook(stack["port"], _msg(555, "/stop"))
    assert _profile(stack["db"], "alice@example.test") == ("", "", "email")
    assert bot[-1] == (555, telegrambot.STOPPED_TEXT)


def test_disconnect_in_settings(stack, users, bot):
    code = _connect_code(users["a"])
    _hook(stack["port"], _msg(555, "/start " + code))
    assert users["a"].post_json("/api/telegram/disconnect", {})[0] == 200
    chat, _tok, chans = _profile(stack["db"], "alice@example.test")
    assert chat == "" and "telegram" not in chans.split(",")


def test_connecting_replaces_a_legacy_own_bot(stack, users, bot):
    _set(stack["db"], "alice@example.test", telegram_token="999:own-bot", telegram_chat_id="42",
         notification_channel="telegram")
    code = _connect_code(users["a"])
    _hook(stack["port"], _msg(555, "/start " + code))
    assert _profile(stack["db"], "alice@example.test") == ("555", "", "telegram")


def test_stop_in_our_bot_never_touches_a_legacy_own_bot_with_the_same_chat_id(stack, users, bot):
    _set(stack["db"], "alice@example.test", telegram_token="999:own-bot", telegram_chat_id="555",
         notification_channel="telegram")
    _hook(stack["port"], _msg(555, "/stop"))
    assert _profile(stack["db"], "alice@example.test")[0] == "555"


def test_without_the_variables_there_is_no_bot(stack, users, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)
    assert users["a"].post_json("/api/telegram/connect", {})[0] == 503
    assert _hook(stack["port"], _msg(555, "/stop"))[0] == 403
    me = json.loads(users["a"].get("/api/me")[2])
    assert me["telegram_bot_available"] is False


def test_me_tells_settings_what_to_draw(stack, users, bot):
    code = _connect_code(users["a"])
    _hook(stack["port"], _msg(555, "/start " + code))
    me = json.loads(users["a"].get("/api/me")[2])
    assert (me["telegram_bot_available"], me["telegram_connected"], me["telegram_own_bot"]) == (True, True, False)


def test_only_an_admin_can_set_up_the_webhook(stack, users, bot):
    assert users["a"].post_json("/api/admin/telegram/setup", {})[0] == 403


def test_signed_out_cannot_ask_for_a_link(stack, bot):
    body = b"{}"
    c = http.client.HTTPConnection("127.0.0.1", stack["port"], timeout=10)
    try:
        c.request("POST", "/api/telegram/connect", body=body,
                  headers={"Content-Type": "application/json", "Content-Length": "2"})
        assert c.getresponse().status == 401
    finally:
        c.close()
