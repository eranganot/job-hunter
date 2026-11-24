"""S3 (2026-11-24): /ig, /fb, /li send visitors to the landing page with their tracking tags."""
import http.client

from tests.test_routes import stack  # noqa: F401


def _get(port, path):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("GET", path)
    r = c.getresponse(); r.read()
    return r.status, r.getheader("Location") or ""


def test_short_links(stack):
    p = stack["port"]
    assert _get(p, "/ig") == (302, "/?utm_source=instagram&utm_medium=bio")
    assert _get(p, "/IG/") == (302, "/?utm_source=instagram&utm_medium=bio")
    assert _get(p, "/fb")[1].startswith("/?utm_source=facebook&utm_medium=page")
    assert _get(p, "/li")[1].startswith("/?utm_source=linkedin&utm_medium=page")
    assert _get(p, "/")[0] == 200
