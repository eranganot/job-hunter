"""JH-2 / JH-4 (2026-11-05): secrets out of URLs and logs, the relay speaking
the server's sync format, and the three P3 link/admin fixes."""
import http.client
import json
import sqlite3
import urllib.request

import pytest

from tests.test_routes import Client, stack, users  # noqa: F401

KEY = "sync-key-for-tests-0123456789"


@pytest.fixture
def sync_key(stack, monkeypatch):
    import app
    monkeypatch.setattr(app, "SYNC_API_KEY", KEY)
    return KEY


def _get(port, path, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", path, headers=headers or {})
    r = conn.getresponse()
    body = r.read()
    conn.close()
    return r.status, body


def test_sync_key_in_the_header_is_accepted(stack, sync_key):
    st, _ = _get(stack["port"], "/api/sync/approved", {"X-Sync-Key": KEY})
    assert st == 200


def test_legacy_query_key_still_works_until_the_relay_is_updated(stack, sync_key):
    st, _ = _get(stack["port"], "/api/sync/approved?api_key=" + KEY)
    assert st == 200


def test_a_wrong_or_missing_key_is_refused(stack, sync_key):
    assert _get(stack["port"], "/api/sync/approved", {"X-Sync-Key": "nope"})[0] == 401
    assert _get(stack["port"], "/api/sync/approved")[0] == 401


def test_no_key_configured_refuses_everything(stack, monkeypatch):
    import app
    monkeypatch.setattr(app, "SYNC_API_KEY", "")
    assert _get(stack["port"], "/api/sync/approved", {"X-Sync-Key": ""})[0] == 401


def test_the_access_log_never_carries_a_query_key():
    import log
    assert log.redact_path("/api/sync/approved?api_key=abc123&x=1") == "/api/sync/approved?api_key=***&x=1"
    assert log.redact_path("/x?token=t&key=k&password=p") == "/x?token=***&key=***&password=***"
    assert log.redact_path("/plain/path") == "/plain/path"
    assert log.Request("GET", "/a?api_key=secret", "1.2.3.4").path == "/a?api_key=***"


def test_relay_posts_the_shape_the_server_reads(stack, sync_key, tmp_path):
    """Before JH-2 the relay posted a bare list with the key in the URL; the
    server reads {"jobs": [...]} and the key from the body or header, so every
    jobs/updates sync was refused."""
    import relay
    base = "http://127.0.0.1:%d" % stack["port"]
    assert "inserted" in relay._post(base + "/api/sync/jobs", {"jobs": []}, KEY)
    assert isinstance(relay._get(base + "/api/sync/approved", KEY), (list, dict))


def test_gemini_key_goes_in_a_header_not_the_url(monkeypatch):
    import gemini
    seen = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"candidates": [], "usageMetadata": {}}).encode()

    def fake_urlopen(req, timeout=None):
        seen["url"], seen["headers"] = req.full_url, dict(req.header_items())
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(gemini, "_record", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(gemini, "check", lambda *a, **k: None, raising=False)
    try:
        gemini.generate({"contents": []}, purpose="test", key="AIza-test-key", retries=0)
    except Exception:  # noqa: BLE001 - only the request shape matters here
        pass
    assert "url" in seen, "no request was made"
    assert "key=" not in seen["url"] and "AIza-test-key" not in seen["url"]
    assert {k.lower(): v for k, v in seen["headers"].items()}.get("x-goog-api-key") == "AIza-test-key"


@pytest.mark.parametrize("path", ["/api/admin/link-audit", "/api/admin/link-search-trial"])
def test_gemini_spending_admin_gets_refuse_cross_site(stack, users, path):
    st, _l, _b = users["admin"].get(path, extra_headers={"Sec-Fetch-Site": "cross-site"})
    assert st == 403


def test_unique_violation_is_recognised():
    import links
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (u TEXT UNIQUE)")
    conn.execute("INSERT INTO t VALUES ('a')")
    with pytest.raises(sqlite3.IntegrityError) as ei:
        conn.execute("INSERT INTO t VALUES ('a')")
    assert links._is_unique_violation(ei.value)
    assert not links._is_unique_violation(ValueError("x"))


def test_blank_location_is_not_matched_to_a_foreign_workday_posting(monkeypatch):
    import links
    import workday
    monkeypatch.setattr(workday, "postings_for", lambda ref, title: [
        {"title": "Senior Product Manager", "url": "https://nvidia.wd5.myworkdayjobs.com/x/job/US-CA/Senior-Product-Manager_1",
         "location": "US-CA-Santa-Clara", "ats": "workday"}])
    learned = {links._norm_company("NVIDIA"): [("workday", "nvidia.wd5/NVIDIAExternalCareerSite")]}
    best = links.resolve_from_learned("NVIDIA", "Senior Product Manager", "", learned)
    assert best is None


@pytest.mark.parametrize("path", ["/api/sync/jobs", "/api/sync/updates", "/api/sync/notify",
                                  "/api/sync/queue-cleanup", "/api/sync/not-a-route"])
def test_sync_posts_without_the_key_are_refused(stack, sync_key, path):
    """The session gate no longer covers /api/sync/*; the key must."""
    body = json.dumps({"jobs": [], "updates": [], "message": "x"}).encode()
    conn = http.client.HTTPConnection("127.0.0.1", stack["port"], timeout=10)
    conn.request("POST", path, body=body, headers={"Content-Type": "application/json",
                                                     "Content-Length": str(len(body))})
    r = conn.getresponse()
    r.read()
    conn.close()
    assert r.status in (401, 404), (path, r.status)
