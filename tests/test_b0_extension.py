"""
tests/test_b0_extension.py - the B0 Chrome extension end to end, in a real
Chrome: pair with a code, open an approved Lever-shaped job, fill the prepared
answers and the CV, press Submit (run B), follow the site to its thank-you page
and report "sent" - and the server moves the job to applied, sent by the device.

The job pages are stand-ins served by the test (same host and paths as Lever);
the app is the local test server. The extension is the one in
tools/b0-extension, with the local server added to its host permissions.
"""
import json
import os
import shutil
import tempfile
import time

import pytest

from tests.test_routes import stack, users, _user_id, _new_job  # noqa: F401

CHROME = "/usr/bin/google-chrome" if os.path.exists("/usr/bin/google-chrome") else None
try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None
pytestmark = pytest.mark.skipif(not (CHROME and sync_playwright), reason="needs Chrome + Playwright")

EXT = os.path.join(os.path.dirname(__file__), "..", "tools", "b0-extension")
JOB = "https://jobs.lever.co/acme-b0/1111"

POSTING = "<html><body><h2>Senior PM</h2></body></html>"
APPLY = """<html><body><form method="post" action="" enctype="multipart/form-data">
<ul>
<li class="application-question"><div class="application-label">Full name</div><input name="name" type="text"></li>
<li class="application-question"><div class="application-label">Email</div><input name="email" type="email"></li>
<li class="application-question"><div class="application-label">Resume/CV</div><input name="resume" type="file"></li>
<li class="application-question"><div class="application-label">Are you authorized to work in Israel?</div>
  <label><input type="radio" name="auth" value="Yes"> Yes</label><label><input type="radio" name="auth" value="No"> No</label></li>
</ul><button id="btn-submit" type="submit">Submit application</button></form></body></html>"""
THANKS = "<html><body><h3>Thank you for applying</h3></body></html>"


def _run(stack, users, tmp_path, job_url, pattern, handler, prepared):
    """Pair the extension, open the job in run B, return (attempt row, job row)."""
    db = stack["db"]
    uid = _user_id(db, "admin@example.test")
    jid = _new_job(db, uid, title="Senior PM", company="Acme B0")
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF-1.4\n% B0 test CV\n%%EOF\n")
    conn = db.get_db()
    conn.execute("UPDATE jobs SET status='approved', url=?, apply_prepared=? WHERE id=?",
                 (job_url, json.dumps(prepared), jid))
    if not conn.execute("SELECT 1 FROM user_profiles WHERE user_id=?", (uid,)).fetchone():
        conn.execute("INSERT INTO user_profiles (user_id) VALUES (?)", (uid,))
    conn.execute("UPDATE user_profiles SET cv_path=? WHERE user_id=?", (str(cv), uid))
    conn.commit()
    conn.close()
    code = json.loads(users["admin"].get("/api/admin/device-code")[2])["code"]
    server = "http://127.0.0.1:%d" % stack["port"]

    ext = tempfile.mkdtemp()
    shutil.copytree(EXT, ext, dirs_exist_ok=True)
    m = json.load(open(os.path.join(ext, "manifest.json")))
    m["host_permissions"].append("http://127.0.0.1/*")
    json.dump(m, open(os.path.join(ext, "manifest.json"), "w"))

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            tempfile.mkdtemp(), executable_path=CHROME, headless=False,
            args=["--headless=new", "--no-sandbox", f"--disable-extensions-except={ext}", f"--load-extension={ext}"])
        try:
            for pat in ([pattern] if isinstance(pattern, str) else pattern):
                ctx.route(pat, handler)
            try:
                sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=10000)
            except Exception:
                # Branded Chrome 137+ ignores --load-extension (CI's google-chrome);
                # Chrome for Testing and Chromium still load it.
                pytest.skip("this Chrome does not load unpacked extensions")
            ext_id = sw.url.split("/")[2]
            popup = ctx.new_page()
            popup.goto(f"chrome-extension://{ext_id}/popup.html")
            popup.click("summary")
            popup.fill("#server", server)
            popup.fill("#code", code)
            popup.click("#pairBtn")
            popup.wait_for_selector("#main:not([hidden])")
            popup.check("input[name=mode][value=B]")
            popup.wait_for_selector("text=Acme B0")
            popup.click(".job button")
            # A tab the extension opens is navigated by the browser itself, which
            # Playwright does not route: load the job again in that same tab
            # (same tab = same task) so the stand-in page is served.
            host = job_url.split("/")[2]
            tab = None
            for _ in range(40):
                tab = next((p for p in ctx.pages if host in p.url), None)
                if tab:
                    break
                ctx.pages[0].wait_for_timeout(250)
            assert tab, "the extension opened no tab for %s" % job_url
            tab.goto(job_url)
            if not prepared:
                tab.click("button:has-text('Submit for me')", timeout=30000)
            deadline = time.time() + 45
            row = None
            while time.time() < deadline and not row:
                c = db.get_db()
                row = c.execute("SELECT outcome, pressed_by, site_status, detail FROM device_attempts WHERE job_id=?",
                                (jid,)).fetchone()
                c.close()
                ctx.pages[0].wait_for_timeout(500)   # keeps Playwright serving the routed pages (time.sleep would not)
            if not row:
                raise AssertionError("no attempt reported; pages %s" % [(p.url, [f.url for f in p.frames]) for p in ctx.pages])
        finally:
            ctx.close()
    conn = db.get_db()
    job = conn.execute("SELECT status, applied_via FROM jobs WHERE id=?", (jid,)).fetchone()
    conn.close()
    return dict(row), dict(job)


def test_b0_extension_fills_submits_and_reports_sent(stack, users, tmp_path):
    posts = []

    def lever(route, request):
        u = request.url
        if request.method == "POST":
            posts.append(request.post_data_buffer or b"")
            return route.fulfill(status=302, headers={"Location": JOB + "/thanks"})
        body = THANKS if u.endswith("/thanks") else APPLY if u.endswith("/apply") else POSTING
        route.fulfill(status=200, content_type="text/html", body=body)

    prepared = [{"label": "Full name", "value": "Dana Levi"}, {"label": "Email", "value": "dana@example.test"},
                {"label": "Are you authorized to work in Israel?", "value": "Yes"}]
    row, job = _run(stack, users, tmp_path, JOB, "https://jobs.lever.co/**", lever, prepared)
    assert (row["outcome"], row["pressed_by"]) == ("sent", "extension"), row
    body = posts[0].decode("latin-1")
    assert "Dana Levi" in body and "dana@example.test" in body
    assert 'filename="cv.pdf"' in body and "B0 test CV" in body
    assert 'name="auth"' in body and "Yes" in body
    assert (job["status"], job["applied_via"]) == ("applied", "device")


COMEET_JOB = "https://www.comeet.com/jobs/acme-b0/22.001/senior-pm/AB.123"
COMEET_PAGE = """<html><body><h2>Senior PM</h2>
<iframe src="https://www.comeet.co/jobs/acme-b0/22.001/apply?token=t" style="width:600px;height:500px"></iframe></body></html>"""
COMEET_FORM = """<html><body><form onsubmit="return false">
<label for="fn">First name</label><input id="fn" name="firstName" type="text">
<label for="cvf">Resume</label><input id="cvf" name="cv" type="file">
<button type="button" onclick="fetch('/careers-api/2.0/company/x/positions/y/apply', {method: 'POST', body: '{}'})">SUBMIT APPLICATION</button>
</form></body></html>"""


def test_b0_extension_reads_comeets_423_inside_its_form_frame(stack, users, tmp_path):
    """Comeet's form is in a comeet.co frame and its verdict is the HTTP code of
    its apply call: 423 = its bot check refused the browser (B0's question)."""
    def comeet(route, request):
        u = request.url
        if "careers-api" in u:
            return route.fulfill(status=423, content_type="application/json", body='{"status":423,"message":""}')
        if "comeet.co/" in u:
            return route.fulfill(status=200, content_type="text/html", body=COMEET_FORM)
        route.fulfill(status=200, content_type="text/html", body=COMEET_PAGE)

    row, job = _run(stack, users, tmp_path, COMEET_JOB, ["https://www.comeet.com/**", "https://www.comeet.co/**"], comeet,
                    [{"label": "First name", "value": "Dana"}])
    assert (row["outcome"], row["site_status"], row["pressed_by"]) == ("blocked", "423", "extension"), row
    assert job["status"] == "approved"            # a refusal leaves the job where it was


def test_b0_run_b_without_prepared_answers_submits_when_eran_asks(stack, users, tmp_path):
    """SmartRecruiters' check stops the server before its form, so nothing is
    prepared: Eran fills the form himself and the extension presses Submit
    ("Submit for me") - still run B."""
    def lever(route, request):
        u = request.url
        if request.method == "POST":
            return route.fulfill(status=302, headers={"Location": JOB + "/thanks"})
        body = THANKS if u.endswith("/thanks") else APPLY if u.endswith("/apply") else POSTING
        route.fulfill(status=200, content_type="text/html", body=body)

    row, job = _run(stack, users, tmp_path, JOB.replace("1111", "2222"), "https://jobs.lever.co/**", lever, [])
    assert (row["outcome"], row["pressed_by"]) == ("sent", "extension"), row
