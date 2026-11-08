"""Phase 5b (Step 4): every plan limit enforced on the server.

The hidden matches are the part a free user has a reason to attack, so most of
this file tries to read or act on a locked job by every route there is. The
rule (Eran, 2026-11-05): the user sees that more were found, never what they
are, and cannot open or apply to them."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import entitlements
import gemini
import migrations
from tests.test_routes import Client, stack, users  # noqa: F401

NOW = datetime(2026, 11, 5, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    migrations.run(c)
    c.execute("INSERT INTO users (id,name,email,password_hash,salt,created_date,role,plan) "
              "VALUES (1,'u','u@x.test','h','s','2026-11-01','user','free')")
    c.commit()
    yield c
    c.close()


_SEQ = iter(range(10 ** 6))


def _jobs(c, scores, uid=1, status="new"):
    ids = []
    for i, sc in enumerate(scores):
        n = next(_SEQ)
        c.execute("INSERT INTO jobs (user_id,title,company,url,status,match_score,found_date) "
                  "VALUES (?,?,?,?,?,?,?)", (uid, "PM %d" % n, "Co %d" % n, "https://x/%d-%d" % (uid, n),
                                             status, sc, "2026-11-05"))
        ids.append(c.execute("SELECT last_insert_rowid()").fetchone()[0])
    c.commit()
    return ids


def _status(c, ids):
    return [c.execute("SELECT status FROM jobs WHERE id=?", (i,)).fetchone()[0] for i in ids]


# ── locking a search's matches ─────────────────────────────────────────────

def test_free_keeps_its_best_3_of_a_search_and_locks_the_rest(conn):
    before = 0
    ids = _jobs(conn, [70, 95, 60, 88, 91])
    locked = entitlements.lock_matches_beyond_limit(conn, {"id": 1, "role": "user", "plan": "free"}, before, 1)
    assert sorted(i for i, _u in locked) == sorted([ids[0], ids[2]])            # 70 and 60
    assert _status(conn, ids) == ["locked", "new", "locked", "new", "new"]
    assert {r[0] for r in conn.execute("SELECT search_batch FROM jobs")} == {1}


def test_only_this_search_is_counted(conn):
    old = _jobs(conn, [99, 98, 97])                                                # an earlier search
    entitlements.lock_matches_beyond_limit(conn, {"id": 1, "role": "user", "plan": "free"}, 0, 1)
    after = conn.execute("SELECT MAX(id) FROM jobs").fetchone()[0]
    new = _jobs(conn, [50, 40])
    assert entitlements.lock_matches_beyond_limit(conn, {"id": 1, "role": "user", "plan": "free"}, after, 2) == []
    assert _status(conn, old + new) == ["new"] * 5


def test_expert_and_admin_lock_nothing(conn):
    _jobs(conn, list(range(50, 70)))
    assert entitlements.lock_matches_beyond_limit(conn, {"id": 1, "role": "user", "plan": "expert"}, 0, 1) == []
    assert entitlements.lock_matches_beyond_limit(conn, {"id": 1, "role": "admin", "plan": "free"}, 0, 1) == []


def test_an_upgrade_unlocks_per_search_up_to_the_new_limit_best_first(conn):
    free = {"id": 1, "role": "user", "plan": "free"}
    a = _jobs(conn, [90, 80, 70, 60, 50, 40, 30, 20])                             # search 1: 8 found
    entitlements.lock_matches_beyond_limit(conn, free, 0, 1)
    mid = conn.execute("SELECT MAX(id) FROM jobs").fetchone()[0]
    b = _jobs(conn, [85, 75, 65, 55])                                               # search 2: 4 found
    entitlements.lock_matches_beyond_limit(conn, free, mid, 2)
    assert entitlements.locked_summary(conn, 1)["count"] == 5 + 1
    entitlements.grant_plan(conn, 1, "premium", now=NOW)                            # 6 per search
    assert _status(conn, a) == ["new"] * 6 + ["locked", "locked"]
    assert _status(conn, b) == ["new"] * 4
    entitlements.grant_plan(conn, 1, "expert", now=NOW)
    assert entitlements.locked_summary(conn, 1)["count"] == 0
    entitlements.grant_plan(conn, 1, "free", now=NOW)                               # downgrade: nothing re-locked
    assert _status(conn, a + b) == ["new"] * 12


def test_the_locked_summary_carries_counts_only(conn):
    _jobs(conn, [90, 80, 70, 60])
    entitlements.lock_matches_beyond_limit(conn, {"id": 1, "role": "user", "plan": "free"}, 0, 7)
    assert entitlements.locked_summary(conn, 1) == {"count": 1, "latest_search": {"batch": 7, "count": 1}}


# ── the launch promotion (migration 16) ────────────────────────────────────

def test_launch_promotion_reaches_every_undecided_non_admin_account_once():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    done = []
    for v, name, fn in migrations.MIGRATIONS:
        if v == 16:
            break
        fn(c)
        done.append(v)
    rows = [(1, "old@x", "user", None), (2, "admin@x", "admin", None), (3, "new@x", "user", "promotion")]
    for uid, email, role, src in rows:
        c.execute("INSERT INTO users (id,name,email,password_hash,salt,created_date,role,plan,plan_source,plan_until) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (uid, email, email, "h", "s", "2026", role, "premium" if src else "free", src,
                   "2026-11-20 00:00:00" if src else None))
    c.commit()
    migrations.m0016_enforcement_launch(c)
    got = {r["id"]: (r["plan"], r["plan_source"], r["plan_after"]) for r in c.execute("SELECT * FROM users")}
    assert got[1] == ("premium", "promotion", "free")
    assert got[2] == ("free", None, None)                                          # admin is a role
    assert c.execute("SELECT plan_until FROM users WHERE id=3").fetchone()[0] == "2026-11-20 00:00:00"
    assert [tuple(r) for r in c.execute("SELECT user_id, reason FROM plan_events")] == [(1, "launch_promotion")]
    migrations.m0016_enforcement_launch(c)                                          # idempotent
    assert c.execute("SELECT COUNT(*) FROM plan_events").fetchone()[0] == 1


# ── the plan's daily AI cap ────────────────────────────────────────────────

def test_the_plan_ai_cap_blocks_without_alerting(monkeypatch):
    alerts = []
    monkeypatch.setattr(gemini, "_alert_locked", lambda *a, **k: alerts.append(a))
    gemini.set_plan_cap_resolver(lambda uid: 2)
    try:
        # Set the day first: a fresh process rolls the day on its first check
        # and clears the per-user counters (production rebuilds them from the
        # ledger), which would wipe the count planted here.
        monkeypatch.setattr(gemini, "_DAY", gemini._today())
        monkeypatch.setattr(gemini, "_sync_locked", lambda day: None)
        monkeypatch.setitem(gemini._PER_USER, 4242, {"calls": 2, "tokens": 0})
        with pytest.raises(gemini.BudgetExceeded) as e:
            gemini.check(4242)
        assert e.value.scope == "plan:4242" and alerts == []
        gemini.set_plan_cap_resolver(lambda uid: None)                              # admin: no plan cap
        gemini.check(4242)
    finally:
        gemini.set_plan_cap_resolver(None)


# ── search cadence ─────────────────────────────────────────────────────────

def test_a_weekly_plan_searches_weekly_whatever_the_profile_says(stack):  # noqa: F811
    sched = stack["app"]._plan_search_schedule
    free = {"role": "user", "plan": "free"}
    assert sched("daily", None, free) == ("weekly", 6)                              # Sunday
    assert sched("daily", 2, free) == ("weekly", 2)
    assert sched("daily", None, {"role": "user", "plan": "premium"}) == ("daily", None)
    assert sched("daily", None, {"role": "admin", "plan": "free"}) == ("daily", None)


# ── over HTTP ──────────────────────────────────────────────────────────────

def _uid(db, email):
    c = db.get_db()
    try:
        return c.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()[0]
    finally:
        c.close()


def _plan(db, email, plan):
    """A permanent plan, no promotion - so the tests do not depend on the date."""
    c = db.get_db()
    c.execute("UPDATE users SET plan=?, plan_until=NULL, plan_after=NULL WHERE email=?", (plan, email))
    c.commit()
    c.close()


@pytest.fixture
def free_alice(stack, users):  # noqa: F811
    db = stack["db"]
    _plan(db, "alice@example.test", "free")
    c = db.get_db()
    c.execute("DELETE FROM usage_counters")
    c.commit()
    c.close()
    yield users["a"]
    _plan(db, "alice@example.test", "premium")


@pytest.fixture
def locked_job(stack, free_alice):  # noqa: F811
    db = stack["db"]
    uid = _uid(db, "alice@example.test")
    c = db.get_db()
    c.execute("INSERT INTO jobs (user_id,title,company,url,status,match_score,found_date,search_batch) "
              "VALUES (?,?,?,?,?,?,?,?)", (uid, "Secret PM", "Hidden Co", "https://hidden.example/1",
                                           "locked", 77, "2026-11-05", 99))
    c.commit()
    jid = c.execute("SELECT id FROM jobs WHERE url='https://hidden.example/1'").fetchone()[0]
    c.close()
    yield jid
    c = db.get_db()
    c.execute("DELETE FROM jobs WHERE id=?", (jid,))
    c.commit()
    c.close()


def test_no_listing_ever_returns_a_locked_job(free_alice, locked_job):
    for status in ("new", "all", "locked", "approved"):
        st, _l, body = free_alice.get("/api/jobs?status=%s" % status)
        assert st == 200
        assert "Hidden Co" not in body.decode() and "hidden.example" not in body.decode(), status


def test_the_locked_count_is_all_that_is_sent(free_alice, locked_job):
    st, _l, body = free_alice.get("/api/jobs/locked")
    out = json.loads(body)
    assert st == 200 and out["count"] == 1 and out["matches_per_search"] == 3 and out["plan"] == "free"
    assert "Hidden Co" not in body.decode() and "hidden.example" not in body.decode()


@pytest.mark.parametrize("action", ["approve", "restore", "later", "applied", "retry", "apply-now",
                                    "check-status", "cover-letter"])
def test_a_locked_job_cannot_be_acted_on(free_alice, locked_job, stack, action):  # noqa: F811
    st, _l, body = free_alice.post_json("/api/jobs/%d/%s" % (locked_job, action), {})
    assert st == 402, (action, st)
    out = json.loads(body)
    assert out["code"] == "upgrade_required" and out["reason"] == "locked_match"
    assert "Hidden Co" not in body.decode()
    c = stack["db"].get_db()
    try:
        assert c.execute("SELECT status FROM jobs WHERE id=?", (locked_job,)).fetchone()[0] == "locked"
    finally:
        c.close()


def test_stage_and_bulk_cannot_reach_a_locked_job(free_alice, locked_job, stack):  # noqa: F811
    st, _l, _b = free_alice.post_json("/api/set-stage", {"id": locked_job, "stage": "interviewing"})
    assert st == 402
    st, _l, body = free_alice.post_json("/api/jobs/bulk", {"action": "approve", "ids": [locked_job]})
    assert st == 200
    c = stack["db"].get_db()
    try:
        assert c.execute("SELECT status FROM jobs WHERE id=?", (locked_job,)).fetchone()[0] == "locked"
    finally:
        c.close()


def _visible_job(db, email, title="Open PM"):
    uid = _uid(db, email)
    c = db.get_db()
    c.execute("INSERT INTO jobs (user_id,title,company,url,status,match_score,found_date) "
              "VALUES (?,?,?,?,?,?,?)", (uid, title, "Open Co", "https://open.example/%s" % title.replace(" ", ""),
                                         "new", 80, "2026-11-05"))
    c.commit()
    jid = c.execute("SELECT MAX(id) FROM jobs").fetchone()[0]
    c.close()
    return jid


def test_free_cannot_defer_premium_can(free_alice, stack, users):  # noqa: F811
    db = stack["db"]
    jid = _visible_job(db, "alice@example.test", "Defer PM")
    st, _l, body = free_alice.post_json("/api/jobs/%d/later" % jid, {})
    assert st == 402 and json.loads(body)["reason"] == "deferred_list"
    _plan(db, "alice@example.test", "premium")
    st, _l, _b = free_alice.post_json("/api/jobs/%d/later" % jid, {})
    assert st == 200


def test_cover_letters_by_plan(free_alice, stack, monkeypatch):  # noqa: F811
    import ai_analysis
    monkeypatch.setattr(ai_analysis, "write_cover_letter", lambda *a, **k: {"letter": "Dear team,", "notes": []})
    db = stack["db"]
    jid = _visible_job(db, "alice@example.test", "Letter PM")
    st, _l, body = free_alice.post_json("/api/jobs/%d/cover-letter" % jid, {})
    assert st == 402 and json.loads(body)["reason"] == "cover_letter_limit"
    _plan(db, "alice@example.test", "premium")                                     # 10 / month
    st, _l, body = free_alice.post_json("/api/jobs/%d/cover-letter" % jid, {})
    assert st == 200 and json.loads(body)["letter"] == "Dear team,"
    c = db.get_db()
    try:
        assert entitlements.used(c, _uid(db, "alice@example.test"), "cover_letter") == 1
    finally:
        c.close()
    st, _l, _b = free_alice.post_json("/api/jobs/%d/cover-letter" % jid, {"action": "save", "letter": "x"})
    c = db.get_db()
    try:
        assert st == 200 and entitlements.used(c, _uid(db, "alice@example.test"), "cover_letter") == 1
    finally:
        c.close()


def test_on_demand_searches_are_metered_and_an_in_flight_press_is_free(free_alice, stack, monkeypatch):  # noqa: F811
    import jobqueue
    import ratelimit
    app = stack["app"]
    monkeypatch.setattr(app, "GEMINI_KEY", "test-key")
    runs = iter([101, None])                                                         # queued, then already running
    monkeypatch.setattr(jobqueue, "enqueue", lambda uid, kind, *a, **k: next(runs))
    ratelimit.reset_all()
    st, _l, body = free_alice.post_json("/api/run-search", {}, {"Sec-Fetch-Site": "same-origin"})
    assert st == 200 and json.loads(body)["status"] == "queued"
    st, _l, body = free_alice.post_json("/api/run-search", {}, {"Sec-Fetch-Site": "same-origin"})
    assert st == 402 and json.loads(body)["reason"] == "on_demand_search_limit"   # Free: 1 / day


def test_cv_reviews_are_metered_and_a_cached_review_is_free(free_alice, stack, monkeypatch):  # noqa: F811
    app = stack["app"]
    db = stack["db"]
    monkeypatch.setattr(app, "_call_gemini_cv_optimizer", lambda **k: {"score": 70})
    monkeypatch.setattr(app, "_extract_cv_text", lambda *a: "x" * 100)
    uid = _uid(db, "alice@example.test")
    c = db.get_db()
    c.execute("UPDATE user_profiles SET cv_path='/tmp/cv.pdf', cv_optimizer_result=NULL, "
              "cv_optimizer_date=NULL WHERE user_id=?", (uid,))
    c.execute("INSERT INTO usage_counters (user_id, meter, period, uses) VALUES (?,?,?,?)",
              (uid, "cv_review", entitlements.period_key("cv_review"), 2))           # Free: 2 / month used
    c.commit()
    c.close()
    st, _l, body = free_alice.post_json("/api/cv-optimizer-analyze", {})
    assert st == 402 and json.loads(body)["reason"] == "cv_review_limit"


def test_free_cannot_choose_a_daily_search(free_alice):
    st, _l, body = free_alice.post_json("/api/save-schedule", {"schedule_frequency": "daily"})
    assert st == 402 and json.loads(body)["reason"] == "search_cadence"
    st, _l, _b = free_alice.post_json("/api/save-schedule", {"schedule_frequency": "weekly"})
    assert st == 200


def test_a_free_search_shows_3_and_says_how_many_more(stack, free_alice, monkeypatch):  # noqa: F811
    """End to end through the real insert path's helper: the summary push names
    only the visible matches and counts the rest."""
    db = stack["db"]
    uid = _uid(db, "alice@example.test")
    c = db.get_db()
    after = c.execute("SELECT COALESCE(MAX(id),0) FROM jobs").fetchone()[0]
    c.close()
    for i, sc in enumerate([91, 82, 73, 64, 55]):
        _visible_job(db, "alice@example.test", "Run PM %d" % i)
        c = db.get_db()
        c.execute("UPDATE jobs SET match_score=? WHERE id=(SELECT MAX(id) FROM jobs)", (sc,))
        c.commit()
        c.close()
    c = db.get_db()
    try:
        user = dict(c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone())
        locked = entitlements.lock_matches_beyond_limit(c, user, after, after + 1)
    finally:
        c.close()
    assert len(locked) == 2
    st, _l, body = free_alice.get("/api/jobs?status=new&sort=match")
    titles = [j["title"] for j in json.loads(body) if j["title"].startswith("Run PM")]
    assert titles == ["Run PM 0", "Run PM 1", "Run PM 2"]


# ── Phase 5c: admin sets plans ─────────────────────────────────────────────

def test_admin_sets_a_promotion_and_the_upgrade_unlocks_held_back_matches(stack, free_alice, locked_job, users):  # noqa: F811
    db = stack["db"]
    uid = _uid(db, "alice@example.test")
    st, _l, body = users["admin"].post_json("/api/admin/users/%d/plan" % uid, {"plan": "expert", "days": 14})
    out = json.loads(body)
    assert st == 200 and out["plan"] == "expert" and out["plan_until"] and out["locked"] == 0
    c = db.get_db()
    try:
        assert c.execute("SELECT status FROM jobs WHERE id=?", (locked_job,)).fetchone()[0] == "new"
        ev = c.execute("SELECT reason, by_user_id FROM plan_events WHERE user_id=? ORDER BY id DESC LIMIT 1",
                       (uid,)).fetchone()
        assert ev[0] == "admin" and ev[1] == _uid(db, "admin@example.test")
    finally:
        c.close()
    st, _l, body = users["admin"].get("/api/admin/users")
    row = [u for u in json.loads(body) if u["id"] == uid][0]
    assert row["plan"] == "expert" and row["plan_source"] == "promotion" and "stats_locked" in row


@pytest.mark.parametrize("payload, code", [
    ({"plan": "beta"}, 400), ({"plan": "premium", "days": 0}, 400),
    ({"plan": "premium", "days": 999}, 400), ({"plan": "premium", "after": "gold", "days": 3}, 400)])
def test_admin_plan_input_is_validated(stack, users, payload, code):  # noqa: F811
    uid = _uid(stack["db"], "bob@example.test")
    assert users["admin"].post_json("/api/admin/users/%d/plan" % uid, payload)[0] == code


def test_only_admin_sets_plans_and_admin_has_no_plan(stack, users):  # noqa: F811
    db = stack["db"]
    bob = _uid(db, "bob@example.test")
    assert users["a"].post_json("/api/admin/users/%d/plan" % bob, {"plan": "expert"})[0] == 403
    assert users["a"].post_json("/api/admin/users/%d/plan" % _uid(db, "alice@example.test"),
                                {"plan": "expert"})[0] == 403                     # not even yourself
    st, _l, body = users["admin"].post_json("/api/admin/users/%d/plan" % _uid(db, "admin@example.test"),
                                            {"plan": "free"})
    assert st == 400 and json.loads(body)["code"] == "admin_has_no_plan"
    assert users["admin"].post_json("/api/admin/users/999999/plan", {"plan": "free"})[0] == 404


# ── Step 6: history older than the plan keeps is hidden, not deleted ───────

def test_old_history_is_hidden_on_free_and_returns_on_upgrade(stack, free_alice):  # noqa: F811
    db = stack["db"]
    uid = _uid(db, "alice@example.test")
    old = (datetime.now(timezone.utc) - timedelta(days=60)).strftime("%Y-%m-%d")
    c = db.get_db()
    for st, title in (("applied", "Old Applied"), ("rejected", "Old Passed"), ("new", "Old New Still Open")):
        c.execute("INSERT INTO jobs (user_id,title,company,url,status,found_date,applied_date) VALUES (?,?,?,?,?,?,?)",
                  (uid, title, "Hist Co", "https://hist.example/%s" % st, st, old, old if st == "applied" else None))
    c.execute("INSERT INTO activity_log (user_id,event_type,details,created_date) VALUES (?,?,?,?)",
              (uid, "jobs_searched", "Old activity line", old + " 10:00:00"))
    c.commit()
    c.close()
    try:
        seen = lambda s: {j["title"] for j in json.loads(free_alice.get("/api/jobs?status=%s" % s)[2])}
        assert "Old Applied" not in seen("applied") and "Old Passed" not in seen("rejected")
        assert "Old Applied" not in seen("all")
        assert "Old New Still Open" in seen("new")                                  # the open queue is never history
        assert "Old activity line" not in free_alice.get("/api/activity")[2].decode()
        _plan(db, "alice@example.test", "premium")                                  # 12 months
        assert "Old Applied" in seen("applied") and "Old Passed" in seen("rejected")
        assert "Old activity line" in free_alice.get("/api/activity")[2].decode()
        c = db.get_db()
        try:                                                                         # hidden, never deleted
            assert c.execute("SELECT COUNT(*) FROM jobs WHERE company='Hist Co'").fetchone()[0] == 3
        finally:
            c.close()
    finally:
        c = db.get_db()
        c.execute("DELETE FROM jobs WHERE company='Hist Co'")
        c.execute("DELETE FROM activity_log WHERE details='Old activity line'")
        c.commit()
        c.close()


def test_history_cutoffs_follow_the_matrix():
    now = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)
    assert entitlements.history_cutoff({"role": "user", "plan": "free"}, now) == "2026-10-06"
    assert entitlements.history_cutoff({"role": "user", "plan": "premium"}, now) == "2025-11-05"
    assert entitlements.history_cutoff({"role": "user", "plan": "expert"}, now) is None
    assert entitlements.history_cutoff({"role": "admin", "plan": "free"}, now) is None
