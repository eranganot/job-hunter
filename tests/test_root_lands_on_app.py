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
    # one small inline script (sign-up links keep the campaign tags); with
    # analytics off, nothing is loaded from anywhere else
    assert page.count("<script") == 1 and "<script src" not in page and "posthog" not in page
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
    assert "tech openings in Israel" in page       # who it is for, said up front


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
    for name in ("job-card.webp", "job-details.webp", "analytics.webp"):
        assert "/landing-assets/" + name in page
    assert "queue.webp" not in page
    for name in ():
        assert "/landing-assets/" + name in page
    assert page.count("<script") == 1 and "<script src" not in page
    assert "fonts.googleapis" not in page and '<link rel="stylesheet"' not in page


def test_a_shared_link_shows_the_large_picture_card():
    """GTM row 1 (2026-11-15): a link pasted into Facebook, LinkedIn, WhatsApp or X shows a 1200x630
    picture. The address must be absolute, so it is offered only when the site knows its own address."""
    import os, struct
    import landing
    page = landing.landing_html("https://x.test")
    v = landing._version("share.png")
    assert len(v) == 8
    assert '<meta property="og:image" content="https://x.test/landing-assets/share.png?v=%s"/>' % v in page
    assert '<meta name="twitter:image" content="https://x.test/landing-assets/share.png?v=%s"/>' % v in page
    assert '<meta name="twitter:card" content="summary_large_image"/>' in page
    assert 'og:image:width" content="1200"' in page and 'og:image:height" content="630"' in page
    assert 'property="og:image:alt"' in page
    # no public address (a preview deploy): no picture claim, the small card stays
    bare = landing.landing_html("")
    assert "og:image" not in bare and "twitter:image" not in bare
    assert '<meta name="twitter:card" content="summary"/>' in bare
    # the committed file really is 1200x630 and small enough for every network (WhatsApp wants < 300 KB)
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "landing", "share.png")
    with open(path, "rb") as f:
        head = f.read(24)
    assert head[:8] == b"\x89PNG\r\n\x1a\n" and struct.unpack(">II", head[16:24]) == (1200, 630)
    assert os.path.getsize(path) < 300_000


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


def test_each_view_picks_a_noa_without_a_cookie_and_a_review_link_shows_one(stack):
    """2026-11-12: the A/B test needs no cookie - a fresh pick per view, the
    variant carried on the sign-up links; ?noa= shows one for reviewing."""
    import re
    def get(path):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (stack["port"], path))
        with urllib.request.urlopen(req) as r:
            return r.read().decode(), r.headers.get("Set-Cookie")
    seen = set()
    for _ in range(40):
        body, set_cookie = get("/")
        assert set_cookie is None
        seen.add(re.search(r'data-noa="(\w)"', body).group(1))
    assert seen == {"a", "d", "e"}
    for v in ("a", "d", "e"):
        body, _c = get("/?noa=" + v)
        assert "/landing-assets/noa-%s.webp" % v in body and 'data-noa="%s"' % v in body
    assert re.search(r'data-noa="[ade]"', get("/?noa=../x")[0])


def test_landing_assets_are_served_with_their_type_and_nothing_else_is(stack):
    import landing
    c = Client(stack["port"])
    for name, ctype in landing.ASSETS.items():
        req = urllib.request.Request("http://127.0.0.1:%d/landing-assets/%s" % (stack["port"], name))
        with urllib.request.urlopen(req) as r:
            data = r.read()
            assert r.status == 200 and r.headers["Content-Type"] == ctype, name
            assert data[:4] in (b"RIFF", b"wOF2", b"\x89PNG"), name
    assert c.get("/landing-assets/../app.py")[0] in (400, 404)
    assert c.get("/landing-assets/OFL-inter.txt")[0] == 404
    assert c.get("/landing-assets/secret.webp")[0] == 404


def test_landing_copy_after_the_panel_review():
    """GTM row 2 (2026-11-15): no "every opening" claim, the two lines under the button, the two new
    questions, and the founder's name in the footer."""
    import landing
    page = landing.landing_html("https://x.test")
    low = page.lower()
    for claim in ("every tech opening", "every opening", "every new tech posting"):
        assert claim not in low
    assert "thousands of tech openings in Israel" in page
    assert "every" not in landing._morning_message(None).split("for you")[0]
    assert "2-minute setup &middot; first picks today" in page
    assert "No employer sees your CV until you apply." in page
    assert "<summary>Will my employer know I'm looking?</summary>" in page
    assert "<summary>What doesn't Noa do (yet)?</summary>" in page
    assert "Built and run by Eran Ganot" in page
    assert "five minutes" not in page
    assert not [ch for ch in page if 0x80 <= ord(ch) <= 0xff]


def test_product_pictures_show_finished_work_and_their_real_size():
    """GTM row 3 (2026-11-15): the details picture shows a written cover letter and a tailored CV, not
    an empty box, and the statistics picture has no approval-rate tile. The page states each picture's
    real size, so nothing jumps while it loads."""
    import os, re, struct
    import landing
    page = landing.landing_html("https://x.test")
    assert "a cover letter to generate" not in page and "approval and applied rates" not in page
    assert "a finished cover letter" in page
    base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "landing")
    for name in ("job-details.webp", "analytics.webp", "job-card.webp"):
        w, h = map(int, re.search(r'%s\?v=[0-9a-f]{8}" width="(\d+)" height="(\d+)"' % re.escape(name), page).groups())
        with open(os.path.join(base, name), "rb") as f:
            head = f.read(30)
        assert head[:4] == b"RIFF" and head[12:16] == b"VP8 ", name      # lossy WebP: size at bytes 26-30
        assert struct.unpack("<HH", head[26:30]) == (w, h), name


def test_landing_assets_carry_their_file_hash_so_a_replaced_picture_is_not_served_stale(stack):
    """2026-11-15: job-details.webp was replaced under the same name and Cloudflare kept serving the old
    file (a week's cache). Every asset address now ends ?v=<hash of the file>, and the server still
    serves the file whatever the query says."""
    import hashlib, os, re
    import landing
    page = landing.landing_html("https://x.test")
    found = re.findall(r"/landing-assets/([A-Za-z0-9._-]+?)\?v=([0-9a-f]{8})", page)
    assert {n for n, _v in found} >= {"job-card.webp", "job-details.webp", "analytics.webp", "noa-a.webp",
                                      "share.png", "fraunces-latin-wght.woff2", "inter-latin-wght.woff2"}
    assert not re.search(r"/landing-assets/[A-Za-z0-9._-]+(?=[\"')])", page)      # none left without a version
    base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "landing")
    for name, v in found:
        with open(os.path.join(base, name), "rb") as f:
            assert hashlib.sha1(f.read()).hexdigest()[:8] == v, name
    c = Client(stack["port"])
    assert c.get("/landing-assets/job-details.webp?v=%s" % landing._version("job-details.webp"))[0] == 200


def test_mobile_hero_shows_noa_and_the_job_card_under_the_main_button():
    """GTM row 4 (2026-11-15): on one column the order is headline, lead, main button, Noa's morning
    message, the job card, then "See how she works". Done with CSS order, so the desktop page and the
    reading order for screen readers are unchanged."""
    import re
    import landing
    page = landing.landing_html("https://x.test")
    css = page[page.index("<style>"):page.index("</style>")]
    block = css[css.index("@media(max-width:61.99rem)"):]
    order = {sel: int(n) for sel, n in re.findall(r"\.hero ([.\w ]+?)\{order:(\d+)", block)}
    assert order[".btn.go"] < order[".m2"] < order[".shot"] < order[".btn.line"]
    assert ".hero .pitch,.hero .chat,.hero .ctas{display:contents}" in block
    assert 'class="pitch"' in page and 'class="msg m1"' in page and 'class="msg m2"' in page
