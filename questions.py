"""Answering an application form's questions on the applicant's behalf.

Eran, 2026-11-07: "For any 'not ordinary' questions like open questions or
multiple answer questions - you should answer on my behalf. If you are not
sure - show me the questions and I'll guide you."

  collect(frame)              every unanswered question on the form: text, long
                              text, <select>, role="combobox", Comeet's custom
                              dropdown, and radio /
                              checkbox GROUPS with their option labels
  answer(frame, applicant, job, llm)
                              answers from the user's saved answers first, then
                              ONE model call with a confidence per answer; fills
                              the confident ones and returns the required ones it
                              is not sure about (with a draft) - the caller then
                              does not submit
  key(label)                  the normalised question text answers are saved under

Works on a Playwright Page or Frame (Comeet's form is an iframe).
"""
from __future__ import annotations

import json
import re

# Identity fields the ATS handlers fill themselves.
_STANDARD = re.compile(r"\b(first[ _-]?name|last[ _-]?name|full[ _-]?name|e-?mail|phone|resume|cv|"
                       r"cover[ _-]?letter)\b", re.I)
# "What is your preferred first name?*" used to be in _STANDARD - but no handler
# fills it, so a required one stayed empty and Greenhouse refused the submit
# (Credible, 2026-11-09). It is now answered from the applicant's first name.
_PREFERRED_NAME = re.compile(r"preferred\s+(first\s+)?name|name\s+you\s+(go|prefer)", re.I)
_DEMOGRAPHIC = re.compile(r"gender|race|ethnic|veteran|disabilit|hispanic|latino|sexual orientation|"
                          r"pronoun|transgender", re.I)

COLLECT_JS = r"""() => {
  const vis = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const labelOf = el => {
    let t = '';
    if (el.id) { const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]'); if (l) t = l.innerText; }
    if (!t && el.getAttribute('aria-labelledby')) {
      t = el.getAttribute('aria-labelledby').split(' ').map(i => (document.getElementById(i) || {}).innerText || '').join(' ');
    }
    if (!t && el.type !== 'radio' && el.type !== 'checkbox' && el.closest('label')) t = el.closest('label').innerText;
    if (!t) t = el.getAttribute('aria-label') || '';
    return clean(t);
  };
  const groupLabel = el => {
    const fs = el.closest('fieldset'); if (fs) { const lg = fs.querySelector('legend'); if (lg) return clean(lg.innerText); }
    const q = el.closest('.application-question, [class*="question"], [role="group"], [role="radiogroup"]');
    if (q) {
      const lab = q.querySelector('.application-label, legend, label:not(:has(input))');
      if (lab) return clean(lab.innerText);
      return clean((q.innerText || '').split('\n')[0]);
    }
    return '';
  };
  const out = [], seen = new Set();
  for (const el of document.querySelectorAll('input, select, textarea')) {
    const type = el.getAttribute('role') === 'combobox' ? 'combobox'
               : el.tagName === 'SELECT' ? 'select' : el.tagName === 'TEXTAREA' ? 'textarea'
               : (el.type || 'text').toLowerCase();
    if (['hidden', 'file', 'submit', 'button', 'image', 'reset', 'password'].includes(type)) continue;
    if (el.disabled || el.readOnly) continue;
    if ((el.name || '').startsWith('g-recaptcha') || (el.name || '').startsWith('h-captcha')) continue;
    const group = (type === 'radio' || type === 'checkbox') && el.name;
    if (group) {
      if (seen.has('g:' + el.name)) continue; seen.add('g:' + el.name);
      const items = [...document.getElementsByName(el.name)];
      if (!items.some(vis)) continue;
      const opts = items.map(i => labelOf(i) || clean((i.closest('label') || {}).innerText) || i.value).filter(Boolean);
      const label = groupLabel(el) || labelOf(el);
      const required = items.some(i => i.required || i.getAttribute('aria-required') === 'true') || /[*✱]/.test(label);
      out.push({selector: 'input[name="' + el.name + '"]', label, type, options: opts.slice(0, 30), required,
                filled: items.some(i => i.checked)});
      continue;
    }
    if (!vis(el) && type !== 'select') continue;
    if (el.getAttribute('aria-label') === 'Search') continue;
    let sel = el.id ? '#' + CSS.escape(el.id) : (el.name ? el.tagName.toLowerCase() + '[name="' + el.name + '"]' : '');
    if (!sel || seen.has(sel)) continue; seen.add(sel);
    const label = labelOf(el) || groupLabel(el) || el.placeholder || el.name || '';
    let options = [];
    if (type === 'select') options = [...el.options].filter(o => o.value && !/^select/i.test(o.text)).map(o => clean(o.text)).slice(0, 40);
    // A React-select combobox keeps its text box EMPTY after a choice; the value
    // shows as select__single-value in a "--has-value" container (Greenhouse's
    // Country, picked from the phone's +972 - read as unanswered, 2026-11-07).
    const comboHasValue = type === 'combobox' && !!(el.closest('[class*="value-container"]') &&
        (/has-value/.test(el.closest('[class*="value-container"]').className) ||
         el.closest('[class*="value-container"]').querySelector('[class*="single-value"], [class*="multi-value"]')));
    const filled = type === 'select' ? !!el.value && el.selectedIndex > 0 || (!!el.value && !/^select/i.test(el.options[el.selectedIndex]?.text || ''))
                 : type === 'combobox' ? (comboHasValue || !!clean(el.value))
                 : !!clean(el.value);
    const required = el.required || el.getAttribute('aria-required') === 'true' || /[*✱]/.test(label);
    out.push({selector: sel, label, type, options, required, filled});
  }
  // Comeet's custom dropdown (AngularJS): no <select>, an <a class="dropdown-toggle"
  // aria-label="Choose answer for <question>"> and a ul.dropdown-menu of options.
  // Not read before 2026-11-09 - Dream's "Are you currently based in Israel? *"
  // went unanswered and Comeet refused the submit with "Required".
  for (const a of document.querySelectorAll('a.dropdown-toggle[aria-haspopup]')) {
    if (!vis(a)) continue;
    const dd = a.closest('.dropdown'); if (!dd) continue;
    const opts = [...dd.querySelectorAll('.dropdown-menu li')].map(l => clean(l.innerText)).filter(Boolean);
    if (!opts.length) continue;
    const box = dd.parentElement && dd.parentElement.parentElement;
    const label = clean((a.getAttribute('aria-label') || '').replace(/^Choose answer for /i, '')) ||
                  clean(((box || {}).innerText || '').split('\n')[0]);
    const required = /[*✱]/.test(((box || {}).innerText || '').split('\n')[0]);
    const sel = a.id ? '[id="' + a.id.replace(/"/g, '\\"') + '"]' : '';
    if (!sel || seen.has(sel)) continue; seen.add(sel);
    const shown = clean(a.innerText);
    out.push({selector: sel, label, type: 'dropdown', options: opts.slice(0, 40), required,
              filled: !!shown && !/^choose/i.test(shown)});
  }
  return out;
}"""


def key(label: str) -> str:
    """'Will you now or in the future require visa sponsorship? (e.g H1B visa)*'
    -> 'will you now or in the future require visa sponsorship e g h1b visa'"""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (label or "").lower())).strip()[:200]


def collect(frame) -> list:
    """Unanswered, non-identity questions on the form, combobox options read."""
    try:
        fields = frame.evaluate(COLLECT_JS) or []
    except Exception as exc:
        print(f"[questions] collect failed: {exc}")
        return []
    out = []
    for f in fields:
        lab = f.get("label") or ""
        if f.get("filled") or not lab or (_STANDARD.search(lab) and not _PREFERRED_NAME.search(lab)
                                          and not f.get("options")):
            continue
        if f["type"] == "combobox" and not f.get("options"):
            import apply_engine as _ae
            f["options"] = _ae._combobox_options(frame, f["selector"])
        f["key"] = key(lab)
        out.append(f)
    return out[:30]


_PROMPT = """You are filling in a job application ON BEHALF of the applicant, in the first person.
Applicant (JSON): {applicant}
Job: {job}

Applicant's standing instructions (they override every rule below):
{instructions}

Questions (JSON array): {questions}

Return ONLY a JSON array, one item per question you can answer:
{{"selector": "...", "value": "...", "confidence": "high" | "low"}}
Rules:
- Follow the standing instructions first. Where they say not to answer something: if the
  question is required, return it with value "" and confidence "low" (the applicant will be
  asked); if it is optional, leave it out. A fact stated in the instructions is a fact you may use.
- Use only facts in the applicant data, the CV excerpt and the job. Never invent employers,
  degrees, numbers, dates, salaries, notice periods, references or certifications.
- Open questions ("why us", "proudest project", "tell us about..."): 2-4 sentences in the
  applicant's voice, specific to this job and grounded in the CV. confidence "high" only if
  the CV gives you real material for it.
- select / combobox / radio: value MUST be exactly one of that question's options.
  checkbox group: value is a JSON list of option texts.
- Visa / work authorization: posting in the applicant's home_country -> use
  needs_sponsorship_in_home_country, otherwise needs_sponsorship_abroad.
- Demographic questions (gender, race, veteran, disability...): the "decline / prefer not
  to say" option, confidence "high"; if none exists, leave the question out.
- Consent / acknowledgement / terms checkboxes: agree, "high".
- "How did you hear about us": "Company website" or the closest option, "high".
- Salary expectations: use salary_expectation from the applicant data (state it as that
  minimum, in the form's format), confidence "high". Notice period / availability: use
  notice_period, "high". If either is absent from the applicant data, confidence "low".
- confidence "low" whenever you are guessing or the answer depends on facts not given
  (start date beyond the notice period, relocation, citizenship of another country,
  specific years of a skill not in the CV)."""


def _fill(frame, q, value) -> bool:
    import apply_engine as _ae
    sel, t = q["selector"], q["type"]
    if t in ("radio", "checkbox"):
        wanted = value if isinstance(value, list) else [value]
        ok = frame.evaluate("""([name, wanted]) => {
          const norm = s => (s || '').replace(/\\s+/g, ' ').trim().toLowerCase();
          const labelOf = el => {
            if (el.id) { const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]'); if (l) return l.innerText; }
            return (el.closest('label') || {}).innerText || el.value;
          };
          let n = 0;
          for (const el of document.getElementsByName(name)) {
            if (wanted.map(norm).includes(norm(labelOf(el))) || wanted.map(norm).includes(norm(el.value))) {
              if (!el.checked) el.click(); n++;
            }
          }
          return n;
        }""", [re.search(r'name="([^"]+)"', sel).group(1), [str(w) for w in wanted]])
        return bool(ok)
    if t == "combobox":
        return _ae._choose_combobox(frame, sel, str(value))
    if t == "dropdown":                   # Comeet's custom dropdown: open it, click the option
        frame.click(sel)
        ok = frame.evaluate("""([sel, want]) => {
          const norm = s => (s || '').replace(/\\s+/g, ' ').trim().toLowerCase();
          const dd = document.querySelector(sel).closest('.dropdown');
          const hit = [...dd.querySelectorAll('.dropdown-menu li a')].find(x => norm(x.innerText) === norm(want));
          if (hit) hit.click();
          return !!hit;
        }""", [sel, str(value)])
        return bool(ok)
    if t == "select":
        try:
            frame.select_option(sel, label=str(value))
        except Exception as exc:          # some forms match the option by value
            print(f"[questions] select by label failed ({type(exc).__name__}), trying by value")
            frame.select_option(sel, value=str(value))
        return True
    frame.fill(sel, str(value))
    return True


def answer(frame, applicant: dict, job: dict, llm=None) -> dict:
    """Fill what can be answered; return {'filled': n, 'unsure': [...], 'questions': [...]}.
    `unsure` = required questions without a confident answer, each with a 'draft'.
    llm(prompt) -> parsed JSON; None means no model available (bank answers only)."""
    qs = collect(frame)
    bank = (applicant or {}).get("answer_bank") or {}
    filled, unsure, ask = 0, [], []
    for q in qs:
        if q["key"] in bank:
            try:
                if _fill(frame, q, bank[q["key"]]):
                    filled += 1
                    continue
            except Exception as exc:
                print(f"[questions] saved answer did not fit {q['label'][:60]!r}: {exc}")
        # A LinkedIn question asked as a custom text question ("LinkedIn URL *",
        # Cloudinary on Lever, 2026-11-08) gets the profile's LinkedIn - it
        # used to go to the model and, in a rehearsal, held the application.
        # The same for GitHub, from a github.com address in the applicant's
        # standing instructions (Eran, 2026-11-08).
        _profile_link = None
        if q.get("type") in ("text", "url"):
            if _PREFERRED_NAME.search(q.get("label") or ""):
                _profile_link = (applicant or {}).get("first_name")
            elif re.search(r"linked\s*in", q.get("label") or "", re.I):
                _profile_link = (applicant or {}).get("linkedin_url")
            elif re.search(r"git\s*hub", q.get("label") or "", re.I):
                _profile_link = (applicant or {}).get("github_url")
        if _profile_link:
            try:
                if _fill(frame, q, _profile_link):
                    filled += 1
                    continue
            except Exception as exc:
                print(f"[questions] profile link {q['label'][:60]!r}: {exc}")
        # A required acknowledgement whose only option is "Yes" ("I read and
        # understood the privacy policy*", Payoneer 2026-11-08) is consent to
        # apply at all - filled without asking anyone.
        if q.get("required") and [o.strip().lower() for o in (q.get("options") or [])] == ["yes"]:
            try:
                if _fill(frame, q, q["options"][0]):
                    filled += 1
                    continue
            except Exception as exc:
                print(f"[questions] acknowledgement {q['label'][:60]!r}: {exc}")
        ask.append(q)
    answers = {}
    if ask and llm is not None:
        safe = {k: v for k, v in (applicant or {}).items()
                if k not in ("answer_bank", "standing_instructions") and not str(k).startswith("_") and not callable(v)}
        notes = ((applicant or {}).get("standing_instructions") or "").strip()[:3000] or "(none)"
        prompt = _PROMPT.format(applicant=json.dumps(safe)[:6000], job=json.dumps(job)[:3500], instructions=notes,
                                questions=json.dumps([{k: q[k] for k in ("selector", "label", "type", "options", "required")}
                                                      for q in ask])[:8000])
        try:
            got = llm(prompt)
        except Exception as exc:
            print(f"[questions] model call failed: {exc}")
            got = []
        for a in got if isinstance(got, list) else []:
            if isinstance(a, dict) and a.get("selector"):
                answers[a["selector"]] = a
    for q in ask:
        a = answers.get(q["selector"])
        demographic = bool(_DEMOGRAPHIC.search(q["label"]))
        confident = a is not None and (a.get("confidence") == "high" or demographic) and a.get("value") not in (None, "", [])
        if confident:
            try:
                if _fill(frame, q, a["value"]):
                    filled += 1
                    continue
            except Exception as exc:
                print(f"[questions] could not fill {q['label'][:60]!r}: {exc}")
        if q.get("required"):
            unsure.append({"key": q["key"], "label": q["label"], "type": q["type"],
                           "options": q.get("options") or [], "draft": (a or {}).get("value", "")})
    return {"filled": filled, "unsure": unsure, "questions": len(qs)}
