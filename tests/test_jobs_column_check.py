"""C15 (2026-11-24): db._jobs_has_column must not send SQLite's PRAGMA to Postgres."""
import db
import dbdriver


class _Rec:
    def __init__(self, found):
        self.sql, self.found = [], found

    def execute(self, sql, params=()):
        self.sql.append(sql)
        found = self.found

        class C:
            def fetchone(self_inner):
                return (1,) if found else None
        return C()


def test_postgres_asks_information_schema(monkeypatch):
    monkeypatch.setattr(dbdriver, "dialect_of", lambda conn: dbdriver.POSTGRES)
    conn = _Rec(True)
    assert db._jobs_has_column(conn, "applied_via") is True
    assert not any("PRAGMA" in q for q in conn.sql)
    assert "information_schema.columns" in conn.sql[0]
    assert db._jobs_has_column(_Rec(False), "nope") is False


def test_sqlite_still_works(tmp_path):
    import sqlite3
    c = sqlite3.connect(str(tmp_path / "t.db"))
    c.execute("CREATE TABLE jobs (id INTEGER, applied_via TEXT)")
    assert db._jobs_has_column(c, "applied_via") is True
    assert db._jobs_has_column(c, "missing") is False
