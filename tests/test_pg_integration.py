"""
tests/test_pg_integration.py - the same schema and the same call idioms, on a
real Postgres.

Skipped unless a connection URL is supplied, so the normal suite stays offline:

    railway run --service Postgres python -m pytest tests/test_pg_integration.py -q

Every test runs inside its own throwaway schema (jh_test_<pid>_<n>), created at
setup and dropped at teardown, so nothing outside that schema is touched. The
module refuses to run against a database whose name looks like production.

The test that matters most is test_schema_matches_sqlite: it builds the schema
on both engines from the same migrations and diffs tables and columns. Without
it, the SQLite and Postgres schemas would be free to drift apart silently -
and Phase 2c would migrate real data into whatever they had drifted into.
"""
import os
import sqlite3
import uuid

import pytest

import dbdriver
import migrations

URL = (os.environ.get("JH_TEST_PG_URL")
       or os.environ.get("DATABASE_PUBLIC_URL")
       or os.environ.get("DATABASE_URL")
       or "")

pytestmark = pytest.mark.skipif(not URL, reason="no Postgres URL (set JH_TEST_PG_URL)")

# Refuse to touch the production database even inside a temp schema.
if URL and "jobhunter_prod" in URL:
    raise RuntimeError(
        "test_pg_integration must never point at jobhunter_prod. "
        "Use the default 'railway' database or jobhunter_staging."
    )


@pytest.fixture
def pg():
    """A Postgres connection isolated in its own schema, dropped afterwards."""
    conn = dbdriver.connect_postgres(URL)
    schema = "jh_test_%s" % uuid.uuid4().hex[:10]
    conn.execute("CREATE SCHEMA " + schema)
    conn.execute("SET search_path TO " + schema)
    yield conn
    try:
        conn.execute("DROP SCHEMA " + schema + " CASCADE")
    finally:
        conn.close()


@pytest.fixture
def lite(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "cmp.db"))
    conn.row_factory = sqlite3.Row
    conn.isolation_level = None
    return conn


# ── Schema ────────────────────────────────────────────────────────────────────

def test_migrations_build_the_schema_on_postgres(pg):
    applied = migrations.run(pg)
    assert applied == [1, 2, 3]
    assert migrations._table_exists(pg, "users")
    assert migrations._table_exists(pg, "jobs")
    assert migrations._has_column(pg, "jobs", "apply_status")


def test_second_run_on_postgres_is_a_no_op(pg):
    migrations.run(pg)
    assert migrations.run(pg) == []


def test_schema_matches_sqlite(pg, lite):
    """The drift detector: same migrations, same tables, same columns."""
    migrations.run(pg)
    migrations.run(lite)

    pg_tables = {r[0] for r in pg.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema=current_schema()").fetchall()}
    lite_tables = {r[0] for r in lite.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()}
    assert pg_tables == lite_tables, (
        f"tables differ - only on pg: {pg_tables - lite_tables}, "
        f"only on sqlite: {lite_tables - pg_tables}")

    for table in sorted(lite_tables):
        pg_cols = {r[0] for r in pg.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema=current_schema() AND table_name=?", (table,)).fetchall()}
        lite_cols = {r[1] for r in lite.execute("PRAGMA table_info(" + table + ")").fetchall()}
        assert pg_cols == lite_cols, (
            f"{table}: only on pg {pg_cols - lite_cols}, only on sqlite {lite_cols - pg_cols}")


# ── The call idioms the app actually uses ─────────────────────────────────────

def _seed_user(conn, email="a@example.test"):
    conn.execute(
        "INSERT INTO users (name, email, password_hash, salt) VALUES (?,?,?,?)",
        ("Ada", email, "hash", "salt"))
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def test_question_mark_parameters_work(pg):
    migrations.run(pg)
    uid = _seed_user(pg)
    row = pg.execute("SELECT name, email FROM users WHERE id=?", (uid,)).fetchone()
    assert row["name"] == "Ada"


def test_last_insert_rowid_returns_the_new_id(pg):
    """auth.create_user does exactly this, twice."""
    migrations.run(pg)
    uid = _seed_user(pg, "one@example.test")
    uid2 = _seed_user(pg, "two@example.test")
    assert isinstance(uid, int) and uid2 == uid + 1


def test_rows_support_every_access_pattern_the_app_uses(pg):
    migrations.run(pg)
    uid = _seed_user(pg)
    row = pg.execute("SELECT id, name, email FROM users WHERE id=?", (uid,)).fetchone()

    assert row["name"] == "Ada"          # key access
    assert row[0] == uid                 # positional
    assert dict(row)["email"] == "a@example.test"
    assert "name" in row.keys()
    a, b, c = row                        # unpacking
    assert (a, b) == (uid, "Ada")


def test_datetime_now_default_matches_the_stored_text_format(pg):
    """Dates stay TEXT 'YYYY-MM-DD HH:MM:SS' so string comparisons keep working."""
    migrations.run(pg)
    uid = _seed_user(pg)
    created = pg.execute("SELECT created_date FROM users WHERE id=?", (uid,)).fetchone()[0]
    assert isinstance(created, str)
    assert len(created) == 19 and created[4] == "-" and created[13] == ":", created


def test_text_date_comparison_against_now_works(pg):
    """auth.get_session_user compares expires_date > datetime('now')."""
    migrations.run(pg)
    uid = _seed_user(pg)
    pg.execute("INSERT INTO sessions (token, user_id, expires_date) VALUES (?,?,?)",
               ("tok", uid, "2099-01-01 00:00:00"))
    row = pg.execute(
        "SELECT token FROM sessions WHERE token=? AND expires_date > datetime('now')",
        ("tok",)).fetchone()
    assert row is not None, "a live session was treated as expired"


def test_like_with_a_literal_percent_and_params(pg):
    """The escaping case: a LIKE pattern plus bound parameters in one query."""
    migrations.run(pg)
    uid = _seed_user(pg)
    pg.execute("INSERT INTO jobs (user_id, title, company, url) VALUES (?,?,?,?)",
               (uid, "VP Product", "Acme", "https://example.test/demo/123"))
    rows = pg.execute(
        "SELECT id FROM jobs WHERE user_id=? AND url LIKE 'https://example.test/demo/%'",
        (uid,)).fetchall()
    assert len(rows) == 1


def test_unique_constraint_is_enforced(pg):
    migrations.run(pg)
    uid = _seed_user(pg)
    pg.execute("INSERT INTO jobs (user_id, title, company, url) VALUES (?,?,?,?)",
               (uid, "t", "c", "https://example.test/1"))
    with pytest.raises(Exception):
        pg.execute("INSERT INTO jobs (user_id, title, company, url) VALUES (?,?,?,?)",
                   (uid, "t", "c", "https://example.test/1"))


def test_foreign_key_cascade_deletes_children(pg):
    """SQLite needs PRAGMA foreign_keys; Postgres enforces them natively."""
    migrations.run(pg)
    uid = _seed_user(pg)
    pg.execute("INSERT INTO jobs (user_id, title, company, url) VALUES (?,?,?,?)",
               (uid, "t", "c", "https://example.test/9"))
    pg.execute("DELETE FROM users WHERE id=?", (uid,))
    left = pg.execute("SELECT COUNT(*) FROM jobs WHERE user_id=?", (uid,)).fetchone()[0]
    assert left == 0
