"""
jobqueue.py - one row per unit of background work.

Phase 3. Today a search is an unbounded `threading.Thread` started inside the
web process and forgotten: nothing bounds how many run, nothing survives a
redeploy mid-run, nothing stops two instances firing the same user's daily
search twice, and a request can sit behind minutes of someone else's work.

Named `jobqueue`, not `queue`: a module called `queue.py` in the repo root
would shadow the standard library's for every import in the process.

**The claim is a compare-and-swap, not `FOR UPDATE SKIP LOCKED`.** The plan
specified SKIP LOCKED, and it does not work against this codebase:

  * `dbdriver` opens Postgres connections with `autocommit=True` (mirroring
    db.get_db()'s SQLite setup), so a `SELECT ... FOR UPDATE` in one statement
    has released its lock before the `UPDATE` in the next one runs. The lock
    would be decorative.
  * SQLite has no row locks and no SKIP LOCKED at all - and production is
    still on SQLite, because the Postgres cutover is blocked on an open
    question. A queue that only works on Postgres could not ship.

So: read a candidate, then `UPDATE ... WHERE id=? AND status='queued'` and
believe the rowcount. The WHERE clause re-checks the status inside the write,
which is atomic on both engines - SQLite serialises writers, Postgres locks the
row for the duration of the UPDATE. Two workers racing for the same row means
one gets rowcount 1 and the other gets 0 and moves on. Correctness does not
depend on the engine; only the amount of retrying does, and with one in-process
worker there is none.
"""
import json
import os
import socket
import time
from datetime import datetime, timezone

import db as database

# Route this module's print() calls through the logging module: timestamps,
# levels, the module name, and the request id of the request in flight.
import log as _log
print = _log.make_print(__name__)  # noqa: A001 - see log.py


QUEUED, RUNNING, DONE, FAILED = "queued", "running", "done", "failed"

# A run whose worker has not touched it in this long is presumed dead. Longer
# than the slowest legitimate run: a search is minutes, not an hour.
STUCK_AFTER_SECONDS = int(os.environ.get("JH_QUEUE_STUCK_AFTER", 1800))
MAX_ATTEMPTS = int(os.environ.get("JH_QUEUE_MAX_ATTEMPTS", 3))

# How many runs of one kind a single user may start per UTC day.
#
# This is the RUN cap; gemini.py holds the CALL cap. They guard different
# things and both are needed: a per-day call ceiling still lets one user start
# fifty searches and spend everyone else's budget before lunch, and a run cap
# alone says nothing about a single run that scores ten thousand jobs.
#
# Counted on the queue rather than on the HTTP route because the scheduler
# enqueues too, and scheduler-started searches cost exactly the same money as
# button-started ones. ratelimit.py's run_search policy (6/hour) is the burst
# guard on the button; this is the daily one on the work itself.
DEFAULT_DAILY_RUNS = {"search": 20, "apply": 20}


def daily_limit(kind):
    """0 disables the cap for that kind, so a bad number is never an outage."""
    env = os.environ.get("JH_RUNS_%s_PER_DAY" % str(kind).upper())
    if env is not None:
        try:
            return int(env)
        except ValueError:
            pass
    return DEFAULT_DAILY_RUNS.get(kind, 0)


class DailyCapReached(RuntimeError):
    """Raised by enqueue() when a user has started their day's allowance.

    An exception rather than a None return, because enqueue() already returns
    None for "deduped" - and a capped user told "already running" would go and
    wait for a run that is never coming.
    """

    def __init__(self, kind, used, limit):
        self.kind, self.used, self.limit = kind, used, limit
        super().__init__("Daily %s limit reached: %s of %s started today"
                         % (kind, used, limit))


def runs_today(user_id, kind, conn=None):
    """How many runs of this kind the user has STARTED today (UTC).

    Creations, not completions: a run that failed still spent what it spent,
    and counting completions would let a user retry a crashing run forever.
    """
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    own = conn is None
    conn = conn or database.get_db()
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM job_runs WHERE user_id=? AND kind=? AND created_date >= ?",
            (user_id, kind, day)).fetchone()
        return int(row[0] or 0)
    finally:
        if own:
            conn.close()

# How many candidate rows to consider before giving up on a claim pass. Bounds
# the work when many rows are locked or ineligible.
_CLAIM_SCAN = 20


def worker_id() -> str:
    """Identifies the claimant. Host plus pid is enough to tell two apart."""
    return "%s:%d" % (socket.gethostname(), os.getpid())


def _now_sql():
    """The schema's date literal, translated per engine by dbdriver."""
    return "datetime('now')"


def enqueue(user_id, kind, payload=None, run_after=None, dedupe=True, cap=True):
    """
    Add work. Returns the new run id, or None when `dedupe` suppressed it.

    Dedupe is on by default because the thing that enqueues is usually a user
    pressing a button: a second press while the first run is still queued or
    running should be a no-op, not a second search.

    Raises DailyCapReached when the user has used the day's allowance for this
    kind. `cap=False` is for internal re-enqueues (a retry of work already
    counted), never for a new user-initiated run.
    """
    conn = database.get_db()
    try:
        if cap:
            limit = daily_limit(kind)
            if limit:
                used = runs_today(user_id, kind, conn)
                if used >= limit:
                    raise DailyCapReached(kind, used, limit)
        if dedupe:
            existing = conn.execute(
                "SELECT id FROM job_runs WHERE user_id=? AND kind=? AND status IN (?,?)",
                (user_id, kind, QUEUED, RUNNING)).fetchone()
            if existing:
                return None

        blob = json.dumps(payload) if payload is not None else None
        if run_after:
            conn.execute(
                "INSERT INTO job_runs (user_id, kind, payload, run_after) VALUES (?,?,?,?)",
                (user_id, kind, blob, run_after))
        else:
            conn.execute(
                "INSERT INTO job_runs (user_id, kind, payload, run_after) "
                "VALUES (?,?,?," + _now_sql() + ")",
                (user_id, kind, blob))
        conn.commit()
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    finally:
        conn.close()


def claim(kinds=None, by=None):
    """
    Take the oldest eligible run, or None.

    Eligible means: queued, its `run_after` has passed, and **this user has no
    other run in flight** - one concurrent run per user, so one heavy account
    cannot starve everyone else or double its own API spend.
    """
    me = by or worker_id()
    conn = database.get_db()
    try:
        sql = ("SELECT id, user_id, kind, payload, attempts FROM job_runs "
               "WHERE status=? AND run_after <= " + _now_sql())
        params = [QUEUED]
        if kinds:
            sql += " AND kind IN (%s)" % ",".join("?" for _ in kinds)
            params.extend(kinds)
        sql += " ORDER BY id LIMIT %d" % _CLAIM_SCAN

        for row in conn.execute(sql, tuple(params)).fetchall():
            busy = conn.execute(
                "SELECT 1 FROM job_runs WHERE user_id=? AND status=? LIMIT 1",
                (row["user_id"], RUNNING)).fetchone()
            if busy:
                continue

            # The compare-and-swap. rowcount 1 means we won the row.
            cur = conn.execute(
                "UPDATE job_runs SET status=?, locked_by=?, locked_at=" + _now_sql() +
                ", started_date=" + _now_sql() + ", attempts=attempts+1 "
                "WHERE id=? AND status=?",
                (RUNNING, me, row["id"], QUEUED))
            conn.commit()
            if cur.rowcount == 1:
                return {"id": row["id"], "user_id": row["user_id"], "kind": row["kind"],
                        "payload": json.loads(row["payload"]) if row["payload"] else None,
                        "attempts": row["attempts"] + 1, "locked_by": me}
        return None
    finally:
        conn.close()


def heartbeat(run_id, by=None):
    """
    Say the run is still alive. Scoped to the holder, so a worker that lost the
    row to the stuck-sweeper cannot keep a run it no longer owns marked healthy.
    """
    me = by or worker_id()
    conn = database.get_db()
    try:
        cur = conn.execute(
            "UPDATE job_runs SET locked_at=" + _now_sql() +
            " WHERE id=? AND locked_by=? AND status=?", (run_id, me, RUNNING))
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def complete(run_id, detail="", by=None):
    me = by or worker_id()
    conn = database.get_db()
    try:
        cur = conn.execute(
            "UPDATE job_runs SET status=?, finished_date=" + _now_sql() +
            ", detail=?, locked_by=NULL, locked_at=NULL "
            "WHERE id=? AND locked_by=? AND status=?",
            (DONE, detail[:2000], run_id, me, RUNNING))
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def fail(run_id, error, by=None, retry=True):
    """
    Record a failure. Re-queues while attempts remain, so a transient Gemini 429
    is a retry rather than a lost run; gives up at MAX_ATTEMPTS so a genuinely
    broken job cannot spin forever.
    """
    me = by or worker_id()
    conn = database.get_db()
    try:
        row = conn.execute("SELECT attempts FROM job_runs WHERE id=?", (run_id,)).fetchone()
        attempts = row["attempts"] if row else MAX_ATTEMPTS
        again = retry and attempts < MAX_ATTEMPTS
        cur = conn.execute(
            "UPDATE job_runs SET status=?, error=?, locked_by=NULL, locked_at=NULL, "
            "finished_date=" + ("NULL" if again else _now_sql()) +
            " WHERE id=? AND locked_by=? AND status=?",
            (QUEUED if again else FAILED, str(error)[:2000], run_id, me, RUNNING))
        conn.commit()
        return {"requeued": again and cur.rowcount == 1, "attempts": attempts}
    finally:
        conn.close()


def requeue_stuck(older_than=None):
    """
    Return runs whose worker died to the queue.

    A redeploy mid-run, an OOM, a crashed thread: the row stays `running` with a
    stale `locked_at` and nothing would ever touch it again. This is the
    existing `applying` sweeper's job, generalised - and the reason `locked_at`
    is written on every heartbeat rather than only at claim time.
    """
    cutoff = older_than if older_than is not None else STUCK_AFTER_SECONDS
    stale = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() - cutoff))
    conn = database.get_db()
    try:
        rows = conn.execute(
            "SELECT id, attempts FROM job_runs WHERE status=? AND locked_at < ?",
            (RUNNING, stale)).fetchall()
        requeued, abandoned = 0, 0
        for row in rows:
            give_up = row["attempts"] >= MAX_ATTEMPTS
            conn.execute(
                "UPDATE job_runs SET status=?, locked_by=NULL, locked_at=NULL, error=? "
                "WHERE id=? AND status=?",
                (FAILED if give_up else QUEUED,
                 "worker stopped responding; abandoned after %d attempt(s)" % row["attempts"]
                 if give_up else "worker stopped responding; requeued",
                 row["id"], RUNNING))
            abandoned += 1 if give_up else 0
            requeued += 0 if give_up else 1
        conn.commit()
        return {"requeued": requeued, "abandoned": abandoned}
    finally:
        conn.close()


def depth():
    """Counts by status, for /api/health. Cheap: one grouped count."""
    conn = database.get_db()
    try:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM job_runs GROUP BY status").fetchall()
        out = {QUEUED: 0, RUNNING: 0, DONE: 0, FAILED: 0}
        for r in rows:
            out[r["status"]] = r["n"]
        oldest = conn.execute(
            "SELECT locked_at FROM job_runs WHERE status=? ORDER BY locked_at LIMIT 1",
            (RUNNING,)).fetchone()
        out["oldest_running_since"] = oldest["locked_at"] if oldest else None
        return out
    finally:
        conn.close()
