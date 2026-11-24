"""C9 (2026-11-24): pages asked for on the Railway address move to
job-hunter.work with a 301; /api/ and the Google callback stay served there."""
import http.client

from tests.test_routes import stack  # noqa: F401

RAIL = "web-production-192b7.up.railway.app"


def _get(port, path, host):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("GET", path, headers={"Host": host})
    r = c.getresponse()
    r.read()
    return r.status, r.getheader("Location") or ""


def test_rule(stack, monkeypatch):
    app = stack["app"]
    monkeypatch.setattr(app, "_public_url", "https://job-hunter.work")
    f = app._railway_forward
    assert f(RAIL, "/privacy") == "https://job-hunter.work/privacy"
    assert f(RAIL, "/app/?x=1") == "https://job-hunter.work/app/?x=1"
    assert f(RAIL + ":443", "/") == "https://job-hunter.work/"
    assert f(RAIL, "/api/health") is None
    assert f(RAIL, "/auth/google/callback?code=1") is None
    assert f("job-hunter.work", "/privacy") is None
    assert f("localhost:8080", "/privacy") is None
    assert f("healthcheck.railway.app", "/api/health") is None
    monkeypatch.setenv("JH_FORWARD_RAILWAY_HOST", "0")
    assert f(RAIL, "/privacy") is None


def test_no_public_url_means_no_forward(stack, monkeypatch):
    app = stack["app"]
    monkeypatch.setattr(app, "_public_url", "")
    assert app._railway_forward(RAIL, "/privacy") is None


def test_live_server(stack, monkeypatch):
    app = stack["app"]
    monkeypatch.setattr(app, "_public_url", "https://job-hunter.work")
    assert _get(stack["port"], "/terms?a=b", RAIL) == (301, "https://job-hunter.work/terms?a=b")
    code, _ = _get(stack["port"], "/api/health", RAIL)
    assert code == 200
    code, loc = _get(stack["port"], "/terms", "127.0.0.1")
    assert code == 200 and loc == ""
