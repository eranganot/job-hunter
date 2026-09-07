"""
tests/test_migration_verify.py - the comparison logic behind scripts/sqlite_to_pg.py.

The migration's whole claim to safety is that its verifier can tell a faithful
copy from a subtly wrong one. These tests check that claim on the coercions that
actually happen between SQLite (dynamically typed) and Postgres (not).
"""
import importlib.util
import os

import pytest

_spec = importlib.util.spec_from_file_location(
    "sqlite_to_pg",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "scripts", "sqlite_to_pg.py"))
migrate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migrate)


@pytest.mark.parametrize("a,b,why", [
    (None, "",     "NULL vs empty string"),
    (None, 0,      "NULL vs zero"),
    (0,    "0",    "integer vs its string form"),
    (5,    "5",    "integer vs its string form"),
    (1,    True,   "integer vs boolean"),
    ("",   " ",    "empty vs whitespace"),
    ("2026-09-07", "2026-09-7", "malformed date"),
])
def test_these_values_must_not_compare_equal(a, b, why):
    assert migrate.canonical(a) != migrate.canonical(b), why


@pytest.mark.parametrize("value", [None, "", 0, 1, -3, 4.5, "text", "  padded  "])
def test_a_value_always_equals_itself(value):
    assert migrate.canonical(value) == migrate.canonical(value)


def test_row_digest_names_the_column_that_differs():
    cols = ["id", "notes", "score"]
    a = migrate.row_digest(cols, (1, None, 5))
    b = migrate.row_digest(cols, (1, "", 5))
    assert a != b
    differing = [x for x, y in zip(a.split("|"), b.split("|")) if x != y]
    assert differing == ["notes=N"], differing


def test_checksum_is_order_independent_per_row_but_content_sensitive():
    cols = ["id", "v"]
    same_a, _ = migrate.table_checksum([(1, "a"), (2, "b")], cols)
    same_b, _ = migrate.table_checksum([(1, "a"), (2, "b")], cols)
    changed, _ = migrate.table_checksum([(1, "a"), (2, "B")], cols)
    assert same_a == same_b
    assert same_a != changed


def test_checksum_maps_rows_by_key_for_pinpointing():
    _sum, per_row = migrate.table_checksum([(7, "x"), (9, "y")], ["id", "v"])
    assert set(per_row) == {7, 9}


def test_table_order_puts_parents_before_children():
    """A child copied before its parent fails on the FK Postgres actually enforces."""
    order = migrate.TABLE_ORDER
    for child, parent in [("user_profiles", "users"), ("sessions", "users"),
                          ("jobs", "users"), ("activity_log", "users"),
                          ("push_subscriptions", "users")]:
        assert order.index(parent) < order.index(child), f"{parent} must precede {child}"


def test_with_database_swaps_only_the_database_name():
    out = migrate.with_database("postgresql://u:p@host:5432/railway", "jobhunter_staging")
    assert out == "postgresql://u:p@host:5432/jobhunter_staging"
    assert "u:p@host:5432" in out, "credentials or host were altered"
