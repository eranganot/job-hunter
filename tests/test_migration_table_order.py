"""
tests/test_migration_table_order.py - the data migration knows every table.

scripts/sqlite_to_pg.py copies tables in a fixed parent-first order and refuses
a source with a table it does not name, rather than silently skipping it. That
refusal is right, but it fired for the first time on the PRODUCTION dry run
(2026-09-21): migrations 6 and 7 had added job_runs and llm_usage after the
list was written, and the staging rehearsal predated both. This moves the
failure from cutover day to the commit that adds the table.
"""
import importlib.util
import os
import sqlite3

import migrations

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _script():
    spec = importlib.util.spec_from_file_location(
        "sqlite_to_pg", os.path.join(ROOT, "scripts", "sqlite_to_pg.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_every_table_the_migrations_create_is_in_table_order():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    migrations.run(conn)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    tables.discard("schema_migrations")
    missing = tables - set(_script().TABLE_ORDER)
    assert not missing, (
        "scripts/sqlite_to_pg.py TABLE_ORDER does not name %s - the production "
        "data migration will refuse to run. Add them (parents first)." % sorted(missing))


def test_users_comes_before_everything_that_points_at_it():
    order = _script().TABLE_ORDER
    assert order[0] == "users"


def test_checksum_does_not_depend_on_the_order_a_database_returns_rows():
    """SQLite sorts text keys by bytes, Postgres by collation - same rows, other order.

    The 2026-09-21 production copy failed verification on `sessions` for this
    reason alone: three token_urlsafe() keys, identical rows, different order.
    """
    mod = _script()
    cols = ["token", "user_id"]
    binary = [("-y", 1), ("Zeta", 2), ("_x", 3), ("alpha", 4)]      # SQLite
    linguistic = [("_x", 3), ("-y", 1), ("alpha", 4), ("Zeta", 2)]  # Postgres
    assert mod.table_checksum(binary, cols)[0] == mod.table_checksum(linguistic, cols)[0]


def test_checksum_still_catches_a_changed_value():
    mod = _script()
    cols = ["token", "user_id"]
    a = [("-y", 1), ("Zeta", 2)]
    b = [("Zeta", 2), ("-y", 99)]
    assert mod.table_checksum(a, cols)[0] != mod.table_checksum(b, cols)[0]
