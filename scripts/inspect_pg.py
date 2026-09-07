#!/usr/bin/env python3
"""
inspect_pg.py - read-only look at the Railway Postgres before Phase 2b uses it.

`railway connect Postgres` needs psql installed. This needs only Python plus
psycopg, and `railway run` injects the service's connection variables:

    pip install "psycopg[binary]"
    railway run --service Postgres python scripts/inspect_pg.py

It prints server version, the databases on the instance with their sizes, and
the tables in the current database with estimated row counts. It prints NO row
data and NO credentials - the output is safe to paste into a chat.

Nothing is created, altered or dropped.
"""
import os
import sys
from urllib.parse import urlparse

# Railway's internal host (*.railway.internal) only resolves inside Railway, so
# from a laptop prefer the public proxy URL.
URL_VARS = ("DATABASE_PUBLIC_URL", "POSTGRES_PUBLIC_URL", "DATABASE_URL", "POSTGRES_URL")


def find_url():
    for var in URL_VARS:
        val = os.environ.get(var)
        if val:
            return var, val
    return None, None


def main():
    try:
        import psycopg
    except ImportError:
        sys.exit('[FAIL] psycopg is not installed. Run:\n'
                 '    pip install "psycopg[binary]"\n'
                 'then re-run: railway run --service Postgres python scripts/inspect_pg.py')

    var, url = find_url()
    if not url:
        sys.exit("[FAIL] no connection URL in the environment.\n"
                 "       Run this through Railway so the variables are injected:\n"
                 "           railway run --service Postgres python scripts/inspect_pg.py\n"
                 f"       (looked for: {', '.join(URL_VARS)})")

    host = urlparse(url).hostname or "?"
    print(f"[..] using {var} -> host {host}")
    if host.endswith(".railway.internal"):
        print("[!!] that host only resolves inside Railway. If this hangs, the public")
        print("     URL is the one you want - check the Postgres service's variables")
        print("     for DATABASE_PUBLIC_URL and export it before running.")

    # Layered check: DNS, then raw TCP, then Postgres. A bare "connection
    # timeout expired" cannot tell you whether the service is asleep, the TCP
    # proxy is off, or your network blocks the port - these three can.
    import socket
    parsed = urlparse(url)
    port = parsed.port or 5432

    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        addrs = sorted({i[4][0] for i in infos})
        print(f"[OK] DNS: {host} -> {', '.join(addrs)}")
    except Exception as e:
        sys.exit(f"[FAIL] DNS lookup failed for {host}: {e}\n"
                 "       The hostname is wrong or unreachable from this network.")

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10)
    tcp_err = None
    try:
        sock.connect((host, port))
        print(f"[OK] TCP: connected to {host}:{port}")
    except Exception as e:
        tcp_err = e
    finally:
        sock.close()

    if tcp_err is not None:
        print(f"[FAIL] TCP: cannot reach {host}:{port} - {tcp_err}")
        print("\n       DNS resolves but nothing accepts a connection. In order of likelihood:")
        print("       1. The Postgres service is stopped/asleep - check it is Running in the")
        print("          Railway dashboard (a volume showing 'Ready' does not mean the service is up).")
        print("       2. The TCP proxy is not enabled for that service - Postgres service ->")
        print("          Settings -> Networking -> Public Networking / TCP Proxy.")
        print(f"       3. Your network blocks outbound port {port}. Test from another network,")
        print(f"          or from PowerShell: Test-NetConnection {host} -Port {port}")
        sys.exit(1)

    try:
        conn = psycopg.connect(url, connect_timeout=20)
    except Exception as e:
        sys.exit(f"[FAIL] TCP works but Postgres refused the session: {e}\n"
                 "       That points at credentials or the database name in the URL,\n"
                 "       not at connectivity.")

    with conn, conn.cursor() as cur:
        cur.execute("SELECT version()")
        print("\n=== server ===")
        print("  " + cur.fetchone()[0].split(",")[0])

        cur.execute("SELECT current_database(), current_user")
        db, user = cur.fetchone()
        print(f"  connected to database '{db}' as '{user}'")

        print("\n=== databases on this instance ===")
        cur.execute("""
            SELECT datname, pg_size_pretty(pg_database_size(datname)) AS size
            FROM pg_database
            WHERE datistemplate = false
            ORDER BY pg_database_size(datname) DESC
        """)
        for name, size in cur.fetchall():
            print(f"  {name:<28} {size}")

        print(f"\n=== tables in '{db}' ===")
        cur.execute("""
            SELECT n.nspname   AS schema,
                   c.relname   AS table,
                   c.reltuples::bigint AS est_rows,
                   pg_size_pretty(pg_total_relation_size(c.oid)) AS size
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind = 'r'
              AND n.nspname NOT IN ('pg_catalog', 'information_schema')
            ORDER BY pg_total_relation_size(c.oid) DESC
        """)
        rows = cur.fetchall()
        if not rows:
            print("  (no user tables - this database is empty)")
        else:
            print(f"  {'schema':<12} {'table':<34} {'~rows':>10}  size")
            for schema, table, est, size in rows:
                print(f"  {schema:<12} {table:<34} {est:>10}  {size}")

        # Does anything Job-Hunter-shaped already live here?
        cur.execute("""
            SELECT COUNT(*) FROM information_schema.tables
            WHERE table_schema NOT IN ('pg_catalog','information_schema')
              AND table_name IN ('users','jobs','sessions','user_profiles','activity_log')
        """)
        overlap = cur.fetchone()[0]
        print(f"\n=== Phase 2b readiness ===")
        print(f"  Job-Hunter-shaped tables already present: {overlap}")
        if overlap:
            print("  -> a migration into THIS database would collide. Use a separate")
            print("     database on the instance (CREATE DATABASE jobhunter_prod).")
        else:
            print("  -> no collision with Job Hunter's table names in this database.")

    conn.close()
    print("\n[DONE] read-only inspection complete - nothing was modified.")


if __name__ == "__main__":
    main()
