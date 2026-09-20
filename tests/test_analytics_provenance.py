"""
tests/test_analytics_provenance.py - "Applied" and "Passed" must mean one thing.

Eran, 2026-09-15, looking at production: "I still think you have wrong
calculation in the analytics ... only applied should be count as applied, if I
click pass (for any reason - even applied elsewhere - should not be count)."

His literal hypothesis was wrong and is pinned here so it cannot come back: a
pass writes status='rejected' whatever reason is chosen. The number was still
untrustworthy, for a different reason - status='applied' was written by FOUR
unrelated things, and status='rejected' by two:

    applied  <- the engine really submitted a form
             <- the user pressed "Mark applied"
             <- a bulk cleanup marked the whole queue applied (never applied to)
             <- a job with no URL, recorded as "submitted"
    rejected <- the user passed on it
             <- the system retired it (dead link, expired, already attempted)

Nothing in the schema separated them, so every rate computed from these columns
was measuring something nobody had defined. Migration 9 adds applied_via and
rejected_by; these tests cover the backfill (the only chance the historical
rows get) and the write sites (so no backfill is ever needed again).
"""
import pytest

import db as database
import migrations
from tests.test_routes import (Client, _new_job, _job_row, _user_id,  # noqa: F401
                               stack, users)                          # noqa: F401


# ── The backfill: historical rows carry only note strings and apply_status ───

@pytest.fixture
def aged(tmp_path):
    """A database in the state prod is in: rows written before provenance
    existed, by every writer that ever wrote them."""
    import app  # noqa: F401  (import resets the db path - do it before pointing)
    import auth
    database.set_db_path(str(tmp_path / "t.db"))
    database.init_db()
    auth.set_db_getter(database.get_db)
    auth.create_user("Eran", "e@x.test", "correct-horse-1")
    conn = database.get_db()
    uid = conn.execute("SELECT id FROM users WHERE email=?", ("e@x.test",)).fetchone()["id"]

    seq = [0]

    def add(n, status, apply_status=None, notes=""):
        for _ in range(n):
            seq[0] += 1
            conn.execute(
                "INSERT INTO jobs (user_id, title, company, url, status, apply_status, notes) "
                "VALUES (?,?,?,?,?,?,?)",
                (uid, f"T{seq[0]}", f"C{seq[0]}", f"https://x.test/{seq[0]}",
                 status, apply_status, notes))
        conn.commit()

    # Exactly the strings app.py writes, at the sites that write them.
    add(5,  "applied", "confirmed", "Applied via Job Hunter - confirmed")     # engine
    add(3,  "applied", "submitted", "Marked applied manually")                # by hand
    add(40, "applied", "manual",    "Marked applied manually (one-time queue cleanup)")
    add(7,  "applied", "manual",    "Marked applied manually (bulk queue cleanup)")
    add(2,  "applied", "submitted", "Applied via Job Hunter (no URL)")        # nothing submitted
    add(1,  "applied", None,        "")                                       # no evidence at all

    add(30, "rejected", None, "Not a good fit")            # his pass
    add(12, "rejected", None, "Already applied elsewhere")  # his pass, the reason he named
    for r in conn.execute("SELECT company, title FROM jobs WHERE status='rejected'").fetchall():
        conn.execute("INSERT INTO rejected_patterns (user_id,company,title,notes,created_date) "
                     "VALUES (?,?,?,?,datetime('now'))", (uid, r["company"], r["title"], "pass"))
    conn.commit()
    add(140, "rejected", None, "Senior PM  [auto-removed: link dead/closed]")
    add(45,  "rejected", None, "Head of Product  [auto-removed: already attempted]")
    add(10,  "rejected", None, "Director  [admin: cleared attempted]")
    add(5,   "rejected", None, "Group PM  [expired]")
    add(3,   "rejected", None, "Staff PM")                 # no evidence at all

    migrations.m0009_decision_provenance(conn)
    stats = database.get_stats(conn, uid)
    conn.close()
    return stats


def test_the_backfill_separates_the_four_kinds_of_applied(aged):
    assert aged["applied_engine"] == 5
    assert aged["applied_manual"] == 3
    assert aged["applied_bulk"]   == 47      # both bulk writers
    assert aged["applied_no_url"] == 2
    assert aged["applied_unknown"] == 1
    assert aged["applied"] == 58             # the raw status count is unchanged


def test_the_bulk_note_does_not_steal_the_hand_marked_rows(aged):
    """"Marked applied manually (one-time queue cleanup)" CONTAINS "Marked
    applied manually". Claimed in the wrong order, all 50 collapse into one
    bucket and the distortion disappears into the number it was hiding in."""
    assert aged["applied_manual"] == 3, "the bulk note swallowed the hand-marked rows"
    assert aged["applied_bulk"] == 47


def test_the_backfill_separates_his_passes_from_the_systems(aged):
    assert aged["passed_by_user"] == 42
    assert aged["passed_by_system"] == 200
    assert aged["passed_unknown"] == 3


def test_a_row_with_no_evidence_is_left_unknown_not_guessed(aged):
    """An unknown origin is a fact about the data. Bucketing it would put the
    number straight back where it started - confidently wrong."""
    assert aged["applied_unknown"] == 1
    assert aged["passed_unknown"] == 3


def test_the_breakdown_adds_up_to_the_totals(aged):
    assert (aged["applied_engine"] + aged["applied_manual"] + aged["applied_bulk"]
            + aged["applied_no_url"] + aged["applied_unknown"]) == aged["applied"]
    assert (aged["passed_by_user"] + aged["passed_by_system"]
            + aged["passed_unknown"] + aged["rejected_archived"]) == aged["rejected"]


# ── The write sites: no future row should ever need a backfill ──────────────

def test_a_pass_is_a_pass_whatever_reason_is_chosen(stack, users):
    """Eran's own hypothesis, pinned. "Already applied elsewhere" is a REASON
    FOR PASSING, and must never reach the applied count by any route."""
    uid = _user_id(stack["db"], "alice@example.test")
    for reason in ("Already applied elsewhere", "Not a good fit", "Wrong location"):
        job_id = _new_job(stack["db"], uid)
        status, _loc, _body = users["a"].post_json(f"/api/jobs/{job_id}/reject", {"reason": reason})
        assert status == 200
        row = _job_row(stack["db"], job_id)
        assert row["status"] == "rejected", f"{reason!r} did not produce a pass"
        assert row["rejected_by"] == "user"
        assert row["applied_via"] is None, f"{reason!r} leaked into the applied count"


def test_marking_a_job_applied_by_hand_says_so(stack, users):
    uid = _user_id(stack["db"], "alice@example.test")
    job_id = _new_job(stack["db"], uid)
    status, _loc, _body = users["a"].post_json(f"/api/jobs/{job_id}/applied", {"notes": "Marked applied manually"})
    assert status == 200
    row = _job_row(stack["db"], job_id)
    assert row["status"] == "applied"
    assert row["applied_via"] == "manual", "a hand-mark must not be counted as an engine submission"


def test_approving_a_job_records_no_provenance(stack, users):
    """Approving is not a decision about applying or passing; if it started
    writing either column the counts would drift with no visible cause."""
    uid = _user_id(stack["db"], "alice@example.test")
    job_id = _new_job(stack["db"], uid)
    status, _loc, _body = users["a"].post_json(f"/api/jobs/{job_id}/approve", {})
    assert status == 200
    row = _job_row(stack["db"], job_id)
    assert row["applied_via"] is None and row["rejected_by"] is None


def test_the_stats_endpoint_serves_the_breakdown(stack, users):
    """The UI cannot show an honest number if the API does not send one."""
    import json as _json
    status, _loc, raw = users["a"].get("/api/stats")
    assert status == 200
    body = _json.loads(raw)
    for key in ("applied_engine", "applied_manual", "applied_bulk", "applied_no_url",
                "applied_unknown", "passed_by_user", "passed_by_system",
                "passed_unknown", "rejected_archived"):
        assert key in body, f"/api/stats no longer sends {key}"


# ── The dashboard must survive a database that has not migrated yet ─────────

def test_stats_degrades_instead_of_500ing_without_the_columns(tmp_path, monkeypatch):
    """get_stats runs on every dashboard load. Mid-deploy, after a rollback, or
    on a restored backup, migration 9 may not have reached this database - and
    the first screen after a deploy is the worst possible place to raise. The
    breakdown goes missing; the numbers that always worked keep working.

    Found by tests/test_db.py, which hand-writes its schema: the first version
    of this code raised "no such column: applied_via" there, which is exactly
    what production would have done on an un-migrated box.
    """
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE jobs (id INTEGER PRIMARY KEY, user_id INTEGER, status TEXT,
                           apply_status TEXT, notes TEXT, company TEXT, title TEXT);
        CREATE TABLE user_profiles (user_id INTEGER PRIMARY KEY, passed_archived_count INTEGER DEFAULT 0);
    """)
    conn.execute("INSERT INTO jobs (user_id, status) VALUES (1, 'applied')")
    conn.execute("INSERT INTO jobs (user_id, status) VALUES (1, 'rejected')")
    conn.execute("INSERT INTO user_profiles (user_id) VALUES (1)")
    conn.commit()

    monkeypatch.setattr(database, "expire_old_jobs", lambda *a, **k: None)
    stats = database.get_stats(conn, 1)
    conn.close()

    assert stats["applied"] == 1 and stats["rejected"] == 1, "the base counts must still work"
    assert "applied_engine" not in stats, "the breakdown must be absent, not zero"


def test_the_ui_treats_a_missing_breakdown_as_missing_not_zero():
    """Paired with the test above: if the API omits the keys, the screen must
    fall back to the old numbers rather than proudly reporting "0 applied"."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    assert 'typeof stats?.applied_engine === "number"' in src, (
        "App.tsx no longer checks whether the breakdown is present before using it")
    assert "hasBreakdown" in src


def test_the_backfill_upgrades_a_database_too_old_to_have_notes(tmp_path):
    """Migration 9 reads columns it did not create. A jobs table old enough to
    predate `notes` - or one created by hand - must still upgrade: a migration
    that raises leaves the schema half-applied and the app down. Skipping a
    backfill only costs those rows an "origin unknown" label, which is exactly
    what they are.

    Caught by tests/test_migrations.py's legacy-database fixture, which is the
    closest thing in this repo to the oldest shape of the real database.
    """
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "legacy.db"))
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
        title TEXT, company TEXT, url TEXT, status TEXT DEFAULT 'new')""")
    conn.execute("INSERT INTO jobs (user_id, status) VALUES (1, 'applied')")
    conn.commit()

    migrations.m0009_decision_provenance(conn)   # must not raise

    cols = [r[1] for r in conn.execute("PRAGMA table_info(jobs)")]
    assert "applied_via" in cols and "rejected_by" in cols, "the columns were not added"
    row = conn.execute("SELECT applied_via FROM jobs").fetchone()
    conn.close()
    assert row["applied_via"] is None, "a row with no evidence must stay unknown"


def test_the_backfill_is_idempotent(tmp_path):
    """Migrations get re-run: a redeploy, a restored backup, a manual repair.
    Running this one twice must not move a row into a different bucket."""
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "twice.db"))
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, title TEXT,
        company TEXT, url TEXT, status TEXT, apply_status TEXT, notes TEXT)""")
    conn.execute("INSERT INTO jobs (user_id, status, apply_status, notes) "
                 "VALUES (1,'applied','manual','Marked applied manually (one-time queue cleanup)')")
    conn.execute("INSERT INTO jobs (user_id, status, apply_status, notes) "
                 "VALUES (1,'applied','submitted','Marked applied manually')")
    conn.commit()

    migrations.m0009_decision_provenance(conn)
    first = [r["applied_via"] for r in conn.execute("SELECT applied_via FROM jobs ORDER BY id")]
    migrations.m0009_decision_provenance(conn)
    second = [r["applied_via"] for r in conn.execute("SELECT applied_via FROM jobs ORDER BY id")]
    conn.close()

    assert first == ["bulk", "manual"], first
    assert second == first, "re-running the backfill reclassified rows"


# ── Recovering the jobs a cleanup marked applied without applying ───────────

def _seed(stack, uid, via, n=1, status="applied"):
    ids = []
    conn = stack["db"].get_db()
    for _ in range(n):
        job_id = _new_job(stack["db"], uid)
        conn.execute("UPDATE jobs SET status=?, applied_via=?, apply_status='manual' WHERE id=?",
                     (status, via, job_id))
        ids.append(job_id)
    conn.commit(); conn.close()
    return ids


def test_bulk_marked_jobs_can_be_sent_back_to_review(stack, users):
    """84 real jobs on production carry applied_via='bulk': never reviewed,
    never applied to. They are recoverable, and recovering them is the only
    honest thing to do with a status that was never true."""
    uid = _user_id(stack["db"], "alice@example.test")
    bulk = _seed(stack, uid, "bulk", 3)
    status, _loc, body = users["a"].post_json("/api/jobs/restore-bulk-marked", {})
    assert status == 200
    import json as _json
    assert _json.loads(body)["restored"] >= 3
    for job_id in bulk:
        row = _job_row(stack["db"], job_id)
        assert row["status"] == "new", "a bulk-marked job did not go back to the queue"
        assert row["applied_via"] is None
        assert row["apply_status"] is None, "it would still render an apply badge"


def test_it_cannot_touch_a_real_application(stack, users):
    """The guard that matters. An engine submission and a hand-marked apply are
    real events; nothing here may undo them, whatever is passed in."""
    uid = _user_id(stack["db"], "alice@example.test")
    engine = _seed(stack, uid, "engine", 2)
    manual = _seed(stack, uid, "manual", 2)
    users["a"].post_json("/api/jobs/restore-bulk-marked", {})
    for job_id in engine + manual:
        row = _job_row(stack["db"], job_id)
        assert row["status"] == "applied", "a real application was reverted"
    assert _job_row(stack["db"], engine[0])["applied_via"] == "engine"
    assert _job_row(stack["db"], manual[0])["applied_via"] == "manual"


def test_it_cannot_reach_another_users_jobs(stack, users):
    """Two independent scopes guard this - the SELECT that picks the ids and the
    UPDATE that writes them. Removing either one alone leaves the behaviour
    correct, because the other still holds; removing BOTH fails this test, which
    is what proves it is not vacuous. That is defence in depth working as
    intended, not a gap in the test."""
    theirs = _seed(stack, _user_id(stack["db"], "alice@example.test"), "bulk", 2)
    users["b"].post_json("/api/jobs/restore-bulk-marked", {})
    for job_id in theirs:
        assert _job_row(stack["db"], job_id)["status"] == "applied", "crossed a tenant boundary"


def test_running_it_twice_is_harmless(stack, users):
    uid = _user_id(stack["db"], "alice@example.test")
    _seed(stack, uid, "bulk", 2)
    users["a"].post_json("/api/jobs/restore-bulk-marked", {})
    status, _loc, body = users["a"].post_json("/api/jobs/restore-bulk-marked", {})
    import json as _json
    assert status == 200 and _json.loads(body)["restored"] == 0


# ── Expired jobs were counted as found and shown nowhere ────────────────────

def test_expired_jobs_are_no_longer_invisible(tmp_path, monkeypatch):
    """The 'expired' status is no longer produced at all (the 3-day rule was
    removed on 2026-09-15) - but get_stats still reports the bucket, because a
    database restored from before the change can still hold those rows, and a
    row in `total` and in no bucket is exactly the invisibility this test
    exists to prevent. Migration 11 converts them; this covers the window in
    between, and a restore years from now.
    """
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE jobs (id INTEGER PRIMARY KEY, user_id INTEGER, status TEXT,
                           apply_status TEXT, notes TEXT, company TEXT, title TEXT,
                           applied_via TEXT, rejected_by TEXT, found_date TEXT);
        CREATE TABLE user_profiles (user_id INTEGER PRIMARY KEY, passed_archived_count INTEGER DEFAULT 0);
    """)
    for _ in range(4):                       # rows a pre-2026-09-15 database holds
        conn.execute("INSERT INTO jobs (user_id,status) VALUES (1,'expired')")
    conn.execute("INSERT INTO jobs (user_id,status) VALUES (1,'new')")
    conn.execute("INSERT INTO user_profiles (user_id) VALUES (1)")
    conn.commit()

    stats = database.get_stats(conn, 1)
    conn.close()

    assert stats["expired"] == 4, "legacy expired rows are unaccounted for again"
    buckets = (stats["new"] + stats["approved"] + stats["applied"]
               + stats["deferred"] + stats["rejected"] + stats["expired"])
    assert buckets == stats["total"], (
        "%d rows are in `total` and in no bucket - invisible on every screen"
        % (stats["total"] - buckets))
