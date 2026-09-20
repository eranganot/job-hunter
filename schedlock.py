"""
schedlock.py - only one instance may run the scheduler tick.

Plan Phase 3.2: "Scheduler moves behind a Postgres advisory lock so two
instances can't double-fire a user's daily run."

Today there is one instance, so nothing double-fires and this buys nothing
visible. It is built now because the failure it prevents is silent and
expensive: two web dynos both reach the same minute, both see
current_hour == search_hour, and both enqueue. The queue's one-run-per-user
rule (jobqueue.py) catches the search itself, but the SCHEDULER is what decides
whether a run happens at all, and a second instance doubles every user's Gemini
spend before the queue gets a say.

Postgres: pg_try_advisory_lock, held on a dedicated connection for the life of
the process. Non-blocking, so a second instance simply does not tick rather
than queueing up behind the first. The lock dies with the connection, which is
what makes a crashed instance recoverable without a timeout to tune.

SQLite: there is no second instance to race - one file, one process - so the
lock is granted unconditionally. Pretending otherwise would mean inventing a
lock table and a lease, i.e. new failure modes to protect against a race that
cannot happen on that engine.
"""
import os

# Arbitrary but fixed. Postgres advisory locks are a 64-bit keyspace shared by
# the whole database, so the number has to be one nothing else picks; it is
# written down here rather than computed from a string, because a hash function
# change would silently move the lock and let two instances tick at once.
LOCK_KEY = 8_143_552_900_117_001

_conn = None          # held for the process lifetime; releasing it releases the lock
_held = None          # None = never asked


def _enabled() -> bool:
    return (os.environ.get("JH_SCHED_LOCK", "1") or "1").strip().lower() not in ("0", "false", "no")


def acquire(get_conn, backend: str) -> bool:
    """True if this process may run the scheduler. Asked once; cached after.

    `get_conn` is called at most once, and the connection is deliberately never
    returned to the pool: an advisory lock lives on its session, so handing the
    connection back would drop the lock while this process still believed it
    held it - the worst of both worlds.
    """
    global _conn, _held
    if _held is not None:
        return _held
    if not _enabled():
        _held = True
        return _held
    if backend != "postgres":
        # One file, one writer, no second instance to race.
        _held = True
        return _held
    try:
        conn = get_conn()
        row = conn.execute("SELECT pg_try_advisory_lock(%s)" % LOCK_KEY).fetchone()
        got = bool(row[0]) if row is not None else False
        if got:
            _conn = conn          # keep the session, keep the lock
        else:
            try:
                conn.close()
            except Exception:
                pass
        _held = got
    except Exception as exc:
        # A lock we cannot take must not stop the scheduler on a single-instance
        # deploy - that would turn a safety net into an outage. Fail OPEN and
        # say so; the queue still refuses a second concurrent run per user.
        print("[scheduler] advisory lock unavailable (%s) - ticking anyway" % str(exc)[:120])
        _held = True
    return _held


def status() -> dict:
    """For /api/health, so 'the scheduler is not running' is answerable from
    outside instead of inferred from jobs not happening."""
    return {"enabled": _enabled(), "holds_lock": _held, "key": LOCK_KEY}


def reset_for_tests():
    global _conn, _held
    if _conn is not None:
        try:
            _conn.close()
        except Exception:
            pass
    _conn, _held = None, None
