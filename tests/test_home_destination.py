"""
tests/test_home_destination.py - Phase 4 item 6: /app is the product now.

Signing in used to land on /dashboard - the legacy UI - while only /register
went to /app. So the convergence work was invisible to anyone who already had
an account: they signed in and met the old design, every time.

The flip is one function, `home_url()`, and one Railway variable. LEGACY_UI=1
sends everyone back without a deploy, which is the whole reason it is a
variable and not an edit: if something is missing from /app, the way back is a
console toggle, not a rollback.

Nothing is deleted. The legacy pages stay reachable by typing the URL for one
release.
"""
import pytest

import app
from tests.test_routes import Client, stack, users  # noqa: F401


def test_home_is_the_app(monkeypatch):
    for value in ("1", "true", "0", ""):              # the LEGACY_UI switch is gone (Step 9)
        monkeypatch.setenv("LEGACY_UI", value)
        assert app.home_url() == "/app"


def test_signing_in_lands_on_the_app(stack):
    """The symptom, end to end: The owner said "staging still looks like the old
    design" and the answer was that signing in sent him there."""
    c = Client(stack["port"])
    c.post_form("/register", {"name": "flip", "email": "flip@example.test",
                              "password": "correct-horse-1", "password2": "correct-horse-1"})
    c2 = Client(stack["port"])
    status, location, _b = c2.post_form(
        "/login", {"email": "flip@example.test", "password": "correct-horse-1"})
    assert status == 302
    assert location == "/app", "signing in still lands on %r" % location


def test_an_already_signed_in_user_asking_for_login_is_sent_to_the_app(stack, users):
    status, location, _b = users["a"].get("/login")
    assert status == 302 and location == "/app", location


def test_an_already_signed_in_user_asking_for_register_is_sent_to_the_app(stack, users):
    status, location, _b = users["a"].get("/register")
    assert status == 302 and location == "/app", location


def test_admin_url_opens_the_app_dashboard(stack, users):
    for who in ("a", "admin"):
        status, location, _b = users[who].get("/admin")
        assert status == 302 and location == "/app?view=dashboard", (who, location)


@pytest.mark.parametrize("path, dest", [("/dashboard", "/app"), ("/dashboard/", "/app"),
                                        ("/settings", "/app"), ("/onboarding", "/app"),
                                        ("/admin", "/app?view=dashboard")])
def test_every_legacy_url_redirects_into_the_app(stack, users, path, dest):
    """Step 9 deleted the legacy pages (DASHBOARD/SETTINGS/ONBOARDING/ADMIN_HTML
    and the frozen Tailwind blob). Their URLs live on in bookmarks, installed
    icons and old notifications, so each redirects - and never serves HTML."""
    status, location, body = users["a"].get(path)
    assert (status, location) == (302, dest)
    assert b"<html" not in body.lower()


def test_the_frozen_tailwind_blob_is_gone(stack):
    assert Client(stack["port"]).get("/static/tw.css")[0] == 404
