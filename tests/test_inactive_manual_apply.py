"""An application the user marks themselves ("I applied") is activity (fix 2026-11-25,
the owner: "Kfir is an active user - he applied for 6 jobs", while the 1 Dec preview
listed him as quiet for 14 days)."""
from datetime import datetime, timezone

import inactive
from tests.test_routes import Client, _new_job, _user_id, stack, users  # noqa: F401


def test_marking_a_job_applied_logs_activity_and_counts_for_the_sweep(stack, users):
    db = stack["db"]
    uid = _user_id(db, "bob@example.test")
    job_id = _new_job(db, uid)
    status, _, _ = users["b"].post_json(f"/api/jobs/{job_id}/applied", {})
    assert status == 200
    c = db.get_db()
    try:
        ev = c.execute("SELECT COUNT(*) FROM activity_log WHERE user_id=? AND event_type='job_marked_applied'", (uid,)).fetchone()[0]
        assert ev == 1
        assert inactive.last_active(c, uid, "2020-01-01 00:00:00").date() == datetime.now(timezone.utc).date()
    finally:
        c.close()


def test_applications_from_before_the_fix_still_count(stack, users):
    """Applications marked before the event was logged carry only jobs.applied_date."""
    db = stack["db"]
    uid = _user_id(db, "alice@example.test")
    job_id = _new_job(db, uid)
    c = db.get_db()
    try:
        c.execute("UPDATE jobs SET status='applied', applied_via='manual', applied_date=? WHERE id=?",
                  ("2026-11-24T10:00:00.123456", job_id))
        c.execute("DELETE FROM activity_log WHERE user_id=?", (uid,))
        c.commit()
        assert inactive.last_active(c, uid, "2026-01-01 00:00:00").strftime("%Y-%m-%d") == "2026-11-24"
        # A bulk "mark applied" is not a real application and does not count.
        c.execute("UPDATE jobs SET applied_via='bulk' WHERE id=?", (job_id,))
        c.commit()
        assert inactive.last_active(c, uid, "2026-01-01 00:00:00").strftime("%Y-%m-%d") == "2026-01-01"
    finally:
        c.close()
