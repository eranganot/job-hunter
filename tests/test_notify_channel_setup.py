"""
A notification channel that can never send is refused at save, and one that is
already stored that way is a WARNING, not an ERROR (2026-11-22).

JOB-HUNTER-M: "[notify] Error on whatsapp: WhatsApp is selected but the Twilio
details are missing" - 14 Sentry alerts from 2026-11-11 to 11-22 for one
profile. /api/save-notifications stored WhatsApp with no Twilio details, and
every notification after that raised inside the sender and was logged at ERROR.
"""
import json

import pytest

from tests.test_routes import stack, users, _user_id  # noqa: F401


# ── the save route ───────────────────────────────────────────────────────────

def _channel_of(db, email):
    conn = db.get_db()
    try:
        return conn.execute("SELECT p.notification_channel FROM user_profiles p JOIN users u "
                            "ON u.id=p.user_id WHERE u.email=?", (email,)).fetchone()[0]
    finally:
        conn.close()


@pytest.mark.parametrize("channel, fields", [
    ("telegram", {"telegram_token": "t", "telegram_chat_id": ""}),
    ("telegram", {"telegram_token": "", "telegram_chat_id": "c"}),
    ("email",    {"email_address": "  "}),
    ("telegram,email", {"telegram_token": "t", "telegram_chat_id": "c", "email_address": ""}),  # one good, one empty
])
def test_a_channel_that_could_never_send_is_refused_with_the_reason(stack, users, channel, fields):
    before = _channel_of(stack["db"], "alice@example.test")
    status, _loc, body = users["a"].post_json(
        "/api/save-notifications", dict(fields, notification_channel=channel))
    assert status == 400, (status, body)
    err = json.loads(body)["error"]
    assert "not connected" in err or "no address is set" in err
    assert _channel_of(stack["db"], "alice@example.test") == before      # nothing stored


def test_a_complete_channel_saves_and_a_later_partial_save_reads_what_is_stored(stack, users):
    ok = {"notification_channel": "telegram", "telegram_token": "t", "telegram_chat_id": "c"}
    assert users["a"].post_json("/api/save-notifications", ok)[0] == 200
    assert _channel_of(stack["db"], "alice@example.test") == "telegram"
    # Re-selecting the same channel without resending the secrets uses the stored ones.
    assert users["a"].post_json("/api/save-notifications", {"notification_channel": "telegram"})[0] == 200
    # Turning everything off is always allowed.
    assert users["a"].post_json("/api/save-notifications", {"notification_channel": "none"})[0] == 200
    assert _channel_of(stack["db"], "alice@example.test") == "none"


def test_saving_credentials_without_a_channel_is_not_validated(stack, users):
    """The UI sends notification_channel every time, but an API caller updating a
    token alone has selected nothing new, so there is nothing to refuse."""
    assert users["a"].post_json("/api/save-notifications", {"telegram_token": "new"})[0] == 200


# ── WhatsApp is hidden (owner, 2026-11-23) ───────────────────────────────────

@pytest.mark.parametrize("channel", ["whatsapp", "telegram,whatsapp", "whatsapp,email"])
def test_whatsapp_cannot_be_selected_even_with_full_details(stack, users, channel):
    before = _channel_of(stack["db"], "alice@example.test")
    status, _loc, body = users["a"].post_json("/api/save-notifications", {
        "notification_channel": channel, "telegram_token": "t", "telegram_chat_id": "c",
        "email_address": "a@e.com", "twilio_account_sid": "AC1", "twilio_auth_token": "tok",
        "whatsapp_number": "+972500000000"})
    assert status == 400 and "not available" in json.loads(body)["error"]
    assert _channel_of(stack["db"], "alice@example.test") == before


def test_the_whatsapp_test_button_says_unavailable_and_sends_nothing(stack, users, monkeypatch):
    sent = []
    monkeypatch.setattr(stack["app"], "send_whatsapp", lambda *a: sent.append(a))
    status, _loc, body = users["a"].post_json("/api/test-notification", {"channel": "whatsapp"})
    out = json.loads(body)
    assert status == 200 and out["success"] is False and "not available" in out["error"]
    assert sent == []
