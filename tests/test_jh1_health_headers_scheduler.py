"""JH-1 / JH-7 (2026-11-05): who sees the full /api/health, the security headers
on every response, the staging scheduler switch, and the scheduler's clock."""
import datetime as dt
import http.client
import json

import pytest

from tests.test_routes import Client, stack, users  # noqa: F401

PRIVATE = ("active_users", "total_jobs", "last_apply", "credentials_key", "llm",
           "llm_history", "db_pool")


def _health(c, headers=None):
    status, _loc, body = c.get("/api/health", extra_headers=headers)
    assert status == 200
    return json.loads(body)


def test_the_health_key_unlocks_the_full_payload(stack, monkeypatch):
    monkeypatch.setenv("JH_HEALTH_KEY", "k-123")
    full = _health(Client(stack["port"]), {"X-Health-Key": "k-123"})
    for key in PRIVATE:
        assert key in full, key
    assert "detail" not in full


def test_a_wrong_or_unset_key_gets_the_public_view(stack, monkeypatch):
    monkeypatch.setenv("JH_HEALTH_KEY", "k-123")
    assert _health(Client(stack["port"]), {"X-Health-Key": "nope"})["detail"] == "public"
    monkeypatch.delenv("JH_HEALTH_KEY", raising=False)
    # No key configured on the box: a header proves nothing.
    assert _health(Client(stack["port"]), {"X-Health-Key": ""})["detail"] == "public"


def test_an_admin_session_sees_everything_a_user_does_not(stack, users):
    admin = _health(users["admin"])
    for key in PRIVATE:
        assert key in admin, key
    assert _health(users["a"])["detail"] == "public"


@pytest.mark.parametrize("path", ["/login", "/api/health"])
def test_security_headers_on_every_response(stack, path):
    conn = http.client.HTTPConnection("127.0.0.1", stack["port"], timeout=10)
    conn.request("GET", path)
    resp = conn.getresponse()
    resp.read()
    conn.close()
    assert resp.getheader("Strict-Transport-Security", "").startswith("max-age=")
    assert resp.getheader("X-Frame-Options") == "DENY"
    assert resp.getheader("Content-Security-Policy") == "frame-ancestors 'none'"
    assert resp.getheader("X-Content-Type-Options") == "nosniff"
    assert resp.getheader("Referrer-Policy") == "strict-origin-when-cross-origin"


def test_scheduler_switch_off_skips_the_tick_and_says_so(stack, monkeypatch):
    import app
    import schedlock

    monkeypatch.setenv("JH_SCHEDULER_ENABLED", "0")
    assert app.scheduler_enabled() is False

    def _boom(*a, **k):
        raise AssertionError("the tick must return before taking the scheduler lock")

    monkeypatch.setattr(schedlock, "acquire", _boom)
    app._check_scheduled_jobs()  # returns quietly
    assert _health(Client(stack["port"]))["scheduler"] == "disabled (JH_SCHEDULER_ENABLED=0)"


@pytest.mark.parametrize("value,expected", [(None, True), ("1", True), ("true", True),
                                            ("0", False), ("false", False), ("OFF", False), ("no", False)])
def test_scheduler_switch_values(monkeypatch, value, expected):
    import app
    if value is None:
        monkeypatch.delenv("JH_SCHEDULER_ENABLED", raising=False)
    else:
        monkeypatch.setenv("JH_SCHEDULER_ENABLED", value)
    assert app.scheduler_enabled() is expected


def test_scheduler_clock_follows_israel_dst():
    import app
    utc = dt.timezone.utc
    # Winter (after the last Sunday of October): GMT+2. The fixed GMT+3 ran every
    # scheduled search an hour early from 2026-10-25.
    assert app.israel_now(dt.datetime(2026, 11, 5, 10, 0, tzinfo=utc)).hour == 12
    # Summer: GMT+3, as before.
    assert app.israel_now(dt.datetime(2026, 7, 1, 10, 0, tzinfo=utc)).hour == 13


def test_a_rotating_forwarded_for_does_not_dodge_the_register_limiter(stack, monkeypatch):
    """JH-3: the client picks the first X-Forwarded-For entry; the proxy appends
    the real address last. Keyed on the first, five signups with five invented
    addresses were five fresh buckets."""
    import urllib.parse
    import ratelimit
    ratelimit.reset_all()
    monkeypatch.setenv("JH_RL_REGISTER_MAX", "3")
    seen = []
    for i in range(5):
        body = urllib.parse.urlencode({
            "name": "xff%d" % i, "email": "xff%d@example.test" % i,
            "password": "correct-horse-1", "password2": "correct-horse-1"}).encode()
        conn = http.client.HTTPConnection("127.0.0.1", stack["port"], timeout=10)
        conn.request("POST", "/register", body=body, headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Content-Length": str(len(body)),
            "X-Forwarded-For": "10.9.8.%d, 203.0.113.9" % i})
        resp = conn.getresponse()
        resp.read()
        conn.close()
        seen.append(resp.status)
    ratelimit.reset_all()
    assert 429 in seen, "rotating X-Forwarded-For bypassed the limiter: %s" % seen
