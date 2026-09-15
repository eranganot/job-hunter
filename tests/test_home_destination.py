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


@pytest.fixture
def legacy(monkeypatch):
    def _set(on):
        if on:
            monkeypatch.setenv("LEGACY_UI", "1")
        else:
            monkeypatch.delenv("LEGACY_UI", raising=False)
    return _set


def test_home_is_the_app_by_default():
    assert app.home_url() == "/app"


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_the_escape_hatch_works_without_a_deploy(monkeypatch, value):
    monkeypatch.setenv("LEGACY_UI", value)
    assert app.home_url() == "/dashboard", "LEGACY_UI=%r did not take effect" % value


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "  "])
def test_a_falsey_value_does_not_silently_flip_it_back(monkeypatch, value):
    """A variable that is set to "0" must not read as on. That mistake is how a
    kill switch ends up permanently engaged with nobody knowing why."""
    monkeypatch.setenv("LEGACY_UI", value)
    assert app.home_url() == "/app"


def test_signing_in_lands_on_the_app(stack):
    """The symptom, end to end: Eran said "staging still looks like the old
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


def test_a_non_admin_bounced_off_admin_lands_on_the_app(stack, users):
    status, location, _b = users["a"].get("/admin")
    assert status == 302 and location == "/app", location


def test_the_legacy_pages_are_still_reachable(stack, users):
    """Nothing is deleted yet. Someone who has bookmarked /dashboard, or needs a
    feature that has not been ported, can still get there by typing the URL."""
    for path in ("/dashboard", "/settings"):
        status, _loc, body = users["a"].get(path)
        assert status == 200, "%s is gone, and item 6 said nothing would be deleted yet" % path
        assert b"<!DOCTYPE html>" in body or b"<!doctype html>" in body
