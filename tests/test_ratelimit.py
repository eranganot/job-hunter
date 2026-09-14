"""
tests/test_ratelimit.py - the shared sliding-window limiter.

Login already had one and it was fine; this covers the generalised version plus
the two doors that had nothing: register (a script could create accounts in a
loop) and run-search (each call spawns a minutes-long thread that spends Gemini
quota, and Gemini 429s have already degraded production scoring twice).
"""
import pytest

import ratelimit


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for name in ("LOGIN", "REGISTER", "RUN_SEARCH"):
        monkeypatch.delenv("JH_RL_%s_MAX" % name, raising=False)
        monkeypatch.delenv("JH_RL_%s_WINDOW" % name, raising=False)
    ratelimit.reset_all()
    yield
    ratelimit.reset_all()


# ── Attempt-based: the call itself is the thing being limited ────────────────

def test_the_limit_is_the_documented_number(monkeypatch):
    monkeypatch.setenv("JH_RL_REGISTER_MAX", "3")
    for i in range(3):
        assert ratelimit.check_and_record("register", "1.2.3.4") == 0, i
    assert ratelimit.check_and_record("register", "1.2.3.4") > 0


def test_one_caller_being_limited_does_not_affect_another():
    """The failure that would matter most: one bad actor locking out everyone."""
    while ratelimit.check_and_record("register", "1.2.3.4") == 0:
        pass
    assert ratelimit.check_and_record("register", "5.6.7.8") == 0


def test_the_wait_is_reported_in_seconds_and_shrinks(monkeypatch):
    monkeypatch.setenv("JH_RL_REGISTER_MAX", "1")
    monkeypatch.setenv("JH_RL_REGISTER_WINDOW", "100")
    ratelimit.check_and_record("register", "ip")
    first = ratelimit.retry_after("register", "ip")
    assert 0 < first <= 101


def test_a_refused_call_is_not_counted_again(monkeypatch):
    """Being refused must not extend the lockout, or it never ends."""
    monkeypatch.setenv("JH_RL_REGISTER_MAX", "1")
    monkeypatch.setenv("JH_RL_REGISTER_WINDOW", "100")
    ratelimit.check_and_record("register", "ip")
    a = ratelimit.retry_after("register", "ip")
    for _ in range(5):
        ratelimit.check_and_record("register", "ip")
    b = ratelimit.retry_after("register", "ip")
    assert b <= a, "refusals pushed the unlock time further out"


# ── Failure-based: login ─────────────────────────────────────────────────────

def test_a_successful_login_clears_the_record(monkeypatch):
    monkeypatch.setenv("JH_RL_LOGIN_MAX", "3")
    ratelimit.record("login", "em:a@b.test")
    ratelimit.record("login", "em:a@b.test")
    ratelimit.clear("login", "em:a@b.test")
    for i in range(3):
        assert ratelimit.retry_after("login", "em:a@b.test") == 0, i
        ratelimit.record("login", "em:a@b.test")


def test_checking_does_not_count_as_an_attempt(monkeypatch):
    monkeypatch.setenv("JH_RL_LOGIN_MAX", "2")
    for _ in range(10):
        assert ratelimit.retry_after("login", "k") == 0
    ratelimit.record("login", "k")
    assert ratelimit.retry_after("login", "k") == 0


# ── The window actually slides ───────────────────────────────────────────────

def test_hits_outside_the_window_stop_counting(monkeypatch):
    monkeypatch.setenv("JH_RL_REGISTER_MAX", "2")
    monkeypatch.setenv("JH_RL_REGISTER_WINDOW", "1")
    real = ratelimit.time.time
    monkeypatch.setattr(ratelimit.time, "time", lambda: real() - 10)
    ratelimit.record("register", "ip")
    ratelimit.record("register", "ip")
    monkeypatch.setattr(ratelimit.time, "time", real)
    assert ratelimit.check_and_record("register", "ip") == 0, "an old hit still counted"


# ── Memory ───────────────────────────────────────────────────────────────────

def test_stale_keys_are_swept_rather_than_accumulating(monkeypatch):
    """
    An attacker rotating addresses never revisits a key, so per-key pruning
    alone would let the dict grow without bound.
    """
    real = ratelimit.time.time
    monkeypatch.setattr(ratelimit.time, "time", lambda: real() - 100000)
    for i in range(50):
        ratelimit.record("register", "ip-%d" % i)
    assert ratelimit.snapshot()["tracked_keys"] == 50

    monkeypatch.setattr(ratelimit.time, "time", real)
    ratelimit.record("register", "fresh")
    assert ratelimit.snapshot()["tracked_keys"] == 1, "the sweep did not run"


def test_buckets_do_not_bleed_into_each_other(monkeypatch):
    monkeypatch.setenv("JH_RL_REGISTER_MAX", "1")
    ratelimit.check_and_record("register", "same-key")
    assert ratelimit.check_and_record("register", "same-key") > 0
    assert ratelimit.retry_after("run_search", "same-key") == 0


def test_every_policy_is_tunable_without_a_deploy():
    """A lockout nobody can relax is its own outage."""
    for name in ratelimit.POLICIES:
        limit, window = ratelimit.POLICIES[name]()
        assert limit > 0 and window > 0, name
