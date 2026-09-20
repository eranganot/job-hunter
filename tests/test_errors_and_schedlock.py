"""
tests/test_errors_and_schedlock.py - the last two Phase 3 items.

Both exist to prevent something SILENT, which is why both are tested for their
behaviour when switched off as carefully as when switched on:

  errors.py   - today an unhandled exception prints a traceback into the Railway
                log and tells nobody. Fine with nine friendly users; the moment
                a stranger can sign up, a 500 on their first screen is invisible
                until they give up, and they will not write in.

  schedlock.py- the queue stops a duplicate SEARCH, but the SCHEDULER decides
                whether a run happens at all. Two instances both seeing the same
                minute would double every user's Gemini spend before the queue
                got a say.
"""
import pytest

import errors
import schedlock


# ── errors: must be invisible when unconfigured, and never raise ────────────

@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("JH_SENTRY_DSN", raising=False)
    errors._client, errors._state = None, "unconfigured"
    schedlock.reset_for_tests()
    yield
    errors._client, errors._state = None, "unconfigured"
    schedlock.reset_for_tests()


def test_no_dsn_means_disabled_not_broken():
    assert "disabled" in errors.init()
    assert errors.status()["reporting"] is False


def test_capture_never_raises_when_unconfigured():
    """The whole point: a reporter that can throw turns one broken request into
    two, and the second happens inside the handler apologising for the first."""
    try:
        raise ValueError("boom")
    except ValueError as e:
        assert errors.capture(e, where="test", path="/api/x") is False


def test_capture_never_raises_even_if_the_client_misbehaves(monkeypatch):
    class Exploding:
        def push_scope(self):
            raise RuntimeError("sentry is having a day")
    errors._client = Exploding()
    try:
        raise ValueError("boom")
    except ValueError as e:
        assert errors.capture(e, where="test") is False     # swallowed, not raised


def test_a_query_string_is_never_sent():
    """Paths are fine; query strings carry ids and search text, and a crash
    report is not a place to start keeping user content."""
    sent = {}

    class Scope:
        def set_tag(self, k, v): sent[k] = v
        def set_user(self, u): sent["user"] = u

    class Fake:
        def push_scope(self):
            import contextlib
            return contextlib.nullcontext(Scope())
        def capture_exception(self, e): sent["exc"] = e

    errors._client = Fake()
    try:
        raise ValueError("boom")
    except ValueError as e:
        assert errors.capture(e, where="do_GET", path="/api/jobs?status=new&q=secret", user_id=7)
    assert sent["path"] == "/api/jobs", sent["path"]
    assert "secret" not in str(sent), sent


def test_status_distinguishes_configured_from_working():
    """"The DSN is set" and "reporting works" are different claims."""
    assert errors.status()["state"] == "unconfigured"
    errors.init()
    assert errors.status()["state"].startswith("disabled")


# ── schedlock ───────────────────────────────────────────────────────────────

def _never_called():
    raise AssertionError("the database was touched on a non-Postgres backend")


def test_sqlite_always_ticks_without_touching_the_database():
    """One file, one writer, no second instance to race. Inventing a lock table
    there would add failure modes to protect against a race that cannot happen."""
    assert schedlock.acquire(_never_called, "sqlite") is True


def test_postgres_takes_the_lock_once_and_caches_it():
    calls = []

    class Conn:
        def execute(self, sql):
            calls.append(sql)
            class R:
                def fetchone(self_inner): return (True,)
            return R()
        def close(self): calls.append("close")

    assert schedlock.acquire(lambda: Conn(), "postgres") is True
    assert schedlock.acquire(lambda: Conn(), "postgres") is True     # cached
    assert len([c for c in calls if "pg_try_advisory_lock" in str(c)]) == 1, calls
    assert "close" not in calls, "the connection was returned - that releases the lock"


def test_a_second_instance_does_not_tick():
    class Conn:
        def execute(self, sql):
            class R:
                def fetchone(self_inner): return (False,)
            return R()
        def close(self): pass

    assert schedlock.acquire(lambda: Conn(), "postgres") is False


def test_a_broken_lock_fails_open_rather_than_stopping_the_clock():
    """A safety net that becomes an outage is worse than no safety net. On a
    single-instance deploy the queue still refuses a second run per user."""
    def boom():
        raise RuntimeError("no such function: pg_try_advisory_lock")
    assert schedlock.acquire(boom, "postgres") is True


def test_it_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("JH_SCHED_LOCK", "0")
    assert schedlock.acquire(_never_called, "postgres") is True


def test_the_key_is_a_written_down_constant():
    """Computing it from a hash of a string would move the lock silently the
    day that string or the hash changed, and let two instances tick at once."""
    assert isinstance(schedlock.LOCK_KEY, int)
    assert schedlock.LOCK_KEY == 8_143_552_900_117_001


def test_health_can_answer_whether_this_instance_runs_the_clock():
    """Otherwise "my daily search stopped" and "the other instance holds the
    lock" look identical from outside."""
    st = schedlock.status()
    assert set(st) == {"enabled", "holds_lock", "key"}
    assert st["holds_lock"] is None          # not yet asked
    schedlock.acquire(_never_called, "sqlite")
    assert schedlock.status()["holds_lock"] is True
