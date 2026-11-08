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


# ── answering every question; holding for the applicant when unsure ────────

import questions  # noqa: E402

FORM_QS = """<!doctype html><html><body><form>
<div class="application-question"><div class="application-label">Current location ✱</div>
  <input name="location" id="loc" type="text"></div>
<div class="application-question"><div class="application-label">Are you legally authorized to work in Israel? ✱</div>
  <label><input type="radio" name="auth" value="a1">Yes</label><label><input type="radio" name="auth" value="a2">No</label></div>
<div class="application-question"><div class="application-label">Languages ✱</div>
  <label><input type="checkbox" name="lang" value="en">English</label><label><input type="checkbox" name="lang" value="he">Hebrew</label></div>
<label for="heard">How did you hear about us? *</label>
<select id="heard" required><option value="">Select...</option><option value="w">Company website</option><option value="l">LinkedIn</option></select>
<label for="why">Why do you want to work here?</label><textarea id="why"></textarea>
<label for="salary">Salary expectations *</label><input id="salary" required>
<label for="first_name">First Name</label><input id="first_name" value="Eran">
</form></body></html>"""


def test_the_collector_sees_every_unanswered_question(page):
    page.set_content(FORM_QS)
    got = {q["label"]: q for q in questions.collect(page)}
    assert got["Are you legally authorized to work in Israel? ✱"]["type"] == "radio"
    assert got["Are you legally authorized to work in Israel? ✱"]["options"] == ["Yes", "No"]
    assert got["Languages ✱"]["options"] == ["English", "Hebrew"] and got["Languages ✱"]["required"]
    assert got["How did you hear about us? *"]["options"] == ["Company website", "LinkedIn"]
    assert got["Why do you want to work here?"]["required"] is False
    assert not any("first name" in k.lower() for k in got)        # identity fields are the handler's


def test_sure_answers_are_filled_and_an_unsure_required_one_holds_the_application(page):
    page.set_content(FORM_QS)
    sel = {q["label"]: q["selector"] for q in questions.collect(page)}

    def llm(prompt):
        assert "ON BEHALF" in prompt
        return [{"selector": sel["Current location ✱"], "value": "Tel Aviv, Israel", "confidence": "high"},
                {"selector": sel["Are you legally authorized to work in Israel? ✱"], "value": "Yes", "confidence": "high"},
                {"selector": sel["Languages ✱"], "value": ["English", "Hebrew"], "confidence": "high"},
                {"selector": sel["How did you hear about us? *"], "value": "Company website", "confidence": "high"},
                {"selector": sel["Why do you want to work here?"], "value": "Because...", "confidence": "low"},
                {"selector": sel["Salary expectations *"], "value": "40,000 ILS", "confidence": "low"}]
    res = questions.answer(page, {"full_name": "Eran Ganot"}, {"title": "PM"}, llm)
    assert page.input_value("#loc") == "Tel Aviv, Israel"
    assert page.is_checked('input[name="auth"][value="a1"]')
    assert page.is_checked('input[value="en"]') and page.is_checked('input[value="he"]')
    assert page.eval_on_selector("#heard", "e => e.options[e.selectedIndex].text") == "Company website"
    assert page.input_value("#why") == ""                        # optional + unsure: left blank
    assert [u["label"] for u in res["unsure"]] == ["Salary expectations *"]
    assert res["unsure"][0]["draft"] == "40,000 ILS"


def test_standing_instructions_go_to_the_model_as_their_own_section(page):
    page.set_content(FORM_QS)
    seen = {}

    def llm(prompt):
        seen["p"] = prompt
        return []
    notes = "Current salary: do not answer."
    res = questions.answer(page, {"full_name": "Eran Ganot", "standing_instructions": notes}, {}, llm)
    head, _, rest = seen["p"].partition("standing instructions (they override every rule below):")
    assert rest.lstrip().startswith(notes)
    assert "standing_instructions" not in head                  # not duplicated in the applicant JSON
    assert "Salary expectations *" in [u["label"] for u in res["unsure"]]   # unanswered required: held
    seen.clear()
    questions.answer(page, {"full_name": "Eran Ganot"}, {}, llm)
    assert "override every rule below):\n(none)" in seen["p"]


def test_a_saved_answer_is_used_without_asking_the_model(page):
    page.set_content(FORM_QS)
    bank = {questions.key("Salary expectations *"): "As discussed in interview"}
    res = questions.answer(page, {"answer_bank": bank}, {}, llm=None)
    assert page.input_value("#salary") == "As discussed in interview"
    assert "Salary expectations *" not in [u["label"] for u in res["unsure"]]


def test_a_react_select_with_a_chosen_value_counts_as_answered(page):
    page.set_content("""<label id="country-label">Country*</label>
      <div class="select__value-container select__value-container--has-value">
        <div class="select__single-value">+972</div>
        <input id="country" role="combobox" aria-required="true" aria-labelledby="country-label" value="">
      </div>""")
    assert [q for q in questions.collect(page) if q["label"].startswith("Country")] == []


# ── Greenhouse embedded on a company's own careers page (Payoneer, 2026-11-08) ──

CAREERS = """<!doctype html><html><body><h1>Director of Product</h1>
<a href="/careers">Apply</a>
<iframe src="https://job-boards.greenhouse.io/embed/job_app?for=acme&validityToken=t" style="width:700px;height:600px"></iframe>
</body></html>"""

# The inputs exist first; 100 ms later the "app" takes over and wipes them -
# what Payoneer's embed did to the first values typed. _gh_fill's check-and-
# retry catches a wipe inside its 250 ms check window; a LATER wipe is what the
# handler's settle wait (networkidle + 1.5 s before filling an embed) is for -
# the live Payoneer rehearsal needed no retries after that wait.
EMBED = """<!doctype html><html><body><form>
<input id="first_name"><input id="last_name"><input id="email"><input id="phone">
<button type="submit" onclick="event.preventDefault()">Submit application</button></form>
<script>setTimeout(() => { for (const i of document.querySelectorAll('input')) i.value = ''; }, 100);</script>
</body></html>"""


def _careers_page():
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    b = pw.chromium.launch(executable_path=CHROME, headless=True, args=["--no-sandbox"])
    ctx = b.new_context()
    ctx.route("https://www.acme.example/**", lambda r: r.fulfill(status=200, content_type="text/html", body=CAREERS))
    ctx.route("https://job-boards.greenhouse.io/**", lambda r: r.fulfill(status=200, content_type="text/html", body=EMBED))
    page = ctx.new_page()
    page.goto("https://www.acme.example/careers/position/1/?gh_jid=1")
    return pw, b, page


def test_the_greenhouse_form_is_found_in_the_embed_iframe():
    pw, b, page = _careers_page()
    try:
        form = ae._greenhouse_form(page, wait_ms=5_000)
        assert form is not None and form is not page and "embed/job_app" in form.url
    finally:
        b.close(); pw.stop()


def test_a_fill_wiped_by_the_form_taking_over_is_retried_until_it_sticks():
    pw, b, page = _careers_page()
    try:
        form = ae._greenhouse_form(page, wait_ms=5_000)
        assert ae._gh_fill(form, "#email", "eran@example.org")       # wiped once, retried, kept
        page.wait_for_timeout(400)
        assert form.input_value("#email") == "eran@example.org"
    finally:
        b.close(); pw.stop()


# React-select markup, as on Payoneer's embedded Greenhouse form (2026-11-08).
# `sticky` decides whether clicking the option actually selects it - on the
# live page inside payoneer.com it intermittently did not, and the engine
# reported success for having clicked.
def _react_select(sticky: bool) -> str:
    return """<!doctype html><html><body>
<label id="pp-label" for="pp">I read and understood the privacy policy*</label>
<div class="select__control"><div class="select__value-container">
  <div class="select__placeholder">Select...</div>
  <input id="pp" role="combobox" aria-required="true" aria-labelledby="pp-label" autocomplete="off">
</div></div>
<div id="pp-list" role="listbox" hidden><div role="option">Yes</div></div>
<script>
const inp = document.getElementById('pp'), box = document.getElementById('pp-list'), vc = inp.parentElement;
inp.addEventListener('keydown', e => { if (e.key === 'Escape') box.hidden = true; });
inp.addEventListener('input', () => { inp.setAttribute('aria-controls', 'pp-list'); box.hidden = false; });
box.querySelector('[role=option]').addEventListener('click', () => {
  box.hidden = true; inp.value = '';
  if (%s) { vc.classList.add('select__value-container--has-value');
            vc.querySelector('.select__placeholder').outerHTML = '<div class="select__single-value">Yes</div>'; }
});
</script></body></html>""" % ("true" if sticky else "false")


def test_a_react_select_choice_counts_only_when_the_value_shows(page):
    page.set_content(_react_select(sticky=True))
    assert ae._choose_combobox(page, "#pp", "Yes") is True
    assert page.inner_text(".select__single-value") == "Yes"
    page.set_content(_react_select(sticky=False))
    assert ae._choose_combobox(page, "#pp", "Yes") is False      # clicked, nothing selected: not success


# Greenhouse's security-code step, as described by projects that met it: Submit
# greys out, a code is emailed, eight one-character boxes appear after a short
# delay; the application goes only after the code and a second Submit.
CODE_FORM = """<!doctype html><html><body><form id="f" onsubmit="return false">
<input id="first_name" value="Eran"><button type="submit" id="go">Submit application</button>
<div id="code"></div></form>
<script>
(() => {
let stage = 0;
document.getElementById('go').addEventListener('click', () => {
  if (stage === 0) {
    stage = 1;
    setTimeout(() => {
      const c = document.getElementById('code');
      c.innerHTML = '<p>Enter the 8-character code we sent to confirm you\\'re a human (security code)</p>' +
        [...Array(8)].map((_, i) => '<input class="sec" maxlength="1" aria-label="Character ' + (i + 1) + '">').join('');
      const boxes = [...document.querySelectorAll('.sec')];
      boxes.forEach((b, i) => b.addEventListener('input', () => { if (b.value && boxes[i + 1]) boxes[i + 1].focus(); }));
    }, 800);
  } else {
    const typed = [...document.querySelectorAll('.sec')].map(b => b.value).join('');
    if (typed === 'K7Q2ZP9X') document.body.innerHTML = '<h1>Thank you for applying!</h1>';
    else document.getElementById('code').insertAdjacentHTML('beforeend', '<p role="alert">Incorrect code</p>');
  }
});
})();
</script></body></html>"""
SUBMIT = ['button[type="submit"]']


def test_a_security_code_prompt_is_answered_with_the_applicants_code(page):
    page.set_content(CODE_FORM)
    page.click("#go")
    asked = []
    res = ae._finalize_after_submit(page, {"email": "e@x.test", "_await_code": lambda: asked.append(1) or "K7Q2 ZP9X"}, SUBMIT)
    assert asked == [1]
    assert res["status"] == "confirmed" and res["success"], res


def test_a_security_code_prompt_with_no_one_to_answer_is_not_a_retry(page):
    page.set_content(CODE_FORM)
    page.click("#go")
    res = ae._finalize_after_submit(page, {"email": "e@x.test"}, SUBMIT)
    assert res["status"] == "needs_code" and "e@x.test" in res["error"]
    page.set_content(CODE_FORM)
    page.click("#go")
    res = ae._finalize_after_submit(page, {"email": "e@x.test", "_await_code": lambda: None}, SUBMIT)
    assert res["status"] == "needs_code" and "in time" in res["error"]


def test_a_wrong_security_code_is_reported_not_called_a_success(page):
    page.set_content(CODE_FORM)
    page.click("#go")
    res = ae._finalize_after_submit(page, {"_await_code": lambda: "WRONG123"}, SUBMIT)
    assert not res["success"]


def test_an_ordinary_form_has_no_code_step(page):
    page.set_content(PAGE)
    assert ae._code_boxes(page) == 0


def test_linkedin_and_github_questions_are_filled_from_the_profile_not_the_model(page):
    page.set_content("""<form>
      <label for="q1">LinkedIn URL *</label><input id="q1" type="text" required>
      <label for="q2">GitHub URL</label><input id="q2" type="text">
    </form>""")
    res = questions.answer(page, {"linkedin_url": "https://www.linkedin.com/in/x/",
                                  "github_url": "https://github.com/example-user"}, {}, llm=None)
    assert page.input_value("#q1") == "https://www.linkedin.com/in/x/"
    assert page.input_value("#q2") == "https://github.com/example-user"
    assert res["unsure"] == []


LEVER_LOCATION = """<form>
<input id="location-input" name="location" type="text">
<input name="selectedLocation" type="hidden" value="">
<div id="dd"></div>
<script>
(() => {
const inp = document.getElementById('location-input');
inp.addEventListener('input', () => {
  document.getElementById('dd').innerHTML = inp.value.length >= 3
    ? '<div class="dropdown-location">Tel Aviv, ISR</div><div class="dropdown-location">Tel Aviv-Yafo, ISR</div>' : '';
  for (const o of document.querySelectorAll('.dropdown-location'))
    o.onclick = () => { inp.value = o.innerText;
      document.querySelector('input[name=selectedLocation]').value = JSON.stringify({name: o.innerText}); };
});
})();
</script></form>"""


def test_lever_location_is_chosen_from_its_dropdown(page):
    """Rehearsal 2026-11-08: Lever's required "Current location" stayed empty."""
    page.set_content(LEVER_LOCATION)
    assert ae._lever_location(page, {"location": "Tel Aviv, Israel"}) is True
    assert "Tel Aviv, ISR" in page.eval_on_selector("input[name=selectedLocation]", "e => e.value")
    page.set_content(LEVER_LOCATION)
    assert ae._lever_location(page, {}) is False                      # no city known: leave it
