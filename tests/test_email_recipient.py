"""
tests/test_email_recipient.py - the email goes to the person it is addressed to.

`send_email(to_addr, ...)` took a recipient and threw it away:

    actual_to = RESEND_VERIFIED_EMAIL

Every user's email notification went to the admin's inbox. Proven 2026-09-20 by
driving deliver_notification for a user whose address is
dana@somewhere-else.test - Resend was handed to=['eran.ganot@gmail.com'] - and
the caller logged "Sent OK" either way, so nothing anywhere said otherwise.

It came from Resend's shared sandbox sender (onboarding@resend.dev), which can
genuinely only deliver to the account's own verified address. That is a real
constraint; silently redirecting other people's mail is not a way to live with
it. While the sandbox sender is in use the send is now REFUSED, which the
callers already turn into a logged failure - so the state of the world is
"nobody got it" and it says so, rather than "the wrong person got it" and it
says delivered.

These tests exist because the failure was invisible from every angle an
operator has: the API call succeeded, the log said Sent OK, and the admin -
the only person who could notice - was receiving the mail.
"""
import sys
import types

import pytest


@pytest.fixture
def resend_stub(monkeypatch):
    """Capture what the SDK is handed, without a network call."""
    sent = []

    class _Emails:
        @staticmethod
        def send(payload):
            sent.append(payload)
            return {"id": "stub"}

    module = types.ModuleType("resend")
    module.Emails = _Emails
    module.api_key = None
    monkeypatch.setitem(sys.modules, "resend", module)
    monkeypatch.setenv("RESEND_API_KEY", "test-key")
    import app
    monkeypatch.setattr(app, "RESEND_API_KEY", "test-key")
    return sent


def _verified(monkeypatch, addr="owner@example.test"):
    import app
    monkeypatch.setattr(app, "RESEND_VERIFIED_EMAIL", addr)
    return addr


def test_a_verified_sender_delivers_to_the_addressed_recipient(resend_stub, monkeypatch):
    """The whole point. With a real sender, to_addr is honoured."""
    import app
    _verified(monkeypatch)
    monkeypatch.setattr(app, "RESEND_FROM", "Job Hunter <alerts@jobhunter.example>")
    app.send_email("dana@somewhere-else.test", "Subject", "Body")
    assert resend_stub, "nothing was sent"
    assert resend_stub[0]["to"] == ["dana@somewhere-else.test"], (
        "the recipient was rewritten: %r" % (resend_stub[0]["to"],))
    assert resend_stub[0]["from"] == "Job Hunter <alerts@jobhunter.example>"


def test_the_sandbox_sender_refuses_a_third_party_recipient(resend_stub, monkeypatch):
    """The old behaviour, now an exception instead of a redirect.

    This is the assertion that would have failed before the fix - loudly, which
    is the entire difference.
    """
    import app
    _verified(monkeypatch)
    monkeypatch.setattr(app, "RESEND_FROM", "Job Hunter <onboarding@resend.dev>")
    with pytest.raises(RuntimeError) as exc:
        app.send_email("dana@somewhere-else.test", "Subject", "Body")
    assert "dana@somewhere-else.test" in str(exc.value), \
        "the refusal does not say whose mail was about to be misdirected"
    assert not resend_stub, "it sent anyway"


def test_the_sandbox_sender_still_mails_the_verified_address(resend_stub, monkeypatch):
    """Admin alerts must keep working on the sandbox sender; that path is legal."""
    import app
    owner = _verified(monkeypatch)
    monkeypatch.setattr(app, "RESEND_FROM", "Job Hunter <onboarding@resend.dev>")
    app.send_email(owner.upper(), "Subject", "Body")   # case must not matter
    assert resend_stub and resend_stub[0]["to"] == [owner.upper()]


def test_an_empty_recipient_is_an_error_not_a_redirect(resend_stub, monkeypatch):
    """A missing address used to become 'send it to the admin'."""
    import app
    _verified(monkeypatch)
    monkeypatch.setattr(app, "RESEND_FROM", "Job Hunter <alerts@jobhunter.example>")
    with pytest.raises(RuntimeError):
        app.send_email("", "Subject", "Body")
    assert not resend_stub


def test_the_notification_path_does_not_report_success_on_a_refusal(resend_stub,
                                                                    monkeypatch, tmp_path):
    """The log is the only place an operator would ever see this.

    A refusal has to land as FAILED. If deliver_notification swallowed it and
    wrote Sent OK, the fix would have changed who receives nothing while leaving
    the record just as wrong.
    """
    import app, db as database, auth
    database.set_db_path(str(tmp_path / "t.db"))
    database.init_db()
    auth.set_db_getter(database.get_db)
    auth.create_user("Dana", "dana@somewhere-else.test", "pw123456")
    conn = database.get_db()
    uid = conn.execute("SELECT id FROM users WHERE email=?",
                       ("dana@somewhere-else.test",)).fetchone()["id"]
    conn.execute("UPDATE user_profiles SET notification_channel='email', "
                 "email_address=? WHERE user_id=?", ("dana@somewhere-else.test", uid))
    conn.commit(); conn.close()

    _verified(monkeypatch)
    monkeypatch.setattr(app, "RESEND_FROM", "Job Hunter <onboarding@resend.dev>")
    monkeypatch.setattr(app, "send_web_push_to_user", lambda *a, **k: None)

    logged = []
    monkeypatch.setattr(app, "_log_notification",
                        lambda uid_, ch, status, *a: logged.append((ch, status)))

    app.deliver_notification(uid, "You have 3 new roles")

    assert not resend_stub, "a third party's address was mailed"
    assert logged, "nothing was recorded at all"
    assert any("FAIL" in status.upper() for _ch, status in logged), \
        "a refused send was recorded as %r" % (logged,)
