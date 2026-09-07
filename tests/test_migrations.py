"""
tests/test_migrations.py - the migration runner that replaced db._migrate().

The property that matters for Phase 2: running the whole set against a database
that already has the schema must be a no-op. Production's DB is the one copy of
every user's job history, and the old try/except list re-attempted every
historical ALTER on every boot and swallowed whatever came back.
"""
import sqlite3

import pytest

import migrations


def fresh(tmp_path, name="t.db"):
    conn = sqlite3.connect(str(tmp_path / name))
    conn.row_factory = sqlite3.Row
    conn.isolation_level = None
    return conn


def cols(conn, table):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


# ── The basics ────────────────────────────────────────────────────────────────

def test_fresh_database_gets_the_whole_schema(tmp_path):
    conn = fresh(tmp_path)
    applied = migrations.run(conn)

    assert applied == [1, 2, 3]
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for expected in ("users", "sessions", "user_profiles", "jobs",
                     "activity_log", "schema_migrations"):
        assert expected in tables, f"{expected} missing from a fresh schema"


def test_second_run_is_a_no_op(tmp_path):
    conn = fresh(tmp_path)
    migrations.run(conn)

    assert migrations.run(conn) == [], "a second run re-applied migrations"
    n = conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
    assert n == len(migrations.MIGRATIONS), "schema_migrations gained duplicate rows"


def test_every_migration_is_recorded_with_its_name(tmp_path):
    conn = fresh(tmp_path)
    migrations.run(conn)

    rows = {r["version"]: r["name"] for r in
            conn.execute("SELECT version, name FROM schema_migrations")}
    for version, name, _fn in migrations.MIGRATIONS:
        assert rows.get(version) == name


def test_versions_are_unique_and_ordered():
    versions = [v for v, _n, _f in migrations.MIGRATIONS]
    assert versions == sorted(versions), "MIGRATIONS is out of order"
    assert len(versions) == len(set(versions)), "duplicate migration version"


# ── Upgrading a database that predates a column ───────────────────────────────

def test_missing_column_is_added_to_a_legacy_database(tmp_path):
    """A pre-migration DB: jobs exists but without match_score, no schema_migrations."""
    conn = fresh(tmp_path, "legacy.db")
    conn.execute("""CREATE TABLE jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, title TEXT, company TEXT, url TEXT,
        status TEXT DEFAULT 'new', apply_status TEXT)""")
    assert "match_score" not in cols(conn, "jobs")

    migrations.run(conn)
    assert "match_score" in cols(conn, "jobs"), "m0002 did not add the missing column"


def test_column_addition_does_not_fail_when_already_present(tmp_path):
    conn = fresh(tmp_path)
    migrations.run(conn)
    before = cols(conn, "jobs")

    migrations.m0002_column_additions(conn)      # deliberately re-run
    assert cols(conn, "jobs") == before


def test_backfill_sets_queued_on_approved_jobs(tmp_path):
    conn = fresh(tmp_path)
    migrations.run(conn)
    conn.execute("INSERT INTO users (id,name,email,password_hash,salt) "
                 "VALUES (1,'u','u@example.test','h','s')")
    conn.execute("INSERT INTO jobs (user_id,title,company,url,status,apply_status) "
                 "VALUES (1,'t','c','https://example.test/1','approved',NULL)")

    migrations.m0003_backfill_queued_apply_status(conn)
    got = conn.execute("SELECT apply_status FROM jobs WHERE id=1").fetchone()[0]
    assert got == "queued"


# ── The safety property ───────────────────────────────────────────────────────

def test_running_against_an_established_schema_changes_nothing(tmp_path):
    """
    Stand in for production: build the schema, add data, then run the whole set
    again and prove neither the schema nor the rows moved.
    """
    conn = fresh(tmp_path)
    migrations.run(conn)
    conn.execute("INSERT INTO users (id,name,email,password_hash,salt) "
                 "VALUES (1,'u','u@example.test','h','s')")
    for i in range(5):
        conn.execute("INSERT INTO jobs (user_id,title,company,url,status) "
                     f"VALUES (1,'t{i}','c','https://example.test/{i}','new')")

    def snapshot():
        schema = sorted(r[0] for r in conn.execute(
            "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"))
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in ("users", "jobs")}
        return schema, counts

    before = snapshot()
    assert migrations.run(conn) == []
    assert snapshot() == before, "re-running migrations altered an established database"


def test_helpers_are_honest_about_what_exists(tmp_path):
    conn = fresh(tmp_path)
    migrations.run(conn)

    assert migrations._table_exists(conn, "jobs")
    assert not migrations._table_exists(conn, "no_such_table")
    assert migrations._has_column(conn, "jobs", "status")
    assert not migrations._has_column(conn, "jobs", "no_such_column")
