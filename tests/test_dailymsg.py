"""The message a user gets when a search brings new picks (GTM Stage 2 row 5, 2026-11-15).

Before: a system log signed by Noa - "🎯 3 new role(s) for review", subject "Job Hunter
Notification", link re-check counts, raw errors, a daily "no new matches today". Now Noa says
what she found and why; nothing new means no message; errors and link counts stay with the admin.
"""
import inspect

import pytest

import auth
import dailymsg
import db as database

TOP = [("Senior Product Manager", "Lumen Health",
        "Your five years leading B2B SaaS roadmaps are exactly what their clinician platform needs. And more."),
       ("Product Manager, Growth", "Orbitly", "You've run onboarding experiments end to end, the core of this role."),
       ("Product Manager - Payments", "Kesef Labs", "")]


def test_seven_picks_name_the_best_and_say_why():
    m = dailymsg.new_picks("Maya", TOP, 7, "https://job-hunter.work/app")
    assert m["push"] == "Today's 7 are in. Senior Product Manager at Lumen Health is very you."
    assert m["subject"] == "Today's picks: Senior Product Manager at Lumen Health + 6 more"
    body = m["body"]
    assert body.startswith("Hi Maya,\n\nToday's 7 are in. The best of them:\n\n")
    # one line of reason under each job: the FIRST sentence of Noa's own "why this fits"
    assert ("Senior Product Manager at Lumen Health\nYour five years leading B2B SaaS roadmaps are exactly "
            "what their clinician platform needs.\n\n") in body
    assert "And more." not in body
    assert "Product Manager - Payments at Kesef Labs\n\n4 more are waiting." in body     # no reason: no empty line of one
    assert "https://job-hunter.work/app" in body
    assert body.endswith("Your personal AI headhunter agent at Job Hunter,\nNoa\n\n"
                         "Choose how I reach you in Settings → Notifications.")
    for old in ("new role(s)", "🎯", "🔗", "Dashboard:", "Job Hunter Notification"):
        assert old not in body and old not in m["push"] and old not in m["subject"]


def test_one_pick_two_picks_and_no_name():
    one = dailymsg.new_picks("Maya", TOP[:1], 1, "L")
    assert one["push"] == "Today's pick is in: Senior Product Manager at Lumen Health. It's very you."
    assert one["subject"] == "Today's pick: Senior Product Manager at Lumen Health"
    assert "more" not in one["body"] and "Swipe right if you like it:" in one["body"]
    two = dailymsg.new_picks("", TOP[:2], 2, "L")
    assert two["body"].startswith("Hi,\n\nToday's 2 are in.") and "more are waiting" not in two["body"]
    assert two["subject"].endswith("+ 1 more")
    four = dailymsg.new_picks("Maya", TOP, 4, "L")
    assert "1 more is waiting." in four["body"]
    # a job with no company, and no jobs at all, still read as sentences
    assert dailymsg.new_picks("Maya", [("Data Analyst", "", "")], 3, "L")["push"] == "Today's 3 are in. Data Analyst is very you."
    assert dailymsg.new_picks("Maya", [], 3, "L")["push"] == "Today's 3 are in."


def test_first_sentence():
    assert dailymsg.first_sentence("  One.  Two. ") == "One."
    assert dailymsg.first_sentence("No full stop here") == "No full stop here"
    assert dailymsg.first_sentence("") == "" and dailymsg.first_sentence(None) == ""
    long = dailymsg.first_sentence("word " * 80)
    assert len(long) <= 181 and long.endswith("…")


def test_a_search_the_user_started_says_more_not_todays():
    m = dailymsg.more_picks("Maya", TOP, 5, "L")
    assert m["push"] == "I found 5 more for you. Senior Product Manager at Lumen Health is very you."
    assert m["subject"] == "5 more picks: Senior Product Manager at Lumen Health + 4 more"
    assert m["body"].startswith("Hi Maya,\n\nYour search found 5 more. The best of them:")
    assert "2 more are waiting." in m["body"] and "Today's" not in m["body"]
    one = dailymsg.more_picks("", TOP[:1], 1, "L")
    assert one["push"] == "I found 1 more for you: Senior Product Manager at Lumen Health."
    assert one["body"].startswith("Hi,\n\nYour search found 1 more:")
    assert dailymsg.more_picks("Maya", [], 3, "L")["push"] == "I found 3 more for you."


@pytest.fixture
def env(tmp_path, monkeypatch):
    import app
    database.set_db_path(str(tmp_path / "t.db"))
    database.init_db()
    auth.set_db_getter(database.get_db)
    auth.create_user("Maya Levi", "maya@e.test", "pw123456")
    conn = database.get_db()
    uid = conn.execute("SELECT id FROM users WHERE email=?", ("maya@e.test",)).fetchone()["id"]
    conn.execute("UPDATE user_profiles SET notification_channel='email', email_address='maya@e.test' "
                 "WHERE user_id=?", (uid,))
    conn.commit()
    conn.close()
    pushes, emails = [], []
    monkeypatch.setattr(app, "send_web_push_to_user",
                        lambda u, m, suf="", url=None, title="": pushes.append(m) or
                        {"configured": True, "devices": 1, "sent": 1, "failed": 0, "errors": []})
    monkeypatch.setattr(app, "send_email", lambda **k: emails.append(k))
    return app, uid, pushes, emails


def test_push_gets_the_short_line_and_email_gets_noas_letter(env):
    app, uid, pushes, emails = env
    m = dailymsg.new_picks("Maya", TOP, 7, "https://job-hunter.work/app")
    res = app.deliver_notification(uid, m["push"], url_suffix="/app#new", subject=m["subject"], body=m["body"])
    assert pushes == [m["push"]]
    (mail,) = emails
    assert mail["subject"] == m["subject"] and mail["body"] == m["body"] and mail["as_noa"] is True
    assert "Dashboard:" not in mail["body"]
    assert res["email"] == "sent"


def test_no_email_when_another_email_covered_it(env):
    """The first search: "Noa found your first matches" has already gone, so only the push follows."""
    app, uid, pushes, emails = env
    res = app.deliver_notification(uid, "Today's 7 are in.", subject="s", body="b", email=False)
    assert pushes == ["Today's 7 are in."] and emails == []
    assert res["email"].startswith("skipped")


def test_a_plain_notification_is_unchanged(env):
    """Admin alerts and the apply summary still go as message + dashboard link."""
    app, uid, pushes, emails = env
    app.deliver_notification(uid, "New User Alert", url_suffix="/app")
    assert emails[0]["subject"] == "Job Hunter Notification"
    assert emails[0]["body"].startswith("New User Alert\n\n") and "Dashboard:" in emails[0]["body"]


def _job(uid, title, company, why, score, url):
    conn = database.get_db()
    conn.execute("INSERT INTO jobs (user_id, title, company, url, why_relevant, match_score, candidate_score, status, "
                 "found_date) VALUES (?,?,?,?,?,?,?,'new',datetime('now'))", (uid, title, company, url, why, score, score))
    conn.commit()
    conn.close()
    return {"title": title, "company": company, "url": url, "url_ok": 1}


def test_a_search_with_new_picks_sends_noas_message_with_the_best_first(env):
    app, uid, pushes, emails = env
    info = [_job(uid, "Product Manager - Payments", "Kesef Labs", "", 70, "https://x.test/3"),
            _job(uid, "Senior Product Manager", "Lumen Health", "Your roadmaps fit their platform. More.", 95, "https://x.test/1"),
            _job(uid, "Product Manager, Growth", "Orbitly", "You ran onboarding experiments.", 88, "https://x.test/2"),
            _job(uid, "Fourth", "Co", "", 60, "https://x.test/4")]
    res = app._notify_search_result(uid, 4, info, admin_lines=["Re-checked 14 existing job link(s): 12 alive, 2 dead."])
    assert pushes == ["Today's 4 are in. Senior Product Manager at Lumen Health is very you."]
    (mail,) = emails
    assert mail["subject"] == "Today's picks: Senior Product Manager at Lumen Health + 3 more"
    body = mail["body"]
    assert body.startswith("Hi Maya,") and "Your roadmaps fit their platform.\n" in body
    assert body.index("Lumen Health") < body.index("Orbitly") < body.index("Kesef Labs") and "Fourth" not in body
    assert "1 more is waiting." in body
    assert "Re-checked" not in body and "Admin only" not in body          # link counts never reach a user
    assert res["email"] == "sent"


def test_an_admin_sees_the_link_counts_under_their_own_letter(env):
    app, uid, pushes, emails = env
    conn = database.get_db()
    conn.execute("UPDATE users SET role='admin' WHERE id=?", (uid,))
    conn.commit()
    conn.close()
    info = [_job(uid, "Senior Product Manager", "Lumen Health", "Fits.", 95, "https://x.test/1")]
    app._notify_search_result(uid, 1, info, admin_lines=["Re-checked 14 existing job link(s): 12 alive, 2 dead."])
    assert emails[0]["body"].endswith("--\nAdmin only: Re-checked 14 existing job link(s): 12 alive, 2 dead.")
    assert "Re-checked" not in pushes[0]


def test_the_first_search_pushes_but_does_not_email_twice(env):
    app, uid, pushes, emails = env
    info = [_job(uid, "Senior Product Manager", "Lumen Health", "Fits.", 95, "https://x.test/1")]
    app._notify_search_result(uid, 1, info, first_search=True)
    assert len(pushes) == 1 and emails == []


def test_nothing_new_says_nothing(env):
    app, uid, pushes, emails = env
    assert app._notify_search_result(uid, 0, []) is None                         # nothing new
    assert app._notify_search_result(uid, 0, [], held_n=5, by_user=False) is None  # the scheduler fed the reserve
    assert app._notify_search_result(uid, 0, [], held_n=5, by_user=True) is None   # only dead/stale jobs left
    assert pushes == [] and emails == []


def test_a_search_the_user_started_pushes_its_own_picks_without_an_email(env):
    """2026-11-16: a user-started search brings up to 7 of its own, on top of today's.
    They are in the app, so a push (and Telegram / WhatsApp), no email."""
    app, uid, pushes, emails = env
    info = [_job(uid, "Senior Product Manager", "Lumen Health", "Fits.", 95, "https://x.test/1"),
            _job(uid, "Product Manager, Growth", "Orbitly", "Fits.", 88, "https://x.test/2")]
    res = app._notify_search_result(uid, 2, info, held_n=0, by_user=True)
    assert pushes == ["I found 2 more for you. Senior Product Manager at Lumen Health is very you."]
    assert emails == [] and res["email"].startswith("skipped")


def test_the_search_no_longer_sends_system_lines_to_users():
    import app
    src = inspect.getsource(app.run_job_search)
    for gone in ("new role(s) for review", "no new matches today", "Re-checked {hist_alive+hist_dead} existing job URL(s)"):
        assert gone not in src, gone
    assert "_notify_search_result(user_id, inserted, new_jobs_info, locked_n, held_n," in src
    # a failed search tells the user nothing: no deliver_notification after the failure is logged
    tail = src[src.index("Job search failed"):]
    assert "deliver_notification" not in tail
    # and the button the user presses says so: that is what gives its search picks of its own
    # (2026-11-24: the call also carries priority=PRIORITY_USER - test_search_pileup.py)
    assert 'jobqueue.enqueue(uid, "search", payload={"by_user": True},' in inspect.getsource(app)
