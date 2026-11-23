// B0 fill engine: fills an application form from the answers Job Hunter prepared
// (jobs.apply_prepared: [{label, value}], read by apply_engine._SNAPSHOT_JS) and
// attaches the CV. No browser-extension APIs here: the Android WebView test app
// injects this same file. Labels are read exactly as _SNAPSHOT_JS reads them, so
// a prepared label meets its own field again.
(() => {
  if (window.JHFill) return;
  const vis = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
  // SmartRecruiters' form is web components: every input sits in a shadow root
  // (spl-input, spl-dropzone, spl-textarea), so document.querySelectorAll finds
  // none of them (the owner, 2026-11-23: "doesn't do anything"). Every lookup
  // here goes through the open shadow roots too.
  const deepAll = (sel, root = document, out = []) => {
    out.push(...root.querySelectorAll(sel));
    for (const el of root.querySelectorAll('*')) if (el.shadowRoot) deepAll(sel, el.shadowRoot, out);
    return out;
  };
  // The element, then its ancestors - across shadow boundaries (host of each root).
  const upward = el => { const a = []; let n = el; for (let i = 0; n && i < 8; i++) { a.push(n); n = n.parentElement || (n.getRootNode() || {}).host; } return a; };
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const norm = s => clean(s).replace(/\s*[*✱]\s*$/, '').toLowerCase();
  const labelOf = el => {
    let t = '';
    if (el.id) { const l = (el.getRootNode() || document).querySelector('label[for="' + CSS.escape(el.id) + '"]'); if (l) t = l.textContent; }
    if (!t) { const h = (el.getRootNode() || {}).host; if (h && h.getAttribute('label')) t = h.getAttribute('label'); }
    if (!t && el.closest('label')) t = el.closest('label').innerText;
    if (!t) { const q = el.closest('.application-question, li, [class*="question"]');
              if (q) t = (q.querySelector('.application-label, label, legend') || q).innerText.split('\n')[0]; }
    return clean(t || el.getAttribute('aria-label') || el.placeholder || el.name);
  };
  const groupLabel = el => {
    const q = el.closest('.application-question, li, fieldset, [class*="question"]');
    return clean(q ? (q.querySelector('.application-label, legend, .text') || q).innerText.split('\n')[0] : el.name);
  };
  const setValue = (el, v) => {
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const set = Object.getOwnPropertyDescriptor(proto, 'value').set;
    el.focus();
    set.call(el, v);
    el.dispatchEvent(new Event('input', {bubbles: true, composed: true}));
    el.dispatchEvent(new Event('change', {bubbles: true, composed: true}));
    el.dispatchEvent(new Event('blur', {bubbles: true, composed: true}));
  };
  const optionText = o => clean(o.text || o.innerText);

  // A radio or checkbox drawn by the site with the real input hidden underneath
  // (SmartRecruiters' "Are you eligible to work in Israel?", 2026-11-23: the
  // page-2 trace saw 3 fields - two dropdowns and the privacy box - and never
  // the Yes/No question) counts when its label is on screen.
  const shownChoice = el => {
    const t = (el.type || '').toLowerCase();
    if (t !== 'radio' && t !== 'checkbox') return false;
    const l = (el.id && (el.getRootNode() || document).querySelector('label[for="' + CSS.escape(el.id) + '"]')) || el.closest('label');
    return !!(l && vis(l)) || vis(((el.getRootNode() || {}).host) || el.parentElement || el);
  };
  function fields() {
    return deepAll('input, select, textarea').filter(el => {
      const t = (el.type || '').toLowerCase();
      return !['hidden', 'submit', 'button', 'image', 'reset', 'password'].includes(t) &&
             !(el.name || '').match(/captcha/i) && (vis(el) || el.tagName === 'SELECT' || t === 'file' || shownChoice(el));
    });
  }

  function hasForm() {
    return fields().some(el => vis(el) && ['text', 'email', 'tel', ''].includes((el.type || '').toLowerCase()));
  }

  // Fill what the prepared answers cover. Returns {filled, missing: [labels not found]}.
  function fill(prepared) {
    const want = new Map((prepared || []).map(p => [norm(p.label), String(p.value)]));
    const used = new Set();
    let filled = 0;
    const seen = new Set();
    for (const el of fields()) {
      const t = (el.type || '').toLowerCase();
      if (t === 'file') continue;
      if (t === 'radio' || t === 'checkbox') {
        if (!el.name || seen.has(el.name)) continue; seen.add(el.name);
        const key = norm(groupLabel(el));
        if (!want.has(key)) continue;
        const values = want.get(key).split(',').map(norm);
        let hit = 0;
        for (const i of document.getElementsByName(el.name)) {
          const lab = norm((i.closest('label') || {}).innerText || i.value);
          if (values.includes(lab) || values.includes(norm(i.value))) { if (!i.checked) i.click(); hit++; }
        }
        if (hit) { filled++; used.add(key); }
        continue;
      }
      const key = norm(labelOf(el));
      if (!want.has(key)) continue;
      const v = want.get(key);
      if (el.tagName === 'SELECT') {
        const o = [...el.options].find(o => norm(optionText(o)) === norm(v)) || [...el.options].find(o => o.value === v);
        if (!o) continue;
        el.value = o.value; el.dispatchEvent(new Event('change', {bubbles: true}));
      } else {
        if (clean(el.value) === clean(v)) { filled++; used.add(key); continue; }
        setValue(el, v);
      }
      filled++; used.add(key);
    }
    // Comeet's answer dropdowns: <a class="dropdown-toggle" aria-label="Choose answer for <question>">.
    for (const a of document.querySelectorAll('a.dropdown-toggle[aria-haspopup]')) {
      const key = norm((a.getAttribute('aria-label') || '').replace(/^Choose answer for /i, ''));
      if (!want.has(key)) continue;
      const dd = a.closest('.dropdown'); if (!dd) continue;
      const li = [...dd.querySelectorAll('.dropdown-menu li')].find(l => norm(l.innerText) === norm(want.get(key)));
      if (!li) continue;
      a.click(); (li.querySelector('a') || li).click();
      filled++; used.add(key);
    }
    const missing = [...want.keys()].filter(k => !used.has(k));
    return {filled, missing};
  }

  // The applicant's own details (from the Profile page, sent with the job list)
  // for the fields every form has. Jobs added by link have no prepared answers
  // (nobody pressed "Apply now" on them), so without this only the CV went in
  // (the owner, 2026-11-23: Tonkean, Fullpath "does not fill everything").
  // Only EMPTY fields are filled: a prepared answer or Eran's typing wins.
  const BASICS = [
    [/confirm.*e-?mail|e-?mail.*(again|confirm)/i, 'email'],
    [/^e-?mail|email address|\be-?mail\b/i, 'email'],
    [/^first name|given name|^first$/i, 'first_name'],
    [/^last name|family name|surname|^last$/i, 'last_name'],
    [/^(full )?name$|^your name|full name/i, 'full_name'],
    [/phone|mobile|telephone/i, 'phone'],
    [/linkedin/i, 'linkedin_url'],
    [/github/i, 'github_url'],
    [/^city$|current location|^location$|city of residence/i, 'city'],
  ];
  function fillBasics(applicant, opts) {
    const a = applicant || {};
    let n = 0;
    const done = new Set();
    for (const el of fields()) {
      const t = (el.type || '').toLowerCase();
      if (!['text', 'email', 'tel', 'url', ''].includes(t) || el.tagName !== 'INPUT') continue;
      if (clean(el.value) || el.readOnly || el.disabled) continue;
      const label = norm(questionLabel(el));
      for (const [re, key] of BASICS) {
        if (!re.test(label) && !(key === 'phone' && t === 'tel')) continue;
        let v = a[key] || '';
        // A separate country-code picker next to the number (SmartRecruiters,
        // Comeet's +972 box): the number without its country code.
        if (key === 'phone' && v && (opts || {}).localPhone) v = a.phone_local || v;
        if (!v) break;
        if (key === 'full_name' && done.has('first_name')) break;
        if (isCombo(el)) { pickOption(el, v); n++; done.add(key); break; }   // City on SmartRecruiters: a suggestion list
        setValue(el, v); n++; done.add(key);
        break;
      }
    }
    return n;
  }

  // ── Searchable dropdowns (SmartRecruiters' City and its "Preliminary questions") ──
  const isCombo = el => el.getAttribute('role') === 'combobox' || !!el.getAttribute('aria-autocomplete') ||
    upward(el).slice(1, 5).some(n => /dropdown|autocomplete|combobox|select/i.test(n.tagName || ''));
  const optionEls = () => deepAll('[role="option"], spl-select-option, li[role="option"]').filter(vis);
  window.JHTrace = window.JHTrace || [];
  const jt = (step, data) => window.JHTrace.push({t: Date.now(), step, data});
  // Click through the page's own world (page.js); false when page.js did not answer.
  let clickSeq = 0;
  const clickInPage = el => new Promise(res => {
    const id = 'c' + (++clickSeq) + '-' + Date.now();
    el.dataset.jhClick = id;
    const done = e => { if (e.source === window && e.data && e.data.__jh === 'clicked' && e.data.id === id) {
      window.removeEventListener('message', done); res(e.data.ok ? 'page' : 'page-failed'); } };
    window.addEventListener('message', done);
    window.postMessage({__jh: 'click', id}, '*');
    setTimeout(() => { window.removeEventListener('message', done); res('no-page-world'); }, 1500);
  });
  async function pickOption(el, value) {
    const want = norm(String(value));
    setValue(el, String(value).split(',')[0]);
    const seen = [];
    for (let i = 0; i < 24; i++) {
      await new Promise(r => setTimeout(r, 250));
      const opts = optionEls();
      seen.push(opts.length);
      const o = opts.find(x => norm(x.textContent) === want) || opts.find(x => norm(x.textContent).startsWith(want)) ||
                opts.find(x => norm(x.textContent).includes(want)) || opts.find(x => want.includes(norm(x.textContent)) && norm(x.textContent));
      if (o) {
        const how = await clickInPage(o);
        await new Promise(r => setTimeout(r, 300));
        if (norm(el.value) !== norm(o.textContent)) { o.click(); await new Promise(r => setTimeout(r, 300)); }
        jt('pick', {label: labelOf(el).slice(0, 60), want, clicked: clean(o.textContent).slice(0, 60), how, after: el.value, rounds: i, seen: seen.slice(-3)});
        return true;
      }
    }
    jt('pick-miss', {label: labelOf(el).slice(0, 60), want, value: el.value, seen: seen.slice(-5),
                     sample: optionEls().slice(0, 5).map(x => clean(x.textContent).slice(0, 40))});
    return false;
  }
  async function comboOptions(el) {
    el.focus(); el.click();
    setValue(el, '');
    await new Promise(r => setTimeout(r, 700));
    const t = [...new Set(optionEls().map(o => clean(o.textContent)).filter(Boolean))].slice(0, 40);
    el.blur();
    return t;
  }

  // ── The questions nothing else answered: read them for the server to answer ──
  const BASIC_LABEL = /first name|last name|surname|full name|^name$|e-?mail|phone|mobile|linkedin|github|^city$|facebook|twitter|website|portfolio/i;
  // The question as Eran sees it: the nearest text printed just above the field,
  // in the same column. SmartRecruiters' "Preliminary questions" components give
  // the extension no label it can read (2026-11-23 trace: 3 questions, every
  // label ""), whatever element holds the text.
  // Inside a header pinned to the top of the page (position fixed/sticky)?
  const pinned = el => upward(el).some(n => n.nodeType === 1 && /^(fixed|sticky)$/.test(getComputedStyle(n).position));
  const textAbove = el => {
    const r = el.getBoundingClientRect();
    if (!r.width) return '';
    let best = '', bestGap = 1e9;
    const walk = root => {
      const it = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
      for (let n = it.nextNode(); n; n = it.nextNode()) {
        const t = clean(n.textContent);
        if (t.length < 3 || t.length > 2000 || /^this field is required/i.test(t)) continue;
        const p = n.parentElement;
        if (!p || /^(SCRIPT|STYLE|OPTION)$/.test(p.tagName) || pinned(p)) continue;
        const rng = document.createRange(); rng.selectNodeContents(n);
        const b = rng.getBoundingClientRect();
        if (!b.width || b.bottom > r.top + 2 || b.right < r.left - 40 || b.left > r.right) continue;
        const gap = r.top - b.bottom;
        if (gap < bestGap && gap < 90) { bestGap = gap; best = t; }
      }
      for (const h of root.querySelectorAll('*')) if (h.shadowRoot) walk(h.shadowRoot);
    };
    walk(document);
    return best;
  };
  const textBeside = el => {
    const host = ((el.getRootNode() || {}).host) || el;
    const r = (vis(el) ? el : host).getBoundingClientRect();
    let best = '', bestGap = 1e9;
    const walk = root => {
      const it = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
      for (let n = it.nextNode(); n; n = it.nextNode()) {
        const t = clean(n.textContent);
        if (t.length < 3 || t.length > 400) continue;
        const rng = document.createRange(); rng.selectNodeContents(n);
        const b = rng.getBoundingClientRect();
        if (!b.width || b.left < r.right - 2 || b.top > r.bottom + 6 || b.bottom < r.top - 6) continue;
        const gap = b.left - r.right;
        if (gap < bestGap && gap < 60) { bestGap = gap; best = t; }
      }
      for (const h of root.querySelectorAll('*')) if (h.shadowRoot) walk(h.shadowRoot);
    };
    walk(document);
    return best;
  };
  const questionLabel = el => {
    let t = labelOf(el);
    // A label that is only the required star (SmartRecruiters prints "*" in its
    // own label - the 2026-11-23 trace's "" labels) is no label.
    if (!t || !t.replace(/[\s*✱:]/g, '') || /^choose a file|^search by|^search$/i.test(t)) {
      const a = (el.type || '').toLowerCase() === 'checkbox' ? (textBeside(el) || textAbove(el)) : textAbove(el);
      if (a) t = a;
    }
    if (!t || /^choose a file|^search by/i.test(t)) {
      for (const n of upward(el).slice(1, 6)) {
        const l = n.getAttribute && n.getAttribute('label');
        if (l) { t = l; break; }
        const lg = n.querySelector && n.querySelector(':scope > legend, :scope > label, :scope > .label, :scope > [class*="label"]');
        if (lg && clean(lg.textContent)) { t = lg.textContent; break; }
      }
    }
    return clean(t);
  };
  // A radio group's question, not its first option's label: the fieldset's
  // legend, else a "label" attribute on the way up (SmartRecruiters' components),
  // else the question block's heading.
  const radioQuestion = el => {
    const fs = el.closest('fieldset');
    const lg = fs && fs.querySelector('legend');
    if (lg && clean(lg.textContent)) return clean(lg.textContent);
    for (const n of upward(el).slice(1, 7)) { const l = n.getAttribute && n.getAttribute('label'); if (l) return clean(l); }
    return textAbove(firstOfGroup(el)) || groupLabel(el);
  };
  // The first radio of its group: the question sits above it, not above "No".
  const firstOfGroup = el => {
    const root = el.getRootNode() || document;
    const g = el.name ? [...root.querySelectorAll('input[type="radio"]')].filter(x => x.name === el.name) : [el];
    const top = g.map(x => [x, (vis(x) ? x : (((x.getRootNode() || {}).host) || x.parentElement || x)).getBoundingClientRect().top])
                 .sort((a, b) => a[1] - b[1]);
    const lab = top[0] && top[0][0];
    const l = lab && lab.id && root.querySelector('label[for="' + CSS.escape(lab.id) + '"]');
    return (l && vis(l)) ? l : lab || el;
  };
  const radioText = e => clean(e.getAttribute('label') || e.getAttribute('aria-label') || e.textContent);
  function ariaRadioGroups() {
    const groups = new Map();
    for (const r of deepAll('[role="radio"]')) {
      if (r.tagName === 'INPUT' || !vis(r)) continue;
      const host = upward(r).slice(1, 6).find(n => /radio-?group/i.test(n.tagName || '') || (n.getAttribute && n.getAttribute('role') === 'radiogroup')) || r.parentElement;
      if (!groups.has(host)) {
        // The group's own question first: SmartRecruiters' spl-radio-group draws it in
        // its shadow root (<label ...>Question<span>*</span></label>). The text above
        // is only a fallback, and only when it reads as a question - on ServiceNow it
        // picked the pinned header's address and answered it "Yes" (2026-11-23).
        const own = host.shadowRoot && host.shadowRoot.querySelector('label, legend, [class*="label"]');
        const ownText = own ? clean(own.textContent).replace(/\s*[*✱]\s*$/, '') : '';
        const above = textAbove(r);
        // On ServiceNow every question came from the text above (2026-11-23 trace):
        // the group carries neither a label attribute nor its own label. 0.7.1 also
        // asked that text to end like a question - B ends "...is YES" (its "*" is a
        // separate element) and was dropped. Pinned headers are already excluded.
        const lab = clean((host.getAttribute && host.getAttribute('label')) || '') || ownText || above;
        jt('radio-group', {label: lab.slice(0, 80), from: host.getAttribute && host.getAttribute('label') ? 'attr' : ownText ? 'shadow' : lab ? 'above' : 'none',
                           options: r.parentElement ? [...r.parentElement.children].length : 0});
        groups.set(host, {host, items: [], label: lab,
          required: !!(host.hasAttribute && (host.hasAttribute('required') || host.getAttribute('aria-required') === 'true')) || /\*\s*$/.test(lab)});
      }
      groups.get(host).items.push(r);
    }
    const out = [...groups.values()];
    unlabeledGroups = out.filter(g => !g.label).length;
    return out.filter(g => g.label);
  }
  let unlabeledGroups = 0;
  const openRadioGroups = () => ariaRadioGroups().filter(g => !g.items.some(i => i.getAttribute('aria-checked') === 'true') && !g.items[0].dataset.jhAsked).length;
  let qSeq = 0;
  async function questions() {
    const out = [], radios = new Map();
    for (const el of fields()) {
      if (el.dataset.jhAsked) continue;
      const t = (el.type || '').toLowerCase();
      if (t === 'file' || !vis(el) && el.tagName !== 'SELECT' && !shownChoice(el)) continue;
      const label = questionLabel(el);
      if (!label || BASIC_LABEL.test(norm(label))) { el.dataset.jhAsked = '1'; continue; }
      if (t === 'radio') {
        const g = el.name || label;
        const lab = clean(((el.getRootNode() || document).querySelector('label[for="' + CSS.escape(el.id || '-') + '"]') || {}).textContent || el.value);
        if (!radios.has(g)) radios.set(g, {els: [], options: [], label: radioQuestion(el) || label});
        radios.get(g).els.push(el); radios.get(g).options.push(lab);
        continue;
      }
      if (t === 'checkbox' ? el.checked : clean(el.value)) continue;
      const id = 'jhq' + (++qSeq);
      el.dataset.jhq = id;
      const required = /\*\s*$/.test(clean(labelOf(el))) || el.required || el.getAttribute('aria-required') === 'true' ||
        upward(el).slice(1, 4).some(n => n.hasAttribute && n.hasAttribute('required'));
      let type = el.tagName === 'SELECT' ? 'select' : el.tagName === 'TEXTAREA' ? 'textarea' : t === 'checkbox' ? 'checkbox' : isCombo(el) ? 'combobox' : 'text';
      let options = type === 'select' ? [...el.options].map(optionText).filter(o => o && !/^select|^choose|^--/i.test(o))
                  : type === 'checkbox' ? ['Yes'] : type === 'combobox' ? await comboOptions(el) : [];
      out.push({selector: id, label: label.replace(/\s*[*✱]\s*$/, ''), type, options, required});
      if (!label) jt('no-label', upward(el).slice(0, 8).map(n => (n.tagName || '').toLowerCase() + '[' +
        [...(n.attributes || [])].map(a => a.name + '=' + String(a.value).slice(0, 30)).join(',') + ']'));
    }
    // Choices that are not inputs at all: SmartRecruiters' <spl-radio role="radio"
    // label="Yes"> inside <spl-radio-group label="Are you eligible to work in Israel?">
    // (seen on its own component, 2026-11-23). Wix's and ServiceNow's Yes/No questions.
    for (const grp of ariaRadioGroups()) {
      if (grp.items.some(i => i.getAttribute('aria-checked') === 'true') || grp.items[0].dataset.jhAsked) continue;
      const id = 'jhq' + (++qSeq);
      grp.items.forEach(e => { e.dataset.jhq = id; });
      out.push({selector: id, label: grp.label.replace(/\s*[*✱]\s*$/, ''), type: 'radio',
                options: grp.items.map(radioText), required: grp.required});
    }
    for (const [g, r] of radios) {
      if (r.els.some(e => e.checked)) continue;
      const id = 'jhq' + (++qSeq);
      r.els.forEach(e => { e.dataset.jhq = id; });
      out.push({selector: id, label: r.label.replace(/\s*[*✱]\s*$/, ''), type: 'radio', options: r.options,
                required: /\*/.test(r.label) || r.els.some(e => e.required)});
    }
    return out;
  }
  async function applyAnswers(answers) {
    let n = 0;
    for (const a of answers || []) {
      const els = deepAll('[data-jhq="' + a.selector + '"]');
      if (!els.length || a.value == null || a.value === '') continue;
      const el = els[0], t = (el.type || '').toLowerCase(), v = Array.isArray(a.value) ? a.value.join(', ') : String(a.value);
      if (el.tagName !== 'INPUT' && el.getAttribute('role') === 'radio') {
        const hit = els.find(e => norm(radioText(e)) === norm(v)) || els.find(e => norm(radioText(e)).startsWith(norm(v)) || norm(v).startsWith(norm(radioText(e))));
        if (hit) {
          const how = await clickInPage(hit);
          await new Promise(r => setTimeout(r, 200));
          if (hit.getAttribute('aria-checked') !== 'true') { hit.click(); await new Promise(r => setTimeout(r, 200)); }
          const ok = hit.getAttribute('aria-checked') === 'true';
          jt('radio', {q: a.selector, want: v, how, checked: ok});
          if (ok) n++;
        } else jt('radio-miss', {q: a.selector, want: v, options: els.map(radioText)});
      } else if (t === 'radio') {
        const labFor = e => (e.getRootNode() || document).querySelector('label[for="' + CSS.escape(e.id || '-') + '"]');
        const hit = els.find(e => norm((labFor(e) || {}).textContent || e.value) === norm(v));
        if (hit) {
          const target = vis(hit) ? hit : (labFor(hit) || hit);
          await clickInPage(target);
          if (!hit.checked) hit.click();
          jt('radio', {q: a.selector, want: v, checked: hit.checked});
          if (hit.checked) n++;
        }
      } else if (t === 'checkbox') {
        if (/^(yes|true|agree|i agree|1)$/i.test(v) && !el.checked) { el.click(); n++; }
      } else if (el.tagName === 'SELECT') {
        const o = [...el.options].find(o => norm(optionText(o)) === norm(v));
        if (o) { el.value = o.value; el.dispatchEvent(new Event('change', {bubbles: true, composed: true})); n++; }
      } else if (isCombo(el)) {
        if (await pickOption(el, v)) n++;
      } else { setValue(el, v); n++; }
      els.forEach(e => { e.dataset.jhAsked = '1'; });
    }
    return n;
  }
  // The step button (SmartRecruiters "Next"): pressed by the extension once a step
  // is filled (the owner, 2026-11-23: "I was needed to click on next"). Never Submit.
  function nextButton() {
    return deepAll('button, spl-button, [role="button"]').filter(vis)
      .find(b => /^(next|continue|next step)$/i.test(clean(b.innerText || b.textContent)));
  }
  // Errors the site shows next to fields after a press ("This field is required").
  function siteErrors() {
    const out = [];
    for (const e of deepAll('[role="alert"], [class*="error"], [class*="invalid"]')) {
      const t = clean(e.textContent);
      if (t && t.length < 160 && vis(e) && !out.includes(t)) out.push(t);
    }
    return out.slice(0, 6);
  }
  const markAsked = () => deepAll('[data-jhq]').forEach(e => { e.dataset.jhAsked = '1'; });

  const fileInputs = () => fields().filter(el => (el.type || '').toLowerCase() === 'file');
  const fileText = el => (el.name || '') + ' ' + (el.id || '') + ' ' + labelOf(el) + ' ' +
    clean((el.closest('.form-group, .application-question, li, div') || {}).innerText || '').slice(0, 80) + ' ' +
    upward(el).slice(1, 6).map(n => clean(n.getAttribute && n.getAttribute('label') || '')).join(' ');
  // The text around a file input, across shadow roots: SmartRecruiters has two
  // dropzones - "Easy Apply" (parses a file to pre-fill) and "Resume *".
  const sectionText = el => upward(el).slice(1).map(n => clean(n.textContent || '').slice(0, 60)).join(' | ');
  const isCover = el => /cover/i.test(fileText(el));

  // The form's cover-letter file field (Comeet: input#coverLetter "Attach Cover Letter").
  function coverInput() { return fileInputs().find(isCover) || null; }

  // Text boxes meant for a letter or a note (Comeet "Personal note" = textarea#inputNote,
  // Lever "Additional information", Greenhouse "Cover Letter" text).
  function noteFields() {
    return deepAll('textarea').filter(el => vis(el) &&
      !/captcha/i.test(el.name || '') &&
      /personal note|cover letter|additional information|anything else|message to|note|comment|interest|hiring manager|why do you want/i
        .test(labelOf(el) + ' ' + (el.name || '') + ' ' + (el.id || '')));
  }

  function setNote(el, text) {
    const max = el.maxLength > 0 ? el.maxLength : 0;
    setValue(el, max && text.length > max ? text.slice(0, max - 1).replace(/\s+\S*$/, '') + '…' : text);
  }

  function putFile(input, b64, name, mime) {
    const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
    const dt = new DataTransfer();
    dt.items.add(new File([bytes], name, {type: mime}));
    input.files = dt.files;
    input.dispatchEvent(new Event('input', {bubbles: true, composed: true}));
    input.dispatchEvent(new Event('change', {bubbles: true, composed: true}));
    return true;
  }

  function attachCover(b64, name, mime) {
    const el = coverInput();
    return el ? putFile(el, b64, name, mime) : false;
  }

  // The CV: a File built from bytes, set on the form's CV input (never the cover-letter one).
  function attachFile(b64, name, mime) {
    const inputs = fileInputs().filter(el => !isCover(el));
    if (!inputs.length) return false;
    const pick = inputs.find(el => /cv|resume|résumé/i.test((el.name || '') + ' ' + (el.id || '') + ' ' + labelOf(el))) ||
                 inputs.find(el => /\bresume\b|\bcv\b/i.test(sectionText(el)) && !/easy apply|autocomplete/i.test(sectionText(el).split(' | ').slice(0, 3).join(' '))) ||
                 inputs[0];
    return putFile(pick, b64, name, mime);
  }

  function submitButton() {
    const btns = deepAll('button, input[type="submit"], a.postings-btn').filter(vis);
    const by = re => btns.find(b => re.test(clean(b.innerText || b.value)));
    return document.querySelector('#btn-submit') && vis(document.querySelector('#btn-submit')) ? document.querySelector('#btn-submit')
      : by(/^submit application$/i) || by(/^submit$/i) || by(/submit|send application|apply now/i)
      // Not a step button: SmartRecruiters' first page ends in "Next" (2026-11-23).
      || btns.find(b => (b.type || '').toLowerCase() === 'submit' && !/^(next|back|continue|previous|save)/i.test(clean(b.innerText || b.value)));
  }

  // Checks meant for a person, visible on this page right now.
  function personCheck() {
    for (const f of document.querySelectorAll('iframe')) {
      const s = f.src || '';
      const big = f.getBoundingClientRect().height > 100;
      if (/captcha-delivery\.com/.test(s)) return 'DataDome';
      if (/hcaptcha\.com/.test(s) && /frame=challenge/.test(s) && big) return 'hCaptcha puzzle';
      if (/recaptcha\/(api2|enterprise)\/bframe/.test(s) && big) return 'reCAPTCHA puzzle';
    }
    return '';
  }

  const THANKS = /thank you for (applying|your application|submitting)|application (has been |was )?(received|submitted)|we('ve| have) received your application/i;
  const confirmed = () => THANKS.test(document.body ? document.body.innerText : '');

  window.JHFill = {siteErrors, unlabeled: () => unlabeledGroups, fields, hasForm, fill, fillBasics, questions, applyAnswers, markAsked, openRadioGroups, nextButton, clickInPage, attachFile, attachCover, coverInput, noteFields, setNote, submitButton, personCheck, confirmed};
})();
