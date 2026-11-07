"""Greenhouse's current form (rehearsal against a live posting, 2026-11-07):
choice questions are JavaScript comboboxes, not <select>, and LinkedIn is a
question_<id> box found only by its label. Runs in a real browser; skipped
where none is installed."""
import os
import pytest

import apply_engine as ae

CHROME = next((p for p in ("/usr/bin/google-chrome", "/usr/bin/chromium") if os.path.exists(p)), None)
pytestmark = pytest.mark.skipif(not (CHROME and ae.PLAYWRIGHT_AVAILABLE), reason="no browser here")

PAGE = """<!doctype html><html><body><form>
<label for="question_1">LinkedIn Profile</label><input id="question_1" type="text">
<label for="country">Country*</label>
<input id="country" role="combobox" aria-required="true" aria-controls="country-list" autocomplete="off">
<div id="country-list" role="listbox" hidden>
  <div role="option">United States +1</div><div role="option">Israel +972</div></div>
<label for="question_2">Will you require visa sponsorship?*</label>
<input id="question_2" role="combobox" aria-required="true" aria-controls="q2-list" autocomplete="off">
<div id="q2-list" role="listbox" hidden><div role="option">Yes</div><div role="option">No</div></div>
<script>
for (const inp of document.querySelectorAll('[role=combobox]')) {
  const box = document.getElementById(inp.getAttribute('aria-controls'));
  inp.addEventListener('click', () => { box.hidden = false; });
  inp.addEventListener('keydown', e => { if (e.key === 'Escape') box.hidden = true; });
  for (const o of box.querySelectorAll('[role=option]'))
    o.addEventListener('click', () => { inp.value = o.innerText; inp.dataset.chosen = o.innerText; box.hidden = true; });
}
</script></form></body></html>"""


@pytest.fixture
def page():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=CHROME, headless=True, args=["--no-sandbox"])
        p = b.new_page()
        p.set_content(PAGE)
        yield p
        b.close()


def test_each_combobox_reports_its_own_options(page):
    fields = {f["selector"]: f for f in ae._collect_unfilled_required(page)}
    assert fields["#question_2"]["type"] == "combobox"
    assert fields["#question_2"]["options"] == ["Yes", "No"]          # not the country list
    assert fields["#country"]["options"] == ["United States +1", "Israel +972"]


def test_an_answer_picks_the_option_not_typed_text(page):
    assert ae._choose_combobox(page, "#question_2", "No")
    assert page.get_attribute("#question_2", "data-chosen") == "No"
    assert not ae._choose_combobox(page, "#country", "Narnia")


def test_linkedin_is_found_by_its_label(page):
    assert ae._fill_by_label(page, ["linkedin"], "https://www.linkedin.com/in/x/")
    assert page.input_value("#question_1") == "https://www.linkedin.com/in/x/"


# ── Comeet: the form lives in an iframe opened by "APPLY FOR THIS JOB" ──────

POSTING = """<!doctype html><html><body><h1>Head of Product</h1>
<a href="#" id="apply" onclick="document.getElementById('f').src='https://www.comeet.co/jobs/1.2/3.4/apply?token=t';return false">APPLY FOR THIS JOB</a>
<iframe id="f" style="width:600px;height:500px"></iframe></body></html>"""

FORM = """<!doctype html><html><body>
<input name="firstName" required aria-label="First name"><input name="lastName" required aria-label="Last name">
<input name="email" type="email" required aria-label="Email"><input name="phone" type="tel" required aria-label="Phone">
<input name="cv" id="cv" type="file" required style="display:none"><input name="websiteUrl" type="url">
<button onclick="%s">SUBMIT APPLICATION</button><div id="out"></div>
<script>window.sent = null;</script></body></html>"""


def _comeet_run(on_submit):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=CHROME, headless=True, args=["--no-sandbox"])
        ctx = b.new_context()
        ctx.route("https://www.comeet.com/**", lambda r: r.fulfill(status=200, content_type="text/html", body=POSTING))
        ctx.route("https://www.comeet.co/**", lambda r: r.fulfill(status=200, content_type="text/html",
                                                                  body=FORM % on_submit))
        page = ctx.new_page()
        page.goto("https://www.comeet.com/jobs/acme/1.2/head-of-product/3.4")
        res = ae._apply_comeet(page, {"first_name": "Eran", "last_name": "Ganot", "email": "e@x.test",
                                      "phone": ae.intl_phone("054-6912084"),
                                      "linkedin_url": "https://www.linkedin.com/in/x/"}, None)
        frame = next(f for f in page.frames if "/apply?token" in f.url)
        values = {n: frame.input_value('input[name="%s"]' % n)
                  for n in ("firstName", "lastName", "email", "phone", "websiteUrl")}
        b.close()
    return res, values


def test_comeet_fills_the_iframe_form_and_needs_a_real_confirmation():
    res, values = _comeet_run("document.getElementById('out').innerText='Thank you for applying!'")
    assert res["status"] == "confirmed", res
    assert values == {"firstName": "Eran", "lastName": "Ganot", "email": "e@x.test",
                      "phone": "+972 546912084", "websiteUrl": "https://www.linkedin.com/in/x/"}


def test_comeet_without_a_confirmation_is_not_counted_as_submitted():
    res, _ = _comeet_run("void 0")      # the button does nothing visible
    assert res["status"] == "failed" and "no confirmation" in res["error"], res


def test_local_israeli_numbers_become_international():
    assert ae.intl_phone("054-6912084") == "+972 546912084"
    assert ae.intl_phone("+44 20 7946 0000") == "+44 20 7946 0000"
    assert ae.intl_phone("0044 20 7946 0000") == "+442079460000"
    assert ae.intl_phone("") == ""
    assert ae._is_comeet("https://www.comeet.com/jobs/cyera/17.008/head-of-product-security/7D.379")
    assert not ae._is_comeet("https://boards.greenhouse.io/x/jobs/1")


def test_hidden_error_templates_are_not_reported_as_the_reason(page):
    """Lever keeps 'File exceeds the maximum upload size of 100MB' hidden in
    every form; it was reported as why each submit failed."""
    page.set_content('<form><input name="x"><div class="error-message" style="display:none">'
                     'File exceeds the maximum upload size of 100MB.</div>'
                     '<div class="error" >Phone is required</div></form>')
    res = ae._verify_submission(page)
    assert res["status"] == "failed"
    assert "Phone is required" in res["error"] and "100MB" not in res["error"]
