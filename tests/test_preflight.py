"""
tests/test_preflight.py - the guard that should have existed on 2026-09-07.

That day, `DB_BACKEND=postgres` was set on the production service. Railway had
already injected a DATABASE_URL pointing at its default `railway` database, so
one variable was enough: the app connected there, ran its own migrations, and
served nine users an empty account while /api/health reported "ok".

The first test below is that exact configuration. Everything else here exists to
make sure the guard refuses the accident without also refusing the real cutover.

These run offline - the Postgres connection is faked - because a guard that only
gets tested when someone remembers to set JH_TEST_PG_URL is a guard that stops
being tested.
"""
import sqlite3

import pytest

import db as database
import dbdriver


@pytest.fixture(autouse=True)
def clean_globals(monkeypatch):
    """db is a module with module-level state; leave it as we found it."""
    monkeypatch.setattr(database, "BACKEND_REFUSAL", None, raising=False)
    monkeypatch.setattr(database, "DATABASE_URL", None, raising=False)
    monkeypatch.setattr(database, "DB_PATH", None, raising=False)
    monkeypatch.delenv("DB_BACKEND", raising=False)
    monkeypatch.delenv("JH_PG_DATABASE", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    yield
    database.BACKEND_REFUSAL = None


def _sqlite_with_users(tmp_path, n):
    path = str(tmp_path / "jobs.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT)")
    for i in range(n):
        conn.execute("INSERT INTO users (email) VALUES (?)", ("u%d@example.test" % i,))
    conn.commit()
    conn.close()
    return path


def _fake_pg(monkeypatch, user_count, url_seen=None):
    """A stand-in Postgres holding `user_count` users, or raising if None."""
    class FakeCur:
        def __init__(self, n):
            self._n = n

        def fetchone(self):
            if self._n is None:
                raise RuntimeError('relation "users" does not exist')
            return (self._n,)

    class FakeConn:
        def execute(self, sql, params=()):
            return FakeCur(user_count)

        def close(self):
            pass

    def fake_connect(url, connect_timeout=15, pooled=True):
        if url_seen is not None:
            url_seen.append(url)
        if user_count == "unreachable":
            raise OSError("connection refused")
        return FakeConn()

    monkeypatch.setattr(dbdriver, "connect_postgres", fake_connect)


# ── The incident ──────────────────────────────────────────────────────────────

def test_db_backend_alone_does_not_serve_an_inherited_database(tmp_path, monkeypatch):
    """
    The 2026-09-07 configuration exactly: DB_BACKEND=postgres, an injected
    DATABASE_URL nobody chose, an empty target, real data in SQLite.
    """
    monkeypatch.setenv("DB_BACKEND", "postgres")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@postgres.railway.internal:5432/railway")
    database.DB_PATH = _sqlite_with_users(tmp_path, 9)
    _fake_pg(monkeypatch, 0)

    reason = database.preflight()

    assert reason, "the guard let the incident through"
    assert "JH_PG_DATABASE" in reason
    assert database.backend() == dbdriver.SQLITE, \
        "refused the target but still routed traffic to it"


def test_a_refusal_keeps_serving_the_real_data(tmp_path, monkeypatch):
    """Falling back is only worth anything if the fallback has the users in it."""
    monkeypatch.setenv("DB_BACKEND", "postgres")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host:5432/railway")
    database.DB_PATH = _sqlite_with_users(tmp_path, 9)
    _fake_pg(monkeypatch, 0)

    database.preflight()
    conn = database.get_db()
    try:
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 9
    finally:
        conn.close()


# ── Check 1: the database must be named on purpose ────────────────────────────

def test_unset_jh_pg_database_is_refused_even_when_the_target_has_data(tmp_path, monkeypatch):
    """Naming it is the point. A populated target you did not choose is still one you did not choose."""
    monkeypatch.setenv("DB_BACKEND", "postgres")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host:5432/railway")
    database.DB_PATH = _sqlite_with_users(tmp_path, 9)
    _fake_pg(monkeypatch, 500)

    assert "JH_PG_DATABASE" in database.preflight()


def test_naming_the_wrong_database_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_BACKEND", "postgres")
    monkeypatch.setenv("JH_PG_DATABASE", "jobhunter_prod")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host:5432/railway")
    database.DB_PATH = _sqlite_with_users(tmp_path, 9)
    _fake_pg(monkeypatch, 9)

    reason = database.preflight()
    assert "jobhunter_prod" in reason and "railway" in reason


def test_missing_database_url_is_refused(monkeypatch):
    monkeypatch.setenv("DB_BACKEND", "postgres")
    assert "DATABASE_URL" in database.preflight()
    assert database.backend() == dbdriver.SQLITE


# ── Check 2: an empty target must not displace a populated volume ─────────────

def test_empty_target_beside_a_populated_volume_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_BACKEND", "postgres")
    monkeypatch.setenv("JH_PG_DATABASE", "jobhunter_prod")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host:5432/jobhunter_prod")
    database.DB_PATH = _sqlite_with_users(tmp_path, 9)
    _fake_pg(monkeypatch, 0)

    reason = database.preflight()
    assert "0 users" in reason and "9" in reason


def test_a_target_with_no_users_table_counts_as_empty(tmp_path, monkeypatch):
    """A virgin database raises on the count; that must read as empty, not as fine."""
    monkeypatch.setenv("DB_BACKEND", "postgres")
    monkeypatch.setenv("JH_PG_DATABASE", "jobhunter_prod")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host:5432/jobhunter_prod")
    database.DB_PATH = _sqlite_with_users(tmp_path, 9)
    _fake_pg(monkeypatch, None)

    assert database.preflight()


def test_an_unreachable_target_is_refused_rather_than_crashing(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_BACKEND", "postgres")
    monkeypatch.setenv("JH_PG_DATABASE", "jobhunter_prod")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host:5432/jobhunter_prod")
    database.DB_PATH = _sqlite_with_users(tmp_path, 9)
    _fake_pg(monkeypatch, "unreachable")

    assert "cannot reach" in database.preflight()


# ── The cutover must still be allowed ─────────────────────────────────────────

def test_the_real_cutover_is_allowed(tmp_path, monkeypatch):
    """Named database, data present on both sides: this is the migration working."""
    monkeypatch.setenv("DB_BACKEND", "postgres")
    monkeypatch.setenv("JH_PG_DATABASE", "jobhunter_prod")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host:5432/jobhunter_prod")
    database.DB_PATH = _sqlite_with_users(tmp_path, 9)
    _fake_pg(monkeypatch, 9)

    assert database.preflight() == ""
    assert database.backend() == dbdriver.POSTGRES
    assert database.BACKEND_REFUSAL is None


def test_a_fresh_install_with_no_sqlite_file_is_allowed(monkeypatch):
    """Nothing to protect means nothing to refuse - a new deployment must boot."""
    monkeypatch.setenv("DB_BACKEND", "postgres")
    monkeypatch.setenv("JH_PG_DATABASE", "jobhunter_prod")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host:5432/jobhunter_prod")
    database.DB_PATH = "/nonexistent/jobs.db"
    _fake_pg(monkeypatch, 0)

    assert database.preflight() == ""
    assert database.backend() == dbdriver.POSTGRES


def test_sqlite_deployments_never_touch_postgres(monkeypatch):
    """The default path must not connect anywhere, whatever DATABASE_URL says."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host:5432/railway")

    def explode(*a, **k):
        raise AssertionError("preflight connected to Postgres on a SQLite deployment")

    monkeypatch.setattr(dbdriver, "connect_postgres", explode)
    assert database.preflight() == ""
    assert database.backend() == dbdriver.SQLITE


# ── Housekeeping ──────────────────────────────────────────────────────────────

def test_preflight_clears_a_previous_refusal(tmp_path, monkeypatch):
    """A fixed deployment must not stay poisoned by the last boot's refusal."""
    monkeypatch.setenv("DB_BACKEND", "postgres")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host:5432/jobhunter_prod")
    database.DB_PATH = _sqlite_with_users(tmp_path, 9)
    _fake_pg(monkeypatch, 9)

    assert database.preflight()                      # unnamed -> refused
    monkeypatch.setenv("JH_PG_DATABASE", "jobhunter_prod")
    assert database.preflight() == ""                # named -> allowed
    assert database.BACKEND_REFUSAL is None


def test_the_volume_is_opened_read_only(tmp_path, monkeypatch):
    """A guard that can damage the data it protects is worse than no guard."""
    monkeypatch.setenv("DB_BACKEND", "postgres")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host:5432/railway")
    path = _sqlite_with_users(tmp_path, 3)
    database.DB_PATH = path
    _fake_pg(monkeypatch, 0)

    before = open(path, "rb").read()
    database.preflight()
    assert open(path, "rb").read() == before, "preflight modified the SQLite volume"
