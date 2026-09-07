"""
migrations.py - ordered, recorded, idempotent schema migrations.

Replaces the old db._migrate() try/except ALTER TABLE list. Three differences
that matter:

  * ordered    - migrations run in version order, not list-definition order
  * recorded   - schema_migrations says what ran and when, so a boot no longer
                 re-attempts every historical ALTER and swallows the errors
  * idempotent - each migration checks before it acts, so running the whole set
                 against an existing production database is a no-op

Every migration takes an open connection. The DDL in m0001 is the schema
db.init_db() has always created - moved here verbatim, not rewritten.

Phase 2b adds the Postgres dialect; the two introspection helpers below are the
only place that needs to know which engine it is talking to.
"""

SCHEMA_MIGRATIONS_DDL = """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        version    INTEGER PRIMARY KEY,
        name       TEXT NOT NULL,
        applied_at TEXT DEFAULT (datetime('now'))
    )
"""



# Column additions carried over from the original init_db() migration list.
# (table, column definition) - applied only when the column is absent.
_BASELINE_ADDITIONS = [
    ('jobs', 'stage TEXT DEFAULT NULL'),
    ('user_profiles', 'weekdays_only INTEGER DEFAULT 0'),
    ('jobs', 'url_verified INTEGER DEFAULT NULL'),
    ('jobs', 'url_check_date TEXT DEFAULT NULL'),
    ('jobs', 'apply_status TEXT DEFAULT NULL'),
    ('jobs', 'apply_confirmation TEXT DEFAULT NULL'),
    ('jobs', 'apply_attempts INTEGER DEFAULT 0'),
    ('jobs', 'apply_error TEXT DEFAULT NULL'),
    ('user_profiles', "email_address TEXT DEFAULT ''"),
    ('user_profiles', "email_smtp_host TEXT DEFAULT 'smtp.gmail.com'"),
    ('user_profiles', 'email_smtp_port INTEGER DEFAULT 587'),
    ('user_profiles', "email_smtp_user TEXT DEFAULT ''"),
    ('user_profiles', "email_smtp_pass TEXT DEFAULT ''"),
    ('jobs', 'publish_date TEXT DEFAULT NULL'),
    ('jobs', 'full_description TEXT DEFAULT NULL'),
    ('jobs', 'apply_failure_type TEXT DEFAULT NULL'),
    ('jobs', 'apply_failure_detail TEXT DEFAULT NULL'),
    ('user_profiles', 'auto_apply_enabled INTEGER DEFAULT 0'),
    ('user_profiles', 'applications_sent_today INTEGER DEFAULT 0'),
    ('user_profiles', 'applications_reset_date TEXT DEFAULT NULL'),
    ('user_profiles', "onboarding_progress TEXT DEFAULT '{}'"),
    ('user_profiles', 'onboarding_dismissed INTEGER DEFAULT 0'),
    ('jobs', 'cover_letter TEXT DEFAULT NULL'),
    ('user_profiles', 'cv_optimizer_result TEXT DEFAULT NULL'),
    ('user_profiles', 'cv_optimizer_date TEXT DEFAULT NULL'),
    ('user_profiles', 'cv_filename TEXT DEFAULT NULL'),
    ('user_profiles', 'cv_uploaded_date TEXT DEFAULT NULL'),
    ('users', 'google_sub TEXT DEFAULT NULL'),
    ('users', "auth_provider TEXT DEFAULT 'password'"),
    ('users', 'avatar_url TEXT DEFAULT NULL'),
    ('jobs', 'apply_strategy TEXT DEFAULT NULL'),
    ('jobs', 'apply_next_attempt_at TEXT DEFAULT NULL'),
    ('jobs', 'apply_evidence_path TEXT DEFAULT NULL'),
    ('jobs', 'apply_resolved_url TEXT DEFAULT NULL'),
    ('jobs', 'apply_submitted_at TEXT DEFAULT NULL'),
    ('user_profiles', 'applications_per_run INTEGER DEFAULT 10'),
    ('user_profiles', 'passed_archived_count INTEGER DEFAULT 0'),
    ('jobs', 'feedback_penalty INTEGER DEFAULT 0'),
    ('jobs', "feedback_reason TEXT DEFAULT ''"),
]

# Non-ALTER statements from that same list. Every one is IF NOT EXISTS,
# so they are safe to execute unconditionally on both engines.
_BASELINE_EXTRA_DDL = [
    "CREATE TABLE IF NOT EXISTS user_blocklist (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, company_name TEXT NOT NULL, reason TEXT DEFAULT '', date_added TEXT DEFAULT (datetime('now')), UNIQUE(user_id, company_name))",
    "CREATE TABLE IF NOT EXISTS pass_reason_stats (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, reason TEXT NOT NULL, count INTEGER DEFAULT 1, last_hit_date TEXT DEFAULT (datetime('now')), UNIQUE(user_id, reason))",
    'CREATE UNIQUE INDEX IF NOT EXISTS idx_users_google_sub ON users(google_sub) WHERE google_sub IS NOT NULL',
    "CREATE TABLE IF NOT EXISTS career_url_cache (id INTEGER PRIMARY KEY AUTOINCREMENT, company TEXT NOT NULL, job_title TEXT NOT NULL, resolved_url TEXT NOT NULL, created_date TEXT DEFAULT (datetime('now')), UNIQUE(company, job_title))",
    "CREATE TABLE IF NOT EXISTS application_answers (\n  user_id INTEGER PRIMARY KEY,\n  first_name TEXT, last_name TEXT, preferred_name TEXT,\n  phone_country_code TEXT, phone TEXT, email TEXT,\n  city TEXT, state_region TEXT, country TEXT, postal_code TEXT, address_line TEXT,\n  work_auth_il INTEGER, work_auth_us INTEGER, work_auth_eu INTEGER,\n  visa_required INTEGER, willing_to_relocate INTEGER,\n  current_title TEXT, current_company TEXT, years_experience INTEGER,\n  salary_expectation_min INTEGER, salary_expectation_currency TEXT,\n  notice_period_days INTEGER, available_start_date TEXT,\n  linkedin_url TEXT, github_url TEXT, portfolio_url TEXT, twitter_url TEXT,\n  eeo_race TEXT, eeo_gender TEXT, eeo_veteran TEXT, eeo_disability TEXT,\n  how_heard TEXT DEFAULT 'Company website',\n  cover_letter_default TEXT, why_company_default TEXT,\n  updated_date TEXT,\n  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE\n)",
]


def _has_column(conn, table, column):
    """True if table.column exists. SQLite path; Phase 2b adds information_schema."""
    try:
        return any(r[1] == column for r in conn.execute("PRAGMA table_info(" + table + ")"))
    except Exception:
        return False


def _table_exists(conn, table):
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        return row is not None
    except Exception:
        return False


def m0001_baseline(conn):
    """The full schema. Every statement is CREATE TABLE IF NOT EXISTS."""

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
    # Columns and tables added after the original schema shipped.
    #
    # This used to be `try: conn.execute(...) except: pass`, which hid every
    # failure. That is survivable on SQLite; on Postgres a failed statement
    # aborts the whole transaction, so the first already-applied ALTER would
    # take every statement after it down with it. Check first, then act.
    for _table, _coldef in _BASELINE_ADDITIONS:
        if not _table_exists(conn, _table):
            continue
        if _has_column(conn, _table, _coldef.split()[0]):
            continue
        conn.execute("ALTER TABLE " + _table + " ADD COLUMN " + _coldef)
        print("[db] baseline: added " + _coldef.split()[0] + " to " + _table)
    for _stmt in _BASELINE_EXTRA_DDL:
        conn.execute(_stmt)

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


def m0002_column_additions(conn):
    """Columns added to tables that already existed in earlier deployments."""
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
    for table, col_def in additions:
        col_name = col_def.split()[0]
        if not _table_exists(conn, table):
            continue
        if _has_column(conn, table, col_name):
            continue
        conn.execute("ALTER TABLE " + table + " ADD COLUMN " + col_def)
        print("[db] migration: added " + col_name + " to " + table)
    conn.commit()


def m0003_backfill_queued_apply_status(conn):
    """Approved jobs with no apply_status get 'queued' so the scheduler sees them."""
    if not _table_exists(conn, "jobs"):
        return
    result = conn.execute(
        "UPDATE jobs SET apply_status='queued' "
        "WHERE status='approved' AND apply_status IS NULL"
    )
    if result.rowcount and result.rowcount > 0:
        print("[db] backfill: " + str(result.rowcount) + " approved job(s) -> apply_status='queued'")
    conn.commit()


MIGRATIONS = [
    (1, "baseline_schema",              m0001_baseline),
    (2, "column_additions",             m0002_column_additions),
    (3, "backfill_queued_apply_status", m0003_backfill_queued_apply_status),
]


def applied_versions(conn):
    conn.execute(SCHEMA_MIGRATIONS_DDL)
    conn.commit()
    return set(r[0] for r in conn.execute("SELECT version FROM schema_migrations"))


def run(conn):
    """Apply every pending migration in order. Returns the list of versions applied."""
    done = applied_versions(conn)
    ran = []
    for version, name, fn in MIGRATIONS:
        if version in done:
            continue
        fn(conn)
        conn.execute("INSERT INTO schema_migrations (version, name) VALUES (?,?)", (version, name))
        conn.commit()
        ran.append(version)
    if ran:
        print("[db] migrations applied: " + ", ".join(str(v) for v in ran))
    return ran
