"""
tests/test_db.py
Unit tests for db.py — schema, core queries, and planned future functions.
"""
import json
import sqlite3
import pytest
from unittest.mock import patch




# ── Minimal in-memory DB fixture ──────────────────────────────────────────────

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
        CREATE TABLE IF NOT EXISTS rejected_patterns (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL,
            company      TEXT,
            title        TEXT,
            notes        TEXT,
            created_date TEXT DEFAULT (datetime('now'))
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


# ══════════════════════════════════════════════════════════════════════════════
# Activity log
# ══════════════════════════════════════════════════════════════════════════════

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


# ══════════════════════════════════════════════════════════════════════════════
# get_stats
# ══════════════════════════════════════════════════════════════════════════════

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


# ══════════════════════════════════════════════════════════════════════════════
# expire_old_jobs
# ══════════════════════════════════════════════════════════════════════════════

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


# ══════════════════════════════════════════════════════════════════════════════
# Job status update (apply_status / apply_error)
# ══════════════════════════════════════════════════════════════════════════════

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


# ══════════════════════════════════════════════════════════════════════════════
# Blocklist
# ══════════════════════════════════════════════════════════════════════════════

class TestBlocklist:

    def test_add_and_retrieve_blocklist(self, mem_db):
        from db import add_to_blocklist, get_blocklist
        user_id = _get_user_id(mem_db)

        add_to_blocklist(mem_db, user_id, "BadCorp", "Bad company")
        blocklist = get_blocklist(mem_db, user_id)
        assert "BadCorp" in blocklist

    def test_blocklist_is_per_user(self, mem_db):
        from db import add_to_blocklist, get_blocklist

        user1_id = _get_user_id(mem_db)
        # Add a second user
        mem_db.execute(
            "INSERT INTO users (name, email, password_hash, salt) VALUES (?,?,?,?)",
            ("Other User", "other@example.com", "hash", "salt")
        )
        mem_db.commit()
        user2_id = mem_db.execute(
            "SELECT id FROM users WHERE email='other@example.com'"
        ).fetchone()[0]

        add_to_blocklist(mem_db, user1_id, "BadCorp", "user1 dislikes")
        add_to_blocklist(mem_db, user2_id, "OtherCorp", "user2 dislikes")

        u1_list = get_blocklist(mem_db, user1_id)
        u2_list = get_blocklist(mem_db, user2_id)
        assert "BadCorp" in u1_list and "OtherCorp" not in u1_list
        assert "OtherCorp" in u2_list and "BadCorp" not in u2_list

    def test_add_to_blocklist_is_idempotent(self, mem_db):
        from db import add_to_blocklist, get_blocklist
        user_id = _get_user_id(mem_db)

        add_to_blocklist(mem_db, user_id, "DupCorp", "first add")
        add_to_blocklist(mem_db, user_id, "DupCorp", "second add")
        blocklist = get_blocklist(mem_db, user_id)
        # UNIQUE(user_id, company_name) means duplicates are ignored
        assert blocklist.count("DupCorp") == 1


# ══════════════════════════════════════════════════════════════════════════════
# Passed-history cleanup (counter preserves 'total' across deletions)
# ══════════════════════════════════════════════════════════════════════════════

class TestCleanupPassedJobs:

    def _ensure_profile(self, conn, user_id):
        """Ensure a user_profiles row + passed_archived_count column exist."""
        conn.execute(
            "INSERT OR IGNORE INTO user_profiles (user_id) VALUES (?)",
            (user_id,)
        )
        try:
            conn.execute(
                "ALTER TABLE user_profiles ADD COLUMN passed_archived_count INTEGER DEFAULT 0"
            )
        except Exception:
            pass  # column already exists
        conn.commit()

    def test_deletes_old_passed_only(self, mem_db):
        from db import cleanup_passed_jobs
        user_id = _get_user_id(mem_db)
        self._ensure_profile(mem_db, user_id)

        # 2 old passed (should delete), 1 fresh passed (should keep), 1 fresh new (untouched)
        mem_db.execute(
            "INSERT INTO jobs (user_id, title, company, url, status, found_date) "
            "VALUES (?, 'Old1', 'C1', 'u1', 'rejected', datetime('now', '-45 days'))",
            (user_id,))
        mem_db.execute(
            "INSERT INTO jobs (user_id, title, company, url, status, found_date) "
            "VALUES (?, 'Old2', 'C2', 'u2', 'rejected', datetime('now', '-60 days'))",
            (user_id,))
        mem_db.execute(
            "INSERT INTO jobs (user_id, title, company, url, status, found_date) "
            "VALUES (?, 'Fresh', 'C3', 'u3', 'rejected', datetime('now', '-5 days'))",
            (user_id,))
        mem_db.execute(
            "INSERT INTO jobs (user_id, title, company, url, status, found_date) "
            "VALUES (?, 'NewJob', 'C4', 'u4', 'new', datetime('now', '-90 days'))",
            (user_id,))
        mem_db.commit()

        deleted = cleanup_passed_jobs(mem_db, user_id=user_id, days=30)
        assert deleted == 2

        # The fresh passed + the 'new' job survive
        rows = mem_db.execute(
            "SELECT title FROM jobs WHERE user_id=? ORDER BY title", (user_id,)
        ).fetchall()
        titles = [r["title"] for r in rows]
        assert "Fresh" in titles
        assert "NewJob" in titles
        assert "Old1" not in titles and "Old2" not in titles

    def test_counter_preserves_total_stat(self, mem_db):
        from db import cleanup_passed_jobs, get_stats
        user_id = _get_user_id(mem_db)
        self._ensure_profile(mem_db, user_id)

        # Insert 3 old passed jobs and 1 fresh approved job
        for i, days in enumerate([40, 50, 60]):
            mem_db.execute(
                "INSERT INTO jobs (user_id, title, company, url, status, found_date) "
                f"VALUES (?, 'P{i}', 'C{i}', 'pu{i}', 'rejected', datetime('now', '-{days} days'))",
                (user_id,))
        mem_db.execute(
            "INSERT INTO jobs (user_id, title, company, url, status, found_date) "
            "VALUES (?, 'A1', 'CA', 'au1', 'approved', datetime('now', '-2 days'))",
            (user_id,))
        mem_db.commit()

        total_before = get_stats(mem_db, user_id)["total"]
        rejected_before = get_stats(mem_db, user_id)["rejected"]

        deleted = cleanup_passed_jobs(mem_db, user_id=user_id, days=30)
        assert deleted == 3

        stats_after = get_stats(mem_db, user_id)
        # The whole point: total and rejected don't drop after cleanup
        assert stats_after["total"] == total_before
        assert stats_after["rejected"] == rejected_before
        # And the counter was bumped exactly by the deleted count
        counter = mem_db.execute(
            "SELECT passed_archived_count FROM user_profiles WHERE user_id=?",
            (user_id,)
        ).fetchone()[0]
        assert counter == 3

    def test_noop_when_nothing_old_enough(self, mem_db):
        from db import cleanup_passed_jobs
        user_id = _get_user_id(mem_db)
        self._ensure_profile(mem_db, user_id)

        mem_db.execute(
            "INSERT INTO jobs (user_id, title, company, url, status, found_date) "
            "VALUES (?, 'Recent', 'C', 'u', 'rejected', datetime('now', '-5 days'))",
            (user_id,))
        mem_db.commit()
        deleted = cleanup_passed_jobs(mem_db, user_id=user_id, days=30)
        assert deleted == 0

    def test_learning_tables_untouched(self, mem_db):
        """rejected_patterns + pass_reason_stats survive job-row deletion."""
        from db import cleanup_passed_jobs
        user_id = _get_user_id(mem_db)
        self._ensure_profile(mem_db, user_id)

        # Old passed job
        mem_db.execute(
            "INSERT INTO jobs (user_id, title, company, url, status, found_date) "
            "VALUES (?, 'OldPM', 'BadCo', 'urlx', 'rejected', datetime('now', '-45 days'))",
            (user_id,))
        # Associated learning rows in separate tables
        mem_db.execute(
            "INSERT INTO rejected_patterns (user_id, company, title, notes) "
            "VALUES (?, 'BadCo', 'OldPM', 'not relevant')",
            (user_id,))
        mem_db.execute(
            "INSERT INTO pass_reason_stats (user_id, reason, count) VALUES (?, 'Wrong seniority level', 5)",
            (user_id,))
        mem_db.commit()

        cleanup_passed_jobs(mem_db, user_id=user_id, days=30)

        # Job row is gone
        assert mem_db.execute("SELECT COUNT(*) FROM jobs WHERE url='urlx'").fetchone()[0] == 0
        # But the learning survives
        assert mem_db.execute(
            "SELECT COUNT(*) FROM rejected_patterns WHERE user_id=? AND company='BadCo'",
            (user_id,)
        ).fetchone()[0] == 1
        assert mem_db.execute(
            "SELECT count FROM pass_reason_stats WHERE user_id=? AND reason='Wrong seniority level'",
            (user_id,)
        ).fetchone()[0] == 5
