"""tests/test_marketing.py - offers and news by email only with consent (GTM row 20).

Israeli anti-spam law: advertising email needs the person's prior consent, the
word "פרסומת" at the start of the subject, the sender's details and a free way
to refuse. These tests protect the parts a later change could quietly break:
the box starts unticked, every change of answer is logged with its date and
words, and only people who said yes are recipients.
"""
import re
import sqlite3

import marketing
from tests.test_routes import Client, stack  # noqa: F401

PW = "correct-horse-1"


def _db():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, email TEXT, is_active INTEGER DEFAULT 1,"
              " delete_after TEXT, marketing_opt_in_at TEXT)")
    c.execute("CREATE TABLE marketing_consent (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, granted INTEGER,"
              " source TEXT, wording TEXT, at TEXT)")
    c.executemany("INSERT INTO users (id,name,email) VALUES (?,?,?)",
                  [(1, "Dana", "dana@x"), (2, "Off", "off@x"), (3, "Gone", "gone@x"), (4, "Leaving", "leave@x")])
    return c


def test_only_changes_are_logged_with_date_source_and_words():
    c = _db()
    assert marketing.record(c, 1, True, "register") is True
    assert marketing.record(c, 1, True, "settings") is False        # same answer: no new row
    assert marketing.record(c, 1, False, "email") is True
    rows = c.execute("SELECT granted, source, wording, at FROM marketing_consent ORDER BY id").fetchall()
    assert [(r[0], r[1]) for r in rows] == [(1, "register"), (0, "email")]
    assert rows[0][2] == marketing.WORDING and re.match(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d", rows[0][3])
    assert marketing.opted_in(c, 1) is False
    assert marketing.record(c, 2, False, "settings") is False       # everyone starts at no


def test_recipients_are_only_active_people_who_said_yes():
    c = _db()
    for uid in (1, 3, 4):
        marketing.record(c, uid, True, "register")
    c.execute("UPDATE users SET is_active=0 WHERE id=3")
    c.execute("UPDATE users SET delete_after='2026-12-30 00:00:00' WHERE id=4")
    assert [r[0] for r in marketing.recipients(c)] == [1]


def test_subject_starts_with_the_word_the_law_asks_for_once():
    assert marketing.subject("New: tailored CVs in Hebrew") == "פרסומת: New: tailored CVs in Hebrew"
    assert marketing.subject("פרסומת: already") == "פרסומת: already"


def test_the_stop_link_is_signed_and_in_the_footer():
    url = marketing.stop_url("https://job-hunter.work/", 7)
    assert url.startswith("https://job-hunter.work/email/offers-off?u=7&t=")
    assert marketing.stop_ok(7, marketing.stop_token(7)) and not marketing.stop_ok(8, marketing.stop_token(7))
    assert not marketing.stop_ok("x", "y")
    foot = marketing.footer("https://job-hunter.work", 7)
    assert url in foot and "support@job-hunter.work" in foot


def test_unknown_source_is_refused():
    c = _db()
    try:
        marketing.record(c, 1, True, "import")
    except ValueError:
        return
    raise AssertionError("an unknown source was accepted")


# ── end to end ────────────────────────────────────────────────────────────────

def _register(stack, email, **extra):  # noqa: F811
    import ratelimit
    ratelimit.reset_all()
    c = Client(stack["port"])
    st, _loc, _ = c.post_form("/register", dict({"name": "Mia", "email": email, "password": PW, "password2": PW}, **extra))
    assert st == 302, st
    return c


def _consent(stack, email):
    conn = stack["db"].get_db()
    try:
        uid = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()[0]
        return uid, marketing.opted_in(conn, uid), [tuple(r) for r in conn.execute(
            "SELECT granted, source FROM marketing_consent WHERE user_id=? ORDER BY id", (uid,)).fetchall()]
    finally:
        conn.close()


def test_the_sign_up_box_starts_unticked_and_names_offers(stack):  # noqa: F811
    _st, _loc, body = Client(stack["port"]).get("/register")
    html = body.decode()
    box = re.search(r'<input type="checkbox" name="marketing"[^>]*>', html).group(0)
    assert "checked" not in box and 'form="regform"' in box
    assert "offers and news" in html and "{marketing_wording}" not in html


def test_ticking_the_box_at_sign_up_is_a_logged_yes_and_leaving_it_is_a_no(stack):  # noqa: F811
    _register(stack, "yes-offers@example.test", marketing="1")
    _uid, on, log = _consent(stack, "yes-offers@example.test")
    assert on and log == [(1, "register")]
    _register(stack, "no-offers@example.test")
    _uid, on, log = _consent(stack, "no-offers@example.test")
    assert not on and log == []


def test_settings_switch_and_the_email_link_change_the_answer(stack):  # noqa: F811
    c = _register(stack, "switch@example.test")
    st, _loc, body = c.get("/api/me")
    assert b'"marketing_opt_in": false' in body
    st, _loc, _b = c.post_json("/api/settings/marketing", {"on": True})
    assert st == 200
    uid, on, log = _consent(stack, "switch@example.test")
    assert on and log == [(1, "settings")]
    anon = Client(stack["port"])
    st, _loc, page = anon.get("/email/offers-off?u=%d&t=%s" % (uid, marketing.stop_token(uid)))
    assert st == 200 and b"Stop offers" in page
    assert _consent(stack, "switch@example.test")[1] is True          # a GET changes nothing
    st, _loc, _p = anon.post_form("/email/offers-off?u=%d&t=%s" % (uid, marketing.stop_token(uid)), {})
    assert st == 200
    assert _consent(stack, "switch@example.test")[1:] == (False, [(1, "settings"), (0, "email")])
    st, _loc, _p = anon.post_form("/email/offers-off?u=%d&t=bad" % uid, {})
    assert st == 400
