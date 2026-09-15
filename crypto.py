"""
crypto.py - the notification credentials, encrypted at rest.

Phase 3 of EXECUTION_PLAN_PUBLIC_LAUNCH.md.

`telegram_token`, `twilio_auth_token` and `email_smtp_pass` are live credentials
for someone else's account, and until now they sat in plain `TEXT` columns. That
mattered more the moment Phase 2 moved the data into Postgres: a database dump
is a file that gets copied to laptops, attached to tickets and kept in backups,
and every copy carried working credentials in the clear.

Three decisions worth knowing about:

  * **Values carry their own format.** Ciphertext is stored as `enc:v1:<token>`.
    A value without that prefix is legacy plaintext and is returned unchanged,
    so encryption can be switched on without a flag day and rows convert as
    they are rewritten.

  * **No key means no encryption, loudly - not an outage.** If
    JH_ENCRYPTION_KEY is unset the app behaves exactly as it did before and
    says so at boot and in /api/health. Refusing to start would turn a
    hardening feature into downtime; encrypting with a key nobody chose would
    be worse, because the data would be unrecoverable the first time the
    environment was rebuilt.

  * **A wrong key fails loudly too.** Decryption never falls back to returning
    ciphertext as if it were a value - that would send a Telegram message to an
    API with gibberish credentials and log a success. It raises, and the caller
    decides.

Recovery, stated plainly: if the key is lost, the encrypted credentials cannot
be read back. For this app that means each user re-enters their Telegram or
Twilio or SMTP details - annoying, not catastrophic. Keep JH_ENCRYPTION_KEY
wherever the rest of the deployment's secrets live.
"""
import base64
import hashlib
import os

# Route this module's print() calls through the logging module: timestamps,
# levels, the module name, and the request id of the request in flight.
import log as _log
print = _log.make_print(__name__)  # noqa: A001 - see log.py


PREFIX = "enc:v1:"

# The fields worth protecting. Names, not guesswork: everything here is a
# credential for a third-party account that can send messages as the user.
SECRET_FIELDS = ("telegram_token", "twilio_auth_token", "email_smtp_pass")

_WARNED = False


class DecryptionError(RuntimeError):
    """Raised when a stored value cannot be decrypted with the configured key."""


def _key():
    raw = (os.environ.get("JH_ENCRYPTION_KEY") or "").strip()
    if not raw:
        return None
    # Accept any passphrase rather than demanding a base64 Fernet key: the
    # operator should be able to paste a long random string without knowing
    # what Fernet wants. SHA-256 gives the 32 bytes Fernet requires.
    return base64.urlsafe_b64encode(hashlib.sha256(raw.encode("utf-8")).digest())


def fingerprint():
    """
    A short, safe identifier for the configured key - or None.

    2026-09-14: the encryptor checked that a key was PRESENT, never that it was
    the RIGHT one, and a placeholder string got used as the key. On that run the
    table happened to be empty so nothing was written; with rows present it
    would have encrypted them under a key the app does not have, which is the
    one unrecoverable mistake in this design. `is_encrypted()` is a prefix test,
    so those rows would even have counted as "already encrypted" on the next
    run - the damage would have looked like success.

    This is a hash of the derived key, not the key: it reveals nothing, and it
    lets /api/health and the script be compared at a glance.
    """
    key = _key()
    if key is None:
        return None
    return hashlib.sha256(key).hexdigest()[:12]


def available() -> bool:
    """Whether encryption is configured. /api/health reports this."""
    return _key() is not None


def _fernet():
    key = _key()
    if key is None:
        return None
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:      # pragma: no cover - pinned in requirements
        raise RuntimeError("JH_ENCRYPTION_KEY is set but cryptography is not installed: %s" % exc)
    return Fernet(key)


def warn_if_unconfigured():
    """Say it once at boot, not on every write."""
    global _WARNED
    if available() or _WARNED:
        return
    _WARNED = True
    bar = "=" * 78
    print(bar, flush=True)
    print("NOTIFICATION CREDENTIALS ARE STORED IN PLAINTEXT", flush=True)
    print("  JH_ENCRYPTION_KEY is not set, so %s" % ", ".join(SECRET_FIELDS), flush=True)
    print("  are written to the database unencrypted - and into every dump of it.", flush=True)
    print("  Set JH_ENCRYPTION_KEY to a long random string, then run", flush=True)
    print("  scripts/encrypt_credentials.py to convert the rows already stored.", flush=True)
    print(bar, flush=True)


def is_encrypted(value) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def encrypt(value):
    """
    Encrypt a credential. Returns the value unchanged when there is no key, when
    it is empty, or when it is already encrypted - so this is safe to call on
    every write without the caller tracking state.
    """
    if not value or not isinstance(value, str) or is_encrypted(value):
        return value
    f = _fernet()
    if f is None:
        warn_if_unconfigured()
        return value
    return PREFIX + f.encrypt(value.encode("utf-8")).decode("ascii")


def decrypt(value):
    """
    Decrypt a stored credential. A value with no prefix is legacy plaintext and
    comes back unchanged, which is what lets the two states coexist.
    """
    if not is_encrypted(value):
        return value
    f = _fernet()
    if f is None:
        raise DecryptionError(
            "a stored credential is encrypted but JH_ENCRYPTION_KEY is not set")
    from cryptography.fernet import InvalidToken
    try:
        return f.decrypt(value[len(PREFIX):].encode("ascii")).decode("utf-8")
    except InvalidToken:
        raise DecryptionError(
            "a stored credential could not be decrypted - JH_ENCRYPTION_KEY does "
            "not match the key it was encrypted with")


def encrypt_fields(mapping: dict) -> dict:
    """Encrypt the secret fields of a column->value mapping, leaving the rest."""
    return {k: (encrypt(v) if k in SECRET_FIELDS else v) for k, v in mapping.items()}


def decrypt_row(row):
    """
    Return a plain dict with the secret fields decrypted.

    Tolerant on read by design: one unreadable credential must not make a user's
    whole profile unloadable, so it becomes "" and is logged. The write path is
    where a bad key should stop things, not the page load.
    """
    if row is None:
        return None
    out = dict(row)
    for field in SECRET_FIELDS:
        if field in out and is_encrypted(out[field]):
            try:
                out[field] = decrypt(out[field])
            except DecryptionError as exc:
                print("[crypto] %s for user %s: %s" % (field, out.get("user_id") or out.get("id"), exc))
                out[field] = ""
    return out
