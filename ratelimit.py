"""
ratelimit.py - one sliding-window limiter, used by every endpoint that needs one.

Phase 3. Login already had a limiter (app.py, per-IP AND per-email, 8 failures
per 15 minutes). It worked; this generalises it rather than replacing it, so
login's behaviour is unchanged and register and run-search stop being the only
unprotected doors.

Two shapes, because the endpoints need different things:

  * FAILURE-based (login): only wrong passwords count, and a success clears the
    record. A user who signs in correctly fifty times is not an attacker.
  * ATTEMPT-based (register, run-search): every call counts, because the
    expensive or abusive thing IS the call - a thousand accounts, or a thousand
    Gemini-burning searches.

Scope, stated because it will matter later: this is per-process memory. The app
runs as one instance, so the limits are the real limits today. Run two
instances and each gets its own counters - at which point this needs Redis,
which the project already has a service for.
"""
import os
import threading
import time

# Route this module's print() calls through the logging module: timestamps,
# levels, the module name, and the request id of the request in flight.
import log as _log
print = _log.make_print(__name__)  # noqa: A001 - see log.py


_LOCK = threading.Lock()
_HITS = {}                      # (bucket, key) -> [timestamps]
_LAST_SWEEP = 0.0
_SWEEP_EVERY = 300              # seconds


def _policy(name, default_limit, default_window):
    """Limits are env-tunable: a lockout that cannot be relaxed is its own outage."""
    limit = int(os.environ.get("JH_RL_%s_MAX" % name.upper(), default_limit))
    window = int(os.environ.get("JH_RL_%s_WINDOW" % name.upper(), default_window))
    return limit, window


# name -> (limit, window seconds)
POLICIES = {
    "login":      lambda: _policy("login", 8, 900),        # 8 failures / 15 min
    "register":   lambda: _policy("register", 5, 3600),    # 5 new accounts / hour / IP
    "run_search": lambda: _policy("run_search", 6, 3600),  # 6 manual searches / hour / user
}


def _sweep(now):
    """
    Drop buckets nobody has touched in a long time.

    Without this the dict grows forever: entries are only pruned when their own
    key is checked again, and an attacker rotating IPs never revisits a key.
    """
    global _LAST_SWEEP
    if now - _LAST_SWEEP < _SWEEP_EVERY:
        return
    _LAST_SWEEP = now
    longest = max(w for _l, w in (p() for p in POLICIES.values()))
    for key in [k for k, ts in _HITS.items() if not ts or now - ts[-1] > longest]:
        _HITS.pop(key, None)


def retry_after(bucket: str, key: str) -> int:
    """Seconds to wait if this key is currently limited, else 0. Records nothing."""
    limit, window = POLICIES[bucket]()
    now = time.time()
    with _LOCK:
        _sweep(now)
        hits = [t for t in _HITS.get((bucket, key), []) if now - t < window]
        _HITS[(bucket, key)] = hits
        if len(hits) >= limit:
            return int(window - (now - hits[0])) + 1
    return 0


def record(bucket: str, key: str):
    """Count one hit against this key."""
    now = time.time()
    with _LOCK:
        _sweep(now)
        _HITS.setdefault((bucket, key), []).append(now)


def clear(bucket: str, key: str):
    """Forget this key - a successful login should not leave a user near a lockout."""
    with _LOCK:
        _HITS.pop((bucket, key), None)


def check_and_record(bucket: str, key: str) -> int:
    """
    Attempt-based limiting in one call: returns seconds to wait, or 0 and counts
    the hit. Used where the call itself is the thing being limited.
    """
    wait = retry_after(bucket, key)
    if wait:
        return wait
    record(bucket, key)
    return 0


def reset_all():
    """For tests."""
    global _LAST_SWEEP
    with _LOCK:
        _HITS.clear()
        _LAST_SWEEP = 0.0


def snapshot():
    """Bucket sizes, for /api/health and for proving the sweep works."""
    with _LOCK:
        return {"tracked_keys": len(_HITS)}
