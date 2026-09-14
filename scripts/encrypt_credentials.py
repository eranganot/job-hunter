#!/usr/bin/env python3
"""
encrypt_credentials.py - convert the credentials already in the database.

Phase 3. crypto.py encrypts on write, so rows convert as users re-save their
settings - but nobody re-saves settings, so without this the existing rows stay
in plaintext forever.

Reads every user_profiles row, encrypts any secret field still in the clear,
then reads it back and checks it decrypts to exactly what was there before. A
row that cannot be proved to round-trip is reported and the run exits 1.

Safe to run repeatedly: an already-encrypted value is left alone.

Usage
    # rehearse - reports what would change, writes nothing
    python scripts/encrypt_credentials.py --dry-run

    # against staging's Postgres
    railway run --service Postgres python scripts/encrypt_credentials.py --database jobhunter_staging
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import crypto              # noqa: E402
import db as database      # noqa: E402

os.environ.setdefault("JH_PG_POOL", "0")   # one-shot: see dbdriver.pooling_wanted


def with_database(url: str, database_name: str) -> str:
    from urllib.parse import urlparse, urlunparse
    p = urlparse(url)
    return urlunparse(p._replace(path="/" + database_name.lstrip("/")))


def log(msg=""):
    print(msg, flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="", help="Postgres URL (default: the environment)")
    ap.add_argument("--database", default="", help="target database name on that server")
    ap.add_argument("--sqlite", default="", help="SQLite file instead of Postgres")
    ap.add_argument("--dry-run", action="store_true", help="report; write nothing")
    args = ap.parse_args()

    if not crypto.available():
        sys.exit("[FAIL] JH_ENCRYPTION_KEY is not set, so there is nothing to encrypt WITH.\n"
                 "       Set it to the same value the app uses, or this would write rows\n"
                 "       the app cannot read.")

    if args.sqlite:
        database.set_db_path(args.sqlite)
        os.environ.pop("DB_BACKEND", None)
        target = "sqlite:" + args.sqlite
    else:
        url = (args.url or os.environ.get("DATABASE_PUBLIC_URL")
               or os.environ.get("DATABASE_URL") or "")
        if not url:
            sys.exit("[FAIL] no Postgres URL and no --sqlite.")
        if args.database:
            url = with_database(url, args.database)
        database.set_database_url(url)
        os.environ["DB_BACKEND"] = "postgres"
        os.environ["JH_PG_DATABASE"] = url.rsplit("/", 1)[-1]
        target = "postgres:" + url.rsplit("/", 1)[-1]

    cols = ", ".join(crypto.SECRET_FIELDS)
    log("=== encrypt stored credentials ===")
    log("  target : %s" % target)
    log("  fields : %s" % cols)
    log("  mode   : %s" % ("DRY RUN (no writes)" if args.dry_run else "encrypt"))
    log()

    conn = database.get_db()
    try:
        rows = conn.execute("SELECT user_id, %s FROM user_profiles ORDER BY user_id" % cols).fetchall()
    except Exception as exc:
        conn.close()
        sys.exit("[FAIL] could not read user_profiles: %s" % exc)

    changed = skipped = 0
    failures = []
    for row in rows:
        uid = row["user_id"]
        updates = {}
        for field in crypto.SECRET_FIELDS:
            value = row[field]
            if not value:
                continue
            if crypto.is_encrypted(value):
                skipped += 1
                continue
            updates[field] = value

        if not updates:
            continue

        names = ", ".join(updates)
        if args.dry_run:
            log("  would encrypt  user %-3s %s" % (uid, names))
            changed += len(updates)
            continue

        sets = ", ".join("%s=?" % f for f in updates)
        conn.execute("UPDATE user_profiles SET %s WHERE user_id=?" % sets,
                     [crypto.encrypt(v) for v in updates.values()] + [uid])
        conn.commit()

        # Read it back and prove it decrypts to what was there before. Anything
        # less is checking our own bookkeeping rather than the data.
        check = conn.execute("SELECT %s FROM user_profiles WHERE user_id=?" % cols, (uid,)).fetchone()
        bad = [f for f, original in updates.items()
               if not crypto.is_encrypted(check[f]) or crypto.decrypt(check[f]) != original]
        if bad:
            failures.append("user %s: %s did not round-trip" % (uid, ", ".join(bad)))
            log("  FAIL user %-3s %s" % (uid, ", ".join(bad)))
            continue

        changed += len(updates)
        log("  ok   user %-3s encrypted %s" % (uid, names))

    conn.close()
    log()
    if failures:
        log("[FAIL] not every credential could be proved to round-trip:")
        for f in failures:
            log("   - " + f)
        sys.exit(1)
    if args.dry_run:
        log("[DONE] dry run - %d value(s) would be encrypted, %d already were." % (changed, skipped))
        return
    log("[DONE] %d value(s) encrypted and verified; %d already encrypted." % (changed, skipped))


if __name__ == "__main__":
    main()
