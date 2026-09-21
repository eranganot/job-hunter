"""
tests/test_llm_history.py - the 14-day record behind the LLM ceiling.

JH_LLM_GLOBAL_CALLS has sat at its placeholder (50,000) since the ledger
shipped, and STATUS has carried "set it from the real number" as an open item
for as long, because there was no way to SEE the real number: gemini.usage()
reports today and nothing else, so the peak day was invisible from outside the
box and the item could only ever be restated.

history() is that number. Two ways it could exist and still be useless, both
checked here, both found in its first draft:

  * it reads the ledger through a path this module does not have. The draft
    called a `_db()` that gemini.py has never defined - gemini.py deliberately
    imports neither app nor db, and reaches the database only through
    callbacks app.py injects - so every call would have returned
    [{"error": ...}] and health would have shown a tidy, permanent lie.
  * it names a column the ledger does not have. m0007 stores prompt_tokens /
    output_tokens; the draft summed `completion_tokens`.

A stubbed source would have caught neither, so the rows below go in through
app.py's own ledger writer and come back out through app.py's own history
source, with nothing in between replaced.
"""
import json

import pytest

import gemini
from tests.test_routes import Client, stack  # noqa: F401


def _write(app_module, day, user_id, calls, total_tokens):
    app_module._llm_ledger_write(day, user_id, "rank", "flash", calls,
                                 total_tokens - 20, 20, total_tokens, 1, None)


def test_history_returns_real_ledger_rows_through_the_app_wiring(stack):
    """End to end: app writes the ledger, app reads it back, gemini exposes it."""
    app_module = stack["app"]
    _write(app_module, "2026-09-18", 1, 3, 120)
    _write(app_module, "2026-09-18", 2, 2, 60)
    _write(app_module, "2026-09-19", 1, 1, 40)

    rows = gemini.history(14)

    assert rows, "history() returned nothing at all"
    assert "error" not in rows[0], "history() failed: %r" % (rows[0],)
    by_day = {r["day"]: r for r in rows}
    assert by_day["2026-09-18"]["calls"] == 5
    assert by_day["2026-09-18"]["tokens"] == 180
    assert by_day["2026-09-18"]["users"] == 2, "distinct users, not rows"
    # The per-user ceiling is set from the busiest ACCOUNT, not the average:
    # user 1 made 3 of the day's 5 calls.
    assert by_day["2026-09-18"]["max_user_calls"] == 3
    assert by_day["2026-09-19"]["calls"] == 1
    days = [r["day"] for r in rows]
    assert days == sorted(days, reverse=True), "newest first"


def test_history_is_empty_not_broken_before_app_wires_it(monkeypatch):
    """gemini.py alone has no database. That must read as 'no data', not a crash.

    The module is imported by tools that never wire the callbacks (the source
    scanner in test_no_unmetered_gemini, for one), and health must answer on a
    box where the wiring has not run yet.
    """
    monkeypatch.setattr(gemini, "_history_source", None)
    assert gemini.history(14) == []


def test_a_broken_ledger_does_not_take_health_down(monkeypatch):
    """/api/health is how a broken box is diagnosed; a statistic must not break it."""
    def boom(days):
        raise RuntimeError("relation llm_usage does not exist")
    monkeypatch.setattr(gemini, "_history_source", boom)
    out = gemini.history(14)
    assert len(out) == 1 and "relation llm_usage" in out[0]["error"]
    # The type goes with the message: "list index out of range" alone, with
    # no traceback anywhere, is what left a 2026-09-20 smoke failure unexplained.
    assert out[0]["error"].startswith("RuntimeError")


def test_the_days_argument_bounds_the_scan(stack):
    """A ceiling read on every deploy must not grow into a full-table scan."""
    app_module = stack["app"]
    for d in range(1, 6):
        _write(app_module, "2026-08-%02d" % d, 1, 1, 30)
    assert len(gemini.history(2)) == 2


def test_health_carries_the_history(stack):
    """The number has to be where it is already looked at, or it is not evidence."""
    _write(stack["app"], "2026-07-04", 1, 7, 42)
    status, _loc, body = Client(stack["port"]).get("/api/health")
    assert status == 200
    payload = json.loads(body)
    assert "llm_history" in payload, "the ceiling evidence is not on health"
    days = {r["day"]: r for r in payload["llm_history"] if "day" in r}
    assert days["2026-07-04"]["calls"] == 7
