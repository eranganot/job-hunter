"""tests/test_metapixel.py - Meta ad measurement only ever follows consent (GTM row 16)."""
import importlib
import json
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FBP = "fb.1.1763280000000.1234567890"
FBC = "fb.1.1763280000001.IwAR0abc_DEF-123"


@pytest.fixture
def mp(monkeypatch):
    monkeypatch.setenv("META_PIXEL_ID", "1131850159259332")
    monkeypatch.setenv("META_CAPI_TOKEN", "test-token")
    monkeypatch.delenv("META_TEST_EVENT_CODE", raising=False)
    import metapixel
    m = importlib.reload(metapixel)
    sent = []
    monkeypatch.setattr(m, "_post", lambda body: sent.append(body))
    m.sent = sent
    yield m
    monkeypatch.delenv("META_PIXEL_ID"); monkeypatch.delenv("META_CAPI_TOKEN")
    importlib.reload(metapixel)


def _drain(m):
    import time
    for _ in range(100):
        if m._q.empty() and m.sent:
            break
        time.sleep(0.02)
    return m.sent


def test_off_without_configuration(monkeypatch):
    monkeypatch.delenv("META_PIXEL_ID", raising=False); monkeypatch.delenv("META_CAPI_TOKEN", raising=False)
    import metapixel
    m = importlib.reload(metapixel)
    assert not m.browser_enabled() and not m.server_enabled() and m.consent_js() == ""
    assert m.send("CompleteRegistration", {"fbp": FBP}, "UA", "1.2.3.4", "https://x/") is False


def test_no_fbp_cookie_means_no_consent_and_nothing_sent(mp):
    assert mp.ids_from_cookies("session=abc; _fbc=%s" % FBC) == {}            # a click id alone is not consent
    assert mp.ids_from_cookies("_fbp=<script>") == {} and mp.clean_ids("not json") == {}
    assert mp.ids_from_cookies("session=abc; _fbp=%s; _fbc=%s" % (FBP, FBC)) == {"fbp": FBP, "fbc": FBC}
    assert mp.send("CompleteRegistration", {}, "UA", "1.2.3.4", "https://x/") is False
    assert mp.send("Purchase", {"fbp": FBP}, "UA", "1.2.3.4", "https://x/") is False     # only our two events


def test_the_event_carries_a_hashed_email_and_no_name_or_job(mp):
    import hashlib
    assert mp.send("FirstApprove", {"fbp": FBP, "fbc": FBC}, "Mozilla/5.0", "203.0.113.9",
                   "https://job-hunter.work/app", email="  Dana@Example.Test ")
    ev = _drain(mp)[0]["data"][0]
    assert ev["event_name"] == "FirstApprove" and ev["action_source"] == "website"
    assert set(ev["user_data"]) == {"fbp", "fbc", "client_user_agent", "client_ip_address", "em"}
    assert ev["user_data"]["em"] == [hashlib.sha256(b"dana@example.test").hexdigest()]
    assert "dana" not in json.dumps(ev).lower()                       # never the readable address
    assert "em" not in mp.payload("FirstApprove", {"fbp": FBP}, "UA", "", "u")["data"][0]["user_data"]
    assert set(ev) == {"event_name", "event_time", "event_id", "action_source", "event_source_url", "user_data"}


def test_the_pixel_loads_only_after_accept_on_a_banner_that_names_meta(mp, monkeypatch):
    import analytics
    monkeypatch.setattr(analytics, "KEY", "phc_test")
    html = analytics.browser_snippet("landing")
    assert "Facebook and Instagram (Meta)" in html and "used for ads" not in html
    js = mp.consent_js()
    # fbevents.js is requested only inside jhMetaLoad, which runs on Accept or on a stored Accept.
    assert js.count("connect.facebook.net") == 1 and js.index("function jhMetaLoad") < js.index("connect.facebook.net")
    tail = js[js.index("function jhMeta(yes)"):]
    assert 'localStorage.getItem("jh_ads_ok")==="1")jhMetaLoad()' in tail
    assert "Max-Age=0" in tail                                              # No thanks removes Meta's cookies
    assert 'localStorage.setItem("jh_ads_ok","0")' in tail                  # a No is remembered too
    # Someone who answered the older analytics-only banner has no Meta answer: ask again (2026-11-17).
    assert 'localStorage.getItem("jh_ads_ok")===null' in js and "jhMetaAsk&&jhMetaAsk()" in html
    # Shown even when PostHog's script is blocked (2026-11-17): a timer asks too,
    # reading PostHog's stored answer instead of PostHog itself.
    assert "setTimeout(jhAsk,2500)" in html and '"__ph_opt_in_out_"+"phc_test"' in html
    assert "if(window.jhMeta){jhMeta(yes)}" in html and "jhCookieSettings" in html
    # Without a pixel the old wording stands and nothing of Meta's is on the page.
    monkeypatch.setattr(mp, "PIXEL_ID", "")
    plain = analytics.browser_snippet("landing")
    assert "facebook" not in plain.lower() and "nothing is sold or used for ads" in plain


def test_the_privacy_policy_names_meta_and_every_store():
    import legal
    p = legal.privacy_html()
    for needle in ("Meta Platforms Ireland", "<code>_fbp</code>", "<code>_fbc</code>", "<code>jh_ads_ok</code>",
                   "Cookie choices", "no longer than 90 days after you sign up", "Your consent, given on the cookie banner",
                   "one-way scrambled code (a SHA-256 hash)"):
        assert needle in p, needle
    assert "No advertising cookies" not in p and "use advertising trackers" not in p
    import metapixel
    assert metapixel.CONSENT_KEY == "jh_ads_ok"
    assert "META_IDS_DAYS = 90" in open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    assert not re.search(r"META_CAPI_TOKEN\s*=\s*[\"'][A-Za-z0-9]", open(os.path.join(ROOT, "metapixel.py")).read())
