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


# ── "Stuck" must mean stuck, not "the sweeper has not got to it yet" ─────────
#
# Found on production 2026-09-15. The smoke ran minutes after a redeploy and
# failed on a claim 1845s old - 45 seconds past a 1800s threshold whose sweeper
# only runs every 120s. Read again fifteen minutes later: claimed=0, and a
# search had completed in between. It fixed itself, exactly as designed, and the
# smoke had called it a fault.

def test_a_claim_just_past_the_threshold_is_recovering_not_stuck(wq):
    jobqueue.enqueue(1, "search")
    run = jobqueue.claim()
    _age_claim(run["id"], jobqueue.STUCK_AFTER_SECONDS + 30)

    h = worker.health()
    assert h["recovering"] is True, h
    assert h["stuck"] is False, "reported a fault the sweeper was about to fix"


def test_a_claim_past_the_sweep_window_too_is_stuck(wq):
    """Past the threshold AND past the window the sweeper is allowed to take
    means the sweeper is not reaching it - which is a real fault."""
    jobqueue.enqueue(1, "search")
    run = jobqueue.claim()
    _age_claim(run["id"], jobqueue.STUCK_AFTER_SECONDS + int(worker.SWEEP_EVERY_SECONDS) + 300)

    h = worker.health()
    assert h["stuck"] is True, h
    assert h["recovering"] is False


def _age_claim(run_id, seconds):
    import time as _t
    stale = _t.strftime("%Y-%m-%d %H:%M:%S", _t.gmtime(_t.time() - seconds))
    conn = database.get_db()
    conn.execute("UPDATE job_runs SET locked_at=? WHERE id=?", (stale, run_id))
    conn.commit()
    conn.close()


# ── A redeploy must not strand a run for half an hour ───────────────────────

def test_a_run_orphaned_by_a_restart_goes_back_at_boot(wq, monkeypatch):
    """The claim is scoped to hostname:pid, so a new process cannot heartbeat
    what the old one held. Waiting out the full threshold cost a real user 1845
    seconds of a search that was never coming back on its own."""
    jobqueue.enqueue(1, "search")
    run = jobqueue.claim()
    assert jobqueue.depth()[jobqueue.RUNNING] == 1

    monkeypatch.delenv("JH_WORKER_BOOT_SWEEP", raising=False)
    out = worker.adopt_orphans()

    assert out["requeued"] == 1, out
    assert jobqueue.depth()[jobqueue.RUNNING] == 0
    assert jobqueue.depth()[jobqueue.QUEUED] == 1
    assert jobqueue.claim(), "the orphaned run is claimable again"


def test_the_boot_sweep_can_be_switched_off_for_a_second_instance(wq, monkeypatch):
    """The sweep's reasoning - 'at startup nothing else is running' - is only
    true with one worker. A second instance starting would otherwise requeue the
    first one's live work out from under it."""
    jobqueue.enqueue(1, "search")
    jobqueue.claim()
    monkeypatch.setenv("JH_WORKER_BOOT_SWEEP", "0")

    out = worker.adopt_orphans()
    assert out.get("skipped") is True
    assert jobqueue.depth()[jobqueue.RUNNING] == 1, "it requeued another worker's job"


def test_a_failed_boot_sweep_does_not_stop_the_worker_starting(wq, monkeypatch):
    monkeypatch.delenv("JH_WORKER_BOOT_SWEEP", raising=False)
    monkeypatch.setattr(jobqueue, "requeue_stuck",
                        lambda **k: (_ for _ in ()).throw(RuntimeError("db gone")))
    out = worker.adopt_orphans()
    assert "db gone" in out.get("error", "")


def test_start_background_actually_runs_the_boot_sweep(wq, monkeypatch):
    """adopt_orphans() working proves nothing if start_background never calls
    it. Removing the call passed every other test in this file."""
    called = []
    monkeypatch.setattr(worker, "adopt_orphans",
                        lambda: called.append(True) or {"requeued": 0, "abandoned": 0})
    monkeypatch.setattr(worker, "loop", lambda *a, **k: None)
    worker._thread = None
    try:
        worker.start_background()
    finally:
        worker.stop_background(timeout=1)
        worker._thread = None
    assert called, "start_background did not sweep orphaned claims at boot"
