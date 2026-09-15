"""
tests/test_pipeline_stages.py - the post-application workflow.

Two bugs were found in these endpoints while porting them to /app, both proven
by driving the route and reading the row back rather than by reading the code:

  1. /api/set-stage wrote `apply_status`, not `stage`. The legacy UI highlights
     the selected button from `job.stage` (app.py:4828-4831) and NOTHING ever
     wrote that column - so a successful update lit up no button and the choice
     vanished on reload, while the toast said "Stage updated". At the same time
     it DESTROYED apply_status, which is the record that the application was
     actually submitted or confirmed.

  2. /api/jobs/bulk passed on jobs without recording who decided, so every
     bulk-passed job read as "origin unknown" in the analytics that separates
     the user's own passes from the system's automatic filtering.
"""
import pytest

from tests.test_routes import (Client, stack, users,          # noqa: F401
                               _new_job, _job_row, _user_id)  # noqa: F401

STAGES = ("screening", "interviewing", "offer", "rejected")


def _applied_job(stack, apply_status="confirmed"):
    db = stack["db"]
    uid = _user_id(db, "alice@example.test")
    job_id = _new_job(db, uid)
    conn = db.get_db()
    conn.execute("UPDATE jobs SET status='applied', apply_status=?, applied_via='engine' "
                 "WHERE id=?", (apply_status, job_id))
    conn.commit(); conn.close()
    return job_id


@pytest.mark.parametrize("stage", STAGES)
def test_a_stage_is_stored_where_the_ui_reads_it(stack, users, stage):
    job_id = _applied_job(stack)
    status, _loc, _b = users["a"].post_json("/api/set-stage", {"id": job_id, "stage": stage})
    assert status == 200
    row = _job_row(stack["db"], job_id)
    assert row["stage"] == stage, (
        "the stage was reported saved but stage is %r - the UI reads THIS column, "
        "so nothing would light up and the choice is lost on reload" % row["stage"])


def test_setting_a_stage_does_not_erase_the_application_record(stack, users):
    """The half that loses data. apply_status is the evidence an application was
    actually sent; a pipeline stage is a note about what happened afterwards.
    One must not overwrite the other."""
    job_id = _applied_job(stack, apply_status="confirmed")
    users["a"].post_json("/api/set-stage", {"id": job_id, "stage": "interviewing"})
    row = _job_row(stack["db"], job_id)
    assert row["apply_status"] == "confirmed", (
        "apply_status became %r - the record that this application was confirmed "
        "has been destroyed by a UI label" % row["apply_status"])
    assert row["status"] == "applied"
    assert row["applied_via"] == "engine", "provenance was collateral damage"


def test_a_stage_can_be_changed_and_the_last_one_wins(stack, users):
    job_id = _applied_job(stack)
    for stage in ("screening", "interviewing", "offer"):
        users["a"].post_json("/api/set-stage", {"id": job_id, "stage": stage})
    assert _job_row(stack["db"], job_id)["stage"] == "offer"


@pytest.mark.parametrize("bad", ["", "hired", "Screening", "applied", None, 123])
def test_an_unknown_stage_is_refused(stack, users, bad):
    job_id = _applied_job(stack)
    status, _loc, _b = users["a"].post_json("/api/set-stage", {"id": job_id, "stage": bad})
    assert status == 400, "accepted %r as a stage" % (bad,)
    assert _job_row(stack["db"], job_id)["stage"] is None


def test_a_stage_cannot_be_set_on_someone_elses_job(stack, users):
    """The scoping attack. The UPDATE is keyed on user_id; this proves it."""
    db = stack["db"]
    victim = _new_job(db, _user_id(db, "alice@example.test"))
    status, _loc, _b = users["b"].post_json("/api/set-stage", {"id": victim, "stage": "offer"})
    # Either refused outright or silently a no-op - what matters is the row.
    assert _job_row(db, victim)["stage"] is None, "user B staged user A's job"


# ── Bulk actions ────────────────────────────────────────────────────────────

def test_bulk_approve_moves_every_id(stack, users):
    db = stack["db"]
    uid = _user_id(db, "alice@example.test")
    ids = [_new_job(db, uid) for _ in range(3)]
    status, _loc, _b = users["a"].post_json("/api/jobs/bulk", {"action": "approve", "ids": ids})
    assert status == 200
    assert all(_job_row(db, i)["status"] == "approved" for i in ids)


def test_a_bulk_pass_records_that_the_user_made_it(stack, users):
    """Without this every bulk-passed job reads as "origin unknown" on the
    analytics screen that exists to separate his decisions from the system's."""
    db = stack["db"]
    uid = _user_id(db, "alice@example.test")
    ids = [_new_job(db, uid) for _ in range(3)]
    users["a"].post_json("/api/jobs/bulk", {"action": "reject", "ids": ids})
    for i in ids:
        row = _job_row(db, i)
        assert row["status"] == "rejected"
        assert row["rejected_by"] == "user", (
            "rejected_by is %r - a bulk pass is still the user's decision" % row["rejected_by"])


def test_a_bulk_approve_records_no_pass(stack, users):
    db = stack["db"]
    uid = _user_id(db, "alice@example.test")
    ids = [_new_job(db, uid)]
    users["a"].post_json("/api/jobs/bulk", {"action": "approve", "ids": ids})
    assert _job_row(db, ids[0])["rejected_by"] is None


def test_bulk_cannot_touch_another_users_jobs(stack, users):
    db = stack["db"]
    mine = _new_job(db, _user_id(db, "bob@example.test"))
    theirs = _new_job(db, _user_id(db, "alice@example.test"))
    status, _loc, body = users["b"].post_json(
        "/api/jobs/bulk", {"action": "approve", "ids": [mine, theirs]})
    assert status == 200
    assert _job_row(db, mine)["status"] == "approved"
    assert _job_row(db, theirs)["status"] == "new", "bulk crossed a tenant boundary"


@pytest.mark.parametrize("payload", [
    {"action": "delete", "ids": [1]},
    {"action": "approve", "ids": []},
    {"action": "", "ids": [1]},
])
def test_bulk_refuses_nonsense(stack, users, payload):
    status, _loc, _b = users["a"].post_json("/api/jobs/bulk", payload)
    assert status == 400
