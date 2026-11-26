"""The job list and /api/me never hold two pooled connections at once
(test week, 26 Nov 2026: PoolTimeout under 60 concurrent users)."""
from tests.test_routes import Client, _user_id, stack, users  # noqa: F401


def test_release_daily_picks_uses_the_callers_connection(stack, users, monkeypatch):
    import app
    db = stack["db"]
    uid = _user_id(db, "alice@example.test")
    conn = db.get_db()
    try:
        user = dict(conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone())
        calls = []
        real = db.get_db
        monkeypatch.setattr(app.database, "get_db", lambda *a, **k: calls.append(1) or real(*a, **k))
        printed = []
        monkeypatch.setattr("builtins.print", lambda *a, **k: printed.append(" ".join(map(str, a))))
        app._release_daily_picks(conn, user)
    finally:
        conn.close()
    assert calls == [], "a second connection was taken while the caller held one"
    assert not any("could not read the search schedule" in p for p in printed)


def test_api_me_and_jobs_take_one_connection_at_a_time(stack, users, monkeypatch):
    import app
    import threading
    real = app.database.get_db
    state = {"open": 0, "max": 0}
    lock = threading.Lock()

    class Tracked:
        def __init__(self, c): self._c = c
        def __getattr__(self, n): return getattr(self._c, n)
        def close(self):
            with lock: state["open"] -= 1
            return self._c.close()

    def tracked(*a, **k):
        with lock:
            state["open"] += 1
            state["max"] = max(state["max"], state["open"])
        return Tracked(real(*a, **k))
    monkeypatch.setattr(app.database, "get_db", tracked)
    for path in ("/api/me", "/api/jobs?status=new"):
        state["max"] = state["open"] = 0
        status, _, _ = users["a"].get(path)
        assert status == 200
        assert state["max"] == 1, "%s held %d connections at once" % (path, state["max"])
