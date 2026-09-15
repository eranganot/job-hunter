"""
tests/test_crypto.py - notification credentials encrypted at rest.

These are live credentials for someone else's account - a Telegram bot token
sends messages as that bot. The property that matters is not "it encrypts", it
is that the two states coexist safely: rows written before the key existed must
keep working, and a wrong key must fail loudly rather than hand the caller
plausible-looking garbage.
"""
import importlib
import io

import pytest

import crypto

TOKEN = "123456789:AAH-real-looking-telegram-bot-token_x"


@pytest.fixture
def keyed(monkeypatch):
    monkeypatch.setenv("JH_ENCRYPTION_KEY", "a long random passphrase for tests")
    importlib.reload(crypto)
    yield crypto
    monkeypatch.delenv("JH_ENCRYPTION_KEY", raising=False)
    importlib.reload(crypto)


@pytest.fixture
def unkeyed(monkeypatch):
    monkeypatch.delenv("JH_ENCRYPTION_KEY", raising=False)
    importlib.reload(crypto)
    return crypto


# ── The basics ────────────────────────────────────────────────────────────────

def test_a_credential_survives_the_round_trip(keyed):
    assert keyed.decrypt(keyed.encrypt(TOKEN)) == TOKEN


def test_the_stored_form_does_not_contain_the_secret(keyed):
    stored = keyed.encrypt(TOKEN)
    assert TOKEN not in stored
    assert "AAH-real-looking" not in stored
    assert stored.startswith("enc:v1:")


def test_encrypting_twice_does_not_double_wrap(keyed):
    once = keyed.encrypt(TOKEN)
    assert keyed.encrypt(once) == once
    assert keyed.decrypt(keyed.encrypt(once)) == TOKEN


def test_empty_values_are_left_alone(keyed):
    for empty in ("", None):
        assert keyed.encrypt(empty) == empty


# ── The two states have to coexist ────────────────────────────────────────────

def test_legacy_plaintext_reads_back_unchanged(keyed):
    """Rows written before the key existed must keep working."""
    assert keyed.decrypt("plain-legacy-token") == "plain-legacy-token"


def test_with_no_key_the_app_behaves_exactly_as_before(unkeyed):
    """No key is a non-regression, not an outage."""
    assert unkeyed.available() is False
    assert unkeyed.encrypt(TOKEN) == TOKEN
    assert unkeyed.decrypt(TOKEN) == TOKEN


def test_a_missing_key_cannot_silently_swallow_existing_ciphertext(keyed, monkeypatch):
    """Losing the key must not look like 'the credential is empty'."""
    stored = keyed.encrypt(TOKEN)
    monkeypatch.delenv("JH_ENCRYPTION_KEY", raising=False)
    importlib.reload(crypto)
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(stored)


def test_the_wrong_key_raises_rather_than_returning_garbage(keyed, monkeypatch):
    """
    Returning ciphertext as if it were a value would send it to Telegram's API
    and log a success. It has to raise.
    """
    stored = keyed.encrypt(TOKEN)
    monkeypatch.setenv("JH_ENCRYPTION_KEY", "a different passphrase entirely")
    importlib.reload(crypto)
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(stored)


# ── Field selection ───────────────────────────────────────────────────────────

def test_only_the_secret_fields_are_encrypted(keyed):
    row = {"telegram_token": TOKEN, "telegram_chat_id": "12345",
           "notification_channel": "telegram", "email_smtp_pass": "hunter2",
           "twilio_auth_token": "abc", "whatsapp_number": "+972500000000"}
    out = keyed.encrypt_fields(row)

    for secret in ("telegram_token", "email_smtp_pass", "twilio_auth_token"):
        assert out[secret].startswith("enc:v1:"), secret
    for plain in ("telegram_chat_id", "notification_channel", "whatsapp_number"):
        assert out[plain] == row[plain], plain


def test_the_secret_field_list_matches_real_columns():
    """A renamed column must not quietly stop being encrypted."""
    import io
    schema = io.open("migrations.py", encoding="utf-8").read()
    for field in crypto.SECRET_FIELDS:
        assert field in schema, "%s is not a column in migrations.py" % field


def test_decrypt_row_is_tolerant_so_one_bad_value_does_not_break_a_login(keyed, monkeypatch, caplog):
    """A page load must not 500 because one credential is unreadable."""
    row = {"user_id": 7, "telegram_token": keyed.encrypt(TOKEN), "telegram_chat_id": "1"}
    monkeypatch.setenv("JH_ENCRYPTION_KEY", "the wrong passphrase")
    importlib.reload(crypto)

    out = crypto.decrypt_row(row)
    assert out["telegram_token"] == ""
    assert out["telegram_chat_id"] == "1", "an unrelated field was damaged"
    # caplog, not capsys: crypto's print() now goes through log.py (see its
    # docstring), so the text is on a log record rather than on stdout.
    assert "could not be decrypted" in caplog.text
    # And it is a WARNING, not INFO. The literal here is "[crypto] %s for user
    # %s: %s" - every failure word lives in the exception, at runtime - so this
    # message is exactly the case the static level audit could not see, and the
    # one that proved the inference needed its WARNING tier.
    levels = [r.levelname for r in caplog.records if "could not be decrypted" in r.getMessage()]
    assert levels and set(levels) == {"WARNING"}, levels


def test_decrypt_row_handles_a_missing_row(keyed):
    assert keyed.decrypt_row(None) is None


# ── Proving the key is the RIGHT key ─────────────────────────────────────────
#
# 2026-09-14: a placeholder string was used as the key by mistake. The table was
# empty so nothing was written - but with rows present, the encryptor would have
# written them under a key the app does not have. Worse, `is_encrypted()` is a
# prefix test, so on the next run those rows would have counted as "already
# encrypted": the damage would have looked like success.

def test_the_fingerprint_identifies_the_key(keyed):
    assert keyed.fingerprint() and len(keyed.fingerprint()) == 12


def test_different_keys_have_different_fingerprints(monkeypatch):
    seen = set()
    for value in ("the real key", "<the value from the web service>", "another"):
        monkeypatch.setenv("JH_ENCRYPTION_KEY", value)
        importlib.reload(crypto)
        seen.add(crypto.fingerprint())
    assert len(seen) == 3, "two different keys produced the same fingerprint"


def test_the_fingerprint_does_not_leak_the_key(monkeypatch):
    secret = "a-very-distinctive-passphrase-value"
    monkeypatch.setenv("JH_ENCRYPTION_KEY", secret)
    importlib.reload(crypto)
    fp = crypto.fingerprint()
    assert secret not in fp and secret[:8] not in fp


def test_no_key_has_no_fingerprint(unkeyed):
    assert unkeyed.fingerprint() is None


def test_the_encryptor_refuses_a_key_that_cannot_read_existing_data():
    """The guard has to be in the script, not merely available to it."""
    src = io.open("scripts/encrypt_credentials.py", encoding="utf-8").read()
    assert "cannot decrypt the credentials already stored" in src
    assert "Nothing was written" in src
