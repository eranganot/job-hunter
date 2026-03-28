"""HTML page templates for Job Hunter."""


LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>""" + _COMMON_HEAD + """
  <title>Job Hunter — Sign In</title>
</head>
<body class="min-h-screen bg-gradient-to-br from-slate-900 via-blue-950 to-blue-900 flex items-center justify-center p-4">
<div class="w-full max-w-md fade">
  <div class="text-center mb-8">
    <div class="inline-flex items-center justify-center w-16 h-16 bg-blue-600 rounded-2xl mb-4 shadow-xl">
      <span class="text-3xl">🎯</span>
    </div>
    <h1 class="text-3xl font-bold text-white">Job Hunter</h1>
    <p class="text-blue-300 mt-1 text-sm">Your AI-powered job search assistant</p>
  </div>

  <div class="bg-white rounded-2xl shadow-2xl p-8">
    <h2 class="text-xl font-bold text-slate-900 mb-6">Sign in to your account</h2>

    {error_block}

    <form method="POST" action="/login" class="space-y-4">
      <div>
        <label class="label" for="email">Email</label>
        <input class="input" type="email" name="email" id="email" placeholder="you@example.com" required autofocus/>
      </div>
      <div>
        <label class="label" for="password">Password</label>
        <input class="input" type="password" name="password" id="password" placeholder="••••••••" required/>
      </div>
      <button type="submit" class="btn btn-primary w-full mt-2">Sign in →</button>
    </form>

    <p class="text-center text-sm text-slate-500 mt-6">
      Don't have an account?
      <a href="/register" class="text-blue-600 font-semibold hover:underline">Create one</a>
    </p>
  </div>
</div>
</body>
</html>"""

REGISTER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>""" + _COMMON_HEAD + """
  <title>Job Hunter — Create Account</title>
</head>
<body class="min-h-screen bg-gradient-to-br from-slate-900 via-blue-950 to-blue-900 flex items-center justify-center p-4">
<div class="w-full max-w-md fade">
  <div class="text-center mb-8">
    <div class="inline-flex items-center justify-center w-16 h-16 bg-blue-600 rounded-2xl mb-4 shadow-xl">
      <span class="text-3xl">🎯</span>
    </div>
    <h1 class="text-3xl font-bold text-white">Job Hunter</h1>
    <p class="text-blue-300 mt-1 text-sm">Let's get you set up</p>
  </div>

  <div class="bg-white rounded-2xl shadow-2xl p-8">
    <h2 class="text-xl font-bold text-slate-900 mb-6">Create your account</h2>

    {error_block}

    <form method="POST" action="/register" class="space-y-4">
      <div>
        <label class="label" for="name">Full name</label>
        <input class="input" type="text" name="name" id="name" placeholder="Eran Ganot" required autofocus/>
      </div>
      <div>
        <label class="label" for="email">Work email</label>
        <input class="input" type="email" name="email" id="email" placeholder="you@example.com" required/>
      </div>
      <div>
        <label class="label" for="password">Password</label>
        <input class="input" type="password" name="password" id="password" placeholder="At least 8 characters" required minlength="8"/>
      </div>
      <div>
        <label class="label" for="password2">Confirm password</label>
        <input class="input" type="password" name="password2" id="password2" placeholder="••••••••" required minlength="8"/>
      </div>
      <button type="submit" class="btn btn-primary w-full mt-2">Create account →</button>
    </form>

    <p class="text-center text-sm text-slate-500 mt-6">
      Already have an account?
      <a href="/login" class="text-blue-600 font-semibold hover:underline">Sign in</a>
    </p>
  </div>
</div>
</body>
</html>"""

def error_block(msg: str) -> str:
    if not msg:
        return ""
    return f"""<div class="bg-red-50 border border-red-200 text-red-700 rounded-xl px-4 py-3 text-sm mb-4">{msg}</div>"""

# ── Onboarding ────────────────────────────────────────────────────────────────

ONBOARDING_HTML = """<!DOCTYPE html>
<html lang="en">
<head>""" + _COMMON_HEAD + """
  <title>Job Hunter — Setup</title>
  <style>
    .step { display:none; }
    .step.active { display:block; }
    .tag { display:inline-flex;align-items:center;gap:.35rem;background:#eff6ff;
           color:#1d4ed8;border:1px solid #bfdbfe;border-radius:9999px;
           padding:.2rem .75rem;font-size:.8rem;font-weight:600;cursor:default; }
    .tag button { background:none;border:none;cursor:pointer;color:#60a5fa;
                  font-size:.9rem;line-height:1;padding:0; }
    .tag button:hover { color:#ef4444; }
    .tag-input-wrap { display:flex;flex-wrap:wrap;gap:.5rem;padding:.5rem;
                      border:1.5px solid #e2e8f0;border-radius:.75rem;
                      cursor:text;transition:border .15s; }
    .tag-input-wrap:focus-within { border-color:#2563eb;box-shadow:0 0 0 3px rgba(37,99,235,.12); }
    .tag-input { border:none;outline:none;flex:1;min-width:120px;font-size:.9rem;background:transparent; }
    .drop-zone { border:2px dashed #cbd5e1;border-radius:1rem;transition:all .2s; }
    .drop-zone.over { border-color:#2563eb;background:#eff6ff; }
    .progress-bar { height:4px;background:#e2e8f0;border-radius:9999px;overflow:hidden; }
    .progress-fill { height:100%;background:linear-gradient(90deg,#2563eb,#7c3aed);
                     border-radius:9999px;transition:width .4s ease; }
  </style>
</head>
<body class="min-h-screen bg-slate-50">

<!-- TOP BAR -->
<header class="bg-white border-b border-slate-100 sticky top-0 z-20">
  <div class="max-w-2xl mx-auto px-5 py-4 flex items-center justify-between">
    <div class="flex items-center gap-2">
      <span class="text-2xl">🎯</span>
      <span class="font-bold text-slate-900">Job Hunter</span>
    </div>
    <div class="text-sm text-slate-400">Step <span id="step-num">1</span> of 4</div>
  </div>
  <div class="progress-bar mx-5 mb-3">
    <div class="progress-fill" id="progress-fill" style="width:25%"></div>
  </div>
</header>

<main class="max-w-2xl mx-auto px-5 py-8">

<!-- ── STEP 1: Upload CV ── -->
<div class="step active fade" id="step-1">
  <h2 class="text-2xl font-bold text-slate-900 mb-1">Upload your CV</h2>
  <p class="text-slate-500 mb-6">We'll analyze it and recommend the best job titles and search strategy for you.</p>

  <div id="drop-zone" class="drop-zone bg-white rounded-2xl p-10 text-center cursor-pointer mb-4"
       onclick="document.getElementById('cv-file').click()">
    <div id="drop-icon" class="text-5xl mb-3">📄</div>
    <p id="drop-text" class="font-semibold text-slate-700">Drag & drop your CV here</p>
    <p class="text-slate-400 text-sm mt-1">or click to browse — PDF only</p>
    <input type="file" id="cv-file" accept=".pdf" class="hidden" onchange="handleFile(this.files[0])"/>
  </div>

  <div id="upload-status" class="hidden bg-blue-50 border border-blue-200 rounded-xl p-4 mb-4 text-sm text-blue-700"></div>

  <button id="analyze-btn" onclick="analyzeCV()"
    class="btn btn-primary w-full hidden">✨ Analyze with AI →</button>
  <button id="skip-cv-btn" onclick="goToStep(2)"
    class="btn btn-secondary w-full mt-2">Skip for now →</button>
</div>

<!-- ── STEP 2: Review Profile ── -->
<div class="step fade" id="step-2">
  <h2 class="text-2xl font-bold text-slate-900 mb-1">Your job profile</h2>
  <p class="text-slate-500 mb-6">Review and adjust the AI recommendations, or fill them in manually.</p>

  <div id="ai-summary-box" class="hidden bg-amber-50 border-l-4 border-amber-400 rounded-xl p-4 mb-6">
    <p class="text-xs font-bold text-amber-700 uppercase tracking-wide mb-1">✨ AI Summary</p>
    <p id="ai-summary-text" class="text-sm text-amber-900"></p>
  </div>

  <div class="space-y-5">
    <div>
      <label class="label">Job titles to search for</label>
      <div class="tag-input-wrap" id="titles-wrap" onclick="focusTagInput('titles-input')">
        <input class="tag-input" id="titles-input" placeholder="e.g. VP Product…" onkeydown="tagKeyDown(event,'titles-wrap')"/>
      </div>
      <p class="text-xs text-slate-400 mt-1">Press Enter or comma to add</p>
    </div>

    <div>
      <label class="label">Key skills & keywords</label>
      <div class="tag-input-wrap" id="keywords-wrap" onclick="focusTagInput('keywords-input')">
        <input class="tag-input" id="keywords-input" placeholder="e.g. B2B, Product Strategy…" onkeydown="tagKeyDown(event,'keywords-wrap')"/>
      </div>
    </div>

    <div>
      <label class="label">Preferred locations</label>
      <div class="tag-input-wrap" id="locations-wrap" onclick="focusTagInput('locations-input')">
        <input class="tag-input" id="locations-input" placeholder="e.g. Tel Aviv…" onkeydown="tagKeyDown(event,'locations-wrap')"/>
      </div>
    </div>

    <div class="grid grid-cols-2 gap-4">
      <div>
        <label class="label">Min salary (NIS/month)</label>
        <input class="input" type="number" id="salary-min" placeholder="55000" step="1000"/>
      </div>
      <div>
        <label class="label">Max salary (NIS/month)</label>
        <input class="input" type="number" id="salary-max" placeholder="85000" step="1000"/>
      </div>
    </div>

    <div>
      <label class="label">LinkedIn URL</label>
      <input class="input" type="url" id="linkedin-url" placeholder="https://linkedin.com/in/yourname"/>
    </div>
    <div>
      <label class="label">Phone number</label>
      <input class="input" type="tel" id="phone" placeholder="+972-54-000-0000"/>
    </div>
  </div>

  <div class="flex gap-3 mt-8">
    <button onclick="goToStep(1)" class="btn btn-secondary">← Back</button>
    <button onclick="saveProfile()" class="btn btn-primary flex-1">Looks good → </button>
  </div>
</div>

<!-- ── STEP 3: Notifications ── -->
<div class="step fade" id="step-3">
  <h2 class="text-2xl font-bold text-slate-900 mb-1">Stay notified</h2>
  <p class="text-slate-500 mb-6">Get a message after each daily search and application run.</p>

  <div class="space-y-3 mb-6">
    <label class="flex items-center gap-4 p-4 border-2 border-slate-200 rounded-xl cursor-pointer
                  hover:border-blue-400 transition-colors has-[:checked]:border-blue-500 has-[:checked]:bg-blue-50">
      <input type="radio" name="notif-channel" value="telegram" onchange="showNotifForm('telegram')" class="accent-blue-600 w-4 h-4"/>
      <div>
        <div class="font-semibold text-slate-900">Telegram</div>
        <div class="text-sm text-slate-500">Receive messages via a Telegram bot</div>
      </div>
      <span class="ml-auto text-2xl">✈️</span>
    </label>

    <label class="flex items-center gap-4 p-4 border-2 border-slate-200 rounded-xl cursor-pointer
                  hover:border-green-400 transition-colors has-[:checked]:border-green-500 has-[:checked]:bg-green-50">
      <input type="radio" name="notif-channel" value="whatsapp" onchange="showNotifForm('whatsapp')" class="accent-green-600 w-4 h-4"/>
      <div>
        <div class="font-semibold text-slate-900">WhatsApp</div>
        <div class="text-sm text-slate-500">Receive messages via Twilio sandbox</div>
      </div>
      <span class="ml-auto text-2xl">💬</span>
    </label>

    <label class="flex items-center gap-4 p-4 border-2 border-slate-200 rounded-xl cursor-pointer
                  hover:border-slate-400 transition-colors has-[:checked]:border-slate-400 has-[:checked]:bg-slate-50">
      <input type="radio" name="notif-channel" value="none" onchange="showNotifForm('none')" checked class="accent-slate-600 w-4 h-4"/>
      <div>
        <div class="font-semibold text-slate-900">Skip for now</div>
        <div class="text-sm text-slate-500">You can set this up later in Settings</div>
      </div>
    </label>
  </div>

  <!-- Telegram form -->
  <div id="form-telegram" class="hidden bg-white border border-slate-200 rounded-xl p-5 space-y-4 mb-4">
    <p class="text-sm text-slate-600 bg-blue-50 rounded-lg p-3">
      1. Search for <strong>@BotFather</strong> on Telegram → /newbot → get your token.<br/>
      2. Start a chat with your new bot, then send any message.<br/>
      3. Visit <code class="bg-slate-100 px-1 rounded">https://api.telegram.org/bot&lt;TOKEN&gt;/getUpdates</code> and copy your <code class="bg-slate-100 px-1 rounded">chat_id</code>.
    </p>
    <div>
      <label class="label">Bot token</label>
      <input class="input" type="text" id="tg-token" placeholder="1234567890:AAH..."/>
    </div>
    <div>
      <label class="label">Chat ID</label>
      <input class="input" type="text" id="tg-chat-id" placeholder="123456789"/>
    </div>
    <button onclick="testTelegram()" class="btn btn-secondary text-sm">🧪 Send test message</button>
    <div id="tg-test-result" class="text-sm hidden"></div>
  </div>

  <!-- WhatsApp form -->
  <div id="form-whatsapp" class="hidden bg-white border border-slate-200 rounded-xl p-5 space-y-4 mb-4">
    <p class="text-sm text-slate-600 bg-green-50 rounded-lg p-3">
      1. Go to <strong>console.twilio.com</strong> → Messaging → Try it out → WhatsApp.<br/>
      2. Send the join message to <strong>+1 415 523 8886</strong> on WhatsApp.<br/>
      3. Paste your Twilio credentials below.
    </p>
    <div>
      <label class="label">Twilio Account SID</label>
      <input class="input" type="text" id="wa-account-sid" placeholder="ACxxxxxxxxxxxxxxxx"/>
    </div>
    <div>
      <label class="label">Twilio Auth Token</label>
      <input class="input" type="text" id="wa-auth-token" placeholder="your auth token"/>
    </div>
    <div>
      <label class="label">Your WhatsApp number</label>
      <input class="input" type="tel" id="wa-number" placeholder="+972546912084"/>
    </div>
    <button onclick="testWhatsapp()" class="btn btn-secondary text-sm">🧪 Send test message</button>
    <div id="wa-test-result" class="text-sm hidden"></div>
  </div>

  <div class="flex gap-3 mt-4">
    <button onclick="goToStep(2)" class="btn btn-secondary">← Back</button>
    <button onclick="saveNotifications()" class="btn btn-primary flex-1">Continue →</button>
  </div>
</div>

<!-- ── STEP 4: Schedule ── -->
<div class="step fade" id="step-4">
  <h2 class="text-2xl font-bold text-slate-900 mb-1">Set your schedule</h2>
  <p id="ob-schedule-desc" class="text-slate-500 mb-6">Job Hunter will run automatically for you.</p>

  <!-- Frequency choice — hidden for admin -->
  <div id="ob-frequency-section" class="hidden mb-5">
    <label class="label">How often should it run?</label>
    <div class="space-y-2">
      <label class="flex items-center gap-4 p-3.5 border-2 border-slate-200 rounded-xl cursor-pointer
                    hover:border-blue-400 transition-colors has-[:checked]:border-blue-500 has-[:checked]:bg-blue-50">
        <input type="radio" name="ob-frequency" value="weekly" onchange="obUpdateScheduleUI()" checked class="accent-blue-600 w-4 h-4"/>
        <div>
          <div class="font-semibold text-sm">Weekly <span class="text-xs text-slate-400 font-normal">(recommended)</span></div>
          <div class="text-xs text-slate-500">One search + apply cycle per week</div>
        </div>
      </label>
      <label class="flex items-center gap-4 p-3.5 border-2 border-slate-200 rounded-xl cursor-pointer
                    hover:border-blue-400 transition-colors has-[:checked]:border-blue-500 has-[:checked]:bg-blue-50">
        <input type="radio" name="ob-frequency" value="daily" onchange="obUpdateScheduleUI()" class="accent-blue-600 w-4 h-4"/>
        <div>
          <div class="font-semibold text-sm">Daily</div>
          <div class="text-xs text-slate-500">Run every day — for intensive searches</div>
        </div>
      </label>
    </div>
  </div>

  <!-- Day pickers — shown for weekly -->
  <div id="ob-day-section" class="bg-white border border-slate-200 rounded-2xl p-5 mb-4 space-y-5">
    <div>
      <label class="label">🔍 Search day</label>
      <div class="flex gap-2 flex-wrap" id="ob-search-day-btns">
        <button type="button" onclick="obSelectDay('search',1)" data-day="1" class="ob-day-btn px-3 py-2 rounded-lg border text-sm font-medium transition-all">Mon</button>
        <button type="button" onclick="obSelectDay('search',2)" data-day="2" class="ob-day-btn px-3 py-2 rounded-lg border text-sm font-medium transition-all">Tue</button>
        <button type="button" onclick="obSelectDay('search',3)" data-day="3" class="ob-day-btn px-3 py-2 rounded-lg border text-sm font-medium transition-all">Wed</button>
        <button type="button" onclick="obSelectDay('search',4)" data-day="4" class="ob-day-btn px-3 py-2 rounded-lg border text-sm font-medium transition-all">Thu</button>
        <button type="button" onclick="obSelectDay('search',5)" data-day="5" class="ob-day-btn px-3 py-2 rounded-lg border text-sm font-medium transition-all">Fri</button>
      </div>
      <input type="hidden" id="ob-search-day" value="1"/>
    </div>
    <div>
      <label class="label">🚀 Apply day</label>
      <div class="flex gap-2 flex-wrap" id="ob-apply-day-btns">
        <button type="button" onclick="obSelectDay('apply',1)" data-day="1" class="ob-day-btn px-3 py-2 rounded-lg border text-sm font-medium transition-all">Mon</button>
        <button type="button" onclick="obSelectDay('apply',2)" data-day="2" class="ob-day-btn px-3 py-2 rounded-lg border text-sm font-medium transition-all">Tue</button>
        <button type="button" onclick="obSelectDay('apply',3)" data-day="3" class="ob-day-btn px-3 py-2 rounded-lg border text-sm font-medium transition-all">Wed</button>
        <button type="button" onclick="obSelectDay('apply',4)" data-day="4" class="ob-day-btn px-3 py-2 rounded-lg border text-sm font-medium transition-all">Thu</button>
        <button type="button" onclick="obSelectDay('apply',5)" data-day="5" class="ob-day-btn px-3 py-2 rounded-lg border text-sm font-medium transition-all">Fri</button>
      </div>
      <input type="hidden" id="ob-apply-day" value="1"/>
    </div>
  </div>

  <div class="bg-white border border-slate-200 rounded-2xl p-5 space-y-5 mb-5">
    <div>
      <label class="label" id="ob-search-time-label">🔍 Search time</label>
      <select class="input" id="search-hour">
        <option value="7">7:00 AM</option><option value="8">8:00 AM</option>
        <option value="9">9:00 AM</option><option value="10">10:00 AM</option>
        <option value="11" selected>11:00 AM</option><option value="12">12:00 PM</option>
        <option value="13">1:00 PM</option>
      </select>
    </div>
    <div>
      <label class="label" id="ob-apply-time-label">🚀 Apply time</label>
      <select class="input" id="apply-hour">
        <option value="12">12:00 PM</option><option value="13">1:00 PM</option>
        <option value="14" selected>2:00 PM</option><option value="15">3:00 PM</option>
        <option value="16">4:00 PM</option><option value="17">5:00 PM</option>
      </select>
    </div>
  </div>

  <div class="bg-gradient-to-r from-blue-50 to-indigo-50 border border-blue-200 rounded-2xl p-5 mb-6">
    <p class="font-semibold text-blue-900 mb-2">Here's how it works:</p>
    <ul class="text-sm text-blue-800 space-y-1" id="ob-how-it-works">
      <li>1️⃣  At your search time, we find new matching jobs</li>
      <li>2️⃣  You get notified and review them in this dashboard</li>
      <li>3️⃣  Tap <strong>Approve</strong> on jobs you like</li>
      <li>4️⃣  At your apply time, we auto-apply to approved jobs</li>
      <li>5️⃣  Jobs not reviewed in 3 days expire automatically</li>
    </ul>
  </div>

  <div class="flex gap-3">
    <button onclick="goToStep(3)" class="btn btn-secondary">← Back</button>
    <button onclick="finishOnboarding()" class="btn btn-primary flex-1">🚀 Start Job Hunt!</button>
  </div>
</div>

</main>

<script>
let currentStep = 1;
let cvUploaded = false;
let aiData = null;
let userRole = 'user';

function goToStep(n) {
  document.querySelectorAll('.step').forEach(s => s.classList.remove('active'));
  document.getElementById('step-' + n).classList.add('active');
  document.getElementById('step-num').textContent = n;
  document.getElementById('progress-fill').style.width = (n * 25) + '%';
  currentStep = n;
  window.scrollTo({top:0, behavior:'smooth'});
  // Initialise schedule step on first visit
  if (n === 4) initScheduleStep();
}

// ── Tags ──────────────────────────────────────────────────────────────────────
function addTag(wrapId, value) {
  const v = value.trim().replace(/,$/,'').trim();
  if (!v) return;
  const wrap = document.getElementById(wrapId);
  const input = wrap.querySelector('.tag-input');
  // Avoid duplicates
  const existing = Array.from(wrap.querySelectorAll('.tag span')).map(s => s.textContent.trim().toLowerCase());
  if (existing.includes(v.toLowerCase())) { input.value=''; return; }
  const tag = document.createElement('span');
  tag.className = 'tag';
  tag.innerHTML = `<span>${v}</span><button type="button" onclick="this.parentElement.remove()">×</button>`;
  wrap.insertBefore(tag, input);
  input.value = '';
}

function focusTagInput(id) { document.getElementById(id).focus(); }

function tagKeyDown(e, wrapId) {
  if (e.key === 'Enter' || e.key === ',') {
    e.preventDefault();
    addTag(wrapId, e.target.value);
  }
}

function getTags(wrapId) {
  return Array.from(document.getElementById(wrapId).querySelectorAll('.tag span')).map(s => s.textContent.trim());
}

function setTags(wrapId, values) {
  const wrap = document.getElementById(wrapId);
  wrap.querySelectorAll('.tag').forEach(t => t.remove());
  (values || []).forEach(v => addTag(wrapId, v));
}

// ── CV Upload ─────────────────────────────────────────────────────────────────
const dz = document.getElementById('drop-zone');
dz.addEventListener('dragover', e => { e.preventDefault(); dz.classList.add('over'); });
dz.addEventListener('dragleave', () => dz.classList.remove('over'));
dz.addEventListener('drop', e => {
  e.preventDefault(); dz.classList.remove('over');
  handleFile(e.dataTransfer.files[0]);
});

function handleFile(file) {
  if (!file || !file.name.endsWith('.pdf')) {
    showUploadStatus('Please upload a PDF file.', 'error'); return;
  }
  document.getElementById('drop-icon').textContent = '⏳';
  document.getElementById('drop-text').textContent = `Uploading ${file.name}…`;
  showUploadStatus('Uploading…', 'info');

  const fd = new FormData();
  fd.append('cv', file);
  fetch('/api/upload-cv', {method:'POST', body:fd})
    .then(r => r.json())
    .then(data => {
      if (data.success) {
        cvUploaded = true;
        document.getElementById('drop-icon').textContent = '✅';
        document.getElementById('drop-text').textContent = file.name + ' ready';
        showUploadStatus('CV uploaded! Click below to analyze it.', 'success');
        document.getElementById('analyze-btn').classList.remove('hidden');
        document.getElementById('skip-cv-btn').textContent = 'Skip AI analysis →';
      } else {
        showUploadStatus(data.error || 'Upload failed.', 'error');
        document.getElementById('drop-icon').textContent = '📄';
        document.getElementById('drop-text').textContent = 'Drag & drop your CV here';
      }
    })
    .catch(() => showUploadStatus('Upload error. Please try again.', 'error'));
}

function showUploadStatus(msg, type) {
  const el = document.getElementById('upload-status');
  el.classList.remove('hidden');
  const colors = {
    info:    'bg-blue-50 border-blue-200 text-blue-700',
    success: 'bg-green-50 border-green-200 text-green-700',
    error:   'bg-red-50 border-red-200 text-red-700',
  };
  el.className = `border rounded-xl p-4 text-sm ${colors[type] || colors.info}`;
  el.textContent = msg;
}

async function analyzeCV() {
  const btn = document.getElementById('analyze-btn');
  btn.textContent = '⏳ Analyzing your CV…';
  btn.disabled = true;
  try {
    const resp = await fetch('/api/analyze-cv', {method:'POST'});
    const data = await resp.json();
    if (data.error) { showUploadStatus(data.error, 'error'); btn.disabled=false; btn.textContent='✨ Analyze with AI →'; return; }
    aiData = data;
    populateStep2(data);
    goToStep(2);
  } catch(e) {
    showUploadStatus('Analysis failed. Skipping to manual entry.', 'error');
    goToStep(2);
  }
  btn.disabled = false;
  btn.textContent = '✨ Analyze with AI →';
}

function populateStep2(data) {
  if (data.summary) {
    document.getElementById('ai-summary-box').classList.remove('hidden');
    document.getElementById('ai-summary-text').textContent = data.summary;
  }
  setTags('titles-wrap', data.job_titles || []);
  setTags('keywords-wrap', data.keywords || []);
  setTags('locations-wrap', data.locations || ['Tel Aviv']);
  if (data.salary_min) document.getElementById('salary-min').value = data.salary_min;
  if (data.salary_max) document.getElementById('salary-max').value = data.salary_max;
}

// ── Profile save ──────────────────────────────────────────────────────────────
async function saveProfile() {
  const body = {
    job_titles:   getTags('titles-wrap'),
    keywords:     getTags('keywords-wrap'),
    locations:    getTags('locations-wrap'),
    salary_min:   parseInt(document.getElementById('salary-min').value) || 0,
    salary_max:   parseInt(document.getElementById('salary-max').value) || 0,
    linkedin_url: document.getElementById('linkedin-url').value,
    phone:        document.getElementById('phone').value,
  };
  await fetch('/api/save-profile', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  goToStep(3);
}

// ── Notification forms ────────────────────────────────────────────────────────
function showNotifForm(channel) {
  ['telegram','whatsapp'].forEach(c => {
    document.getElementById('form-'+c).classList.toggle('hidden', c !== channel);
  });
}

async function testTelegram() {
  const token = document.getElementById('tg-token').value;
  const chatId = document.getElementById('tg-chat-id').value;
  if (!token || !chatId) { alert('Enter token and chat ID first.'); return; }
  const r = await fetch('/api/test-notification', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({channel:'telegram', telegram_token:token, telegram_chat_id:chatId})});
  const d = await r.json();
  const el = document.getElementById('tg-test-result');
  el.classList.remove('hidden');
  el.className = `text-sm p-3 rounded-lg mt-2 ${d.success ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`;
  el.textContent = d.success ? '✅ Message sent! Check Telegram.' : '❌ ' + (d.error || 'Failed');
}

async function testWhatsapp() {
  const sid    = document.getElementById('wa-account-sid').value;
  const token  = document.getElementById('wa-auth-token').value;
  const number = document.getElementById('wa-number').value;
  if (!sid || !token || !number) { alert('Fill in all fields first.'); return; }
  const r = await fetch('/api/test-notification', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({channel:'whatsapp', twilio_account_sid:sid, twilio_auth_token:token, whatsapp_number:number})});
  const d = await r.json();
  const el = document.getElementById('wa-test-result');
  el.classList.remove('hidden');
  el.className = `text-sm p-3 rounded-lg mt-2 ${d.success ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`;
  el.textContent = d.success ? '✅ Message sent! Check WhatsApp.' : '❌ ' + (d.error || 'Failed');
}

async function saveNotifications() {
  const channel = document.querySelector('input[name="notif-channel"]:checked')?.value || 'none';
  const body = { notification_channel: channel };
  if (channel === 'telegram') {
    body.telegram_token   = document.getElementById('tg-token').value;
    body.telegram_chat_id = document.getElementById('tg-chat-id').value;
  } else if (channel === 'whatsapp') {
    body.twilio_account_sid = document.getElementById('wa-account-sid').value;
    body.twilio_auth_token  = document.getElementById('wa-auth-token').value;
    body.whatsapp_number    = document.getElementById('wa-number').value;
  }
  await fetch('/api/save-notifications', {method:'POST',
    headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  goToStep(4);
}

// ── Onboarding schedule helpers ───────────────────────────────────────────────
async function initScheduleStep() {
  try {
    const r = await fetch('/api/me');
    const me = await r.json();
    userRole = me.role || 'user';
  } catch(e) {}

  const isAdmin = userRole === 'admin';
  document.getElementById('ob-schedule-desc').textContent = isAdmin
    ? 'As admin, your schedule runs daily.'
    : 'Choose how often Job Hunter searches and applies for you.';

  document.getElementById('ob-frequency-section').classList.toggle('hidden', isAdmin);
  obUpdateScheduleUI();
  // Default select Monday
  obSelectDay('search', 1);
  obSelectDay('apply',  1);
}

function obUpdateScheduleUI() {
  const isAdmin = userRole === 'admin';
  const freq = isAdmin ? 'daily' : (document.querySelector('input[name="ob-frequency"]:checked')?.value || 'weekly');
  document.getElementById('ob-day-section').classList.toggle('hidden', freq !== 'weekly');
}

function obSelectDay(type, day) {
  document.getElementById('ob-'+type+'-day').value = day;
  const container = document.getElementById('ob-'+type+'-day-btns');
  container.querySelectorAll('.ob-day-btn').forEach(b => {
    const active = parseInt(b.dataset.day) === parseInt(day);
    b.className = 'ob-day-btn px-3 py-2 rounded-lg border text-sm font-medium transition-all ' +
      (active ? 'bg-blue-600 border-blue-600 text-white' : 'border-slate-200 text-slate-600 hover:border-blue-400 hover:text-blue-600');
  });
}

// ── Finish ────────────────────────────────────────────────────────────────────
async function finishOnboarding() {
  const isAdmin = userRole === 'admin';
  const freq = isAdmin ? 'daily' : (document.querySelector('input[name="ob-frequency"]:checked')?.value || 'weekly');
  const body = {
    schedule_frequency: freq,
    search_hour:        parseInt(document.getElementById('search-hour').value),
    apply_hour:         parseInt(document.getElementById('apply-hour').value),
    search_day_of_week: parseInt(document.getElementById('ob-search-day').value || 1),
    apply_day_of_week:  parseInt(document.getElementById('ob-apply-day').value  || 1),
    onboarding_complete: 1,
  };
  await fetch('/api/save-schedule', {method:'POST',
    headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  window.location.href = '/dashboard';
}
</script>
</body>
</html>"""

# ── Settings ──────────────────────────────────────────────────────────────────

SETTINGS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>""" + _COMMON_HEAD + """
  <title>Job Hunter — Settings</title>
  <style>
    .tab-btn { transition:all .15s; }
    .tab-btn.active { background:#fff;color:#1d4ed8;box-shadow:0 1px 4px rgba(0,0,0,.1);font-weight:600; }
    .panel { display:none; }
    .panel.active { display:block; }
    .tag { display:inline-flex;align-items:center;gap:.35rem;background:#eff6ff;
           color:#1d4ed8;border:1px solid #bfdbfe;border-radius:9999px;
           padding:.2rem .75rem;font-size:.8rem;font-weight:600; }
    .tag button { background:none;border:none;cursor:pointer;color:#60a5fa;font-size:.9rem;line-height:1;padding:0; }
    .tag button:hover { color:#ef4444; }
    .tag-input-wrap { display:flex;flex-wrap:wrap;gap:.5rem;padding:.5rem;
                      border:1.5px solid #e2e8f0;border-radius:.75rem;cursor:text;transition:border .15s; }
    .tag-input-wrap:focus-within { border-color:#2563eb;box-shadow:0 0 0 3px rgba(37,99,235,.12); }
    .tag-input { border:none;outline:none;flex:1;min-width:120px;font-size:.9rem;background:transparent; }
    .save-toast { position:fixed;bottom:1.5rem;right:1.5rem;background:#1e293b;color:#fff;
                  padding:.75rem 1.25rem;border-radius:.75rem;font-size:.875rem;font-weight:600;
                  opacity:0;transition:opacity .3s;pointer-events:none;z-index:50; }
    .save-toast.show { opacity:1; }
  </style>
</head>
<body class="bg-slate-50 min-h-screen">

<!-- HEADER -->
<header class="bg-gradient-to-r from-slate-900 via-blue-900 to-blue-800 text-white shadow-xl sticky top-0 z-30">
  <div class="max-w-3xl mx-auto px-5 py-3 flex items-center justify-between gap-3">
    <a href="/dashboard" class="flex items-center gap-2 hover:opacity-80 transition-opacity">
      <span class="text-xl">🎯</span>
      <span class="font-bold">Job Hunter</span>
    </a>
    <div class="flex items-center gap-3">
      <span id="user-name-display" class="text-blue-300 text-sm hidden sm:block"></span>
      <a href="/dashboard" class="btn btn-secondary text-sm px-4 py-2 min-h-0 h-9">← Dashboard</a>
      <a href="/logout" class="text-blue-300 hover:text-white text-sm transition-colors">Sign out</a>
    </div>
  </div>
</header>

<div class="max-w-3xl mx-auto px-5 py-8">
  <h1 class="text-2xl font-bold text-slate-900 mb-6">Settings</h1>

  <!-- Tabs -->
  <div class="flex gap-1 bg-slate-200 p-1 rounded-xl mb-6 overflow-x-auto">
    <button onclick="setTab('profile')"       id="tab-profile"       class="tab-btn active flex-1 px-3 py-2 rounded-lg text-sm whitespace-nowrap">Profile</button>
    <button onclick="setTab('preferences')"   id="tab-preferences"   class="tab-btn text-slate-600 flex-1 px-3 py-2 rounded-lg text-sm whitespace-nowrap">Job Prefs</button>
    <button onclick="setTab('notifications')" id="tab-notifications" class="tab-btn text-slate-600 flex-1 px-3 py-2 rounded-lg text-sm whitespace-nowrap">Notifications</button>
    <button onclick="setTab('schedule')"      id="tab-schedule"      class="tab-btn text-slate-600 flex-1 px-3 py-2 rounded-lg text-sm whitespace-nowrap">Schedule</button>
    <button onclick="setTab('account')"       id="tab-account"       class="tab-btn text-slate-600 flex-1 px-3 py-2 rounded-lg text-sm whitespace-nowrap">Account</button>
  </div>

  <!-- Profile panel -->
  <div class="panel active bg-white rounded-2xl p-6 shadow-sm border border-slate-100" id="panel-profile">
    <h3 class="font-bold text-slate-900 mb-4">Personal Information</h3>
    <div class="space-y-4">
      <div>
        <label class="label">Full name</label>
        <input class="input" type="text" id="s-name" placeholder="Your name"/>
      </div>
      <div>
        <label class="label">Email <span class="text-slate-400 font-normal">(cannot change)</span></label>
        <input class="input bg-slate-50 cursor-not-allowed" type="email" id="s-email" readonly/>
      </div>
      <div>
        <label class="label">Phone</label>
        <input class="input" type="tel" id="s-phone" placeholder="+972-54-000-0000"/>
      </div>
      <div>
        <label class="label">LinkedIn URL</label>
        <input class="input" type="url" id="s-linkedin" placeholder="https://linkedin.com/in/yourname"/>
      </div>
    </div>
    <button onclick="saveProfile()" class="btn btn-primary mt-6">Save changes</button>

    <!-- CV Upload -->
    <div class="mt-8 pt-6 border-t border-slate-100">
      <h4 class="font-bold text-slate-900 mb-1">Your CV</h4>
      <p id="cv-current" class="text-sm text-slate-500 mb-3">No CV uploaded yet.</p>
      <div id="cv-drop" class="border-2 border-dashed border-slate-200 rounded-xl p-6 text-center cursor-pointer hover:border-blue-400 hover:bg-blue-50 transition-all"
           onclick="document.getElementById('cv-file-input').click()">
        <p class="text-slate-600 font-medium text-sm">📄 Click to upload a new CV</p>
        <p class="text-slate-400 text-xs mt-1">PDF only — replaces current CV</p>
        <input type="file" id="cv-file-input" accept=".pdf" class="hidden" onchange="uploadCV(this.files[0])"/>
      </div>
      <div id="cv-upload-status" class="hidden text-sm p-3 rounded-lg mt-3"></div>
      <button id="cv-analyze-btn" onclick="reanalyzeCV()" class="hidden btn btn-secondary mt-3 text-sm">✨ Re-analyze with AI →</button>
    </div>
  </div>

  <!-- Job Preferences panel -->
  <div class="panel bg-white rounded-2xl p-6 shadow-sm border border-slate-100" id="panel-preferences">
    <h3 class="font-bold text-slate-900 mb-4">Job Search Preferences</h3>
    <div class="space-y-5">
      <div>
        <label class="label">Job titles to search for</label>
        <div class="tag-input-wrap" id="s-titles-wrap" onclick="document.getElementById('s-titles-input').focus()">
          <input class="tag-input" id="s-titles-input" placeholder="Add title…" onkeydown="tagKeyDown(event,'s-titles-wrap')"/>
        </div>
        <p class="text-xs text-slate-400 mt-1">Press Enter or comma to add</p>
      </div>
      <div>
        <label class="label">Key skills & keywords</label>
        <div class="tag-input-wrap" id="s-keywords-wrap" onclick="document.getElementById('s-keywords-input').focus()">
          <input class="tag-input" id="s-keywords-input" placeholder="Add keyword…" onkeydown="tagKeyDown(event,'s-keywords-wrap')"/>
        </div>
      </div>
      <div>
        <label class="label">Preferred locations</label>
        <div class="tag-input-wrap" id="s-locations-wrap" onclick="document.getElementById('s-locations-input').focus()">
          <input class="tag-input" id="s-locations-input" placeholder="Add location…" onkeydown="tagKeyDown(event,'s-locations-wrap')"/>
        </div>
      </div>
      <div class="grid grid-cols-2 gap-4">
        <div>
          <label class="label">Min salary (NIS/month)</label>
          <input class="input" type="number" id="s-salary-min" placeholder="55000" step="1000"/>
        </div>
        <div>
          <label class="label">Max salary (NIS/month)</label>
          <input class="input" type="number" id="s-salary-max" placeholder="85000" step="1000"/>
        </div>
      </div>
    </div>
    <button onclick="savePreferences()" class="btn btn-primary mt-6">Save preferences</button>
  </div>

  <!-- Notifications panel -->
  <div class="panel bg-white rounded-2xl p-6 shadow-sm border border-slate-100" id="panel-notifications">
    <h3 class="font-bold text-slate-900 mb-4">Notification Channel</h3>
    <div class="space-y-3 mb-5">
      <label class="flex items-center gap-4 p-4 border-2 border-slate-200 rounded-xl cursor-pointer
                    hover:border-blue-400 transition-colors has-[:checked]:border-blue-500 has-[:checked]:bg-blue-50">
        <input type="radio" name="s-notif" value="telegram" onchange="showNotifSection('telegram')" class="accent-blue-600 w-4 h-4"/>
        <span class="font-semibold">Telegram</span>
        <span class="ml-auto text-xl">✈️</span>
      </label>
      <label class="flex items-center gap-4 p-4 border-2 border-slate-200 rounded-xl cursor-pointer
                    hover:border-green-400 transition-colors has-[:checked]:border-green-500 has-[:checked]:bg-green-50">
        <input type="radio" name="s-notif" value="whatsapp" onchange="showNotifSection('whatsapp')" class="accent-green-600 w-4 h-4"/>
        <span class="font-semibold">WhatsApp</span>
        <span class="ml-auto text-xl">💬</span>
      </label>
      <label class="flex items-center gap-4 p-4 border-2 border-slate-200 rounded-xl cursor-pointer
                    hover:border-slate-400 transition-colors has-[:checked]:border-slate-400">
        <input type="radio" name="s-notif" value="none" onchange="showNotifSection('none')" class="accent-slate-600 w-4 h-4"/>
        <span class="font-semibold">Off</span>
      </label>
    </div>

    <div id="sn-telegram" class="hidden space-y-4 border-t pt-5">
      <div><label class="label">Bot token</label><input class="input" type="text" id="sn-tg-token" placeholder="123456789:AAH..."/></div>
      <div><label class="label">Chat ID</label><input class="input" type="text" id="sn-tg-chat-id" placeholder="12345678"/></div>
      <button onclick="testNotification('telegram')" class="btn btn-secondary text-sm">🧪 Test</button>
    </div>

    <div id="sn-whatsapp" class="hidden space-y-4 border-t pt-5">
      <div><label class="label">Twilio Account SID</label><input class="input" type="text" id="sn-wa-sid" placeholder="ACxxxxxxxx"/></div>
      <div><label class="label">Twilio Auth Token</label><input class="input" type="text" id="sn-wa-token" placeholder="auth token"/></div>
      <div><label class="label">Your WhatsApp number</label><input class="input" type="tel" id="sn-wa-number" placeholder="+972..."/></div>
      <button onclick="testNotification('whatsapp')" class="btn btn-secondary text-sm">🧪 Test</button>
    </div>

    <div id="test-notif-result" class="hidden text-sm p-3 rounded-lg mt-3"></div>
    <button onclick="saveNotifications()" class="btn btn-primary mt-6">Save notifications</button>
  </div>

  <!-- Schedule panel -->
  <div class="panel bg-white rounded-2xl p-6 shadow-sm border border-slate-100" id="panel-schedule">
    <h3 class="font-bold text-slate-900 mb-1">Schedule</h3>
    <p id="schedule-role-note" class="text-sm text-slate-500 mb-5"></p>

    <!-- Frequency toggle — hidden for admin (always daily) -->
    <div id="frequency-section" class="hidden mb-6">
      <label class="label">How often should Job Hunter run?</label>
      <div class="space-y-2">
        <label class="flex items-center gap-4 p-3.5 border-2 border-slate-200 rounded-xl cursor-pointer
                      hover:border-blue-400 transition-colors has-[:checked]:border-blue-500 has-[:checked]:bg-blue-50">
          <input type="radio" name="s-frequency" value="weekly" onchange="updateScheduleUI()" class="accent-blue-600 w-4 h-4 shrink-0"/>
          <div>
            <div class="font-semibold text-sm">Weekly <span class="text-xs text-slate-400 font-normal">(recommended)</span></div>
            <div class="text-xs text-slate-500">One search + apply cycle per week — less noise, more quality</div>
          </div>
        </label>
        <label class="flex items-center gap-4 p-3.5 border-2 border-slate-200 rounded-xl cursor-pointer
                      hover:border-blue-400 transition-colors has-[:checked]:border-blue-500 has-[:checked]:bg-blue-50">
          <input type="radio" name="s-frequency" value="daily" onchange="updateScheduleUI()" class="accent-blue-600 w-4 h-4 shrink-0"/>
          <div>
            <div class="font-semibold text-sm">Daily</div>
            <div class="text-xs text-slate-500">Run every day — best during an active intensive search</div>
          </div>
        </label>
      </div>
    </div>

    <!-- Day-of-week pickers — shown for weekly schedule -->
    <div id="day-section" class="hidden mb-6 space-y-5">
      <div>
        <label class="label">🔍 Search day</label>
        <div class="flex gap-2 flex-wrap" id="search-day-btns">
          <button type="button" onclick="selectDay('search',1)" data-day="1" class="day-btn px-3 py-2 rounded-lg border text-sm font-medium transition-all">Mon</button>
          <button type="button" onclick="selectDay('search',2)" data-day="2" class="day-btn px-3 py-2 rounded-lg border text-sm font-medium transition-all">Tue</button>
          <button type="button" onclick="selectDay('search',3)" data-day="3" class="day-btn px-3 py-2 rounded-lg border text-sm font-medium transition-all">Wed</button>
          <button type="button" onclick="selectDay('search',4)" data-day="4" class="day-btn px-3 py-2 rounded-lg border text-sm font-medium transition-all">Thu</button>
          <button type="button" onclick="selectDay('search',5)" data-day="5" class="day-btn px-3 py-2 rounded-lg border text-sm font-medium transition-all">Fri</button>
        </div>
        <input type="hidden" id="s-search-day" value="1"/>
      </div>
      <div>
        <label class="label">🚀 Apply day</label>
        <div class="flex gap-2 flex-wrap" id="apply-day-btns">
          <button type="button" onclick="selectDay('apply',1)" data-day="1" class="day-btn px-3 py-2 rounded-lg border text-sm font-medium transition-all">Mon</button>
          <button type="button" onclick="selectDay('apply',2)" data-day="2" class="day-btn px-3 py-2 rounded-lg border text-sm font-medium transition-all">Tue</button>
          <button type="button" onclick="selectDay('apply',3)" data-day="3" class="day-btn px-3 py-2 rounded-lg border text-sm font-medium transition-all">Wed</button>
          <button type="button" onclick="selectDay('apply',4)" data-day="4" class="day-btn px-3 py-2 rounded-lg border text-sm font-medium transition-all">Thu</button>
          <button type="button" onclick="selectDay('apply',5)" data-day="5" class="day-btn px-3 py-2 rounded-lg border text-sm font-medium transition-all">Fri</button>
        </div>
        <input type="hidden" id="s-apply-day" value="1"/>
      </div>
    </div>

    <!-- Time pickers (always shown) -->
    <div class="space-y-5">
      <div>
        <label class="label">🔍 Search time</label>
        <select class="input" id="s-search-hour">
          <option value="7">7:00 AM</option><option value="8">8:00 AM</option>
          <option value="9">9:00 AM</option><option value="10">10:00 AM</option>
          <option value="11">11:00 AM</option><option value="12">12:00 PM</option>
          <option value="13">1:00 PM</option>
        </select>
      </div>
      <div>
        <label class="label">🚀 Apply time</label>
        <select class="input" id="s-apply-hour">
          <option value="12">12:00 PM</option><option value="13">1:00 PM</option>
          <option value="14">2:00 PM</option><option value="15">3:00 PM</option>
          <option value="16">4:00 PM</option><option value="17">5:00 PM</option>
        </select>
      </div>
    </div>

    <div class="mt-5 flex items-center gap-3">
      <input type="checkbox" id="s-weekdays-only" class="w-4 h-4 rounded accent-blue-600">
      <label for="s-weekdays-only" class="text-sm text-slate-700 cursor-pointer">📅 Weekdays only — skip Saturday &amp; Sunday</label>
    </div>
    <button onclick="saveSchedule()" class="btn btn-primary mt-6">Save schedule</button>
  </div>

  <!-- Account panel -->
  <div class="panel bg-white rounded-2xl p-6 shadow-sm border border-slate-100" id="panel-account">
    <h3 class="font-bold text-slate-900 mb-4">Change Password</h3>
    <div class="space-y-4">
      <div><label class="label">Current password</label><input class="input" type="password" id="s-cur-pw" placeholder="••••••••"/></div>
      <div><label class="label">New password</label><input class="input" type="password" id="s-new-pw" placeholder="At least 8 characters" minlength="8"/></div>
      <div><label class="label">Confirm new password</label><input class="input" type="password" id="s-new-pw2" placeholder="••••••••"/></div>
    </div>
    <div id="pw-result" class="hidden text-sm p-3 rounded-lg mt-3"></div>
    <button onclick="changePassword()" class="btn btn-primary mt-4">Change password</button>

    <div class="mt-10 pt-6 border-t border-slate-200">
      <h4 class="font-bold text-slate-900 mb-1">Sign out</h4>
      <p class="text-sm text-slate-500 mb-3">You'll need to sign back in to access your dashboard.</p>
      <a href="/logout" class="btn btn-secondary">Sign out</a>
    </div>
  </div>
</div>

<div id="save-toast" class="save-toast">✅ Saved!</div>

<script>
let userData = {};

async function loadUser() {
  const r = await fetch('/api/me');
  userData = await r.json();
  document.getElementById('user-name-display').textContent = userData.name;
  document.getElementById('user-name-display').classList.remove('hidden');

  // Profile
  document.getElementById('s-name').value    = userData.name || '';
  document.getElementById('s-email').value   = userData.email || '';
  document.getElementById('s-phone').value   = userData.phone || '';
  document.getElementById('s-linkedin').value = userData.linkedin_url || '';

  // Preferences
  setTags('s-titles-wrap',    tryParse(userData.job_titles, []));
  setTags('s-keywords-wrap',  tryParse(userData.keywords, []));
  setTags('s-locations-wrap', tryParse(userData.locations, ['Tel Aviv']));
  if (userData.salary_min) document.getElementById('s-salary-min').value = userData.salary_min;
  if (userData.salary_max) document.getElementById('s-salary-max').value = userData.salary_max;

  // Notifications
  const ch = userData.notification_channel || 'none';
  const radio = document.querySelector('input[name="s-notif"][value="'+ch+'"]');
  if (radio) { radio.checked = true; showNotifSection(ch); }
  if (userData.telegram_token)    document.getElementById('sn-tg-token').value   = userData.telegram_token;
  if (userData.telegram_chat_id)  document.getElementById('sn-tg-chat-id').value = userData.telegram_chat_id;
  if (userData.twilio_account_sid) document.getElementById('sn-wa-sid').value    = userData.twilio_account_sid;
  if (userData.twilio_auth_token)  document.getElementById('sn-wa-token').value  = userData.twilio_auth_token;
  if (userData.whatsapp_number)    document.getElementById('sn-wa-number').value = userData.whatsapp_number;

  // CV
  if (userData.cv_path) {
    document.getElementById('cv-current').textContent = '✅ CV on file — upload a new PDF to replace it.';
    document.getElementById('cv-analyze-btn').classList.remove('hidden');
  }

  // Schedule — role-aware
  const isAdmin = userData.role === 'admin';
  const freq    = userData.schedule_frequency || (isAdmin ? 'daily' : 'weekly');

  document.getElementById('schedule-role-note').textContent = isAdmin
    ? '🔒 As the admin, your schedule runs daily.'
    : 'Choose how often Job Hunter searches and applies for you.';

  if (!isAdmin) {
    document.getElementById('frequency-section').classList.remove('hidden');
    const freqRadio = document.querySelector('input[name="s-frequency"][value="'+freq+'"]');
    if (freqRadio) freqRadio.checked = true;
  }

  updateScheduleUI();

  // Set hours
  const sh = document.getElementById('s-search-hour');
  const ah = document.getElementById('s-apply-hour');
  if (userData.search_hour) { for(let o of sh.options) if(parseInt(o.value)===userData.search_hour) o.selected=true; }
  if (userData.apply_hour)  { for(let o of ah.options) if(parseInt(o.value)===userData.apply_hour)  o.selected=true; }
  const woChk = document.getElementById('s-weekdays-only');
  if (woChk) woChk.checked = !!userData.weekdays_only;

  // Set days
  selectDay('search', userData.search_day_of_week || 1);
  selectDay('apply',  userData.apply_day_of_week  || 1);
}

function updateScheduleUI() {
  const isAdmin = userData.role === 'admin';
  const freq    = isAdmin ? 'daily' : (document.querySelector('input[name="s-frequency"]:checked')?.value || 'weekly');
  document.getElementById('day-section').classList.toggle('hidden', freq !== 'weekly');
}

function selectDay(type, day) {
  document.getElementById('s-'+type+'-day').value = day;
  const container = document.getElementById(type+'-day-btns');
  container.querySelectorAll('.day-btn').forEach(b => {
    const active = parseInt(b.dataset.day) === parseInt(day);
    b.className = 'day-btn px-3 py-2 rounded-lg border text-sm font-medium transition-all ' +
      (active ? 'bg-blue-600 border-blue-600 text-white' : 'border-slate-200 text-slate-600 hover:border-blue-400 hover:text-blue-600');
  });
}

function tryParse(v, def) { try { return JSON.parse(v); } catch { return def; } }

function setTab(name) {
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  document.getElementById('panel-' + name).classList.add('active');
}

function showNotifSection(ch) {
  document.getElementById('sn-telegram').classList.toggle('hidden', ch !== 'telegram');
  document.getElementById('sn-whatsapp').classList.toggle('hidden', ch !== 'whatsapp');
}

// Tags (same as onboarding)
function addTag(wrapId, value) {
  const v = value.trim().replace(/,$/,'').trim();
  if (!v) return;
  const wrap = document.getElementById(wrapId);
  const input = wrap.querySelector('.tag-input');
  const existing = Array.from(wrap.querySelectorAll('.tag span')).map(s => s.textContent.trim().toLowerCase());
  if (existing.includes(v.toLowerCase())) { input.value=''; return; }
  const tag = document.createElement('span');
  tag.className = 'tag';
  tag.innerHTML = `<span>${v}</span><button type="button" onclick="this.parentElement.remove()">×</button>`;
  wrap.insertBefore(tag, input);
  input.value = '';
}
function tagKeyDown(e, wrapId) {
  if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); addTag(wrapId, e.target.value); }
}
function getTags(wrapId) {
  return Array.from(document.getElementById(wrapId).querySelectorAll('.tag span')).map(s => s.textContent.trim());
}
function setTags(wrapId, values) {
  const wrap = document.getElementById(wrapId);
  wrap.querySelectorAll('.tag').forEach(t => t.remove());
  (values || []).forEach(v => addTag(wrapId, v));
}

function showToast(msg) {
  const t = document.getElementById('save-toast');
  t.textContent = msg || '✅ Saved!';
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2500);
}

async function saveProfile() {
  await fetch('/api/save-profile', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({name: document.getElementById('s-name').value,
      phone: document.getElementById('s-phone').value,
      linkedin_url: document.getElementById('s-linkedin').value})});
  showToast();
}

async function savePreferences() {
  await fetch('/api/save-profile', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({
      job_titles: getTags('s-titles-wrap'), keywords: getTags('s-keywords-wrap'),
      locations:  getTags('s-locations-wrap'),
      salary_min: parseInt(document.getElementById('s-salary-min').value)||0,
      salary_max: parseInt(document.getElementById('s-salary-max').value)||0,
    })});
  showToast();
}

async function saveNotifications() {
  const ch = document.querySelector('input[name="s-notif"]:checked')?.value || 'none';
  const body = {notification_channel: ch};
  if (ch === 'telegram') {
    body.telegram_token = document.getElementById('sn-tg-token').value;
    body.telegram_chat_id = document.getElementById('sn-tg-chat-id').value;
  } else if (ch === 'whatsapp') {
    body.twilio_account_sid = document.getElementById('sn-wa-sid').value;
    body.twilio_auth_token  = document.getElementById('sn-wa-token').value;
    body.whatsapp_number    = document.getElementById('sn-wa-number').value;
  }
  await fetch('/api/save-notifications', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  showToast();
}

async function testNotification(channel) {
  const body = {channel};
  if (channel === 'telegram') {
    body.telegram_token = document.getElementById('sn-tg-token').value;
    body.telegram_chat_id = document.getElementById('sn-tg-chat-id').value;
  } else {
    body.twilio_account_sid = document.getElementById('sn-wa-sid').value;
    body.twilio_auth_token  = document.getElementById('sn-wa-token').value;
    body.whatsapp_number    = document.getElementById('sn-wa-number').value;
  }
  const r = await fetch('/api/test-notification', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  const d = await r.json();
  const el = document.getElementById('test-notif-result');
  el.classList.remove('hidden');
  el.className = `text-sm p-3 rounded-lg mt-3 ${d.success ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`;
  el.textContent = d.success ? '✅ Test message sent!' : '❌ ' + (d.error||'Failed');
}

async function saveSchedule() {
  const isAdmin = userData.role === 'admin';
  const freq    = isAdmin ? 'daily' : (document.querySelector('input[name="s-frequency"]:checked')?.value || 'weekly');
  await fetch('/api/save-schedule', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({
      schedule_frequency: freq,
      search_hour:        parseInt(document.getElementById('s-search-hour').value),
      apply_hour:         parseInt(document.getElementById('s-apply-hour').value),
      search_day_of_week: parseInt(document.getElementById('s-search-day').value || 1),
      apply_day_of_week:  parseInt(document.getElementById('s-apply-day').value  || 1),
      weekdays_only:      document.getElementById('s-weekdays-only')?.checked ? 1 : 0,
    })});
  showToast();
}

async function uploadCV(file) {
  if (!file || !file.name.endsWith('.pdf')) {
    showCVStatus('Please upload a PDF file.', 'error'); return;
  }
  showCVStatus('Uploading…', 'info');
  const fd = new FormData();
  fd.append('cv', file);
  const r = await fetch('/api/upload-cv', {method:'POST', body:fd});
  const d = await r.json();
  if (d.success) {
    showCVStatus('✅ CV uploaded successfully!', 'success');
    document.getElementById('cv-current').textContent = '✅ New CV on file.';
    document.getElementById('cv-analyze-btn').classList.remove('hidden');
  } else {
    showCVStatus('❌ ' + (d.error||'Upload failed.'), 'error');
  }
}

async function reanalyzeCV() {
  const btn = document.getElementById('cv-analyze-btn');
  btn.textContent = '⏳ Analyzing…';
  btn.disabled = true;
  const r = await fetch('/api/analyze-cv', {method:'POST'});
  const d = await r.json();
  if (d.error) {
    showCVStatus('❌ ' + d.error, 'error');
  } else {
    showCVStatus('✅ AI analysis complete! Job preferences updated.', 'success');
    // Reload to show updated preferences
    await loadUser();
    setTab('preferences');
  }
  btn.textContent = '✨ Re-analyze with AI →';
  btn.disabled = false;
}

function showCVStatus(msg, type) {
  const el = document.getElementById('cv-upload-status');
  el.classList.remove('hidden');
  const c = {info:'bg-blue-50 border border-blue-200 text-blue-700',
             success:'bg-green-50 border border-green-200 text-green-700',
             error:'bg-red-50 border border-red-200 text-red-700'};
  el.className = `text-sm p-3 rounded-lg mt-3 ${c[type]||c.info}`;
  el.textContent = msg;
}

async function changePassword() {
  const cur  = document.getElementById('s-cur-pw').value;
  const nw   = document.getElementById('s-new-pw').value;
  const nw2  = document.getElementById('s-new-pw2').value;
  const el   = document.getElementById('pw-result');
  el.classList.remove('hidden');
  if (nw !== nw2) { el.className='text-sm p-3 rounded-lg mt-3 bg-red-50 text-red-700'; el.textContent='Passwords do not match.'; return; }
  const r = await fetch('/api/change-password', {method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify({current_password:cur, new_password:nw})});
  const d = await r.json();
  el.className = `text-sm p-3 rounded-lg mt-3 ${d.success ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`;
  el.textContent = d.success ? '✅ Password changed.' : '❌ ' + (d.error||'Failed');
  if (d.success) { ['s-cur-pw','s-new-pw','s-new-pw2'].forEach(id => document.getElementById(id).value=''); }
}

loadUser();
</script>
</body>
</html>"""

# ── Admin Panel ───────────────────────────────────────────────────────────────

ADMIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>""" + _COMMON_HEAD + """
  <title>Job Hunter — Admin</title>
</head>
<body class="bg-slate-50 min-h-screen">
<header class="bg-gradient-to-r from-slate-900 via-blue-900 to-blue-800 text-white shadow-xl sticky top-0 z-30">
  <div class="max-w-5xl mx-auto px-5 py-3 flex items-center justify-between">
    <div class="flex items-center gap-3">
      <span class="text-xl">🎯</span>
      <span class="font-bold">Job Hunter</span>
      <span class="text-xs bg-amber-500 text-white px-2 py-0.5 rounded-full font-bold ml-1">ADMIN</span>
    </div>
    <a href="/dashboard" class="btn btn-secondary text-sm px-4 py-2 min-h-0 h-9">← Dashboard</a>
  </div>
</header>

<div class="max-w-5xl mx-auto px-5 py-8">
  <h1 class="text-2xl font-bold text-slate-900 mb-1">Admin Panel</h1>
  <p class="text-slate-500 text-sm mb-6">All users and their pipeline status.</p>
  <div id="users-grid" class="space-y-4">
    <div class="text-center py-10 text-slate-400 animate-pulse text-sm">Loading users…</div>
  </div>
</div>

<script>
async function loadUsers() {
  const r = await fetch('/api/admin/users');
  if (r.status === 403 || r.status === 401) {
    document.getElementById('users-grid').innerHTML = '<p class="text-red-600 text-center py-8">Access denied — admins only.</p>';
    return;
  }
  const users = await r.json();
  if (!users || users.length === 0) {
    document.getElementById('users-grid').innerHTML = '<p class="text-slate-400 text-center py-8">No users found.</p>';
    return;
  }
  document.getElementById('users-grid').innerHTML = users.map(u => `
    <div class="bg-white rounded-2xl border border-slate-100 shadow-sm p-5">
      <div class="flex items-start justify-between gap-3 flex-wrap">
        <div class="flex items-center gap-3">
          <div class="w-10 h-10 rounded-full bg-blue-600 text-white flex items-center justify-center font-bold text-sm shrink-0">
            ${(u.name||'?').split(' ').map(w=>w[0]).join('').slice(0,2).toUpperCase()}
          </div>
          <div>
            <p class="font-bold text-slate-900">${u.name}</p>
            <p class="text-sm text-slate-500">${u.email}</p>
            <p class="text-xs text-slate-400 mt-0.5">Joined ${new Date(u.created_date).toLocaleDateString('en-GB',{day:'numeric',month:'short',year:'numeric'})}</p>
          </div>
        </div>
        <div class="flex items-center gap-2 flex-wrap">
          ${u.role==='admin'?'<span class="text-xs bg-amber-100 text-amber-700 px-2 py-0.5 rounded-full font-bold">admin</span>':''}
          <span class="text-xs px-2 py-0.5 rounded-full font-semibold ${u.is_active?'bg-green-100 text-green-700':'bg-red-100 text-red-600'}">${u.is_active?'Active':'Inactive'}</span>
        </div>
      </div>
      <div class="grid grid-cols-4 gap-3 mt-4 pt-4 border-t border-slate-100 text-center">
        <div><div class="text-xl font-bold text-slate-800">${u.stats_new||0}</div><div class="text-xs text-slate-400">New</div></div>
        <div><div class="text-xl font-bold text-green-600">${u.stats_approved||0}</div><div class="text-xs text-slate-400">Approved</div></div>
        <div><div class="text-xl font-bold text-purple-600">${u.stats_applied||0}</div><div class="text-xs text-slate-400">Applied</div></div>
        <div><div class="text-xl font-bold text-slate-600">${u.stats_total||0}</div><div class="text-xs text-slate-400">Total</div></div>
      </div>
      ${u.role !== 'admin' ? `
      <div class="mt-3 flex gap-2">
        <button onclick="toggleUser(${u.id}, ${u.is_active})"
          class="text-xs px-4 py-2 rounded-lg border font-medium transition-all ${u.is_active?'border-red-200 text-red-600 hover:bg-red-50':'border-green-200 text-green-600 hover:bg-green-50'}">
          ${u.is_active?'🚫 Deactivate':'✅ Activate'}
        </button>
      </div>` : ''}
    </div>
  `).join('');
}

async function toggleUser(id, currentActive) {
  await fetch('/api/admin/users/'+id+'/toggle', {method:'POST'});
  loadUsers();
}

loadUsers();
</script>
</body>
</html>"""

# ── Dashboard (user-aware) ────────────────────────────────────────────────────

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>""" + _COMMON_HEAD + """
  <title>Job Hunter</title>
  <style>
    .card { transition: box-shadow .15s ease, transform .15s ease; }
    @media (hover: hover) { .card:hover { box-shadow:0 8px 32px rgba(0,0,0,.10);transform:translateY(-2px); } }
    .tab-active { background:#fff;color:#1d4ed8;box-shadow:0 1px 4px rgba(0,0,0,.12);font-weight:600; }
    .why-box { background:linear-gradient(135deg,#fffbeb,#fef9ec);border-left:3px solid #f59e0b; }
    .clamp3 { overflow:hidden;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical; }
    .fade { animation:fadeUp .22s ease; }
    @keyframes fadeUp { from{opacity:0;transform:translateY(8px)} to{opacity:1;transform:none} }
    ::-webkit-scrollbar { width:5px } ::-webkit-scrollbar-track { background:#f8fafc }
    ::-webkit-scrollbar-thumb { background:#cbd5e1;border-radius:8px }
    .tab-scroll { overflow-x:auto;-webkit-overflow-scrolling:touch;scrollbar-width:none; }
    .tab-scroll::-webkit-scrollbar { display:none; }
    .btn-touch { min-height:44px;min-width:44px;display:inline-flex;align-items:center;justify-content:center; }
    .safe-bottom { padding-bottom:env(safe-area-inset-bottom,0px); }
    /* Avatar dropdown */
    .dropdown { position:relative; }
    .dropdown-menu { display:none;position:absolute;right:0;top:110%;
                     background:#fff;border:1px solid #e2e8f0;border-radius:.75rem;
                     box-shadow:0 8px 24px rgba(0,0,0,.12);min-width:160px;z-index:50; }
    .dropdown:hover .dropdown-menu, .dropdown.open .dropdown-menu { display:block; }
    .dropdown-item { display:block;padding:.65rem 1rem;font-size:.875rem;color:#374151;
                     text-decoration:none;transition:background .12s; }
    .dropdown-item:hover { background:#f8fafc;color:#1d4ed8; }
    .sort-btn { transition:all .15s; }
    .sort-btn.active-sort { background:#2563eb;color:#fff;border-color:#2563eb; }
    .reason-btn:hover { border-color:#2563eb;background:#eff6ff;color:#1d4ed8; }
  </style>
</head>
<body class="bg-slate-50 min-h-screen">

<!-- HEADER -->
<header class="bg-gradient-to-r from-slate-900 via-blue-900 to-blue-800 text-white shadow-2xl sticky top-0 z-30">
  <div class="max-w-4xl mx-auto px-4 py-3 flex items-center justify-between gap-3">
    <div class="min-w-0">
      <h1 class="text-base font-bold tracking-tight">🎯 Job Hunter</h1>
      <p id="user-tagline" class="text-blue-300 text-xs mt-0.5 hidden sm:block"></p>
    </div>
    <div id="stats-bar" class="flex gap-3 sm:gap-5 text-center shrink-0"></div>
    <div class="flex items-center gap-2 shrink-0">
      <button onclick="loadAll()" class="btn-touch text-blue-300 hover:text-white text-xl transition-colors" title="Refresh">↻</button>
      <div class="dropdown">
        <button id="avatar-btn" aria-label="Account menu" aria-haspopup="true" onclick="this.closest('.dropdown').classList.toggle('open')"
          class="btn-touch w-9 h-9 rounded-full bg-blue-600 text-white text-sm font-bold flex items-center justify-center">?</button>
        <div class="dropdown-menu">
          <a href="/settings" class="dropdown-item">⚙️ Settings</a>
          <a href="/admin"    class="dropdown-item hidden" id="admin-link">🛡️ Admin</a>
          <a href="/logout"   class="dropdown-item">← Sign out</a>
        </div>
      </div>
    </div>
  </div>
</header>

<!-- TABS -->
<div class="max-w-4xl mx-auto px-4 mt-4">
  <div class="tab-scroll flex gap-1 bg-slate-200 p-1 rounded-xl w-full">
    <button onclick="setTab('new')"      id="tab-new"      role="tab" aria-selected="true"  class="tab-btn tab-active flex-1 px-3 py-2 rounded-lg text-sm transition-all whitespace-nowrap">New</button>
    <button onclick="setTab('approved')" id="tab-approved" role="tab" aria-selected="false" class="tab-btn text-slate-600 flex-1 px-3 py-2 rounded-lg text-sm transition-all whitespace-nowrap">Approved</button>
    <button onclick="setTab('applied')"  id="tab-applied"  role="tab" aria-selected="false" class="tab-btn text-slate-600 flex-1 px-3 py-2 rounded-lg text-sm transition-all whitespace-nowrap">Applied</button>
    <button onclick="setTab('rejected')" id="tab-rejected" class="tab-btn text-slate-600 flex-1 px-3 py-2 rounded-lg text-sm transition-all whitespace-nowrap">Passed</button>
    <button onclick="setTab('expired')"  id="tab-expired"  class="tab-btn text-slate-600 flex-1 px-3 py-2 rounded-lg text-sm transition-all whitespace-nowrap">Expired</button>
    <button onclick="setTab('activity')" id="tab-activity" class="tab-btn text-slate-600 flex-1 px-3 py-2 rounded-lg text-sm transition-all whitespace-nowrap">Activity</button>
  </div>
  <p id="schedule-hint" class="text-xs text-slate-400 italic text-right mt-2"></p>
</div>

<!-- Sort + Bulk controls -->
<div id="sort-bar" class="max-w-4xl mx-auto px-4 mt-2 flex items-center gap-2 flex-wrap">
  <span class="text-xs text-slate-400 font-medium shrink-0">Sort:</span>
  <button onclick="setSort('date')"    id="sort-date"    class="sort-btn active-sort text-xs px-3 py-1.5 rounded-lg border font-medium">📅 Date</button>
  <button onclick="setSort('match')"   id="sort-match"   class="sort-btn text-xs px-3 py-1.5 rounded-lg border border-slate-200 text-slate-600 font-medium hover:border-blue-400">🎯 Match</button>
  <button onclick="setSort('company')" id="sort-company" class="sort-btn text-xs px-3 py-1.5 rounded-lg border border-slate-200 text-slate-600 font-medium hover:border-blue-400">🏢 Company</button>
  <button onclick="toggleSelect()" id="bulk-toggle" class="hidden text-xs px-3 py-1.5 rounded-lg border border-slate-200 text-slate-500 font-medium hover:border-blue-400 transition-all">☐ Select</button>
  <button onclick="runSearch()" id="run-search-btn" class="ml-auto text-xs px-3 py-1.5 rounded-lg border border-blue-300 text-blue-600 font-semibold bg-blue-50 hover:bg-blue-100 transition-all flex items-center gap-1">🔍 Run Search Now</button>
</div>

<div id="cv-warning" class="hidden max-w-4xl mx-auto px-4 pt-3">
  <div class="flex items-center gap-3 bg-amber-50 border border-amber-200 rounded-xl px-4 py-3 text-sm text-amber-800">
    <span class="text-xl">⚠️</span>
    <span><strong>No CV uploaded.</strong> Auto-apply will fail without a CV. Go to <a href="#profile" onclick="showTab('profile')" class="underline font-semibold">Profile → CV</a> and paste your resume.</span>
    <button onclick="document.getElementById('cv-warning').classList.add('hidden')" class="ml-auto text-amber-500 hover:text-amber-700 text-lg leading-none">✕</button>
  </div>
</div>
<main class="max-w-4xl mx-auto px-4 py-4 space-y-4 safe-bottom" id="jobs-list"></main>
<div id="empty-state" class="hidden text-center py-24 px-4">
  <div class="text-5xl mb-3 opacity-30">🔍</div>
  <p id="empty-msg" class="text-slate-500 font-medium">Nothing here yet</p>
  <p class="text-slate-400 text-sm mt-1">New jobs appear at your daily search time</p>
  <button id="empty-search-cta" onclick="runSearch()" class="mt-5 px-5 py-2.5 bg-blue-600 hover:bg-blue-700 text-white text-sm font-semibold rounded-xl shadow-sm transition-all">🔍 Run Search Now</button>
</div>

<!-- Activity panel -->
<div id="activity-panel" class="hidden max-w-4xl mx-auto px-4 py-4 space-y-2"></div>

<!-- Bulk action bar (floating) -->
<div id="bulk-bar" class="hidden fixed bottom-6 left-1/2 -translate-x-1/2 z-50 bg-slate-900 text-white rounded-2xl px-4 py-3 flex items-center gap-3 shadow-2xl text-sm whitespace-nowrap">
  <span id="bulk-count" class="font-medium">0 selected</span>
  <button onclick="bulkAction('approve')" class="bg-blue-600 hover:bg-blue-700 px-4 py-2 rounded-xl font-semibold transition-all">✅ Approve</button>
  <button onclick="bulkAction('reject')"  class="bg-red-600 hover:bg-red-700 px-4 py-2 rounded-xl font-semibold transition-all">❌ Pass</button>
  <button onclick="clearSelect()" class="text-slate-400 hover:text-white px-2 transition-all text-xl leading-none">✕</button>
</div>

<!-- Pass reason modal -->
<div id="pass-modal" class="hidden fixed inset-0 z-50 bg-black/40 items-end justify-center p-4" onclick="if(event.target===this)skipReason()">
  <div class="bg-white rounded-2xl shadow-2xl w-full max-w-md p-5 fade">
    <h3 class="font-bold text-slate-900 mb-0.5">Why are you passing?</h3>
    <p class="text-xs text-slate-400 mb-4">Helps improve future matches</p>
    <div class="space-y-2 mb-3">
      <button onclick="selectReason('Not a good fit')"        class="reason-btn w-full text-left px-4 py-3 rounded-xl border border-slate-200 text-sm text-slate-700 transition-all">🤔 Not a good fit</button>
      <button onclick="selectReason('Wrong seniority level')" class="reason-btn w-full text-left px-4 py-3 rounded-xl border border-slate-200 text-sm text-slate-700 transition-all">📊 Wrong seniority level</button>
      <button onclick="selectReason('Salary too low')"        class="reason-btn w-full text-left px-4 py-3 rounded-xl border border-slate-200 text-sm text-slate-700 transition-all">💰 Salary too low</button>
      <button onclick="selectReason('Bad company')"           class="reason-btn w-full text-left px-4 py-3 rounded-xl border border-slate-200 text-sm text-slate-700 transition-all">🏢 Bad company</button>
      <button onclick="selectReason('Wrong location')"        class="reason-btn w-full text-left px-4 py-3 rounded-xl border border-slate-200 text-sm text-slate-700 transition-all">📍 Wrong location</button>
      <button onclick="selectReason('Already applied elsewhere')"       class="reason-btn w-full text-left px-4 py-3 rounded-xl border border-slate-200 text-sm text-slate-700 transition-all">✓ Already applied elsewhere</button>
      <button onclick="selectReason('Not relevant to my search')" class="reason-btn w-full text-left px-4 py-3 rounded-xl border border-slate-200 text-sm text-slate-700 transition-all">🔍 Not relevant to my search</button>
    </div>
    <button onclick="skipReason()" class="w-full text-sm text-slate-400 hover:text-slate-600 py-2 transition-all">Skip — no reason</button>
  </div>
</div>

<script>
function showToast(msg) {
  const t = document.createElement('div');
  t.textContent = msg;
  t.style.cssText = 'position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:#1e293b;color:#f8fafc;padding:12px 24px;border-radius:10px;font-size:14px;font-weight:500;z-index:9999;box-shadow:0 4px 16px rgba(0,0,0,.25);transition:opacity .3s';
  document.body.appendChild(t);
  setTimeout(() => { t.style.opacity = '0'; setTimeout(() => t.remove(), 320); }, 3200);
}
</script>
<script>
let tab = 'new';
let me = {};
let sortBy = 'date';
var applyFilter = 'all';
let selectMode = false;
let selectedIds = new Set();
let _pendingPassId = null;

async function api(path, method='GET', body=null) {
  const opts = {method, headers:{'Content-Type':'application/json'}};
  if (body) opts.body = JSON.stringify(body);
  const r = await fetch(path, opts);
  if (r.status === 401) { window.location.href='/login'; return {}; }
  return r.json();
}

async function loadMe() {
  me = await api('/api/me');
  if (!me || !me.id) return;
  document.getElementById('user-tagline').textContent = me.name || '';
  document.getElementById('user-tagline').classList.remove('hidden');
  const initials = (me.name||'?').split(' ').map(w=>w[0]).join('').slice(0,2).toUpperCase();
  document.getElementById('avatar-btn').textContent = initials;
  const sh = me.search_hour || 11;
  const ah = me.apply_hour || 14;
  const fmt = h => h < 12 ? h+' AM' : h===12 ? '12 PM' : (h-12)+' PM';
  document.getElementById('schedule-hint').textContent = '🔍 '+fmt(sh)+' · 🚀 '+fmt(ah);
  if (me.role === 'admin') {
    const al = document.getElementById('admin-link');
    if (al) al.classList.remove('hidden');
  }
}

// ── Sort ──────────────────────────────────────────────────────────────────────
function setSort(s) {
  sortBy = s;
  document.querySelectorAll('.sort-btn').forEach(b => {
    const active = b.id === 'sort-' + s;
    b.classList.toggle('active-sort', active);
    b.classList.toggle('border-slate-200', !active);
    b.classList.toggle('text-slate-600', !active);
  });
  loadJobs(tab);
}

// ── Bulk select ───────────────────────────────────────────────────────────────
function toggleSelect() {
  selectMode = !selectMode;
  selectedIds.clear();
  const btn = document.getElementById('bulk-toggle');
  btn.textContent = selectMode ? '✕ Cancel' : '☐ Select';
  if (selectMode) {
    btn.classList.add('bg-slate-900','text-white','border-slate-900');
    btn.classList.remove('text-slate-500','border-slate-200');
  } else {
    btn.classList.remove('bg-slate-900','text-white','border-slate-900');
    btn.classList.add('text-slate-500','border-slate-200');
    document.getElementById('bulk-bar').classList.add('hidden');
  }
  loadJobs(tab);
}

function clearSelect() {
  selectMode = false;
  selectedIds.clear();
  const btn = document.getElementById('bulk-toggle');
  if (btn) {
    btn.textContent = '☐ Select';
    btn.classList.remove('bg-slate-900','text-white','border-slate-900');
    btn.classList.add('text-slate-500','border-slate-200');
  }
  document.getElementById('bulk-bar').classList.add('hidden');
  loadJobs(tab);
}

function toggleJobSelect(id) {
  if (selectedIds.has(id)) selectedIds.delete(id);
  else selectedIds.add(id);
  const cb = document.getElementById('cb-'+id);
  if (cb) cb.checked = selectedIds.has(id);
  const card = document.getElementById('job-'+id);
  if (card) {
    card.classList.toggle('ring-2', selectedIds.has(id));
    card.classList.toggle('ring-blue-400', selectedIds.has(id));
  }
  const bar = document.getElementById('bulk-bar');
  bar.classList.toggle('hidden', selectedIds.size === 0);
  const cnt = document.getElementById('bulk-count');
  if (cnt) cnt.textContent = selectedIds.size + ' selected';
}

async function bulkAction(action) {
  if (selectedIds.size === 0) return;
  const ids = [...selectedIds];
  clearSelect();
  await api('/api/jobs/bulk', 'POST', {action, ids});
  loadAll();
}

// ── Pass reason modal ─────────────────────────────────────────────────────────
function openPassModal(id) {
  _pendingPassId = id;
  const m = document.getElementById('pass-modal');
  m.classList.remove('hidden');
  m.classList.add('flex');
}

function closePassModal() {
  const m = document.getElementById('pass-modal');
  m.classList.add('hidden');
  m.classList.remove('flex');
}

function skipReason() {
  const id = _pendingPassId;
  _pendingPassId = null;
  closePassModal();
  doReject(id, '');
}

function selectReason(reason) {
  const id = _pendingPassId;
  _pendingPassId = null;
  closePassModal();
  doReject(id, reason);
}

async function doReject(id, reason) {
  const card = document.getElementById('job-'+id);
  if (card) { card.style.opacity='.35'; card.style.pointerEvents='none'; }
  await api('/api/jobs/'+id+'/reject', 'POST', {reason});
  loadAll();
}

// ── Activity log ──────────────────────────────────────────────────────────────
async function loadActivity() {
  const panel = document.getElementById('activity-panel');
  if (!panel) return;
  panel.innerHTML = '<div class="text-center py-10 text-slate-300 text-sm animate-pulse">Loading…</div>';
  const items = await api('/api/activity');
  if (!items || items.length === 0) {
    panel.innerHTML = '<div class="text-center py-16 text-slate-400"><div class="text-4xl mb-3 opacity-30">--</div><p class="font-medium">No activity yet</p><p class="text-sm mt-1">Actions like approving jobs and running searches appear here</p></div>';
    return;
  }
  const icons = {jobs_searched:'🔍',job_approved:'✅',job_rejected:'❌',job_applied:'🚀',notification_sent:'🔔',cv_uploaded:'📄',profile_updated:'⚙️',jobs_injected:'📋',job_stage_updated:'🔄',cv_analyzed:'🧠',bulk_approve:'✅',bulk_reject:'❌',job_status_checked:'📋'};
  panel.innerHTML = items.map(item => {
    const icon = icons[item.event_type] || '📋';
    const dt = new Date(item.created_date);
    const dateStr = dt.toLocaleDateString('en-GB',{day:'numeric',month:'short'}) + ' ' +
      dt.toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit'});
    const labels = {'jobs_searched':'Job Search','job_approved':'Job Approved','job_rejected':'Job Rejected','job_applied':'Applied','cv_uploaded':'CV Uploaded','cv_analyzed':'CV Analyzed','job_status_checked':'Status Check','bulk_approve':'Bulk Approve','bulk_reject':'Bulk Reject','jobs_injected':'Jobs Imported','job_stage_updated':'Stage Updated'};
    const label = labels[item.event_type] || item.event_type.replace(/_/g,' ').replace(/\\b\\w/g, c=>c.toUpperCase());
    return `<div class="bg-white rounded-xl border border-slate-100 px-4 py-3 flex items-center gap-3 fade">
      <span class="text-xl w-8 text-center shrink-0">${icon}</span>
      <div class="flex-1 min-w-0">
        <p class="text-sm font-semibold text-slate-800">${label}</p>
        ${item.details ? `<p class="text-xs text-slate-500 mt-0.5 truncate">${item.details}</p>` : ''}
      </div>
      <span class="text-xs text-slate-400 shrink-0">${dateStr}</span>
    </div>`;
  }).join('');
}

async function loadStats() {
  const s = await api('/api/stats');
  document.getElementById('stats-bar').innerHTML = `
    <div class="text-center"><div class="text-lg sm:text-xl font-bold">${s.new}</div><div class="text-blue-300 text-xs">New</div></div>
    <div class="text-center"><div class="text-lg sm:text-xl font-bold text-green-300">${s.approved}</div><div class="text-blue-300 text-xs">Approved</div></div>
    <div class="hidden sm:block text-center"><div class="text-lg sm:text-xl font-bold text-purple-300">${s.applied}</div><div class="text-blue-300 text-xs">Applied</div></div>
    <div class="hidden sm:block text-center"><div class="text-lg sm:text-xl font-bold text-slate-300">${s.total}</div><div class="text-blue-300 text-xs">Total</div></div>`;
}

function ago(d) {
  if (!d) return '';
  const h = Math.floor((Date.now()-new Date(d))/3.6e6);
  if (h < 1) return 'just now';
  if (h < 24) return h+'h ago';
  return Math.floor(h/24)+'d ago';
}

function sourceBadge(s) {
  const map = {LinkedIn:'bg-blue-100 text-blue-700',AllJobs:'bg-orange-100 text-orange-700',
    Indeed:'bg-violet-100 text-violet-700',Glassdoor:'bg-emerald-100 text-emerald-700',
    Crunchbase:'bg-pink-100 text-pink-700',GeekTime:'bg-cyan-100 text-cyan-700'};
  const cls = map[s]||'bg-slate-100 text-slate-600';
  return `<span class="inline-flex px-2 py-0.5 rounded-full text-xs font-medium ${cls}">${s||'Unknown'}</span>`;
}



function actionBar(job) {
  if (job.status === 'new') return `
    <div class="mt-4 pt-4 border-t border-slate-100 space-y-2">
      <button onclick="act(${job.id},'approve')" class="btn-touch w-full bg-blue-600 hover:bg-blue-700 active:bg-blue-800 active:scale-95 text-white text-sm font-semibold rounded-xl transition-all px-4">✅ Approve to Apply</button>
      <div class="flex gap-2">
        <button onclick="act(${job.id},'later')"  class="btn-touch flex-1 bg-slate-100 hover:bg-slate-200 text-slate-600 text-sm font-medium rounded-xl px-4">⏸ Later</button>
        <button onclick="act(${job.id},'reject')" class="btn-touch flex-1 bg-red-50 hover:bg-red-100 text-red-600 text-sm font-medium rounded-xl px-4">❌ Pass</button>
      </div>
    </div>`;
  if (job.status === 'approved') return `
    <div class="flex items-center gap-3 mt-4 pt-4 border-t border-slate-100">
      <div class="flex-1 text-sm text-green-700 bg-green-50 rounded-xl px-4 py-2.5 font-medium">📋 Queued — marks as applied at ${me.apply_hour ? (me.apply_hour > 12 ? (me.apply_hour-12)+' PM' : me.apply_hour+' AM') : '2 PM'}</div>
      <button onclick="act(${job.id},'reject')" class="btn-touch text-xs text-slate-400 hover:text-red-500 px-2">Undo</button>
    </div>`;
  if (job.status === 'applied') {
    return `
    <div class="mt-4 pt-4 border-t border-slate-100">
      <div class="flex items-center gap-2 flex-wrap">
        <span class="inline-flex items-center gap-2 text-sm text-purple-700 bg-purple-50 px-3 py-2 rounded-xl font-medium">🚀 Applied ${ago(job.applied_date)}</span>
           ${applyStatusBadge(job)}
        <div class="flex gap-1.5 flex-wrap">
          <button onclick="setStage(${job.id},'screening')"    class="stage-btn text-xs px-2.5 py-1.5 rounded-lg border transition-all ${job.stage==='screening'   ?'bg-blue-100 text-blue-700 border-blue-300 font-semibold':'border-slate-200 text-slate-500 hover:border-slate-400'}">📞 Screening</button>
          <button onclick="setStage(${job.id},'interviewing')" class="stage-btn text-xs px-2.5 py-1.5 rounded-lg border transition-all ${job.stage==='interviewing'?'bg-amber-100 text-amber-700 border-amber-300 font-semibold':'border-slate-200 text-slate-500 hover:border-slate-400'}">👥 Interviewing</button>
          <button onclick="setStage(${job.id},'offer')"        class="stage-btn text-xs px-2.5 py-1.5 rounded-lg border transition-all ${job.stage==='offer'       ?'bg-green-100 text-green-700 border-green-300 font-semibold':'border-slate-200 text-slate-500 hover:border-slate-400'}">🎉 Offer!</button>
          <button onclick="setStage(${job.id},'rejected')"     class="stage-btn text-xs px-2.5 py-1.5 rounded-lg border transition-all ${job.stage==='rejected'    ?'bg-red-100 text-red-600 border-red-300 font-semibold':'border-slate-200 text-slate-500 hover:border-slate-400'}">❌ Rejected</button>
        </div>
      </div>
      ${job.apply_confirmation ? `<div class="mt-2 text-xs text-slate-600 bg-slate-50 rounded-lg px-3 py-2 border border-slate-100"><span class="font-medium">✅ Confirmed:</span> ${job.apply_confirmation.substring(0,220)}${job.apply_confirmation.length>220?'…':''}</div>` : ''}
      ${(job.apply_status === 'manual_required' && job.apply_error) ? `<div class="mt-2 text-xs text-amber-700 bg-amber-50 rounded-lg px-3 py-2 border border-amber-100">👤 <span class="font-medium">Manual apply needed:</span> ${job.apply_error}</div>` : ''}
      ${(job.apply_status === 'failed' || job.apply_status === 'manual_required') ? `<div class="mt-3 flex gap-2">
        <button onclick="retryApply(${job.id})" class="flex-1 bg-blue-600 hover:bg-blue-700 text-white text-sm font-semibold px-4 py-2 rounded-xl transition-all">Retry Auto-Apply</button>
        ${job.url ? `<a href="${job.url}" target="_blank" onclick="event.stopPropagation()" class="flex-1 text-center bg-slate-100 hover:bg-slate-200 text-slate-700 text-sm font-medium px-4 py-2 rounded-xl transition-all">Apply Manually</a>` : ''}
      </div>` : ''}
    </div>`;
  }
  if (job.status === 'failed') return `
    <div class="mt-4 pt-4 border-t border-slate-100">
      <span class="inline-flex items-center gap-2 text-sm text-red-600 bg-red-50 px-4 py-2.5 rounded-xl font-medium">⚠️ Failed — ${job.notes||'see notes'}</span>
    </div>`;
  return '';
}

function matchBadge(pct) {
  if (pct == null) return '';
  const cls = pct >= 70 ? 'bg-green-100 text-green-700 border-green-200'
            : pct >= 45 ? 'bg-amber-100 text-amber-700 border-amber-200'
                        : 'bg-red-50 text-red-600 border-red-200';
  return `<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-bold border ${cls}" title="Match between this role and your profile">${pct}% match</span>`;
}

function candidateBadge(score) {
  if (score == null) return '';
  const cls = score >= 70 ? 'bg-blue-100 text-blue-700 border-blue-200'
            : score >= 45 ? 'bg-indigo-50 text-indigo-600 border-indigo-200'
                          : 'bg-slate-100 text-slate-500 border-slate-200';
  const icon = score >= 70 ? '⭐' : score >= 45 ? '✦' : '◇';
  return `<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-bold border ${cls}" title="Your candidate strength score for this role">${icon} ${score} score</span>`;
}

function statusCheckBadge(job) {
  if (!job.status || job.status === 'new') return '<span class="inline-flex items-center text-xs px-2 py-0.5 rounded-full bg-sky-50 text-sky-600 border border-sky-200">New</span>';
  if (job.status === 'approved') return '<span class="inline-flex items-center text-xs px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-600 border border-emerald-200">Approved</span>';
  if (job.status === 'applied') return '<span class="inline-flex items-center text-xs px-2 py-0.5 rounded-full bg-purple-50 text-purple-600 border border-purple-200">Applied</span>';
  if (job.status === 'rejected') return '<span class="inline-flex items-center text-xs px-2 py-0.5 rounded-full bg-red-50 text-red-500 border border-red-200">Passed</span>';
  return '';
}

async function checkStatus(id) {
  const btn = document.getElementById('verify-btn-'+id);
  if (btn) { btn.innerHTML = '⏳'; btn.disabled = true; btn.title = 'Checking…'; }
  try {
    await api('/api/jobs/'+id+'/check-status', 'POST', {});
    loadJobs(tab);
  } catch(e) {
    if (btn) { btn.innerHTML = '🔍'; btn.disabled = false; btn.title = 'Verify if still open'; }
  }
}

function urlVerifiedBadge(job) {
  if (job.url_verified === 1) return '<span class="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-green-50 text-green-600 border border-green-200">Verified</span>';
  if (job.url_verified === 0) return '<span class="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-red-50 text-red-500 border border-red-200">Dead link</span>';
  return '';
}
function applyStatusBadge(job) {
  const map = {confirmed:'✅ Confirmed',submitted:'📤 Submitted',manual_required:'👤 Manual needed',failed:'❌ Failed'};
  const cls = {confirmed:'bg-green-50 text-green-700 border-green-200',submitted:'bg-blue-50 text-blue-700 border-blue-200',manual_required:'bg-amber-50 text-amber-700 border-amber-200',failed:'bg-red-50 text-red-700 border-red-200'};
  if (!job.apply_status || !map[job.apply_status]) return '';
  return `<span class="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full border ${cls[job.apply_status]}">${map[job.apply_status]}</span>`;
}
function renderJob(job) {
  const badges = [matchBadge(job.match_score), statusCheckBadge(job), urlVerifiedBadge(job)].filter(Boolean).join('');
  const verifyBtn = job.url
    ? `<button id="verify-btn-${job.id}" onclick="checkStatus(${job.id})" class="btn-touch shrink-0 text-slate-400 hover:text-blue-600 transition-colors text-base" title="Verify if role is still open">🔍</button>`
    : '';
  const isSelectable = selectMode && job.status === 'new';
  const isSelected   = selectedIds.has(job.id);
  const checkbox = isSelectable
    ? `<input type="checkbox" id="cb-${job.id}" ${isSelected?'checked':''} onclick="event.stopPropagation();toggleJobSelect(${job.id})" class="w-5 h-5 rounded accent-blue-600 cursor-pointer shrink-0 mt-0.5"/>`
    : '';
  return `
  <div class="card bg-white rounded-2xl shadow-sm border border-slate-100 p-4 sm:p-5 fade ${isSelected?'ring-2 ring-blue-400':''}" id="job-${job.id}"
       ${isSelectable ? `onclick="toggleJobSelect(${job.id})" style="cursor:pointer"` : ''}>
    <div class="flex items-start justify-between gap-2">
      ${checkbox ? `<div class="pt-0.5">${checkbox}</div>` : ''}
      <div class="flex-1 min-w-0">
        <div class="flex flex-wrap items-center gap-2 mb-1.5">
          ${sourceBadge(job.source)}
          <span class="text-slate-400 text-xs">${ago(job.found_date)}</span>
        </div>
        <h2 class="text-base sm:text-lg font-bold text-slate-900 leading-snug">${job.title}</h2>
        <p class="text-blue-700 font-semibold mt-0.5 text-sm sm:text-base">${job.company}</p>
        ${job.company_info ? `<p class="text-slate-500 text-sm mt-0.5 leading-snug">${job.company_info}</p>` : ''}
        <p class="text-slate-400 text-xs mt-1.5">📍 ${job.location||'Tel Aviv'}</p>
      </div>
      <div class="flex items-center gap-1.5 shrink-0">
        ${verifyBtn}
        ${job.url ? `<a href="${job.url}" target="_blank" onclick="event.stopPropagation()" class="btn-touch text-xs text-blue-600 font-medium border border-blue-200 px-3 rounded-lg hover:bg-blue-50 whitespace-nowrap">View ↗</a>` : ''}
      </div>
    </div>
    ${badges ? `<div class="flex flex-wrap gap-2 mt-2.5">${badges}</div>` : ''}
    ${job.why_relevant ? `<div class="why-box mt-3 rounded-xl p-3"><p class="text-xs font-bold text-amber-700 mb-1 uppercase tracking-wide">✨ Why this fits you</p><p class="text-sm text-amber-900 leading-relaxed">${job.why_relevant}</p></div>` : ''}
    ${job.description ? `<p class="clamp3 text-sm text-slate-600 leading-relaxed mt-3">${job.description}</p>` : ''}
    ${isSelectable ? '' : actionBar(job)}
  </div>`;
}

async function loadJobs(status) {
  const list  = document.getElementById('jobs-list');
  const empty = document.getElementById('empty-state');
  list.innerHTML = '<div class="text-center py-10 text-slate-300 text-sm animate-pulse">Loading…</div>';
  let jobs = await api('/api/jobs?status=' + status + '&sort=' + sortBy);
  if (!jobs || jobs.length === 0) {
    list.innerHTML = '';
    empty.classList.remove('hidden');
    const msgs = {new:'No new jobs yet — next search at your scheduled time.',
      approved:'No approved jobs. Go to New and click Approve.',
      applied:'No applications yet.',rejected:'Nothing passed on yet.',expired:'No expired listings.'};
    document.getElementById('empty-msg').textContent = msgs[status]||'Nothing here.';
    const emCta = document.getElementById('empty-search-cta');
    if (emCta) emCta.classList.toggle('hidden', status !== 'new');
  } else {
    empty.classList.add('hidden');
    let html = '';
    if (status === 'approved') html += `<div class="bg-gradient-to-r from-green-50 to-emerald-50 border border-green-200 rounded-2xl p-4 flex items-center justify-between fade"><div><p class="font-bold text-green-800 text-sm sm:text-base">${jobs.length} position${jobs.length>1?'s':''} queued</p><p class="text-xs sm:text-sm text-green-600 mt-0.5">Auto-apply runs at your scheduled time</p></div><button id="run-apply-btn" onclick="runApply()" class="flex items-center gap-2 bg-green-600 hover:bg-green-700 active:bg-green-800 text-white text-sm font-semibold px-4 py-2 rounded-xl shadow-sm transition-all">🚀 Apply Now</button></div>`;
    if (status === 'applied') {
      const counts = {all: jobs.length, failed: 0, submitted: 0, confirmed: 0, manual_required: 0};
      jobs.forEach(j => { if (j.apply_status && counts[j.apply_status] !== undefined) counts[j.apply_status]++; });
      const pill = (key, label, cls) => {
        const active = applyFilter === key;
        const count = counts[key] || 0;
        if (key !== 'all' && count === 0) return '';
        return '<button onclick="applyFilter=\\'' + key + '\\';loadJobs(\\'applied\\')" class="text-xs font-medium px-3 py-1.5 rounded-full border transition-all '
          + (active ? cls + ' font-bold' : 'border-slate-200 text-slate-500 hover:border-slate-400 bg-white')
          + '">' + label + (key !== 'all' ? ' (' + count + ')' : '') + '</button>';
      };
      html += '<div class="flex gap-2 flex-wrap mb-3 mt-1">'
        + pill('all', 'All', 'bg-slate-100 text-slate-700 border-slate-300')
        + pill('failed', 'Failed', 'bg-red-100 text-red-700 border-red-300')
        + pill('submitted', 'Submitted', 'bg-blue-100 text-blue-700 border-blue-300')
        + pill('confirmed', 'Confirmed', 'bg-green-100 text-green-700 border-green-300')
        + pill('manual_required', 'Manual Needed', 'bg-amber-100 text-amber-700 border-amber-300')
        + '</div>';
      if (applyFilter !== 'all') {
        jobs = jobs.filter(j => j.apply_status === applyFilter);
      }
    }
    html += jobs.map(renderJob).join('');
    list.innerHTML = html;
  }
}


function retryApply(id) {
  if (!confirm('Retry auto-apply for this job?')) return;
  var card = document.getElementById('job-'+id);
  if (card) { card.style.opacity='.35'; card.style.pointerEvents='none'; }
  api('/api/jobs/'+id+'/retry', 'POST', {}).then(function() {
    showToast('Job moved back to Approved queue for retry');
    loadAll();
  });
}

async function act(id, action) {
  if (action === 'reject') { openPassModal(id); return; }
  const card = document.getElementById('job-'+id);
  if (card) { card.style.opacity='.35'; card.style.pointerEvents='none'; }
  await api('/api/jobs/'+id+'/'+action, 'POST', {});
  loadAll();
}

function setTab(t) {
  tab = t;
  if (t !== 'applied') applyFilter = 'all';
  document.querySelectorAll('.tab-btn').forEach(b => { b.classList.remove('tab-active'); b.classList.add('text-slate-600'); });
  document.getElementById('tab-'+t).classList.add('tab-active');
  document.getElementById('tab-'+t).classList.remove('text-slate-600');

  const isActivity = t === 'activity';
  const isNew = t === 'new';
  document.getElementById('sort-bar').classList.toggle('hidden', isActivity);
  document.getElementById('activity-panel').classList.toggle('hidden', !isActivity);
  document.getElementById('jobs-list').classList.toggle('hidden', isActivity);
  document.getElementById('empty-state').classList.toggle('hidden', true);
  const bulkToggle = document.getElementById('bulk-toggle');
  if (bulkToggle) bulkToggle.classList.toggle('hidden', !isNew);
  const runSearchBtn = document.getElementById('run-search-btn');
  if (runSearchBtn) runSearchBtn.classList.remove('hidden');
  if (!isNew && selectMode) clearSelect();

  if (isActivity) {
    loadActivity();
  } else {
    loadJobs(t);
  }
}

async function loadAll() {
  await Promise.all([loadStats(), tab === 'activity' ? loadActivity() : loadJobs(tab)]);
}

document.addEventListener('click', e => {
  if (!e.target.closest('.dropdown')) document.querySelectorAll('.dropdown').forEach(d => d.classList.remove('open'));
});

loadMe().then(() => loadAll());
setInterval(loadAll, 5 * 60 * 1000);

async function runSearch() {
  if (window.__searchRunning) return; // prevent concurrent searches
  window.__searchRunning = true;
  const setBtn = (disabled, html) => {
    const b = document.getElementById('run-search-btn');
    if (b) { b.disabled = disabled; b.innerHTML = html; }
  };
  setBtn(true, '⏳ Searching…');
  try {
    const r = await fetch('/api/run-search', {method:'POST', headers:{'Content-Type':'application/json'}});
    if (!r.ok) {
      showToast('Failed to start search. Please try again.');
      setBtn(false, '🔍 Run Search Now');
      window.__searchRunning = false;
      return;
    }
    // Poll activity log until a new jobs_searched entry appears
    // Activity dates are stored as SQL "YYYY-MM-DD HH:MM:SS" UTC — convert for correct comparison
    const startTime = Date.now();
    const poll = async () => {
      if (Date.now() - startTime > 180000) {
        showToast('Search is taking longer than usual. Check the Activity tab for results.');
        setBtn(false, '🔍 Run Search Now');
        window.__searchRunning = false;
        return;
      }
      try {
        const ar = await fetch('/api/activity?limit=3');
        const entries = await ar.json();
        const done = entries.find(e => e.event_type === 'jobs_searched' &&
          new Date(e.created_date.replace(' ', 'T') + 'Z').getTime() >= startTime);
        if (done) {
          const msg = done.details || '';
          const m = msg.match(/([0-9]+) new/);
          if (m && parseInt(m[1]) > 0) {
            showToast('🎉 Search complete — ' + m[1] + ' new job' + (m[1]==='1'?'':'s') + ' added!');
            setTimeout(() => loadAll(), 500);
          } else {
            showToast('✅ Search complete — jobs list is up to date.');
          }
          setBtn(false, '🔍 Run Search Now');
          window.__searchRunning = false;
        } else { setTimeout(poll, 5000); }
      } catch(e) { setTimeout(poll, 5000); }
    };
    setTimeout(poll, 8000);
  } catch(e) {
    showToast('Connection error — could not start search.');
    setBtn(false, '🔍 Run Search Now');
    window.__searchRunning = false;
  }
}
async function setStage(id, stage) {
  try {
    const r = await fetch('/api/set-stage', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({id, stage})});
    const data = r.ok ? await r.json() : null;
    if (data && data.ok) {
      const card = document.getElementById('job-'+id);
      if (card) {
        card.querySelectorAll('.stage-btn').forEach(b => {
          b.className = 'stage-btn text-xs px-2.5 py-1.5 rounded-lg border transition-all border-slate-200 text-slate-500 hover:border-slate-400';
        });
        let act = null;
        card.querySelectorAll('.stage-btn').forEach(b => { if ((b.getAttribute('onclick')||'').includes("'"+stage+"'")) act = b; });
        if (act) act.className = 'stage-btn text-xs px-2.5 py-1.5 rounded-lg border transition-all border-blue-400 text-blue-600 bg-blue-50 font-medium';
        if (act) act.className = 'stage-btn text-xs px-2.5 py-1.5 rounded-lg border transition-all border-blue-400 text-blue-600 bg-blue-50 font-medium';
      }
      showToast('Stage updated ✅');
    } else {
      showToast('Stage update failed ❌');
    }
  } catch(e) {
    showToast('Connection error ❌');
  }
}

async function runApply() {
  const btn = document.getElementById('run-apply-btn');
  if (btn) { btn.disabled = true; btn.innerHTML = 'Applying...'; }
  try {
    const r = await fetch('/api/run-apply', {method:'POST', headers:{'Content-Type':'application/json'}});
    const data = await r.json();
    if (r.ok) {
      const n = data.applied ?? 0;
      if (n > 0) {
        alert('Applied to ' + n + ' job' + (n === 1 ? '' : 's') + '!');
        setTimeout(() => loadAll(), 1500);
      } else {
        alert(data.error ? 'Apply error: ' + data.error : 'No approved jobs to apply to -- approve some first.');
      }
    } else {
      alert('Apply failed: ' + (data.error || 'Server error'));
    }
  } catch(e) {
    alert('Connection error - please try again.');
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = 'Apply Now'; }
  }
}
</script>
<script>
(function(){
  const _o=window.setTab;
  window.setTab=function(t){_o(t);history.replaceState(null,'','#'+t);};
  const _h=location.hash.replace('#','');
  const _v=['new','approved','applied','passed','activity','profile','preferences','notifications','schedule'];
  if(_h&&_v.includes(_h))window.setTab(_h);
})();

// Show CV warning banner if no CV is uploaded
(async () => {
  try {
    const r = await fetch('/api/me');
    const u = await r.json();
    if (r.ok && !u.cv_path && !u.cv_text) {
      const w = document.getElementById('cv-warning');
      if (w) w.classList.remove('hidden');
    }
  } catch(e) {}
})();
</script>
</body>
</html>"""

# ─────────────────────────────────────────────────────────────────────────────
# HTTP HANDLER
# ─────────────────────────────────────────────────────────────────────────────

