"""
auth.py — Authentication helpers for Job Hunter
"""
import hashlib
import hmac
import secrets

import os

import crypto
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie

# Route this module's print() calls through the logging module: timestamps,
# levels, the module name, and the request id of the request in flight.
import log as _log
print = _log.make_print(__name__)  # noqa: A001 - see log.py


# ── Session lifetime ─────────────────────────────────────────────────────────
#
# Sliding, not fixed: a session that is being used is extended, and one that is
# not dies on its own. 30 fixed days meant an abandoned laptop stayed signed in
# for a month; 14 sliding days means an idle session dies in two weeks while an
# active user is never signed out.
#
# The window is only rewritten once it is more than SESSION_REFRESH_AFTER old,
# so an extension costs one UPDATE a day per user rather than one per request.
SESSION_DAYS = int(os.environ.get("JH_SESSION_DAYS", 14))
SESSION_REFRESH_AFTER = int(os.environ.get("JH_SESSION_REFRESH_AFTER_HOURS", 24))

# The format the database compares against.
#
# THIS IS NOT COSMETIC. expires_date used to be written by datetime.now()
# .isoformat(), which produces "2026-10-15T05:48:31.558130" - a "T" separator,
# local time, and microseconds - while every comparison in this file is against
# SQL's datetime('now'), which produces "2026-09-15 05:48:31" - a space, and
# UTC. The comparison is lexicographic on TEXT, and "T" (0x54) sorts after
# " " (0x20), so on the expiry date itself an expired token compared GREATER
# than the current time and was accepted for the rest of that day. Proven by
# observation on 2026-09-15: a token that expired an hour ago was returned by
# the live query. Same format, same clock, or the check does not check.
_SQL_TIME = "%Y-%m-%d %H:%M:%S"


def _utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _stamp(dt) -> str:
    return dt.strftime(_SQL_TIME)


# Injected by app.py
_get_db     = None
_admin_email = ""   # email of the admin user (set from config.json)


def set_db_getter(fn):
    global _get_db
    _get_db = fn


def set_admin_email(email: str):
    global _admin_email
    _admin_email = email.strip().lower()


# ── Password ──────────────────────────────────────────────────────────────────

def hash_password(password: str, salt: str = None):
    if salt is None:
        salt = secrets.token_hex(32)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260_000)
    return dk.hex(), salt


def verify_password(password: str, stored_hash: str, salt: str) -> bool:
    computed, _ = hash_password(password, salt)
    return hmac.compare_digest(computed, stored_hash)


# ── Users ─────────────────────────────────────────────────────────────────────

def create_user(name: str, email: str, password: str):
    """Returns (user_id, error_message). error_message is None on success."""
    normalized_email = email.strip().lower()
    # The admin email (from config) gets the 'admin' role and daily schedule by default
    role               = "admin" if (_admin_email and normalized_email == _admin_email) else "user"
    default_frequency  = "daily" if role == "admin" else "weekly"

    conn = _get_db()
    try:
        pw_hash, salt = hash_password(password)
        conn.execute(
            "INSERT INTO users (name, email, password_hash, salt, created_date, role) VALUES (?,?,?,?,?,?)",
            (name.strip(), normalized_email, pw_hash, salt, datetime.now().isoformat(), role)
        )
        user_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO user_profiles (user_id, schedule_frequency) VALUES (?,?)",
            (user_id, default_frequency)
        )
        conn.commit()
        return user_id, None
    except Exception as e:
        if "UNIQUE" in str(e):
            return None, "An account with that email already exists."
        return None, str(e)
    finally:
        conn.close()


def authenticate(email: str, password: str):
    """Returns (user_dict, error_message)."""
    conn = _get_db()
    user = conn.execute(
        "SELECT * FROM users WHERE email=? AND is_active=1",
        (email.strip().lower(),)
    ).fetchone()
    conn.close()
    if not user:
        return None, "Invalid email or password."
    if not verify_password(password, user["password_hash"], user["salt"]):
        return None, "Invalid email or password."
    return dict(user), None


def change_password(user_id: int, current_pw: str, new_pw: str, keep_token: str = ""):
    conn = _get_db()
    user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    conn.close()
    if not user:
        return "User not found."
    if not verify_password(current_pw, user["password_hash"], user["salt"]):
        return "Current password is incorrect."
    pw_hash, salt = hash_password(new_pw)
    conn2 = _get_db()
    conn2.execute(
        "UPDATE users SET password_hash=?, salt=? WHERE id=?",
        (pw_hash, salt, user_id)
    )
    conn2.commit()
    conn2.close()
    # Every other session for this user dies with the old password. `keep_token`
    # is the one making the change, so the user is not signed out of the tab
    # they are typing in.
    revoked = delete_sessions_for_user(user_id, keep_token)
    if revoked:
        print("[auth] password change for user %s revoked %s other session(s)"
              % (user_id, revoked))
    return None


# ── Sessions ──────────────────────────────────────────────────────────────────

def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(48)
    expires = _stamp(_utc_now() + timedelta(days=SESSION_DAYS))
    conn = _get_db()
    conn.execute(
        "INSERT INTO sessions (token, user_id, expires_date) VALUES (?,?,?)",
        (token, user_id, expires)
    )
    conn.commit()
    conn.close()
    return token


def _slide_if_stale(token: str, current_expires) -> bool:
    """Extend a session that is being used. Returns True if it was rewritten.

    Rate-limited by SESSION_REFRESH_AFTER so a busy page does not turn every
    request into a write - the point is to keep an active session alive, not to
    record the exact moment it was last used.
    """
    full = timedelta(days=SESSION_DAYS)
    try:
        current = datetime.strptime(str(current_expires)[:19], _SQL_TIME)
    except (ValueError, TypeError):
        # Unparseable, which includes every row written in the old "T" format.
        # Rewriting it is the right answer: a window nobody can read is a window
        # nobody can enforce.
        current = _utc_now()
    if (full - (current - _utc_now())) < timedelta(hours=SESSION_REFRESH_AFTER):
        return False
    conn = _get_db()
    try:
        conn.execute("UPDATE sessions SET expires_date=? WHERE token=?",
                     (_stamp(_utc_now() + full), token))
        conn.commit()
        return True
    finally:
        conn.close()


def touch_session(token: str) -> bool:
    """Explicit form of the slide, for callers outside the session lookup."""
    if not token:
        return False
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT expires_date FROM sessions WHERE token=?", (token,)).fetchone()
    finally:
        conn.close()
    return _slide_if_stale(token, row["expires_date"]) if row else False


def delete_sessions_for_user(user_id: int, keep_token: str = "") -> int:
    """Sign a user out everywhere, optionally keeping the session doing it.

    Called when the password changes. Without it, changing your password - the
    one thing a user does when they think someone else is in their account -
    left the intruder signed in for the rest of the session's life.
    """
    conn = _get_db()
    try:
        if keep_token:
            cur = conn.execute("DELETE FROM sessions WHERE user_id=? AND token<>?",
                               (user_id, keep_token))
        else:
            cur = conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        conn.commit()
        return cur.rowcount or 0
    finally:
        conn.close()


def get_session_user(token: str):
    """Returns user dict (with profile) or None."""
    if not token:
        return None
    conn = _get_db()
    row = conn.execute("""
        SELECT u.id, u.name, u.email, u.created_date, u.role, u.plan,
               p.cv_path, p.cv_analyzed, p.cv_summary,
               p.cv_filename, p.cv_uploaded_date, p.cv_optimizer_date,
               p.job_titles, p.keywords, p.locations,
               p.salary_min, p.salary_max, p.experience_years, p.seniority,
               p.linkedin_url, p.phone,
               p.notification_channel,
               p.telegram_token, p.telegram_chat_id,
               p.twilio_account_sid, p.twilio_auth_token, p.whatsapp_number,
               p.email_address,
               p.schedule_frequency, p.search_hour, p.search_day_of_week,
               p.apply_hour, p.apply_day_of_week, p.onboarding_complete,
               p.onboarding_dismissed,
               p.auto_apply_enabled, p.weekdays_only
               , s.expires_date AS _session_expires
        FROM sessions s
        JOIN users u ON u.id = s.user_id
        LEFT JOIN user_profiles p ON p.user_id = u.id
        WHERE s.token=? AND s.expires_date > datetime('now') AND u.is_active=1
    """, (token,)).fetchone()
    conn.close()
    if row is None:
        return None
    row = dict(row)
    # Slide the window using the value this query already read, rather than
    # spending a second round trip per request on it.
    _slide_if_stale(token, row.pop("_session_expires", None))
    # The profile this returns is what /api/me hands the browser, so the
    # credentials have to be readable here or the settings page shows
    # ciphertext in the input boxes.
    return crypto.decrypt_row(row)


def delete_session(token: str):
    conn = _get_db()
    conn.execute("DELETE FROM sessions WHERE token=?", (token,))
    conn.commit()
    conn.close()


def cleanup_expired_sessions() -> int:
    """Delete sessions past their expiry. Returns the number removed."""
    conn = _get_db()
    try:
        cur = conn.execute("DELETE FROM sessions WHERE expires_date <= datetime('now')")
        conn.commit()
        return cur.rowcount or 0
    finally:
        conn.close()


def update_profile(user_id: int, **kwargs):
    if not kwargs:
        return
    # Every credential write in the app goes through here, so this is the one
    # place encryption has to happen. Non-secret fields pass through untouched.
    kwargs = crypto.encrypt_fields(kwargs)
    conn = _get_db()
    sets = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [user_id]
    conn.execute(f"UPDATE user_profiles SET {sets} WHERE user_id=?", vals)
    conn.commit()
    conn.close()


def update_user(user_id: int, **kwargs):
    if not kwargs:
        return
    conn = _get_db()
    sets = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [user_id]
    conn.execute(f"UPDATE users SET {sets} WHERE id=?", vals)
    conn.commit()
    conn.close()


# ── Cookie helpers ────────────────────────────────────────────────────────────

def get_token_from_request(headers) -> str:
    cookie_str = headers.get("Cookie", "")
    cookies = SimpleCookie()
    cookies.load(cookie_str)
    return cookies["session"].value if "session" in cookies else ""


def make_session_cookie(token: str) -> str:
    max_age = 30 * 24 * 3600
    return f"session={token}; Path=/; HttpOnly; Secure; Max-Age={max_age}; SameSite=Lax"


def clear_session_cookie() -> str:
    return "session=; Path=/; HttpOnly; Secure; Max-Age=0; SameSite=Lax"


# ── Google Sign-In (OAuth) ────────────────────────────────────

def find_or_create_google_user(google_sub: str, email: str, name: str = "", avatar_url: str = ""):
    """Resolve a Google identity to a local user.

    Returns (user_dict, is_new, error_message).
      1. Match by google_sub  -> existing linked account.
      2. Match by email       -> link Google onto the existing (password) account.
      3. Otherwise            -> create a new Google-backed user + profile row.
    Only Google-verified emails should reach this function (caller enforces).
    """
    normalized_email = (email or "").strip().lower()
    if not google_sub or not normalized_email:
        return None, False, "Missing Google account details."

    conn = _get_db()
    try:
        # 1. Already linked by Google subject id
        row = conn.execute(
            "SELECT * FROM users WHERE google_sub=? AND is_active=1", (google_sub,)
        ).fetchone()
        if row:
            return dict(row), False, None

        # 2. Existing account with the same verified email -> link it
        row = conn.execute(
            "SELECT * FROM users WHERE email=? AND is_active=1", (normalized_email,)
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE users SET google_sub=?, avatar_url=COALESCE(NULLIF(?,''), avatar_url) WHERE id=?",
                (google_sub, avatar_url or "", row["id"])
            )
            conn.commit()
            updated = conn.execute("SELECT * FROM users WHERE id=?", (row["id"],)).fetchone()
            return dict(updated), False, None

        # 3. Brand-new user. Admin email (from config) gets the admin role.
        role              = "admin" if (_admin_email and normalized_email == _admin_email) else "user"
        default_frequency = "daily" if role == "admin" else "weekly"
        # Google users have no password; store a random unusable hash so the
        # NOT NULL columns are satisfied and password login can never succeed.
        pw_hash, salt = hash_password(secrets.token_urlsafe(32))
        display_name = (name or normalized_email.split("@")[0]).strip()
        conn.execute(
            "INSERT INTO users (name, email, password_hash, salt, created_date, role, "
            "google_sub, auth_provider, avatar_url) VALUES (?,?,?,?,?,?,?,?,?)",
            (display_name, normalized_email, pw_hash, salt, datetime.now().isoformat(),
             role, google_sub, "google", avatar_url or "")
        )
        user_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO user_profiles (user_id, schedule_frequency) VALUES (?,?)",
            (user_id, default_frequency)
        )
        conn.commit()
        created = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(created), True, None
    except Exception as e:
        if "UNIQUE" in str(e):
            # Race: concurrent link/create. Re-fetch by sub or email.
            row = conn.execute(
                "SELECT * FROM users WHERE google_sub=? OR email=?",
                (google_sub, normalized_email)
            ).fetchone()
            if row:
                return dict(row), False, None
        return None, False, str(e)
    finally:
        conn.close()
