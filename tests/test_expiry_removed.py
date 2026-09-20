"""
tests/test_expiry_removed.py - jobs no longer disappear for not being looked at.

Eran, seeing 33 jobs in "found" that were in no bucket: "what does founded but
expired means? from where? from the swipe? how can job be expired?"

He had never been told, because nothing ever said it. expire_old_jobs() moved
any job still in the swipe queue to status='expired' once it was THREE DAYS
old, and it ran on every /api/stats and /api/jobs load - so opening the app
discarded whatever he had not got to over a long weekend. get_stats then
counted 'expired' in `total` and in no bucket, so those jobs inflated "found"
while appearing on no screen at all.

Removed on his decision: a job leaves the review queue when he decides, or when
the link checker proves the posting is gone. Age is not evidence a job is
closed - it is evidence nobody looked, which is the opposite of a reason to
delete it.
"""
from datetime import datetime, timedelta

import pytest

import db as database
import migrations


def _db(tmp_path):
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                           title TEXT, company TEXT, url TEXT, status TEXT,
                           apply_status TEXT, notes TEXT, applied_via TEXT,
                           rejected_by TEXT, found_date TEXT);
        CREATE TABLE user_profiles (user_id INTEGER PRIMARY KEY, passed_archived_count INTEGER DEFAULT 0);
    """)
    conn.execute("INSERT INTO user_profiles (user_id) VALUES (1)")
    conn.commit()
    return conn


def test_an_old_unswiped_job_stays_in_the_queue(tmp_path):
    """The behaviour change, stated as the thing a user would notice: go away
    for a week, come back, your jobs are still there."""
    conn = _db(tmp_path)
    long_ago = (datetime.now() - timedelta(days=90)).isoformat()
    for i in range(5):
        conn.execute("INSERT INTO jobs (user_id,status,found_date) VALUES (1,'new',?)", (long_ago,))
    conn.commit()

    database.expire_old_jobs(conn, 1)
    stats = database.get_stats(conn, 1)          # also calls it internally
    conn.close()

    assert stats["new"] == 5, "a 90-day-old job was taken out of the review queue"
    assert stats["expired"] == 0


def test_every_job_is_in_exactly_one_bucket(tmp_path):
    """The invisibility, pinned. `total` must never exceed the buckets: a job in
    `total` and in no bucket is a job the user is told about and cannot find."""
    conn = _db(tmp_path)
    old = (datetime.now() - timedelta(days=30)).isoformat()
    for status in ("new", "approved", "applied", "deferred", "rejected"):
        conn.execute("INSERT INTO jobs (user_id,status,found_date) VALUES (1,?,?)", (status, old))
    conn.commit()
    stats = database.get_stats(conn, 1)
    conn.close()
    buckets = (stats["new"] + stats["approved"] + stats["applied"]
               + stats["deferred"] + stats["rejected"] + stats["expired"])
    assert buckets == stats["total"], (
        "%d job(s) are counted in `total` and in no bucket" % (stats["total"] - buckets))


# ── Migration 11: the rows the rule already took ───────────────────────────

def test_expired_rows_go_back_to_the_queue(tmp_path):
    conn = _db(tmp_path)
    for i in range(3):
        conn.execute("INSERT INTO jobs (user_id,status,title) VALUES (1,'expired',?)", (f"T{i}",))
    conn.commit()
    migrations.m0011_undo_expiry(conn)
    rows = [dict(r) for r in conn.execute("SELECT status FROM jobs")]
    conn.close()
    assert all(r["status"] == "new" for r in rows), rows


def test_rows_already_converted_to_passes_go_back_too(tmp_path):
    """A restart turned expired rows into system-passes. Those were not his
    decision either, so they come back as well."""
    conn = _db(tmp_path)
    conn.execute("INSERT INTO jobs (user_id,status,rejected_by,notes) "
                 "VALUES (1,'rejected','system','Senior PM  [expired]')")
    conn.commit()
    migrations.m0011_undo_expiry(conn)
    row = dict(conn.execute("SELECT status, rejected_by, notes FROM jobs").fetchone())
    conn.close()
    assert row["status"] == "new"
    assert row["rejected_by"] is None
    assert "[expired]" not in row["notes"], "the marker would make it look expired again"


def test_a_real_pass_is_never_dragged_back(tmp_path):
    """The guard that matters. A job the user passed on stays passed, including
    one whose notes happen to mention the word."""
    conn = _db(tmp_path)
    conn.execute("INSERT INTO jobs (user_id,status,rejected_by,notes) "
                 "VALUES (1,'rejected','user','Not a good fit')")
    conn.execute("INSERT INTO jobs (user_id,status,rejected_by,notes) "
                 "VALUES (1,'rejected','user','the posting looked [expired] to me')")
    conn.execute("INSERT INTO jobs (user_id,status,rejected_by,notes) "
                 "VALUES (1,'rejected','system','dead  [auto-removed: link dead/closed]')")
    conn.commit()
    migrations.m0011_undo_expiry(conn)
    rows = [dict(r) for r in conn.execute("SELECT status, rejected_by FROM jobs ORDER BY id")]
    conn.close()
    assert [r["status"] for r in rows] == ["rejected", "rejected", "rejected"], rows
    assert rows[1]["rejected_by"] == "user", "a user pass was reclassified"


def test_the_migration_is_idempotent(tmp_path):
    conn = _db(tmp_path)
    conn.execute("INSERT INTO jobs (user_id,status,rejected_by,notes) "
                 "VALUES (1,'rejected','system','x [expired]')")
    conn.execute("INSERT INTO jobs (user_id,status) VALUES (1,'expired')")
    conn.commit()
    migrations.m0011_undo_expiry(conn)
    first = [r["status"] for r in conn.execute("SELECT status FROM jobs ORDER BY id")]
    migrations.m0011_undo_expiry(conn)
    second = [r["status"] for r in conn.execute("SELECT status FROM jobs ORDER BY id")]
    conn.close()
    assert first == ["new", "new"] and second == first


def test_nothing_still_writes_the_expired_status():
    """The rule is gone from the code, not just disabled at one call site."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    for name in ("app.py",):
        src = (root / name).read_text(encoding="utf-8")
        assert "status='expired'" not in src, "%s still writes status='expired'" % name
