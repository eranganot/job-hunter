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

  async function report(outcome, siteStatus, detail, extra) {
    if (reported) return; reported = true;
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
      bar.style.cssText = 'position:fixed;z-index:2147483647;left:12px;right:12px;bottom:12px;padding:12px 14px;border-radius:10px;' +
        'font:14px/1.4 -apple-system,Segoe UI,Arial,sans-serif;background:#0f172a;color:#fff;box-shadow:0 6px 24px rgba(0,0,0,.35)';
      document.documentElement.appendChild(bar);
    }
    bar.innerHTML = '';
    const p = document.createElement('div'); p.textContent = 'Job Hunter B0 · ' + text; bar.appendChild(p);
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
    if (!task || e.source !== window || !d || d.__jh !== 'net' || !submittedAt) return;
    if (SYSTEM === 'Comeet' && /careers-api/.test(d.url) && /apply/.test(d.url)) {
      if (d.status >= 200 && d.status < 300) report('sent', d.status, 'Comeet accepted the application', {confirmed: true});
      else if (d.status === 423) report('blocked', 423, 'Comeet refused it (423)');
      else report('unknown', d.status, 'Comeet answered ' + d.status);
    }
  });

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
    // A page reached after Submit (Lever's /thanks, a thank-you page).
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
    // Wait for a form in THIS frame (Comeet's sits in a comeet.co frame).
    for (let i = 0; i < 30 && !JHFill.hasForm(); i++) {
      const c = JHFill.personCheck();
      if (c && TOP) { checkSeen = c; banner(c + ' is showing before the form - solve it yourself, then the form gets filled.'); }
      await wait(1000);
    }
    if (!JHFill.hasForm()) return;
    if (await ask({type: 'claim'}) !== true) return;     // one frame per tab does the work

    banner('Filling ' + (task.prepared || []).length + ' prepared answers…');
    const res = JHFill.fill(task.prepared || []);
    let cv = false;
    const file = await ask({type: 'cv', jobId: task.jobId});
    if (file && file.b64) cv = JHFill.attachFile(file.b64, file.name, file.mime);
    await wait(1500);
    JHFill.fill(task.prepared || []);                     // Comeet shows its questions once the CV is in
    const line = 'Filled ' + res.filled + ' of ' + (task.prepared || []).length + ' answers' + (cv ? ' + CV' : ', no CV attached') +
                 (res.missing.length ? '. Not found: ' + res.missing.slice(0, 4).join('; ') : '') + '.';

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
    pressForMeImpl = async () => {
      if (submittedAt) return;
      const b = JHFill.submitButton();
      if (!b) { banner(line + ' No Submit button found - press it yourself.', 'ready'); return; }
      submittedAt = Date.now(); pressedBy = 'extension';
      await ask({type: 'submitted', pressedBy: 'extension'});
      b.click();
      banner(line + ' Submitted by the extension, waiting for the site…');
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

  if (window.JHFill) run().catch(e => console.warn('Job Hunter B0:', e));
})();
