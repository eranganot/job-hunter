"""The full /api/health shows the caller's own address headers (launch finding
F6, 2026-11-24); the anonymous view must not."""
import app


def test_request_ip_is_not_in_the_public_health_view():
    assert "request_ip" not in app.HEALTH_PUBLIC_KEYS
    out = app.public_health({"status": "ok", "request_ip": {"client_ip": "1.2.3.4"}})
    assert "request_ip" not in out
