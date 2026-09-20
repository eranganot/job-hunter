"""
errors.py - report an unhandled exception somewhere a person will see it.

Plan Phase 3.6 names Sentry. The gap it closes is specific: today an unhandled
exception prints a traceback into the Railway log, and nothing tells anyone.
With nine friendly users that is survivable - Eran notices when his own search
fails. **The moment a stranger can sign up, a 500 on their first screen is
invisible until they give up and leave**, and they will not write in.

Deliberately a thin wrapper rather than sentry_sdk's framework integrations:

  - This is a stdlib http.server app. The integrations hook WSGI/ASGI, so they
    would not see these handlers anyway.
  - It must be a NO-OP when JH_SENTRY_DSN is unset, which is every local run and
    every test. An error reporter that needs configuring to not explode is one
    more thing that can take the app down.
  - The import is lazy, so sentry_sdk is not a hard dependency. A deploy that
    cannot install it loses reporting, not serving.

Scrubbing: request paths are sent, query strings are NOT. Job Hunter puts no
secrets in a path, but /api/... query strings carry ids and search text, and a
crash report is not a place to start keeping user content.
"""
import os
import sys

_client = None        # the sentry_sdk module once initialised
_state = "unconfigured"


def init() -> str:
    """Set up reporting if a DSN is configured. Returns the resulting state.

    Called once at boot. Safe to call again; safe to never call.
    """
    global _client, _state
    if _state != "unconfigured":
        return _state
    dsn = (os.environ.get("JH_SENTRY_DSN", "") or "").strip()
    if not dsn:
        _state = "disabled (no JH_SENTRY_DSN)"
        return _state
    try:
        import sentry_sdk
    except ImportError:
        _state = "unavailable (sentry-sdk not installed)"
        return _state
    try:
        sentry_sdk.init(
            dsn=dsn,
            environment=os.environ.get("RAILWAY_ENVIRONMENT_NAME", "unknown"),
            release=os.environ.get("RAILWAY_GIT_COMMIT_SHA", "")[:7] or None,
            # No performance tracing: the value here is "something broke and
            # nobody knew", not latency percentiles, and traces are the
            # expensive half of the free tier.
            traces_sample_rate=0.0,
            send_default_pii=False,
        )
        _client = sentry_sdk
        _state = "on"
    except Exception as exc:
        _state = "failed (%s)" % str(exc)[:80]
    return _state


def capture(exc: BaseException, *, where: str = "", path: str = "", user_id=None) -> bool:
    """Report one exception. Returns True if it was sent.

    NEVER raises. A reporter that can throw turns one broken request into two,
    and the second one happens inside the handler that was trying to apologise
    for the first.
    """
    if _client is None:
        return False
    try:
        with _client.push_scope() as scope:
            if where:
                scope.set_tag("where", where)
            if path:
                # Path only - query strings carry ids and search text.
                scope.set_tag("path", str(path).split("?")[0])
            if user_id is not None:
                scope.set_user({"id": user_id})
            _client.capture_exception(exc)
        return True
    except Exception:
        return False


def status() -> dict:
    """For /api/health, so "is error reporting actually on" is answerable from
    outside rather than assumed because the variable looks set."""
    return {"state": _state, "reporting": _client is not None}
