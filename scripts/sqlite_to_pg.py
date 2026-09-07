#!/usr/bin/env python3
"""
sqlite_to_pg.py - move Job Hunter's data from SQLite to Postgres, and prove it.

Phase 2c of EXECUTION_PLAN_PUBLIC_LAUNCH.md.

Row counts alone are a weak check: they pass while a NULL quietly becomes an
empty string, an integer becomes text, or a date loses a character. So after
copying, this compares a canonical per-table checksum computed on BOTH sides in
Python - value by value, type included - and names the first rows that differ.

The source file is opened read-only. Nothing is written to SQLite, ever.

Usage
    # rehearse (no writes to Postgres)
    python scripts/sqlite_to_pg.py jobs.db --database jobhunter_staging --dry-run

    # do it
    python scripts/sqlite_to_pg.py jobs.db --database jobhunter_staging --truncate

    # under Railway, so DATABASE_PUBLIC_URL is injected:
    railway run --service Postgres python scripts/sqlite_to_pg.py <db> --database jobhunter_staging
"""
import argparse
import hashlib
import os
import sqlite3
import sys
from urllib.parse import urlparse, urlunparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dbdriver          # noqa: E402
import migrations        # noqa: E402

# Parents before children: every FK points at a table earlier in this list.
# Copying out of order would fail on the foreign keys Postgres actually enforces.
TABLE_ORDER = [
    "users",
    "user_profiles",
    "sessions",
    "application_answers",
    "jobs",
    "activity_log",
    "rejected_patterns",
    "user_blocklist",
    "pass_reason_stats",
    "push_subscriptions",
    "career_url_cache",
    "app_flags",
]

BATCH = 500


def log(msg=""):
    print(msg, flush=True)


def canonical(value) -> str:
    """
    A value's identity for comparison: type AND content.

    'null' and '' must not compare equal, and 5 must not equal '5' - those are
    exactly the silent coercions a row-count check would wave through.
    """
    if value is None:
        return "N"
    if isinstance(value, bool):
        return "b:%d" % int(value)
    if isinstance(value, int):
        return "i:%d" % value
    if isinstance(value, float):
        return "f:%r" % value
    if isinstance(value, (bytes, bytearray)):
        return "x:" + hashlib.md5(bytes(value)).hexdigest()
    return "s:" + str(value)


def row_digest(cols, row) -> str:
    return "|".join("%s=%s" % (c, canonical(row[i])) for i, c in enumerate(cols))


def table_checksum(fetch_rows, cols):
    """md5 over every row, plus a {pk: digest} map for pinpointing differences."""
    h = hashlib.md5()
    per_row = {}
    for row in fetch_rows:
        d = row_digest(cols, row)
        h.update(d.encode("utf-8"))
        h.update(b"\n")
        per_row[row[0]] = d          # row[0] is the first ordered column (the key)
    return h.hexdigest(), per_row


def sqlite_columns(lite, table):
    return [r[1] for r in lite.execute("PRAGMA table_info(%s)" % table)]


def pg_columns(pg, table):
    rows = pg.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema=current_schema() AND table_name=? ORDER BY ordinal_position",
        (table,)).fetchall()
    return [r[0] for r in rows]


def key_column(cols):
    """What to order and index by: id when present, else the first column."""
    return "id" if "id" in cols else cols[0]


def with_database(url: str, database: str) -> str:
    """Point a connection URL at a specific database, leaving credentials alone."""
    p = urlparse(url)
    return urlunparse(p._replace(path="/" + database.lstrip("/")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sqlite_path")
    ap.add_argument("--url", default="", help="Postgres URL (default: from the environment)")
    ap.add_argument("--database", default="", help="target database name on that server")
    ap.add_argument("--truncate", action="store_true", help="clear target tables first")
    ap.add_argument("--dry-run", action="store_true", help="read and report; write nothing")
    ap.add_argument("--verify-only", action="store_true",
                    help="skip the copy; just compare an existing target against the source")
    ap.add_argument("--allow-prod", action="store_true", help="permit a target named *prod*")
    args = ap.parse_args()

    if not os.path.exists(args.sqlite_path):
        sys.exit("[FAIL] no such SQLite file: " + args.sqlite_path)

    url = (args.url or os.environ.get("JH_TEST_PG_URL") or
           os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL") or "")
    if not url:
        sys.exit("[FAIL] no Postgres URL. Pass --url, or run under `railway run --service Postgres`.")
    if args.database:
        url = with_database(url, args.database)

    target_db = (urlparse(url).path or "/").lstrip("/")
    if "prod" in target_db and not args.allow_prod:
        sys.exit("[FAIL] target database '%s' looks like production. "
                 "Re-run with --allow-prod once you mean it." % target_db)

    log("=== sqlite -> postgres ===")
    log("  source : %s" % args.sqlite_path)
    log("  target : %s on %s" % (target_db, urlparse(url).hostname))
    mode = "DRY RUN (no writes)" if args.dry_run else (
        "VERIFY ONLY (no writes)" if args.verify_only else "copy")
    log("  mode   : %s" % mode)
    log()

    # Read-only: a migration must never be able to damage its own source.
    lite = sqlite3.connect("file:%s?mode=ro" % os.path.abspath(args.sqlite_path), uri=True)
    lite.text_factory = str
    pg = dbdriver.connect_postgres(url)

    # --- schema ------------------------------------------------------------
    applied = migrations.run(pg)
    log("[OK] target schema ready (migrations applied: %s)" % (applied or "already current"))

    src_tables = {r[0] for r in lite.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    src_tables.discard("schema_migrations")

    unknown = src_tables - set(TABLE_ORDER)
    if unknown:
        sys.exit("[FAIL] source has tables this script does not know how to order: %s\n"
                 "       Add them to TABLE_ORDER (parents first) rather than skipping them." %
                 ", ".join(sorted(unknown)))

    tables = [t for t in TABLE_ORDER if t in src_tables]
    log("[OK] %d tables to copy" % len(tables))

    # --- pre-flight --------------------------------------------------------
    plan = []
    for t in tables:
        s_cols = sqlite_columns(lite, t)
        p_cols = pg_columns(pg, t)
        shared = [c for c in s_cols if c in p_cols]
        missing = [c for c in s_cols if c not in p_cols]
        if missing:
            sys.exit("[FAIL] %s: columns exist in SQLite but not in Postgres: %s\n"
                     "       The schemas have drifted - fix migrations before migrating data." %
                     (t, ", ".join(missing)))
        n = lite.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
        existing = pg.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
        plan.append((t, shared, n, existing))
        log("     %-22s %6d rows -> target has %d" % (t, n, existing))

    occupied = [(t, e) for t, _c, _n, e in plan if e]
    if occupied and not args.truncate and not args.dry_run and not args.verify_only:
        sys.exit("\n[FAIL] target is not empty: %s\n"
                 "       Re-run with --truncate to replace it, or point at an empty database." %
                 ", ".join("%s=%d" % (t, e) for t, e in occupied))

    if args.dry_run:
        log("\n[DONE] dry run - nothing was written.")
        return

    # --- copy --------------------------------------------------------------
    if args.verify_only:
        log("\n-- skipping the copy (--verify-only) --")
    else:
        log("\n-- copying --")
        for t, cols, n, _existing in plan:
            if args.truncate:
                pg.execute("TRUNCATE TABLE %s CASCADE" % t)
            if n == 0:
                log("     %-22s empty" % t)
                continue

            collist = ", ".join(cols)
            placeholders = ", ".join(["?"] * len(cols))
            insert = "INSERT INTO %s (%s) VALUES (%s)" % (t, collist, placeholders)

            cur = lite.execute("SELECT %s FROM %s" % (collist, t))
            copied = 0
            while True:
                rows = cur.fetchmany(BATCH)
                if not rows:
                    break
                try:
                    pg.executemany(insert, rows)
                except Exception as batch_err:
                    # Find the offending row rather than reporting "a batch failed".
                    for row in rows:
                        try:
                            pg.execute(insert, row)
                        except Exception as row_err:
                            detail = ", ".join(
                                "%s=%r" % (c, v) for c, v in zip(cols, row)
                                if v is not None)[:400]
                            sys.exit("\n[FAIL] %s: row rejected by Postgres\n"
                                     "       %s\n       row: %s\n"
                                     "       (batch error: %s)" % (t, row_err, detail, batch_err))
                    raise
                copied += len(rows)
            log("     %-22s %6d copied" % (t, copied))

        # --- sequences ---------------------------------------------------------
        # Without this the next INSERT reuses id 1 and collides immediately.
        log("\n-- sequences --")
        for t, cols, _n, _e in plan:
            if "id" not in cols:
                continue
            row = pg.execute(
                "SELECT setval(pg_get_serial_sequence(?, 'id'), "
                "COALESCE((SELECT MAX(id) FROM %s), 1), (SELECT MAX(id) IS NOT NULL FROM %s))" % (t, t),
                (t,)).fetchone()
            log("     %-22s next id after %s" % (t, row[0]))

    # --- verification ------------------------------------------------------
    log("\n-- verification (row counts, then value-by-value checksums) --")
    failures = []
    for t, cols, n, _e in plan:
        key = key_column(cols)
        order = ", ".join([key] + [c for c in cols if c != key])

        src_rows = lite.execute("SELECT %s FROM %s ORDER BY %s" % (order, t, key)).fetchall()
        dst_rows = pg.execute("SELECT %s FROM %s ORDER BY %s" % (order, t, key)).fetchall()
        ordered_cols = [key] + [c for c in cols if c != key]

        if len(src_rows) != len(dst_rows):
            failures.append("%s: %d rows in SQLite, %d in Postgres" % (t, len(src_rows), len(dst_rows)))
            log("  FAIL %-20s count %d != %d" % (t, len(src_rows), len(dst_rows)))
            continue

        s_sum, s_map = table_checksum(src_rows, ordered_cols)
        d_sum, d_map = table_checksum([tuple(r) for r in dst_rows], ordered_cols)

        if s_sum == d_sum:
            log("  ok   %-20s %6d rows  %s" % (t, len(src_rows), s_sum[:12]))
            continue

        diffs = [k for k in s_map if s_map.get(k) != d_map.get(k)][:3]
        failures.append("%s: checksum mismatch (%d rows)" % (t, len(src_rows)))
        log("  FAIL %-20s checksum differs" % t)
        for k in diffs:
            log("       key %r" % (k,))
            s_parts = s_map[k].split("|")
            d_parts = (d_map.get(k) or "").split("|")
            for sp, dp in zip(s_parts, d_parts):
                if sp != dp:
                    log("         sqlite  %s" % sp)
                    log("         postgres %s" % dp)

    lite.close()
    pg.close()

    log()
    if failures:
        log("[FAIL] migration did NOT verify:")
        for f in failures:
            log("   - " + f)
        sys.exit(1)
    log("[DONE] every table matched on row count and on value-by-value checksum.")


if __name__ == "__main__":
    main()
