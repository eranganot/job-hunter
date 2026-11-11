"""Step 6 (2026-11-11): Terms of Service and Privacy Policy.

The pages are public, and every fact they state about the app is pinned here to
the code that makes it true - so changing a retention period, a cookie or a
provider without changing the policy fails the suite instead of making the
policy quietly wrong."""
import os
import re

import pytest

import legal
from tests.test_routes import Client, stack  # noqa: F401

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


@pytest.mark.parametrize("path,heading", [("/terms", "Terms of Service"), ("/privacy", "Privacy Policy")])
def test_the_pages_are_public(stack, path, heading):
    status, location, body = Client(stack["port"]).get(path)
    assert status == 200 and location is None
    page = body.decode()
    assert "<h1>%s</h1>" % heading in page
    assert "support@job-hunter.work" in page
    assert "<script" not in page


def test_no_character_the_server_would_strip():
    """send_html drops U+0080-U+00FF; such characters must be entities."""
    for page in (legal.terms_html("https://x.test"), legal.privacy_html("https://x.test")):
        assert not [ch for ch in page if 0x80 <= ord(ch) <= 0xff]


def test_signup_and_the_landing_page_link_to_both(stack):
    c = Client(stack["port"])
    for path in ("/register", "/login", "/"):
        page = c.get(path)[2].decode()
        assert 'href="/terms"' in page and 'href="/privacy"' in page, path


def test_retention_periods_match_the_code():
    import accountdata
    import auth
    p = legal.privacy_html()
    assert auth.SESSION_DAYS == 14 and "14 days without use" in p
    assert accountdata.GRACE_DAYS == 14 and "14-day grace period" in p
    assert auth.VERIFY_TTL_S == 48 * 3600 and auth.RESET_TTL_S == 3600 and "48 hours and 1 hour" in p
    assert 'os.environ.get("JH_LLM_RETENTION_DAYS", "90")' in _read("app.py")
    assert "<td>Usage counts of AI features</td><td>90 days</td>" in p
    assert "llm_usage" in accountdata._KEEP_ANONYMISED and "Anonymous usage counts" in p
    t = legal.terms_html()
    assert "14-day grace period" in t and "18 years old" in t


def test_every_cookie_and_storage_key_listed_is_real_and_none_is_missing():
    p = legal.privacy_html()
    code = _read("app.py") + _read("auth.py")
    set_cookies = set(re.findall(r'(?:Set-Cookie", f?"|return f")([a-z_]+)=', code))
    assert set_cookies == {"session", "g_state", "g_verifier"}, set_cookies
    for name in set_cookies:
        assert "<code>%s</code>" % name in p, name
    web = ""
    for dirpath, _dirs, files in os.walk(os.path.join(ROOT, "web", "src")):
        for fn in files:
            if fn.endswith((".ts", ".tsx")):
                web += _read(dirpath, fn)
    keys = set(re.findall(r'localStorage\.(?:setItem|getItem)\("([^"]+)"', web))
    assert keys, "no localStorage keys found - the pattern drifted"
    for k in keys:
        assert "<code>%s</code>" % k in p, "localStorage key %r is not in the Privacy Policy" % k
    nav = re.findall(r'const NAV_KEY = "([^"]+)"', web)
    assert nav and "<code>%s</code>" % nav[0] in p, "the sessionStorage key is not in the Privacy Policy"
    assert web.count("sessionStorage.setItem(") == 1, "a new sessionStorage key: add it to the policy"
    assert "document.cookie" not in web and "indexedDB" not in web, "a new client-side store: add it to the policy"


def test_the_processors_named_are_the_ones_the_code_calls():
    p = legal.privacy_html()
    code = "".join(_read(f) for f in os.listdir(ROOT) if f.endswith(".py"))
    for provider, evidence in (("Resend", "import resend"), ("Gemini", "GEMINI_API_KEY"),
                               ("Telegram", "api.telegram.org"), ("Twilio", "api.twilio.com")):
        assert evidence in code, "%s: %s no longer in the code - update the policy" % (provider, evidence)
        assert provider in p, provider
    errors = _read("errors.py")
    assert "send_default_pii=False" in errors and "include_local_variables=False" in errors
    assert "Sentry (EU region)" in p and "stack-frame variables are not sent" in p
    assert "Railway" in p and "EU West, Amsterdam" in p
