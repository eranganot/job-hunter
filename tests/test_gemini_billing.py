"""Gemini billing exhausted (HTTP 402) - pause instead of failing every call.

Observed 2026-10-25: the project's prepaid credits ran out; every call came
back 402 RESOURCE_EXHAUSTED and each became its own Sentry event."""
import io
import time
import urllib.error

import pytest

import gemini
from tests.test_gemini_budget import _Resp, OK_PAYLOAD, g  # noqa: F401  (fixture)

BODY_402 = ('{"error": {"code": 402, "message": "Your prepayment credits are depleted. Please go to '
            'AI Studio at https://ai.studio/projects to manage your project and billing.", '
            '"status": "RESOURCE_EXHAUSTED"}}')


def _http_error(code, body):
    def raiser(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, code, "x", {}, io.BytesIO(body.encode()))
    return raiser


def test_402_pauses_gemini_alerts_once_and_says_why(g, monkeypatch):
    calls = []

    def boom(req, timeout=None):
        calls.append(1)
        return _http_error(402, BODY_402)(req, timeout)
    monkeypatch.setattr(gemini.urllib.request, "urlopen", boom)

    with pytest.raises(gemini.BillingExhausted) as first:
        gemini.generate({"x": 1}, purpose="score")
    assert "billing exhausted" in str(first.value) and gemini.GEMINI_PAUSED_MARKER not in str(first.value)
    assert len(calls) == 1, "a billing refusal was retried"
    assert len(g.alerts) == 1 and "BILLING" in g.alerts[0]

    # While paused: no network, a marked exception, still one alert.
    for _ in range(5):
        with pytest.raises(gemini.BillingExhausted) as later:
            gemini.generate({"x": 1}, purpose="score")
        assert str(later.value).startswith(gemini.GEMINI_PAUSED_MARKER)
    assert len(calls) == 1 and len(g.alerts) == 1
    st = gemini.billing_status()
    assert st["paused"] and st["skipped_calls"] == 5 and st["trips"] == 1
    assert gemini.usage()["billing"]["paused"] is True


def test_callers_treat_it_as_a_spent_budget(g, monkeypatch):
    monkeypatch.setattr(gemini.urllib.request, "urlopen", _http_error(402, BODY_402))
    with pytest.raises(gemini.BudgetExceeded):
        gemini.generate({"x": 1}, purpose="score")


def test_service_resumes_by_itself_once_the_pause_is_over(g, monkeypatch):
    monkeypatch.setattr(gemini.urllib.request, "urlopen", _http_error(402, BODY_402))
    with pytest.raises(gemini.BillingExhausted):
        gemini.generate({"x": 1}, purpose="score")
    gemini._BILLING["paused_until"] = time.time() - 1          # pause elapsed; credits topped up
    monkeypatch.setattr(gemini.urllib.request, "urlopen", lambda req, timeout=None: _Resp(OK_PAYLOAD))
    assert gemini.generate({"x": 1}, purpose="score")["candidates"]
    assert gemini.billing_status()["paused"] is False


def test_a_rate_limit_429_is_not_a_billing_pause(g, monkeypatch):
    seq = iter([_http_error(429, '{"error":{"status":"RESOURCE_EXHAUSTED","message":"quota per minute"}}'),
                lambda req, timeout=None: _Resp(OK_PAYLOAD)])
    monkeypatch.setattr(gemini.urllib.request, "urlopen", lambda req, timeout=None: next(seq)(req, timeout))
    monkeypatch.setattr(gemini.time, "sleep", lambda s: None)
    assert gemini.generate({"x": 1}, purpose="score")["candidates"]
    assert gemini.billing_status()["trips"] == 0


@pytest.mark.parametrize("code,body,expected", [
    (402, "", True),
    (429, "RESOURCE_EXHAUSTED: Your prepayment credits are depleted", True),
    (403, "RESOURCE_EXHAUSTED billing account disabled", True),
    (429, "RESOURCE_EXHAUSTED quota exceeded per minute", False),
    (500, "internal", False),
])
def test_is_billing_error(code, body, expected):
    assert gemini.is_billing_error(code, body) is expected


def test_sentry_drops_the_per_call_paused_lines_but_keeps_the_trip():
    import errors
    paused = {"logentry": {"message": "[ai] scoring error: [gemini-paused] Gemini billing exhausted - call skipped"}}
    trip = {"exception": {"values": [{"value": "Gemini billing exhausted (HTTP 402) - pausing Gemini until 21:00 UTC"}]}}
    assert errors._drop_known_noise(paused, {}) is None
    assert errors._drop_known_noise(trip, {}) is trip
