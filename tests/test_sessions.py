"""
tests/test_sessions.py - session expiry, the sliding window, and revocation.

The first test in this file is the regression for a bug that made the expiry
check decorative. expires_date was written by datetime.now().isoformat() -
"2026-10-15T05:48:31.558130" - and compared against SQL datetime('now') -
"2026-09-15 05:48:31". TEXT comparison is lexicographic and "T" (0x54) sorts
after " " (0x20), so on the expiry date itself an expired token compared
GREATER than the current time. A session was therefore valid for the whole of
the day it was supposed to die on, and cleanup_expired_sessions could not
delete it either, because it uses the same comparison.

That it survived this long is the interesting part: every test of session
expiry until now used a token expiring days away or days past, where the DATE
differs and the separator never decides the comparison. The bug only shows on
the boundary day, which is exactly the day nobody writes a fixture for.
"""
from datetime import datetime, timedelta, timezone

import pytest

import auth
import db as database
import migrations


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "s.db"), raising=False)
    monkeypatch.setattr(database, "DATABASE_URL", None, raising=False)
    monkeypatch.setattr(database, "BACKEND_REFUSAL", None, raising=False)
    monkeypatch.delenv("DB_BACKEND", raising=False)
    database.init_db()
    auth.set_db_getter(database.get_db)
    auth.set_admin_email("admin@example.test")
    conn = database.get_db()
    for i, email in ((1, "u1@example.test"), (2, "u2@example.test")):
        conn.execute("INSERT INTO users (id,name,email,password_hash,salt) VALUES (?,?,?,?,?)",
                     (i, "u%d" % i, email, "h", "s"))
    conn.commit()
    conn.close()
    return database


def _utc(delta):
    return (datetime.now(timezone.utc).replace(tzinfo=None) + delta)


def _put_session(token, expires_str, user_id=1):
    conn = database.get_db()
    conn.execute("INSERT INTO sessions (token, user_id, expires_date) VALUES (?,?,?)",
                 (token, user_id, expires_str))
    conn.commit()
    conn.close()


# ── The expiry comparison ────────────────────────────────────────────────────

def test_a_session_that_expired_an_hour_ago_is_refused(db):
    """The regression. Written the old way this token was ACCEPTED, because
    'T' sorts after ' ' and the date part was identical."""
    _put_session("stale", auth._stamp(_utc(timedelta(hours=-1))))
    assert auth.get_session_user("stale") is None


def test_a_session_expiring_in_an_hour_is_still_accepted(db):
    """The other side of the boundary - the fix must not expire live sessions."""
    _put_session("fresh", auth._stamp(_utc(timedelta(hours=1))))
    assert auth.get_session_user("fresh") is not None


def test_the_stored_format_is_the_one_the_database_compares_against(db):
    """The bug in one assertion: same separator, same width, same clock."""
    token = auth.create_session(1)
    conn = database.get_db()
    stored = conn.execute("SELECT expires_date FROM sessions WHERE token=?",
                          (token,)).fetchone()["expires_date"]
    sql_now = conn.execute("SELECT datetime('now')").fetchone()[0]
    conn.close()
    assert "T" not in stored, stored
    assert len(stored) == len(sql_now), (stored, sql_now)
    assert stored[10] == sql_now[10] == " "


def test_a_new_session_is_stamped_in_utc_not_local_time(db, monkeypatch):
    """The stamp was local while the comparison was UTC, so every session in a
    UTC+3 country quietly outlived its window by three hours.

    Asserted on the MECHANISM, not on the value: CI and the sandbox both run in
    UTC, where local and UTC are the same number and a value comparison proves
    nothing. A first version of this test passed happily with the clock put
    back to datetime.now() - the mutation check is what caught it. So the real
    datetime is replaced by one whose local time is deliberately three hours
    off, which is Israel, which is where this app is developed.
    """
    real = auth.datetime
    offset = timedelta(hours=3)

    class SkewedDatetime(real):
        @classmethod
        def now(cls, tz=None):
            base = real.now(timezone.utc)
            return base if tz is not None else (base + offset).replace(tzinfo=None)

    monkeypatch.setattr(auth, "datetime", SkewedDatetime)
    token = auth.create_session(1)
    conn = database.get_db()
    stored = conn.execute("SELECT expires_date FROM sessions WHERE token=?",
                          (token,)).fetchone()["expires_date"]
    conn.close()

    expected_utc = real.now(timezone.utc).replace(tzinfo=None) + timedelta(days=auth.SESSION_DAYS)
    drift = abs((real.strptime(stored, "%Y-%m-%d %H:%M:%S") - expected_utc).total_seconds())
    assert drift < 120, (
        "session stamped %s; UTC expiry is %s - the stamp is following the "
        "local clock, so the window is %sh longer than it says"
        % (stored, expected_utc, offset.total_seconds() / 3600))


def test_an_expired_session_is_actually_deletable(db):
    """cleanup_expired_sessions uses the same comparison, so it inherited the
    same blind spot - an expired-today row could never be swept."""
    _put_session("stale", auth._stamp(_utc(timedelta(hours=-1))))
    assert auth.cleanup_expired_sessions() == 1


# ── The sliding window ───────────────────────────────────────────────────────

def test_using_a_session_extends_it(db):
    _put_session("used", auth._stamp(_utc(timedelta(days=2))))
    assert auth.get_session_user("used") is not None
    conn = database.get_db()
    after = conn.execute("SELECT expires_date FROM sessions WHERE token='used'").fetchone()[0]
    conn.close()
    remaining = datetime.strptime(after, "%Y-%m-%d %H:%M:%S") - _utc(timedelta(0))
    assert remaining > timedelta(days=auth.SESSION_DAYS - 1)


def test_a_fresh_session_is_not_rewritten_on_every_request(db):
    """An extension per request is a database write per request. The window is
    only rewritten once it has aged past the refresh threshold."""
    token = auth.create_session(1)
    conn = database.get_db()
    before = conn.execute("SELECT expires_date FROM sessions WHERE token=?",
                          (token,)).fetchone()[0]
    conn.close()
    for _ in range(3):
        auth.get_session_user(token)
    conn = database.get_db()
    after = conn.execute("SELECT expires_date FROM sessions WHERE token=?",
                         (token,)).fetchone()[0]
    conn.close()
    assert before == after


def test_an_unreadable_expiry_is_rewritten_rather_than_trusted(db):
    """Rows in the old 'T' format cannot be parsed by the slide. A window
    nobody can read is a window nobody can enforce, so it is replaced."""
    old_format = (_utc(timedelta(days=9))).isoformat()      # the pre-fix shape
    _put_session("legacy", old_format)
    auth.get_session_user("legacy")
    conn = database.get_db()
    after = conn.execute("SELECT expires_date FROM sessions WHERE token='legacy'").fetchone()[0]
    conn.close()
    assert "T" not in after, after


# ── Revocation ───────────────────────────────────────────────────────────────

def test_changing_the_password_signs_out_every_other_session(db):
    """The one action a user takes when they think someone else is in their
    account. Before this it did nothing to the intruder's session."""
    conn = database.get_db()
    pw_hash, salt = auth.hash_password("correct-horse-1")
    conn.execute("UPDATE users SET password_hash=?, salt=? WHERE id=1", (pw_hash, salt))
    conn.commit(); conn.close()

    mine = auth.create_session(1)
    theirs = auth.create_session(1)

    assert auth.change_password(1, "correct-horse-1", "new-horse-2", keep_token=mine) is None
    assert auth.get_session_user(theirs) is None, "the other session survived"
    assert auth.get_session_user(mine) is not None, "the caller was signed out of their own tab"


def test_a_failed_password_change_revokes_nothing(db):
    """A wrong current password must not be a way to sign someone else out."""
    conn = database.get_db()
    pw_hash, salt = auth.hash_password("correct-horse-1")
    conn.execute("UPDATE users SET password_hash=?, salt=? WHERE id=1", (pw_hash, salt))
    conn.commit(); conn.close()

    theirs = auth.create_session(1)
    assert auth.change_password(1, "wrong-password", "new-horse-2") is not None
    assert auth.get_session_user(theirs) is not None


def test_revocation_does_not_reach_another_users_sessions(db):
    mine = auth.create_session(1)
    other_user = auth.create_session(2)
    auth.delete_sessions_for_user(1, keep_token="")
    assert auth.get_session_user(mine) is None
    assert auth.get_session_user(other_user) is not None
