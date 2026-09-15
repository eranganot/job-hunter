"""
tests/test_gemini_budget.py - the daily spend ceiling and the ledger behind it.

The thing being protected is money, and the failure mode is silence: before
gemini.py, ten call sites spent Gemini quota and not one of them counted. So
these tests care less about the happy path than about the four ways a ceiling
can be present and still not work -

  * it counts calls but the process restarts and the day starts over,
  * it blocks but never tells anyone,
  * it depends on a response field that one day is missing,
  * it guards nine doors and a tenth call site walks past it.

The last one is checked by tests/test_no_unmetered_gemini.py, which reads the
source. The other three are here.
"""
import json
import urllib.error

import pytest

import gemini


class _Resp:
    """Stand-in for the urlopen context manager."""

    def __init__(self, payload):
        self._data = json.dumps(payload).encode()

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


OK_PAYLOAD = {
    "candidates": [{"content": {"parts": [{"text": "hi"}]}}],
    "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 20,
                      "totalTokenCount": 120},
}


@pytest.fixture
def g(monkeypatch):
    """A clean meter with a captured ledger and a captured alert channel."""
    gemini.reset_for_tests()
    rows, alerts = [], []
    gemini.set_ledger_writer(lambda *r: rows.append(r))
    gemini.set_alert_sender(lambda m: alerts.append(m))
    gemini.set_sync_source(lambda day: {})
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    for name in ("GLOBAL_CALLS", "GLOBAL_TOKENS", "USER_CALLS", "USER_TOKENS", "ENFORCE"):
        monkeypatch.delenv("JH_LLM_%s" % name, raising=False)
    gemini.rows, gemini.alerts = rows, alerts
    yield gemini
    gemini.reset_for_tests()
    gemini.set_ledger_writer(None)
    gemini.set_alert_sender(None)
    gemini.set_sync_source(None)


def _ok(monkeypatch, payload=None):
    monkeypatch.setattr(gemini.urllib.request, "urlopen",
                        lambda *a, **k: _Resp(payload or OK_PAYLOAD))


# ── the ledger ───────────────────────────────────────────────────────────────

def test_a_call_is_counted_in_calls_and_in_tokens(g, monkeypatch):
    _ok(monkeypatch)
    g.generate({"contents": []}, purpose="unit", user_id=7)

    assert g.usage()["global"] == {"calls": 1, "tokens": 120}
    assert g.usage(user_id=7)["user"] == {"calls": 1, "tokens": 120}
    (row,) = g.rows
    assert row[2] == "unit" and row[5] == 100 and row[6] == 20 and row[7] == 120


def test_a_response_with_no_usage_metadata_still_counts_the_call(g, monkeypatch):
    """The guard degrades to call-counting rather than to nothing.

    Token accounting depends on a field Google can rename; the ceiling must not.
    """
    _ok(monkeypatch, {"candidates": [{"content": {"parts": [{"text": "hi"}]}}]})
    g.generate({"contents": []}, purpose="unit", user_id=7)

    assert g.usage()["global"] == {"calls": 1, "tokens": 0}


def test_a_failed_call_is_counted_too(g, monkeypatch):
    """A loop of failures is exactly what a ceiling should stop."""
    def _boom(*a, **k):
        raise urllib.error.HTTPError("u", 400, "bad", {}, None)
    monkeypatch.setattr(gemini.urllib.request, "urlopen", _boom)

    with pytest.raises(RuntimeError):
        g.generate({"contents": []}, purpose="unit", user_id=7, retries=0)

    assert g.usage()["global"]["calls"] == 1
    assert g.rows[0][8] == 0            # ok flag


def test_rows_queue_when_the_ledger_is_unavailable_and_go_in_later(g, monkeypatch):
    """A database blip must not lose spend: the ceiling would drift below truth."""
    _ok(monkeypatch)
    g.set_ledger_writer(lambda *r: (_ for _ in ()).throw(RuntimeError("db down")))
    g.generate({"contents": []}, purpose="unit", user_id=7)
    g.generate({"contents": []}, purpose="unit", user_id=7)

    landed = []
    g.set_ledger_writer(lambda *r: landed.append(r))
    g._flush()
    assert len(landed) == 2


# ── the ceilings ─────────────────────────────────────────────────────────────

def test_the_global_ceiling_blocks_the_call_that_would_cross_it(g, monkeypatch):
    monkeypatch.setenv("JH_LLM_GLOBAL_CALLS", "2")
    _ok(monkeypatch)
    g.generate({}, purpose="unit", user_id=1)
    g.generate({}, purpose="unit", user_id=1)

    with pytest.raises(gemini.BudgetExceeded) as e:
        g.generate({}, purpose="unit", user_id=1)
    assert e.value.scope == "global" and e.value.limit == 2


def test_one_user_cannot_spend_another_users_allowance(g, monkeypatch):
    monkeypatch.setenv("JH_LLM_USER_CALLS", "1")
    _ok(monkeypatch)
    g.generate({}, purpose="unit", user_id=1)

    with pytest.raises(gemini.BudgetExceeded):
        g.generate({}, purpose="unit", user_id=1)
    # user 2's budget is untouched
    g.generate({}, purpose="unit", user_id=2)
    assert g.usage(user_id=2)["user"]["calls"] == 1


def test_a_token_ceiling_stops_a_few_enormous_calls(g, monkeypatch):
    """Calls are the abuse unit; tokens are the cost unit. Ten calls of a
    million tokens each pass a call ceiling and should not pass a token one."""
    monkeypatch.setenv("JH_LLM_GLOBAL_TOKENS", "200")
    _ok(monkeypatch)
    g.generate({}, purpose="unit", user_id=1)      # 120 tokens, under
    g.generate({}, purpose="unit", user_id=1)      # 240 tokens, over

    with pytest.raises(gemini.BudgetExceeded) as e:
        g.generate({}, purpose="unit", user_id=1)
    assert e.value.unit == "tokens"


def test_a_ceiling_of_zero_is_off_not_locked_shut(g, monkeypatch):
    """A limit that cannot be relaxed is its own outage."""
    monkeypatch.setenv("JH_LLM_GLOBAL_CALLS", "0")
    monkeypatch.setenv("JH_LLM_USER_CALLS", "0")
    _ok(monkeypatch)
    for _ in range(5):
        g.generate({}, purpose="unit", user_id=1)
    assert g.usage()["global"]["calls"] == 5


# ── the alert ────────────────────────────────────────────────────────────────

def test_crossing_the_ceiling_tells_the_admin(g, monkeypatch):
    monkeypatch.setenv("JH_LLM_GLOBAL_CALLS", "1")
    _ok(monkeypatch)
    g.generate({}, purpose="unit", user_id=1)
    with pytest.raises(gemini.BudgetExceeded):
        g.generate({}, purpose="unit", user_id=1)

    assert len(g.alerts) == 1
    assert "ceiling reached" in g.alerts[0]


def test_a_breach_alerts_once_not_once_per_blocked_call(g, monkeypatch):
    """A breached ceiling blocks every subsequent call; one notification per
    blocked call would be the outage's second act."""
    monkeypatch.setenv("JH_LLM_GLOBAL_CALLS", "1")
    _ok(monkeypatch)
    g.generate({}, purpose="unit", user_id=1)
    for _ in range(5):
        with pytest.raises(gemini.BudgetExceeded):
            g.generate({}, purpose="unit", user_id=1)

    assert len(g.alerts) == 1


def test_observe_mode_alerts_without_blocking(g, monkeypatch):
    """JH_LLM_ENFORCE=0 is the escape hatch for a ceiling set wrongly in
    production: keep the reporting, drop the blocking, no deploy."""
    monkeypatch.setenv("JH_LLM_GLOBAL_CALLS", "1")
    monkeypatch.setenv("JH_LLM_ENFORCE", "0")
    _ok(monkeypatch)
    g.generate({}, purpose="unit", user_id=1)
    g.generate({}, purpose="unit", user_id=1)      # would have been blocked

    assert g.usage()["global"]["calls"] == 2
    assert len(g.alerts) == 1


# ── surviving a restart ──────────────────────────────────────────────────────

def test_a_restart_does_not_hand_the_day_a_fresh_budget(g, monkeypatch):
    """The process most likely to have just restarted is the one stuck in the
    runaway loop the ceiling exists to stop."""
    monkeypatch.setenv("JH_LLM_GLOBAL_CALLS", "10")
    _ok(monkeypatch)
    g.set_sync_source(lambda day: {"global": {"calls": 10, "tokens": 5000},
                                   "users": {1: {"calls": 10, "tokens": 5000}}})
    g.reset_for_tests()                            # the "restart"

    with pytest.raises(gemini.BudgetExceeded):
        g.generate({}, purpose="unit", user_id=1)


def test_a_broken_ledger_does_not_take_gemini_down_with_it(g, monkeypatch):
    """Accounting is not allowed to become an availability dependency."""
    monkeypatch.setenv("JH_LLM_GLOBAL_CALLS", "10")
    _ok(monkeypatch)
    g.set_sync_source(lambda day: (_ for _ in ()).throw(RuntimeError("db down")))

    assert g.generate({}, purpose="unit", user_id=1)["candidates"]


# ── attribution ──────────────────────────────────────────────────────────────

def test_a_bound_run_charges_the_user_whose_run_it_is(g, monkeypatch):
    _ok(monkeypatch)
    with gemini.bind_user(42):
        g.generate({}, purpose="unit")
    assert g.usage(user_id=42)["user"]["calls"] == 1


def test_an_explicit_user_wins_over_the_binding(g, monkeypatch):
    _ok(monkeypatch)
    with gemini.bind_user(42):
        g.generate({}, purpose="unit", user_id=43)
    assert g.usage(user_id=42)["user"]["calls"] == 0
    assert g.usage(user_id=43)["user"]["calls"] == 1


def test_the_binding_is_restored_not_cleared(g, monkeypatch):
    """Nested runs (a run that calls a helper that binds) must not leave the
    outer run's calls attributed to nobody."""
    _ok(monkeypatch)
    with gemini.bind_user(1):
        with gemini.bind_user(2):
            pass
        assert gemini.current_user() == 1


# ── retries ──────────────────────────────────────────────────────────────────

def test_a_429_is_retried_rather_than_degrading_scoring(g, monkeypatch):
    """The two logged prod incidents were single un-retried 429s. Seven of the
    ten old call sites had no backoff at all; they all do now."""
    calls = {"n": 0}

    def _flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.HTTPError("u", 429, "slow down", {}, None)
        return _Resp(OK_PAYLOAD)

    monkeypatch.setattr(gemini.urllib.request, "urlopen", _flaky)
    monkeypatch.setattr(gemini.time, "sleep", lambda s: None)

    assert g.generate({}, purpose="unit", user_id=1)["candidates"]
    assert calls["n"] == 2
    assert g.usage()["global"]["calls"] == 1       # one success, not two
