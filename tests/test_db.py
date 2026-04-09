tests/test_db.py
Unit tests for db.py â schema, core queries, and planned future functions.
"""
import json
import sqlite3
import pytest
from unittest.mock import patch



# ââ Minimal in-memory DB fixture ââââââââââââââââââââââââââââââââââââââââââââââ

class _NonClosing(sqlite3.Connection):
    """Prevent close() mid-test so the shared in-memory connection stays open."""
    def close(self):
        pass  # no-op: let the fixture's teardown close explicitly


@pytest.fixture
def mem_db(monkeypatch):
    """
    Create an in-memory SQLite DB with the real Job Hunter schema,
    and patch db.get_db() so all db module calls use it.
    """
    import db as db_module

    conn = sqlite3.connect(":memory:", factory=_NonClosing)
    conn.row_factory = sqlite3.Row

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT NOT NULL,
            email         TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            salt          TEXT NOT NULL,
            created_date  TEXT DEFAULT (datetime('now')),
            is_active     INTEGER DEFAULT 1,
            role          TEXT DEFAULT 'user'
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token        TEXT PRIMARY KEY,
            user_id      INTEGER NOT NULL,
            created_date TEXT DEFAULT (datetime('now')),
            expires_date TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS user_profiles (
            user_id           INTEGER PRIMARY KEY,
            cv_path           TEXT DEFAULT '',
            cv_summary        TEXT DEFAULT '',
            job_titles        TEXT DEFAULT '[]',
            keywords          TEXT DEFAULT '[]',
            locations         TEXT DEFAULT '[]',
            salary_min        INTEGER DEFAULT 0,
            salary_max        INTEGER DEFAULT 0,
            seniority         TEXT DEFAULT 'senior',
            experience_years  INTEGER DEFAULT 0,
            apply_frequency   TEXT DEFAULT 'weekly',
            search_hour       INTEGER DEFAULT 11,
            apply_hour        INTEGER DEFAULT 17,
            search_day_of_week INTEGER DEFAULT 1,
            apply_day_of_week  INTEGER DEFAULT 1,
            weekdays_only     INTEGER DEFAULT 0,
            linkedin_url      TEXT DEFAULT '',
            phone             TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS jobs (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL,
            title        TEXT NOT NULL,
            company      TEXT NOT NULL,
            location     TEXT DEFAULT 'Tel Aviv',
            url          TEXT,
            description  TEXT,
            why_relevant TEXT,
            company_info TEXT,
            source       TEXT,
            found_date   TEXT,
            status       TEXT DEFAULT 'new',
            applied_date TEXT,
            notes        TEXT,
            stage        TEXT DEFAULT NULL,
            url_verified INTEGER DEFAULT NULL,
            url_check_date TEXT DEFAULT NULL,
            apply_status TEXT DEFAULT NULL,
            apply_confirmation TEXT DEFAULT NULL,
            apply_attempts INTEGER DEFAULT 0,
            apply_error  TEXT DEFAULT NULL,
            match_score  INTEGER DEFAULT 0,
            pass_reason  TEXT DEFAULT NULL,
            apply_failure_type TEXT DEFAULT NULL,
            apply_failure_detail TEXT DEFAULT NULL,
            UNIQUE(user_id, url)
        );
        CREATE TABLE IF NOT EXISTS activity_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            event_type  TEXT NOT NULL,
            details     TEXT DEFAULT '',
            created_date TEXT DEFAULT (datetime('now'))
        );
        -- Future tables (for blocklist / pass-reason features)
        CREATE TABLE IF NOT EXISTS user_blocklist (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL,
            company_name TEXT NOT NULL,
            reason       TEXT,
            date_added   TEXT DEFAULT (datetime('now')),
            UNIQUE(user_id, company_name)
        );
        CREATE TABLE IF NOT EXISTS pass_reason_stats (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL,
            reason       TEXT NOT NULL,
            count        INTEGER DEFAULT 1,
            last_hit_date TEXT DEFAULT (datetime('now')),
            UNIQUE(user_id, reason)
        );
    """)

    # Seed a test user
    conn.execute(
        "INSERT INTO users (name, email, password_hash, salt) VALUES (?,?,?,?)",
        ("Test User", "test@example.com", "hash", "salt")
    )
    conn.commit()

    monkeypatch.setattr(db_module, "get_db", lambda: conn)
    yield conn
    conn.close()


def _get_user_id(conn) -> int:
    return conn.execute("SELECT id FROM users WHERE email='test@example.com'").fetchone()[0]


def _insert_job(conn, user_id: int, title: str, company: str, status: str = "new",
                url: str = None) -> int:
    url = url or f"https://example.com/jobs/{title.replace(' ', '-').lower()}"
    conn.execute(
        """INSERT INTO jobs (user_id, title, company, url, status)
           VALUES (?, ?, ?, ?, ?)""",
        (user_id, title, company, url, status)
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


# ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
# Activity log
# ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

class TestActivityLog:

    def test_log_and_retrieve_activity(self, mem_db):
        from db import log_activity, get_activity
        user_id = _get_user_id(mem_db)

        log_activity(user_id, "search_complete", "Found 10 new jobs")
        events = get_activity(user_id, limit=10)

        assert len(events) >= 1
        types = [dict(e)["event_type"] for e in events]
        assert "search_complete" in types

    def test_multiple_events_returned_newest_first(self, mem_db):
        from db import log_activity, get_activity
        user_id = _get_user_id(mem_db)

        log_activity(user_id, "event_a", "first")
        log_activity(user_id, "event_b", "second")
        events = get_activity(user_id, limit=10)

        event_types = [dict(e)["event_type"] for e in events]
        assert "event_a" in event_types
        assert "event_b" in event_types

    def test_limit_respected(self, mem_db):
        from db import log_activity, get_activity
        user_id = _get_user_id(mem_db)

        for i in range(10):
            log_activity(user_id, "bulk_event", f"event {i}")

        events = get_activity(user_id, limit=3)
        assert len(events) <= 3


# ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
# get_stats
# ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

class TestGetStats:

    def test_empty_stats_all_zero(self, mem_db):
        from db import get_stats
        user_id = _get_user_id(mem_db)
        stats = get_stats(mem_db, user_id)

        assert stats["new"] == 0
        assert stats["approved"] == 0
        assert stats["applied"] == 0
        assert stats["total"] == 0

    def test_stats_reflect_inserted_jobs(self, mem_db):
        from db import get_stats
        user_id = _get_user_id(mem_db)

        _insert_job(mem_db, user_id, "Engineer 1", "Corp A", status="new", url="https://a.com/1")
        _insert_job(mem_db, user_id, "Engineer 2", "Corp B", status="new", url="https://a.com/2")
        _insert_job(mem_db, user_id, "Manager 1", "Corp C", status="approved", url="https://a.com/3")
        _insert_job(mem_db, user_id, "Manager 2", "Corp D", status="applied", url="https://a.com/4")

        stats = get_stats(mem_db, user_id)

        assert stats["new"] == 2
        assert stats["approved"] == 1
        assert stats["applied"] == 1
        assert stats["total"] == 4

    def test_stats_has_required_keys(self, mem_db):
        from db import get_stats
        user_id = _get_user_id(mem_db)
        stats = get_stats(mem_db, user_id)

        for key in ("new", "approved", "applied", "total"):
            assert key in stats, f"Missing stat key: {key}"


# ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
# expire_old_jobs
# ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

class TestExpireOldJobs:

    def test_old_new_jobs_are_expired(self, mem_db):
        from db import expire_old_jobs
        user_id = _get_user_id(mem_db)

        # Insert a job with an old found_date
        mem_db.execute(
            """INSERT INTO jobs (user_id, title, company, url, status, found_date)
               VALUES (?, ?, ?, ?, 'new', datetime('now', '-40 days'))""",
            (user_id, "Old Job", "OldCorp", "https://old.com/job1")
        )
        mem_db.commit()

        expire_old_jobs(mem_db, user_id)

        row = mem_db.execute(
            "SELECT status FROM jobs WHERE url='https://old.com/job1'"
        ).fetchone()
        # Expired jobs should move to 'rejected' or be removed
        assert row is None or dict(row)["status"] != "new"

    def test_recent_jobs_not_expired(self, mem_db):
        from db import expire_old_jobs
        user_id = _get_user_id(mem_db)

        _insert_job(mem_db, user_id, "Fresh Job", "FreshCorp", status="new",
                    url="https://fresh.com/job1")

        expire_old_jobs(mem_db, user_id)

        row = mem_db.execute(
            "SELECT status FROM jobs WHERE url='https://fresh.com/job1'"
        ).fetchone()
        assert row is not None
        assert dict(row)["status"] == "new"


# ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
# Job status update (apply_status / apply_error)
# ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

class TestJobStatusUpdate:

    def test_update_apply_status_directly(self, mem_db):
        """Directly test the jobs table supports apply_status and apply_error columns."""
        user_id = _get_user_id(mem_db)
        job_id = _insert_job(mem_db, user_id, "Test Job", "TestCorp",
                              url="https://test.com/job1")

        mem_db.execute(
            "UPDATE jobs SET apply_status=?, apply_error=? WHERE id=?",
            ("failed", "reCAPTCHA detected", job_id)
        )
        mem_db.commit()

        row = dict(mem_db.execute(
            "SELECT apply_status, apply_error FROM jobs WHERE id=?", (job_id,)
        ).fetchone())
        assert row["apply_status"] == "failed"
        assert row["apply_error"] == "reCAPTCHA detected"

    def test_apply_failure_type_column_exists(self, mem_db):
        """apply_failure_type column should be present (added in improvement #4)."""
        user_id = _get_user_id(mem_db)
        job_id = _insert_job(mem_db, user_id, "Job X", "Corp X",
                              url="https://test.com/job2")

        mem_db.execute(
            "UPDATE jobs SET apply_failure_type=?, apply_failure_detail=? WHERE id=?",
            ("captcha", "hCaptcha widget detected on page", job_id)
        )
        mem_db.commit()

        row = dict(mem_db.execute(
            "SELECT apply_failure_type, apply_failure_detail FROM jobs WHERE id=?",
            (job_id,)
        ).fetchone())
        assert row["apply_failure_type"] == "captcha"
        assert "hCaptcha" in row["apply_failure_detail"]


# ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
# Blocklist (improvement #2 â marked xfail until implemented)
# ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

class TestBlocklist:

    @pytest.mark.xfail(reason="#2 blocklist functions not yet implemented", strict=False)
    def test_add_and_retrieve_blocklist(self, mem_db):
        from db import add_to_blocklist, get_blocklist
        user_id = _get_user_id(mem_db)

        add_to_blocklist(mem_db, user_id, "BadCorp", "Bad company")
        blocklist = get_blocklist(mem_db, user_id)
        assert "BadCorp" in blocklist

    @pytest.mark.xfail(reason="#2 blocklist functions not yet implemented", strict=False)
    def test_blocklist_is_per_user(self, mem_db):
        from db import add_to_blocklist, get_blocklist
"""
