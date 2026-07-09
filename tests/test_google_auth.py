"""
tests/test_google_auth.py
Unit tests for auth.find_or_create_google_user — link-by-sub, link-by-email,
new-user creation, and admin-role assignment.
"""
import sqlite3
import pytest

import auth


class _NonClosing(sqlite3.Connection):
    """Keep the shared in-memory connection open across auth's close() calls."""
    def close(self):
        pass


@pytest.fixture
def google_db(monkeypatch):
    conn = sqlite3.connect(":memory:", factory=_NonClosing)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT NOT NULL,
            email         TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            salt          TEXT NOT NULL,
            created_date  TEXT DEFAULT (datetime('now')),
            is_active     INTEGER DEFAULT 1,
            role          TEXT DEFAULT 'user',
            google_sub    TEXT DEFAULT NULL,
            auth_provider TEXT DEFAULT 'password',
            avatar_url    TEXT DEFAULT NULL
        );
        CREATE UNIQUE INDEX idx_users_google_sub ON users(google_sub) WHERE google_sub IS NOT NULL;
        CREATE TABLE user_profiles (
            user_id            INTEGER PRIMARY KEY,
            schedule_frequency TEXT DEFAULT 'weekly'
        );
        CREATE TABLE sessions (
            token        TEXT PRIMARY KEY,
            user_id      INTEGER NOT NULL,
            created_date TEXT DEFAULT (datetime('now')),
            expires_date TEXT NOT NULL
        );
    """)
    monkeypatch.setattr(auth, "_get_db", lambda: conn)
    monkeypatch.setattr(auth, "_admin_email", "admin@example.com")
    yield conn
    sqlite3.Connection.close(conn)


def _count_users(conn):
    return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def test_creates_new_google_user(google_db):
    user, is_new, err = auth.find_or_create_google_user(
        "sub-123", "New.Person@Example.com", "New Person", "http://pic")
    assert err is None
    assert is_new is True
    assert user["email"] == "new.person@example.com"      # normalized
    assert user["google_sub"] == "sub-123"
    assert user["auth_provider"] == "google"
    assert user["role"] == "user"
    # a profile row must exist so the app doesn't 500 on first load
    prof = google_db.execute(
        "SELECT * FROM user_profiles WHERE user_id=?", (user["id"],)).fetchone()
    assert prof is not None


def test_admin_email_gets_admin_role(google_db):
    user, is_new, err = auth.find_or_create_google_user(
        "sub-admin", "admin@example.com", "Admin")
    assert err is None and is_new is True
    assert user["role"] == "admin"


def test_second_login_matches_by_sub(google_db):
    u1, new1, _ = auth.find_or_create_google_user("sub-xyz", "a@example.com", "A")
    u2, new2, err = auth.find_or_create_google_user("sub-xyz", "a@example.com", "A")
    assert err is None
    assert new1 is True and new2 is False
    assert u1["id"] == u2["id"]
    assert _count_users(google_db) == 1


def test_links_existing_password_account_by_email(google_db):
    # pre-existing password account (no google_sub)
    pw_hash, salt = auth.hash_password("secret123")
    google_db.execute(
        "INSERT INTO users (name, email, password_hash, salt, role) VALUES (?,?,?,?,?)",
        ("Eran", "eran@example.com", pw_hash, salt, "user"))
    google_db.execute("INSERT INTO user_profiles (user_id) VALUES (1)")

    user, is_new, err = auth.find_or_create_google_user(
        "sub-eran", "Eran@example.com", "Eran G", "http://avatar")
    assert err is None
    assert is_new is False                    # linked, not created
    assert user["id"] == 1
    assert user["google_sub"] == "sub-eran"
    assert user["avatar_url"] == "http://avatar"
    assert _count_users(google_db) == 1       # no duplicate row


def test_rejects_missing_details(google_db):
    user, is_new, err = auth.find_or_create_google_user("", "x@example.com", "X")
    assert user is None and err
    user, is_new, err = auth.find_or_create_google_user("sub", "", "X")
    assert user is None and err
