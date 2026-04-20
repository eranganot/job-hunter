"""
auth.py — Authentication helpers for Job Hunter
"""
import hashlib
import secrets
from datetime import datetime, timedelta
from http.cookies import SimpleCookie

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
    return computed == stored_hash


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


def change_password(user_id: int, current_pw: str, new_pw: str):
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
    return None


# ── Sessions ──────────────────────────────────────────────────────────────────

def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(48)
    expires = (datetime.now() + timedelta(days=30)).isoformat()
    conn = _get_db()
    conn.execute(
        "INSERT INTO sessions (token, user_id, expires_date) VALUES (?,?,?)",
        (token, user_id, expires)
    )
    conn.commit()
    conn.close()
    return token


def get_session_user(token: str):
    """Returns user dict (with profile) or None."""
    if not token:
        return None
    conn = _get_db()
    row = conn.execute("""
        SELECT u.id, u.name, u.email, u.created_date, u.role,
               p.cv_path, p.cv_analyzed, p.cv_summary,
               p.job_titles, p.keywords, p.locations,
               p.salary_min, p.salary_max, p.experience_years, p.seniority,
               p.linkedin_url, p.phone,
               p.notification_channel,
               p.telegram_token, p.telegram_chat_id,
               p.twilio_account_sid, p.twilio_auth_token, p.whatsapp_number,
               p.schedule_frequency, p.search_hour, p.search_day_of_week,
               p.apply_hour, p.apply_day_of_week, p.onboarding_complete,
               p.onboarding_dismissed
        FROM sessions s
        JOIN users u ON u.id = s.user_id
        LEFT JOIN user_profiles p ON p.user_id = u.id
        WHERE s.token=? AND s.expires_date > datetime('now') AND u.is_active=1
    """, (token,)).fetchone()
    conn.close()
    return dict(row) if row else None


def delete_session(token: str):
    conn = _get_db()
    conn.execute("DELETE FROM sessions WHERE token=?", (token,))
    conn.commit()
    conn.close()


def update_profile(user_id: int, **kwargs):
    if not kwargs:
        return
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
    return f"session={token}; Path=/; HttpOnly; Max-Age={max_age}; SameSite=Lax"


def clear_session_cookie() -> str:
    return "session=; Path=/; HttpOnly; Max-Age=0; SameSite=Lax"
