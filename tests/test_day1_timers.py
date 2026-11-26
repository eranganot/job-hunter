"""tests/test_day1_timers.py - the timers that start on launch day, run together on the
real schema through the scheduler's own wrappers (test week, item T1, 2026-11-25).

Each timer has its own unit test on a hand-made table. This one runs what the scheduler
runs on 1 Dec - app._run_inactive_sweep, _run_followups, _run_fit_survey and _run_probes -
on a database built by the real migrations, with the clock moved: the evening before,
the morning of Day 1, every half hour through the day, and the next day. It checks that
each fires on the day, only for the right people, and only once.
"""
from datetime import datetime, timedelta, timezone

import pytest

from tests.test_routes import Client, stack, users  # noqa: F401

UTC = timezone.utc
EVE = datetime(2026, 11, 30, 21, 0, tzinfo=UTC)            # 23:00 Israel, the night before
DAY1 = datetime(2026, 12, 1, 4, 0, tzinfo=UTC)             # 06:00 Israel
FMT = "%Y-%m-%d %H:%M:%S"


def ago(days, at=DAY1):
    return (at - timedelta(days=days)).strftime(FMT)


@pytest.fixture()
def world(stack, monkeypatch):  # noqa: F811
    import app
    import db as database
    import schedlock
    monkeypatch.setenv("LIFECYCLE_EMAILS", "1")
    monkeypatch.setenv("JH_FOLLOWUP_EMAILS", "1")       # off by default since 26 Nov; tested on here
    for k in ("JH_INACTIVE_FROM", "JH_FOLLOWUP_FROM", "JH_FIT_FROM", "JH_PROBES_UNTIL"):
        monkeypatch.delenv(k, raising=False)                # the defaults ship: 2026-12-01
    monkeypatch.setattr(app, "_sender", lambda who="noa": ("Eran <eran@job-hunter.work>", ""))
    monkeypatch.setattr(schedlock, "acquire", lambda *a, **k: True)
    monkeypatch.setattr(app, "_track", lambda *a, **k: None)
    mails = []
    monkeypatch.setattr(app, "_lifecycle_send",
                        lambda to, subject, body, sender, unsub: mails.append((to, subject, sender)))

    c = database.get_db()
    c.execute("DELETE FROM users WHERE email LIKE '%@drill.test'")
    people = {
        # name: (created, role, active, opted_out, finished setup)
        "quiet":    (ago(60), "user", 1, 0, 1),     # last decision 20 days ago -> paused + email
        "quietopt": (ago(60), "user", 1, 1, 1),     # same, opted out -> paused, no email
        "busy":     (ago(60), "user", 1, 0, 1),     # approved 2 days ago -> nothing
        "newbie":   (ago(5), "user", 1, 0, 1),      # 5 days old -> not paused
        "nosetup":  (ago(60), "user", 1, 0, 0),     # never finished setup -> never paused
        "disabled": (ago(60), "user", 0, 0, 1),     # disabled -> nothing at all
        "applier":  (ago(40), "user", 1, 0, 1),     # applied 11 days ago -> follow-up
        "fit14":    (ago(14.2), "user", 1, 0, 1),   # day 14, activated -> fit email
        "fit14no":  (ago(14.2), "user", 1, 0, 1),   # day 14, never approved -> no fit email; 14 quiet days -> paused
        "boss":     (ago(90), "admin", 1, 0, 1),    # admin -> never
    }
    ids = {}
    for name, (created, role, active, opt, setup) in people.items():
        cur = c.execute("INSERT INTO users (name, email, password_hash, salt, role, is_active, created_date, lifecycle_opt_out) "
                        "VALUES (?,?,?,?,?,?,?,?)", (name.title(), name + "@drill.test", "x", "x", role, active, created, opt))
        uid = cur.lastrowid or c.execute("SELECT id FROM users WHERE email=?", (name + "@drill.test",)).fetchone()[0]
        ids[name] = uid
        c.execute("INSERT INTO user_profiles (user_id, onboarding_complete) VALUES (?,?)", (uid, setup))
    act = [("quiet", "job_approved", ago(20)), ("quietopt", "job_approved", ago(20)),
           ("busy", "job_approved", ago(2)), ("applier", "job_applied", ago(11)),
           ("fit14", "job_approved", ago(3)), ("fit14no", "notification", ago(1))]
    for n, ev, at in act:
        c.execute("INSERT INTO activity_log (user_id, event_type, created_date) VALUES (?,?,?)", (ids[n], ev, at))
    for n in ("fit14", "fit14no"):
        c.execute("INSERT INTO funnel_events (user_id, step, created_at) VALUES (?,?,?)", (ids[n], "signup", ago(14.2)))
    c.execute("INSERT INTO funnel_events (user_id, step, created_at) VALUES (?,?,?)", (ids["fit14"], "first_approve", ago(13)))
    c.execute("INSERT INTO jobs (user_id, title, company, url, status, applied_via, applied_date) VALUES (?,?,?,?,?,?,?)",
              (ids["applier"], "Senior PM", "Lumen", "https://lumen.example/jobs/1", "applied", "manual", ago(10.6)))
    c.execute("INSERT INTO jobs (user_id, title, company, url, status, applied_via, applied_date) VALUES (?,?,?,?,?,?,?)",
              (ids["applier"], "PM Pay", "Kesef", "https://kesef.example/jobs/2", "applied", "bulk", ago(10.6)))
    c.commit()
    c.close()
    return {"app": app, "db": database, "ids": ids, "mails": mails}


def _tick(app, at):
    app._run_inactive_sweep(now=at)
    app._run_followups(now=at)
    app._run_fit_survey(now=at)


def test_day1_timers_fire_once_on_the_day_for_the_right_people(world):
    app, database, ids, mails = world["app"], world["db"], world["ids"], world["mails"]

    # The evening before: nothing.
    _tick(app, EVE)
    assert mails == []
    c = database.get_db()
    assert c.execute("SELECT COUNT(*) FROM users WHERE email LIKE '%@drill.test' AND search_paused_at IS NOT NULL").fetchone()[0] == 0

    # Day 1, every half hour from 06:00 to 23:30 Israel time, then the next day.
    at = DAY1
    while at < DAY1 + timedelta(hours=42):
        _tick(app, at)
        at += timedelta(minutes=30)

    paused = {r[0] for r in c.execute("SELECT email FROM users WHERE email LIKE '%@drill.test' "
                                      "AND search_paused_at IS NOT NULL")}
    assert paused == {"quiet@drill.test", "quietopt@drill.test", "fit14no@drill.test"}
    to_list = sorted(to for to, _, _ in mails)
    assert to_list == ["applier@drill.test", "fit14@drill.test", "fit14no@drill.test", "quiet@drill.test"], to_list
    subj = {to: s for to, s, _ in mails}
    assert subj["quiet@drill.test"] == "Still looking, Quiet?"
    # Senders: the pause and the fit question from Eran, the follow-up from Noa.
    by = {to: s for to, _, s in mails}
    assert by["fit14no@drill.test"] == "eran" and by["quiet@drill.test"] == "eran" and by["fit14@drill.test"] == "eran" and by["applier@drill.test"] == "noa"
    # The follow-up email went out at or after 11:30 Israel time, never before.
    # Only the real application was asked about; the bulk "mark applied" was not.
    asked = {r[0] for r in c.execute("SELECT title FROM jobs WHERE user_id=? AND followup_at IS NOT NULL",
                                     (ids["applier"],))}
    assert asked == {"Senior PM"}
    c.close()


def test_followup_waits_for_1130_israel_time(world):
    app, mails = world["app"], world["mails"]
    app._run_followups(now=datetime(2026, 12, 1, 9, 29, tzinfo=UTC))      # 11:29 Israel
    assert mails == []
    app._run_followups(now=datetime(2026, 12, 1, 9, 30, tzinfo=UTC))      # 11:30 Israel
    assert [m[0] for m in mails] == ["applier@drill.test"]


def test_probe_accounts_search_until_the_eve_and_switch_off_on_day1(world):
    app, database = world["app"], world["db"]
    app._run_probes(now=EVE)                                               # 23:00 Israel, 30 Nov
    c = database.get_db()
    q = "SELECT COUNT(*) FROM users WHERE email LIKE 'probe-%@job-hunter.test' AND is_active=1"
    try:
        on = c.execute(q).fetchone()[0]
        app._run_probes(now=datetime(2026, 11, 30, 22, 30, tzinfo=UTC))   # 00:30 Israel, 1 Dec
        off = c.execute(q).fetchone()[0]
        total = c.execute("SELECT COUNT(*) FROM users WHERE email LIKE 'probe-%@job-hunter.test'").fetchone()[0]
    finally:
        c.close()
    assert (on, off, total) == (3, 0, 3)


def test_followup_is_off_unless_switched_on(world, monkeypatch):
    """The owner, 26 Nov: no "Did they get back to you?" until the app has the screens."""
    app, mails = world["app"], world["mails"]
    monkeypatch.delenv("JH_FOLLOWUP_EMAILS")
    app._run_followups(now=datetime(2026, 12, 1, 10, 0, tzinfo=UTC))
    assert mails == []
