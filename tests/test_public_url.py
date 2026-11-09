"""job-hunter.work (2026-11-09): Google sign-in must return to the address it
started from - its state cookie belongs to that address - on the new domain and
on the Railway one alike; an unknown Host never steers the redirect."""
from tests.test_routes import stack  # noqa: F401


def test_google_returns_to_the_address_it_started_from(stack, monkeypatch):
    app = stack["app"]
    monkeypatch.setattr(app, "_SERVED_HOSTS", {"job-hunter.work", "web-production-192b7.up.railway.app"})
    monkeypatch.setattr(app, "MOBILE_URL", "https://job-hunter.work")
    assert app._base_for("job-hunter.work") == "https://job-hunter.work"
    assert app._base_for("web-production-192b7.up.railway.app") == "https://web-production-192b7.up.railway.app"
    assert app._base_for("evil.example") == "https://job-hunter.work"
    assert app._base_for("") == "https://job-hunter.work"
