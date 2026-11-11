"""
tests/test_root_lands_on_app.py - the bare domain lands on the new UI.

Phase 4 made /app the home destination through app.home_url(), and moved every
redirect it could find: login, register, Google callback, the admin bounce.
Two were missed, and they are the two a returning user actually hits:

  * GET /  - the bare domain, i.e. the link the owner clicks - redirected a signed-in
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


def test_the_switch_that_restored_the_old_page_is_gone(stack, users, monkeypatch):
    """Step 9 (2026-11-05) deleted the legacy pages; LEGACY_UI=1 now changes
    nothing - every old URL opens /app."""
    monkeypatch.setenv("LEGACY_UI", "1")
    assert users["a"].get("/")[1] == "/app"
    assert users["a"].get("/dashboard")[:2] == (302, "/app")


def test_signed_out_gets_the_landing_page(stack, monkeypatch):
    """Step 10 (2026-11-09): the bare domain is the public page for strangers,
    not a sign-in form. It must not leak the app shell or anyone's data."""
    from tests.test_routes import Client
    monkeypatch.delenv("LEGACY_UI", raising=False)
    status, location, body = Client(stack["port"]).get("/")
    assert status == 200 and location is None
    page = body.decode()
    assert 'href="/register"' in page and 'href="/login"' in page
    assert 'property="og:title"' in page and 'name="description"' in page
    assert "<script" not in page, "the landing page runs no script"
    # send_html strips U+0080-U+00FF (a legacy mojibake filter), so the page
    # spells those characters as entities - a raw one would vanish silently.
    import landing
    assert not [ch for ch in landing.landing_html("https://x.test") if 0x80 <= ord(ch) <= 0xff]


def test_notification_links_point_at_the_home_ui(stack, monkeypatch):
    """A push or Telegram message's link must not open the legacy page either."""
    import re
    src = open(stack["app"].__file__, encoding="utf-8").read()
    stale = re.findall(r'url_suffix="/dashboard[^"]*"', src)
    assert not stale, "notification links still hardcode the legacy page: %s" % stale


def test_the_landing_page_never_says_beta_and_states_the_offer():
    """2026-11-11: the public offer is "Premium is free for a limited time";
    accounts are on the Beta plan internally, and the page does not say so."""
    import landing
    page = landing.landing_html("https://x.test")
    assert "beta" not in page.lower()
    assert "Premium is free for a limited time" in page
    assert "tech roles in Israel" in page          # who it is for, said up front


def test_the_activity_line_is_shown_only_with_a_real_count():
    import landing
    assert "The latest search checked" not in landing.landing_html("https://x.test")
    assert "The latest search checked" not in landing.landing_html("https://x.test", postings_checked=120)
    assert "9,563 job postings" in landing.landing_html("https://x.test", postings_checked=9563)


def test_the_headline_font_is_inlined_not_fetched():
    import landing
    page = landing.landing_html("https://x.test")
    assert "data:font/woff2;base64," in page
    assert "fonts.googleapis" not in page and "<link rel=\"stylesheet\"" not in page
