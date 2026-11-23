"""
Job Hunter's own Telegram bot (@Noajobhunter_bot, display name "Noa"): one bot for
every user, a notification channel only (owner, 2026-11-23).

Before this, every user had to create their OWN bot in BotFather and paste its
token and their chat id into Settings - fine for a developer, impossible for the
public. Now:

  1. Settings -> Connect Telegram asks /api/telegram/connect for a one-time code
     and opens  t.me/<bot>?start=<code>.
  2. The user taps Start. Telegram posts "/start <code>" to /telegram/webhook;
     we match the code, store the chat id on the profile and reply "Connected".
  3. "/stop" in the chat (or Disconnect in Settings) turns it off.

Notification-only: a Telegram bot cannot close its chat box, so users CAN type.
The bot acts on /start and /stop and ignores everything else, answering at most
once a day per chat with a fixed line pointing to the app.

Railway variables (web service):
  TELEGRAM_BOT_TOKEN       the token from BotFather - never in a profile, never in chat
  TELEGRAM_WEBHOOK_SECRET  any long random string; Telegram sends it back on every
                           webhook call (X-Telegram-Bot-Api-Secret-Token) and a
                           call without it is refused
  TELEGRAM_BOT_USERNAME    optional; read from getMe when unset

Profiles that still hold their own bot token (the legacy design) keep sending
through that bot until they connect here, which clears it.
"""
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

CODE_MINUTES = 15
HINT_EVERY_SECONDS = 24 * 3600

CONNECTED_TEXT = ("✅ Connected. I'm Noa from Job Hunter, and I'll send your job alerts here.\n\n"
                  "This chat only sends notifications; manage everything in the Job Hunter app. "
                  "Send /stop to turn them off.")
STOPPED_TEXT = ("Stopped. You won't get Job Hunter notifications here any more. "
                "To turn them back on: Job Hunter → Settings → Connect Telegram.")
EXPIRED_TEXT = ("This link has expired or was already used. Open Job Hunter → Settings → "
                "Connect Telegram to get a new one.")
HOW_TO_CONNECT_TEXT = ("Hi, I'm Noa from Job Hunter. To get your job alerts here, open "
                       "Job Hunter → Settings → Connect Telegram.")
NOTIFY_ONLY_TEXT = ("This chat only sends notifications. Manage everything in the Job Hunter app. "
                    "Send /stop to turn them off.")

_username_cache = {"name": None}
_last_hint = {}          # chat id -> time.time() of the last "notifications only" reply


# ── configuration ────────────────────────────────────────────────────────────

def token() -> str:
    return (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()


def webhook_secret() -> str:
    return (os.environ.get("TELEGRAM_WEBHOOK_SECRET") or "").strip()


def configured() -> bool:
    """Both variables are needed: without the secret the webhook cannot tell
    Telegram from anyone else, so the bot is not offered at all."""
    return bool(token() and webhook_secret())


def secret_ok(header_value) -> bool:
    want = webhook_secret()
    return bool(want) and hmac.compare_digest((header_value or "").strip(), want)


# ── Bot API ──────────────────────────────────────────────────────────────────

def api(method: str, params: dict, timeout: int = 10) -> dict:
    """One Bot API call; returns `result`, RAISES when Telegram does not accept it."""
    tok = token()
    if not tok:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
    req = urllib.request.Request(f"https://api.telegram.org/bot{tok}/{method}",
                                 data=json.dumps(params).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            out = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Telegram refused {method}: HTTP {e.code} "
                           f"{e.read().decode('utf-8', 'ignore')[:200]}")
    if not out.get("ok"):
        raise RuntimeError(f"Telegram refused {method}: {str(out)[:200]}")
    return out.get("result")


def send_message(chat_id, text: str):
    api("sendMessage", {"chat_id": chat_id, "text": text, "disable_web_page_preview": True})


def username() -> str:
    env = (os.environ.get("TELEGRAM_BOT_USERNAME") or "").strip().lstrip("@")
    if env:
        return env
    if not _username_cache["name"]:
        _username_cache["name"] = (api("getMe", {}) or {}).get("username") or ""
    return _username_cache["name"]


def setup(public_base_url: str) -> dict:
    """Point Telegram at our webhook and show only /stop in the bot's menu.
    Run once after the variables are set (Admin, or POST /api/admin/telegram/setup)."""
    url = public_base_url.rstrip("/") + "/telegram/webhook"
    api("setWebhook", {"url": url, "secret_token": webhook_secret(),
                       "allowed_updates": ["message"], "drop_pending_updates": True})
    api("setMyCommands", {"commands": [{"command": "stop", "description": "Stop notifications"}]})
    me = api("getMe", {}) or {}
    info = api("getWebhookInfo", {}) or {}
    _username_cache["name"] = me.get("username") or _username_cache["name"]
    return {"bot": "@" + (me.get("username") or ""), "name": me.get("first_name"),
            "webhook": info.get("url"), "pending": info.get("pending_update_count"),
            "last_error": info.get("last_error_message")}


# ── connect codes ────────────────────────────────────────────────────────────

def _hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_connect_code(conn, user_id: int) -> str:
    """32 characters of [A-Za-z0-9_-], within Telegram's 64-character start
    parameter. Only its hash is stored; one live code per user."""
    code = secrets.token_urlsafe(24)
    conn.execute("DELETE FROM telegram_connect_codes WHERE user_id=?", (user_id,))
    conn.execute("INSERT INTO telegram_connect_codes (code_hash, user_id, expires_at) VALUES (?,?,?)",
                 (_hash(code), user_id, (_now() + timedelta(minutes=CODE_MINUTES)).isoformat(" ")))
    conn.commit()
    return code


def connect_url(code: str) -> str:
    return f"https://t.me/{username()}?start={code}"


def _take_code(conn, code: str):
    """The user id for a live code, consuming it; None if unknown or expired."""
    row = conn.execute("SELECT user_id, expires_at FROM telegram_connect_codes WHERE code_hash=?",
                       (_hash(code),)).fetchone()
    if not row:
        return None
    conn.execute("DELETE FROM telegram_connect_codes WHERE code_hash=?", (_hash(code),))
    conn.commit()
    if str(row[1]) < _now().isoformat(" "):
        return None
    return int(row[0])


# ── channels on the profile ──────────────────────────────────────────────────

def _channels(value) -> list:
    return [c.strip() for c in str(value or "").split(",") if c.strip() and c.strip() != "none"]


def with_channel(value, channel: str) -> str:
    chans = _channels(value)
    if channel not in chans:
        chans.append(channel)
    return ",".join(chans) or "none"


def without_channel(value, channel: str) -> str:
    return ",".join(c for c in _channels(value) if c != channel) or "none"


def link(conn, update_profile, user_id: int, chat_id) -> None:
    """Attach this chat to the account. Clears a legacy own-bot token, so the
    account's notifications now come from Job Hunter's bot."""
    row = conn.execute("SELECT notification_channel FROM user_profiles WHERE user_id=?", (user_id,)).fetchone()
    update_profile(user_id, telegram_chat_id=str(chat_id), telegram_token="",
                   notification_channel=with_channel(row[0] if row else "", "telegram"))


def unlink(conn, update_profile, user_id: int) -> None:
    row = conn.execute("SELECT notification_channel FROM user_profiles WHERE user_id=?", (user_id,)).fetchone()
    update_profile(user_id, telegram_chat_id="", telegram_token="",
                   notification_channel=without_channel(row[0] if row else "", "telegram"))


def linked_users(conn, chat_id) -> list:
    """Accounts this chat receives Job Hunter's bot notifications for (not a
    legacy own-bot setup, whose chat id belongs to another bot)."""
    rows = conn.execute("SELECT user_id, telegram_token FROM user_profiles WHERE telegram_chat_id=?",
                        (str(chat_id),)).fetchall()
    return [int(r[0]) for r in rows if not (r[1] or "").strip()]


# ── the webhook ──────────────────────────────────────────────────────────────

def handle_update(conn, update: dict, update_profile, send=None) -> str:
    """Act on one Telegram update. Returns what was done (for logs and tests).
    `send(chat_id, text)` defaults to the Bot API; tests pass a recorder."""
    send = send or send_message
    msg = update.get("message") or {}
    chat = msg.get("chat") or {}
    if chat.get("type") != "private" or "id" not in chat:
        return "ignored: not a private message"
    chat_id = chat["id"]
    text = (msg.get("text") or "").strip()
    cmd, _, arg = text.partition(" ")
    cmd = cmd.split("@", 1)[0].lower()          # "/start@Noajobhunter_bot" in some clients

    if cmd == "/start":
        arg = arg.strip()
        if arg:
            uid = _take_code(conn, arg)
            if uid is None:
                send(chat_id, EXPIRED_TEXT)
                return "refused: unknown or expired code"
            link(conn, update_profile, uid, chat_id)
            send(chat_id, CONNECTED_TEXT)
            return f"connected user {uid}"
        if linked_users(conn, chat_id):
            send(chat_id, NOTIFY_ONLY_TEXT)
            return "already connected"
        send(chat_id, HOW_TO_CONNECT_TEXT)
        return "start without a code"

    if cmd == "/stop":
        uids = linked_users(conn, chat_id)
        for uid in uids:
            unlink(conn, update_profile, uid)
        send(chat_id, STOPPED_TEXT)
        return f"stopped {len(uids)} account(s)"

    # Anything else: notification-only. One fixed answer a day per chat at most.
    now = time.time()
    if now - _last_hint.get(chat_id, 0) >= HINT_EVERY_SECONDS:
        _last_hint[chat_id] = now
        send(chat_id, NOTIFY_ONLY_TEXT)
        return "ignored: hinted"
    return "ignored"
