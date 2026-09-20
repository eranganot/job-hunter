"""
log.py - one logging setup for every module, and a request id that ties a
request's log lines to each other.

What was already here, because it changes what this module is for
-----------------------------------------------------------------
The plan's observability item reads "print() -> logging (~166 calls)". Half of
that was already done: app.py shadows the `print` builtin with a shim that
routes every call through the logging module, so its 153 print() sites already
carry a timestamp and a level. The count was also app.py's alone - the repo has
**298** print() calls, and the other 142 (apply_engine, db, relay, worker,
crypto, storage, gemini, dbdriver, migrations, ai_analysis, ingestion) still
went to bare stdout with no timestamp, no level and no module name.

So this module does not rewrite 298 call sites by hand. It extracts app.py's
shim, tests it, and lets the other modules opt in with one line each.

The level inference, and the hole a static audit could not see
--------------------------------------------------------------
The shim picks a level from the message text. Grading app.py's 153 messages
statically said the inference was fine: 107 INFO, 46 ERROR, and of those only
12 arguable - non-fatal failures better as WARNING, with nothing real quietly
graded INFO. On that reading it should have been left alone.

**That reading was wrong, and a test proved it.** crypto.py logs a failed
decryption as `print("[crypto] %s for user %s: %s" % (field, uid, exc))` - the
literal carries no failure word at all, and the words live in `exc`, at
runtime. A static scan cannot see them. So "a stored credential could not be
decrypted - JH_ENCRYPTION_KEY does not match the key it was encrypted with"
was graded **INFO**: invisible to any alert keyed on level, which is the exact
direction the audit had declared clean. **130 of the repo's 246 print() calls
end in a bare interpolated value**, so their level is decided by text nobody
wrote deliberately.

The fix is a third tier rather than a wider ERROR. The vocabulary an exception
message actually uses - "could not", "cannot", "unable to", "refused",
"invalid", "timed out", "no such", "not found", "unavailable", "denied" -
now grades **WARNING**, while ERROR stays reserved for the explicit
error/fail/exception/traceback words. Measured against every static message in
the repo this regrades **three**, all correctly (pywebpush unavailable, the
ingestion module falling back to legacy dedup, a refused cross-site request),
and none of the three is an error - which is why they are warnings and not a
fourth entry in the ERROR list that an alert rule would then have to ignore.

An explicit level always beats the inference: use get(__name__) and call
.warning/.error directly anywhere the level actually matters.

What is actually new
--------------------
A **request id**. A user reports "it broke at about two" and today the logs
answer with a hundred interleaved lines from ten threads and no way to tell
which belong together or whose they are. Every record emitted while a request
is in flight now carries `rid` (that request) and `u` (the user id, once auth
has resolved one), and each request ends with one access line giving method,
path, status and duration. That is the difference between "a 500 happened" and
"user 7's cover-letter request 3f2a1b failed, and here are the six lines it
produced on the way".

Thread safety: the context is a ContextVar, and a thread started by
ThreadingMixIn begins with a fresh context, so two concurrent requests cannot
see each other's id. Work handed to a NEW background thread starts with no
request - it logs `rid=-`, which is honest, rather than inheriting an id for a
request that has already returned.
"""
from __future__ import annotations

import contextvars
import logging
import os
import re
import secrets
import time

LOGGER_NAME = "jobhunter"

# The request currently being served on this thread, or None.
_REQUEST: contextvars.ContextVar = contextvars.ContextVar("jh_request", default=None)

_CONFIGURED = False

# Paths whose successful responses are not worth a line each. A PWA shell load
# is ~30 static files; logging them buries the request that actually matters.
# Only 2xx/3xx are skipped - a 404 or a 500 on a static path is a real event.
_QUIET_PREFIXES = ("/app/", "/static/", "/favicon", "/sw.js", "/manifest")


class Request:
    __slots__ = ("rid", "user_id", "method", "path", "ip", "started", "status")

    def __init__(self, method, path, ip):
        self.rid = secrets.token_hex(3)      # short on purpose: it is read by eye
        self.user_id = None
        self.method = method
        self.path = path
        self.ip = ip
        self.started = time.time()
        self.status = None

    @property
    def ms(self):
        return int((time.time() - self.started) * 1000)


class _ContextFilter(logging.Filter):
    """Put rid/uid on every record, including records from libraries.

    A Filter rather than a Formatter subclass or a LoggerAdapter: those only
    reach records made through them, and the point is that a record from
    anywhere in the process - including one a library emits during a request -
    can be tied back to the request that caused it.
    """

    def filter(self, record):
        req = _REQUEST.get()
        record.rid = req.rid if req else "-"
        record.uid = (req.user_id if (req and req.user_id is not None) else "-")
        return True


def setup(level=None):
    """Configure the root logger. Idempotent; safe to call from any module."""
    global _CONFIGURED
    if _CONFIGURED:
        return logging.getLogger(LOGGER_NAME)
    lvl = level or os.environ.get("JH_LOG_LEVEL", "INFO").upper()
    fmt = "%(asctime)s [%(levelname)s] [%(name)s] rid=%(rid)s u=%(uid)s %(message)s"
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(fmt))
    handler.addFilter(_ContextFilter())
    handler._jh_own = True
    root = logging.getLogger()
    # Remove only handlers this module installed. An earlier version cleared
    # ALL of them, to stop app.py's basicConfig handler double-printing - and
    # in doing so silently removed any handler the host had already attached.
    # The tests found it first: pytest's caplog attaches to the root, so every
    # assertion about a log record saw nothing. A deployment that configures
    # logging before importing this would have lost it the same way, and would
    # have had no test to notice.
    for h in list(root.handlers):
        if getattr(h, "_jh_own", False):
            root.removeHandler(h)
    root.addHandler(handler)
    # The context fields must exist on records from handlers this module did
    # NOT install (caplog's, a host's) or their formatters raise KeyError.
    root.addFilter(_ContextFilter())
    try:
        root.setLevel(getattr(logging, lvl))
    except AttributeError:
        root.setLevel(logging.INFO)
    _CONFIGURED = True
    return logging.getLogger(LOGGER_NAME)


def get(name: str):
    """A logger for one module. `name` is the short module name, not __name__,
    because "jobhunter.gemini" reads better in a log line than "gemini"."""
    setup()
    short = name.rsplit(".", 1)[-1].replace("_", "-")
    return logging.getLogger("%s.%s" % (LOGGER_NAME, short))


# ── the print shim ───────────────────────────────────────────────────────────

# The words an exception message uses when it is not using "error" or "fail".
# These grade WARNING, not ERROR - see the module docstring for why the tier is
# separate and what it was measured against.
_TROUBLE = re.compile(
    r"\bcould not\b|\bcouldn't\b|\bcannot\b|\bcan't\b|\bunable to\b|"
    r"\brefused\b|\bdenied\b|\binvalid\b|\btimed out\b|\btimeouts?\b|"
    r"\bno such\b|\bnot found\b|\bdoes not match\b|\bdoesn't match\b|"
    r"\bunavailable\b|\baborted\b|\bis not configured\b|\bmissing required\b")

# The words that mean a line is actually an error - as WORDS.
#
# This was a bare `"error" in msg` substring test, which matched the column
# name in "[db] baseline: added apply_error to jobs" and logged every schema
# line of every startup at ERROR. Thirteen of them rendered in red directly
# above four real failures in a ship run (2026-09-15): a log that cries wolf on
# every deploy is worse than one tier too quiet, because the reader stops
# reading it. Python's \b treats "_" as a word character, so "\berror\b" does
# not match inside apply_error, apply_failure_type or read_timeout - which is
# exactly the distinction that was missing.
_BAD = re.compile(r"\berrors?\b|\berrored\b|\bfail(?:s|ed|ing|ure|ures)?\b|"
                  r"\btracebacks?\b|\bexceptions?\b")


def _level_for(msg: str) -> int:
    """Grade a legacy print() by its text. See the module docstring."""
    low = msg.lower()
    if _BAD.search(low):
        return logging.ERROR
    if re.search(r"\bwarn(?:ing|ings|ed)?\b", low) or _TROUBLE.search(low):
        return logging.WARNING
    return logging.INFO


def make_print(name: str):
    """Build a drop-in `print` that logs, tagged with this module's name.

    A module installs it with:

        import log
        print = log.make_print(__name__)      # noqa: A001

    which converts every print() in that module at once. Editing 142 call sites
    by hand would be churn with a real chance of typos, for an outcome this
    achieves in one line per module - and unlike the hand edit, this one is
    tested (tests/test_log.py) rather than reviewed.
    """
    logger = get(name)

    def _safe(a):
        # The original shim's fallback re-ran str() on the same arguments, so an
        # object whose __str__ raises took down the code that was reporting on
        # it - a logging call is never allowed to be the thing that fails.
        try:
            return str(a)
        except Exception:
            try:
                return repr(a)
            except Exception:
                return "<unprintable %s>" % type(a).__name__

    def _print(*args, **kwargs):
        msg = kwargs.get("sep", " ").join(_safe(a) for a in args)
        logger.log(_level_for(msg), msg)

    return _print


# ── request context ──────────────────────────────────────────────────────────

def begin(method: str, path: str, ip: str = "") -> Request:
    req = Request(method, path, ip)
    _REQUEST.set(req)
    return req


def current():
    return _REQUEST.get()


def set_user(user_id):
    """Called once auth has resolved a session. Everything logged after this
    point in the request carries the user, which is the field that turns a log
    search into an answer."""
    req = _REQUEST.get()
    if req is not None and user_id is not None:
        req.user_id = user_id


def current_user_id():
    """Whoever this request resolved to, or None. Lets an error report name the
    user without every call site having to thread the id down to it."""
    req = _REQUEST.get()
    return getattr(req, "user_id", None) if req is not None else None


def set_status(code):
    req = _REQUEST.get()
    if req is not None:
        req.status = code


def end(logger=None):
    """Emit the one access line for this request and clear the context."""
    req = _REQUEST.get()
    if req is None:
        return
    status = req.status if req.status is not None else 0
    quiet = (200 <= int(status) < 400) and req.path.startswith(_QUIET_PREFIXES)
    lg = logger or get("access")
    if quiet:
        lvl = logging.DEBUG
    elif int(status) >= 500:
        lvl = logging.ERROR
    elif int(status) >= 400:
        lvl = logging.WARNING
    else:
        lvl = logging.INFO
    # Logged while the context is still set, so the record's own rid/uid fields
    # are filled by the filter like every other line. Cleared afterwards.
    lg.log(lvl, "%s %s -> %s in %sms ip=%s",
           req.method, req.path, status, req.ms, req.ip or "-")
    _REQUEST.set(None)
    return req


class request:
    """Context manager form, so a handler cannot forget to end a request."""

    def __init__(self, method, path, ip=""):
        self._args = (method, path, ip)

    def __enter__(self):
        return begin(*self._args)

    def __exit__(self, *exc):
        end()
        return False


def reset_for_tests():
    global _CONFIGURED
    _REQUEST.set(None)
    _CONFIGURED = False
