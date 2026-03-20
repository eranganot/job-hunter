#!/usr/bin/env python3
"""
Job Hunter — Standalone Multi-User App
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Local development:
    python3 app.py           → http://localhost:5001

Cloud (Railway):
    Set environment variables (see .env.example), push to GitHub, deploy.
    Run relay.py on your Mac to bridge scheduled tasks to the cloud.
"""

import json
import os
import re
import shutil
import socket
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import auth
import db as database
from ai_analysis import analyze_cv

# ── Config ────────────────────────────────────────────────────────────────────

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            return json.load(f)
    return {}

CONFIG = load_config()

# Env vars take priority (Railway/cloud); config.json is the local fallback
def _cfg(env_key: str, json_key: str, default="") -> str:
    return os.environ.get(env_key) or CONFIG.get(json_key, default)

ANTHROPIC_KEY = _cfg("ANTHROPIC_API_KEY", "anthropic_api_key")
ADMIN_EMAIL   = _cfg("ADMIN_EMAIL",        "admin_email")
SYNC_API_KEY  = _cfg("SYNC_API_KEY",       "sync_api_key")   # shared secret for relay���server calls
PORT          = int(_cfg("PORT", "port", "5001"))

# Persistent paths — override with env vars on Railway (point to a mounted volume)
DB_FILE     = _cfg("DATABASE_PATH", "_db_path", os.path.join(BASE_DIR, "jobs.db"))
UPLOADS_DIR = _cfg("UPLOADS_DIR",   "_uploads",  os.path.join(BASE_DIR, "uploads"))
os.makedirs(UPLOADS_DIR, exist_ok=True)

# ── Local IP (for mobile access) ─────────────────────────────────────────────

def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except Exception:
        return "localhost"
    finally:
        s.close()

LOCAL_IP   = get_local_ip()
MOBILE_URL = f"http://{LOCAL_IP}:{PORT}"

# ── DB + Auth init ────────────────────────────────────────────────────────────

database.set_db_path(DB_FILE)
auth.set_db_getter(database.get_db)
auth.set_admin_email(ADMIN_EMAIL)

# ── Notifications ─────────────────────────────────────────────────────────────

def send_telegram(token: str, chat_id: str, message: str):
    try:
        url  = f"https://api.telegram.org/bot{token}/sendMessage"
        clean = re.sub(r"<[^>]+>", "", message)
        data  = urllib.parse.urlencode({"chat_id": chat_id, "text": clean}).encode()
        req   = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            if result.get("ok"):
                print("[telegram] ✅ Sent")
            else:
                print(f"[telegram] ⚠️  {result}")
    except Exception as e:
        print(f"[telegram] Error: {e}")


def send_whatsapp(account_sid: str, auth_token: str, to_number: str, message: str):
    import base64
    try:
        url   = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
        clean = re.sub(r"<[^>]+>", "", message)
        body  = urllib.parse.urlencode({
            "To":   f"whatsapp:{to_number}",
            "From": "whatsapp:+14155238886",
            "Body": clean,
        }).encode()
        creds  = base64.b64encode(f"{account_sid}:{auth_token}".encode()).decode()
        req    = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        req.add_header("Authorization", f"Basic {creds}")
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            if result.get("sid"):
                print(f"[whatsapp] ✅ Sent — {result['sid']}")
            else:
                print(f"[whatsapp] ⚠️  {result}")
    except Exception as e:
        print(f"[whatsapp] Error: {e}")


def deliver_notification(user_id: int, message: str):
    """Look up user notification settings and deliver accordingly."""
    conn = database.get_db()
    p = conn.execute(
        "SELECT * FROM user_profiles WHERE user_id=?", (user_id,)
    ).fetchone()
    conn.close()
    if not p:
        return
    channel = p["notification_channel"]
    msg_with_link = message + f"\n\n📱 Dashboard: {MOBILE_URL}"
    if channel == "telegram" and p["telegram_token"] and p["telegram_chat_id"]:
        send_telegram(p["telegram_token"], p["telegram_chat_id"], msg_with_link)
    elif channel == "whatsapp" and p["twilio_account_sid"] and p["whatsapp_number"]:
        send_whatsapp(p["twilio_account_sid"], p["twilio_auth_token"],
                      p["whatsapp_number"], msg_with_link)


def check_notifications():
    notify_file = os.path.join(BASE_DIR, "notify.json")
    if not os.path.exists(notify_file):
        return
    try:
        with open(notify_file) as f:
            data = json.load(f)
        message = data.get("message", "Job Hunter notification")
        user_id = data.get("user_id", 1)
        os.remove(notify_file)
        deliver_notification(user_id, message)
    except Exception as e:
        print(f"[notify] Error: {e}")


def file_watcher():
    while True:
        database.import_pending_jobs(BASE_DIR)
        database.import_applied_updates(BASE_DIR)
        check_notifications()
        time.sleep(30)

# ── Multipart parser ──────────────────────────────────────────────────────────

def parse_multipart(headers, body: bytes) -> dict:
    """Returns dict: field_name → str  or  field_name → {'filename':str,'data':bytes}"""
    ctype = headers.get("Content-Type", "")
    boundary = None
    for part in ctype.split(";"):
        part = part.strip()
        if part.startswith("boundary="):
            boundary = part[9:].strip('"')
            break
    if not boundary:
        return {}

    parts = {}
    sep = f"--{boundary}".encode()
    sections = body.split(sep)
    for section in sections[1:-1]:
        if b"\r\n\r\n" not in section:
            continue
        header_raw, content = section.split(b"\r\n\r\n", 1)
        content = content.rstrip(b"\r\n")
        header_str = header_raw.decode("utf-8", errors="replace")
        name = filename = None
        for line in header_str.split("\r\n"):
            if "Content-Disposition" in line:
                for item in line.split(";"):
                    item = item.strip()
                    if item.startswith("name="):
                        name = item[5:].strip('"')
                    elif item.startswith("filename="):
                        filename = item[9:].strip('"')
        if name:
            if filename:
                parts[name] = {"filename": filename, "data": content}
            else:
                parts[name] = content.decode("utf-8", errors="replace")
    return parts

# ─────────────────────────────────────────────────────────────────────────────
# HTML PAGES
# ─────────────────────────────────────────────────────────────────────────────

_COMMON_HEAD = """
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover"/>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    * { -webkit-tap-highlight-color: transparent; box-sizing: border-box; }
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }
    .btn { display:inline-flex;align-items:center;justify-content:center;min-height:44px;
           font-weight:600;border-radius:0.75rem;transition:all .15s;cursor:pointer;padding:0 1.25rem; }
    .btn-primary { background:#2563eb;color:#fff; }
    .btn-primary:hover { background:#1d4ed8; }
    .btn-primary:active { background:#1e40af;transform:scale(.98); }
    .btn-secondary { background:#f1f5f9;color:#475569; }
    .btn-secondary:hover { background:#e2e8f0; }
    .input { width:100%;border:1.5px solid #e2e8f0;border-radius:.75rem;
             padding:.65rem 1rem;font-size:.95rem;outline:none;transition:border .15s; }
    .input:focus { border-color:#2563eb;box-shadow:0 0 0 3px rgba(37,99,235,.12); }
    .label { display:block;font-size:.875rem;font-weight:600;color:#374151;margin-bottom:.35rem; }
    .fade { animation:fadeUp .22s ease; }
    @keyframes fadeUp { from{opacity:0;transform:translateY(8px)} to{opacity:1;transform:none} }
  </style>
"""

# ── Auth pages ────────────────────────────────────────────────────────────────

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
        <button id="avatar-btn" onclick="this.closest('.dropdown').classList.toggle('open')"
          class="btn-touch w-9 h-9 rounded-full bg-blue-600 text-white text-sm font-bold flex items-center justify-center">?</button>
        <div class="dropdown-menu">
          <a href="/settings" class="dropdown-item">⚙️ Settings</a>
          <a href="/logout"   class="dropdown-item">← Sign out</a>
        </div>
      </div>
    </div>
  </div>
</header>

<!-- TABS -->
<div class="max-w-4xl mx-auto px-4 mt-4">
  <div class="tab-scroll flex gap-1 bg-slate-200 p-1 rounded-xl w-full">
    <button onclick="setTab('new')"      id="tab-new"      class="tab-btn tab-active flex-1 px-3 py-2 rounded-lg text-sm transition-all whitespace-nowrap">New</button>
    <button onclick="setTab('approved')" id="tab-approved" class="tab-btn text-slate-600 flex-1 px-3 py-2 rounded-lg text-sm transition-all whitespace-nowrap">Approved</button>
    <button onclick="setTab('applied')"  id="tab-applied"  class="tab-btn text-slate-600 flex-1 px-3 py-2 rounded-lg text-sm transition-all whitespace-nowrap">Applied</button>
    <button onclick="setTab('rejected')" id="tab-rejected" class="tab-btn text-slate-600 flex-1 px-3 py-2 rounded-lg text-sm transition-all whitespace-nowrap">Passed</button>
    <button onclick="setTab('expired')"  id="tab-expired"  class="tab-btn text-slate-600 flex-1 px-3 py-2 rounded-lg text-sm transition-all whitespace-nowrap">Expired</button>
  </div>
  <p id="schedule-hint" class="text-xs text-slate-400 italic text-right mt-2"></p>
</div>

<main class="max-w-4xl mx-auto px-4 py-4 space-y-4 safe-bottom" id="jobs-list"></main>
<div id="empty-state" class="hidden text-center py-24 px-4">
  <div class="text-5xl mb-3 opacity-30">🔍</div>
  <p id="empty-msg" class="text-slate-500 font-medium">Nothing here yet</p>
  <p class="text-slate-400 text-sm mt-1">New jobs appear at your daily search time</p>
</div>

<script>
let tab = 'new';
let me = {};

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
      <div class="flex-1 text-sm text-green-700 bg-green-50 rounded-xl px-4 py-2.5 font-medium">✅ Queued — applies at ${me.apply_hour ? (me.apply_hour > 12 ? (me.apply_hour-12)+' PM' : me.apply_hour+' AM') : '2 PM'}</div>
      <button onclick="act(${job.id},'reject')" class="btn-touch text-xs text-slate-400 hover:text-red-500 px-2">Undo</button>
    </div>`;
  if (job.status === 'applied') return `
    <div class="mt-4 pt-4 border-t border-slate-100">
      <span class="inline-flex items-center gap-2 text-sm text-purple-700 bg-purple-50 px-4 py-2.5 rounded-xl font-medium">🚀 Applied ${ago(job.applied_date)}</span>
    </div>`;
  if (job.status === 'failed') return `
    <div class="mt-4 pt-4 border-t border-slate-100">
      <span class="inline-flex items-center gap-2 text-sm text-red-600 bg-red-50 px-4 py-2.5 rounded-xl font-medium">⚠️ Failed — ${job.notes||'see notes'}</span>
    </div>`;
  return '';
}

function renderJob(job) {
  return `
  <div class="card bg-white rounded-2xl shadow-sm border border-slate-100 p-4 sm:p-5 fade" id="job-${job.id}">
    <div class="flex items-start justify-between gap-2">
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
      ${job.url ? `<a href="${job.url}" target="_blank" class="btn-touch shrink-0 text-xs text-blue-600 font-medium border border-blue-200 px-3 rounded-lg hover:bg-blue-50 whitespace-nowrap">View ↗</a>` : ''}
    </div>
    ${job.why_relevant ? `<div class="why-box mt-3 rounded-xl p-3"><p class="text-xs font-bold text-amber-700 mb-1 uppercase tracking-wide">✨ Why this fits you</p><p class="text-sm text-amber-900 leading-relaxed">${job.why_relevant}</p></div>` : ''}
    ${job.description ? `<p class="clamp3 text-sm text-slate-600 leading-relaxed mt-3">${job.description}</p>` : ''}
    ${actionBar(job)}
  </div>`;
}

async function loadJobs(status) {
  const list  = document.getElementById('jobs-list');
  const empty = document.getElementById('empty-state');
  list.innerHTML = '<div class="text-center py-10 text-slate-300 text-sm animate-pulse">Loading…</div>';
  const jobs = await api('/api/jobs?status=' + status);
  if (!jobs || jobs.length === 0) {
    list.innerHTML = '';
    empty.classList.remove('hidden');
    const msgs = {new:'No new jobs yet — next search at your scheduled time.',
      approved:'No approved jobs. Go to New and click Approve.',
      applied:'No applications yet.',rejected:'Nothing passed on yet.',expired:'No expired listings.'};
    document.getElementById('empty-msg').textContent = msgs[status]||'Nothing here.';
  } else {
    empty.classList.add('hidden');
    let html = '';
    if (status === 'approved') html += `<div class="bg-gradient-to-r from-green-50 to-emerald-50 border border-green-200 rounded-2xl p-4 flex items-center justify-between fade"><div><p class="font-bold text-green-800 text-sm sm:text-base">${jobs.length} position${jobs.length>1?'s':''} queued</p><p class="text-xs sm:text-sm text-green-600 mt-0.5">Auto-apply runs at your scheduled time</p></div><span class="text-3xl">🚀</span></div>`;
    html += jobs.map(renderJob).join('');
    list.innerHTML = html;
  }
}

async function act(id, action) {
  const card = document.getElementById('job-'+id);
  if (card) { card.style.opacity='.35'; card.style.pointerEvents='none'; }
  await api('/api/jobs/'+id+'/'+action, 'POST', {});
  loadAll();
}

function setTab(t) {
  tab = t;
  document.querySelectorAll('.tab-btn').forEach(b => { b.classList.remove('tab-active'); b.classList.add('text-slate-600'); });
  document.getElementById('tab-'+t).classList.add('tab-active');
  document.getElementById('tab-'+t).classList.remove('text-slate-600');
  loadJobs(t);
}

async function loadAll() { await Promise.all([loadStats(), loadJobs(tab)]); }

document.addEventListener('click', e => {
  if (!e.target.closest('.dropdown')) document.querySelectorAll('.dropdown').forEach(d => d.classList.remove('open'));
});

loadMe().then(() => loadAll());
setInterval(loadAll, 5 * 60 * 1000);
</script>
</body>
</html>"""

# ─────────────────────────────────────────────────────────────────────────────
# HTTP HANDLER
# ─────────────────────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] {fmt % args}")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def send_html(self, html: str, code: int = 200):
        body = html.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, data, code: int = 200):
        body = json.dumps(data, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def redirect(self, location: str):
        self.send_response(302)
        self.send_header("Location", location)
        self.end_headers()

    def read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length) if length else b""

    def read_json(self) -> dict:
        try:
            return json.loads(self.read_body())
        except Exception:
            return {}

    def get_user(self):
        token = auth.get_token_from_request(self.headers)
        return auth.get_session_user(token)

    def require_auth(self):
        """Returns user dict or None (and sends redirect if not authed)."""
        user = self.get_user()
        if not user:
            self.redirect("/login")
        return user

    def _check_sync_key(self, key: str) -> bool:
        """Validate the shared secret used by relay.py and scheduled tasks."""
        return bool(SYNC_API_KEY) and key == SYNC_API_KEY

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    # ── GET ───────────────────────────────────────────────────────────────────

    def do_GET(self):
        parsed = urlparse(self.path)
        path   = parsed.path
        qs     = parse_qs(parsed.query)

        # Public routes
        if path in ("/login", "/login/"):
            user = self.get_user()
            if user:
                self.redirect("/dashboard")
            else:
                self.send_html(LOGIN_HTML.replace("{error_block}", ""))
            return

        if path in ("/register", "/register/"):
            user = self.get_user()
            if user:
                self.redirect("/dashboard")
            else:
                self.send_html(REGISTER_HTML.replace("{error_block}", ""))
            return

        if path in ("/logout", "/logout/"):
            token = auth.get_token_from_request(self.headers)
            if token:
                auth.delete_session(token)
            self.send_response(302)
            self.send_header("Set-Cookie", auth.clear_session_cookie())
            self.send_header("Location", "/login")
            self.end_headers()
            return

        # Root redirect
        if path in ("/", ""):
            user = self.get_user()
            self.redirect("/dashboard" if user else "/login")
            return

        # Auth-required routes
        if path in ("/dashboard", "/dashboard/"):
            user = self.require_auth()
            if not user:
                return
            if not user.get("onboarding_complete"):
                self.redirect("/onboarding")
                return
            self.send_html(DASHBOARD_HTML)
            return

        if path in ("/onboarding", "/onboarding/"):
            user = self.require_auth()
            if not user:
                return
            self.send_html(ONBOARDING_HTML)
            return

        if path in ("/settings", "/settings/"):
            user = self.require_auth()
            if not user:
                return
            self.send_html(SETTINGS_HTML)
            return

        # API routes (all require auth)
        if path == "/api/me":
            user = self.require_auth()
            if not user:
                return
            self.send_json(user)
            return

        if path == "/api/stats":
            user = self.require_auth()
            if not user:
                return
            conn = database.get_db()
            self.send_json(database.get_stats(conn, user["id"]))
            conn.close()
            return

        if path == "/api/jobs":
            user = self.require_auth()
            if not user:
                return
            status = qs.get("status", ["new"])[0]
            conn   = database.get_db()
            database.expire_old_jobs(conn, user["id"])
            if status == "all":
                rows = conn.execute(
                    "SELECT * FROM jobs WHERE user_id=? ORDER BY found_date DESC",
                    (user["id"],)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM jobs WHERE user_id=? AND status=? ORDER BY found_date DESC",
                    (user["id"], status)
                ).fetchall()
            conn.close()
            self.send_json([dict(r) for r in rows])
            return

        if path == "/api/patterns":
            user = self.require_auth()
            if not user:
                return
            conn = database.get_db()
            rows = conn.execute(
                "SELECT * FROM rejected_patterns WHERE user_id=? ORDER BY created_date DESC LIMIT 50",
                (user["id"],)
            ).fetchall()
            conn.close()
            self.send_json([dict(r) for r in rows])
            return

        # ── Sync: export approved jobs for relay/scheduled tasks ──
        if path == "/api/sync/approved":
            qs = parse_qs(parsed.query)
            if not self._check_sync_key(qs.get("api_key", [""])[0]):
                self.send_json({"error": "Unauthorized"}, 401)
                return
            conn = database.get_db()
            rows = conn.execute("SELECT * FROM jobs WHERE status='approved'").fetchall()
            conn.close()
            self.send_json([dict(r) for r in rows])
            return

        self.send_response(404)
        self.end_headers()

    # ── POST ──────────────────────────────────────────────────────────────────

    def do_POST(self):
        parsed = urlparse(self.path)
        path   = parsed.path

        # ── Login ──
        if path == "/login":
            body = urllib.parse.parse_qs(self.read_body().decode())
            email    = body.get("email", [""])[0]
            password = body.get("password", [""])[0]
            user, err = auth.authenticate(email, password)
            if err:
                html = LOGIN_HTML.replace("{error_block}", error_block(err))
                self.send_html(html)
                return
            token = auth.create_session(user["id"])
            self.send_response(302)
            self.send_header("Set-Cookie", auth.make_session_cookie(token))
            dest = "/dashboard" if user.get("onboarding_complete") else "/onboarding"
            self.send_header("Location", dest)
            self.end_headers()
            return

        # ── Register ──
        if path == "/register":
            body = urllib.parse.parse_qs(self.read_body().decode())
            name     = body.get("name", [""])[0].strip()
            email    = body.get("email", [""])[0]
            password = body.get("password", [""])[0]
            password2 = body.get("password2", [""])[0]
            if not name or not email or not password:
                html = REGISTER_HTML.replace("{error_block}", error_block("All fields are required."))
                self.send_html(html)
                return
            if password != password2:
                html = REGISTER_HTML.replace("{error_block}", error_block("Passwords don't match."))
                self.send_html(html)
                return
            if len(password) < 8:
                html = REGISTER_HTML.replace("{error_block}", error_block("Password must be at least 8 characters."))
                self.send_html(html)
                return
            user_id, err = auth.create_user(name, email, password)
            if err:
                html = REGISTER_HTML.replace("{error_block}", error_block(err))
                self.send_html(html)
                return
            token = auth.create_session(user_id)
            self.send_response(302)
            self.send_header("Set-Cookie", auth.make_session_cookie(token))
            self.send_header("Location", "/onboarding")
            self.end_headers()
            return

        # All API POST routes require auth
        user = self.get_user()
        if not user:
            self.send_json({"error": "Unauthorized"}, 401)
            return
        user_id = user["id"]

        # ── CV Upload ──
        if path == "/api/upload-cv":
            body = self.read_body()
            parts = parse_multipart(self.headers, body)
            cv_part = parts.get("cv")
            if not cv_part or not isinstance(cv_part, dict):
                self.send_json({"error": "No file received."})
                return
            if not cv_part["filename"].lower().endswith(".pdf"):
                self.send_json({"error": "Only PDF files are accepted."})
                return
            user_upload_dir = os.path.join(UPLOADS_DIR, str(user_id))
            os.makedirs(user_upload_dir, exist_ok=True)
            cv_path = os.path.join(user_upload_dir, "cv.pdf")
            with open(cv_path, "wb") as f:
                f.write(cv_part["data"])
            auth.update_profile(user_id, cv_path=cv_path, cv_analyzed=0)
            print(f"[cv] Saved CV for user {user_id}: {cv_path}")
            self.send_json({"success": True, "path": cv_path})
            return

        # ── CV Analyze ──
        if path == "/api/analyze-cv":
            if not ANTHROPIC_KEY:
                self.send_json({"error": "Anthropic API key not configured. Add it to config.json."})
                return
            # Get CV path from profile
            conn = database.get_db()
            row = conn.execute("SELECT cv_path FROM user_profiles WHERE user_id=?", (user_id,)).fetchone()
            conn.close()
            cv_path = row["cv_path"] if row else None
            if not cv_path or not os.path.exists(cv_path):
                self.send_json({"error": "No CV uploaded yet. Please upload your PDF first."})
                return
            try:
                data = analyze_cv(cv_path, ANTHROPIC_KEY)
                # Save to profile
                auth.update_profile(
                    user_id,
                    cv_analyzed=1,
                    cv_summary=data.get("summary", ""),
                    job_titles=json.dumps(data.get("job_titles", [])),
                    keywords=json.dumps(data.get("keywords", [])),
                    locations=json.dumps(data.get("locations", ["Tel Aviv"])),
                    salary_min=data.get("salary_min", 0),
                    salary_max=data.get("salary_max", 0),
                    experience_years=data.get("experience_years", 0),
                    seniority=data.get("seniority", ""),
                )
                database.write_users_config(BASE_DIR)
                self.send_json(data)
            except Exception as e:
                print(f"[analyze] Error: {e}")
                self.send_json({"error": str(e)})
            return

        # ── Save profile ──
        if path == "/api/save-profile":
            data = self.read_json()
            kwargs = {}
            for field in ("name", "phone", "linkedin_url"):
                if field in data:
                    kwargs[field] = data[field]
            if "name" in kwargs:
                auth.update_user(user_id, name=kwargs.pop("name"))
            for field in ("job_titles", "keywords", "locations"):
                if field in data:
                    val = data[field]
                    kwargs[field] = json.dumps(val) if isinstance(val, list) else val
            for field in ("salary_min", "salary_max"):
                if field in data:
                    kwargs[field] = int(data[field])
            if kwargs:
                auth.update_profile(user_id, **kwargs)
            database.write_users_config(BASE_DIR)
            self.send_json({"success": True})
            return

        # ── Save notifications ──
        if path == "/api/save-notifications":
            data = self.read_json()
            kwargs = {}
            for field in ("notification_channel", "telegram_token", "telegram_chat_id",
                          "twilio_account_sid", "twilio_auth_token", "whatsapp_number"):
                if field in data:
                    kwargs[field] = data[field]
            if kwargs:
                auth.update_profile(user_id, **kwargs)
            database.write_users_config(BASE_DIR)
            self.send_json({"success": True})
            return

        # ── Test notification ──
        if path == "/api/test-notification":
            data    = self.read_json()
            channel = data.get("channel", "none")
            msg     = f"✅ Job Hunter test message — connection works! Dashboard: {MOBILE_URL}"
            try:
                if channel == "telegram":
                    send_telegram(data.get("telegram_token",""), data.get("telegram_chat_id",""), msg)
                elif channel == "whatsapp":
                    send_whatsapp(data.get("twilio_account_sid",""), data.get("twilio_auth_token",""),
                                  data.get("whatsapp_number",""), msg)
                self.send_json({"success": True})
            except Exception as e:
                self.send_json({"success": False, "error": str(e)})
            return

        # ── Save schedule ──
        if path == "/api/save-schedule":
            data = self.read_json()
            kwargs = {}
            for int_field in ("search_hour", "apply_hour", "search_day_of_week",
                              "apply_day_of_week", "onboarding_complete"):
                if int_field in data:
                    kwargs[int_field] = int(data[int_field])
            if "schedule_frequency" in data:
                # Admin is always daily regardless of what was sent
                if user.get("role") == "admin":
                    kwargs["schedule_frequency"] = "daily"
                else:
                    kwargs["schedule_frequency"] = data["schedule_frequency"]
            if kwargs:
                auth.update_profile(user_id, **kwargs)
            database.write_users_config(BASE_DIR)
            self.send_json({"success": True})
            return

        # ── Change password ──
        if path == "/api/change-password":
            data = self.read_json()
            err = auth.change_password(user_id, data.get("current_password",""), data.get("new_password",""))
            if err:
                self.send_json({"success": False, "error": err})
            else:
                self.send_json({"success": True})
            return

        # ── Job actions ──
        m = re.match(r"^/api/jobs/(\d+)/(approve|reject|later|applied|failed)$", path)
        if m:
            job_id = int(m.group(1))
            action = m.group(2)
            data   = self.read_json()
            conn   = database.get_db()

            # Verify ownership
            job = conn.execute(
                "SELECT * FROM jobs WHERE id=? AND user_id=?", (job_id, user_id)
            ).fetchone()
            if not job:
                conn.close()
                self.send_json({"error": "Not found"}, 404)
                return

            status_map = {"approve":"approved","reject":"rejected",
                          "later":"new","applied":"applied","failed":"failed"}
            new_status = status_map[action]
            notes      = data.get("notes", "")

            if action == "later":
                conn.execute("UPDATE jobs SET found_date=?, notes=? WHERE id=?",
                             (datetime.now().isoformat(), notes, job_id))
            elif action in ("applied", "failed"):
                conn.execute("UPDATE jobs SET status=?, applied_date=?, notes=? WHERE id=?",
                             (new_status, datetime.now().isoformat(), notes, job_id))
            else:
                conn.execute("UPDATE jobs SET status=?, notes=? WHERE id=?",
                             (new_status, notes, job_id))

            if action == "reject":
                conn.execute(
                    "INSERT INTO rejected_patterns (user_id,company,title,notes,created_date) VALUES (?,?,?,?,?)",
                    (user_id, job["company"], job["title"],
                     notes or "No reason given", datetime.now().isoformat())
                )

            conn.commit()
            conn.close()
            database.write_approved_jobs(BASE_DIR)
            self.send_json({"success": True})
            return

        # ── Sync endpoints — called by relay.py on Mac, no session needed ──────

        if path == "/api/sync/jobs":
            data = self.read_json()
            if not self._check_sync_key(data.get("api_key", "")):
                self.send_json({"error": "Unauthorized"}, 401)
                return
            jobs = data.get("jobs", [])
            conn = database.get_db()
            inserted = 0
            for j in jobs:
                uid = j.get("user_id", 1)
                try:
                    conn.execute("""
                        INSERT OR IGNORE INTO jobs
                          (user_id,title,company,location,url,description,
                           why_relevant,company_info,source,found_date,status)
                        VALUES (?,?,?,?,?,?,?,?,?,?,'new')
                    """, (uid, j.get("title",""), j.get("company",""),
                          j.get("location","Tel Aviv"), j.get("url",""),
                          j.get("description",""), j.get("why_relevant",""),
                          j.get("company_info",""), j.get("source",""),
                          j.get("found_date", datetime.now().isoformat())))
                    if conn.execute("SELECT changes()").fetchone()[0] > 0:
                        inserted += 1
                except Exception:
                    pass
            conn.commit()
            conn.close()
            print(f"[sync] {inserted} new jobs ingested via API")
            self.send_json({"success": True, "inserted": inserted})
            return

        if path == "/api/sync/updates":
            data = self.read_json()
            if not self._check_sync_key(data.get("api_key", "")):
                self.send_json({"error": "Unauthorized"}, 401)
                return
            updates = data.get("updates", [])
            conn = database.get_db()
            for u in updates:
                conn.execute(
                    "UPDATE jobs SET status=?, applied_date=?, notes=? WHERE id=?",
                    (u.get("status","applied"), u.get("applied_date"),
                     u.get("notes",""), u["id"])
                )
            conn.commit()
            conn.close()
            print(f"[sync] {len(updates)} job statuses updated via API")
            self.send_json({"success": True, "updated": len(updates)})
            return

        if path == "/api/sync/notify":
            data = self.read_json()
            if not self._check_sync_key(data.get("api_key", "")):
                self.send_json({"error": "Unauthorized"}, 401)
                return
            message = data.get("message", "Job Hunter notification")
            user_id = int(data.get("user_id", 1))
            deliver_notification(user_id, message)
            self.send_json({"success": True})
            return

        self.send_response(404)
        self.end_headers()

# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"\n🎯  Job Hunter (Multi-User) starting…")
    database.init_db()

    # Process any waiting files
    database.import_pending_jobs(BASE_DIR)
    database.import_applied_updates(BASE_DIR)
    check_notifications()

    # Background watcher
    t = threading.Thread(target=file_watcher, daemon=True)
    t.start()

    ai_status = "✅ Configured" if ANTHROPIC_KEY else "⚠️  Not set — add to config.json"

    print(f"\n📂  Folder:        {BASE_DIR}")
    print(f"🗄️   Database:      jobs.db")
    print(f"🤖  Anthropic AI:  {ai_status}")
    print(f"\n🖥️   Desktop:       http://localhost:{PORT}")
    print(f"📱  Mobile:        {MOBILE_URL}   open on your phone")
    print(f"⌨��8�   Ctrl+C to stop\n")

    server = HTTPServer(("0.0.0.0", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋  Stopped.")
