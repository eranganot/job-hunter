"""Setup ends -> "Noa is finding your first matches" -> an email when they're
ready (2026-11-12). The first matches are the activation moment."""
import json

import pytest

from tests.test_routes import Client, stack, users  # noqa: F401


@pytest.fixture
def mails(stack, monkeypatch):
    out = []
    monkeypatch.setattr(stack["app"], "send_email", lambda to, subj, body, *a, **k: out.append((to, subj, body)))
    import ratelimit
    ratelimit.reset_all()
    return out


def _new_user(stack, email):
    c = Client(stack["port"])
    c.post_form("/register", {"name": "Maya Cohen", "email": email,
                              "password": "pw-long-enough-1", "password2": "pw-long-enough-1"})
    db = stack["db"].get_db()
    uid = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()["id"]
    db.close()
    return c, uid


def _me(c):
    return json.loads(c.get("/api/me")[2])


def test_me_says_the_first_search_is_running_until_it_finishes(stack, mails):
    c, uid = _new_user(stack, "fm-1@example.test")
    assert _me(c)["first_search_running"] is False                 # nothing queued yet
    c.post_json("/api/save-profile", {"job_titles": ["Product Manager"]})
    c.post_json("/api/save-schedule", {"onboarding_complete": 1})
    assert _me(c)["first_search_running"] is True
    stack["app"]._funnel(uid, "first_search")                         # the search finished
    assert _me(c)["first_search_running"] is False


def test_a_later_search_is_not_the_first(stack, users, mails):
    app = stack["app"]
    db = stack["db"].get_db()
    uid = db.execute("SELECT id FROM users WHERE email='bob@example.test'").fetchone()["id"]
    db.close()
    app._funnel(uid, "first_search")
    import jobqueue
    jobqueue.enqueue(uid, "search")
    assert _me(users["b"])["first_search_running"] is False


def test_the_email_goes_once_in_noas_voice(stack, mails):
    app = stack["app"]
    _c, uid = _new_user(stack, "fm-2@example.test")
    assert app._funnel(uid, "first_search") is True
    app._first_matches_email(uid, 7)
    assert app._funnel(uid, "first_search") is False                  # second time: no email path
    (to, subj, body), = [m for m in mails if "Noa" in m[1]]
    assert to == "fm-2@example.test" and subj == "Noa found your first 7 matches"
    assert body.startswith("Hi Maya,") and "/app" in body and body.rstrip().endswith("Job Hunter")


def test_an_empty_first_search_says_what_to_change(stack, mails):
    app = stack["app"]
    _c, uid = _new_user(stack, "fm-3@example.test")
    app._first_matches_email(uid, 0)
    (_to, subj, body), = [m for m in mails if "Noa" in m[1]]
    assert "came back empty" in subj and "Add a title" in body


def test_the_test_account_gets_no_email(stack, users, mails):
    app = stack["app"]
    d = json.loads(users["admin"].post_json("/api/admin/test-account/reset", {})[2])
    db = stack["db"].get_db()
    tid = db.execute("SELECT id FROM users WHERE email=?", (d["email"],)).fetchone()["id"]
    db.close()
    app._first_matches_email(tid, 5)
    assert [m for m in mails if "Noa" in m[1]] == []


def test_the_app_shows_the_waiting_screen():
    import glob, os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    js = "".join(open(f, encoding="utf-8").read() for f in glob.glob(os.path.join(root, "web_bundle", "assets", "*.js")))
    assert "Noa is finding your first matches" in js and "first_search_running" in js


def test_the_cv_fills_linkedin_and_phone_but_never_overwrites(stack, mails, monkeypatch):
    """analyze_cv always extracted LinkedIn and phone; /api/analyze-cv dropped
    them (the owner, 2026-11-12: setup should fill the profile from the CV too)."""
    app = stack["app"]
    c, uid = _new_user(stack, "fm-cv@example.test")
    monkeypatch.setattr(app, "GEMINI_KEY", "test-key", raising=False)
    monkeypatch.setattr(app, "_cv_file", lambda *_a, **_k: "/tmp/cv.pdf")
    cv = {"job_titles": ["Data Analyst"], "keywords": ["SQL"], "locations": ["Tel Aviv"], "summary": "s",
          "linkedin_url": "https://www.linkedin.com/in/maya-cohen", "phone": "+972 50 123 4567",
          "email": "maya.cohen@gmail.example", "full_name": "Maya R. Cohen"}
    monkeypatch.setattr(app, "analyze_cv", lambda *_a, **_k: dict(cv))
    out = json.loads(c.post_json("/api/analyze-cv", {})[2])
    assert out["profile_filled"] == ["email_address", "linkedin_url", "phone"]   # the name was given at sign-up
    me = _me(c)
    assert me["linkedin_url"] == cv["linkedin_url"] and me["phone"] == cv["phone"]
    assert me["email_address"] == cv["email"] and me["name"] == "Maya Cohen"
    c.post_json("/api/save-profile", {"phone": "+972 52 999 9999"})        # the user's own number
    cv["phone"] = "+972 50 000 0000"
    out = json.loads(c.post_json("/api/analyze-cv", {})[2])
    assert out["profile_filled"] == [] and _me(c)["phone"] == "+972 52 999 9999"


def test_job_mail_comes_from_noa_only_once_her_address_is_set(stack, monkeypatch):
    """The owner, 2026-11-12: job-search mail from Noa, account mail from support@.
    Until NOA_FROM is set (after the Cloudflare route exists) nothing changes."""
    app = stack["app"]
    monkeypatch.setattr(app, "RESEND_FROM", "Job Hunter <support@job-hunter.work>")
    monkeypatch.setattr(app, "NOA_FROM", "")
    assert app._sender(True) == ("Job Hunter <support@job-hunter.work>", "")
    monkeypatch.setattr(app, "NOA_FROM", "Noa from Job Hunter <noa@job-hunter.work>")
    assert app._sender(True) == ("Noa from Job Hunter <noa@job-hunter.work>", "noa@job-hunter.work")
    assert app._sender(False) == ("Job Hunter <support@job-hunter.work>", "")      # verification, password
    monkeypatch.setattr(app, "RESEND_FROM", "Job Hunter <%s>" % app.RESEND_SANDBOX_FROM)
    assert app._sender(True)[0].endswith("<%s>" % app.RESEND_SANDBOX_FROM)       # never on the sandbox
