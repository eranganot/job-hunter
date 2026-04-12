"""
db.py — Multi-user database layer for Job Hunter
"""
import sqlite3
import json
import os
from datetime import datetime, timedelta

DB_PATH = None  # Injected by app.py at startup


def set_db_path(path: str):
    global DB_PATH
    DB_PATH = path


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT NOT NULL,
            email         TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            salt          TEXT NOT NULL,
            created_date  TEXT DEFAULT (datetime('now')),
            is_active     INTEGER DEFAULT 1,
            role          TEXT DEFAULT 'user'
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            token        TEXT PRIMARY KEY,
            user_id      INTEGER NOT NULL,
            created_date TEXT DEFAULT (datetime('now')),
            expires_date TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_profiles (
            user_id              INTEGER PRIMARY KEY,
            cv_path              TEXT,
            cv_analyzed          INTEGER DEFAULT 0,
            cv_summary           TEXT,
            job_titles           TEXT DEFAULT '[]',
            keywords             TEXT DEFAULT '[]',
            locations            TEXT DEFAULT '["Tel Aviv"]',
            salary_min           INTEGER DEFAULT 0,
            salary_max           INTEGER DEFAULT 0,
            experience_years     INTEGER DEFAULT 0,
            seniority            TEXT DEFAULT '',
            linkedin_url         TEXT DEFAULT '',
            phone                TEXT DEFAULT '',
            notification_channel TEXT DEFAULT 'none',
            telegram_token       TEXT DEFAULT '',
            telegram_chat_id     TEXT DEFAULT '',
            twilio_account_sid   TEXT DEFAULT '',
            twilio_auth_token    TEXT DEFAULT '',
            whatsapp_number      TEXT DEFAULT '',
            email_address        TEXT DEFAULT '',
            email_smtp_host      TEXT DEFAULT 'smtp.gmail.com',
            email_smtp_port      INTEGER DEFAULT 587,
            email_smtp_user      TEXT DEFAULT '',
            email_smtp_pass      TEXT DEFAULT '',
            schedule_frequency   TEXT DEFAULT 'weekly',
            search_hour          INTEGER DEFAULT 11,
            search_day_of_week   INTEGER DEFAULT 1,
            apply_hour           INTEGER DEFAULT 14,
            apply_day_of_week    INTEGER DEFAULT 1,
            onboarding_complete  INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    conn.execute("""
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
            UNIQUE(user_id, url),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    # Migrations — safe to re-run on every start
    for _migration in [
        "ALTER TABLE jobs ADD COLUMN stage TEXT DEFAULT NULL",
        "ALTER TABLE user_profiles ADD COLUMN weekdays_only INTEGER DEFAULT 0",
        "ALTER TABLE jobs ADD COLUMN url_verified INTEGER DEFAULT NULL",
        "ALTER TABLE jobs ADD COLUMN url_check_date TEXT DEFAULT NULL",
        "ALTER TABLE jobs ADD COLUMN apply_status TEXT DEFAULT NULL",
        "ALTER TABLE jobs ADD COLUMN apply_confirmation TEXT DEFAULT NULL",
        "ALTER TABLE jobs ADD COLUMN apply_attempts INTEGER DEFAULT 0",
        "ALTER TABLE jobs ADD COLUMN apply_error TEXT DEFAULT NULL",
        "ALTER TABLE user_profiles ADD COLUMN email_address TEXT DEFAULT ''",
        "ALTER TABLE user_profiles ADD COLUMN email_smtp_host TEXT DEFAULT 'smtp.gmail.com'",
        "ALTER TABLE user_profiles ADD COLUMN email_smtp_port INTEGER DEFAULT 587",
        "ALTER TABLE user_profiles ADD COLUMN email_smtp_user TEXT DEFAULT ''",
        "ALTER TABLE user_profiles ADD COLUMN email_smtp_pass TEXT DEFAULT ''",
        "ALTER TABLE jobs ADD COLUMN publish_date TEXT DEFAULT NULL",
        "ALTER TABLE jobs ADD COLUMN full_description TEXT DEFAULT NULL",
        "ALTER TABLE jobs ADD COLUMN apply_failure_type TEXT DEFAULT NULL",
        "ALTER TABLE jobs ADD COLUMN apply_failure_detail TEXT DEFAULT NULL",
        "CREATE TABLE IF NOT EXISTS user_blocklist (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, company_name TEXT NOT NULL, reason TEXT DEFAULT '', date_added TEXT DEFAULT (datetime('now')), UNIQUE(user_id, company_name))",
        "CREATE TABLE IF NOT EXISTS pass_reason_stats (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, reason TEXT NOT NULL, count INTEGER DEFAULT 1, last_hit_date TEXT DEFAULT (datetime('now')), UNIQUE(user_id, reason))",
        "ALTER TABLE user_profiles ADD COLUMN auto_apply_enabled INTEGER DEFAULT 0",
        "ALTER TABLE user_profiles ADD COLUMN applications_sent_today INTEGER DEFAULT 0",
        "ALTER TABLE user_profiles ADD COLUMN applications_reset_date TEXT DEFAULT NULL",
        "ALTER TABLE user_profiles ADD COLUMN onboarding_progress TEXT DEFAULT '{}'",
        "ALTER TABLE user_profiles ADD COLUMN onboarding_dismissed INTEGER DEFAULT 0",
        "ALTER TABLE jobs ADD COLUMN cover_letter TEXT DEFAULT NULL",
    ]:
        try:
            conn.execute(_migration)
        except Exception:
            pass  # column already exists

    conn.execute("""
        CREATE TABLE IF NOT EXISTS rejected_patterns (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL,
            company      TEXT,
            title        TEXT,
            notes        TEXT,
            created_date TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_blocklist (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL,
            company_name TEXT NOT NULL,
            reason       TEXT DEFAULT '',
            date_added   TEXT DEFAULT (datetime('now')),
            UNIQUE(user_id, company_name)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS pass_reason_stats (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER NOT NULL,
            reason        TEXT NOT NULL,
            count         INTEGER DEFAULT 1,
            last_hit_date TEXT DEFAULT (datetime('now')),
            UNIQUE(user_id, reason)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS activity_log (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL,
            event_type   TEXT NOT NULL,
            details      TEXT DEFAULT '',
            created_date TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    conn.commit()
    _migrate(conn)
    count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()
    print(f"[db] Schema ready — {count} user(s) registered.")


def _migrate(conn: sqlite3.Connection):
    """Safely add new columns to existing databases without breaking anything."""
    additions = [
        ("users",         "role TEXT DEFAULT 'user'"),
        ("user_profiles", "schedule_frequency TEXT DEFAULT 'weekly'"),
        ("user_profiles", "search_day_of_week INTEGER DEFAULT 1"),
        ("user_profiles", "apply_day_of_week INTEGER DEFAULT 1"),
        ("jobs",          "match_score INTEGER DEFAULT NULL"),
        ("jobs",          "candidate_score INTEGER DEFAULT NULL"),
        ("jobs",          "status_check TEXT DEFAULT NULL"),
        ("jobs",          "status_checked_date TEXT DEFAULT NULL"),
    ]
    for table, col_def in additions:
        col_name = col_def.split()[0]
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")
            conn.commit()
            print(f"[db] Migration: added {col_name} to {table}")
        except Exception:
            pass  # Column already exists — that's fine


# ── Activity log ──────────────────────────────────────────────────────────────

def log_activity(user_id: int, event_type: str, details: str = ""):
    """Append an entry to the activity log for a user."""
    try:
        conn = get_db()
        conn.execute(
            "INSERT INTO activity_log (user_id, event_type, details) VALUES (?,?,?)",
            (user_id, event_type, details)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[activity] Log error: {e}")


def get_activity(user_id: int, limit: int = 100):
    """Return recent activity entries for a user, newest first."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM activity_log WHERE user_id=? ORDER BY created_date DESC LIMIT ?",
        (user_id, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Job helpers ───────────────────────────────────────────────────────────────

def expire_old_jobs(conn: sqlite3.Connection, user_id: int):
    cutoff = (datetime.now() - timedelta(days=3)).isoformat()
    conn.execute(
        "UPDATE jobs SET status='expired' WHERE user_id=? AND status='new' AND found_date < ?",
        (user_id, cutoff)
    )
    conn.commit()


def get_stats(conn: sqlite3.Connection, user_id: int) -> dict:
    expire_old_jobs(conn, user_id)
    return {
        "new":      conn.execute("SELECT COUNT(*) FROM jobs WHERE user_id=? AND status='new'",      (user_id,)).fetchone()[0],
        "approved": conn.execute("SELECT COUNT(*) FROM jobs WHERE user_id=? AND status='approved'", (user_id,)).fetchone()[0],
        "applied":  conn.execute("SELECT COUNT(*) FROM jobs WHERE user_id=? AND status='applied'",  (user_id,)).fetchone()[0],
        "rejected": conn.execute("SELECT COUNT(*) FROM jobs WHERE user_id=? AND status='rejected'", (user_id,)).fetchone()[0],
        "total":    conn.execute("SELECT COUNT(*) FROM jobs WHERE user_id=?",                       (user_id,)).fetchone()[0],
    }


def write_approved_jobs(base_dir: str):
    """Write approved_jobs.json so the apply scheduled task can pick them up."""
    conn = get_db()
    rows = conn.execute("SELECT * FROM jobs WHERE status='approved'").fetchall()
    conn.close()
    path = os.path.join(base_dir, "approved_jobs.json")
    with open(path, "w") as f:
        json.dump([dict(r) for r in rows], f, indent=2, default=str)


def import_pending_jobs(base_dir: str):
    path = os.path.join(base_dir, "pending_jobs.json")
    if not os.path.exists(path):
        return
    try:
        with open(path) as f:
            jobs = json.load(f)
        conn = get_db()
        inserted = 0
        for j in jobs:
            user_id = j.get("user_id", 1)  # default to first user if not specified
            conn.execute("""
                INSERT OR IGNORE INTO jobs
                  (user_id,title,company,location,url,description,
                   why_relevant,company_info,source,found_date,status)
                VALUES (?,?,?,?,?,?,?,?,?,?,'new')
            """, (
                user_id, j.get("title",""), j.get("company",""),
                j.get("location","Tel Aviv"), j.get("url",""),
                j.get("description",""), j.get("why_relevant",""),
                j.get("company_info",""), j.get("source",""),
                j.get("found_date", datetime.now().isoformat())
            ))
            if conn.execute("SELECT changes()").fetchone()[0] > 0:
                inserted += 1
        conn.commit()
        conn.close()
        os.remove(path)
        if inserted > 0:
            for uid in set(j.get("user_id", 1) for j in jobs):
                cnt = sum(1 for j in jobs if j.get("user_id", 1) == uid)
                log_activity(uid, "jobs_searched", f"Found {cnt} new job(s)")
        print(f"[import] {inserted} new jobs imported from pending_jobs.json")
    except Exception as e:
        print(f"[import] Error: {e}")


def import_applied_updates(base_dir: str):
    path = os.path.join(base_dir, "applied_updates.json")
    if not os.path.exists(path):
        return
    try:
        with open(path) as f:
            updates = json.load(f)
        conn = get_db()
        for u in updates:
            conn.execute(
                "UPDATE jobs SET status=?, applied_date=?, notes=? WHERE id=?",
                (u.get("status","applied"), u.get("applied_date"),
                 u.get("notes",""), u["id"])
            )
        conn.commit()
        conn.close()
        os.remove(path)
        print(f"[import] {len(updates)} job statuses updated")
    except Exception as e:
        print(f"[import] Error applied updates: {e}")


def write_users_config(base_dir: str):
    """Write users_config.json so scheduled tasks can read per-user preferences."""
    conn = get_db()
    rows = conn.execute("""
        SELECT u.id, u.name, u.email,
               p.job_titles, p.keywords, p.locations,
               p.salary_min, p.linkedin_url, p.phone,
               p.search_hour, p.apply_hour,
               p.notification_channel,
               p.telegram_token, p.telegram_chat_id,
               p.twilio_account_sid, p.twilio_auth_token, p.whatsapp_number
        FROM users u
        JOIN user_profiles p ON p.user_id = u.id
        WHERE u.is_active=1 AND p.onboarding_complete=1
    """).fetchall()
    conn.close()
    users = []
    for r in rows:
        d = dict(r)
        for key in ("job_titles", "keywords", "locations"):
            try:
                d[key] = json.loads(d[key] or "[]")
            except Exception:
                d[key] = []
        users.append(d)
    path = os.path.join(base_dir, "users_config.json")
    with open(path, "w") as f:
        json.dump(users, f, indent=2, default=str)

# ── Pass-reason feedback loop (improvement #2) ──────────────────────────────

def add_to_blocklist(conn: sqlite3.Connection, user_id: int, company_name: str, reason: str = "") -> None:
    """Add a company to the user's blocklist (idempotent — ignores duplicates)."""
    conn.execute(
        "INSERT OR IGNORE INTO user_blocklist (user_id, company_name, reason) VALUES (?, ?, ?)",
        (user_id, company_name, reason or "")
    )
    conn.commit()


def get_blocklist(conn: sqlite3.Connection, user_id: int) -> list:
    """Return list of company names the user has blocklisted."""
    rows = conn.execute(
        "SELECT company_name FROM user_blocklist WHERE user_id=? ORDER BY date_added DESC",
        (user_id,)
    ).fetchall()
    return [row[0] for row in rows]


def record_pass_reason_stat(conn: sqlite3.Connection, user_id: int, reason: str) -> None:
    """Increment (or insert) the pass-reason count for a user."""
    from datetime import datetime
    conn.execute(
        """INSERT INTO pass_reason_stats (user_id, reason, count, last_hit_date)
           VALUES (?, ?, 1, ?)
           ON CONFLICT(user_id, reason) DO UPDATE SET
               count = count + 1,
               last_hit_date = excluded.last_hit_date""",
        (user_id, reason, datetime.now().isoformat())
    )
    conn.commit()


def get_pass_reason_stats(conn: sqlite3.Connection, user_id: int) -> dict:
    """Return {reason: count} dict for the given user, sorted by count desc."""
    rows = conn.execute(
        "SELECT reason, count FROM pass_reason_stats WHERE user_id=? ORDER BY count DESC",
        (user_id,)
    ).fetchall()
    return {row[0]: row[1] for row in rows}
