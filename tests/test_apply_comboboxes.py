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
