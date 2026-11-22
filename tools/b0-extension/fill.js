// B0 fill engine: fills an application form from the answers Job Hunter prepared
// (jobs.apply_prepared: [{label, value}], read by apply_engine._SNAPSHOT_JS) and
// attaches the CV. No browser-extension APIs here: the Android WebView test app
// injects this same file. Labels are read exactly as _SNAPSHOT_JS reads them, so
// a prepared label meets its own field again.
(() => {
  if (window.JHFill) return;
  const vis = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const norm = s => clean(s).replace(/\s*[*✱]\s*$/, '').toLowerCase();
  const labelOf = el => {
    let t = '';
    if (el.id) { const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]'); if (l) t = l.innerText; }
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
    el.dispatchEvent(new Event('input', {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
    el.dispatchEvent(new Event('blur', {bubbles: true}));
  };
  const optionText = o => clean(o.text || o.innerText);

  function fields() {
    return [...document.querySelectorAll('input, select, textarea')].filter(el => {
      const t = (el.type || '').toLowerCase();
      return !['hidden', 'submit', 'button', 'image', 'reset', 'password'].includes(t) &&
             !(el.name || '').match(/captcha/i) && (vis(el) || el.tagName === 'SELECT' || t === 'file');
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

  const fileInputs = () => fields().filter(el => (el.type || '').toLowerCase() === 'file');
  const fileText = el => (el.name || '') + ' ' + (el.id || '') + ' ' + labelOf(el) + ' ' +
    clean((el.closest('.form-group, .application-question, li, div') || {}).innerText || '').slice(0, 80);
  const isCover = el => /cover/i.test(fileText(el));

  // The form's cover-letter file field (Comeet: input#coverLetter "Attach Cover Letter").
  function coverInput() { return fileInputs().find(isCover) || null; }

  // Text boxes meant for a letter or a note (Comeet "Personal note" = textarea#inputNote,
  // Lever "Additional information", Greenhouse "Cover Letter" text).
  function noteFields() {
    return [...document.querySelectorAll('textarea')].filter(el => vis(el) &&
      !/captcha/i.test(el.name || '') &&
      /personal note|cover letter|additional information|anything else|message to|note|comment/i
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
    input.dispatchEvent(new Event('input', {bubbles: true}));
    input.dispatchEvent(new Event('change', {bubbles: true}));
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
    const pick = inputs.find(el => /cv|resume|résumé/i.test((el.name || '') + ' ' + (el.id || '') + ' ' + labelOf(el))) || inputs[0];
    return putFile(pick, b64, name, mime);
  }

  function submitButton() {
    const btns = [...document.querySelectorAll('button, input[type="submit"], a.postings-btn')].filter(vis);
    const by = re => btns.find(b => re.test(clean(b.innerText || b.value)));
    return document.querySelector('#btn-submit') && vis(document.querySelector('#btn-submit')) ? document.querySelector('#btn-submit')
      : by(/^submit application$/i) || by(/^submit$/i) || by(/submit|send application|apply now/i)
      || btns.find(b => (b.type || '').toLowerCase() === 'submit');
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

  window.JHFill = {fields, hasForm, fill, attachFile, attachCover, coverInput, noteFields, setNote, submitButton, personCheck, confirmed};
})();
