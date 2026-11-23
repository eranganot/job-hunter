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

SYSTEM_CHROME = "/usr/bin/google-chrome" if os.path.exists("/usr/bin/google-chrome") else None
try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None


def _bundled_chromium():
    """Playwright's own Chromium, where `playwright install chromium` has run
    (the Dockerfile, and CI since #174). Unlike branded Chrome 137+ it still
    honours --load-extension, so with it these tests run in CI instead of
    skipping (2026-11-22)."""
    if not sync_playwright:
        return None
    try:
        with sync_playwright() as pw:
            path = pw.chromium.executable_path
        return path if path and os.path.exists(path) else None
    except Exception:  # noqa: BLE001 - no driver, no browser: fall back
        return None


CHROME = _bundled_chromium() or SYSTEM_CHROME
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


def _run(stack, users, tmp_path, job_url, pattern, handler, prepared, mode="B", as_eran=None, cover_letter=None,
         profile=None):
    """Pair the extension, open the job in run B, return (attempt row, job row)."""
    db = stack["db"]
    uid = _user_id(db, "admin@example.test")
    jid = _new_job(db, uid, title="Senior PM", company="Acme B0")
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF-1.4\n% B0 test CV\n%%EOF\n")
    conn = db.get_db()
    conn.execute("UPDATE jobs SET status='approved', url=?, apply_prepared=? WHERE id=?",
                 (job_url, json.dumps(prepared), jid))
    if cover_letter is not None:
        conn.execute("UPDATE jobs SET cover_letter=? WHERE id=?", (cover_letter, jid))
    if not conn.execute("SELECT 1 FROM user_profiles WHERE user_id=?", (uid,)).fetchone():
        conn.execute("INSERT INTO user_profiles (user_id) VALUES (?)", (uid,))
    conn.execute("UPDATE user_profiles SET cv_path=? WHERE user_id=?", (str(cv), uid))
    for k, v in (profile or {}).items():
        conn.execute("UPDATE user_profiles SET %s=? WHERE user_id=?" % k, (v, uid))
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
                # Branded Chrome 137+ ignores --load-extension (the fallback when
                # Playwright's Chromium is not installed); Chromium still loads it.
                pytest.skip("this Chrome does not load unpacked extensions")
            ext_id = sw.url.split("/")[2]
            popup = ctx.new_page()
            popup.goto(f"chrome-extension://{ext_id}/popup.html")
            popup.click("summary")
            popup.fill("#server", server)
            popup.fill("#code", code)
            popup.click("#pairBtn")
            popup.wait_for_selector("#main:not([hidden])")
            popup.check("input[name=mode][value=%s]" % mode)
            popup.wait_for_selector("text=Acme B0")
            popup.click(".job button")
            # A tab the extension opens is navigated by the browser itself, which
            # Playwright does not route: load the job again in that same tab
            # (same tab = same task) so the stand-in page is served.
            host = job_url.split("/")[2]
            tab = None
            for _ in range(160):          # the first load is the real site, unrouted: it can be slow
                tab = next((p for p in ctx.pages if host in p.url), None)
                if tab:
                    break
                ctx.pages[0].wait_for_timeout(250)
            assert tab, "the extension opened no tab for %s; tabs %s" % (job_url, sw.evaluate("chrome.tabs.query({}).then(t => t.map(x => x.url + ' | ' + x.pendingUrl + ' | ' + x.status))"))
            tab.goto(job_url)
            if not prepared and mode == "B" and not as_eran:
                tab.click("button:has-text('Submit for me')", timeout=30000)
            if as_eran:
                as_eran(tab)
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
    import datetime
    assert ' CV %d.pdf"' % datetime.date.today().year in body and "B0 test CV" in body   # never "cv.pdf"
    assert 'filename="cv.pdf"' not in body
    assert 'name="auth"' in body and "Yes" in body
    assert (job["status"], job["applied_via"]) == ("applied", "device")


COMEET_JOB = "https://www.comeet.com/jobs/acme-b0/22.001/senior-pm/AB.123"
# Like comeet.com: the application frame appears only after "APPLY FOR THIS JOB" (the owner, 2026-11-22).
COMEET_PAGE = """<html><body><h2>Senior PM</h2><div id="slot"></div>
<button onclick="document.getElementById('slot').innerHTML = '<iframe src=&quot;https://www.comeet.co/jobs/acme-b0/22.001/apply?token=t&quot; style=&quot;width:600px;height:500px&quot;></iframe>'">APPLY FOR THIS JOB</button></body></html>"""
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


def test_b0_run_a_comeet_submit_eran_presses_is_reported_from_comeets_answer(stack, users, tmp_path):
    """Run A: Eran presses Submit. The click listener can miss it (a page may
    redraw its button), so Comeet's own apply call is what proves the press:
    the 2xx is reported as sent, pressed by the user."""
    def comeet(route, request):
        u = request.url
        if "careers-api" in u:
            return route.fulfill(status=200, content_type="application/json", body='{"status":200}')
        if "comeet.co/" in u:
            return route.fulfill(status=200, content_type="text/html", body=COMEET_FORM)
        route.fulfill(status=200, content_type="text/html", body=COMEET_PAGE)

    def eran_presses(tab):
        for _ in range(60):
            f = next((f for f in tab.frames if "://www.comeet.co/" in f.url), None)
            v = f.evaluate("document.querySelector('#fn') && document.querySelector('#fn').value") if f else None
            if v == "Dana":
                f.evaluate("fetch('/careers-api/2.0/company/x/positions/y/apply', {method: 'POST', body: '{}'})")
                return
            tab.wait_for_timeout(500)
        raise AssertionError("the form was never filled: %s" % [(f.url[:80], f.evaluate("document.body ? document.body.innerText.slice(0,200) : ''")) for f in tab.frames])

    row, job = _run(stack, users, tmp_path, COMEET_JOB.replace("AB.123", "AB.456"),
                    ["https://www.comeet.com/**", "https://www.comeet.co/**"], comeet,
                    [{"label": "First name", "value": "Dana"}], mode="A", as_eran=eran_presses)
    assert (row["outcome"], row["site_status"], row["pressed_by"]) == ("sent", "200", "user"), row
    assert job["applied_via"] == "device"


def test_the_popup_adds_a_job_by_its_link(stack, users, monkeypatch):
    """B0: Eran pastes a Lever link (or is on the page) and it lands in his
    queue, approved - Lever's /apply page and tracking query trimmed off."""
    import device
    monkeypatch.setattr(device, "_page_meta", lambda url: {"title": "Linkco - Head of Product", "company": "",
                                                           "description": ""})
    code = json.loads(users["admin"].get("/api/admin/device-code")[2])["code"]
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
            try:
                sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=10000)
            except Exception:
                pytest.skip("this Chrome does not load unpacked extensions")
            popup = ctx.new_page()
            popup.goto(f"chrome-extension://{sw.url.split('/')[2]}/popup.html")
            popup.click("summary")
            popup.fill("#server", "http://127.0.0.1:%d" % stack["port"])
            popup.fill("#code", code)
            popup.click("#pairBtn")
            popup.wait_for_selector("#main:not([hidden])")
            popup.fill("#link", "https://jobs.lever.co/linkco/9f8e-77/apply?lever-source=LinkedIn")
            popup.click("#addLink")
            popup.wait_for_selector("text=Added: Linkco", timeout=15000)
            popup.wait_for_selector("text=Head of Product")
        finally:
            ctx.close()
    conn = stack["db"].get_db()
    row = conn.execute("SELECT status, company, title FROM jobs WHERE url='https://jobs.lever.co/linkco/9f8e-77'").fetchone()
    conn.close()
    assert dict(row) == {"status": "approved", "company": "Linkco", "title": "Head of Product"}


LETTER = ("Dear Hiring Team,\n\nI am applying for the Senior PM role at Acme because it sits where I have "
          "spent ten years: personalization products used by millions.\n\nAt DAZN I lead the team.\n\n"
          "Best regards,\nDana Levi")
COMEET_FORM_CL = """<html><body><form onsubmit="return false">
<label for="fn">First name</label><input id="fn" name="firstName" type="text">
<label for="cvf">Resume</label><input id="cvf" name="cv" type="file">
<label for="coverLetter">Attach Cover Letter</label><input id="coverLetter" name="coverLetter" type="file">
<label for="inputNote">Personal note</label><textarea id="inputNote" name="comment"></textarea>
<button type="button" onclick="fetch('/careers-api/2.0/company/x/positions/y/apply', {method: 'POST', body: JSON.stringify({
  cv: (document.getElementById('cvf').files[0] || {}).name, cl: (document.getElementById('coverLetter').files[0] || {}).name,
  cltype: (document.getElementById('coverLetter').files[0] || {}).type, note: document.getElementById('inputNote').value})})">SUBMIT APPLICATION</button>
</form></body></html>"""


def test_b0_comeet_gets_the_cover_letter_as_a_pdf_and_its_opening_as_the_note(stack, users, tmp_path):
    """The owner, 2026-11-22: the letter goes up as "<Name> Cover Letter <year>.pdf"
    in Comeet's cover-letter field, and the Personal note gets its opening -
    never the "how did you hear about us" sentence it used to get."""
    sent = []

    def comeet(route, request):
        u = request.url
        if "careers-api" in u:
            sent.append(json.loads(request.post_data or "{}"))
            return route.fulfill(status=200, content_type="application/json", body='{"status":200}')
        if "comeet.co/" in u:
            return route.fulfill(status=200, content_type="text/html", body=COMEET_FORM_CL)
        route.fulfill(status=200, content_type="text/html", body=COMEET_PAGE)

    row, job = _run(stack, users, tmp_path, COMEET_JOB.replace("AB.123", "AB.789"),
                    ["https://www.comeet.com/**", "https://www.comeet.co/**"], comeet,
                    [{"label": "First name", "value": "Dana"}], cover_letter=LETTER)
    assert (row["outcome"], row["pressed_by"]) == ("sent", "extension"), row
    import datetime
    got = sent[0]
    assert got["cl"].endswith(" Cover Letter %d.pdf" % datetime.date.today().year) and got["cltype"] == "application/pdf", got
    assert got["cv"].endswith(" CV %d.pdf" % datetime.date.today().year), got   # the CV stays in the CV field
    assert got["note"].startswith("I am applying for the Senior PM role at Acme"), got
    assert "Dear" not in got["note"] and "Job Hunter" not in got["note"], got


# SmartRecruiters' real form (inspected 2026-11-23 on Wix's Base44 posting): every
# input inside a web component's shadow root, an "Easy Apply" dropzone at the top
# and the "Resume *" one below, a phone number next to a separate +972 picker.
SR_FORM = """<html><body>
<script>
customElements.define('spl-input', class extends HTMLElement {
  connectedCallback() {
    const r = this.attachShadow({mode: 'open'}), id = this.getAttribute('fid'), lab = this.getAttribute('label');
    r.innerHTML = (lab ? '<label for="' + id + '">' + lab + '*</label>' : '') +
      '<input id="' + id + '" type="' + (this.getAttribute('type') || 'text') + '"' +
      (lab ? '' : ' aria-label="Phone number"') + '>';
  }
});
customElements.define('spl-dropzone', class extends HTMLElement {
  connectedCallback() { this.attachShadow({mode: 'open'}).innerHTML = '<label for="f' + this.id + '">Choose a file or drop it here</label><input type="file" id="f' + this.id + '">'; }
});
customElements.define('spl-textarea', class extends HTMLElement {
  connectedCallback() { this.attachShadow({mode: 'open'}).innerHTML = '<label for="m">Let the company know about your interest</label><textarea id="m"></textarea>'; }
});
</script>
<section><h2>Easy Apply</h2><p>Choose an option to autocomplete your application.</p><spl-dropzone id="2"></spl-dropzone></section>
<section><h2>Personal information</h2>
<spl-input fid="first-name-input" label="First name"></spl-input>
<spl-input fid="last-name-input" label="Last name"></spl-input>
<spl-input fid="email-input" label="Email" type="email"></spl-input>
<spl-input fid="confirm-email-input" label="Confirm your email" type="email"></spl-input>
<span>+972</span><spl-input fid="phone" type="tel"></spl-input></section>
<section><h3>Resume *</h3><spl-dropzone id="1"></spl-dropzone></section>
<section><h3>Message to the Hiring Team</h3><spl-textarea></spl-textarea></section>
<button type="button" onclick="
  const v = id => document.querySelector('[fid=' + id + ']').shadowRoot.querySelector('input').value;
  const f = id => (document.getElementById(id).shadowRoot.querySelector('input').files[0] || {}).name || '';
  fetch('/sr-submit', {method: 'POST', body: JSON.stringify({first: v('first-name-input'), last: v('last-name-input'),
    email: v('email-input'), confirm: v('confirm-email-input'), phone: v('phone'), resume: f('1'), easy: f('2'),
    note: document.querySelector('spl-textarea').shadowRoot.querySelector('textarea').value})})
  .then(() => { document.body.innerHTML = '<h1>Thank you for applying</h1>'; });">Submit</button>
</body></html>"""


def test_b0_smartrecruiters_shadow_form_gets_contact_details_cv_and_note(stack, users, tmp_path):
    """The owner, 2026-11-23: on ServiceNow, Check Point and Wix the extension
    "doesn't do anything" - SmartRecruiters' inputs are all in shadow roots, so
    the extension saw no form and stopped without a word. And jobs added by link
    have no prepared answers, so even where it saw the form only the CV went in."""
    sent = []

    def sr(route, request):
        if request.method == "POST" and "sr-submit" in request.url:
            sent.append(json.loads(request.post_data or "{}"))
            return route.fulfill(status=200, body="{}")
        route.fulfill(status=200, content_type="text/html", body=SR_FORM)

    row, job = _run(stack, users, tmp_path, "https://jobs.smartrecruiters.com/oneclick-ui/company/Acme/publication/abc-123",
                    "https://jobs.smartrecruiters.com/**", sr, [], cover_letter=LETTER,
                    profile={"phone": "054-123-4567", "linkedin_url": "https://www.linkedin.com/in/dana"})
    assert row["outcome"] == "sent", row
    got = sent[0]
    import datetime
    assert got["first"] == "admin" and got["email"] == "admin@example.test" == got["confirm"], got   # one-word account name: no last name
    assert got["phone"] == "0541234567" or got["phone"].replace("-", "").replace(" ", "") == "0541234567", got
    assert got["resume"].endswith(" CV %d.pdf" % datetime.date.today().year) and got["easy"] == "", got
    assert got["note"].startswith("Dear Hiring Team"), got        # no cover-letter file field: the whole letter


# SmartRecruiters in two steps, as the owner saw it on Wix and Check Point
# (2026-11-23): City is a suggestion list; "Next" opens "Preliminary questions"
# (a searchable dropdown, a Yes/No radio, the privacy declaration) without a page load.
SR_STEPS = """<html><body>
<script>
customElements.define('spl-input', class extends HTMLElement {
  connectedCallback() {
    const r = this.attachShadow({mode: 'open'}), id = this.getAttribute('fid'), lab = this.getAttribute('label');
    r.innerHTML = '<label for="' + id + '">' + lab + '*</label><input id="' + id + '" type="' + (this.getAttribute('type') || 'text') + '">';
  }
});
customElements.define('spl-dropdown', class extends HTMLElement {
  connectedCallback() {
    const r = this.attachShadow({mode: 'open'}), opts = this.getAttribute('options').split('|');
    r.innerHTML = '<label for="d">' + this.getAttribute('label') + ' *</label><input id="d" role="combobox"><div id="l"></div>';
    const i = r.getElementById('d'), l = r.getElementById('l');
    const show = () => { l.innerHTML = ''; for (const o of opts) if (o.toLowerCase().includes(i.value.toLowerCase())) {
      const e = document.createElement('spl-select-option'); e.textContent = o; e.setAttribute('role', 'option');
      e.onclick = () => { i.value = o; this.value = o; l.innerHTML = ''; }; l.appendChild(e); } };
    i.addEventListener('input', show); i.addEventListener('focus', show);
  }
});
customElements.define('spl-radio-group', class extends HTMLElement {
  connectedCallback() {
    this.attachShadow({mode: 'open'}).innerHTML = '<fieldset><legend>' + this.getAttribute('label') + ' *</legend>' +
      '<input type="radio" name="e" id="y" required><label for="y">Yes</label><input type="radio" name="e" id="n"><label for="n">No</label></fieldset>';
  }
});
customElements.define('spl-checkbox', class extends HTMLElement {
  connectedCallback() { this.attachShadow({mode: 'open'}).innerHTML = '<input type="checkbox" id="c" required><label for="c">' + this.getAttribute('label') + '*</label>'; }
});
function next() {
  const v = id => document.querySelector('[fid=' + id + ']').shadowRoot.querySelector('input').value;
  window.step1 = {first: v('first-name-input'), city: document.querySelector('#city').value};
  document.getElementById('s1').remove();
  document.getElementById('s2').innerHTML = '<h2>Preliminary questions</h2>' +
    '<spl-dropdown id="years" label="How many years of experience do you have as a Product Manager in tech product companies?" options="0-2|3-5|6-9|10+"></spl-dropdown>' +
    '<spl-radio-group id="elig" label="Are you eligible to work in Israel?"></spl-radio-group>' +
    '<spl-checkbox id="priv" label="You declare that you have read and understand the privacy notice of Wix."></spl-checkbox>' +
    '<button type="button" id="go">Submit</button>';
  document.getElementById('go').onclick = () => {
    const sr = id => document.getElementById(id).shadowRoot;
    fetch('/sr-submit', {method: 'POST', body: JSON.stringify(Object.assign({}, window.step1, {
      years: sr('years').getElementById('d').value, eligible: sr('elig').getElementById('y').checked,
      privacy: sr('priv').getElementById('c').checked}))})
    .then(() => { document.body.innerHTML = '<h1>Thank you for applying</h1>'; });
  };
}
</script>
<div id="s1"><h2>Personal information</h2>
<spl-input fid="first-name-input" label="First name"></spl-input>
<spl-dropdown id="city" label="City" options="Tel Aviv, Israel|Tel Mond, Israel|Haifa, Israel"></spl-dropdown>
<button type="submit" onclick="next()">Next</button></div>
<div id="s2"></div>
</body></html>"""


def test_b0_smartrecruiters_second_step_is_answered_and_city_picked(stack, users, tmp_path, monkeypatch):
    """The owner, 2026-11-23, run A and run B: "City is still missing", "the
    second page is completely empty"."""
    import app as _app
    import apply_engine as _ae
    import device as _dev
    monkeypatch.setattr(_app, "GEMINI_KEY", "test")

    def fake(prompt, **kw):
        if "ON BEHALF of the applicant" not in prompt:
            return {}
        qs = json.loads(prompt.split("Questions (JSON array): ", 1)[1].split("\n\nReturn ONLY", 1)[0])
        out = []
        for q in qs:
            if "years" in q["label"]:
                out.append({"selector": q["selector"], "value": "10+", "confidence": "high"})
            elif "eligible" in q["label"]:
                out.append({"selector": q["selector"], "value": "Yes", "confidence": "high"})
        return out
    monkeypatch.setattr(_ae, "_claude_json", fake)
    monkeypatch.setattr(_ae, "extract_applicant_data", lambda text, email, fallback_name="": {"email": email})
    conn = stack["db"].get_db()
    _uid = conn.execute("SELECT id FROM users WHERE email='admin@example.test'").fetchone()[0]
    if not conn.execute("SELECT 1 FROM user_profiles WHERE user_id=?", (_uid,)).fetchone():
        conn.execute("INSERT INTO user_profiles (user_id) VALUES (?)", (_uid,))
    conn.execute("UPDATE user_profiles SET locations=? WHERE user_id=?", ('["Tel Aviv, Israel", "San Diego"]', _uid))
    conn.commit()
    conn.close()
    sent = []

    def sr(route, request):
        if request.method == "POST" and "sr-submit" in request.url:
            sent.append(json.loads(request.post_data or "{}"))
            return route.fulfill(status=200, body="{}")
        route.fulfill(status=200, content_type="text/html", body=SR_STEPS)

    def eran(tab):                                     # run A: Eran presses Next, then Submit
        tab.wait_for_function("document.querySelector('#city') && document.querySelector('#city').value === 'Tel Aviv, Israel'", timeout=30000)
        tab.click("button:has-text('Next')")
        tab.wait_for_function("""(() => { const p = document.querySelector('#priv'); return p && p.shadowRoot.getElementById('c').checked
                                  && document.querySelector('#years').shadowRoot.getElementById('d').value === '10+'; })()""", timeout=40000)
        tab.wait_for_function("document.querySelector('#elig').shadowRoot.getElementById('y').checked", timeout=20000)
        tab.click("#go")

    row, job = _run(stack, users, tmp_path, "https://jobs.smartrecruiters.com/oneclick-ui/company/Acme/publication/step-2",
                    "https://jobs.smartrecruiters.com/**", sr, [], mode="A", as_eran=eran)
    assert row["outcome"] == "sent", row
    got = sent[0]
    assert got["city"] == "Tel Aviv, Israel" and got["years"] == "10+" and got["eligible"] is True and got["privacy"] is True, got
