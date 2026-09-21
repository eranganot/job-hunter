"""
tests/test_root_lands_on_app.py - the bare domain lands on the new UI.

Phase 4 made /app the home destination through app.home_url(), and moved every
redirect it could find: login, register, Google callback, the admin bounce.
Two were missed, and they are the two a returning user actually hits:

  * GET /  - the bare domain, i.e. the link Eran clicks - redirected a signed-in
    user to a hardcoded "/dashboard";
  * GET /dashboard served DASHBOARD_HTML unconditionally, so any bookmark, any
    old home-screen icon and every notification link (five url_suffix sites
    still said "/dashboard") opened the legacy design.

Found 2026-09-21: https://web-production-192b7.up.railway.app/ showed the old UI
to a signed-in user while /app showed the new one. LEGACY_UI=1 must still bring
the old page back - that switch is the reason the legacy code has not been
deleted yet.
"""
import pytest

from tests.test_routes import stack, users  # noqa: F401


def test_the_bare_domain_sends_a_signed_in_user_to_the_new_ui(stack, users, monkeypatch):
    monkeypatch.delenv("LEGACY_UI", raising=False)
    status, location, _ = users["a"].get("/")
    assert status == 302
    assert location == "/app", "/ sent a signed-in user to %r" % location


def test_an_old_dashboard_link_opens_the_new_ui(stack, users, monkeypatch):
    """Bookmarks, installed icons and every notification sent before today."""
    monkeypatch.delenv("LEGACY_UI", raising=False)
    status, location, body = users["a"].get("/dashboard")
    assert status == 302 and location == "/app", (
        "/dashboard served %s %r - the legacy page" % (status, body[:60]))


def test_legacy_ui_still_restores_the_old_page(stack, users, monkeypatch):
    """The escape hatch must keep working until the legacy code is deleted."""
    monkeypatch.setenv("LEGACY_UI", "1")
    assert users["a"].get("/")[1] == "/dashboard"
    status, _loc, body = users["a"].get("/dashboard")
    assert status == 200 and b"<html" in body.lower()


def test_signed_out_still_goes_to_login(stack, monkeypatch):
    from tests.test_routes import Client
    monkeypatch.delenv("LEGACY_UI", raising=False)
    assert Client(stack["port"]).get("/")[1] == "/login"


def test_notification_links_point_at_the_home_ui(stack, monkeypatch):
    """A push or Telegram message's link must not open the legacy page either."""
    import re
    src = open(stack["app"].__file__, encoding="utf-8").read()
    stale = re.findall(r'url_suffix="/dashboard[^"]*"', src)
    assert not stale, "notification links still hardcode the legacy page: %s" % stale
