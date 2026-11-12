"""The generic apply path (2026-11-12). Production logs, 2026-11-11: the model was
sent page.content()[:8000], which held ZERO form fields on every page measured
(the first field sits 26k-250k characters in), so it invented ~40 selectors that
each waited 20 s - 13 minutes a job, far past the 150 s deadline - and the
abandoned run kept filling for 7+ minutes after it. Now the model gets the real
fields, by number; a page with no form says so at once; and the engine stops
itself, unsent, at its deadline. Runs the real engine in a real browser against
a local site; skipped where no browser is installed."""
import http.server
import json
import os
import threading
import time

import pytest

import apply_engine as ae

CHROME = next((p for p in ("/usr/bin/google-chrome", "/usr/bin/chromium") if os.path.exists(p)), None)
browser_only = pytest.mark.skipif(not (CHROME and ae.PLAYWRIGHT_AVAILABLE), reason="no browser here")

# 40 KB of script before the form: the shape of every page measured in production.
PADDING = "<script>/*" + ("x" * 40_000) + "*/</script>"
FORM_PAGE = """<!doctype html><html><head>%s</head><body><h1>Senior PM</h1>
<form action="/submit" method="post">
<label for="fn">First name</label><input id="fn" name="a1" required>
<label for="em">Email address</label><input id="em" name="a2" type="email" required>
<input type="hidden" name="csrf" value="t"><textarea name="g-recaptcha-response"></textarea>
<label for="cv">Resume</label><input id="cv" name="a3" type="file">
<button type="submit">Submit application</button></form></body></html>""" % PADDING
NO_FORM_PAGE = "<!doctype html><html><body><h1>About us</h1><p>A news article.</p></body></html>"
POSTS = []


class _H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = (NO_FORM_PAGE if self.path.startswith("/article") else FORM_PAGE).encode()
        self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        POSTS.append(self.rfile.read(n).decode("utf-8", "replace"))
        self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
        self.wfile.write(b"<html><body>Thank you for applying!</body></html>")


@pytest.fixture
def site(monkeypatch):
    POSTS.clear()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    real_launch = ae.sync_playwright

    class _PW:   # launch the installed browser
        def __enter__(self):
            self.cm = real_launch(); pw = self.cm.__enter__()
            orig = pw.chromium.launch
            pw.chromium.launch = lambda **kw: orig(**{**kw, "executable_path": CHROME})
            return pw

        def __exit__(self, *a):
            return self.cm.__exit__(*a)
    monkeypatch.setattr(ae, "sync_playwright", lambda: _PW())
    yield "http://127.0.0.1:%d" % srv.server_address[1]
    srv.shutdown()


def _model(monkeypatch, seen):
    def fake(prompt, **kw):
        if "The form's fields, numbered:" in prompt:
            fields = json.loads(prompt.split("The form's fields, numbered:\n", 1)[1].split("\n\nReturn a JSON ARRAY", 1)[0])
            seen.extend(f for _, f in fields)
            out = []
            for n, f in fields:
                if "first" in f["label"].lower():
                    out.append({"field": n, "action": "fill", "value": "Dana"})
                elif "email" in f["label"].lower():
                    out.append({"field": n, "action": "fill", "value": "dana@example.test"})
            return out
        if "successfully received" in prompt:
            return {"confirmed": "thank you" in prompt.lower(), "message": "ok"}
        return {}
    monkeypatch.setattr(ae, "_claude_json", fake)


APPLICANT = {"full_name": "Dana Test", "email": "dana@example.test"}


@browser_only
def test_the_model_sees_the_real_fields_and_the_form_is_sent(site, monkeypatch):
    seen = []
    _model(monkeypatch, seen)
    t0 = time.time()
    res = ae.submit_application(site + "/careers/pm", "Senior PM", "Acme", {**APPLICANT, "_deadline_s": 60},
                                None, force=True)
    assert [f["label"] for f in seen] == ["First name", "Email address", "Resume"]   # hidden + captcha skipped
    assert res["status"] == "confirmed", res
    assert len(POSTS) == 1 and "a1=Dana" in POSTS[0] and "dana%40example.test" in POSTS[0]
    assert time.time() - t0 < 40


@browser_only
def test_a_page_with_no_form_is_manual_at_once(site, monkeypatch):
    _model(monkeypatch, [])
    t0 = time.time()
    res = ae.submit_application(site + "/article/1", "Senior PM", "Acme", {**APPLICANT, "_deadline_s": 60},
                                None, force=True)
    assert res["status"] == "manual_required" and "No application form" in res["error"]
    assert POSTS == [] and time.time() - t0 < 30


@browser_only
def test_the_engine_stops_unsent_at_its_deadline(site, monkeypatch):
    _model(monkeypatch, [])
    # The deadline has passed by the time the form is filled.
    res = ae.submit_application(site + "/careers/pm", "Senior PM", "Acme", {**APPLICANT, "_deadline_s": 0.01},
                                None, force=True)
    assert res["status"] == "failed" and res["apply_failure_type"] == "timeout"
    assert "nothing was sent" in res["apply_failure_detail"]
    assert POSTS == []


def test_ashby_links_open_the_application_form():
    f = ae._ashby_application_url
    job = "https://jobs.ashbyhq.com/pointfive/a24e482d-6e8c-4bf2-8f81-cefc63dccbcf"
    assert f(job) == job + "/application"
    assert f(job + "/application") == job + "/application"
    assert f(job + "?utm_source=x") == job + "/application?utm_source=x"
    assert f("https://jobs.ashbyhq.com/pointfive") == "https://jobs.ashbyhq.com/pointfive"   # board, not a job
    assert f("https://boards.greenhouse.io/x/jobs/1") == "https://boards.greenhouse.io/x/jobs/1"


def test_the_app_hands_the_engine_its_deadline():
    src = open(os.path.join(os.path.dirname(__file__), "..", "app.py"), encoding="utf-8").read()
    body = src[src.index("def _submit_application_guarded"):src.index("def _supervised_live_apply")]
    assert '"_deadline_s": max(30, _APPLY_DEADLINE_S - 15)' in body
