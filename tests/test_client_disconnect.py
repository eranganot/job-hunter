"""
A client that hangs up mid-reply is not a server error (2026-11-22).

Production, 10:41:38: one closed connection on /api/jobs became three Sentry
issues - BrokenPipeError reported by the catch-all (JOB-HUNTER-W), the
catch-all's own ERROR line (X), and the access line "-> 500" at ERROR (E),
because the catch-all then tried to write a 500 to the dead socket.

These drive the real Handler: a disconnect is logged as 499 and reported to
nobody; a genuine exception still reports and still answers 500.
"""
import http.client
import logging
import time

import pytest

from tests.test_routes import stack  # noqa: F401


def _access_lines(caplog, path):
    return [r for r in caplog.records
            if r.name.endswith("access") and path in r.getMessage()]


def _wait_for_access_line(caplog, path, timeout=5.0):
    # The server thread writes the access line as the request context closes,
    # which can land a moment after the client has seen the connection drop.
    end = time.time() + timeout
    while time.time() < end:
        lines = _access_lines(caplog, path)
        if lines:
            return lines[-1]
        time.sleep(0.05)
    raise AssertionError("no access line for %s" % path)


@pytest.fixture
def captured(monkeypatch):
    import errors
    sent = []
    monkeypatch.setattr(errors, "capture", lambda exc, **kw: sent.append(exc) or True)
    return sent


def _request(port, method, path):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        c.request(method, path, body=b"{}" if method == "POST" else None,
                  headers={"Content-Type": "application/json"})
        r = c.getresponse()
        return r.status, r.read()
    except (http.client.RemoteDisconnected, ConnectionError):
        return None, b""
    finally:
        c.close()


@pytest.mark.parametrize("method,inner", [("GET", "_do_GET_inner"), ("POST", "_do_POST_inner")])
@pytest.mark.parametrize("gone", [BrokenPipeError, ConnectionResetError, ConnectionAbortedError])
def test_a_client_that_hung_up_is_logged_499_and_reported_to_nobody(
        stack, monkeypatch, caplog, captured, method, inner, gone):
    def hang_up(self):
        raise gone(32, "Broken pipe")
    monkeypatch.setattr(stack["app"].Handler, inner, hang_up)
    path = "/api/x-disconnect-%s-%s" % (method.lower(), gone.__name__)
    caplog.set_level(logging.DEBUG)

    status, _ = _request(stack["port"], method, path)

    line = _wait_for_access_line(caplog, path)
    assert "-> 499" in line.getMessage(), line.getMessage()
    assert line.levelno == logging.WARNING          # a breadcrumb in Sentry, not an event
    assert captured == []                           # nothing reported
    assert status is None                           # no 500 written to the dead socket
    assert not [r for r in caplog.records
                if "Unhandled exception" in r.getMessage() and path in r.getMessage()]


@pytest.mark.parametrize("method,inner", [("GET", "_do_GET_inner"), ("POST", "_do_POST_inner")])
def test_a_real_exception_still_reports_and_answers_500(
        stack, monkeypatch, caplog, captured, method, inner):
    def boom(self):
        raise RuntimeError("a real bug")
    monkeypatch.setattr(stack["app"].Handler, inner, boom)
    path = "/api/x-real-bug-%s" % method.lower()
    caplog.set_level(logging.DEBUG)

    status, body = _request(stack["port"], method, path)

    assert status == 500 and b"a real bug" not in body and b"Something went wrong" in body   # text stays in the log, not the reply (2026-11-25)
    assert len(captured) == 1 and isinstance(captured[0], RuntimeError)
    line = _wait_for_access_line(caplog, path)
    assert "-> 500" in line.getMessage() and line.levelno == logging.ERROR
