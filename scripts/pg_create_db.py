#!/usr/bin/env python3
"""
pg_create_db.py - create an empty database on the Railway Postgres server.

Why this exists: RAILWAY_RUNBOOK.md step 2.2 first said `railway connect
Postgres` and then "type CREATE DATABASE at the psql prompt". `railway connect`
needs the psql client installed locally, and without it the command exits and
drops you back in PowerShell - where the SQL line is then read as a PowerShell
command ("The term 'CREATE' is not recognized"). Found 2026-09-21 on Eran's
machine. This does the same one statement through psycopg, which the migration
script already needs, so there is nothing new to install.

Usage (PowerShell, from the repo root):
    railway run --service Postgres python scripts/pg_create_db.py jobhunter_prod

Only ever CREATEs. It never drops, never touches an existing database, and says
so when the database is already there.
"""
import os
import re
import sys
from urllib.parse import urlparse


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: python scripts/pg_create_db.py <database_name>")
    name = sys.argv[1].strip()
    # CREATE DATABASE cannot take a bind parameter, so the name is validated
    # instead: lowercase letters, digits and underscores only.
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", name):
        sys.exit("[FAIL] %r is not a plain database name (a-z, 0-9, _)." % name)

    # PUBLIC first: `railway run` executes on YOUR machine, and the private
    # DATABASE_URL points at postgres.railway.internal, which only resolves
    # inside Railway.
    url = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL") or ""
    if not url:
        sys.exit("[FAIL] no DATABASE_PUBLIC_URL / DATABASE_URL. "
                 "Run it under: railway run --service Postgres python scripts/pg_create_db.py %s" % name)
    host = urlparse(url).hostname or ""
    if host.endswith(".railway.internal"):
        sys.exit("[FAIL] only the private URL (%s) is available, and it does not resolve "
                 "outside Railway. Enable the Postgres service's public networking "
                 "(TCP proxy) so DATABASE_PUBLIC_URL is set." % host)

    try:
        import psycopg
    except ImportError:
        sys.exit("[FAIL] psycopg is not installed: pip install \"psycopg[binary]\"")

    # Host AND port: every Railway environment's public proxy is the same host
    # (junction.proxy.rlwy.net) on a different port, so the host alone cannot
    # tell production's server from staging's. On 2026-09-21 this created
    # jobhunter_prod on the STAGING server because the CLI was linked there,
    # and the output looked identical to a correct run.
    print("server : %s:%s" % (host, urlparse(url).port))
    # autocommit: CREATE DATABASE refuses to run inside a transaction block.
    with psycopg.connect(url, autocommit=True, connect_timeout=15) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s",
                              (name,)).fetchone()
        if exists:
            print("[OK] database %s already exists - left untouched." % name)
        else:
            conn.execute('CREATE DATABASE "%s"' % name)
            print("[OK] created database %s" % name)
        names = [r[0] for r in conn.execute(
            "SELECT datname FROM pg_database WHERE NOT datistemplate ORDER BY datname")]
    print("databases on this server: %s" % ", ".join(names))
    if name.endswith("_prod") and "jobhunter_staging" in names:
        print("[WARN] jobhunter_staging is on this server too. If production has its own "
              "Postgres service, this is the STAGING server - check `railway status`.")


if __name__ == "__main__":
    main()
