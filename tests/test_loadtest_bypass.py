"""C12 (2026-11-24): the staging-only sign-up bypass for load tests."""
import http.client
import urllib.parse
import uuid

from tests.test_routes import stack  # noqa: F401


def _register(port, key=None):
    body = urllib.parse.urlencode({"name": "L", "email": "lt-%s@job-hunter.test" % uuid.uuid4().hex[:8],
                                   "password": "password123", "password2": "password123"})
    h = {"Content-Type": "application/x-www-form-urlencoded"}
    if key:
        h["X-Loadtest-Key"] = key
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("POST", "/register", body=body, headers=h)
    r = c.getresponse(); r.read()
    return r.status


def _fill_limit(port, monkeypatch):
    import ratelimit
    monkeypatch.setattr(ratelimit, "check_and_record", lambda bucket, key: 600 if bucket == "register" else 0)


def test_bypass_needs_key_and_matching_header(stack, monkeypatch):
    _fill_limit(stack["port"], monkeypatch)
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "staging")
    monkeypatch.delenv("JH_LOADTEST_KEY", raising=False)
    assert _register(stack["port"], "anything") == 429          # no key configured
    monkeypatch.setenv("JH_LOADTEST_KEY", "lt-secret")
    assert _register(stack["port"], "wrong") == 429
    assert _register(stack["port"]) == 429
    assert _register(stack["port"], "lt-secret") in (302, 303)


def test_never_in_production(stack, monkeypatch):
    _fill_limit(stack["port"], monkeypatch)
    monkeypatch.setenv("JH_LOADTEST_KEY", "lt-secret")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
    assert _register(stack["port"], "lt-secret") == 429


def test_script_refuses_production():
    import subprocess, sys
    r = subprocess.run([sys.executable, "scripts/loadtest.py", "--base", "https://job-hunter.work", "--key", "x"],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode != 0 and "Refusing" in (r.stderr + r.stdout)
