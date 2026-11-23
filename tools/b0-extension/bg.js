// B0 background: holds the device token, opens each job in its own tab, hands
// the tab its prepared answers and CV, and reports each attempt to Job Hunter.
// Calls go from here (not from the job page), so the employer's page never sees
// the token and the browser's cross-site rules don't apply.
const DEFAULT_SERVER = 'https://job-hunter.work';

const cfg = async () => Object.assign({server: DEFAULT_SERVER, token: '', mode: 'A'},
  await chrome.storage.local.get(['server', 'token', 'mode']));
const tasks = async () => (await chrome.storage.session.get('tasks')).tasks || {};
const saveTasks = t => chrome.storage.session.set({tasks: t});

async function api(path, opts) {
  const c = await cfg();
  const r = await fetch(c.server.replace(/\/$/, '') + path, Object.assign({
    headers: Object.assign({'Content-Type': 'application/json'}, c.token ? {Authorization: 'Bearer ' + c.token} : {}),
  }, opts || {}));
  return r;
}

const b64 = buf => {
  let s = ''; const a = new Uint8Array(buf);
  for (let i = 0; i < a.length; i += 0x8000) s += String.fromCharCode.apply(null, a.subarray(i, i + 0x8000));
  return btoa(s);
};

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  (async () => {
    const tabId = sender.tab && sender.tab.id;
    if (msg.type === 'pair') {
      await chrome.storage.local.set({server: msg.server || DEFAULT_SERVER});
      const r = await api('/api/device/pair', {method: 'POST', body: JSON.stringify({code: msg.code, label: 'Chrome (B0)'})});
      const d = await r.json().catch(() => ({}));
      if (r.ok && d.token) { await chrome.storage.local.set({token: d.token}); return reply({ok: true}); }
      return reply({ok: false, error: d.error || ('HTTP ' + r.status)});
    }
    if (msg.type === 'add') {
      const r = await api('/api/device/jobs/add', {method: 'POST', body: JSON.stringify({url: msg.url})});
      if (r.status === 401) { await chrome.storage.local.remove('token'); return reply({error: 'unpaired'}); }
      return reply(await r.json().catch(() => ({error: 'HTTP ' + r.status})));
    }
    if (msg.type === 'jobs') {
      const r = await api('/api/device/jobs');
      if (r.status === 401) { await chrome.storage.local.remove('token'); return reply({error: 'unpaired'}); }
      return reply(await r.json());
    }
    if (msg.type === 'start') {
      const c = await cfg();
      const tab = await chrome.tabs.create({url: msg.job.url, active: true});
      const t = await tasks();
      t[tab.id] = {jobId: msg.job.id, prepared: msg.job.prepared || [], system: msg.job.system,
                   applicant: msg.applicant || {},
                   mode: c.mode, claimed: false, submittedAt: 0, reported: false};
      await saveTasks(t);
      return reply({ok: true});
    }
    const t = await tasks();
    const task = tabId != null ? t[tabId] : null;
    if (msg.type === 'task?') {
      if (!task || task.reported) return reply(null);
      const copy = Object.assign({}, task);
      // A new page in the tab before Submit (a reload, Comeet opening its form,
      // a redirect) fills again: the frame that filled the old page is gone.
      if (sender.frameId === 0 && !task.submittedAt && task.claimed) { task.claimed = false; await saveTasks(t); }
      return reply(copy);
    }
    if (!task) return reply(null);
    if (msg.type === 'claim') {                       // the first frame with a form does the work
      if (task.claimed) return reply(false);
      task.claimed = true; await saveTasks(t); return reply(true);
    }
    if (msg.type === 'cover') {
      const r = await api('/api/device/jobs/' + encodeURIComponent(msg.jobId) + '/cover-letter');
      return reply(await r.json().catch(() => ({error: 'HTTP ' + r.status})));
    }
    if (msg.type === 'coverpdf') {
      const r = await api('/api/device/jobs/' + encodeURIComponent(msg.jobId) + '/cover-letter?format=pdf');
      if (!r.ok) return reply(null);
      const cd = r.headers.get('Content-Disposition') || '';
      const star = (cd.match(/filename\*=UTF-8''([^;]+)/i) || [])[1];
      const name = star ? decodeURIComponent(star) : ((cd.match(/filename="([^"]+)"/) || [])[1] || 'Cover Letter.pdf');
      return reply({b64: b64(await r.arrayBuffer()), name, mime: 'application/pdf'});
    }
    if (msg.type === 'cv') {
      const r = await api('/api/device/cv?job=' + encodeURIComponent(msg.jobId));
      if (!r.ok) return reply(null);
      const cd = r.headers.get('Content-Disposition') || '';
      const star = (cd.match(/filename\*=UTF-8''([^;]+)/i) || [])[1];
      const name = star ? decodeURIComponent(star) : ((cd.match(/filename="([^"]+)"/) || [])[1] || 'CV.pdf');
      return reply({b64: b64(await r.arrayBuffer()), name, mime: r.headers.get('Content-Type') || 'application/pdf'});
    }
    if (msg.type === 'submitted') {
      task.submittedAt = Date.now(); task.pressedBy = msg.pressedBy; await saveTasks(t); return reply(true);
    }
    if (msg.type === 'report') {
      if (task.reported) return reply(false);
      task.reported = true; await saveTasks(t);
      const r = await api('/api/device/attempt', {method: 'POST', body: JSON.stringify(msg.attempt)});
      const log = (await chrome.storage.local.get('log')).log || [];
      log.unshift(Object.assign({at: new Date().toISOString(), http: r.status}, msg.attempt));
      await chrome.storage.local.set({log: log.slice(0, 100)});
      return reply(r.ok);
    }
    reply(null);
  })();
  return true;
});

chrome.tabs.onRemoved.addListener(async tabId => {
  const t = await tasks();
  if (t[tabId]) { delete t[tabId]; await saveTasks(t); }
});
