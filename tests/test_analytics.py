"""Product analytics (analytics.py), 2026-11-12: server-side funnel events,
attribution carried in the URL, the consent-gated browser snippet."""
import base64
import json
import urllib.request

import pytest

from tests.test_routes import Client, stack, users  # noqa: F401


@pytest.fixture
def sent(stack, monkeypatch):
    """Turn analytics on and record what would be sent, without a network."""
    import analytics
    app = stack["app"]
    out = []
    monkeypatch.setattr(analytics, "KEY", "phc_test")
    monkeypatch.setattr(analytics, "capture",
                        lambda uid, ev, props=None, **kw: out.append((uid, ev, dict(props or {}), kw)))
    monkeypatch.setattr(analytics, "capture_anonymous",
                        lambda ev, props=None, user_agent="": out.append((None, ev, dict(props or {}), {"ua": user_agent})))
    monkeypatch.setattr(app, "send_email", lambda *a, **k: None)
    app._ANALYTICS_SKIP.clear()
    analytics._opened.clear()
    import ratelimit
    ratelimit.reset_all()
    return out


def _events(out, name):
    return [e for e in out if e[1] == name]


def test_attribution_is_cleaned():
    import analytics
    a = analytics.clean_attribution({"utm_source": ["facebook"], "utm_campaign": "launch week<script>",
                                     "email": "x@y.z", "noa": "d", "ref": "a" * 200})
    assert a == {"utm_source": "facebook", "utm_campaign": "launchweekscript", "noa": "d", "ref": "a" * 64}
    assert analytics.decode_attribution(analytics.encode_attribution(a)) == a


def test_a_signup_from_a_campaign_link_carries_its_source(stack, sent):
    c = Client(stack["port"])
    st, _l, body = c.get("/register?utm_source=facebook&utm_campaign=launch&noa=e&email=leak@x.test")
    page = body.decode()
    assert 'name="utm_source" value="facebook"' in page and 'name="noa" value="e"' in page
    assert 'name="email" value="leak' not in page                       # only campaign tags pass
    assert 'href="/auth/google/start?utm_source=facebook&amp;utm_campaign=launch&amp;noa=e"' in page
    st, _l, _b = c.post_form("/register", {"name": "Dana", "email": "dana-a@example.test",
                                           "password": "pw-long-enough-1", "password2": "pw-long-enough-1",
                                           "utm_source": "facebook", "utm_campaign": "launch", "noa": "e"})
    assert st == 302
    (uid, _ev, props, kw), = _events(sent, "user_signed_up")
    assert props == {"utm_source": "facebook", "utm_campaign": "launch", "noa": "e", "method": "email"}
    assert kw["set_once"] == {"utm_source": "facebook", "utm_campaign": "launch", "noa": "e"}
    assert "dana" not in json.dumps(sent).lower()                       # no name or email leaves


def test_the_funnel_steps_are_sent_once_each(stack, sent):
    c = Client(stack["port"])
    c.post_form("/register", {"name": "Lior", "email": "lior-a@example.test",
                              "password": "pw-long-enough-1", "password2": "pw-long-enough-1"})
    app = stack["app"]
    db = stack["db"].get_db()
    uid = db.execute("SELECT id FROM users WHERE email='lior-a@example.test'").fetchone()["id"]
    db.close()
    for _ in range(3):
        app._funnel(uid, "cv")
        app._funnel(uid, "first_approve")
    assert len(_events(sent, "cv_uploaded")) == 1
    assert len(_events(sent, "activated")) == 1


def test_the_test_account_is_never_sent_and_the_admin_is_marked_internal(stack, users, sent):
    app = stack["app"]
    st, _l, body = users["admin"].post_json("/api/admin/test-account/reset", {})
    d = json.loads(body)
    db = stack["db"].get_db()
    tid = db.execute("SELECT id FROM users WHERE email=?", (d["email"],)).fetchone()["id"]
    aid = db.execute("SELECT id FROM users WHERE role='admin'").fetchone()["id"]
    db.close()
    app._funnel(tid, "cv")
    app._track(aid, "job_approved")
    assert all(e[0] != tid for e in sent)
    (_u, _e, props, _kw), = [e for e in sent if e[0] == aid]
    assert props.get("internal") is True


def test_opening_the_app_counts_once_a_day(stack, users, monkeypatch):
    import analytics
    out = []
    monkeypatch.setattr(analytics, "KEY", "phc_test")
    monkeypatch.setattr(analytics, "capture", lambda uid, ev, *a, **k: out.append(ev))
    analytics._opened.clear()
    for _ in range(3):
        assert users["b"].get("/api/me")[0] == 200
    assert out.count("app_opened") == 1


def test_landing_views_are_counted_with_the_noa_variant_and_bots_are_not(stack, sent):
    def get(path, ua):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (stack["port"], path), headers={"User-Agent": ua})
        with urllib.request.urlopen(req) as r:
            return r.read().decode()
    page = get("/?utm_source=linkedin&noa=d", "Mozilla/5.0")
    (_u, ev, props, kw), = _events(sent, "landing_viewed")
    assert props["utm_source"] == "linkedin" and props["noa"] == "d" and 'data-noa="d"' in page
    assert kw["ua"] == "Mozilla/5.0"
    import analytics
    assert analytics._BOT.search("facebookexternalhit/1.1") and analytics._BOT.search("Googlebot")


def test_without_a_key_nothing_is_loaded_or_sent(monkeypatch):
    import analytics, landing
    monkeypatch.setattr(analytics, "KEY", "")
    page = landing.landing_html("https://x.test")
    assert "posthog" not in page and 'id="cc"' not in page
    analytics.capture(1, "x")                    # no-op, no thread, no error


def test_with_a_key_the_page_waits_for_consent(monkeypatch):
    import analytics, landing
    monkeypatch.setattr(analytics, "KEY", "phc_test")
    page = landing.landing_html("https://x.test", noa="e")
    assert 'cookieless_mode:"on_reject"' in page and "disable_session_recording:true" in page
    assert 'id="cc"' in page and "opt_in_capturing" in page and 'href="/privacy"' in page
    assert '"noa_variant": "e"' in page


def test_google_signup_keeps_the_source_through_state(stack, sent, monkeypatch):
    app = stack["app"]
    monkeypatch.setattr(app, "GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setattr(app, "GOOGLE_CLIENT_SECRET", "sec")
    req = urllib.request.Request("http://127.0.0.1:%d/auth/google/start?utm_source=partner&ref=coach1" % stack["port"])

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    opener = urllib.request.build_opener(NoRedirect)
    try:
        opener.open(req)
    except urllib.error.HTTPError as e:
        loc, cookies = e.headers["Location"], e.headers.get_all("Set-Cookie")
    state = [c for c in cookies if c.startswith("g_state=")][0].split(";")[0].split("=", 1)[1]
    assert "." in state and ("state=" + state.replace(".", "%2E") in loc or "state=" + state in loc)
    enc = state.split(".", 1)[1]
    import analytics
    assert analytics.decode_attribution(base64.urlsafe_b64decode(enc + "=" * (-len(enc) % 4)).decode()) == \
        {"utm_source": "partner", "ref": "coach1"}
