"""Daily picks (the owner, 2026-11-12): at most 7 new matches reach a user per
Israel day, best first; the rest wait in Noa's reserve (status 'held'),
unseen and unmentioned, and come in on the following days."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from tests.test_routes import Client, stack, users  # noqa: F401

import entitlements
import plans


def _uid(stack, email):
    db = stack["db"].get_db()
    try:
        return db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()["id"]
    finally:
        db.close()


def _user(stack, uid):
    db = stack["db"].get_db()
    try:
        return dict(db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone())
    finally:
        db.close()


def _search(stack, uid, scores, found=None, url_verified=None):
    """Insert one search's jobs the way run_job_search does (status 'new'),
    then hold them. Returns the ids, in insert order."""
    db = stack["db"].get_db()
    try:
        after = db.execute("SELECT COALESCE(MAX(id), 0) FROM jobs").fetchone()[0]
        ids = []
        for i, sc in enumerate(scores):
            cur = db.execute(
                "INSERT INTO jobs (user_id, title, company, url, status, match_score, found_date, url_verified) "
                "VALUES (?,?,?,?, 'new', ?, ?, ?)",
                (uid, "Job %d" % sc, "Co %d" % i, "https://example.test/%d/%d/%d" % (uid, after, i), sc,
                 found or datetime.now(timezone.utc).strftime("%Y-%m-%d"), url_verified))
            ids.append(cur.lastrowid)
        db.commit()
        entitlements.hold_new_matches(db, _user(stack, uid), after)
        return ids
    finally:
        db.close()


def _status(stack, uid):
    db = stack["db"].get_db()
    try:
        return {r["id"]: r["status"] for r in db.execute("SELECT id, status FROM jobs WHERE user_id=?", (uid,))}
    finally:
        db.close()


def _release(stack, uid, now=None, by_user=False):
    db = stack["db"].get_db()
    try:
        return entitlements.release(db, _user(stack, uid), now=now, by_user=by_user)
    finally:
        db.close()


def _fresh_user(stack, email):
    import ratelimit
    ratelimit.reset_all()
    c = Client(stack["port"])
    c.post_form("/register", {"name": "Dana", "email": email,
                              "password": "pw-long-enough-1", "password2": "pw-long-enough-1"})
    return c, _uid(stack, email)


def test_every_plan_gets_seven_a_day_and_admin_is_not_capped():
    assert plans.MATCHES_PER_DAY == 7
    for name in ("free", "premium", "beta", "expert"):
        assert plans.row(name)["matches_per_day"] == 7, name
    assert plans.row("admin")["matches_per_day"] == 7          # the owner, 2026-11-13


def test_a_big_first_search_is_a_week_of_picks_best_first(stack):
    _c, uid = _fresh_user(stack, "dp-1@example.test")
    ids = _search(stack, uid, list(range(60, 80)))          # 20 matches, scores 60..79
    assert set(_status(stack, uid).values()) == {"held"}
    day1 = _release(stack, uid)
    assert len(day1) == 7
    st = _status(stack, uid)
    assert sorted(i for i, s in st.items() if s == "new") == ids[-7:]      # the 7 best
    assert _release(stack, uid) == []                                       # same day: nothing more
    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
    assert len(_release(stack, uid, now=tomorrow)) == 7
    assert len(_release(stack, uid, now=tomorrow + timedelta(days=1))) == 6   # the last 6
    assert "held" not in _status(stack, uid).values()


def test_a_later_search_tops_up_today_and_its_best_can_beat_the_reserve(stack):
    _c, uid = _fresh_user(stack, "dp-2@example.test")
    _search(stack, uid, [50, 51, 52])
    assert len(_release(stack, uid)) == 3                   # 3 of today's 7
    later = _search(stack, uid, [90, 91, 92, 93, 94, 95])
    out = _release(stack, uid)
    assert [i for i, _u in out] == [later[5], later[4], later[3], later[2]]   # 4 more, best first
    assert sum(1 for s in _status(stack, uid).values() if s == "new") == 7


def test_dead_links_and_stale_jobs_never_come_out_of_the_reserve(stack):
    _c, uid = _fresh_user(stack, "dp-3@example.test")
    old = (datetime.now(timezone.utc) - timedelta(days=entitlements.HOLD_DAYS + 1)).strftime("%Y-%m-%d")
    stale = _search(stack, uid, [99], found=old)
    dead = _search(stack, uid, [98], url_verified=0)
    good = _search(stack, uid, [10])
    assert [i for i, _u in _release(stack, uid)] == good
    st = _status(stack, uid)
    assert st[stale[0]] == "held" and st[dead[0]] == "held"


def test_missed_days_do_not_pile_up(stack):
    _c, uid = _fresh_user(stack, "dp-4@example.test")
    _search(stack, uid, list(range(30)))
    in_a_week = datetime.now(timezone.utc) + timedelta(days=7)
    assert len(_release(stack, uid, now=in_a_week)) == 7


def test_the_admin_gets_daily_picks_like_everyone(stack, users):
    # The owner, 2026-11-13: "set my cap to 7 as well". Was: admin saw every match at once.
    uid = _uid(stack, "admin@example.test")
    _search(stack, uid, list(range(20)))
    assert list(_status(stack, uid).values()).count("held") == 20


def test_held_jobs_do_not_exist_for_the_user_until_released(stack, monkeypatch):
    _at_israel(monkeypatch, stack, 2026, 11, 23, 20)        # Monday evening: no search still to come
    c, uid = _fresh_user(stack, "dp-5@example.test")
    ids = _search(stack, uid, list(range(10)))
    held = ids[0]                                            # the worst: stays in the reserve today
    me = json.loads(c.get("/api/me")[2])                    # opening the app releases today's picks
    assert me["daily_picks"] == {"cap": 7, "today": 7}
    for q in ("all", "new", "held"):
        jobs = json.loads(c.get("/api/jobs?status=" + q)[2])
        jobs = jobs.get("jobs", jobs) if isinstance(jobs, dict) else jobs
        assert held not in [j["id"] for j in jobs], q
        if q != "held":
            assert len(jobs) == 7, q
    assert c.post_json("/api/jobs/%d/approve" % held, {})[0] == 404
    assert c.post_json("/api/jobs/bulk", {"ids": [held], "action": "approve"})[0] == 200
    assert _status(stack, uid)[held] == "held"
    stats = json.loads(c.get("/api/stats")[2])
    assert stats["total"] == 7 and stats["new"] == 7


def test_a_search_the_user_starts_brings_up_to_seven_more_on_top_of_todays(stack):
    """The owner, 2026-11-16: "the cap should be per search and not per day ... I do
    want to give the option to run the search again"."""
    _c, uid = _fresh_user(stack, "dp-6@example.test")
    _search(stack, uid, list(range(60, 80)))               # 20 in the reserve
    assert len(_release(stack, uid)) == 7                   # today's 7
    assert _release(stack, uid) == []                       # the day's picks are done
    more = _search(stack, uid, [95, 96, 97])                # the user searches again
    out = _release(stack, uid, by_user=True)
    assert len(out) == 7                                    # its own 7, best of the reserve first
    assert [i for i, _u in out][:3] == [more[2], more[1], more[0]]
    db = stack["db"].get_db()
    try:
        assert entitlements.released_today(db, uid) == 7    # the extra 7 do not use up the day
        kinds = [r[0] for r in db.execute(
            "SELECT release_kind FROM jobs WHERE user_id=? AND status='new'", (uid,))]
    finally:
        db.close()
    assert sorted(kinds, key=str) == sorted([None] * 7 + ["search"] * 7, key=str)
    assert len(_release(stack, uid, by_user=True)) == 7     # and again: another 7
    assert _release(stack, uid) == []                       # the app opening still adds nothing today
    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
    assert len(_release(stack, uid, now=tomorrow)) == 2     # tomorrow's picks: what is left


def test_a_user_search_before_the_days_picks_leaves_the_day_untouched(stack):
    _c, uid = _fresh_user(stack, "dp-7@example.test")
    _search(stack, uid, list(range(10)))
    assert len(_release(stack, uid, by_user=True)) == 7
    assert len(_release(stack, uid)) == 3                   # the day's picks still come: the 3 left


def test_the_app_knows_todays_picks_are_done():
    import glob, os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    js = "".join(open(f, encoding="utf-8").read() for f in glob.glob(os.path.join(root, "web_bundle", "assets", "*.js")))
    assert "That's today's picks" in js and "daily_picks" in js
    # 2026-11-16: the end-of-day screen offers another search again
    assert "Want more now?" in js


def test_noa_speaks_in_the_app():
    """The owner approved three more places for Noa (2026-11-12): the job card's
    reason, the dashboard greeting and the end-of-day screen."""
    import glob, os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    js = "".join(open(f, encoding="utf-8").read() for f in glob.glob(os.path.join(root, "web_bundle", "assets", "*.js")))
    for s in ("Why Noa picked this", "I brought you", "That's today's picks", "/landing-assets/noa-"):
        assert s in js, s
    assert "Why this is a strong fit" not in js


# ── An app open before today's scheduled search (2026-11-22) ──────────────────
# Opening the app released the day's picks without a notification, so the
# scheduled search found nothing left to release and - by design - said
# nothing. The owner opens the app most mornings and got no notification for a
# week. While the search is still to come today, an app open leaves the reserve
# for it.

def _at_israel(monkeypatch, stack, y, m, d, hour, minute=0):
    from zoneinfo import ZoneInfo
    fixed = datetime(y, m, d, hour, minute, tzinfo=ZoneInfo("Asia/Jerusalem"))
    monkeypatch.setattr(stack["app"], "israel_now", lambda now_utc=None: fixed)
    return fixed


def _schedule(stack, uid, hour=11, freq="daily", weekdays_only=0, plan=None, paused=None):
    db = stack["db"].get_db()
    try:
        db.execute("UPDATE user_profiles SET search_hour=?, schedule_frequency=?, search_day_of_week=NULL, "
                   "weekdays_only=? WHERE user_id=?", (hour, freq, weekdays_only, uid))
        db.execute("UPDATE users SET search_paused_at=?" + (", plan=?" if plan else "") + " WHERE id=?",
                   ((paused, plan, uid) if plan else (paused, uid)))
        db.commit()
    finally:
        db.close()


def _premium(stack, uid):
    """A daily-search plan, so the day of the week does not matter."""
    _schedule(stack, uid, plan="premium")


def _me(c):
    return json.loads(c.get("/api/me")[2])["daily_picks"]


def test_an_app_open_before_the_search_leaves_the_picks_for_it_and_the_search_announces_them(stack, monkeypatch):
    c, uid = _fresh_user(stack, "dp-8@example.test")
    _premium(stack, uid)
    _search(stack, uid, list(range(60, 75)))                # 15 in the reserve from earlier days
    _at_israel(monkeypatch, stack, 2026, 11, 23, 9, 14)     # 09:14, search at 11:00
    assert stack["app"]._scheduled_search_pending(uid)
    assert _me(c) == {"cap": 7, "today": 0}                 # the app open released nothing
    jobs = json.loads(c.get("/api/jobs?status=all")[2])
    assert (jobs.get("jobs", jobs) if isinstance(jobs, dict) else jobs) == []   # nor did the job list
    assert set(_status(stack, uid).values()) == {"held"}
    # 11:00 - the scheduled search releases the day's picks, so it has something to say
    sent = []
    monkeypatch.setattr(stack["app"], "deliver_notification", lambda u, msg, **kw: sent.append((u, msg)) or {})
    released = _release(stack, uid)
    assert len(released) == 7
    info = [{"title": "t", "company": "c", "url": u} for _i, u in released]
    stack["app"]._notify_search_result(uid, len(released), info)
    assert len(sent) == 1 and sent[0][0] == uid


def test_once_the_search_has_run_or_its_hour_has_passed_the_app_releases(stack, monkeypatch):
    app = stack["app"]
    c, uid = _fresh_user(stack, "dp-9@example.test")
    _premium(stack, uid)
    _search(stack, uid, list(range(20)))
    _at_israel(monkeypatch, stack, 2026, 11, 23, 11, 5)     # the search hour, search not yet logged
    assert app._scheduled_search_pending(uid)
    db = stack["db"].get_db()                               # the search ran (09:05 UTC = 11:05 Israel)
    try:
        db.execute("INSERT INTO activity_log (user_id, event_type, details, created_date) "
                   "VALUES (?, 'jobs_searched', 'Found 0 new job(s)', '2026-11-23 09:05:00')", (uid,))
        db.commit()
    finally:
        db.close()
    assert not app._scheduled_search_pending(uid)
    _at_israel(monkeypatch, stack, 2026, 11, 24, 12)        # next day, after the hour, no search logged
    assert not app._scheduled_search_pending(uid)           # it failed or was skipped: do not wait forever
    assert _me(c)["today"] == 7


def test_no_search_today_means_the_app_releases_as_before(stack, monkeypatch):
    app = stack["app"]
    _c, uid = _fresh_user(stack, "dp-10@example.test")
    _schedule(stack, uid, plan="free")                      # Free searches weekly, on Sunday
    _at_israel(monkeypatch, stack, 2026, 11, 23, 8)         # Monday 08:00
    assert not app._scheduled_search_pending(uid)
    _at_israel(monkeypatch, stack, 2026, 11, 22, 8)         # Sunday 08:00: its search is today
    assert app._scheduled_search_pending(uid)
    _schedule(stack, uid, plan="premium", weekdays_only=1)
    _at_israel(monkeypatch, stack, 2026, 11, 21, 8)         # Saturday, weekdays only
    assert not app._scheduled_search_pending(uid)
    _schedule(stack, uid, plan="premium", paused="2026-11-01 00:00:00")
    _at_israel(monkeypatch, stack, 2026, 11, 23, 8)         # paused for inactivity: no search coming
    assert not app._scheduled_search_pending(uid)


def test_the_scheduler_and_the_app_agree_on_which_days_have_a_search():
    import app
    assert app._search_runs_today("daily", None, 0, None, 0)
    assert not app._search_runs_today("daily", None, 1, None, 5)          # Saturday, weekdays only
    assert not app._search_runs_today("weekly", 6, 0, None, 0)            # weekly on Sunday, Monday today
    assert app._search_runs_today("weekly", 6, 0, None, 6)
    assert not app._search_runs_today("daily", None, 0, "2026-11-01", 0)  # paused
