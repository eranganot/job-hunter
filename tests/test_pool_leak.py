"""
tests/test_pool_leak.py - the 2026-09-25 production outage, pinned.

What happened: "pass on a job" with a reason raised on Postgres
(`column reference "count" is ambiguous`, db.record_pass_reason_stat). The
reject handler has no try/finally, so each failure skipped conn.close() and
the pooled connection never went back. Nine of those plus schedlock's
permanent lock connection filled the 10-slot pool, and every request after
that waited 20s and returned "couldn't get a connection after 20.00 sec".

Three separate guarantees, each tested on its own so one cannot hide behind
another:
  1. the upsert works on Postgres (and still on SQLite);
  2. a connection a request leaks is handed back at the end of the request,
     so a buggy route costs one failed request, not the process;
  3. /api/health reports pool state (503) instead of hanging when the pool
     is exhausted.

The Postgres cases need JH_TEST_PG_URL (as tests/test_pg_integration.py).
The route-level ones run in a subprocess: pool size is read at import, and
app.py wires its database at import time, so a fresh interpreter is the only
way to get a small pool without disturbing every other test module.
"""
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import textwrap
import uuid

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
PG_URL = os.environ.get("JH_TEST_PG_URL")
needs_pg = pytest.mark.skipif(not PG_URL, reason="no Postgres URL (set JH_TEST_PG_URL)")


# ── 1. the upsert ────────────────────────────────────────────────────────────

def test_pass_reason_upsert_counts_on_sqlite(tmp_path):
    import db as database
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    conn.execute("CREATE TABLE pass_reason_stats (user_id INTEGER, reason TEXT, "
                 "count INTEGER, last_hit_date TEXT, UNIQUE(user_id, reason))")
    for _ in range(3):
        database.record_pass_reason_stat(conn, 1, "Wrong location")
    assert conn.execute("SELECT count FROM pass_reason_stats").fetchone()[0] == 3


@needs_pg
def test_pass_reason_upsert_counts_on_postgres():
    import db as database
    import dbdriver
    table = "prs_%s" % uuid.uuid4().hex[:8]
    conn = dbdriver.connect_postgres(PG_URL, pooled=False)
    try:
        # Run the real function against a schema-local copy of the table, so the
        # test never touches a pass_reason_stats another test may own.
        conn.execute("CREATE SCHEMA %s" % table)
        conn.execute("SET search_path TO %s" % table)
        conn.execute("CREATE TABLE pass_reason_stats (user_id INTEGER, reason TEXT, "
                     "count INTEGER, last_hit_date TEXT, UNIQUE(user_id, reason))")
        for _ in range(3):
            database.record_pass_reason_stat(conn, 1, "Wrong location")
        assert conn.execute("SELECT count FROM pass_reason_stats").fetchone()[0] == 3
    finally:
        try:
            conn.execute("SET search_path TO public")
            conn.execute("DROP SCHEMA IF EXISTS %s CASCADE" % table)
        finally:
            conn.close()


# ── 2. leaked connections come back ──────────────────────────────────────────

@needs_pg
def test_release_returns_connections_the_code_forgot(monkeypatch):
    import dbdriver
    # A URL no other test uses, so _get_pool builds a fresh pool with these limits.
    url = PG_URL + ("&" if "?" in PG_URL else "?") + "application_name=leak_%s" % uuid.uuid4().hex[:6]
    monkeypatch.setattr(dbdriver, "POOL_MIN", 0)
    monkeypatch.setattr(dbdriver, "POOL_MAX", 2)
    monkeypatch.setattr(dbdriver, "POOL_TIMEOUT", 1.0)
    try:
        dbdriver.release_thread_connections()          # start clean
        leaked = [dbdriver.connect_postgres(url, pooled=True) for _ in range(2)]
        # Pool is now full of connections nobody will close.
        with pytest.raises(Exception, match="couldn't get a connection"):
            dbdriver.connect_postgres(url, pooled=True)
        assert dbdriver.release_thread_connections("test") == 2
        assert all(c._returned for c in leaked)
        # And the pool serves again, both slots.
        a = dbdriver.connect_postgres(url, pooled=True)
        b = dbdriver.connect_postgres(url, pooled=True)
        a.close(); b.close()
        assert dbdriver.release_thread_connections("test") == 0   # nothing left over
    finally:
        pool = dbdriver._POOLS.pop(url, None)
        if pool is not None:
            pool.close()


@needs_pg
def test_detached_connection_survives_release(monkeypatch):
    """schedlock's advisory lock lives on its session; release must not take it."""
    import dbdriver
    url = PG_URL + ("&" if "?" in PG_URL else "?") + "application_name=detach_%s" % uuid.uuid4().hex[:6]
    monkeypatch.setattr(dbdriver, "POOL_MIN", 0)
    try:
        dbdriver.release_thread_connections()
        c = dbdriver.connect_postgres(url, pooled=True).detach()
        assert dbdriver.release_thread_connections("test") == 0
        assert not c._returned
        assert c.execute("SELECT 1").fetchone()[0] == 1
        c.close()
    finally:
        pool = dbdriver._POOLS.pop(url, None)
        if pool is not None:
            pool.close()


def test_schedlock_detaches_the_lock_connection():
    import schedlock
    calls = []

    class _Conn:
        def execute(self, _sql):
            class _R:
                def fetchone(self_inner):
                    return (True,)
            return _R()

        def detach(self):
            calls.append("detach")
            return self

        def close(self):
            calls.append("close")

    schedlock.reset_for_tests()
    try:
        assert schedlock.acquire(lambda: _Conn(), "postgres") is True
        assert calls == ["detach"]
    finally:
        schedlock._conn = None
        schedlock.reset_for_tests()


# ── route level, in a fresh interpreter ──────────────────────────────────────

_SCRIPT = textwrap.dedent(r'''
    import json, os, sys, tempfile, threading, time
    sys.path.insert(0, os.environ["JH_ROOT"])
    tmp = tempfile.mkdtemp()
    os.environ.update(DATABASE_PATH=os.path.join(tmp, "t.db"),
                      UPLOADS_DIR=os.path.join(tmp, "up"),
                      ADMIN_EMAIL="admin@example.test", JH_SCHED_LOCK="0")
    os.makedirs(os.environ["UPLOADS_DIR"], exist_ok=True)
    import db as database
    import app as A, dbdriver
    A.notify_admin_new_user = lambda **k: None
    A.deliver_notification = lambda *a, **k: None
    database.init_db()
    from tests.test_routes import Client, _Server
    srv = _Server(("127.0.0.1", 0), A.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    c = Client(srv.server_address[1])
    st = c.post_form("/register", {"name": "a", "email": "a@example.test",
                                   "password": "correct-horse-1", "password2": "correct-horse-1"})[0]
    assert st == 302, st
    out = {}
    mode = os.environ["MODE"]
    if mode == "leak":
        # Simulate ANY route whose exception skips conn.close(): the very
        # failure that caused the outage, whatever raises next time.
        def _boom(*_a, **_k):
            raise RuntimeError("simulated failure after get_db()")
        database.record_pass_reason_stat = _boom
        conn = database.get_db()
        uid = conn.execute("SELECT id FROM users WHERE email='a@example.test'").fetchone()[0]
        ids = [conn.execute(
            "INSERT INTO jobs (user_id,title,company,location,url,status,match_score) "
            "VALUES (?,?,?,?,?,?,?) RETURNING id",
            (uid, "PM", "Acme", "TLV", "https://x.test/%d-%f" % (i, time.time()), "new", 90)
        ).fetchone()[0] for i in range(6)]
        conn.close()
        out["rejects"] = []
        for j in ids:
            t = time.time()
            s = c.post_json("/api/jobs/%d/reject" % j, {"reason": "Wrong location"})[0]
            out["rejects"].append([s, round(time.time() - t, 2)])
        t = time.time()
        s, _, body = c.get("/api/health")
        out["health"] = [s, round(time.time() - t, 2), json.loads(body)]
    elif mode == "exhausted":
        # Hold every slot, as the leaked connections did in production.
        held = [dbdriver.connect_postgres(database.DATABASE_URL or os.environ["DATABASE_URL"]).detach()
                for _ in range(int(os.environ["JH_PG_POOL_MAX"]))]
        t = time.time()
        s, _, body = c.get("/api/health")
        out["health"] = [s, round(time.time() - t, 2), json.loads(body)]
        for h in held:
            h.close()
    print("RESULT " + json.dumps(out))
''')


def _run(mode, dbname):
    base = PG_URL.rsplit("/", 1)[0]
    admin = subprocess.run(
        [sys.executable, "-c",
         "import psycopg,sys; c=psycopg.connect(sys.argv[1], autocommit=True); "
         "c.execute('DROP DATABASE IF EXISTS %s'); c.execute('CREATE DATABASE %s')" % (dbname, dbname),
         PG_URL], capture_output=True, text=True)
    assert admin.returncode == 0, admin.stderr
    # Start from a clean slate: earlier test modules set JH_* / DATABASE_*
    # variables in os.environ (JH_PG_POOL=0 among them), and inheriting those
    # silently turned the pool off and made the exhaustion test pass vacuously.
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("JH_", "DATABASE_", "DB_BACKEND"))}
    env.update(JH_ROOT=str(ROOT), MODE=mode, DB_BACKEND="postgres",
               DATABASE_URL="%s/%s" % (base, dbname), JH_PG_DATABASE=dbname,
               JH_PG_POOL="1", JH_PG_POOL_MIN="0", JH_PG_POOL_MAX="3",
               JH_PG_POOL_TIMEOUT="2")
    try:
        p = subprocess.run([sys.executable, "-c", _SCRIPT], env=env, cwd=str(ROOT),
                           capture_output=True, text=True, timeout=120)
        line = [l for l in p.stdout.splitlines() if l.startswith("RESULT ")]
        assert line, "no result\nSTDOUT:\n%s\nSTDERR:\n%s" % (p.stdout[-3000:], p.stderr[-3000:])
        return json.loads(line[-1][len("RESULT "):]), p.stdout + p.stderr
    finally:
        subprocess.run(
            [sys.executable, "-c",
             "import psycopg,sys; c=psycopg.connect(sys.argv[1], autocommit=True); "
             "c.execute('DROP DATABASE IF EXISTS %s WITH (FORCE)')" % dbname, PG_URL],
            capture_output=True, text=True)


@needs_pg
def test_failing_route_cannot_exhaust_the_pool():
    """Twice as many failing requests as the pool has slots, then health.

    Before the fix: the third reject hung for the pool timeout and every request
    after it failed - the production outage, at pool size 3 instead of 10.
    """
    out, log = _run("leak", "jh_leak_%s" % uuid.uuid4().hex[:6])
    # Each reject fails (the simulated error) - but fast, every time.
    assert all(s == 500 and secs < 1.5 for s, secs in out["rejects"]), out["rejects"]
    status, secs, body = out["health"]
    assert status == 200 and secs < 1.5, out["health"]
    assert body["db_leaks_reclaimed"]["count"] >= len(out["rejects"]), body
    assert "reclaimed" in log and "WARNING" in log   # and it said so


@needs_pg
def test_health_reports_pool_state_when_exhausted():
    out, _ = _run("exhausted", "jh_exh_%s" % uuid.uuid4().hex[:6])
    status, secs, body = out["health"]
    assert status == 503, out["health"]
    assert body["status"] == "db_unavailable"
    pool = next(iter(body["db_pool"].values()))
    assert pool["available"] == 0 and pool["size"] == 3, body
