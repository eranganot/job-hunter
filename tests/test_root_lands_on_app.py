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
import urllib.request

import pytest

from tests.test_routes import Client, stack, users  # noqa: F401


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
    assert "tech opening in Israel" in page        # who it is for, said up front


def test_noa_is_the_product_and_says_she_is_an_ai():
    """2026-11-12: the persona is the positioning. The page uses her name, and
    says plainly that she is software."""
    import landing
    page = landing.landing_html("https://x.test")
    assert "Meet Noa, your personal AI headhunter agent." in page
    assert "She works for you, not for the companies." in page
    assert page.count(">Meet Noa</a>") == 4 and "Let Noa work for you" not in page
    assert "She is software, not a person" in page
    assert "she never makes a decision for you" in page


def test_the_number_in_noas_message_is_real_or_absent():
    import landing
    assert "postings in my latest search" not in landing.landing_html("https://x.test")
    assert "postings in my latest search" not in landing.landing_html("https://x.test", postings_checked=120)
    assert "I read 9,563 postings in my latest search" in landing.landing_html("https://x.test", postings_checked=9563)


def test_the_page_is_dark_uses_the_app_icon_and_app_screenshots():
    import landing
    page = landing.landing_html("https://x.test")
    assert 'name="color-scheme" content="dark"' in page
    assert '<img src="/app/icon-192.png"' in page
    for name in ("job-card.webp", "queue.webp", "job-details.webp", "analytics.webp"):
        assert "/landing-assets/" + name in page
    assert "<script" not in page
    assert "fonts.googleapis" not in page and '<link rel="stylesheet"' not in page


def test_every_noa_variant_has_its_picture_and_only_that_one():
    import landing
    for v in landing.NOA_VARIANTS:
        page = landing.landing_html("https://x.test", noa=v)
        assert "/landing-assets/noa-%s.webp" % v in page
        others = [o for o in landing.NOA_VARIANTS if o != v]
        assert not any("/landing-assets/noa-%s.webp" % o in page for o in others)
    # an unknown variant falls back to the first
    assert "/landing-assets/noa-a.webp" in landing.landing_html("https://x.test", noa="zz")


def test_the_page_has_no_characters_send_html_would_strip():
    """app.send_html removes U+0080-U+00FF; the page must not rely on them."""
    import re, landing
    page = landing.landing_html("https://x.test", postings_checked=9563)
    assert not re.search(r"[\x80-\xff]", page)


def test_one_noa_for_everyone_until_consent_and_a_review_link_shows_another(stack):
    """No cookie for the A/B test before the consent banner exists: the page
    shows Noa A and sets nothing; ?noa= shows another one for reviewing."""
    def get(path):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (stack["port"], path))
        with urllib.request.urlopen(req) as r:
            return r.read().decode(), r.headers.get("Set-Cookie")
    body, set_cookie = get("/")
    assert set_cookie is None and "/landing-assets/noa-a.webp" in body
    for v in ("d", "e"):
        body, set_cookie = get("/?noa=" + v)
        assert set_cookie is None and "/landing-assets/noa-%s.webp" % v in body
    assert "/landing-assets/noa-a.webp" in get("/?noa=../x")[0]


def test_landing_assets_are_served_with_their_type_and_nothing_else_is(stack):
    import landing
    c = Client(stack["port"])
    for name, ctype in landing.ASSETS.items():
        req = urllib.request.Request("http://127.0.0.1:%d/landing-assets/%s" % (stack["port"], name))
        with urllib.request.urlopen(req) as r:
            data = r.read()
            assert r.status == 200 and r.headers["Content-Type"] == ctype, name
            assert data[:4] in (b"RIFF", b"wOF2"), name
    assert c.get("/landing-assets/../app.py")[0] in (400, 404)
    assert c.get("/landing-assets/OFL-inter.txt")[0] == 404
    assert c.get("/landing-assets/secret.webp")[0] == 404
