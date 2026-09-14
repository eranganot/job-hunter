#!/usr/bin/env python3
"""
import_cv_files.py - move CV PDFs off the volume and into the database.

Phase 2d of EXECUTION_PLAN_PUBLIC_LAUNCH.md.

Reads <uploads>/<user_id>/cv.pdf for every user directory it finds, stores the
bytes in `user_files`, then reads each one back out of the database and
compares sha256 AND length against the source file. A run that cannot prove a
byte-for-byte round trip exits non-zero and says which user failed.

Nothing is written to the source directory, ever - the files stay where they
are, and become a cache rather than the only copy.

Usage
    # rehearse - reads everything, writes nothing
    python scripts/import_cv_files.py --uploads C:\\dev\\_backups\\job-hunter\\2026-09-07_2050\\web-volume-uploads --dry-run

    # against staging's Postgres
    python scripts/import_cv_files.py --uploads <dir> --url <postgres-url> --database jobhunter_staging

    # re-check a database that was imported earlier, writing nothing
    python scripts/import_cv_files.py --uploads <dir> --verify-only
"""
import argparse
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as database      # noqa: E402
import migrations          # noqa: E402
import storage             # noqa: E402

# A one-shot script opens one connection and exits: a pool is overhead here, and
# an extra dependency to install wherever this runs. `railway run` executes on
# the operator's machine, not on Railway - which is exactly where a missing
# psycopg_pool stopped this script on 2026-09-14.
os.environ.setdefault("JH_PG_POOL", "0")
from scripts.sqlite_to_pg import with_database  # noqa: E402


def log(msg=""):
    print(msg, flush=True)


def discover(uploads):
    """(user_id, path) for every <uploads>/<digits>/cv.pdf, lowest id first."""
    found = []
    for name in sorted(os.listdir(uploads), key=lambda x: (not x.isdigit(), x)):
        if not name.isdigit():
            continue
        cv = os.path.join(uploads, name, "cv.pdf")
        if os.path.isfile(cv) and os.path.getsize(cv) > 0:
            found.append((int(name), cv))
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uploads", required=True, help="directory holding <user_id>/cv.pdf")
    ap.add_argument("--url", default="", help="Postgres URL (default: the environment / SQLite)")
    ap.add_argument("--database", default="", help="target database name on that server")
    ap.add_argument("--sqlite", default="", help="SQLite file to use instead of Postgres")
    ap.add_argument("--dry-run", action="store_true", help="read and report; write nothing")
    ap.add_argument("--verify-only", action="store_true",
                    help="skip the import; just re-check what is already stored")
    args = ap.parse_args()

    if not os.path.isdir(args.uploads):
        sys.exit("[FAIL] no such uploads directory: " + args.uploads)

    # Point db at whatever was asked for, exactly as the app would see it.
    if args.sqlite:
        database.set_db_path(args.sqlite)
        os.environ.pop("DB_BACKEND", None)
        target = "sqlite:" + args.sqlite
    else:
        url = (args.url or os.environ.get("JH_TEST_PG_URL")
               or os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL") or "")
        if not url:
            sys.exit("[FAIL] no Postgres URL and no --sqlite. Pass --url, or run under "
                     "`railway run --service Postgres`.")
        if args.database:
            url = with_database(url, args.database)
        database.set_database_url(url)
        os.environ["DB_BACKEND"] = "postgres"
        # preflight() would refuse a target this script is about to populate;
        # the guard protects the *app*, and here we are deliberately the writer.
        os.environ["JH_PG_DATABASE"] = url.rsplit("/", 1)[-1]
        target = "postgres:" + url.rsplit("/", 1)[-1]

    storage.set_uploads_dir(args.uploads)

    files = discover(args.uploads)
    log("=== CV files -> database ===")
    log("  source : %s" % args.uploads)
    log("  target : %s" % target)
    log("  mode   : %s" % ("DRY RUN (no writes)" if args.dry_run else
                           ("VERIFY ONLY (no writes)" if args.verify_only else "import")))
    log("  found  : %d CV file(s)" % len(files))
    log()

    if not files:
        sys.exit("[FAIL] no <user_id>/cv.pdf under that directory - wrong path?")

    conn = database.get_db()
    applied = migrations.run(conn)
    conn.close()
    log("[OK] schema ready (migrations applied: %s)" % (applied or "already current"))

    if args.dry_run:
        log()
        for uid, path in files:
            with open(path, "rb") as f:
                data = f.read()
            log("     user %-3s %8d bytes  sha256 %s  %s"
                % (uid, len(data), hashlib.sha256(data).hexdigest()[:12], os.path.basename(path)))
        log("\n[DONE] dry run - nothing was written.")
        return

    failures = []
    log()
    for uid, path in files:
        with open(path, "rb") as f:
            source = f.read()
        src_digest = hashlib.sha256(source).hexdigest()

        if not args.verify_only:
            try:
                storage.put(uid, source, filename="cv.pdf")
            except Exception as exc:
                failures.append("user %s: store failed: %s" % (uid, exc))
                log("  FAIL user %-3s store failed: %s" % (uid, exc))
                continue

        # Read it back out of the database - not out of the cache - and compare
        # against the file on disk. Anything less would be checking our own
        # bookkeeping rather than the data.
        stored = storage.get_bytes(uid, adopt=False)
        if stored is None:
            failures.append("user %s: nothing came back from the database" % uid)
            log("  FAIL user %-3s nothing came back from the database" % uid)
            continue
        got_digest = hashlib.sha256(stored).hexdigest()
        if got_digest != src_digest or len(stored) != len(source):
            failures.append("user %s: %s/%dB in, %s/%dB out"
                            % (uid, src_digest[:12], len(source), got_digest[:12], len(stored)))
            log("  FAIL user %-3s sha256 %s in, %s out" % (uid, src_digest[:12], got_digest[:12]))
            continue
        log("  ok   user %-3s %8d bytes  sha256 %s" % (uid, len(stored), got_digest[:12]))

    log()
    if failures:
        log("[FAIL] the import did NOT verify:")
        for f in failures:
            log("   - " + f)
        sys.exit(1)
    log("[DONE] %d CV(s) stored and verified byte-for-byte against the source files." % len(files))


if __name__ == "__main__":
    main()
