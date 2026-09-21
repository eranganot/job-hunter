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


# ── Connection pooling ────────────────────────────────────────────────────────

def test_pool_settings_come_from_the_environment(monkeypatch):
    import importlib
    monkeypatch.setenv("JH_PG_POOL_MAX", "3")
    reloaded = importlib.reload(dbdriver)
    assert reloaded.POOL_MAX == 3
    monkeypatch.delenv("JH_PG_POOL_MAX")
    importlib.reload(dbdriver)


def test_close_returns_a_pooled_connection_instead_of_closing_it():
    """
    get_db() is called ~189 times; the call sites all close afterwards. With a
    pool, close() must mean 'give it back', or the pool drains to nothing.
    """
    returned = []

    class FakePool:
        def putconn(self, conn):
            returned.append(conn)

    class FakeConn:
        closed = False

        def close(self):
            raise AssertionError("a pooled connection must not be closed directly")

    conn = FakeConn()
    wrapper = dbdriver.PgConnection(conn, pool=FakePool())
    wrapper.close()
    assert returned == [conn]


def test_double_close_returns_the_connection_only_once():
    """Returning the same connection twice corrupts the pool's accounting."""
    returned = []

    class FakePool:
        def putconn(self, conn):
            returned.append(conn)

    wrapper = dbdriver.PgConnection(object(), pool=FakePool())
    wrapper.close()
    wrapper.close()
    assert len(returned) == 1


def test_unpooled_connection_still_closes_for_real():
    closed = []

    class FakeConn:
        def close(self):
            closed.append(True)

    dbdriver.PgConnection(FakeConn()).close()
    assert closed == [True]


# ── Pooling: who needs it, and what happens when it is missing ────────────────
#
# 2026-09-14: `railway run python scripts/import_cv_files.py ...` died on
# ModuleNotFoundError: psycopg_pool. `railway run` injects variables and runs
# the command on the OPERATOR'S machine - so a script inherited the server's
# pooled path and, with it, a dependency that had no business being required to
# copy eight files.

def test_the_server_pools_by_default(monkeypatch):
    monkeypatch.delenv("JH_PG_POOL", raising=False)
    assert dbdriver.pooling_wanted() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "OFF", " 0 "])
def test_a_script_can_opt_out(monkeypatch, value):
    monkeypatch.setenv("JH_PG_POOL", value)
    assert dbdriver.pooling_wanted() is False


def test_every_one_shot_script_opts_out():
    """The fix has to be in the scripts, not only available to them."""
    import io as _io
    for path in ("scripts/import_cv_files.py", "scripts/sqlite_to_pg.py"):
        src = _io.open(path, encoding="utf-8").read()
        assert 'JH_PG_POOL", "0"' in src, "%s still takes the server's pooled path" % path


def test_a_missing_pool_library_degrades_rather_than_crashing(monkeypatch):
    """
    An outage is the wrong answer to a missing performance dependency - but so
    is silence, which turns it into "Postgres is slow" for a reason that is not
    about Postgres.
    """
    import sys
    monkeypatch.setitem(sys.modules, "psycopg_pool", None)   # import raises
    monkeypatch.setattr(dbdriver, "_POOLS", {}, raising=False)
    monkeypatch.setattr(dbdriver, "POOL_UNAVAILABLE", None, raising=False)

    assert dbdriver._get_pool("postgresql://u:p@h:5432/db") is None
    assert dbdriver.POOL_UNAVAILABLE, "the reason was not recorded"


def test_health_reports_that_pooling_is_unavailable(monkeypatch):
    """Pooled and unpooled look identical from outside unless the box says so."""
    monkeypatch.setattr(dbdriver, "_POOLS", {}, raising=False)
    monkeypatch.setattr(dbdriver, "POOL_UNAVAILABLE", "No module named 'psycopg_pool'",
                        raising=False)
    stats = dbdriver.pool_stats()
    assert "unavailable" in stats.get("pooling", "")


def test_pooling_is_decided_by_the_environment_not_hardcoded():
    """connect_postgres must default to 'ask', so JH_PG_POOL actually reaches it."""
    import inspect
    assert inspect.signature(dbdriver.connect_postgres).parameters["pooled"].default is None


# ── Duplicate column names (2026-09-21) ──────────────────────────────────────
#
# Postgres names an unaliased expression after its function, so a query with
# two COALESCE(...) columns returns two columns both called "coalesce". Row
# used to answer positional access from list(self.values()) - and a dict holds
# one value per key, so the duplicate overwrote the first, the row shrank, and
# every index after it shifted by one. On staging that made /api/health's
# llm_history fail with "list index out of range", and made the LLM ledger's
# restart re-sync fail the same way (so a Postgres restart handed the day a
# fresh budget). SQLite names these columns by full expression text, so the
# identical query was right there, and every SQLite-backed test passed.
# The rows below are shaped exactly like the one psycopg produced.

def _dup_row():
    return Row([("day", "2026-09-20"), ("coalesce", 5), ("coalesce", 180), ("count", 2)])


def test_positional_access_survives_duplicate_column_names():
    r = _dup_row()
    assert (r[0], r[1], r[2], r[3]) == ("2026-09-20", 5, 180, 2), \
        "a duplicate column name shifted the positions - calls read as tokens"


def test_unpacking_and_len_survive_duplicate_column_names():
    r = _dup_row()
    day, calls, tokens, users = r
    assert (calls, tokens, users) == (5, 180, 2)
    assert len(r) == 4


def test_the_real_query_shape_on_sqlite_and_pg_agree(sqlite_row):
    """The same four-column aggregate, positionally, must read the same on both."""
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE u (day TEXT, user_id INT, calls INT, total_tokens INT)")
    conn.executemany("INSERT INTO u VALUES (?,?,?,?)",
                     [("2026-09-20", 1, 3, 120), ("2026-09-20", 2, 2, 60)])
    lite = conn.execute(
        "SELECT day, COALESCE(SUM(calls),0), COALESCE(SUM(total_tokens),0), "
        "COUNT(DISTINCT user_id) FROM u GROUP BY day").fetchone()
    assert tuple(lite) == tuple(_dup_row())
