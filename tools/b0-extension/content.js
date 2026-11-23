// B0 orchestration in each frame of a job page the popup opened: open the form,
// fill it, press Submit (run B) or wait for Eran's press (run A), read the
// site's answer and report it. Every attempt is reported once.
(() => {
  const host = location.host;
  const SYSTEM = /comeet/.test(host) ? 'Comeet' : /lever\.co/.test(host) ? 'Lever'
               : /smartrecruiters/.test(host) ? 'SmartRecruiters' : 'Other';
  const TOP = window === window.top;
  const ask = msg => new Promise(res => { try { chrome.runtime.sendMessage(msg, res); } catch (e) { res(null); } });
  const wait = ms => new Promise(r => setTimeout(r, ms));
  let task = null, reported = false, submittedAt = 0, pressedBy = 'user', checkSeen = '';
  let pressForMe = () => {};
  // What the extension did on this page, step by step, sent to Job Hunter so a
  // failure can be read instead of guessed (2026-11-23: City and SmartRecruiters'
  // second step failed on Eran's runs but passed on every stand-in).
  const traceBuf = [];
  let traceTimer = null;
  const trace = (step, data) => {
    traceBuf.push({t: Date.now(), step, data: data === undefined ? null : data});
    if (window.JHTrace && window.JHTrace.length) traceBuf.push(...window.JHTrace.splice(0));
    clearTimeout(traceTimer);
    traceTimer = setTimeout(flushTrace, 800);
  };
  const flushTrace = () => {
    if (!task || !traceBuf.length) return;
    if (window.JHTrace && window.JHTrace.length) traceBuf.push(...window.JHTrace.splice(0));
    ask({type: 'trace', jobId: task.jobId, url: location.href, top: TOP, version: chrome.runtime.getManifest().version,
         events: traceBuf.splice(0)});
  };

  async function report(outcome, siteStatus, detail, extra) {
    if (reported) return; reported = true;
    if (outcome === 'nothing_sent') laterSend();
    await ask({type: 'report', attempt: Object.assign({job_id: task.jobId, outcome, site_status: String(siteStatus || ''),
      detail: (checkSeen ? '[' + checkSeen + ' shown] ' : '') + (detail || ''), pressed_by: pressedBy,
      device: 'chrome', url: location.href}, extra || {})});
    banner('Reported: ' + outcome + (siteStatus ? ' (' + siteStatus + ')' : '') + '. You can close this tab.', 'done');
  }

  // ── The banner: what happened, and Eran's own verdict when detection can't tell ──
  let bar;
  function banner(text, state) {
    if (!bar) {
      bar = document.createElement('div');
      bar.setAttribute('data-jh-bar', '');               // never taken for the site's own buttons
      // Top-right and narrow: at the bottom, full width, it covered Fullpath's Submit
      // button (the owner, 2026-11-23). It can be folded to a small tab.
      bar.style.cssText = 'position:fixed;z-index:2147483647;right:12px;top:12px;max-width:380px;padding:10px 12px;border-radius:10px;' +
        'font:13px/1.4 -apple-system,Segoe UI,Arial,sans-serif;background:#0f172a;color:#fff;box-shadow:0 6px 24px rgba(0,0,0,.35)';
      document.documentElement.appendChild(bar);
    }
    bar.innerHTML = '';
    const fold = document.createElement('button');
    fold.textContent = '–'; fold.title = 'Fold';
    fold.style.cssText = 'float:right;margin:-4px -4px 0 8px;border:0;background:transparent;color:#94a3b8;cursor:pointer;font-size:16px';
    const p = document.createElement('div'); p.textContent = 'Job Hunter B0 · ' + text;
    fold.onclick = () => { const f = p.style.display !== 'none'; p.style.display = f ? 'none' : ''; fold.textContent = f ? 'Job Hunter ▸' : '–';
                           [...bar.querySelectorAll('button')].forEach(b => { if (b !== fold) b.style.display = f ? 'none' : ''; }); };
    bar.appendChild(fold); bar.appendChild(p);
    if (state === 'ready' && !submittedAt) {
      // Run B where nothing was prepared (SmartRecruiters): Eran fills, the extension presses Submit.
      const b = document.createElement('button'); b.textContent = 'Submit for me';
      b.style.cssText = 'margin-top:8px;padding:6px 10px;border-radius:8px;border:0;cursor:pointer;background:#22c55e;color:#0f172a';
      b.onclick = () => pressForMe();
      bar.appendChild(b);
    }
    if (state !== 'done' && submittedAt) {
      const row = document.createElement('div'); row.style.marginTop = '8px';
      for (const [label, outcome] of [['It went through', 'sent'], ['A check stopped it', 'blocked'], ['Nothing was sent', 'nothing_sent']]) {
        const b = document.createElement('button'); b.textContent = label;
        b.style.cssText = 'margin-right:8px;padding:6px 10px;border-radius:8px;border:0;cursor:pointer;background:#e2e8f0;color:#0f172a';
        b.onclick = () => report(outcome, '', 'Eran said: ' + label);
        row.appendChild(b);
      }
      bar.appendChild(row);
    }
  }

  // ── The site's answer ──
  window.addEventListener('message', e => {
    const d = e.data;
    if (!task || e.source !== window || !d || d.__jh !== 'net') return;
    if (SYSTEM === 'Comeet' && /careers-api/.test(d.url) && /apply/.test(d.url)) {
      // The click listener on Submit can miss (the page may redraw its button):
      // Comeet's own apply call is the proof that Submit was pressed.
      if (!submittedAt) { submittedAt = Date.now(); pressedBy = 'user'; }
      if (d.status >= 200 && d.status < 300) report('sent', d.status, 'Comeet accepted the application', {confirmed: true});
      else if (d.status === 423) report('blocked', 423, 'Comeet refused it (423)');
      else report('unknown', d.status, 'Comeet answered ' + d.status);
    }
  });

  // After "Nothing was sent": Eran may press Submit himself (ServiceNow, 2026-11-23).
  // A thank-you page in the next 10 minutes is reported as his send.
  async function laterSend() {
    for (let i = 0; i < 600; i++) {
      await wait(1000);
      if (JHFill.confirmed() || /\/thanks/.test(location.pathname)) {
        trace('later-send', {url: location.pathname});
        await ask({type: 'report', attempt: {job_id: task.jobId, outcome: 'sent', site_status: 'page', pressed_by: 'user',
          detail: 'Thank-you page after Eran pressed Submit himself', device: 'chrome', url: location.href, confirmed: true}});
        banner('Reported: sent (you pressed Submit). You can close this tab.', 'done');
        return;
      }
    }
  }

  async function watchAfterSubmit() {
    for (let i = 0; i < 240 && !reported; i++) {         // up to 4 minutes: a puzzle takes a person time
      const c = JHFill.personCheck();
      if (c && c !== checkSeen) { checkSeen = c; banner(c + ' is showing - solve it yourself, then the application goes on.'); }
      if (JHFill.confirmed()) { report('sent', 'page', 'The page thanked for the application', {confirmed: true}); return; }
      await wait(1000);
    }
    if (!reported) banner('No clear answer from the site. Tell me what happened:');
  }

  async function run() {
    // The page can load before the popup's task is stored: ask a few times.
    for (let i = 0; i < 10 && !task; i++) { task = await ask({type: 'task?'}); if (!task) await wait(500); }
    if (!task) return;
    // A page reached after Submit (Lever's /thanks, a thank-you page) - also when
    // the click on Submit was not seen, as long as this tab already filled a form.
    if (TOP && !task.submittedAt && task.claimed && (/\/thanks/.test(location.pathname) || JHFill.confirmed())) {
      task.submittedAt = Date.now(); task.pressedBy = 'user';
    }
    if (task.submittedAt && TOP) {
      submittedAt = task.submittedAt; pressedBy = task.pressedBy || 'user';
      await wait(1500);
      if (JHFill.confirmed() || /\/thanks/.test(location.pathname)) { report('sent', 'page', 'Thank-you page: ' + location.pathname, {confirmed: true}); return; }
      watchAfterSubmit(); return;
    }
    // Lever: the form is on <posting>/apply.
    if (TOP && SYSTEM === 'Lever' && !/\/apply/.test(location.pathname)) {
      location.href = location.href.split('?')[0].replace(/\/$/, '') + '/apply'; return;
    }
    // SmartRecruiters: the way in is the "I'm interested" link.
    if (TOP && SYSTEM === 'SmartRecruiters' && !/oneclick-ui/.test(location.href)) {
      const a = document.querySelector('a[href*="oneclick-ui"]');
      if (a) { location.href = a.href; return; }
    }
    // Comeet: the form frame appears only after "APPLY FOR THIS JOB" (the owner, 2026-11-22).
    if (TOP && SYSTEM === 'Comeet' && !document.querySelector('iframe[src*="comeet.co"][src*="apply"]')) {
      for (let i = 0; i < 10; i++) {
        const b = [...document.querySelectorAll('button, a')].find(e => /apply for this job|^apply now$|^apply$/i.test((e.innerText || '').trim()));
        if (b) { b.click(); break; }
        await wait(500);
      }
    }
    // Wait for a form in THIS frame (Comeet's sits in a comeet.co frame).
    for (let i = 0; i < 30 && !JHFill.hasForm(); i++) {
      const c = JHFill.personCheck();
      if (c && TOP) { checkSeen = c; banner(c + ' is showing before the form - solve it yourself, then the form gets filled.'); }
      await wait(1000);
    }
    if (!JHFill.hasForm()) {                             // never silent again (2026-11-23)
      if (TOP) banner('No application form found on this page. Open the form yourself - the extension fills it when it appears.');
      for (let i = 0; i < 120 && !JHFill.hasForm(); i++) await wait(1000);
      if (!JHFill.hasForm()) return;
    }
    if (await ask({type: 'claim'}) !== true) return;     // one frame per tab does the work

    banner('Filling ' + (task.prepared || []).length + ' prepared answers…');
    trace('form', {system: SYSTEM, fields: JHFill.fields().length, prepared: (task.prepared || []).length,
                   applicant: Object.keys(task.applicant || {}).filter(k => (task.applicant || {})[k])});
    const res = JHFill.fill(task.prepared || []);
    const basicsOpts = {localPhone: SYSTEM === 'SmartRecruiters'};
    let basics = JHFill.fillBasics(task.applicant, basicsOpts);
    trace('basics', {filled: basics});
    let cv = false;
    const file = await ask({type: 'cv', jobId: task.jobId});
    if (file && file.b64) cv = JHFill.attachFile(file.b64, file.name, file.mime);
    await wait(1500);
    JHFill.fill(task.prepared || []);                     // Comeet shows its questions once the CV is in
    basics += JHFill.fillBasics(task.applicant, basicsOpts);  // and its contact fields
    // The cover letter (the owner, 2026-11-22): as a PDF where the form takes a file,
    // and in the note box - its opening paragraph next to the PDF, the whole letter without one.
    let letter = '';
    const notes = JHFill.noteFields();
    if (JHFill.coverInput() || notes.length) {
      banner('Writing the cover letter…');
      const cl = await ask({type: 'cover', jobId: task.jobId});
      if (cl && cl.letter) {
        let pdf = false;
        if (JHFill.coverInput()) {
          const f = await ask({type: 'coverpdf', jobId: task.jobId});
          if (f && f.b64) pdf = JHFill.attachCover(f.b64, f.name, f.mime);
        }
        // The owner, 2026-11-23: "if you can't write anything useful - keep it empty".
        // Next to the PDF the note box gets the note or nothing.
        for (const n of notes) { const t = pdf ? (cl.note || '') : cl.letter; if (t) JHFill.setNote(n, t); }
        letter = pdf ? ' + cover letter PDF' + (notes.length ? ' and its opening in the note' : '') : (notes.length ? ' + cover letter in the note' : '');
      } else letter = ' (no cover letter: ' + ((cl && cl.error) || 'not written') + ')';
    }
    // The questions nothing covered yet, answered by Job Hunter (saved answers,
    // standing instructions, the model) - the same rules as the apply engine.
    async function answerRound() {
      const qs = await JHFill.questions();
      trace('questions', qs.map(q => ({label: q.label.slice(0, 80), type: q.type, options: q.options.length, required: q.required})));
      if (!qs.length) return {n: 0, unsure: JHFill.unlabeled() ? [JHFill.unlabeled() + ' Yes/No question(s) I could not read'] : []};
      banner('Answering ' + qs.length + ' question' + (qs.length > 1 ? 's' : '') + '…');
      const r = await ask({type: 'answers', jobId: task.jobId, questions: qs,
                           page_text: (document.body.innerText || '').slice(0, 3000)});
      trace('server-answers', r ? {answers: (r.answers || []).map(a => ({q: a.selector, v: String(a.value).slice(0, 60)})),
                                   unsure: r.unsure, error: r.error} : 'no reply');
      const n = r && r.answers ? await JHFill.applyAnswers(r.answers) : 0;
      trace('applied', {n});
      JHFill.markAsked();
      const blind = JHFill.unlabeled();
      return {n, unsure: ((r && r.unsure) || (r && r.error ? ['(' + r.error + ')'] : []))
                           .concat(blind ? [blind + ' Yes/No question' + (blind > 1 ? 's' : '') + ' I could not read'] : [])
                           .concat(((r && r.drafts) || []).map(l => 'check: ' + l))};
    }
    const q1 = await answerRound();
    // Press the step's "Next" once it is filled; the next step is filled by the watcher.
    let nextPressed = null;
    async function pressNext() {
      const nb = JHFill.nextButton();
      if (!nb || nb === nextPressed || submittedAt) return;
      await wait(1500);
      if (JHFill.nextButton() !== nb) return;
      nextPressed = nb;
      const how = await JHFill.clickInPage(nb);
      trace('next', {how});
    }
    await pressNext();
    const answered = n => n.n ? ', ' + n.n + ' questions answered' : '';
    const leftOver = n => n.unsure.length ? ' Yours to answer: ' + n.unsure.slice(0, 3).join('; ') + '.' : '';
    const line = 'Filled ' + res.filled + ' of ' + (task.prepared || []).length + ' prepared answers' + answered(q1) +
                 (basics ? ', ' + basics + ' contact fields' : '') + (cv ? ' + CV' : ', no CV attached') + letter +
                 (res.missing.length ? '. Not found: ' + res.missing.slice(0, 4).join('; ') : '') + '.' + leftOver(q1);

    let pressForMeImpl = null;
    const btn = JHFill.submitButton();
    const mark = async who => {
      if (submittedAt) return;
      submittedAt = Date.now(); pressedBy = who;
      await ask({type: 'submitted', pressedBy: who});
      banner(line + ' Submitted (' + (who === 'extension' ? 'by the extension' : 'by you') + '), waiting for the site…');
      watchAfterSubmit();
    };
    if (btn) btn.addEventListener('click', () => mark('user'), {capture: true, once: true});
    // A form in steps (SmartRecruiters: "Next" -> "Preliminary questions") shows
    // new fields without loading a page: fill each step as it appears.
    (async () => {
      let seenBtn = btn;
      for (let i = 0; i < 600 && !submittedAt && !reported; i++) {
        await wait(2000);
        // Sent without the press being seen (Wix, 2026-11-23: Eran sent it, nothing was recorded).
        if (JHFill.confirmed()) { submittedAt = Date.now(); pressedBy = 'user'; trace('thanked-unseen', {url: location.pathname});
          report('sent', 'page', 'The page thanked for the application', {confirmed: true}); return; }
        // fields() already keeps only what is on screen, hidden-input radios included
        // (a follow-up question that appears after an answer: ServiceNow's "relocating
        // at your own expense?" once "Yes" is chosen, 2026-11-23).
        const fresh = JHFill.openRadioGroups() > 0 || JHFill.fields().some(el => !el.dataset.jhAsked && !el.dataset.jhq && el.type !== 'file' &&
          !((el.type === 'checkbox' || el.type === 'radio') ? el.checked : (el.value || '').trim()));
        await pressNext();
        if (!fresh) continue;
        trace('new-step', {fields: JHFill.fields().length});
        const b = JHFill.fillBasics(task.applicant, basicsOpts);
        const q = await answerRound();
        JHFill.markAsked();
        const nb = JHFill.submitButton();
        if (nb && nb !== seenBtn) { seenBtn = nb; nb.addEventListener('click', () => mark('user'), {capture: true, once: true}); }
        if (b || q.n || q.unsure.length)
          banner('Next step: ' + (b ? b + ' contact fields, ' : '') + q.n + ' questions answered.' + leftOver(q) +
                 (task.mode === 'B' ? ' Check it, then "Submit for me".' : ' Check it and press Submit yourself.'), task.mode === 'B' ? 'ready' : '');
      }
    })();
    pressForMeImpl = async () => {
      if (submittedAt) return;
      const b = JHFill.submitButton();
      if (!b) { banner(line + ' No Submit button found - press it yourself.', 'ready'); return; }
      submittedAt = Date.now(); pressedBy = 'extension';
      await ask({type: 'submitted', pressedBy: 'extension'});
      // Through the page world, like SmartRecruiters' options and Yes/No choices: on
      // ServiceNow (0.7.2, 2026-11-23) the extension's own click on Submit did nothing
      // and Eran pressed it himself. The isolated click stays as the fallback.
      const r = b.getBoundingClientRect();
      const how = await JHFill.clickInPage(b);
      if (how === 'no-page-world') b.click();
      trace('press', {how, tag: b.tagName.toLowerCase(), text: (b.innerText || b.value || '').trim().slice(0, 40),
                      at: [Math.round(r.left), Math.round(r.top)], url: location.pathname});
      banner(line + ' Submitted by the extension, waiting for the site…');
      setTimeout(() => {
        if (reported) return;
        const errs = JHFill.siteErrors();
        trace('after-press', {url: location.pathname, errors: errs, thanked: JHFill.confirmed()});
        if (errs.length) banner('The site did not take it: ' + errs.slice(0, 3).join('; ') + '. Fix that and press Submit yourself.');
      }, 6000);
      watchAfterSubmit();
    };
    pressForMe = pressForMeImpl;
    if (task.mode === 'B' && (task.prepared || []).length) {
      banner(line + ' The extension presses Submit in 5 seconds - check the form now.');
      await wait(5000);
      await pressForMeImpl();
    } else if (task.mode === 'B') {
      banner(line + ' Nothing was prepared for this job: fill the form, then press "Submit for me".', 'ready');
    } else {
      banner(line + ' Check the form and press Submit yourself.');
    }
  }

  if (window.JHFill) run().catch(e => { console.warn('Job Hunter B0:', e); trace('error', String(e && e.stack || e)); flushTrace(); });
})();
