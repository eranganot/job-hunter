"""
worker.py - drains `job_runs`.

Phase 3. Runs **in-process** by decision: a daemon thread inside the existing
web service, not a second Railway service. The boundary is written so splitting
it out later is a `CMD` change rather than a refactor - this module imports only
`jobqueue`, and the handlers are registered into it from outside. That is also
what keeps it free of a circular import with `app.py`, where the handlers live.

What changes for a user: pressing "Run search" no longer starts a thread inside
the request. It writes a row. The work happens on the worker, one run per user
at a time, and it survives a redeploy because an unfinished row is still there
to be claimed afterwards.

The heartbeat runs on its own timer thread while a handler executes, because a
handler is a minutes-long blocking call and cannot pause to say it is alive.
Without it the stuck-run sweeper would eventually steal work from a worker that
was doing exactly what it was told.
"""
import threading
import time
import traceback
from datetime import datetime, timezone

import jobqueue

# Route this module's print() calls through the logging module: timestamps,
# levels, the module name, and the request id of the request in flight.
import log as _log
print = _log.make_print(__name__)  # noqa: A001 - see log.py


# kind -> callable(user_id, payload) . Registered by app.py at startup.
HANDLERS = {}

POLL_SECONDS = 3.0
HEARTBEAT_SECONDS = 30.0
SWEEP_EVERY_SECONDS = 120.0

_thread = None
_stop = threading.Event()
_last_sweep = 0.0


def register(kind, handler):
    """Teach the worker how to run one kind of job."""
    HANDLERS[kind] = handler


def _beat_while(run_id, holder, done):
    """Say the run is alive until `done` is set."""
    while not done.wait(HEARTBEAT_SECONDS):
        try:
            if not jobqueue.heartbeat(run_id, by=holder):
                # The row was taken away - the sweeper decided we were dead.
                # Stop claiming to be alive; the handler will finish into a
                # rejected complete(), which is the correct outcome.
                return
        except Exception as exc:
            print("[worker] heartbeat failed for run %s: %s" % (run_id, exc))


def run_once():
    """
    Claim and run at most one job. Returns True if work was done.

    A handler raising is a failed run, never a dead worker: the traceback is
    printed, the failure recorded, and the loop continues.
    """
    run = jobqueue.claim(kinds=list(HANDLERS) or None)
    if not run:
        return False

    handler = HANDLERS.get(run["kind"])
    if handler is None:
        jobqueue.fail(run["id"], "no handler registered for kind %r" % run["kind"],
                      by=run["locked_by"], retry=False)
        return True

    done = threading.Event()
    beat = threading.Thread(target=_beat_while, args=(run["id"], run["locked_by"], done),
                            daemon=True)
    beat.start()
    started = time.time()
    try:
        detail = handler(run["user_id"], run["payload"])
        jobqueue.complete(run["id"], str(detail or "")[:500], by=run["locked_by"])
        print("[worker] %s for user %s finished in %.1fs"
              % (run["kind"], run["user_id"], time.time() - started))
    except Exception as exc:
        print("[worker] %s for user %s failed: %s\n%s"
              % (run["kind"], run["user_id"], exc, traceback.format_exc()))
        jobqueue.fail(run["id"], exc, by=run["locked_by"])
    finally:
        done.set()
    return True


def _maybe_sweep():
    """Return work abandoned by a dead worker. Cheap, so run it on a timer."""
    global _last_sweep
    now = time.time()
    if now - _last_sweep < SWEEP_EVERY_SECONDS:
        return
    _last_sweep = now
    try:
        out = jobqueue.requeue_stuck()
        if out["requeued"] or out["abandoned"]:
            print("[worker] swept stuck runs: %s" % out)
    except Exception as exc:
        print("[worker] sweep failed: %s" % exc)


def loop(stop=None, poll=None):
    """Drain until told to stop. Idle polling is a single indexed SELECT."""
    stop = stop or _stop
    poll = POLL_SECONDS if poll is None else poll
    print("[worker] started as %s, handlers: %s"
          % (jobqueue.worker_id(), ", ".join(sorted(HANDLERS)) or "none"))
    while not stop.is_set():
        try:
            _maybe_sweep()
            if not run_once():
                stop.wait(poll)
        except Exception as exc:
            # The loop itself must not be killable by one bad iteration.
            print("[worker] loop error: %s\n%s" % (exc, traceback.format_exc()))
            stop.wait(poll)
    print("[worker] stopped")


def start_background():
    """Start the worker thread. Idempotent."""
    global _thread
    if _thread and _thread.is_alive():
        return _thread
    _stop.clear()
    _thread = threading.Thread(target=loop, name="jobworker", daemon=True)
    _thread.start()
    return _thread


def health():
    """What /api/health needs to tell a wedged worker from an idle one.

    Queue depth alone cannot: depth 0 and depth 50 are both just a number, and
    a worker that died mid-job leaves its row claimed and RUNNING forever. The
    distinguishing fact is how long the oldest claimed job has gone without a
    heartbeat - past jobqueue.STUCK_AFTER_SECONDS it is presumed dead, which is
    the same threshold the requeue sweeper uses, so this reports the condition
    the app is already acting on rather than a second opinion about it.
    """
    alive = bool(_thread and _thread.is_alive())
    out = {"running": alive, "handlers": sorted(HANDLERS)}
    try:
        d = jobqueue.depth()
    except Exception as exc:
        out["queue_unavailable"] = str(exc)[:120]
        return out
    out["claimed"] = d.get(jobqueue.RUNNING, 0)
    since = d.get("oldest_running_since")
    out["oldest_claim_age_s"] = None
    out["stuck"] = False
    if since:
        try:
            started = datetime.strptime(str(since)[:19], "%Y-%m-%d %H:%M:%S")
            age = int((datetime.now(timezone.utc).replace(tzinfo=None) - started)
                      .total_seconds())
            out["oldest_claim_age_s"] = age
            out["stuck"] = age > jobqueue.STUCK_AFTER_SECONDS
        except (ValueError, TypeError) as exc:
            out["oldest_claim_age_s"] = "unparseable: %s" % str(since)[:40]
    return out


def stop_background(timeout=5.0):
    """For tests and a clean shutdown."""
    _stop.set()
    if _thread and _thread.is_alive():
        _thread.join(timeout)
