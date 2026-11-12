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


def _release(stack, uid, now=None):
    db = stack["db"].get_db()
    try:
        return entitlements.release(db, _user(stack, uid), now=now)
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
    assert plans.row("admin")["matches_per_day"] is None


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


def test_the_admin_sees_every_match_at_once(stack, users):
    uid = _uid(stack, "admin@example.test")
    _search(stack, uid, list(range(20)))
    assert "held" not in _status(stack, uid).values()


def test_held_jobs_do_not_exist_for_the_user_until_released(stack):
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


def test_the_app_knows_todays_picks_are_done():
    import glob, os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    js = "".join(open(f, encoding="utf-8").read() for f in glob.glob(os.path.join(root, "web_bundle", "assets", "*.js")))
    assert "That's today's picks" in js and "daily_picks" in js
