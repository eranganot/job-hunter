"""
tests/test_auth_pages.py - the sign-in and sign-up pages.

These are the only screens a stranger sees before they have an account, and
they were the last two still styled by /static/tw.css - a frozen gzip+base64
Tailwind blob inside app.py that no build step regenerates. The first
impression of the product depended on an artifact nobody could rebuild.

They are now self-contained dark pages matching /app. What these tests protect
is not the styling but the things a restyle silently breaks: the field names
the POST handlers read, the error slot, and the Google button.
"""
import re

import pytest

from tests.test_routes import Client, stack, users  # noqa: F401

PAGES = ("/login", "/register")


@pytest.mark.parametrize("path", PAGES)
def test_the_page_renders_for_a_stranger(stack, path):
    status, _loc, body = Client(stack["port"]).get(path)
    assert status == 200
    assert b"<!DOCTYPE html>" in body


@pytest.mark.parametrize("path", PAGES)
def test_the_page_carries_no_external_stylesheet(stack, path):
    """The whole point of the restyle: no dependency on a file that cannot be
    rebuilt. If a <link rel=stylesheet> comes back, the page can go unstyled in
    production while every test here still passes."""
    _st, _loc, body = Client(stack["port"]).get(path)
    html = body.decode("utf-8", "replace")
    assert 'rel="stylesheet"' not in html, "%s links an external stylesheet again" % path
    assert "tw.css" not in html, "%s depends on the frozen Tailwind blob again" % path


@pytest.mark.parametrize("path, fields", [
    ("/login", ["email", "password"]),
    ("/register", ["name", "email", "password", "password2"]),
])
def test_the_form_still_posts_the_names_the_handler_reads(stack, path, fields):
    """A restyle that renames an input renames it into a 500. The handlers read
    these by name; nothing else connects the markup to them."""
    _st, _loc, body = Client(stack["port"]).get(path)
    html = body.decode("utf-8", "replace")
    assert re.search(r'<form[^>]+method="POST"[^>]+action="%s"' % path, html), \
        "%s no longer posts to itself" % path
    for f in fields:
        assert re.search(r'name="%s"' % f, html), "%s lost its %r input" % (path, f)


@pytest.mark.parametrize("path", PAGES)
def test_google_sign_in_is_still_offered(stack, path):
    _st, _loc, body = Client(stack["port"]).get(path)
    assert b"/auth/google/start" in body, "%s lost the Google button" % path


@pytest.mark.parametrize("path", PAGES)
def test_the_error_slot_is_filled_not_printed(stack, path):
    """{error_block} is substituted by .replace(). If a restyle drops or renames
    it, the placeholder renders to the user as literal text and real errors
    vanish - both at once."""
    import app
    html = getattr(app, "LOGIN_HTML" if path == "/login" else "REGISTER_HTML")
    assert "{error_block}" in html, "%s lost its error slot" % path

    _st, _loc, body = Client(stack["port"]).get(path)
    assert b"{error_block}" not in body, "%s renders the placeholder to the user" % path


def test_a_failed_login_shows_the_reason(stack):
    """End to end: the slot is not just present, it carries a real message."""
    status, _loc, body = Client(stack["port"]).post_form(
        "/login", {"email": "nobody@example.test", "password": "wrong-password-1"})
    assert status == 200
    html = body.decode("utf-8", "replace")
    assert 'class="err"' in html, "a failed sign-in rendered no error block"
    assert "{error_block}" not in html


def test_a_signed_in_user_is_not_shown_the_login_page(stack, users):
    status, location, _b = users["a"].get("/login")
    assert status == 302 and location, "an authenticated user was served the sign-in form"
