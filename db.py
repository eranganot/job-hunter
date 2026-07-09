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
    # timeout=30 — Python-level lock wait (some platforms ignore busy_timeout
    # otherwise). Pair it with PRAGMA busy_timeout for belt-and-suspenders.
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    # Autocommit (isolation_level=None): every write commits immediately and
    # releases the SQLite writer lock, so a leaked/abandoned connection can
    # never hold the lock open and starve other writers (the root cause of
    # persistent "database is locked"). Explicit conn.commit() calls become
    # harmless no-ops; readers are unaffected (WAL).
    conn.isolation_level = None
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode=WAL")
    # When multiple threads (search, apply, activity-log) try to write at the
    # same time, the default behaviour is to raise 'database is locked'
    # immediately. busy_timeout makes writers wait up to N ms for the lock to
    # clear instead — eliminates the spurious lock errors seen in prod logs
    # (2026-05-27) without changing semantics.
    conn.execute("PRAGMA busy_timeout = 30000")    # 30s — writers wait instead of failing (fixes removes dropped under heavy search)
    # NORMAL is safe under WAL and noticeably faster than the default FULL;
    # the only thing it relaxes is fsync on every commit (durability across a
    # power cut, which Railway's host already protects against).
    conn.execute("PRAGMA synchronous = NORMAL")
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
        "ALTER TABLE user_profiles ADD COLUMN cv_optimizer_result TEXT DEFAULT NULL",
        "ALTER TABLE user_profiles ADD COLUMN cv_optimizer_date TEXT DEFAULT NULL",
        "ALTER TABLE user_profiles ADD COLUMN cv_filename TEXT DEFAULT NULL",
        "ALTER TABLE user_profiles ADD COLUMN cv_uploaded_date TEXT DEFAULT NULL",
        # ── Google Sign-In (OAuth) ──
        "ALTER TABLE users ADD COLUMN google_sub TEXT DEFAULT NULL",
        "ALTER TABLE users ADD COLUMN auth_provider TEXT DEFAULT 'password'",
        "ALTER TABLE users ADD COLUMN avatar_url TEXT DEFAULT NULL",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_google_sub ON users(google_sub) WHERE google_sub IS NOT NULL",
        # ── Phase-1 robustness migrations ─────────────────────────────────────
        "ALTER TABLE jobs ADD COLUMN apply_strategy TEXT DEFAULT NULL",
        "ALTER TABLE jobs ADD COLUMN apply_next_attempt_at TEXT DEFAULT NULL",
        "ALTER TABLE jobs ADD COLUMN apply_evidence_path TEXT DEFAULT NULL",
        "ALTER TABLE jobs ADD COLUMN apply_resolved_url TEXT DEFAULT NULL",
        "ALTER TABLE jobs ADD COLUMN apply_submitted_at TEXT DEFAULT NULL",
        "ALTER TABLE user_profiles ADD COLUMN applications_per_run INTEGER DEFAULT 10",
        # ── Passed-history cleanup (preserves historical 'total' across deletions) ──
        "ALTER TABLE user_profiles ADD COLUMN passed_archived_count INTEGER DEFAULT 0",
        # ── Feedback learning loop: per-job penalty derived from pass history ──
        "ALTER TABLE jobs ADD COLUMN feedback_penalty INTEGER DEFAULT 0",
        "ALTER TABLE jobs ADD COLUMN feedback_reason TEXT DEFAULT ''",
        # career_url_cache: created separately below (CREATE TABLE IF NOT EXISTS)
        "CREATE TABLE IF NOT EXISTS career_url_cache (id INTEGER PRIMARY KEY AUTOINCREMENT, company TEXT NOT NULL, job_title TEXT NOT NULL, resolved_url TEXT NOT NULL, created_date TEXT DEFAULT (datetime(\'now\')), UNIQUE(company, job_title))",
        # application_answers: canonical applicant profile (§4.6)
        """CREATE TABLE IF NOT EXISTS application_answers (
  user_id INTEGER PRIMARY KEY,
  first_name TEXT, last_name TEXT, preferred_name TEXT,
  phone_country_code TEXT, phone TEXT, email TEXT,
  city TEXT, state_region TEXT, country TEXT, postal_code TEXT, address_line TEXT,
  work_auth_il INTEGER, work_auth_us INTEGER, work_auth_eu INTEGER,
  visa_required INTEGER, willing_to_relocate INTEGER,
  current_title TEXT, current_company TEXT, years_experience INTEGER,
  salary_expectation_min INTEGER, salary_expectation_currency TEXT,
  notice_period_days INTEGER, available_start_date TEXT,
  linkedin_url TEXT, github_url TEXT, portfolio_url TEXT, twitter_url TEXT,
  eeo_race TEXT, eeo_gender TEXT, eeo_veteran TEXT, eeo_disability TEXT,
  how_heard TEXT DEFAULT \'Company website\',
  cover_letter_default TEXT, why_company_default TEXT,
  updated_date TEXT,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
)"""
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

    conn.execute("""
        CREATE TABLE IF NOT EXISTS push_subscriptions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL,
            endpoint     TEXT NOT NULL,
            subscription TEXT NOT NULL,
            created_date TEXT DEFAULT (datetime('now')),
            UNIQUE(user_id, endpoint),
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
        ("rejected_patterns", "location TEXT DEFAULT NULL"),
    ]
    applied = 0
    for table, col_def in additions:
        col_name = col_def.split()[0]
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")
            conn.commit()
            applied += 1
            print(f"[db] Migration: added {col_name} to {table}")
        except Exception as _me:
            # "duplicate column" is the normal idempotent case; surface anything else.
            if "duplicate column" not in str(_me).lower():
                print(f"[db] Migration WARNING ({table}.{col_name}): {_me}")
    # Stamp a simple schema version for visibility and future versioned migrations.
    try:
        conn.execute(f"PRAGMA user_version = {len(additions)}")
        conn.commit()
    except Exception as _ve:
        print(f"[db] schema-version stamp failed: {_ve}")

    # Backfill: approved jobs with no apply_status get 'queued' so the scheduler picks them up
    try:
        result = conn.execute(
            "UPDATE jobs SET apply_status=\'queued\' WHERE status=\'approved\' AND apply_status IS NULL"
        )
        if result.rowcount > 0:
            conn.commit()
            print(f"[db] Backfill: {result.rowcount} approved job(s) set to apply_status=\'queued\'")
    except Exception as e:
        print(f"[db] Backfill error: {e}")


# ── Activity log ──────────────────────────────────────────────────────────────

def log_activity(user_id: int, event_type: str, details: str = ""):
    """Append an entry to the activity log for a user.

    Retries up to 3 times on 'database is locked' since activity-log writes
    are the highest-frequency writers and the most likely to lose a race
    against a long-running search transaction (PRAGMA busy_timeout=10000
    already covers most cases, this is belt-and-suspenders).
    """
    import time as _t
    import sqlite3 as _sq
    for _attempt in range(3):
        try:
            conn = get_db()
            try:
                conn.execute(
                    "INSERT INTO activity_log (user_id, event_type, details) VALUES (?,?,?)",
                    (user_id, event_type, details)
                )
                conn.commit()
                return
            finally:
                conn.close()
        except _sq.OperationalError as e:
            if "database is locked" in str(e).lower() and _attempt < 2:
                _t.sleep(0.5 * (2 ** _attempt))   # 0.5s, 1.0s
                continue
            print(f"[activity] Log error: {e}")
            return
        except Exception as e:
            print(f"[activity] Log error: {e}")
            return


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
    """Move stale 'new' jobs to 'expired'. Lock-tolerant: this is called from
    every /api/stats poll, so if another writer is holding the write lock
    we silently skip rather than 500-ing the dashboard. The next stats poll
    will retry the cleanup naturally.
    """
    cutoff = (datetime.now() - timedelta(days=3)).isoformat()
    try:
        conn.execute(
            "UPDATE jobs SET status='expired' WHERE user_id=? AND status='new' AND found_date < ?",
            (user_id, cutoff)
        )
        conn.commit()
    except sqlite3.OperationalError as e:
        if "database is locked" in str(e).lower():
            # Non-critical cleanup — skip this round, next /api/stats will retry
            print(f"[db] expire_old_jobs: lock contended, skipping for now")
            return
        raise


def get_stats(conn: sqlite3.Connection, user_id: int) -> dict:
    expire_old_jobs(conn, user_id)
    # Historical counter so cleanup of old 'passed' (rejected) rows doesn't shrink
    # the user-visible 'total'. Column is created by the migration in init_db().
    try:
        archived = conn.execute(
            "SELECT COALESCE(passed_archived_count, 0) FROM user_profiles WHERE user_id=?",
            (user_id,)
        ).fetchone()
        archived_count = archived[0] if archived else 0
    except Exception:
        archived_count = 0
    return {
        "new":      conn.execute("SELECT COUNT(*) FROM jobs WHERE user_id=? AND status='new'",      (user_id,)).fetchone()[0],
        "approved": conn.execute("SELECT COUNT(*) FROM jobs WHERE user_id=? AND status='approved'", (user_id,)).fetchone()[0],
        "applied":  conn.execute("SELECT COUNT(*) FROM jobs WHERE user_id=? AND status='applied'",  (user_id,)).fetchone()[0],
        "deferred": conn.execute("SELECT COUNT(*) FROM jobs WHERE user_id=? AND status='deferred'", (user_id,)).fetchone()[0],
        "rejected": conn.execute("SELECT COUNT(*) FROM jobs WHERE user_id=? AND status='rejected'", (user_id,)).fetchone()[0] + archived_count,
        "total":    conn.execute("SELECT COUNT(*) FROM jobs WHERE user_id=?",                       (user_id,)).fetchone()[0] + archived_count,
    }


def cleanup_passed_jobs(conn: sqlite3.Connection, user_id: int = None, days: int = 30) -> int:
    """Delete passed jobs (status='rejected') older than `days`, incrementing
    user_profiles.passed_archived_count by the deleted row count per user so the
    'total' stat stays truthful.

    The 'learning' (rejected_patterns + pass_reason_stats) lives in separate
    tables and is unaffected by this deletion.

    Args:
        conn:    open sqlite3 connection.
        user_id: target user, or None to clean for all users.
        days:    age threshold in days (default 30).

    Returns:
        Total number of rows deleted across all targeted users.
    """
    days = int(days or 30)
    cutoff_expr = f"date('now', '-{days} days')"
    total_deleted = 0
    try:
        if user_id is not None:
            user_ids = [int(user_id)]
        else:
            user_ids = [r[0] for r in conn.execute(
                "SELECT DISTINCT user_id FROM jobs WHERE status='rejected'"
            ).fetchall()]
        for uid in user_ids:
            cnt_row = conn.execute(
                f"SELECT COUNT(*) FROM jobs WHERE user_id=? AND status='rejected' "
                f"AND found_date IS NOT NULL AND found_date < {cutoff_expr}",
                (uid,)
            ).fetchone()
            cnt = cnt_row[0] if cnt_row else 0
            if cnt <= 0:
                continue
            conn.execute(
                "UPDATE user_profiles SET passed_archived_count = COALESCE(passed_archived_count, 0) + ? "
                "WHERE user_id=?",
                (cnt, uid)
            )
            conn.execute(
                f"DELETE FROM jobs WHERE user_id=? AND status='rejected' "
                f"AND found_date IS NOT NULL AND found_date < {cutoff_expr}",
                (uid,)
            )
            total_deleted += cnt
        conn.commit()
    except Exception as e:
        print(f"[db] cleanup_passed_jobs error: {e}")
    return total_deleted


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


def remove_from_blocklist(conn: sqlite3.Connection, user_id: int, company_name: str) -> None:
    """Remove a company from the user's blocklist."""
    conn.execute(
        "DELETE FROM user_blocklist WHERE user_id=? AND company_name=?",
        (user_id, company_name)
    )
    conn.commit()


def record_pass_reason_stat(conn: sqlite3.Connection, user_id: int, reason: str) -> None:
    """Increment (or insert) the pass-reason count for a user.

    WARNING: This helper calls `conn.commit()`. Do NOT call it from inside an
    outer transaction on the same connection — it will prematurely commit
    your other pending writes, causing partial-state bugs. If you need this
    inside another transaction, inline the SQL (see app.py approve/reject).
    """
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


def get_feedback_signals(conn: sqlite3.Connection, user_id: int) -> dict:
    """Aggregate a user's pass/reject history into structured learning signals.

    This is the read side of the feedback learning loop. It is deterministic and
    cheap (two small SELECTs), so it is safe to call on every job-list render.

    Returns a dict:
      bad_companies          set[str]      - companies passed with a "Bad company" reason (lowercased)
      passed_companies       dict[str,int] - every passed company -> times passed (lowercased)
      disliked_title_tokens  set[str]      - title words (len>3) seen in >=2 distinct passed roles
      reason_counts          dict[str,int] - pass_reason_stats {reason: count}
      examples               list[dict]    - up to 12 recent {company,title,reason} for AI context
    """
    import re as _re
    from datetime import datetime as _dt

    rows = conn.execute(
        "SELECT company, title, notes, location, created_date FROM rejected_patterns "
        "WHERE user_id=? ORDER BY created_date DESC",
        (user_id,)
    ).fetchall()

    # Recency decay: a pass loses half its weight every ~90 days so stale
    # signals fade instead of penalizing forever (#18).
    _now = _dt.now()
    def _weight(created):
        try:
            age = max(0.0, (_now - _dt.fromisoformat((created or "").replace(" ", "T"))).total_seconds() / 86400.0)
        except Exception:
            age = 0.0
        return 0.5 ** (age / 90.0)

    bad_companies: set = set()
    passed_companies: dict = {}
    tok_weight: dict = {}
    loc_weight: dict = {}
    examples: list = []

    for r in rows:
        comp = (r["company"] or "").strip().lower()
        ttl  = (r["title"] or "").strip().lower()
        note = (r["notes"] or "").strip()
        try:
            loc = (r["location"] or "").strip().lower()
        except Exception:
            loc = ""
        try:
            created = r["created_date"]
        except Exception:
            created = None
        w = _weight(created)
        if comp:
            passed_companies[comp] = passed_companies.get(comp, 0.0) + w
            if "bad company" in note.lower():
                bad_companies.add(comp)
        if ttl:
            for tk in {x for x in _re.split(r"[^a-z0-9]+", ttl) if len(x) > 3}:
                tok_weight[tk] = tok_weight.get(tk, 0.0) + w
        # Location becomes a dislike signal only when the user passed *for a
        # location reason* -- never penalize a city just because it appeared (#17).
        if loc and "location" in note.lower():
            loc_weight[loc] = loc_weight.get(loc, 0.0) + w
        if len(examples) < 12:
            examples.append({
                "company": r["company"] or "",
                "title":   r["title"] or "",
                "reason":  note,
            })

    disliked_title_tokens = {t for t, wv in tok_weight.items() if wv >= 1.5}
    disliked_locations = {l for l, wv in loc_weight.items() if wv >= 1.0}

    return {
        "bad_companies":         bad_companies,
        "passed_companies":      passed_companies,
        "disliked_title_tokens": disliked_title_tokens,
        "disliked_locations":    disliked_locations,
        "reason_counts":         get_pass_reason_stats(conn, user_id),
        "examples":              examples,
    }
