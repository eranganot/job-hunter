"""Sentry 2026-11-08: web-search JSON slicing and LinkedIn 429s."""
import io
import urllib.error

import pytest

import app

JOB = '{"job_title":"CPO","company":"X","url":"https://a","description":"%s"}'


@pytest.mark.parametrize("text", [
    "[" + JOB % "Lead product [Hybrid] in TLV." + "]",                 # bracket inside the last item
    "```json\n[" + JOB % "d" + "]\n```\nNote: see [source link].",     # trailing bracketed note
    "Here you go: [" + JOB % "d" + "]",
])
def test_array_survives_brackets_that_broke_rfind(text):
    got = app._json_array_from_text(text)
    assert len(got) == 1 and got[0]["url"] == "https://a"


def test_no_array_is_empty_not_an_error():
    assert app._json_array_from_text("No postings found [none].") == []
    assert app._json_array_from_text("") == []


def _err(code):
    return urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(b""))


class _Resp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_linkedin_429_is_retried_then_succeeds(monkeypatch):
    answers = [_err(429), _err(429), _Resp(b"<li class='base-card'>ok</li>")]
    def fake(req, timeout):
        a = answers.pop(0)
        if isinstance(a, Exception): raise a
        return a
    monkeypatch.setattr(app.urllib.request, "urlopen", fake)
    waits = []
    assert "base-card" in app._linkedin_fetch("https://x", _sleep=waits.append)
    assert waits == [2, 5]


def test_linkedin_gives_up_after_retries_and_other_errors_are_not_retried(monkeypatch):
    calls = []
    def always(code):
        def fake(req, timeout):
            calls.append(code); raise _err(code)
        return fake
    monkeypatch.setattr(app.urllib.request, "urlopen", always(429))
    with pytest.raises(urllib.error.HTTPError):
        app._linkedin_fetch("https://x", _sleep=lambda s: None)
    assert len(calls) == 3
    calls.clear()
    monkeypatch.setattr(app.urllib.request, "urlopen", always(403))
    with pytest.raises(urllib.error.HTTPError):
        app._linkedin_fetch("https://x", _sleep=lambda s: None)
    assert len(calls) == 1
