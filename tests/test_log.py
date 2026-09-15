"""
tests/test_log.py - the request id, the level inference, and the print shim.

Logging is the thing you only find out is wrong when you need it, which is the
worst possible moment. So the properties here are the ones that decide whether
a production question is answerable at all:

  * can two concurrent requests' lines be told apart,
  * does a line name the user it was for,
  * does a real failure reach a level an alert can key on.

The third is the one that was actually broken. See log.py's docstring: a static
audit of every print() in the repo said the level inference was fine, and it
was not, because 130 of the 246 messages end in an interpolated value whose
text no one wrote.
"""
import logging
import threading

import pytest

import log


@pytest.fixture(autouse=True)
def clean():
    log.reset_for_tests()
    yield
    log.reset_for_tests()


# ── the level inference ──────────────────────────────────────────────────────

@pytest.mark.parametrize("msg, level", [
    ("[search] found 12 jobs",                      "INFO"),
    ("[apply] submission failed",                   "ERROR"),
    ("Traceback (most recent call last)",           "ERROR"),
    ("[db] warning: slow query",                    "WARNING"),
    # The tier that did not exist, and the message that proved it was needed.
    ("[crypto] telegram_token for user 7: a stored credential could not be "
     "decrypted - JH_ENCRYPTION_KEY does not match the key it was encrypted with",
                                                    "WARNING"),
    ("[push] pywebpush unavailable: no module",     "WARNING"),
    ("[ats] request timed out after 30s",           "WARNING"),
    ("[auth] permission denied for token",          "WARNING"),
])
def test_a_message_lands_on_a_level_an_alert_can_key_on(msg, level):
    assert logging.getLevelName(log._level_for(msg)) == level


def test_trouble_words_do_not_inflate_the_error_count():
    """WARNING is a separate tier on purpose: if 'could not' graded ERROR, an
    alert on ERROR would fire on every optional dependency that is not
    installed, and would then have to be ignored."""
    assert log._level_for("[push] pywebpush unavailable") == logging.WARNING
    assert log._level_for("[apply] submission failed") == logging.ERROR


# ── the print shim ───────────────────────────────────────────────────────────

def test_the_shim_logs_instead_of_printing(caplog, capsys):
    p = log.make_print("widget")
    with caplog.at_level(logging.INFO):
        p("[widget] hello", 42)
    assert "[widget] hello 42" in caplog.text
    assert capsys.readouterr().out == "", "the shim still wrote to stdout"


def test_the_shim_names_the_module_that_logged(caplog):
    with caplog.at_level(logging.INFO):
        log.make_print("gemini")("anything")
    assert any(r.name.endswith(".gemini") for r in caplog.records), \
        [r.name for r in caplog.records]


def test_the_shim_survives_an_argument_that_cannot_be_stringified(caplog):
    """A logging call that raises takes down the code it was reporting on."""
    class Hostile:
        def __str__(self): raise ValueError("no")
    with caplog.at_level(logging.INFO):
        log.make_print("widget")(Hostile())        # must not raise


# ── the request context ──────────────────────────────────────────────────────

def test_every_line_of_a_request_carries_its_id(caplog):
    with caplog.at_level(logging.INFO):
        with log.request("GET", "/api/jobs", "1.2.3.4") as req:
            log.make_print("widget")("working")
    rec = [r for r in caplog.records if r.getMessage() == "working"][0]
    assert rec.rid == req.rid
    assert len(req.rid) == 6


def test_a_line_names_the_user_once_auth_has_resolved_one(caplog):
    with caplog.at_level(logging.INFO):
        with log.request("GET", "/api/jobs"):
            log.make_print("widget")("before auth")
            log.set_user(7)
            log.make_print("widget")("after auth")
    by_msg = {r.getMessage(): r for r in caplog.records}
    assert by_msg["before auth"].uid == "-"
    assert by_msg["after auth"].uid == 7


def test_two_concurrent_requests_do_not_share_an_id():
    """ThreadingMixIn gives every request its own thread, and a thread starts
    with a fresh context - without which two users' lines interleave with the
    same id and the log is worse than none."""
    seen, barrier = {}, threading.Barrier(2)

    def one(tag):
        with log.request("GET", "/x") as req:
            barrier.wait(timeout=5)
            log.set_user(tag)
            seen[tag] = (req.rid, log.current().user_id)

    ts = [threading.Thread(target=one, args=(i,)) for i in (1, 2)]
    [t.start() for t in ts]
    [t.join(timeout=5) for t in ts]

    assert seen[1][0] != seen[2][0]
    assert seen[1][1] == 1 and seen[2][1] == 2


def test_a_background_thread_does_not_inherit_a_finished_request(caplog):
    """Attributing a worker's line to a request that already returned would be
    worse than attributing it to nothing."""
    out = {}
    with log.request("GET", "/api/run-search"):
        t = threading.Thread(target=lambda: out.update(rid=log.current()))
        t.start(); t.join(timeout=5)
    assert out["rid"] is None


# ── the access line ──────────────────────────────────────────────────────────

def test_a_request_ends_with_one_line_saying_what_happened(caplog):
    with caplog.at_level(logging.INFO):
        with log.request("POST", "/api/run-search", "9.9.9.9"):
            log.set_user(3)
            log.set_status(429)
    line = [r for r in caplog.records if "/api/run-search ->" in r.getMessage()]
    assert len(line) == 1
    rec = line[0]
    assert "POST /api/run-search -> 429" in rec.getMessage()
    assert "9.9.9.9" in rec.getMessage()
    # rid and uid are record FIELDS, not message text - so they are on every
    # line the request emitted, not only this one.
    assert rec.uid == 3 and rec.rid


def test_a_server_error_is_logged_at_error_and_a_refusal_at_warning(caplog):
    with caplog.at_level(logging.DEBUG):
        with log.request("GET", "/api/jobs"):
            log.set_status(500)
        with log.request("GET", "/api/jobs"):
            log.set_status(403)
    levels = [r.levelname for r in caplog.records if "/api/jobs ->" in r.getMessage()]
    assert levels == ["ERROR", "WARNING"]


def test_a_successful_static_asset_does_not_get_its_own_line(caplog):
    """A PWA shell load is ~30 files. Logging each one buries the request that
    matters - but a 404 on one of them is still a real event."""
    with caplog.at_level(logging.INFO):
        with log.request("GET", "/app/assets/index.js"):
            log.set_status(200)
        with log.request("GET", "/app/assets/missing.js"):
            log.set_status(404)
    lines = [r.getMessage() for r in caplog.records if "->" in r.getMessage()]
    assert len(lines) == 1 and "missing.js" in lines[0]


def test_ending_a_request_that_never_started_is_not_an_error():
    log.end()          # must not raise


# ── A log that cries wolf on every deploy stops being read ───────────────────

import logging as _logging

import pytest as _pytest

import log as _log


@_pytest.mark.parametrize("msg, level", [
    # The exact lines from Eran's ship run, 2026-09-15. Thirteen of these
    # rendered in red directly above four real failures, because the grader
    # asked `"error" in msg` and the COLUMN is called apply_error.
    ("[db] baseline: added apply_error to jobs",          _logging.INFO),
    ("[db] baseline: added apply_failure_type to jobs",   _logging.INFO),
    ("[db] baseline: added apply_failure_detail to jobs", _logging.INFO),
    ("[db] migration: added read_timeout to jobs",        _logging.INFO),
    ("[db] baseline: added applied_via to jobs",          _logging.INFO),
    ("[worker] claimed job 7",                            _logging.INFO),
    # Still errors, as words.
    ("[worker] job 3 failed",                             _logging.ERROR),
    ("[gemini] request error: 429",                       _logging.ERROR),
    ("[apply] 2 failures in this run",                    _logging.ERROR),
    ("Traceback (most recent call last):",                _logging.ERROR),
    ("[apply] exception while submitting",                _logging.ERROR),
    # Still warnings.
    ("[cv] could not read the CV",                        _logging.WARNING),
    ("[push] endpoint unavailable",                       _logging.WARNING),
    ("[sched] warning: clock skew",                       _logging.WARNING),
])
def test_a_column_name_is_not_an_error(msg, level):
    assert _log._level_for(msg) == level, (
        "%r graded %s, expected %s"
        % (msg, _logging.getLevelName(_log._level_for(msg)), _logging.getLevelName(level)))


def test_the_grader_still_grades():
    """Paired with the test above: if the fix had been "never return ERROR",
    every case above would pass and the tier would be worthless."""
    levels = {_log._level_for(m) for m in
              ("job failed", "added apply_error to jobs", "could not connect")}
    assert levels == {_logging.ERROR, _logging.INFO, _logging.WARNING}, (
        "the grader no longer distinguishes the three tiers: %s" % levels)
