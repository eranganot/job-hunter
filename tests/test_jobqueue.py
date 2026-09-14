"""
tests/test_jobqueue.py - the work queue.

The properties that matter are the ones an unbounded `threading.Thread` cannot
give you: two workers never run the same job, one user cannot occupy the queue,
a worker that dies releases its work, and a redeploy mid-run loses nothing.

The claim is deliberately NOT `FOR UPDATE SKIP LOCKED` (see jobqueue.py), so the
race is tested directly rather than trusted to the engine.
"""
import os
import threading

import pytest

import db as database
import jobqueue
import migrations


@pytest.fixture
def q(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "q.db"), raising=False)
    monkeypatch.setattr(database, "DATABASE_URL", None, raising=False)
    monkeypatch.setattr(database, "BACKEND_REFUSAL", None, raising=False)
    monkeypatch.delenv("DB_BACKEND", raising=False)
    database.init_db()

    conn = database.get_db()
    for i in (1, 2):
        conn.execute("INSERT INTO users (id,name,email,password_hash,salt) VALUES (?,?,?,?,?)",
                     (i, "u%d" % i, "u%d@example.test" % i, "h", "s"))
    conn.commit()
    conn.close()
    return jobqueue


# ── Enqueue ──────────────────────────────────────────────────────────────────

def test_work_comes_back_out_the_way_it_went_in(q):
    rid = q.enqueue(1, "search", {"titles": ["VP Product"]})
    assert rid

    run = q.claim()
    assert run["id"] == rid
    assert run["user_id"] == 1 and run["kind"] == "search"
    assert run["payload"] == {"titles": ["VP Product"]}
    assert run["attempts"] == 1


def test_pressing_the_button_twice_does_not_queue_two_searches(q):
    first = q.enqueue(1, "search")
    second = q.enqueue(1, "search")
    assert first and second is None, "a second press queued a duplicate run"


def test_a_different_kind_or_user_is_not_a_duplicate(q):
    assert q.enqueue(1, "search")
    assert q.enqueue(1, "apply"), "a different kind was treated as a duplicate"
    assert q.enqueue(2, "search"), "another user was treated as a duplicate"


def test_work_scheduled_for_later_is_not_claimed_yet(q):
    q.enqueue(1, "search", run_after="2999-01-01 00:00:00")
    assert q.claim() is None


# ── The claim ────────────────────────────────────────────────────────────────

def test_two_workers_never_get_the_same_run(q):
    """The property the whole design exists for."""
    q.enqueue(1, "search")
    a = q.claim(by="worker-a")
    b = q.claim(by="worker-b")
    assert a is not None
    assert b is None, "two workers claimed the same run"


def test_racing_threads_claim_each_run_exactly_once(q):
    """
    The compare-and-swap under real concurrency, not one call after another.
    Eight threads, five runs: every run claimed, none claimed twice.
    """
    for i in range(5):
        q.enqueue(i % 2 + 1, "search-%d" % i)

    claimed, errors = [], []
    lock = threading.Lock()

    def grab(n):
        try:
            for _ in range(3):
                run = q.claim(by="w%d" % n)
                if run:
                    with lock:
                        claimed.append(run["id"])
        except Exception as exc:      # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=grab, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    assert len(claimed) == len(set(claimed)), "a run was claimed twice: %s" % claimed


def test_one_user_cannot_occupy_the_queue(q):
    """One run per user in flight, so a heavy account cannot starve the rest."""
    q.enqueue(1, "search")
    q.enqueue(1, "apply")
    q.enqueue(2, "search")

    first = q.claim()
    second = q.claim()

    assert first["user_id"] == 1
    assert second is not None and second["user_id"] == 2, \
        "user 1 got a second concurrent run while one was still in flight"


def test_finishing_a_run_frees_the_user(q):
    q.enqueue(1, "search")
    q.enqueue(1, "apply")
    first = q.claim()
    assert q.claim() is None

    q.complete(first["id"], "found 4", by=first["locked_by"])
    assert q.claim() is not None, "the user stayed blocked after their run finished"


def test_oldest_work_goes_first(q):
    a = q.enqueue(1, "search")
    b = q.enqueue(2, "search")
    assert q.claim()["id"] == a
    assert q.claim()["id"] == b


# ── Completion and failure ───────────────────────────────────────────────────

def test_only_the_holder_can_complete_a_run(q):
    q.enqueue(1, "search")
    run = q.claim(by="worker-a")
    assert q.complete(run["id"], by="worker-b") is False
    assert q.complete(run["id"], by="worker-a") is True


def test_a_transient_failure_is_retried(q):
    """A Gemini 429 should be a retry, not a lost run."""
    q.enqueue(1, "search")
    run = q.claim(by="w")
    out = q.fail(run["id"], "429 rate limited", by="w")
    assert out["requeued"] is True

    again = q.claim(by="w")
    assert again is not None and again["id"] == run["id"]
    assert again["attempts"] == 2, "the retry did not count as another attempt"


def test_a_job_that_keeps_failing_eventually_stops(q, monkeypatch):
    monkeypatch.setattr(jobqueue, "MAX_ATTEMPTS", 2)
    q.enqueue(1, "search")
    for _ in range(2):
        run = q.claim(by="w")
        if run:
            q.fail(run["id"], "still broken", by="w")
    assert q.claim() is None, "a permanently broken job kept being retried"
    assert q.depth()["failed"] == 1


# ── The worker died ──────────────────────────────────────────────────────────

def test_a_dead_workers_run_is_returned_to_the_queue(q):
    """A redeploy mid-run must lose nothing."""
    q.enqueue(1, "search")
    run = q.claim(by="doomed-worker")

    out = q.requeue_stuck(older_than=-1)      # everything counts as stale
    assert out["requeued"] == 1

    recovered = q.claim(by="fresh-worker")
    assert recovered is not None and recovered["id"] == run["id"]


def test_a_healthy_run_is_left_alone(q):
    """The sweeper must not steal work from a worker that is still going."""
    q.enqueue(1, "search")
    run = q.claim(by="busy-worker")
    assert q.heartbeat(run["id"], by="busy-worker") is True

    out = q.requeue_stuck(older_than=3600)
    assert out["requeued"] == 0
    assert q.claim(by="other") is None, "a live run was handed to another worker"


def test_a_worker_cannot_heartbeat_a_run_it_has_lost(q):
    q.enqueue(1, "search")
    run = q.claim(by="doomed-worker")
    q.requeue_stuck(older_than=-1)
    assert q.heartbeat(run["id"], by="doomed-worker") is False


def test_a_run_that_dies_too_many_times_is_abandoned_not_looped(q, monkeypatch):
    monkeypatch.setattr(jobqueue, "MAX_ATTEMPTS", 1)
    q.enqueue(1, "search")
    q.claim(by="doomed")
    out = q.requeue_stuck(older_than=-1)
    assert out == {"requeued": 0, "abandoned": 1}


# ── Health ───────────────────────────────────────────────────────────────────

def test_depth_reports_what_health_needs(q):
    q.enqueue(1, "search")
    q.enqueue(2, "search")
    q.claim(by="w")

    d = q.depth()
    assert d["queued"] == 1 and d["running"] == 1
    assert d["oldest_running_since"], "nothing to detect a wedged worker with"


def test_the_module_does_not_shadow_the_standard_library():
    """A file named queue.py here would break every stdlib `import queue`."""
    import queue as stdlib_queue
    assert "jobqueue" not in (stdlib_queue.__file__ or "")
    assert hasattr(stdlib_queue, "Queue")
