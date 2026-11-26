"""/favicon.ico answers, and the CSP allows Cloudflare's analytics beacon (2026-11-26)."""
from tests.test_routes import Client, stack, users  # noqa: F401


def test_favicon_is_served(stack):
    import urllib.request
    r = urllib.request.urlopen("http://127.0.0.1:%d/favicon.ico" % stack["port"])
    assert r.status == 200 and r.headers["Content-Type"] == "image/png" and r.read()[:4] == b"\x89PNG"


def test_csp_allows_the_cloudflare_beacon():
    import app
    assert "https://static.cloudflareinsights.com" in app.CSP_REPORT_ONLY
    assert "https://cloudflareinsights.com" in app.CSP_REPORT_ONLY
