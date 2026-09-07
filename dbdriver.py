"""
dbdriver.py - one connection interface over sqlite3 and Postgres.

Phase 2b of EXECUTION_PLAN_PUBLIC_LAUNCH.md. The app has ~189 SQL call sites
that all take the connection `db.get_db()` hands them and call `.execute(sql,
params)` on it with sqlite's `?` placeholders. Rewriting those is a 189-site
diff with 189 chances to introduce a subtle bug in code that touches every
user's data.

So instead the connection changes underneath them. This module provides a
Postgres connection that behaves the way the callers already expect:

  * `?` placeholders, translated to `%s`
  * rows that answer to row["col"], row[0], dict(row), row.keys(), and unpack
    positionally - the full sqlite3.Row contract (see tests)
  * the two SQLite-only functions the code actually calls in SQL, mapped over

What it deliberately does NOT do is pretend to be a general SQLite emulator.
It covers exactly the idioms this codebase uses; anything else should fail
loudly rather than quietly do something different from SQLite.
"""
import re

SQLITE = "sqlite"
POSTGRES = "postgres"

# The app stores dates as TEXT in 'YYYY-MM-DD HH:MM:SS' and compares them as
# strings. Phase 2b keeps that representation so the migration is a data move
# rather than a data-model change; proper timestamptz is logged as follow-up.
PG_NOW = "to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS')"

_DATETIME_NOW = re.compile(r"datetime\(\s*'now'\s*\)", re.IGNORECASE)
_INSERT_IGNORE = re.compile(r"INSERT\s+OR\s+IGNORE\s+INTO", re.IGNORECASE)
_INSERT_REPLACE = re.compile(
    r"INSERT\s+OR\s+REPLACE\s+INTO\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)",
    re.IGNORECASE)

# Postgres needs an explicit conflict target for an upsert. These are the only
# two tables the code upserts into, and the target is their existing unique
# constraint. Mapped by name on purpose: an unmapped table raises rather than
# silently doing something different from SQLite.
_UPSERT_KEYS = {
    "app_flags": ("key",),
    "push_subscriptions": ("user_id", "endpoint"),
}
_LAST_ROWID = re.compile(r"last_insert_rowid\(\s*\)", re.IGNORECASE)


def convert_placeholders(sql: str, escape_percent: bool) -> str:
    """
    Rewrite sqlite `?` placeholders as psycopg `%s`, leaving anything inside a
    string literal or a quoted identifier alone.

    When parameters are passed, psycopg treats `%` in the query as its own
    formatting character, so every literal one must be doubled - including the
    ones inside LIKE patterns such as LIKE 'https://example.com/demo/%'.
    """
    out = []
    quote = None          # "'" or '"' when inside a literal/identifier
    i = 0
    while i < len(sql):
        ch = sql[i]

        if quote:
            if ch == quote:
                # '' and "" are escaped quotes, not the end of the literal
                if i + 1 < len(sql) and sql[i + 1] == quote:
                    out.append(ch)
                    out.append(sql[i + 1])
                    i += 2
                    continue
                quote = None
            if ch == "%" and escape_percent:
                out.append("%%")
            else:
                out.append(ch)
            i += 1
            continue

        if ch in ("'", '"'):
            quote = ch
            out.append(ch)
        elif ch == "?":
            out.append("%s")
        elif ch == "%" and escape_percent:
            out.append("%%")
        else:
            out.append(ch)
        i += 1

    if quote:
        raise ValueError("unbalanced quote in SQL: " + sql[:80])
    return "".join(out)


def _rewrite_upserts(sql: str) -> str:
    """
    INSERT OR IGNORE  -> INSERT ... ON CONFLICT DO NOTHING
    INSERT OR REPLACE -> INSERT ... ON CONFLICT (keys) DO UPDATE SET ...

    Note the one semantic difference: SQLite's OR REPLACE deletes the old row
    and inserts a new one, resetting any column the statement does not name.
    ON CONFLICT DO UPDATE only touches the named columns. Both upsert sites in
    this codebase list every column, so the behaviour matches - which is why
    the mapping is explicit rather than generic.
    """
    if _INSERT_IGNORE.search(sql):
        return _INSERT_IGNORE.sub("INSERT INTO", sql).rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"

    m = _INSERT_REPLACE.search(sql)
    if not m:
        return sql
    table = m.group(1)
    cols = [c.strip() for c in m.group(2).split(",") if c.strip()]
    keys = _UPSERT_KEYS.get(table.lower())
    if not keys:
        raise ValueError(
            "INSERT OR REPLACE INTO %s has no conflict target in dbdriver._UPSERT_KEYS. "
            "Add one naming that table's unique constraint." % table)
    updates = [c for c in cols if c.lower() not in {k.lower() for k in keys}]
    if not updates:
        tail = " ON CONFLICT (%s) DO NOTHING" % ", ".join(keys)
    else:
        tail = " ON CONFLICT (%s) DO UPDATE SET %s" % (
            ", ".join(keys), ", ".join("%s=EXCLUDED.%s" % (c, c) for c in updates))
    out = sql[:m.start()] + "INSERT INTO %s (%s)" % (table, ", ".join(cols)) + sql[m.end():]
    return out.rstrip().rstrip(";") + tail


def translate(sql: str, dialect: str, has_params: bool) -> str:
    """SQLite-flavoured SQL -> the dialect actually in use."""
    if dialect == SQLITE:
        return sql
    sql = _rewrite_upserts(sql)
    sql = _DATETIME_NOW.sub(PG_NOW, sql)
    sql = _LAST_ROWID.sub("lastval()", sql)
    return convert_placeholders(sql, escape_percent=has_params)


class Row(dict):
    """
    A result row with sqlite3.Row's access contract.

    dict subclass, so dict(row), {**row} and json round-trips work; __getitem__
    also takes an integer for positional access, and iteration yields values
    (not keys) so `a, b = cur.fetchone()` behaves as it does on SQLite.
    """

    __slots__ = ()

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return dict.__getitem__(self, key)

    def __iter__(self):
        return iter(self.values())


def _row_factory(cursor):
    """psycopg row factory producing Row objects."""
    cols = [d.name for d in (cursor.description or [])]

    def make(values):
        return Row(zip(cols, values))

    return make


class PgCursor:
    """Wraps a psycopg cursor so it answers like a sqlite3 one."""

    def __init__(self, cur):
        self._cur = cur

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    def fetchmany(self, size=None):
        return self._cur.fetchmany(size) if size is not None else self._cur.fetchmany()

    def __iter__(self):
        return iter(self._cur)

    @property
    def rowcount(self):
        return self._cur.rowcount

    @property
    def description(self):
        return self._cur.description

    @property
    def lastrowid(self):
        raise NotImplementedError(
            "lastrowid has no Postgres equivalent. Use 'INSERT ... RETURNING id' "
            "or SELECT last_insert_rowid(), which this driver maps to lastval()."
        )

    def close(self):
        self._cur.close()


class PgConnection:
    """
    A psycopg connection wearing sqlite3.Connection's interface, limited to the
    idioms this codebase uses. Autocommit mirrors db.get_db()'s SQLite setup
    (isolation_level=None), so .commit() stays a harmless no-op at call sites.
    """

    dialect = POSTGRES

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=()):
        sql = translate(sql, POSTGRES, bool(params))
        cur = self._conn.cursor(row_factory=_row_factory)
        cur.execute(sql, tuple(params) if params else None)
        return PgCursor(cur)

    def executemany(self, sql, seq_of_params):
        seq = list(seq_of_params)
        sql = translate(sql, POSTGRES, bool(seq))
        cur = self._conn.cursor(row_factory=_row_factory)
        cur.executemany(sql, [tuple(p) for p in seq])
        return PgCursor(cur)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    @property
    def closed(self):
        return self._conn.closed

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def connect_postgres(url: str, connect_timeout: int = 15) -> PgConnection:
    """Open a Postgres connection with the sqlite-compatible wrapper."""
    import psycopg  # imported lazily: SQLite-only deployments need not install it

    conn = psycopg.connect(url, autocommit=True, connect_timeout=connect_timeout)
    return PgConnection(conn)


def dialect_of(conn) -> str:
    """Which engine is behind this connection."""
    return getattr(conn, "dialect", SQLITE)
