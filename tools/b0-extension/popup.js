const B0_SYSTEMS = ['Comeet', 'Lever', 'SmartRecruiters'];
const $ = id => document.getElementById(id);
const send = msg => new Promise(res => chrome.runtime.sendMessage(msg, res));

async function show() {
  const c = Object.assign({server: 'https://job-hunter.work', token: '', mode: 'A'},
    await chrome.storage.local.get(['server', 'token', 'mode']));
  $('server').value = c.server;
  $('codeLink').href = c.server.replace(/\/$/, '') + '/api/admin/device-code';
  $('pair').hidden = !!c.token; $('main').hidden = !c.token;
  if (!c.token) return;
  for (const r of document.querySelectorAll('input[name=mode]')) {
    r.checked = r.value === c.mode;
    r.onchange = () => chrome.storage.local.set({mode: r.value});
  }
  const log = (await chrome.storage.local.get('log')).log || [];
  const n = o => log.filter(x => x.outcome === o).length;
  $('tally').textContent = `${n('sent')} sent, ${n('blocked')} blocked, ${n('nothing_sent')} nothing sent, ${n('unknown')} unclear`;
  const d = await send({type: 'jobs'});
  if (!d || d.error) { if (d && d.error === 'unpaired') return show(); $('jobs').innerHTML = '<span class="err">Could not load your jobs.</span>'; return; }
  const list = (d.jobs || []).filter(j => $('all').checked || B0_SYSTEMS.includes(j.system));
  $('jobs').innerHTML = list.length ? '' : '<span class="muted">No approved Comeet, Lever or SmartRecruiters jobs. Approve some in the app, then press Apply now on each so Noa prepares the answers (SmartRecruiters cannot be prepared: its check stops the server first).</span>';
  for (const j of list) {
    const row = document.createElement('div'); row.className = 'job';
    const info = document.createElement('div');
    const t = document.createElement('b'); t.textContent = `${j.company} · ${j.title}`;
    const m = document.createElement('span'); m.className = 'muted';
    m.textContent = `${j.system} · ${j.prepared.length ? j.prepared.length + ' answers prepared' : 'no answers prepared: you fill the form, then "Submit for me"'}`;
    info.append(t, m);
    const inTest = B0_SYSTEMS.includes(j.system);
    if (!inTest) m.textContent = `${j.system} · not in this test: the extension can't fill it`;
    const b = document.createElement('button'); b.textContent = 'Open';
    if (!j.prepared.length || !inTest) b.className = 'quiet';
    if (!inTest) b.disabled = true;
    b.onclick = async () => { await send({type: 'start', job: j}); window.close(); };
    row.append(info, b);
    $('jobs').appendChild(row);
  }
}

$('pairBtn').onclick = async () => {
  $('pairMsg').textContent = '';
  const r = await send({type: 'pair', code: $('code').value.trim(), server: $('server').value.trim()});
  if (r && r.ok) show(); else $('pairMsg').textContent = (r && r.error) || 'Could not connect.';
};
$('all').onchange = show;
$('unpair').onclick = async e => { e.preventDefault(); await chrome.storage.local.remove('token'); show(); };
show();
