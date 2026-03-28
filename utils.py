"""Utility functions and configuration for Job Hunter."""
import os, json, socket, time
import urllib.request, urllib.parse, urllib.error
import database
import auth

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
SYNC_API_KEY  = _cfg("SYNC_API_KEY",       "sync_api_key")   # shared secret for relay↔server calls
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
_railway_domain = os.environ.get('RAILWAY_PUBLIC_DOMAIN', '')
MOBILE_URL = (f"https://{_railway_domain}" if _railway_domain
              else f"http://{LOCAL_IP}:{PORT}")

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


def repair_mojibake(s: str) -> str:
    """Undo multiple layers of UTF-8→Latin-1 mojibake accumulated from repeated encoding passes."""
    for _ in range(8):
        try:
            s = s.encode('latin-1').decode('utf-8', errors='strict')
        except Exception:
            break
    return s


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
        print(f'[whatsapp] Error: {e}' + ((' | Twilio: ' + e.read().decode('utf-8','ignore')) if hasattr(e, 'read') else ''))


def deliver_notification(user_id: int, message: str, url_suffix: str = ""):
    """Look up user notification settings and deliver accordingly."""
    message = repair_mojibake(message)
    conn = database.get_db()
    p = conn.execute(
        "SELECT * FROM user_profiles WHERE user_id=?", (user_id,)
    ).fetchone()
    conn.close()
    if not p:
        return
    channel = p["notification_channel"]
    dashboard_url = f"{MOBILE_URL}{url_suffix}"
    msg_with_link = message + f"\n\n\U0001F4F1 Dashboard: {dashboard_url}"
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


def _scheduler_already_ran(user_id: int, event_type: str, today: str) -> bool:
    """Check activity_log to see if this scheduled job already fired today.
    DB-backed so it survives server restarts (unlike an in-memory dict).
    """
    try:
        conn = database.get_db()
        row = conn.execute(
            "SELECT 1 FROM activity_log "
            "WHERE user_id=? AND event_type=? AND created_date >= ? LIMIT 1",
            (user_id, event_type, today)
        ).fetchone()
        conn.close()
        return row is not None
    except Exception:
        return False



# ── Multipart parsing ─────────────────────────────────────────────────────────

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

