"""Contact support (The owner, 2026-11-09): a signed-in user's message is emailed to
the admin with Reply-To set to the user, so answering is a plain Reply.

What must hold: no account, no message; the email really goes out before the
route says "sent", and a failed send is reported as a failure; 5 a day per
user; nothing past SUPPORT_MAX_CHARS."""
import json

import pytest

import ratelimit
from tests.test_routes import Client, stack, users  # noqa: F401


@pytest.fixture
def outbox(stack, monkeypatch):  # noqa: F811
    """Capture send_email calls in-process (the route calls app.send_email)."""
    sent = []
    app = stack["app"]

    def fake_send(to_addr, subject, body, reply_to="", **_kw):
        sent.append({"to": to_addr, "subject": subject, "body": body, "reply_to": reply_to})

    monkeypatch.setattr(app, "send_email", fake_send)
    monkeypatch.setattr(app, "SUPPORT_EMAIL", "support-inbox@example.test")
    ratelimit.reset_all()
    yield sent
    ratelimit.reset_all()


def test_a_message_reaches_the_support_inbox_with_reply_to_the_user(users, outbox):  # noqa: F811
    st, _l, body = users["a"].post_json("/api/support", {"subject": "Bug\nhere", "message": "The swipe froze."})
    assert st == 200 and json.loads(body) == {"success": True}
    assert len(outbox) == 1
    m = outbox[0]
    assert m["to"] == "support-inbox@example.test"
    assert m["reply_to"] == "alice@example.test"
    assert m["subject"] == "[Job Hunter support] Bug here"           # no header injection
    assert "The swipe froze." in m["body"] and "Account id:" in m["body"] and "Plan: Beta" in m["body"]


def test_signed_out_requests_send_nothing(stack, outbox):  # noqa: F811
    st, _l, _b = Client(stack["port"]).post_json("/api/support", {"message": "hi"})
    assert st in (401, 302, 403) and outbox == []


@pytest.mark.parametrize("payload, code", [
    ({"message": "   "}, "empty"), ({}, "empty"), ({"message": "x" * 5001}, "too_long")])
def test_empty_and_oversized_messages_are_refused(users, outbox, payload, code):  # noqa: F811
    st, _l, body = users["a"].post_json("/api/support", payload)
    assert st == 400 and json.loads(body)["code"] == code and outbox == []


def test_five_a_day_then_429(users, outbox):  # noqa: F811
    for _ in range(5):
        assert users["a"].post_json("/api/support", {"message": "hi"})[0] == 200
    st, _l, body = users["a"].post_json("/api/support", {"message": "hi"})
    assert st == 429 and json.loads(body)["code"] == "rate_limited"
    assert len(outbox) == 5
    assert users["b"].post_json("/api/support", {"message": "hi"})[0] == 200   # per user, not global


def test_a_failed_send_is_a_failure_not_sent(stack, users, monkeypatch, caplog):  # noqa: F811
    ratelimit.reset_all()

    def boom(*_a, **_k):
        raise RuntimeError("RESEND_API_KEY not configured")

    monkeypatch.setattr(stack["app"], "send_email", boom)
    st, _l, body = users["a"].post_json("/api/support", {"message": "hello"})
    assert st == 503 and json.loads(body)["code"] == "send_failed"
    assert "[support] ERROR message from user" in caplog.text
    ratelimit.reset_all()


def test_send_email_passes_reply_to_to_resend(monkeypatch):
    import sys
    import types
    sent = []
    module = types.ModuleType("resend")
    module.Emails = type("E", (), {"send": staticmethod(lambda p: sent.append(p) or {"id": "x"})})
    monkeypatch.setitem(sys.modules, "resend", module)
    import app
    monkeypatch.setattr(app, "RESEND_API_KEY", "k")
    monkeypatch.setattr(app, "RESEND_VERIFIED_EMAIL", "owner@example.test")   # no personal default in code
    app.send_email(app.RESEND_VERIFIED_EMAIL, "s", "b", reply_to="u@example.test")
    assert sent[0]["reply_to"] == "u@example.test" and sent[0]["to"] == [app.RESEND_VERIFIED_EMAIL]
    app.send_email(app.RESEND_VERIFIED_EMAIL, "s", "b")
    assert "reply_to" not in sent[1]


def test_no_personal_address_is_a_code_default(monkeypatch):
    """Launch cleanup (2026-11-10): the support inbox and the web-push contact
    come from configuration (RESEND_VERIFIED_EMAIL / JH_SUPPORT_EMAIL /
    VAPID_SUBJECT, falling back to ADMIN_EMAIL), never from an address in the code."""
    import re
    src = open("app.py", encoding="utf-8").read()
    assert not re.search(r"[\w.+-]+@gmail\.com", src)
