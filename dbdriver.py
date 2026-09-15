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
import os
import re
import threading

# Route this module's print() calls through the logging module: timestamps,
# levels, the module name, and the request id of the request in flight.
import log as _log
print = _log.make_print(__name__)  # noqa: A001 - see log.py


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
    "user_files": ("user_id", "kind"),
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


def _normalise(value):
    """
    Binary columns must come back as `bytes`, exactly as sqlite3 returns a BLOB.

    Phase 2d stores CV PDFs in a bytea column. Depending on the driver version a
    bytea can arrive as a memoryview, which is *almost* bytes - it slices and
    compares, so a shallow test passes - but it hashes differently, will not
    json-serialise, and str()s to "<memory at 0x...>". Left alone it would turn
    the migration verifier's value-level checksum into a comparison of two
    pointer addresses, which is exactly the class of silent corruption that
    checksum exists to catch.
    """
    if isinstance(value, memoryview):
        return value.tobytes()
    if isinstance(value, bytearray):
        return bytes(value)
    return value


def _row_factory(cursor):
    """psycopg row factory producing Row objects."""
    cols = [d.name for d in (cursor.description or [])]

    def make(values):
        return Row(zip(cols, (_normalise(v) for v in values)))

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

    def __init__(self, conn, pool=None):
        self._conn = conn
        self._pool = pool
        self._returned = False

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
        """Return to the pool when pooled; otherwise close for real."""
        if self._returned:
            return
        self._returned = True
        if self._pool is not None:
            self._pool.putconn(self._conn)
        else:
            self._conn.close()

    @property
    def closed(self):
        return self._conn.closed

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


# get_db() is called at ~189 sites, often several times per request. A SQLite
# connection is almost free; a Postgres one is a TCP handshake, TLS and an auth
# round trip. Without pooling, moving to Postgres would look like "Postgres is
# slow" for reasons that have nothing to do with Postgres.
_POOLS = {}
_POOL_LOCK = threading.Lock()

POOL_MIN = int(os.environ.get("JH_PG_POOL_MIN", "1"))
POOL_MAX = int(os.environ.get("JH_PG_POOL_MAX", "10"))

# Set when psycopg_pool could not be imported. /api/health reports it, because
# "the app is quietly unpooled" and "the app is pooled" look identical from
# outside and differ by more than an order of magnitude in connection cost.
POOL_UNAVAILABLE = None


def pooling_wanted() -> bool:
    """
    Whether THIS process should pool.

    The server should: get_db() is called at ~189 sites, often several times per
    request. A one-shot CLI script should not - it opens one connection and
    exits, so a pool is pure overhead and an extra dependency to install
    wherever the script happens to run. `railway run` executes on the operator's
    laptop, not on Railway, which is where that bit us (2026-09-14).
    """
    return os.environ.get("JH_PG_POOL", "1").strip().lower() not in ("0", "false", "no", "off")


def _get_pool(url: str):
    """One pool per URL per process, created on first use."""
    with _POOL_LOCK:
        pool = _POOLS.get(url)
        if pool is None:
            global POOL_UNAVAILABLE
            try:
                from psycopg_pool import ConnectionPool
            except ImportError as exc:
                # Refusing to start would turn a performance feature into an
                # outage. Running unpooled in silence would turn it into a
                # mystery - "Postgres is slow" for a reason that is not about
                # Postgres. So: carry on, loudly, and say so in /api/health.
                POOL_UNAVAILABLE = str(exc)
                print("=" * 78, flush=True)
                print("CONNECTION POOLING IS OFF: %s" % exc, flush=True)
                print("  Every get_db() will open a new Postgres connection - "
                      "roughly 14x the cost per call.", flush=True)
                print("  Fix: pip install -r requirements.txt "
                      "(psycopg[binary,pool]==3.3.5 provides it).", flush=True)
                print("=" * 78, flush=True)
                return None

            def _configure(conn):
                # Mirrors db.get_db()'s SQLite setup (isolation_level=None), so
                # .commit() at the call sites stays a harmless no-op.
                conn.autocommit = True

            pool = ConnectionPool(url, min_size=POOL_MIN, max_size=POOL_MAX,
                                  configure=_configure, open=True, timeout=20)
            _POOLS[url] = pool
        return pool


def close_pools():
    """Shut every pool down. For tests and clean process exit."""
    with _POOL_LOCK:
        for pool in _POOLS.values():
            try:
                pool.close()
            except Exception:
                pass
        _POOLS.clear()


def pool_stats(url: str = None):
    """Pool health, for /api/health and for diagnosing exhaustion."""
    with _POOL_LOCK:
        pools = _POOLS if url is None else {url: _POOLS.get(url)}
        out = {}
        if POOL_UNAVAILABLE and not pools:
            return {"pooling": "unavailable: " + POOL_UNAVAILABLE}
        for key, pool in pools.items():
            if pool is None:
                continue
            st = pool.get_stats()
            out[key.split("@")[-1]] = {
                "size": st.get("pool_size"), "available": st.get("pool_available"),
                "waiting": st.get("requests_waiting"),
            }
        return out


def connect_postgres(url: str, connect_timeout: int = 15, pooled: bool = None) -> PgConnection:
    """
    Borrow a pooled Postgres connection wearing the sqlite-compatible interface.

    PgConnection.close() returns it to the pool rather than closing the socket,
    so the existing `conn = get_db() ... conn.close()` pattern keeps working
    unchanged - it just stops being expensive.
    """
    import psycopg  # imported lazily: SQLite-only deployments need not install it

    if pooled is None:
        pooled = pooling_wanted()
    if pooled:
        pool = _get_pool(url)
        if pool is not None:
            return PgConnection(pool.getconn(), pool=pool)
        # _get_pool said pooling is unavailable and has already said why.
    return PgConnection(psycopg.connect(url, autocommit=True,
                                        connect_timeout=connect_timeout))


def describe_server(url: str) -> str:
    """
    Ask a server what databases it actually has.

    Used to turn `FATAL: database "x" does not exist` into something that names
    the server it asked and lists what is on it. On 2026-09-14 that bare FATAL
    was the whole diagnostic, while the app was serving thousands of rows out
    of the very database the error said was missing - so the useful question
    was never "does it exist" but "on WHICH server does it exist".

    Read-only, and never prints or returns credentials.
    """
    from urllib.parse import urlparse, urlunparse
    try:
        import psycopg
    except ImportError as exc:
        # A diagnostic that raises is worse than no diagnostic: it replaces the
        # real error with its own.
        return "  (cannot inspect the server: %s)" % exc

    parsed = urlparse(url)
    where = "%s:%s" % (parsed.hostname, parsed.port or 5432)
    lines = ["  server : %s" % where, "  asked for database: %r" % (parsed.path or "/").lstrip("/")]

    for probe in ("postgres", "railway", ""):
        try:
            target = urlunparse(parsed._replace(path="/" + probe)) if probe else url
            conn = psycopg.connect(target, autocommit=True, connect_timeout=10)
        except Exception:
            continue
        try:
            rows = conn.execute(
                "SELECT datname FROM pg_database WHERE NOT datistemplate ORDER BY datname"
            ).fetchall()
            names = [r[0] for r in rows]
            lines.append("  databases on THIS server: %s" % ", ".join(names))
        except Exception as exc:
            lines.append("  (could not list databases: %s)" % exc)
        finally:
            conn.close()
        break
    else:
        lines.append("  (could not reach this server at all to ask)")

    lines.append("")
    lines.append("  If the database you want exists but not here, you are pointed at a "
                 "DIFFERENT Postgres instance -")
    lines.append("  Railway gives each environment its own copy of a service, and "
                 "`railway run` uses whichever")
    lines.append("  environment the CLI is linked to. Check with `railway status`.")
    return "\n".join(lines)


def dialect_of(conn) -> str:
    """Which engine is behind this connection."""
    return getattr(conn, "dialect", SQLITE)
