"""/api/admin/paid-pool/check (2026-11-23): admin only; reports the key check."""
import json

from tests.test_routes import stack, users  # noqa: F401


def test_only_an_admin_can_check_or_rerun_a_paid_source(stack, users):
    status, _loc, _body = users["a"].post_json("/api/admin/paid-pool/check", {"source": "apify", "run": True})
    assert status == 403


def test_an_admin_without_a_key_gets_the_reason_and_no_run(stack, users, monkeypatch):
    monkeypatch.delenv("APIFY_TOKEN", raising=False)
    status, _loc, body = users["admin"].post_json("/api/admin/paid-pool/check", {"source": "apify", "run": True})
    out = json.loads(body)
    assert status == 200 and out["key"] == {"ok": False, "detail": "no API key set"} and "run" not in out
