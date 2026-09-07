"""
tests/test_dbdriver.py - the SQLite -> Postgres compatibility layer.

These are the tests that decide whether ~189 untouched SQL call sites keep
working after the engine changes underneath them. The row contract is checked
against sqlite3.Row itself rather than against my memory of it.
"""
import sqlite3

import pytest

import dbdriver
from dbdriver import POSTGRES, SQLITE, Row, convert_placeholders, translate


# ── Placeholder translation ───────────────────────────────────────────────────

def test_question_marks_become_percent_s():
    assert convert_placeholders("SELECT * FROM jobs WHERE id=? AND user_id=?", False) \
        == "SELECT * FROM jobs WHERE id=%s AND user_id=%s"


def test_question_mark_inside_a_string_literal_is_left_alone():
    sql = "SELECT * FROM jobs WHERE title='what?' AND id=?"
    assert convert_placeholders(sql, False) == "SELECT * FROM jobs WHERE title='what?' AND id=%s"


def test_question_mark_inside_a_quoted_identifier_is_left_alone():
    sql = 'SELECT "odd?col" FROM jobs WHERE id=?'
    assert convert_placeholders(sql, False) == 'SELECT "odd?col" FROM jobs WHERE id=%s'


def test_escaped_quotes_do_not_end_the_literal():
    sql = "SELECT * FROM jobs WHERE notes='it''s ok?' AND id=?"
    out = convert_placeholders(sql, False)
    assert out.endswith("id=%s")
    assert "it''s ok?" in out, "the escaped-quote literal was corrupted"


def test_literal_percent_is_doubled_only_when_params_are_passed():
    sql = "SELECT * FROM jobs WHERE url LIKE 'https://example.test/demo/%'"
    assert convert_placeholders(sql, True) == \
        "SELECT * FROM jobs WHERE url LIKE 'https://example.test/demo/%%'"
    assert convert_placeholders(sql, False) == sql


def test_generated_placeholders_are_not_themselves_escaped():
    out = convert_placeholders("SELECT * FROM jobs WHERE id=? AND url LIKE '%x%'", True)
    assert "id=%s" in out, "the placeholder we generated got mangled"
    assert "'%%x%%'" in out


def test_unbalanced_quote_is_rejected_loudly():
    with pytest.raises(ValueError):
        convert_placeholders("SELECT * FROM jobs WHERE title='oops", False)


# ── Function mapping ──────────────────────────────────────────────────────────

def test_sqlite_dialect_is_passed_through_untouched():
    sql = "SELECT datetime('now'), last_insert_rowid() FROM jobs WHERE id=?"
    assert translate(sql, SQLITE, True) == sql


def test_datetime_now_is_mapped_and_keeps_the_text_format():
    out = translate("SELECT * FROM sessions WHERE expires_date > datetime('now')", POSTGRES, False)
    assert "datetime('now')" not in out
    assert "to_char(now()" in out
    assert "YYYY-MM-DD HH24:MI:SS" in out, "the stored TEXT date format must not change"


def test_datetime_now_tolerates_whitespace_and_case():
    out = translate("SELECT DATETIME( 'now' )", POSTGRES, False)
    assert "to_char(now()" in out


def test_last_insert_rowid_maps_to_lastval():
    out = translate("SELECT last_insert_rowid()", POSTGRES, False)
    assert out == "SELECT lastval()"


def test_translation_composes_with_placeholders():
    out = translate("INSERT INTO jobs (user_id, found_date) VALUES (?, datetime('now'))",
                    POSTGRES, True)
    assert "%s" in out and "to_char(now()" in out and "?" not in out


# ── The row contract, checked against sqlite3.Row ────────────────────────────

@pytest.fixture
def sqlite_row():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE t (id INTEGER, name TEXT, email TEXT)")
    conn.execute("INSERT INTO t VALUES (7, 'ada', 'a@example.test')")
    return conn.execute("SELECT id, name, email FROM t").fetchone()


@pytest.fixture
def pg_row():
    return Row([("id", 7), ("name", "ada"), ("email", "a@example.test")])


def test_row_matches_sqlite_on_key_access(sqlite_row, pg_row):
    assert pg_row["name"] == sqlite_row["name"] == "ada"


def test_row_matches_sqlite_on_positional_access(sqlite_row, pg_row):
    assert pg_row[0] == sqlite_row[0] == 7
    assert pg_row[2] == sqlite_row[2] == "a@example.test"


def test_row_matches_sqlite_on_dict_conversion(sqlite_row, pg_row):
    assert dict(pg_row) == dict(sqlite_row)


def test_row_matches_sqlite_on_keys(sqlite_row, pg_row):
    assert list(pg_row.keys()) == list(sqlite_row.keys())
    # app.py does exactly this in five places
    assert ("name" in pg_row.keys()) == ("name" in sqlite_row.keys())
    assert ("nope" in pg_row.keys()) == ("nope" in sqlite_row.keys())


def test_row_matches_sqlite_on_iteration_and_unpacking(sqlite_row, pg_row):
    assert list(pg_row) == list(sqlite_row) == [7, "ada", "a@example.test"]
    a, b, c = pg_row
    assert (a, b, c) == tuple(sqlite_row)


def test_row_matches_sqlite_on_len(sqlite_row, pg_row):
    assert len(pg_row) == len(sqlite_row) == 3


def test_row_survives_json(pg_row):
    import json
    assert json.loads(json.dumps(dict(pg_row)))["email"] == "a@example.test"


# ── Dialect detection ─────────────────────────────────────────────────────────

def test_a_plain_sqlite_connection_reports_sqlite():
    conn = sqlite3.connect(":memory:")
    assert dbdriver.dialect_of(conn) == SQLITE


def test_the_wrapper_reports_postgres():
    class FakePg:
        dialect = POSTGRES
    assert dbdriver.dialect_of(FakePg()) == POSTGRES


def test_lastrowid_fails_loudly_rather_than_silently(monkeypatch):
    """Silence here would mean inserting rows and losing their ids."""
    cur = dbdriver.PgCursor(object())
    with pytest.raises(NotImplementedError) as e:
        _ = cur.lastrowid
    assert "RETURNING" in str(e.value)


# ── Upserts ───────────────────────────────────────────────────────────────────

def test_insert_or_ignore_becomes_on_conflict_do_nothing():
    out = translate("INSERT OR IGNORE INTO jobs (user_id, url) VALUES (?, ?)", POSTGRES, True)
    assert out.startswith("INSERT INTO jobs")
    assert out.endswith("ON CONFLICT DO NOTHING")
    assert "OR IGNORE" not in out


def test_insert_or_replace_uses_the_tables_conflict_target():
    out = translate("INSERT OR REPLACE INTO app_flags (key, value) VALUES (?, ?)", POSTGRES, True)
    assert "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value" in out
    assert "OR REPLACE" not in out


def test_insert_or_replace_handles_a_composite_key():
    out = translate(
        "INSERT OR REPLACE INTO push_subscriptions "
        "(user_id, endpoint, subscription, created_date) VALUES (?,?,?,?)", POSTGRES, True)
    assert "ON CONFLICT (user_id, endpoint) DO UPDATE SET" in out
    assert "subscription=EXCLUDED.subscription" in out
    assert "user_id=EXCLUDED.user_id" not in out, "key columns must not be in the update list"


def test_unmapped_upsert_table_raises_rather_than_guessing():
    with pytest.raises(ValueError) as e:
        translate("INSERT OR REPLACE INTO mystery (a, b) VALUES (?, ?)", POSTGRES, True)
    assert "_UPSERT_KEYS" in str(e.value)


def test_upserts_are_untouched_on_sqlite():
    sql = "INSERT OR IGNORE INTO jobs (user_id, url) VALUES (?, ?)"
    assert translate(sql, SQLITE, True) == sql
