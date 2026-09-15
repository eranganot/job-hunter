"""
gemini.py - the only place in this app that talks to the Gemini API.

Why this module exists
----------------------
Before it, ten separate call sites built their own URL and called urlopen
themselves: four in ai_analysis.py, five in app.py, one in apply_engine.py.
Three of them went through app._gemini_generate and got retry/backoff on 429;
the other seven did not, which is the mechanism behind the two logged incidents
where "Gemini 429s degraded prod scoring" - a single un-retried 429 dropped
scoring to the weak keyword heuristic and nothing said so.

Ten doors also means a spend ceiling can only be enforced ten times, and the
eleventh call site someone adds next month silently bypasses it. So: one door.

What it enforces
----------------
  * GLOBAL daily ceiling - the runaway-loop guard. A bug that spins Gemini
    calls in a tight loop is the failure mode that actually costs money
    overnight; a finite ceiling stops it even when nobody is watching.
  * PER-USER daily ceiling - the abuse guard. Matters the moment signups are
    public: one account cannot spend everyone else's budget.

Both are counted in CALLS and in TOKENS (Gemini returns usageMetadata on every
generateContent response). Tokens are the real unit of cost, calls are the unit
that is always available - if a response arrives without usageMetadata the call
is still counted, so the guard degrades to call-counting rather than to nothing.

Defaults are deliberately generous. Nothing here has ever been measured on this
app, and a ceiling invented from nothing that throttles nine real users is a
worse outcome than no ceiling at all. The defaults are set well above anything
normal use can reach and comfortably below what a runaway loop does in an hour;
once a week of real rows exists in llm_usage, tighten them with JH_LLM_* env
vars - no deploy needed.

Breach behaviour is the CALLER's decision, which is why this raises a typed
BudgetExceeded rather than returning a sentinel: job scoring should fall back
to the keyword heuristic it already has, while a cover letter should fail
loudly rather than hand the user something it did not generate.

Scope, stated because it will matter later: the running totals are per-process
memory, re-synced from the database every _SYNC_EVERY seconds. The app runs as
one instance, so today the totals are exact. Run two and each undercounts by
up to the other's activity within a sync window - at which point this wants
Redis, the same conclusion ratelimit.py reaches for the same reason.
"""
from __future__ import annotations

import json
import os
import random
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

# Route this module's print() calls through the logging module: timestamps,
# levels, the module name, and the request id of the request in flight.
import log as _log
print = _log.make_print(__name__)  # noqa: A001 - see log.py


API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_MODEL = "gemini-2.5-flash"

_LOCK = threading.RLock()
_DAY = ""                      # UTC day the counters below describe
_GLOBAL = {"calls": 0, "tokens": 0}
_PER_USER: dict[int, dict] = {}   # user_id -> {"calls": n, "tokens": n}
_LAST_SYNC = 0.0
_SYNC_EVERY = 30               # seconds; see the multi-instance note above
_ALERTED: set = set()          # (day, scope) already alerted on, so one breach
                               # does not send one notification per blocked call

_PENDING: list = []            # rows the ledger could not write yet
_MAX_PENDING = 500

# Injected by app.py at import time so this module never imports app or db
# directly (both import paths that would close a circle).
_ledger_writer = None
_alert_sender = None


_CURRENT = threading.local()


class bind_user:
    """Attribute every Gemini call on THIS thread to a user, as a context manager.

    ai_analysis.py takes user_id as an explicit argument, which is the clearer
    shape and the one to prefer. apply_engine.py cannot: its LLM calls sit three
    levels down from the run inside a helper (_claude) that eight call sites
    share, and threading an id through all of them would be churn for no reading
    benefit. So the apply run binds once and the helper stays as it is.

    Thread-local, not global, so two users' runs cannot be attributed to each
    other. It does NOT cross into threads the bound code spawns - a spawned
    thread records against no user, which undercounts that user rather than
    charging the wrong one. An explicit user_id argument always wins over the
    binding.
    """

    def __init__(self, user_id):
        self.user_id = user_id
        self._prev = None

    def __enter__(self):
        self._prev = getattr(_CURRENT, "user_id", None)
        _CURRENT.user_id = self.user_id
        return self

    def __exit__(self, *exc):
        _CURRENT.user_id = self._prev
        return False


def current_user():
    return getattr(_CURRENT, "user_id", None)


class BudgetExceeded(RuntimeError):
    """Raised INSTEAD of calling Gemini, when a ceiling is already spent.

    Carries the scope that tripped so callers and logs can say which one.
    """

    def __init__(self, scope: str, used: int, limit: int, unit: str):
        self.scope, self.used, self.limit, self.unit = scope, used, limit, unit
        super().__init__(
            "Gemini %s daily ceiling reached: %s %s used of %s"
            % (scope, used, unit, limit)
        )


# ── limits ────────────────────────────────────────────────────────────────────

def _limit(name: str, default: int) -> int:
    try:
        return int(os.environ.get("JH_LLM_%s" % name, default))
    except (TypeError, ValueError):
        return default


def limits() -> dict:
    """Current ceilings. 0 means unlimited, so a limit can be switched off
    without a code change if one of them turns out to be wrong in production."""
    return {
        "global_calls":   _limit("GLOBAL_CALLS", 50000),
        "global_tokens":  _limit("GLOBAL_TOKENS", 0),
        "user_calls":     _limit("USER_CALLS", 1500),
        "user_tokens":    _limit("USER_TOKENS", 0),
        "enforce":        _limit("ENFORCE", 1),
    }


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ── counters ──────────────────────────────────────────────────────────────────

def set_ledger_writer(fn):
    """fn(day, user_id, purpose, model, calls, prompt_tokens, output_tokens,
    total_tokens, ok, error) -> None. Set by app.py."""
    global _ledger_writer
    _ledger_writer = fn


def set_alert_sender(fn):
    """fn(message) -> None. Set by app.py; used once per day per breached scope."""
    global _alert_sender
    _alert_sender = fn


def set_sync_source(fn):
    """fn(day) -> {"global": {...}, "users": {uid: {...}}}. Set by app.py so a
    restart does not hand the day a fresh, empty budget."""
    global _sync_source
    _sync_source = fn


_sync_source = None


def _roll_day_locked(day: str):
    global _DAY, _GLOBAL, _PER_USER, _LAST_SYNC
    _DAY = day
    _GLOBAL = {"calls": 0, "tokens": 0}
    _PER_USER = {}
    _LAST_SYNC = 0.0


def _sync_locked(day: str, force: bool = False):
    """Re-seed the in-memory counters from the ledger.

    Without this a restart hands the current day a clean budget, which is
    exactly the wrong behaviour when the thing that restarted the process was
    the runaway loop the ceiling is meant to stop.
    """
    global _LAST_SYNC
    now = time.time()
    if not force and now - _LAST_SYNC < _SYNC_EVERY:
        return
    _LAST_SYNC = now
    if _sync_source is None:
        return
    try:
        data = _sync_source(day) or {}
    except Exception as e:                       # ledger unavailable: keep going
        print("[gemini] usage sync failed (non-fatal): %s" % e)
        return
    g = data.get("global") or {}
    _GLOBAL["calls"] = max(_GLOBAL["calls"], int(g.get("calls") or 0))
    _GLOBAL["tokens"] = max(_GLOBAL["tokens"], int(g.get("tokens") or 0))
    for uid, row in (data.get("users") or {}).items():
        try:
            uid = int(uid)
        except (TypeError, ValueError):
            continue
        cur = _PER_USER.setdefault(uid, {"calls": 0, "tokens": 0})
        cur["calls"] = max(cur["calls"], int(row.get("calls") or 0))
        cur["tokens"] = max(cur["tokens"], int(row.get("tokens") or 0))


def usage(user_id: int | None = None) -> dict:
    """Today's totals. Cheap; safe to call from a health endpoint."""
    day = _today()
    with _LOCK:
        if day != _DAY:
            _roll_day_locked(day)
        _sync_locked(day)
        out = {"day": day, "global": dict(_GLOBAL), "limits": limits()}
        if user_id is not None:
            out["user"] = dict(_PER_USER.get(int(user_id), {"calls": 0, "tokens": 0}))
        return out


def _alert_locked(scope: str, message: str):
    key = (_DAY, scope)
    if key in _ALERTED:
        return
    _ALERTED.add(key)
    if _alert_sender is None:
        print("[gemini] BREACH (no alert channel): %s" % message)
        return
    try:
        _alert_sender(message)
    except Exception as e:
        print("[gemini] breach alert failed (non-fatal): %s" % e)


def check(user_id: int | None = None, cost: int = 1):
    """Raise BudgetExceeded if this call would cross a ceiling.

    Alerting happens whether or not enforcement is on: knowing the ceiling was
    crossed is the point, blocking is the consequence.
    """
    lim = limits()
    day = _today()
    with _LOCK:
        if day != _DAY:
            _roll_day_locked(day)
        _sync_locked(day)
        breaches = []
        if lim["global_calls"] and _GLOBAL["calls"] + cost > lim["global_calls"]:
            breaches.append(("global", _GLOBAL["calls"], lim["global_calls"], "calls"))
        if lim["global_tokens"] and _GLOBAL["tokens"] >= lim["global_tokens"]:
            breaches.append(("global", _GLOBAL["tokens"], lim["global_tokens"], "tokens"))
        if user_id is not None:
            u = _PER_USER.get(int(user_id), {"calls": 0, "tokens": 0})
            if lim["user_calls"] and u["calls"] + cost > lim["user_calls"]:
                breaches.append(("user:%s" % user_id, u["calls"], lim["user_calls"], "calls"))
            if lim["user_tokens"] and u["tokens"] >= lim["user_tokens"]:
                breaches.append(("user:%s" % user_id, u["tokens"], lim["user_tokens"], "tokens"))
        if not breaches:
            return
        scope, used, limit, unit = breaches[0]
        _alert_locked(
            scope,
            "\U000026A0 Job-Hunter: Gemini %s daily ceiling reached - %s %s of %s used "
            "(%s). Calls are %s."
            % (scope, used, unit, limit, day,
               "BLOCKED" if lim["enforce"] else "still going through (JH_LLM_ENFORCE=0)"),
        )
        if lim["enforce"]:
            raise BudgetExceeded(scope, used, limit, unit)


def _record(user_id, purpose, model, prompt_tokens, output_tokens, total_tokens,
            ok=True, error=""):
    day = _today()
    with _LOCK:
        if day != _DAY:
            _roll_day_locked(day)
        _GLOBAL["calls"] += 1
        _GLOBAL["tokens"] += int(total_tokens or 0)
        if user_id is not None:
            cur = _PER_USER.setdefault(int(user_id), {"calls": 0, "tokens": 0})
            cur["calls"] += 1
            cur["tokens"] += int(total_tokens or 0)
    row = (day, user_id, purpose, model, 1, int(prompt_tokens or 0),
           int(output_tokens or 0), int(total_tokens or 0), 1 if ok else 0,
           (error or "")[:300])
    _flush(row)


def _flush(row=None):
    """Write to the ledger, holding rows back if the database is unavailable.

    A failed ledger write must never fail a Gemini call that already succeeded -
    the user's work is done and the row is only accounting. But dropping it
    silently would make the ceiling drift below the truth, so rows queue and go
    in on the next successful write.
    """
    with _LOCK:
        if row is not None:
            _PENDING.append(row)
            if len(_PENDING) > _MAX_PENDING:
                del _PENDING[: len(_PENDING) - _MAX_PENDING]
        if _ledger_writer is None or not _PENDING:
            return
        batch, del_count = list(_PENDING), len(_PENDING)
    try:
        for r in batch:
            _ledger_writer(*r)
    except Exception as e:
        print("[gemini] ledger write failed (queued, non-fatal): %s" % e)
        return
    with _LOCK:
        del _PENDING[:del_count]


def _usage_from(result: dict) -> tuple:
    """Pull token counts out of a generateContent response.

    Defensive on purpose: the guard must keep working if the field is renamed
    or absent, degrading to call-counting rather than to a crash.
    """
    meta = (result or {}).get("usageMetadata") or {}
    p = meta.get("promptTokenCount") or 0
    o = meta.get("candidatesTokenCount") or 0
    t = meta.get("totalTokenCount") or 0
    if not t:
        # Some responses count thinking separately; fall back to the sum.
        t = int(p or 0) + int(o or 0) + int(meta.get("thoughtsTokenCount") or 0)
    return int(p or 0), int(o or 0), int(t or 0)


def api_key(explicit: str = "") -> str:
    return (explicit
            or os.environ.get("GEMINI_API_KEY", "")
            or os.environ.get("GEMINI_KEY", ""))


def generate(body, *, purpose: str, user_id: int | None = None,
             key: str = "", model: str = DEFAULT_MODEL,
             timeout: int = 90, retries: int = 4) -> dict:
    """POST one generateContent request. The ONLY way this app calls Gemini.

    `body` is the request dict (or pre-encoded bytes, for the call sites that
    already built one) - each caller keeps its own prompt shape and its own
    response parsing; only the metered middle is shared.

    Retries 429/500/503 with exponential backoff, honouring Retry-After. This
    was app._gemini_generate, which only three of the ten call sites used.
    """
    k = api_key(key)
    if not k:
        raise ValueError("GEMINI_API_KEY not configured.")
    if user_id is None:
        user_id = current_user()
    check(user_id=user_id)

    if isinstance(body, (bytes, bytearray)):
        data = bytes(body)
    else:
        data = json.dumps(body).encode("utf-8")
    url = "%s/%s:generateContent?key=%s" % (API_BASE, model, k)

    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                url, data=data, method="POST",
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            p, o, t = _usage_from(result)
            _record(user_id, purpose, model, p, o, t, ok=True)
            return result
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 500, 503) and attempt < retries:
                ra = e.headers.get("Retry-After") if e.headers else None
                delay = (float(ra) if (ra and str(ra).isdigit())
                         else min(2 ** attempt + random.random(), 30))
                print("[gemini] HTTP %s on %s - backoff retry %s/%s in %.1fs"
                      % (e.code, purpose, attempt + 1, retries, delay))
                time.sleep(delay)
                continue
            body_txt = ""
            try:
                body_txt = e.read().decode("utf-8", errors="replace")[:300]
            except Exception:
                pass
            # A failed call still consumed quota at Google's end on some error
            # classes, and more importantly a loop of failures is exactly what
            # a ceiling should stop - so failures are recorded too.
            _record(user_id, purpose, model, 0, 0, 0, ok=False,
                    error="HTTP %s %s" % (e.code, body_txt))
            raise RuntimeError("Gemini API error %s: %s" % (e.code, body_txt))
        except Exception as e:
            last = e
            if attempt < retries:
                time.sleep(min(2 ** attempt, 8))
                continue
            _record(user_id, purpose, model, 0, 0, 0, ok=False, error=str(e))
            raise
    raise last  # pragma: no cover


def generate_text(body, **kw) -> str:
    """generate(), then the first candidate's text. Most callers want this."""
    result = generate(body, **kw)
    return result["candidates"][0]["content"]["parts"][0]["text"]


def reset_for_tests():
    with _LOCK:
        _roll_day_locked("")
        _ALERTED.clear()
        del _PENDING[:]
