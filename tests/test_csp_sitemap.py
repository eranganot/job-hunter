"""C9 (2026-11-24): the full CSP runs report-only (the enforced header stays
frame-ancestors), reports are counted, and the site has a sitemap."""
import http.client
import json

from tests.test_routes import Client, stack  # noqa: F401


def test_headers(stack):
    c = http.client.HTTPConnection("127.0.0.1", stack["port"], timeout=10)
    c.request("GET", "/login"); r = c.getresponse(); r.read()
    assert r.getheader("Content-Security-Policy") == "frame-ancestors 'none'"
    ro = r.getheader("Content-Security-Policy-Report-Only")
    for part in ("default-src 'self'", "https://connect.facebook.net", "https://*.posthog.com",
                 "object-src 'none'", "report-uri /api/csp-report"):
        assert part in ro
    assert "camera=()" in r.getheader("Permissions-Policy")


def _post(port, body):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    raw = json.dumps(body).encode()
    c.request("POST", "/api/csp-report", body=raw,
              headers={"Content-Type": "application/csp-report", "Content-Length": str(len(raw))})
    r = c.getresponse(); r.read()
    return r.status


def test_reports_are_counted(stack):
    app = stack["app"]
    app._CSP_REPORTS.clear()
    rep = {"csp-report": {"document-uri": "https://job-hunter.work/", "effective-directive": "script-src-elem",
                          "blocked-uri": "https://evil.example/x.js?a=1"}}
    assert _post(stack["port"], rep) == 204
    assert _post(stack["port"], rep) == 204
    # the newer Reporting API shape: a list of {"body": {...}}
    assert _post(stack["port"], [{"type": "csp-violation", "body": {"effectiveDirective": "x",
                                  "effective-directive": "img-src", "blocked-uri": "data"}}]) == 204
    assert app._CSP_REPORTS["script-src-elem https://evil.example"] == 2
    assert app._CSP_REPORTS["img-src data"] == 1
    c = http.client.HTTPConnection("127.0.0.1", stack["port"], timeout=10)
    c.request("POST", "/api/csp-report", body=b"not json", headers={"Content-Length": "8"})
    r = c.getresponse(); r.read()
    assert r.status == 204


def test_reports_are_capped(stack):
    app = stack["app"]
    app._CSP_REPORTS.clear()
    for i in range(app._CSP_REPORTS_MAX + 20):
        app._csp_record({"csp-report": {"effective-directive": "img-src", "blocked-uri": "https://h%d.example/" % i}})
    assert len(app._CSP_REPORTS) == app._CSP_REPORTS_MAX


def test_sitemap_and_robots(stack):
    st, _l, body = Client(stack["port"]).get("/sitemap.xml")
    body = body.decode() if isinstance(body, bytes) else body
    assert st == 200 and "<urlset" in body and "/privacy</loc>" in body and "/terms</loc>" in body
    st, _l, body = Client(stack["port"]).get("/robots.txt")
    body = body.decode() if isinstance(body, bytes) else body
    assert "Sitemap: " in body and body.strip().endswith("/sitemap.xml")
