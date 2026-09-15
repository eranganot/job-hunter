"""
tests/test_worker.py - the loop that drains the queue.

What this replaces: `threading.Thread(target=run_job_search).start()` inside a
request handler. The properties worth testing are the ones that change makes
possible - a failing job doesn't kill the worker, a long job isn't stolen from
it, and an unfinished run survives the process.
"""
import threading
import time

import pytest

import db as database
import jobqueue
import worker


@pytest.fixture
def wq(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "w.db"), raising=False)
    monkeypatch.setattr(database, "DATABASE_URL", None, raising=False)
    monkeypatch.setattr(database, "BACKEND_REFUSAL", None, raising=False)
    monkeypatch.delenv("DB_BACKEND", raising=False)
    database.init_db()

    conn = database.get_db()
    conn.execute("INSERT INTO users (id,name,email,password_hash,salt) VALUES (1,'A','a@e.test','h','s')")
    conn.execute("INSERT INTO users (id,name,email,password_hash,salt) VALUES (2,'B','b@e.test','h','s')")
    conn.commit()
    conn.close()

    monkeypatch.setattr(worker, "HANDLERS", {}, raising=False)
    monkeypatch.setattr(worker, "_last_sweep", 0.0, raising=False)
    yield worker
    worker.stop_background(timeout=2)


def _status(run_id):
    conn = database.get_db()
    row = conn.execute("SELECT status, detail, error FROM job_runs WHERE id=?", (run_id,)).fetchone()
    conn.close()
    return row


# ── Running work ─────────────────────────────────────────────────────────────

def test_a_queued_job_is_run_and_marked_done(wq):
    seen = []
    wq.register("search", lambda uid, payload: seen.append((uid, payload)) or "found 4")
    rid = jobqueue.enqueue(1, "search", {"titles": ["VP"]})

    assert wq.run_once() is True
    assert seen == [(1, {"titles": ["VP"]})]
    row = _status(rid)
    assert row["status"] == "done" and row["detail"] == "found 4"


def test_an_empty_queue_is_not_work(wq):
    wq.register("search", lambda uid, payload: None)
    assert wq.run_once() is False


def test_only_registered_kinds_are_claimed(wq):
    """An unregistered kind must not be swallowed by a worker that can't run it."""
    wq.register("search", lambda uid, payload: None)
    jobqueue.enqueue(1, "apply")
    assert wq.run_once() is False, "the worker claimed a job it has no handler for"


# ── Failure ──────────────────────────────────────────────────────────────────

def test_a_handler_that_raises_does_not_kill_the_worker(wq):
    def boom(uid, payload):
        raise RuntimeError("gemini said no")

    wq.register("search", boom)
    rid = jobqueue.enqueue(1, "search")

    assert wq.run_once() is True          # it returns, it does not propagate
    row = _status(rid)
    assert row["status"] == "queued", "a transient failure was not retried"
    assert "gemini said no" in row["error"]


def test_a_job_with_no_handler_is_failed_without_retrying(wq, monkeypatch):
    """Retrying something nothing can run would loop until MAX_ATTEMPTS for nothing."""
    wq.register("search", lambda uid, payload: None)
    rid = jobqueue.enqueue(1, "apply")
    monkeypatch.setattr(worker, "HANDLERS", {"apply": None}, raising=False)

    assert wq.run_once() is True
    row = _status(rid)
    assert row["status"] == "failed"
    assert "no handler" in row["error"]


# ── The heartbeat ────────────────────────────────────────────────────────────

def test_a_long_job_is_not_stolen_while_it_runs(wq, monkeypatch):
    """
    The sweeper exists to recover dead workers. It must not reclaim a run from
    a worker that is simply busy - which is what the heartbeat prevents.
    """
    monkeypatch.setattr(worker, "HEARTBEAT_SECONDS", 0.05)
    started, release = threading.Event(), threading.Event()

    def slow(uid, payload):
        started.set()
        release.wait(3)
        return "done at last"

    wq.register("search", slow)
    rid = jobqueue.enqueue(1, "search")

    runner = threading.Thread(target=wq.run_once, daemon=True)
    runner.start()
    assert started.wait(3), "the handler never started"
    time.sleep(0.3)                                   # several heartbeats

    swept = jobqueue.requeue_stuck(older_than=1)      # anything idle >1s is stale
    release.set()
    runner.join(5)

    assert swept["requeued"] == 0, "a live run was taken from a busy worker"
    assert _status(rid)["status"] == "done"


# ── The loop ─────────────────────────────────────────────────────────────────

def test_the_loop_drains_and_stops_when_asked(wq):
    done = []
    wq.register("search", lambda uid, payload: done.append(uid))
    jobqueue.enqueue(1, "search")
    jobqueue.enqueue(2, "search")

    stop = threading.Event()
    t = threading.Thread(target=wq.loop, kwargs={"stop": stop, "poll": 0.01}, daemon=True)
    t.start()

    deadline = time.time() + 5
    while len(done) < 2 and time.time() < deadline:
        time.sleep(0.02)
    stop.set()
    t.join(3)

    assert sorted(done) == [1, 2]
    assert not t.is_alive(), "the loop ignored the stop signal"


def test_starting_the_worker_twice_does_not_start_two(wq):
    wq.register("search", lambda uid, payload: None)
    a = wq.start_background()
    b = wq.start_background()
    assert a is b
    wq.stop_background(timeout=2)


# ── health(): telling a wedged worker from an idle one ───────────────────────

def test_health_reports_a_job_nobody_has_touched_as_stuck(wq, monkeypatch):
    """Queue depth cannot distinguish these: depth 0 and depth 50 are both just
    a number, and a worker that died mid-job leaves its row claimed forever.
    The distinguishing fact is a claim with no recent heartbeat.

    Asserted by making a claim OLD, not by asserting stuck is False on a clean
    queue - that version passed with the threshold comparison deleted.
    """
    jobqueue.enqueue(1, "search")
    run = jobqueue.claim()
    assert run

    conn = database.get_db()
    conn.execute("UPDATE job_runs SET locked_at=? WHERE id=?",
                 ("2020-01-01 00:00:00", run["id"]))
    conn.commit()
    conn.close()

    h = worker.health()
    assert h["claimed"] == 1
    assert h["stuck"] is True, h
    assert h["oldest_claim_age_s"] > jobqueue.STUCK_AFTER_SECONDS


def test_health_does_not_cry_stuck_over_a_fresh_claim(wq):
    jobqueue.enqueue(1, "search")
    assert jobqueue.claim()
    h = worker.health()
    assert h["claimed"] == 1 and h["stuck"] is False, h
