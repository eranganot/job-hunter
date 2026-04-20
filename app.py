#!/usr/bin/env python3
"""
Job Hunter – Standalone Multi-User App
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
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, urlparse

import auth
import db as database
from ai_analysis import analyze_cv

# ── Compiled Tailwind CSS (gzipped + base64, 19KB → 4KB stored) ───────────────
import gzip as _gz, base64 as _b64
_TW_CSS = _gz.decompress(_b64.b64decode("H4sIAH8xyGkC/+08aa/kNnJ/RdmBgdcTSaOzDz1ssMEiCyyw3g9xAmSxMx/ULapbfrqi473Xo3R+e4qnSEndksYTAwvYhsfNqmJdLBbJIjUf9SCMG1TpwRHFRYU6w2jejGNRRagy6jI8JfnZeA+s5yn4lcObKszrNGxQT9qDBFVVNNDmrfoFvfXkpCUo61OYYl623LzyZhnmgNP6xlU0kvx0Mb4WRcYh9akq0tSo87A06qZKTk2O6jooq+I9yZLmSqnOVRglKG+MuCoyoyzqpEmKnPMQ2NckvI9sihEOvJXkYSp0ScP6giLjK6oKDsvbDIFWRpyc2woNoczRI+IqPMlyKjwYSV6jRoEUcQwg4y2Jmktgle9j1KlIiyr4EMexhKPA6nwMn/yDbruW7nhb3fQ34/71JYyKt8DSLO2DBf9IFNOoR1AqGEVD7DFtK27XsUrOFzqEDHIqcgi1upHG40rihQMuLeKRxyBJ/ooq0aEOm7aSsDUqk5A3oqooudJchfD0QsCKXgI4UlCghpoKxEhlgRnrLlCqEQJc4IhpriP40MoeIZuLVQyT3KiTr2gIS8Nr0TZDaAl/jIB1c8XG3IKAi/ktrfyWVn5LK7+lle+SVj59/CcNAOlbkkenutZeXdMz7YP2P9qPf/4P7S/JCUHMQuvSNGUdfPok0ZqnIvv46eNw03Ms3rFyeE6wXASQZ/oT4qYu0iTSPiAf7dDxNrVhwirCbA1+97tbcCnqRr80WdqlSY6MC8JDF9im/2y8oeNLAnMavTfEGUYY/dzC0NmW9cOzkRVfjSY8Ujd5z0Yht6SfcYFTSpgl6TVoExiDvAZ/V0ms19e6QZnRJroE/NeyTJH2RzwLtH/Lip8T/Sd0LpD2n38eNn+6Zsci1f9aNIVMzwQiPNYImDYNOKoO8qLKwpQiX8MqCXHuGKGFzZApL+CJFHuDJQOSzMuwAs/djkV07bKwOic5pGvZc0l+ATua26XqGMR6pv0Zhg0UpMqSZSW7fL+Fx2MVvAEBevp7kzQp+rLplAGI0KmoiNJBm0N/LFOLiqZB0fMcwe1i6xdHv7j6xdMvvn7ZdsQPZIS4WgTyphoRdqrqQ0Gc7qjDslLk505mAoMDatxORYT0l2OklxWCgc7KbhARWZEXOMcj/ac//Qi/jX9H5zYNK/1HlKeFDqDwVOh/LHII7LDW/5IcERWvYWpAtFWCKu2v6E0XrH5REPS+sVF2qwGUSv7aWz/c6hYsbksJuvN/UMLAehbrYIVgB5C8omecqxJIcUYIYZXDbqNGuAvmBnO6aWDBNkzHxzKBN8QHNHELJlOKOuJ7yAt44lo8iCYjC4BpWNYo4D9uxxa453qSl22jF2Vzroq21EE8OjU6ZgxRHSrjokTFyI8KdsKRCp56EueMiRCbmjvPKfDpd1gCrBorZl8ZRhEms7iZ1C7qMDJpIfNlMLi56ggx3a4l+j1FfNlMoCoEdk1iYOBg+yTN1LAsUQgSTyigDJ/xEoO9nUfjLCIjkyw8I6pjQFJrXJzaGm8iOlhwsJOCsG0KhoRZA0sfhFFEFwO6NpPesKk7g8Z1dy/agoBrm+Q58TIsVswvPQ6EqjiezYgWzHyw9XT5MmU99n2coDR6ZtqzzVJgOJDsejGUhZRSpphRr4g+cQL74LZMizDiut33Pw45kabqNoOouXZRUpewkgdpUoMXYA26HdPi9PLfbdEgPYr0KNXpVlQfJU79UuklTmUi+9+InWDbGKKn6IzyqOsjNEN5q8Oi1eLlFoTTTQI2cCqeoyRMi7PUXUxVGGE8q/gY32hs0uAA007oQpKvmNtjVMc3STabVx8Op9ANY85qmssSBn+Hw4aYUDoboVNb1UBTFrBTgkUhgCHAWS3iiAjFYZvCitNGSaGfQojuWkfZEUV6Ajv9DOlJdtaL4884Y9WvZ/01iVAhRpKM3zDBZkkUpeiGO1LqLHznSy5OR0pEX4AY5V/Y/IYhaZ4Y7Pdt3iQpTEiYp182GyGThKXJLDLQK8zo2sDAToUxwjh5B3PFqkCaN5MvDt1oubiZNRjzcu0xtH0zySHHsDp62LFuJl08jC1fRWD3VkFQmymKG8P+/Mnp8K/Ah5XLxNsOiywu0PGr4VrdV7KqvAcuAXg9wCMAvwf4AMjeDewwFuwGYYwBLH6NqvepmR0NmxMy1WB9I6ph1GfTH2LdnUA7Q5zAuENM38kboGyKSHs9iMJCi6pHUMUFBhws6Yf9Zdo9zlYQEnzYRxjU9AYRhAC7Crgn92S4zaFbBcq4xCl6F2GJGzhGSNZVMBLsZp4rWDw4BjduJg33QXxfDNviud9h8i5gCwMJiN+Jo4PDYXvRkaoPal9wMQKBFNJgPSzr9XIz37AkOj25IAA5DORygMcBgoaTCIDPAEKVN1CFcWbtuIVNXZ8LqHJvMDPI/2mxAIA4YRjOeyqlDo8ZQ1CegvK3EiqLJIyzlzB1JmM8MYQQVfh/ga3ZGlapvsD6/wI6ESRtYa3kShOd3qP6E8zaH577vY9APcFm7WlIvdEnoNfNRqOn7r4PbQMCF6r+q4fTKhaD/20Ax4zIwV7uQEtbHPO3IQb63P7wgq4k89da2aY16sAkseaY/u1mhjnsmkBViqYtnCdJW3Nq7dQek5NxRF/hfPBkerqlm1vd3mhJHic5rPs3k64+kLVh75CmxVu/IEkgQcby+nAxM+lqTHM/W5np5MHzCu/7aohR8hv2GiVxLwDbLK8h28OOpXlydIg7CIonS7fjCqynAfFWhWUnfgX4D5jawAROy7AiNx1Z6MgWpg5olGAwp8F7jxEFADn+hIg5MgkF3Ux80E/iKycRTVY5GJIdUfOGZ/WQjhzGOBb8Acdqu4M/Rc4kEJw0CZBnSwx1KKgHuBQgkXgEQnMjlXQ17H+R1+4vm/9Vm3SqcOIKVuiqxtVYKatCBJ6eqH4fn2zN0PrgVLttNptndbmRu97v1SvrfCdlv13Xxaq630nV3bfrulusrPd9lLW/VVV7TtECfsWQXAy25vJ2QNsSAdtu9W22txKA64Dgygiaqs1PkGqGvGkRRwBRmiZlndR9B918u0AyoFrnBclBBEINCSgIsh7eEKOILI+s8oCvAdqazUiOJ0utSnCAf+AcKEjS84CATXuOz6IhnmcKTjHSgecJCuXIvuDGEJBmFJQjoY6dKK+S/fW4cwopiP/Ee222Q5Aomm6y3MfREbkM4TT0NEhhgiSEQxBsUy1LuZXqD19KJag6H58c39Mcz9Xsw+GTiEG1GyyCm4EAZ40AV3NcS7Pd/QoB3hoBsAU62Jq7XcT/mLZojf6YtWMfNHDUcv7uCv7eDtTfAX93Of8V7jlsNXvrA3trEXuUoSpMo1Ue2u5ICDnWshE+4y39KgF7LGC3WEDZVmW6apAdF9S34T/fXyShQtG6OQCTzHLwf4vY1y/Xdf7Zkim2NITonn1NkvBgDngQRN5hhYA1DnKwBeAgby5Kz6MUd5YZDwuo91Pc+Q5jfwVfPK3AM66/iO9yxuBq299rtv2Q7zGFzp8/eVY3xSWE0wH+1/Q4NeSNFca5kPK87WBGTCkBbLfLbXN32uEw6zKehdboi3V1cZg+5EyTzwq+nsX4egv4rnCE40BedrXdY7Ysla0JSotO1ZlxwxnMXzmF8Px0Zrmu8oGluXtN2RmMmeJsuHLAvMOsA4ZJcAHjqSR4j7GzxgtTye8e4zWe2GskHHxnAd/DcoVtcIKrzYQCOQSsS6XzUSs/Haq60Y0YrliGlaB6agqNFGr7kpVgUMMWuyalE/x+SUmOytOm4AOK420ca2MWysunzehtE03CkEfJYyCwTLc2E1ykR1BDHkTHYFrwRp9ixe1Rs9zAoNiKo9j7ZoM8C4xxwSjvVzRoEKMDi6zY3jnht1pk+7rj6p7z65iDX8SRcBsbwxVyLd3f63Bg+s4afbCRG+6n/CQ/07urOcgliu+nFAfenhXGM+oSJlJmUDjE8Xx3eqcHmeMVjulGYbB2nDQBgT0PATcTV/74pSitNZTGtoew4kH5Dod7BmTXPhjB71f5hU9PjMuQKv3WmeqxdUQfd9BhN0W/E+SeSm6PiZk5V3r7xHHS9ZOg55df/FYK+thqh2l6iXwkwt1NduHFFtzHUno45lQHfn2D6R2F3p2gdgXtVqH1Jmg9TquyndSiV8JViaeN7G30FHJ7gpiN0tFw9t0AteMor5vulMo+JGEg3FXJqEq5ciubKZNh8uFiHuZCn7vQi2/cZihWv5eQvHRPQESIjCUAhsSFPekFD3Wy/F7H6VXwFFqHxZ5CLGuMX6PIvIfEtmLeWVHDHvO2+ejRt4qZRG/udxPkjkQ+sHKO+3stc59gTu/zMAl+Aac8idtZFkNlKEraTEH6AknfoSlITyBrlCUjxluMTlFIwsPvJo3laHJNpVDg2i9svEhokYAYPMAyTIs8S6MOGBx+aTl58Bbk3plXJqZbQInlbpalvbe0vasdFjLczzOE8/DW02xvlqNaBHzAcKL294ilN8tyqtz3iOP8yPgHzcbFJW+7jON2luNEDeARw/mRdg7aDs469ryGfWFhdmzAkXuISeewmOm8ohA8cOTd72ZZ8g38rCs9EkAHbyHHBb60Qce9tp2fh6w2sWAiwgHXxRXQ+fGhZYlZHd2DtgUd94v4zQfkRB3iAb8lmQdmIATkPD9c25jn52q2BaG4YEwGxYcHFk/UHB7ynM9mjsWK6I6/kKe3IEPuSZnM3nsLeS6Y2ZYFywxMb/ewkOd8BO1sDScLa6nl82Pu2xrk8f1ShvOrF+Rwz9b8pTYf5v04KgPdYSgd+O4Hz1QFaMwvPOEdab/QsDZ7U+r4WxcdBVVfk1XJ7G3oeqEg68dYJfN2vr89wJmTKmBY4kGR1QNxAVHY04PdHmq6PdiTwF4P9i35rZLJPu7CO2r1EzDHL98134I/4KgEf8plfod/bTb6NGzYq39qIFFuZLE6/02u2sWT8f5p1+iDNl18gSaVD6Sv2ibxFNVLBmmqvTZW2sbqG+7AXHsDLD0AbjHWG2Hv+GLAcdoVQ84zDsuigdqisz2lNB4CTGE4S5VW+N1VWeE7q7IYYjj//IpDDNIGQwzKYoUtxRXW3WDu6e+ZSFRirxwnvrxUVSevojeaRf6dNJ48w9hM46jI8SecD2SQFz/YgH9+JG4ojwlaNlDTwzMaFGm8mNPUAwFlLFL1xNeuk0cDuRtbA/AzUVLAw28kO6lZVkWJKmAP8GcJ3iQZ5hK3Of1od+JppgO85S5Ry74yM22/VmQSdetJsQSlD28kdPl6Wh98xcagcZKm5DO2F/T/pjhz4qTmDPd9ZfMm2etNkrlAdcFl1c/B4DlKcKEl2e/7KEWVJTai64RN7ksfSWMzwR4Imr/Fws+E8EHHf3CpOxDir5Wx5F5+IGO32pDxefq+CHGwXOus0Tnzvgx27FonYeIUdl+CuA5eacWSS+GhlNVjvuQilwlRKjuymF9Y3xmx3y5kv6jYI3NnJYBFzBcVAmTm5CiyjPWyAwljLj6iZryHqwZBCSLRTWx8ZZX+Mba/z7/GrhH/pR2vCPxEPvQ4+AEFdMpfeWIefPUvPcGAf+zPWYThPIXD8V6xfcFDK4vUqncPlglJCE3i66XgV0yWrT1ITH/Alxeh9tR/KrX1IAo3nVlnn+mnmOqHmTeC+AUfw5DuYWnQz0TEjQYGl+/G4DrNGlypWpRw5dWQ6LL4euj2f/yWjc6eTAAA"))

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


RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")


RESEND_VERIFIED_EMAIL = os.environ.get("RESEND_VERIFIED_EMAIL", "eran.ganot@gmail.com")


def send_email(to_addr: str, subject: str, body: str, **_kwargs):
    """Send an email notification via Resend SDK.
    Uses onboarding@resend.dev sender which only delivers to the verified account email.
    """
    if not RESEND_API_KEY:
        raise RuntimeError("RESEND_API_KEY not configured")
    try:
        import resend
    except ImportError:
        raise RuntimeError("resend package not installed")
    # onboarding@resend.dev can only send to the Resend account's verified email
    actual_to = RESEND_VERIFIED_EMAIL
    resend.api_key = RESEND_API_KEY
    result = resend.Emails.send({
        "from": "Job Hunter <onboarding@resend.dev>",
        "to": [actual_to],
        "subject": subject,
        "text": body,
    })
    if result.get("id"):
        print(f"[email/resend] ✅ Sent to {actual_to} — {result['id']}")
    else:
        print(f"[email/resend] ⚠️  {result}")


def _log_notification(user_id: int, channel: str, status: str, error_msg: str = ""):
    """Log notification attempt to activity_log for visibility."""
    try:
        detail = f"[{channel.upper()}] {status}"
        if error_msg:
            detail += f" - {error_msg[:200]}"
        database.log_activity(user_id, "notification", detail)
    except Exception:
        pass


def deliver_notification(user_id: int, message: str, url_suffix: str = ""):
    """Look up user notification settings and deliver accordingly."""
    message = repair_mojibake(message)
    conn = database.get_db()
    p = conn.execute(
        "SELECT * FROM user_profiles WHERE user_id=?", (user_id,)
    ).fetchone()
    conn.close()
    if not p:
        print(f"[notify] No profile found for user {user_id}")
        return
    channels = [ch.strip() for ch in (p["notification_channel"] or "none").split(",")]
    if not channels or channels == ["none"]:
        print(f"[notify] No notification channels configured for user {user_id}")
        return
    dashboard_url = f"{MOBILE_URL}{url_suffix}"
    msg_with_link = message + f"\n\n\U0001F4F1 Dashboard: {dashboard_url}"
    for channel in channels:
        try:
            if channel == "telegram" and p["telegram_token"] and p["telegram_chat_id"]:
                send_telegram(p["telegram_token"], p["telegram_chat_id"], msg_with_link)
                _log_notification(user_id, "telegram", "Sent OK")
                print(f"[notify] Telegram sent to user {user_id}")
            elif channel == "whatsapp" and p["twilio_account_sid"] and p["whatsapp_number"]:
                send_whatsapp(p["twilio_account_sid"], p["twilio_auth_token"],
                              p["whatsapp_number"], msg_with_link)
                _log_notification(user_id, "whatsapp", "Sent OK")
                print(f"[notify] WhatsApp sent to user {user_id}")
            elif channel == "email" and p["email_address"]:
                send_email(
                    to_addr=p["email_address"],
                    subject="Job Hunter Notification",
                    body=msg_with_link,
                )
                _log_notification(user_id, "email", "Sent OK")
                print(f"[notify] Email sent to user {user_id}")
        except Exception as _notif_err:
            _log_notification(user_id, channel, "FAILED", str(_notif_err))
            print(f"[notify] Error on {channel}: {_notif_err}")


def notify_admin_new_user(new_user_email: str, new_user_name: str):
    """Send a new-signup alert to the admin via ALL their configured channels."""
    if not ADMIN_EMAIL:
        return
    conn = database.get_db()
    admin_row = conn.execute(
        "SELECT id FROM users WHERE lower(email)=lower(?)", (ADMIN_EMAIL,)
    ).fetchone()
    if not admin_row:
        conn.close()
        print(f"[admin-notify] No admin user row for {ADMIN_EMAIL}")
        return
    admin_id = admin_row["id"]
    conn.close()
    display = new_user_name or new_user_email
    message = f"\U0001F680 New User Alert: {display} ({new_user_email}) has joined Job-Hunter."
    try:
        deliver_notification(admin_id, message, url_suffix="/dashboard")
    except Exception as e:
        print(f"[admin-notify] failed (non-fatal): {e}")


def bump_onboarding(user_id: int, key: str):
    """Set a single onboarding milestone to true (idempotent)."""
    try:
        conn = database.get_db()
        row = conn.execute(
            "SELECT onboarding_progress FROM user_profiles WHERE user_id=?", (user_id,)
        ).fetchone()
        if not row:
            conn.close()
            return
        progress = json.loads(row["onboarding_progress"] or "{}")
        if progress.get(key):
            conn.close()
            return  # already set
        progress[key] = True
        conn.execute(
            "UPDATE user_profiles SET onboarding_progress=? WHERE user_id=?",
            (json.dumps(progress), user_id)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[onboarding] bump {key} for user {user_id}: {e}")


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


def _check_scheduled_jobs() -> None:
    """Auto-trigger search/apply for each active user when their scheduled hour arrives."""
    try:
        now = datetime.now(__import__("datetime").timezone(__import__("datetime").timedelta(hours=3)))  # Israel time (GMT+3)
        today = now.strftime('%Y-%m-%d')
        current_hour = now.hour
        conn = database.get_db()
        rows = conn.execute(
            "SELECT u.id, p.search_hour, p.apply_hour, "
            "p.schedule_frequency, p.search_day_of_week, p.apply_day_of_week, p.weekdays_only, p.auto_apply_enabled "
            "FROM users u JOIN user_profiles p ON p.user_id = u.id "
            "WHERE u.is_active = 1"
        ).fetchall()
        conn.close()
        for row in rows:
            uid, sh, ah = row[0], row[1], row[2]
            freq = row[3] or 'daily'
            s_dow = row[4]
            a_dow = row[5]
            wo = row[6]
            auto_apply = row[7]
            cur_dow = now.weekday()  # 0=Mon ... 6=Sun
            # Skip weekends if weekdays_only
            if wo and cur_dow >= 5:
                continue
            # Search: check hour + frequency/day
            if current_hour == sh and not _scheduler_already_ran(uid, 'jobs_searched', today):
                run_search = True
                if freq == 'weekly' and s_dow is not None and cur_dow != s_dow:
                    run_search = False
                if run_search:
                    print(f'[scheduler] Triggering search for user {uid} at hour {sh}')
                    threading.Thread(target=run_job_search, args=(uid,), daemon=True).start()
            # Apply: check hour + frequency/day
            if current_hour == ah and not _scheduler_already_ran(uid, 'job_applied', today):
                run_apply = True
                if freq == 'weekly' and a_dow is not None and cur_dow != a_dow:
                    run_apply = False
                if not auto_apply:
                    run_apply = False
                if run_apply:
                    print(f'[scheduler] Triggering apply for user {uid} at hour {ah}')
                    threading.Thread(target=run_job_apply, args=(uid,), daemon=True).start()
    except Exception as e:
        print(f'[scheduler] Error: {e}')


def file_watcher():
    while True:
        database.import_pending_jobs(BASE_DIR)
        database.import_applied_updates(BASE_DIR)
        _check_scheduled_jobs()
        time.sleep(60)


_search_running: set = set()


def run_job_search(user_id: int):
    """Search for new jobs via multi-round Anthropic web-search (one call per job title)."""
    if user_id in _search_running:
        return
    _search_running.add(user_id)
    try:
        conn = database.get_db()
        profile = conn.execute(
            "SELECT * FROM user_profiles WHERE user_id=?", (user_id,)
        ).fetchone()
        conn.close()
        if not profile:
            print(f"[run-search] No profile for user {user_id}")
            return
        import urllib.request as _ur
        import urllib.error  as _ue
        try:
            titles    = json.loads(profile["job_titles"] or "[]")
            keywords  = json.loads(profile["keywords"]   or "[]")
            locations = json.loads(profile["locations"]  or "[]")
        except Exception:
            titles, keywords, locations = [], [], ["Tel Aviv"]
        if not locations: locations = ["Tel Aviv"]
        if not titles:    titles    = ["Senior Product Manager"]
        today = datetime.now().strftime("%Y-%m-%d")

        # ── Load all existing URLs to dedup against full history ─────────
        conn = database.get_db()
        existing_urls = {r[0] for r in conn.execute(
            "SELECT url FROM jobs WHERE user_id=? AND url!='' "
            "AND status NOT IN ('rejected','expired') "
            "AND found_date >= date('now', '-45 days')", (user_id,)
        ).fetchall()}
        conn.close()

        def _search_jobs_with_claude_websearch(titles_: list, locs_: list, kws_: list) -> list:
            """Search Israeli jobs via Greenhouse/Lever APIs, filter by preferences, score against CV."""
            import threading as _thr, urllib.request as _ur2, json as _js2
            all_raw = []
            _lk = _thr.Lock()

            # -- Israeli company slugs (Greenhouse) --
            _GH_COMPANIES = {
                'similarweb': 'SimilarWeb', 'taboola': 'Taboola', 'payoneer': 'Payoneer',
                'forter': 'Forter', 'riskified': 'Riskified', 'appsflyer': 'AppsFlyer',
                'fireblocks': 'Fireblocks', 'cybereason': 'Cybereason', 'jfrog': 'JFrog',
                'wizinc': 'Wiz', 'honeybook': 'HoneyBook', 'optimove': 'Optimove',
                'transmitsecurity': 'Transmit Security', 'via': 'Via', 'nice': 'NICE',
                'yotpo': 'Yotpo', 'bringg': 'Bringg', 'bigid': 'BigID',
                'axonius': 'Axonius', 'lightricks': 'Lightricks', 'catonetworks': 'Cato Networks',
                'snyk': 'Snyk', 'sentinelone': 'SentinelOne', 'monday': 'monday.com',
                'wix': 'Wix', 'fiverr': 'Fiverr', 'tipalti': 'Tipalti',
                'checkmarx': 'Checkmarx', 'rapyd': 'Rapyd', 'lemonade': 'Lemonade',
                'papayaglobal': 'Papaya Global', 'deel': 'Deel', 'drata': 'Drata',
                'hibob': 'HiBob', 'ironclad': 'Ironclad', 'nextinsurance': 'Next Insurance',
                'playtika': 'Playtika', 'gett': 'Gett', 'outbrain': 'Outbrain',
                'guardicore': 'Guardicore', 'earnix': 'Earnix', 'pentera': 'Pentera',
                'drivenets': 'DriveNets', 'orcasecurity': 'Orca Security',
                'aquasecurity': 'Aqua Security', 'seekingalpha': 'Seeking Alpha',
                'fundbox': 'Fundbox', 'ironsource': 'ironSource',
                # Global tech companies with Israeli offices (Greenhouse)
                'cyberark': 'CyberArk', 'varonis': 'Varonis', 'zscaler': 'Zscaler',
                'sisense': 'Sisense', 'gong-io': 'Gong', 'armis': 'Armis',
                'safebreach': 'SafeBreach', 'cellebrite': 'Cellebrite', 'sealights': 'SeaLights',
                'datarails': 'DataRails', 'bizzabo': 'Bizzabo', 'lusha': 'Lusha',
                'perion': 'Perion', 'akamai': 'Akamai', 'illumio': 'Illumio',
            }
            # -- Israeli company slugs (Lever) --
            _LV_COMPANIES = {
                'walkme': 'WalkMe', 'cloudinary': 'Cloudinary',
                # Global tech with Israeli offices (Lever)
                'kaltura': 'Kaltura', 'namogoo': 'Namogoo', 'guesty': 'Guesty',
                'skai': 'Skai', 'nexthink': 'Nexthink', 'bringg': 'Bringg',
            }
            # Build title match phrases from user preferences
            def _expand_title_variants(title):
                """Expand a job title into structural variants (VP<->VP of<->Vice President<->Head of<->Director, Senior<->Sr.<->Lead).
                Pure linguistic rules — works for any domain the user enters."""
                t = title.lower().strip()
                variants = {t}
                level_rules = [
                    ('vp of ',            ['vice president of ', 'head of ', 'director of ']),
                    ('vp ',               ['vice president ', 'head of ', 'director of ']),
                    ('vice president of ', ['vp of ', 'head of ', 'director of ']),
                    ('vice president ',   ['vp ', 'head of ', 'director of ']),
                    ('head of ',          ['director of ', 'vp of ', 'vp ']),
                    ('director of ',      ['head of ', 'vp of ', 'vp ']),
                    ('director ',         ['head of ', 'vp ']),
                    ('chief ',            ['vp of ', 'head of ']),
                ]
                seniority_rules = [
                    ('senior ',  ['sr. ', 'sr ', 'lead ']),
                    ('sr. ',     ['senior ', 'sr ', 'lead ']),
                    ('sr ',      ['senior ', 'sr. ', 'lead ']),
                    ('lead ',    ['senior ', 'sr. ']),
                    ('principal ', ['senior ', 'lead ', 'staff ']),
                    ('staff ',   ['principal ', 'senior ']),
                ]
                for pattern, replacements in level_rules + seniority_rules:
                    if t.startswith(pattern):
                        rest = t[len(pattern):]
                        for r in replacements:
                            variants.add(r + rest)
                return list(variants)

            _phrases = []
            for _t in titles_:
                if _t.strip():
                    _phrases.extend(_expand_title_variants(_t))
            _phrases = list(set(_phrases))
            # Also build 2-word combos from each title for partial matching
            _bigrams = set()
            for _t in titles_:
                words = [w for w in _t.lower().split() if len(w) > 2]
                for i in range(len(words)):
                    for j in range(i+1, len(words)):
                        _bigrams.add((words[i], words[j]))
            print(f"[search] Matching phrases: {_phrases}, bigrams: {len(_bigrams)}")

            def _title_match(title):
                tl = title.lower()
                # Full phrase match (e.g. "product manager" in title)
                if any(phrase in tl for phrase in _phrases):
                    return True
                # Bigram match: at least 2 words from same user title appear
                for w1, w2 in _bigrams:
                    if w1 in tl and w2 in tl:
                        return True
                return False

            def _get_json(url, timeout=20):
                try:
                    rq = _ur2.Request(url, headers={"User-Agent": "JobHunter/1.0"})
                    with _ur2.urlopen(rq, timeout=timeout) as r:
                        return _js2.loads(r.read().decode("utf-8", errors="replace"))
                except Exception as e:
                    return None

            # -- Query Greenhouse boards --
            def _query_gh(slug, company_name):
                data = _get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")
                if not data: return
                for j in data.get("jobs", []):
                    t = j.get("title", "")
                    if not _title_match(t): continue
                    loc = j.get("location", {}).get("name", "") if isinstance(j.get("location"), dict) else ""
                    jurl = f"https://boards.greenhouse.io/{slug}/jobs/{j.get('id', '')}"
                    _gh_content = re.sub(r'<[^>]+>', ' ', j.get("content", "")).strip()[:5000] if j.get("content") else ""
                    with _lk:
                        all_raw.append({"job_title": t, "company": company_name,
                                        "location": loc, "url": jurl,
                                        "description": (_gh_content[:300] if _gh_content else t),
                                        "full_description": _gh_content or t, "source": "greenhouse"})

            # -- Query Lever boards --
            def _query_lv(slug, company_name):
                data = _get_json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
                if not isinstance(data, list): return
                for j in data:
                    t = j.get("text", "")
                    if not _title_match(t): continue
                    cats = j.get("categories") or {}
                    loc = cats.get("location", "")
                    if not loc:
                        al = cats.get("allLocations") or []
                        loc = al[0] if al else ""
                    jurl = j.get("hostedUrl") or f"https://jobs.lever.co/{slug}/{j.get('id', '')}"
                    desc = (j.get("descriptionPlain") or t)[:300]
                    full_desc = j.get("descriptionPlain") or t
                    with _lk:
                        all_raw.append({"job_title": t, "company": company_name,
                                        "location": loc, "url": jurl,
                                        "description": desc, "full_description": full_desc, "source": "lever"})
            # -- Run all API queries in parallel --
            print(f"[search] Querying {len(_GH_COMPANIES)} Greenhouse + {len(_LV_COMPANIES)} Lever boards (global + IL)...")
            threads = []
            for slug, name in _GH_COMPANIES.items():
                threads.append(_thr.Thread(target=_query_gh, args=(slug, name), daemon=True))
            for slug, name in _LV_COMPANIES.items():
                threads.append(_thr.Thread(target=_query_lv, args=(slug, name), daemon=True))

            # Launch in batches of 30
            for i in range(0, len(threads), 30):
                batch = threads[i:i+30]
                for t in batch: t.start()
                for t in batch: t.join(timeout=25)

            # -- TechMap product.csv (curated Israeli product jobs) --
            try:
                import csv as _csv, io as _io
                _tm_url = 'https://raw.githubusercontent.com/mluggy/techmap/main/jobs/product.csv'
                _tm_req = _ur2.Request(_tm_url, headers={"User-Agent": "JobHunter/1.0"})
                with _ur2.urlopen(_tm_req, timeout=15) as _tm_resp:
                    _tm_text = _tm_resp.read().decode('utf-8', errors='replace')
                _tm_count = 0
                for _row in _csv.DictReader(_io.StringIO(_tm_text)):
                    _tm_title = (_row.get('title') or '').strip()
                    if _title_match(_tm_title):
                        _tm_url_j = (_row.get('url') or '').strip()
                        if _tm_url_j:
                            all_raw.append({"job_title": _tm_title, "company": _row.get('company',''),
                                            "location": _row.get('city',''), "url": _tm_url_j,
                                            "description": _tm_title, "source": "techmap"})
                            _tm_count += 1
                print(f"[search] TechMap CSV: {_tm_count} product jobs matched")
            except Exception as _tme:
                print(f"[search] TechMap CSV error: {_tme}")

            # -- Comeet boards for Israeli companies --
            _comeet_slugs = {'monday': 'monday.com', 'ironsource': 'ironSource', 'gong': 'Gong',
                             'yotpo2': 'Yotpo', 'lightricks2': 'Lightricks'}
            for _cm_slug, _cm_name in _comeet_slugs.items():
                try:
                    _cm_url = f'https://www.comeet.co/careers/api/{_cm_slug}/positions'
                    _cm_data = _get_json(_cm_url, timeout=12)
                    if isinstance(_cm_data, list):
                        for _cm_j in _cm_data:
                            _cm_t = _cm_j.get('name', '')
                            if _title_match(_cm_t):
                                _cm_loc = ''
                                if _cm_j.get('location'):
                                    _cm_loc = _cm_j['location'].get('name', '') if isinstance(_cm_j['location'], dict) else str(_cm_j['location'])
                                all_raw.append({"job_title": _cm_t, "company": _cm_name,
                                                "location": _cm_loc, "url": _cm_j.get('url',''),
                                                "description": _cm_t, "source": "comeet"})
                except Exception:
                    pass
            print(f"[search] Comeet + SpeakNow queries done")

            # -- SpeakNow careers --
            try:
                _sn_req = _ur2.Request('https://speaknow.co/careers/', headers={"User-Agent": "JobHunter/1.0"})
                with _ur2.urlopen(_sn_req, timeout=12) as _sn_resp:
                    _sn_html = _sn_resp.read().decode('utf-8', errors='replace')
                import re as _re_sn
                # Find job links on the careers page
                _sn_links = _re_sn.findall(r'href=["\'](https?://[^"\'>]*(?:career|job|position|apply)[^"\'>]*)["\'\s>]', _sn_html, _re_sn.IGNORECASE)
                for _sn_url in set(_sn_links[:10]):
                    all_raw.append({"job_title": "SpeakNow Career Opportunity", "company": "SpeakNow",
                                    "location": "Israel", "url": _sn_url,
                                    "description": "Career opportunity at SpeakNow", "source": "speaknow"})
            except Exception as _sne:
                print(f"[search] SpeakNow error: {_sne}")

            # -- Sparkhire careers (best-effort) --
            _SPARKHIRE_COMPANIES = [
                ('hibob', 'HiBob'), ('monday', 'monday.com'), ('fiverr', 'Fiverr'),
                ('rapyd', 'Rapyd'), ('gong', 'Gong'), ('lemonade', 'Lemonade'),
            ]
            _sparkhire_count = 0
            for _sh_slug, _sh_name in _SPARKHIRE_COMPANIES:
                try:
                    _sh_req = _ur2.Request(f"https://candidate.sparkhire.com/users/{_sh_slug}/jobs", headers={"User-Agent": "Mozilla/5.0 JobHunter/1.0"})
                    with _ur2.urlopen(_sh_req, timeout=10) as _sh_resp:
                        _sh_html = _sh_resp.read().decode('utf-8', errors='replace')
                    import re as _re_sh
                    _sh_jobs = _re_sh.findall(r'<a[^>]+href="([^"]+)"[^>]*>\s*<[^>]+>([^<]{5,120})</[^>]+>\s*</a>', _sh_html)
                    for _sh_href, _sh_title in _sh_jobs[:15]:
                        _t = _sh_title.strip()
                        if _title_match(_t):
                            _u = _sh_href if _sh_href.startswith('http') else f"https://candidate.sparkhire.com{_sh_href}"
                            all_raw.append({"title": _t, "company": _sh_name, "location": "", "url": _u, "description": "", "source": "sparkhire"})
                            _sparkhire_count += 1
                except Exception:
                    pass
            print(f"[search] Sparkhire: {_sparkhire_count} jobs matched")

            # -- Workday tenants (public JSON search API) --
            _WORKDAY_TENANTS = [
                ("nvidia", "wd5", "nvidiaexternal", "NVIDIA"),
                ("ibm", "wd1", "IBM", "IBM"),
                ("ebay", "wd1", "ebay", "eBay"),
                ("paypal", "wd1", "paypal", "PayPal"),
                ("intel", "wd1", "External", "Intel"),
                ("hpe", "wd1", "Jobsatyou", "HPE"),
                ("vmware", "wd1", "VMware", "VMware"),
                ("accenture", "wd3", "AccentureCareers", "Accenture"),
                ("salesforce", "wd12", "External_Career_Site", "Salesforce"),
                ("dell", "wd1", "External", "Dell"),
            ]
            _workday_count = 0
            for _wd_t, _wd_s, _wd_st, _wd_n in _WORKDAY_TENANTS:
                try:
                    _wd_url = f"https://{_wd_t}.{_wd_s}.myworkdayjobs.com/wday/cxs/{_wd_t}/{_wd_st}/jobs"
                    _wd_body = _js2.dumps({"limit": 20, "offset": 0, "searchText": ""}).encode('utf-8')
                    _wd_req = _ur2.Request(_wd_url, data=_wd_body, headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "Mozilla/5.0 JobHunter/1.0"})
                    with _ur2.urlopen(_wd_req, timeout=12) as _wd_resp:
                        _wd_data = _js2.loads(_wd_resp.read().decode('utf-8', errors='replace'))
                    for _wd_j in (_wd_data.get("jobPostings") or [])[:20]:
                        _wd_tt = (_wd_j.get("title") or "").strip()
                        if _title_match(_wd_tt):
                            _wd_p = _wd_j.get("externalPath") or ""
                            _wd_full = f"https://{_wd_t}.{_wd_s}.myworkdayjobs.com{_wd_p}" if _wd_p else ""
                            all_raw.append({"title": _wd_tt, "company": _wd_n, "location": _wd_j.get("locationsText","") or "", "url": _wd_full, "description": "", "source": "workday"})
                            _workday_count += 1
                except Exception:
                    pass
            print(f"[search] Workday: {_workday_count} jobs matched")

            # -- LinkedIn public guest endpoint --
            _linkedin_count = 0
            import urllib.parse as _urp2
            for _li_kw in titles_[:5]:
                try:
                    _li_q = _urp2.quote(_li_kw)
                    _li_l = _urp2.quote("Israel")
                    _li_url = f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords={_li_q}&location={_li_l}&start=0"
                    _li_req = _ur2.Request(_li_url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
                    with _ur2.urlopen(_li_req, timeout=12) as _li_resp:
                        _li_html = _li_resp.read().decode('utf-8', errors='replace')
                    import re as _re_li
                    _li_cards = _re_li.findall(r'<a[^>]+class="base-card__full-link[^"]*"[^>]+href="([^"?]+)', _li_html)
                    _li_titles = _re_li.findall(r'<h3[^>]+class="base-search-card__title"[^>]*>\s*([^<]+?)\s*</h3>', _li_html)
                    _li_comps = _re_li.findall(r'<h4[^>]+class="base-search-card__subtitle"[^>]*>\s*<a[^>]*>\s*([^<]+?)\s*</a>', _li_html)
                    _li_locs = _re_li.findall(r'<span[^>]+class="job-search-card__location"[^>]*>\s*([^<]+?)\s*</span>', _li_html)
                    for _li_i in range(min(20, len(_li_cards), len(_li_titles))):
                        _li_t = _li_titles[_li_i].strip()
                        if _title_match(_li_t):
                            _li_c = _li_comps[_li_i].strip() if _li_i < len(_li_comps) else ""
                            _li_lc = _li_locs[_li_i].strip() if _li_i < len(_li_locs) else ""
                            all_raw.append({"title": _li_t, "company": _li_c, "location": _li_lc, "url": _li_cards[_li_i], "description": "", "source": "linkedin"})
                            _linkedin_count += 1
                except Exception:
                    pass
            print(f"[search] LinkedIn guest: {_linkedin_count} jobs matched")

            # -- Indeed (best-effort) --
            _indeed_count = 0
            for _in_kw in titles_[:3]:
                try:
                    _in_q = _urp2.quote(_in_kw)
                    _in_url = f"https://il.indeed.com/jobs?q={_in_q}&l=Israel&fromage=7&sort=date"
                    _in_req = _ur2.Request(_in_url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"})
                    with _ur2.urlopen(_in_req, timeout=12) as _in_resp:
                        _in_html = _in_resp.read().decode('utf-8', errors='replace')
                    import re as _re_in
                    _in_items = _re_in.findall(r'data-jk="([a-z0-9]+)"[^>]*>[\s\S]{0,2000}?title="([^"]{5,200})"', _in_html)
                    for _in_jk, _in_tt in _in_items[:15]:
                        _in_t = _in_tt.strip()
                        if _title_match(_in_t):
                            all_raw.append({"title": _in_t, "company": "", "location": "", "url": f"https://il.indeed.com/viewjob?jk={_in_jk}", "description": "", "source": "indeed"})
                            _indeed_count += 1
                except Exception:
                    pass
            print(f"[search] Indeed: {_indeed_count} jobs matched")

            # -- Second-level dedup: normalized company+title fingerprint --
            def _norm_fp(_r):
                import re as _re_fp
                _tt = (_r.get("title") or "").lower()
                _cc = (_r.get("company") or "").lower()
                _tt = _re_fp.sub(r'[\(\[].*?[\)\]]', '', _tt)
                _tt = _re_fp.sub(r'[^a-z0-9 ]', ' ', _tt)
                _tt = _re_fp.sub(r'\s+', ' ', _tt).strip()
                _cc = _re_fp.sub(r'[^a-z0-9 ]', ' ', _cc)
                _cc = _re_fp.sub(r'\s+', ' ', _cc).strip()
                return f"{_cc}|{_tt}"
            _seen_fps = set()
            _deduped = []
            for _r in all_raw:
                _fp = _norm_fp(_r)
                if _fp and _fp != "|" and _fp not in _seen_fps:
                    _seen_fps.add(_fp)
                    _deduped.append(_r)
            print(f"[search] Dedup: {len(all_raw)} -> {len(_deduped)} after normalized fingerprint")
            all_raw = _deduped
            print(f"[search] Pre-filter: {len(all_raw)} title-matched jobs from {len(_GH_COMPANIES)+len(_LV_COMPANIES)} companies")

            if not all_raw:
                return []

            # -- Score against user CV + preferences --
            # Cascading fallback: Gemini Flash (free) -> Anthropic Haiku -> rule-based heuristic

            # Fetch CV summary from DB for richer scoring context
            _cv_text = ""
            try:
                _conn2 = database.get_db()
                _prof2 = _conn2.execute(
                    "SELECT cv_summary FROM user_profiles WHERE user_id=?",
                    (user_id,)
                ).fetchone()
                _conn2.close()
                _cv_text = (_prof2["cv_summary"] or "") if _prof2 else ""
            except Exception:
                _cv_text = ""

            profile_text = (
                f"Target roles: {', '.join(titles_[:4])}\n"
                f"Key skills: {', '.join(kws_[:10])}\n"
                f"Locations: {', '.join(locs_)} or Remote\n"
                f"Seniority: Senior / Director / VP / Head-of"
            )
            if _cv_text:
                profile_text += f"\n\nCV Summary (first 1500 chars):\n{_cv_text[:1500]}"

            def _parse_scored_response(text_):
                """Extract a valid JSON array of scored jobs from an AI response string."""
                t = text_.strip()
                if "```" in t:
                    t = t.split("```")[1]
                    if t.startswith("json"): t = t[4:]
                si = t.rfind("["); ei = t.rfind("]")
                if si < 0 or ei <= si:
                    return []
                parsed = _js2.loads(t[si:ei+1])
                out = []
                for j in parsed:
                    if isinstance(j, dict) and j.get("url") and j.get("candidate_score", 0) >= 40:
                        j.setdefault("match_score", j.get("candidate_score", 0))
                        j.setdefault("found_date", today)
                        j.setdefault("source", "greenhouse/lever")
                        out.append(j)
                return out

            def _score_batch(batch_, profile_text_):
                """Score a batch of jobs via Gemini -> Anthropic -> heuristic fallback."""
                import os as _os_sb
                _GEMINI_KEY = _os_sb.environ.get('GEMINI_API_KEY', '')

                jobs_json_ = _js2.dumps(
                    [{"job_title": j.get("job_title",""), "company": j.get("company",""),
                      "location": j.get("location",""), "url": j.get("url",""),
                      "description": (j.get("description") or ""),
                      "full_description": (j.get("full_description") or j.get("description") or "")[:2000]}
                     for j in batch_], ensure_ascii=False)

                prompt_ = (
                    "You are a job matching assistant. Review these job listings and score each "
                    f"for this candidate:\n\n{profile_text_}\n\n"
                    f"Job listings (JSON):\n{jobs_json_}\n\n"
                    "Return ONLY a JSON array with fields: job_title, company, location, url, "
                    "publish_date (ISO date string or null if unknown), "
                    "full_description (preserve the full_description from input if provided), "
                    "description (2-3 sentences), candidate_score (0-100), fit_reason (1-2 sentences). "
                    "Only include jobs with candidate_score >= 40. Be strict: only include jobs that "
                    "closely match the candidate's target role titles and experience level. "
                    "Return ONLY valid JSON, no markdown."
                )

                # --- Try Gemini Flash first (free tier: 1500 req/day) ---
                if _GEMINI_KEY:
                    try:
                        _g_body = _js2.dumps({
                            'contents': [{'parts': [{'text': prompt_}]}],
                            'generationConfig': {'temperature': 0.2, 'maxOutputTokens': 4096}
                        }).encode('utf-8')
                        _g_url = ('https://generativelanguage.googleapis.com/v1beta/models/'
                                  'gemini-2.0-flash:generateContent?key=' + _GEMINI_KEY)
                        _g_req = _ur2.Request(_g_url, data=_g_body,
                                              headers={'Content-Type': 'application/json'}, method='POST')
                        with _ur2.urlopen(_g_req, timeout=60) as _g_resp:
                            _g_data = _js2.loads(_g_resp.read().decode('utf-8'))
                        _g_text = _g_data['candidates'][0]['content']['parts'][0]['text']
                        result_ = _parse_scored_response(_g_text)
                        print(f"[search] Gemini scored {len(batch_)} -> {len(result_)} passed")
                        return result_
                    except Exception as _ge:
                        print(f"[search] Gemini scoring error: {_ge} -- trying Anthropic")

                # --- Try Anthropic Claude Haiku (secondary) ---
                if ANTHROPIC_KEY:
                    try:
                        _a_body = _js2.dumps({"model": "claude-haiku-4-5-20251001", "max_tokens": 4096,
                            "messages": [{"role": "user", "content": prompt_}]}).encode()
                        _a_req = _ur2.Request("https://api.anthropic.com/v1/messages", data=_a_body, method="POST",
                            headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01",
                                     "content-type": "application/json"})
                        with _ur2.urlopen(_a_req, timeout=60) as _a_resp:
                            _a_result = _js2.loads(_a_resp.read())
                        _a_text = ""
                        for blk in _a_result.get("content", []):
                            if blk.get("type") == "text": _a_text += blk["text"]
                        result_ = _parse_scored_response(_a_text)
                        print(f"[search] Anthropic scored {len(batch_)} -> {len(result_)} passed")
                        return result_
                    except Exception as _ae:
                        print(f"[search] Anthropic scoring error: {_ae} -- falling back to heuristic")

                # --- Rule-based heuristic (no API needed) ---
                print(f"[search] Heuristic scoring {len(batch_)} jobs (no AI key available)")
                out_ = []
                for j in batch_:
                    _t = (j.get("job_title") or "").lower()
                    _desc = (j.get("full_description") or j.get("description") or "").lower()
                    _loc = (j.get("location") or "").lower()
                    _score = 0
                    if any(ph in _t for ph in _phrases):
                        _score += 50
                    else:
                        for _w1, _w2 in _bigrams:
                            if _w1 in _t and _w2 in _t:
                                _score += 35
                                break
                    for _kw in kws_:
                        if _kw.lower() in _desc:
                            _score += 5
                        if _score >= 70:
                            break
                    for _lp in locs_:
                        if _lp.lower() in _loc or "remote" in _loc or "israel" in _loc:
                            _score += 10
                            break
                    if _score >= 40:
                        _jc = dict(j)
                        _jc["candidate_score"] = min(_score, 95)
                        _jc["match_score"] = _jc["candidate_score"]
                        _jc.setdefault("found_date", today)
                        _jc.setdefault("source", j.get("source", "greenhouse/lever"))
                        _jc.setdefault("fit_reason", "Matched by title and skills")
                        _jc.setdefault("description", j.get("description") or j.get("job_title", ""))
                        _jc.setdefault("publish_date", None)
                        out_.append(_jc)
                print(f"[search] Heuristic: {len(batch_)} -> {len(out_)} passed")
                return out_

            # Score all collected jobs in batches of 25
            scored_jobs = []
            for batch_i in range(0, len(all_raw), 25):
                batch = all_raw[batch_i:batch_i+25]
                scored_jobs.extend(_score_batch(batch, profile_text))

            # -- Supplemental: Gemini + Google Search for LinkedIn/Glassdoor/Wellfound --
            import os as _os_ws
            _GEMINI_KEY_WS = _os_ws.environ.get('GEMINI_API_KEY', '')
            if _GEMINI_KEY_WS:
                try:
                    for _ws_title in titles_[:3]:
                        try:
                            _ws_prompt = (
                                f'Find 5 current "{_ws_title}" job listings in '
                                f'{", ".join(locs_[:2]) or "Israel"}. '
                                'Focus on linkedin.com/jobs, glassdoor.com, and wellfound.com. '
                                'Return ONLY a JSON array. '
                                'Each item: {"job_title":"...","company":"...","location":"...","url":"...","description":"..."}'
                            )
                            _ws_body = _js2.dumps({
                                'contents': [{'parts': [{'text': _ws_prompt}]}],
                                'tools': [{'google_search': {}}],
                                'generationConfig': {'temperature': 0.1, 'maxOutputTokens': 2048}
                            }).encode('utf-8')
                            _ws_url = ('https://generativelanguage.googleapis.com/v1beta/models/'
                                       'gemini-2.0-flash:generateContent?key=' + _GEMINI_KEY_WS)
                            _ws_req = _ur2.Request(_ws_url, data=_ws_body,
                                                   headers={'Content-Type': 'application/json'}, method='POST')
                            with _ur2.urlopen(_ws_req, timeout=60) as _ws_resp:
                                _ws_data = _js2.loads(_ws_resp.read().decode('utf-8'))
                            _ws_text = _ws_data['candidates'][0]['content']['parts'][0]['text'].strip()
                            _ws_si = _ws_text.rfind('['); _ws_ei = _ws_text.rfind(']')
                            if _ws_si >= 0 and _ws_ei > _ws_si:
                                for _ws_j in _js2.loads(_ws_text[_ws_si:_ws_ei+1]):
                                    if isinstance(_ws_j, dict) and _ws_j.get('url'):
                                        _ws_j.setdefault('candidate_score', 70)
                                        _ws_j.setdefault('match_score', 70)
                                        _ws_j.setdefault('fit_reason', 'Found via Google Search')
                                        _ws_j.setdefault('source', 'web_search')
                                        _ws_j.setdefault('found_date', today)
                                        scored_jobs.append(_ws_j)
                            print(f"[search] Gemini web search for '{_ws_title}': added jobs")
                        except Exception as _wse:
                            print(f"[search] Gemini web search error for '{_ws_title}': {_wse}")
                except Exception as _wse2:
                    print(f"[search] Gemini web search supplemental skipped: {_wse2}")
            else:
                print("[search] Gemini web search skipped -- no GEMINI_API_KEY")

            print(f"[search] Final: {len(scored_jobs)} scored jobs (from {len(all_raw)} pre-filtered)")
            return scored_jobs

        all_jobs_data = []
        seen_urls     = set(existing_urls)
        seen_key      = set()

        # Search real job sites via Claude web_search (LinkedIn, AllJobs, Drushim, etc.)
        jobs_data = _search_jobs_with_claude_websearch(titles, locations, keywords)
        print(f"[run-search] Found {len(jobs_data)} jobs via Claude web_search")

        for j in jobs_data:
            jurl = (j.get("url") or "").strip()
            jkey = (j.get("job_title","").lower().strip(), j.get("company","").lower().strip())
            if jurl and jurl in seen_urls: continue
            if not jurl and jkey in seen_key: continue
            if jurl: seen_urls.add(jurl)
            if jkey: seen_key.add(jkey)
            all_jobs_data.append(j)

        if not all_jobs_data:
            database.log_activity(user_id, "jobs_searched", "Search returned no new results")
            try:
                deliver_notification(user_id, f"🔍 Search Complete — {today}\n\nNo new jobs found this run.", url_suffix="/dashboard#new")
            except Exception as _dn_err:
                print(f"[run-search] deliver_notification error: {_dn_err}")
            return

        # ── URL check for new jobs ───────────────────────────────────────
        import apply_engine as _ae
        from concurrent.futures import ThreadPoolExecutor as _TPE
        _new_urls = {j.get("url","").strip() for j in all_jobs_data if j.get("url")}
        _url_ok   = {}
        with _TPE(max_workers=8) as _ex:
            _futs = {_ex.submit(_ae.check_url_alive, u): u for u in _new_urls}
            for _f, _u in _futs.items():
                try:    _url_ok[_u] = 1 if _f.result(timeout=12) else 0
                except: _url_ok[_u] = 0
        _chk_date = datetime.now().isoformat()

        # ── Load rejected patterns to filter out ────────────────────────
        conn = database.get_db()
        _rej_patterns = conn.execute(
            "SELECT LOWER(TRIM(company)) as c, LOWER(TRIM(title)) as t FROM rejected_patterns WHERE user_id=?",
            (user_id,)
        ).fetchall()
        _rej_set = {(r["c"], r["t"]) for r in _rej_patterns}
        _rej_companies = {r["c"] for r in _rej_patterns if r["c"]}
        conn.close()

        # ── Insert new jobs (skip rejected patterns) ─────────────────────
        conn = database.get_db(); inserted = 0; new_jobs_info = []; skipped_rej = 0
        for j in all_jobs_data:
            _jc = j.get("company","").strip().lower()
            _jt = j.get("job_title","").strip().lower()
            if (_jc, _jt) in _rej_set:
                skipped_rej += 1
                continue
            if j.get("match_score", 0) <= 0:
                continue
            try:
                _jurl = (j.get("url") or "").strip()
                if _jurl and _url_ok.get(_jurl) == 0:
                    continue  # skip dead links
                # Dedup: skip if same company+title already exists for this user
                _jtitle = j.get("job_title", "").strip().lower()
                _jcomp = j.get("company", "").strip().lower()
                if _jtitle and _jcomp:
                    dup = conn.execute(
                        "SELECT id FROM jobs WHERE user_id=? AND LOWER(TRIM(title))=? AND LOWER(TRIM(company))=?",
                        (user_id, _jtitle, _jcomp)
                    ).fetchone()
                    if dup:
                        continue  # duplicate job from different source
                conn.execute(
                    "INSERT OR IGNORE INTO jobs "
                    "(user_id,title,company,location,url,description,why_relevant,source,"
                    "found_date,match_score,candidate_score,status,url_verified,url_check_date,publish_date,full_description) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,'new',?,?,?,?)",
                    (user_id, j.get("job_title",""), j.get("company",""), j.get("location",""),
                     _jurl, j.get("description",""), j.get("fit_reason",""), j.get("source",""),
                     j.get("found_date",today), j.get("match_score",0), j.get("candidate_score",0),
                     _url_ok.get(_jurl) if _jurl else None, _chk_date if _jurl else None,
                     j.get("publish_date"), (j.get("full_description") or "")[:5000]))
                inserted += 1
                new_jobs_info.append({"title":j.get("job_title",""),"company":j.get("company",""),"url_ok":_url_ok.get(_jurl) if _jurl else None})
            except Exception as e: print(f"[run-search] insert error: {e}")
        conn.commit(); conn.close()

        # ── URL check ALL historical unverified jobs ─────────────────────
        conn = database.get_db()
        unverified = conn.execute(
            "SELECT id, url FROM jobs WHERE user_id=? AND url_verified IS NULL AND url!=''", (user_id,)
        ).fetchall()
        conn.close()
        hist_alive = hist_dead = 0
        if unverified:
            with _TPE(max_workers=8) as _ex2:
                _futs2 = {_ex2.submit(_ae.check_url_alive, r["url"]): r for r in unverified}
                hist_results = {}
                for _f2, _r2 in _futs2.items():
                    try:    hist_results[_r2["id"]] = 1 if _f2.result(timeout=12) else 0
                    except: hist_results[_r2["id"]] = 0
            conn = database.get_db()
            for job_id, ok in hist_results.items():
                conn.execute("UPDATE jobs SET url_verified=?, url_check_date=? WHERE id=?",(ok,_chk_date,job_id))
                if ok: hist_alive += 1
                else:  hist_dead  += 1
            conn.commit(); conn.close()

        database.log_activity(user_id, "jobs_searched", f"Found {inserted} new job(s) across {len(titles)} title search(es) ({skipped_rej} rejected-pattern matches skipped)")

        # ── Consolidated search notification ─────────────────────────────
        notif_lines = [f"🔍 Search Complete — {today}"]
        if inserted > 0:
            notif_lines.append(f"\n📋 {inserted} new job(s) added for review:")
            for info in new_jobs_info[:10]:
                icon = "🔗" if info["url_ok"]==1 else ("⚠️" if info["url_ok"]==0 else "")
                notif_lines.append(f"  • {info['title']} @ {info['company']} {icon}".rstrip())
            if len(new_jobs_info)>10: notif_lines.append(f"  … and {len(new_jobs_info)-10} more")
        else:
            notif_lines.append("\nNo new jobs inserted (all already in history).")
        if (hist_alive+hist_dead)>0:
            notif_lines.append(f"\n🔄 Re-checked {hist_alive+hist_dead} existing job URL(s):")
            notif_lines.append(f"  ✅ {hist_alive} alive  ❌ {hist_dead} dead")
        try:
            deliver_notification(user_id, "\n".join(notif_lines), url_suffix="/dashboard#new")
        except Exception as _dn_err:
            print(f"[run-search] deliver_notification error: {_dn_err}")
        print(f"[run-search] user {user_id}: inserted={inserted} hist_checked={hist_alive+hist_dead}")

    except Exception as e:
        import traceback as _tb
        _tb_str = _tb.format_exc()
        print(f"[run-search] Error: {type(e).__name__}: {e}\n{_tb_str}")
        err_summary = f"{type(e).__name__}: {str(e)[:120]}"
        database.log_activity(user_id, "jobs_searched", f"Job search failed — {err_summary}")
        try:
            deliver_notification(user_id, f"\u274c Job search failed\n\n{err_summary}\n\nWill retry at next scheduled time.", url_suffix="/dashboard")
        except Exception as _ne:
            print(f"[run-search] Notification also failed: {_ne}")
    finally:
        _search_running.discard(user_id)

def run_job_apply(user_id: int) -> int:
    """Submit applications to all approved jobs using browser automation + Claude."""
    # ── Rate-limit + auto_apply gate ──────────────────────────────────────────
    from datetime import timezone as _tz
    today_utc = datetime.now(_tz.utc).strftime("%Y-%m-%d")
    _rc = database.get_db()
    _prof = _rc.execute(
        "SELECT auto_apply_enabled, applications_sent_today, applications_reset_date "
        "FROM user_profiles WHERE user_id=?", (user_id,)
    ).fetchone()
    _urow = _rc.execute("SELECT email FROM users WHERE id=?", (user_id,)).fetchone()
    _is_admin = (_urow and _urow["email"] and _urow["email"].lower() == (ADMIN_EMAIL or "").lower())

    # SILENT MODE: auto-apply off => return immediately, no notification
    if not _prof or not _prof["auto_apply_enabled"]:
        _rc.close()
        return {"applied": 0, "error": "", "skipped": "auto_apply_disabled"}

    # Daily reset at 00:00 UTC
    if _prof["applications_reset_date"] != today_utc:
        _rc.execute(
            "UPDATE user_profiles SET applications_sent_today=0, applications_reset_date=? "
            "WHERE user_id=?", (today_utc, user_id))
        _rc.commit()
        _sent_today = 0
    else:
        _sent_today = _prof["applications_sent_today"] or 0

    DAILY_CAP = 3
    _remaining = None if _is_admin else max(0, DAILY_CAP - _sent_today)
    _rc.close()

    if _remaining is not None and _remaining <= 0:
        return {"applied": 0, "error": "", "skipped": "daily_limit_reached"}

    try:
        import apply_engine
        conn = database.get_db()
        jobs = conn.execute(
            "SELECT id, title, company, url FROM jobs WHERE user_id=? AND status='approved'",
            (user_id,)
        ).fetchall()

        # Cap jobs to daily remaining quota (non-admin only)
        if _remaining is not None:
            jobs = jobs[:_remaining]

        if not jobs:
            conn.close()
            return {"applied": 0, "error": ""}

        # Gather user + CV data for form filling
        user    = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        profile = conn.execute(
            "SELECT cv_summary, cv_path FROM user_profiles WHERE user_id=?", (user_id,)
        ).fetchone()
        conn.close()

        cv_text   = (profile["cv_summary"] or "") if profile else ""
        email     = user["email"] if user else ""
        applicant = apply_engine.extract_applicant_data(cv_text, email)

        cv_path = None
        if profile and profile["cv_path"]:
            cv_path = profile["cv_path"]

        today = datetime.now().strftime("%Y-%m-%d")
        count = 0
        confirmed_list, submitted_list, manual_list, failed_list = [], [], [], []

        for j in jobs:
            job_url = j["url"] or ""
            if job_url:
                res = apply_engine.submit_application(
                    job_url, j["title"], j["company"],
                    applicant, cv_path, ANTHROPIC_KEY
                )
                apply_status       = res["status"]
                apply_confirmation = res.get("confirmation_text", "")[:1000]
                apply_error        = res.get("error", "")[:500]
                apply_failure_type   = res.get("apply_failure_type")
                apply_failure_detail = (res.get("apply_failure_detail") or "")[:300]
                notes = f"Applied via Job Hunter — {apply_status}"
            else:
                apply_status       = "submitted"
                apply_confirmation = ""
                apply_error        = "No URL available"
                notes = "Applied via Job Hunter (no URL)"

            c2 = database.get_db()
            if apply_status in ("submitted", "confirmed"):
                c2.execute(
                    "UPDATE jobs SET status='applied', applied_date=?, notes=?, "
                    "apply_status=?, apply_confirmation=?, apply_error=?, "
                    "apply_failure_type=?, apply_failure_detail=?, "
                    "apply_attempts=COALESCE(apply_attempts,0)+1 "
                    "WHERE id=? AND user_id=?",
                    (today, notes, apply_status, apply_confirmation,
                     apply_error, apply_failure_type, apply_failure_detail, j["id"], user_id)
                )
            else:
                # Failed — keep status='approved' so user can retry
                c2.execute(
                    "UPDATE jobs SET notes=?, "
                    "apply_status=?, apply_error=?, "
                    "apply_failure_type=?, apply_failure_detail=?, "
                    "apply_attempts=COALESCE(apply_attempts,0)+1 "
                    "WHERE id=? AND user_id=?",
                    (notes, apply_status, apply_error, apply_failure_type, apply_failure_detail, j["id"], user_id)
                )
            c2.commit()
            c2.close()

            database.log_activity(
                user_id, "job_applied",
                f"{j['title']} @ {j['company']} — {apply_status}"
            )
            count += 1
            # Increment daily application counter
            try:
                _uc = database.get_db()
                _uc.execute("UPDATE user_profiles SET applications_sent_today = applications_sent_today + 1 WHERE user_id=?", (user_id,))
                _uc.commit()
                _uc.close()
            except Exception:
                pass
            if apply_status == "confirmed":
                confirmed_list.append(j)
            elif apply_status == "submitted":
                submitted_list.append(j)
            elif apply_status == "manual_required":
                manual_list.append(j)
            else:
                failed_list.append(j)

        # ── Notifications ────────────────────────────────────────────────────────────────────────────────
        # ── Single consolidated apply notification ──────────────────────────────
        today_str = datetime.now().strftime("%Y-%m-%d")
        notif_lines = [f"🚀 Apply Run Complete — {today_str}", f"📊 {count} application(s) submitted\n"]
        if confirmed_list:
            notif_lines.append(f"✅ {len(confirmed_list)} Confirmed:")
            for j in confirmed_list[:5]:
                notif_lines.append(f"  • {j['title']} @ {j['company']}")
            if len(confirmed_list) > 5:
                notif_lines.append(f"  … +{len(confirmed_list)-5} more")
        if submitted_list:
            notif_lines.append(f"\n📤 {len(submitted_list)} Submitted (awaiting confirmation):")
            for j in submitted_list[:5]:
                notif_lines.append(f"  • {j['title']} @ {j['company']}")
            if len(submitted_list) > 5:
                notif_lines.append(f"  … +{len(submitted_list)-5} more")
        if manual_list:
            notif_lines.append(f"\n👤 {len(manual_list)} Need Manual Apply:")
            for j in manual_list[:5]:
                notif_lines.append(f"  • {j['title']} @ {j['company']}")
            if len(manual_list) > 5:
                notif_lines.append(f"  … +{len(manual_list)-5} more")
        if failed_list:
            notif_lines.append(f"\n❌ {len(failed_list)} Failed:")
            for j in failed_list[:5]:
                notif_lines.append(f"  • {j['title']} @ {j['company']}")
            if len(failed_list) > 5:
                notif_lines.append(f"  … +{len(failed_list)-5} more")
        deliver_notification(user_id, "\n".join(notif_lines), url_suffix="/dashboard#applied")
        print(f"[run-apply] user {user_id}: {count} — confirmed={len(confirmed_list)} submitted={len(submitted_list)} manual={len(manual_list)} failed={len(failed_list)}")
        return {"applied": count, "error": ""}

    except Exception as e:
        print(f"[run-apply] Error: {e}")
        import traceback; traceback.print_exc()
        return {"applied": 0, "error": str(e)}


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
  <link rel="stylesheet" href="/static/tw.css"/>
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
    .tag { display:inline-flex;align-items:center;gap:.4rem;background:#eff6ff;
           color:#1d4ed8;border:1px solid #bfdbfe;border-radius:9999px;
           padding:.25rem .5rem .25rem .75rem;font-size:.8rem;font-weight:600; }
    .tag button { background:#dbeafe;border:none;cursor:pointer;color:#3b82f6;font-size:.85rem;
                  line-height:1;padding:2px 5px;border-radius:9999px;transition:all .15s; }
    .tag button:hover { background:#fecaca;color:#dc2626; }
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
  const existing = Array.from(wrap.querySelectorAll('.tag [contenteditable]')).map(s => s.textContent.trim().toLowerCase());
  if (existing.includes(v.toLowerCase())) { input.value=''; return; }
  const tag = document.createElement('span');
  tag.className = 'tag';
  const label = document.createElement('span');
  label.contentEditable = 'true';
  label.textContent = v;
  label.title = 'Tap to edit';
  label.style.cssText = 'outline:none;cursor:text;min-width:1ch;white-space:nowrap;-webkit-user-select:text;user-select:text;';
  const stopAndFocus = e => { e.stopPropagation(); e.preventDefault(); label.focus(); };
  label.addEventListener('click', e => e.stopPropagation());
  label.addEventListener('touchstart', e => e.stopPropagation(), {passive:false});
  label.addEventListener('touchend', stopAndFocus, {passive:false});
  label.addEventListener('keydown', e => {
    e.stopPropagation();
    if (e.key === 'Enter') { e.preventDefault(); label.blur(); }
    if (e.key === 'Escape') { label.textContent = v; label.blur(); }
  });
  label.addEventListener('blur', () => {
    const nv = label.textContent.trim();
    if (!nv) tag.remove(); else label.textContent = nv;
  });
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.textContent = '×';
  btn.addEventListener('click', e => { e.stopPropagation(); tag.remove(); });
  btn.addEventListener('touchend', e => { e.stopPropagation(); e.preventDefault(); tag.remove(); }, {passive:false});
  tag.appendChild(label);
  tag.appendChild(btn);
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
  return Array.from(document.getElementById(wrapId).querySelectorAll('.tag [contenteditable]')).map(s => s.textContent.trim()).filter(Boolean);
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
  if (file.size > 10 * 1024 * 1024) {
    showUploadStatus('❌ File too large (max 10 MB).', 'error'); return;
  }
  document.getElementById('drop-icon').textContent = '⏳';
  document.getElementById('drop-text').textContent = `Uploading ${file.name}…`;
  showUploadStatus('Uploading…', 'info');

  const reader = new FileReader();
  reader.onload = function() {
    const base64 = reader.result.split(',')[1];
    const payload = JSON.stringify({filename: file.name, data: base64});
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/upload-cv', true);
    xhr.setRequestHeader('Content-Type', 'application/json');
    xhr.timeout = 30000;
    xhr.onload = function() {
      try {
        const d = JSON.parse(xhr.responseText);
        if (d.success) {
          cvUploaded = true;
          document.getElementById('drop-icon').textContent = '✅';
          document.getElementById('drop-text').textContent = file.name + ' ready';
          showUploadStatus('CV uploaded! Click below to analyze it.', 'success');
          document.getElementById('analyze-btn').classList.remove('hidden');
          document.getElementById('skip-cv-btn').textContent = 'Skip AI analysis →';
        } else {
          showUploadStatus('❌ ' + (d.error || 'Upload failed.'), 'error');
          document.getElementById('drop-icon').textContent = '📄';
          document.getElementById('drop-text').textContent = 'Drag & drop your CV here';
        }
      } catch(e) {
        showUploadStatus('❌ Server returned invalid response.', 'error');
        document.getElementById('drop-icon').textContent = '📄';
        document.getElementById('drop-text').textContent = 'Drag & drop your CV here';
      }
    };
    xhr.onerror = function() {
      showUploadStatus('❌ Network error. Check your connection.', 'error');
      document.getElementById('drop-icon').textContent = '📄';
      document.getElementById('drop-text').textContent = 'Drag & drop your CV here';
    };
    xhr.ontimeout = function() {
      showUploadStatus('❌ Upload timed out. Try a smaller file.', 'error');
      document.getElementById('drop-icon').textContent = '📄';
      document.getElementById('drop-text').textContent = 'Drag & drop your CV here';
    };
    xhr.send(payload);
  };
  reader.onerror = function() {
    showUploadStatus('❌ Could not read file.', 'error');
    document.getElementById('drop-icon').textContent = '📄';
    document.getElementById('drop-text').textContent = 'Drag & drop your CV here';
  };
  reader.readAsDataURL(file);
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
    .tag { display:inline-flex;align-items:center;gap:.4rem;background:#eff6ff;
           color:#1d4ed8;border:1px solid #bfdbfe;border-radius:9999px;
           padding:.25rem .5rem .25rem .75rem;font-size:.8rem;font-weight:600; }
    .tag button { background:#dbeafe;border:none;cursor:pointer;color:#3b82f6;font-size:.85rem;
                  line-height:1;padding:2px 5px;border-radius:9999px;transition:all .15s; }
    .tag button:hover { background:#fecaca;color:#dc2626; }
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
  <div class="max-w-4xl mx-auto px-5 py-3 flex items-center justify-between gap-3">
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

<div class="max-w-4xl mx-auto px-5 py-8">
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
      <button id="cv-analyze-btn" onclick="analyzeCvWithAI()" class="hidden btn btn-secondary mt-3 text-sm">✨ Analyze your CV with AI →</button>
      <p class="text-xs text-slate-400 mt-2">Get a free Gemini AI review of your CV — includes a score out of 100, key strengths, and specific improvement suggestions.</p>
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
    </div>
    <button onclick="savePreferences()" class="btn btn-primary mt-6">Save preferences</button>
  </div>

  <!-- Notifications panel -->
  <div class="panel bg-white rounded-2xl p-6 shadow-sm border border-slate-100" id="panel-notifications">
    <h3 class="font-bold text-slate-900 mb-4">Notification Channel</h3>
    <div class="space-y-3 mb-5">
      <label class="flex items-center gap-4 p-4 border-2 border-slate-200 rounded-xl cursor-pointer
                    hover:border-blue-400 transition-colors has-[:checked]:border-blue-500 has-[:checked]:bg-blue-50">
        <input type="checkbox" name="s-notif" value="telegram" onchange="toggleNotifSection(this)" class="accent-blue-600 w-4 h-4"/>
        <span class="font-semibold">Telegram</span>
        <span class="ml-auto text-xl">✈️</span>
      </label>
      <label class="flex items-center gap-4 p-4 border-2 border-slate-200 rounded-xl cursor-pointer
                    hover:border-green-400 transition-colors has-[:checked]:border-green-500 has-[:checked]:bg-green-50">
        <input type="checkbox" name="s-notif" value="whatsapp" onchange="toggleNotifSection(this)" class="accent-green-600 w-4 h-4"/>
        <span class="font-semibold">WhatsApp</span>
        <span class="ml-auto text-xl">💬</span>
      </label>
      <label class="flex items-center gap-4 p-4 border-2 border-slate-200 rounded-xl cursor-pointer
                    hover:border-orange-400 transition-colors has-[:checked]:border-orange-500 has-[:checked]:bg-orange-50">
        <input type="checkbox" name="s-notif" value="email" onchange="toggleNotifSection(this)" class="accent-orange-600 w-4 h-4"/>
        <span class="font-semibold">Email</span>
        <span class="ml-auto text-xl">✉️</span>
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

    <div id="sn-email" class="hidden space-y-4 border-t pt-5">
      <div><label class="label">Email address</label><input class="input" type="email" id="sn-email-addr" placeholder="you@example.com"/></div>
      <p class="text-xs text-slate-400">Notifications will be sent via Resend.com to this address</p>
      <button onclick="testNotification('email')" class="btn btn-secondary text-sm">🧪 Test</button>
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

    <!-- Auto-apply toggle -->
    <div class="mt-6 pt-5 border-t border-slate-100">
      <label class="flex items-center justify-between cursor-pointer">
        <div>
          <span class="text-sm font-semibold text-slate-700">Enable Automatic Applications</span>
          <p class="text-xs text-slate-400 mt-0.5">When off, Job Hunter will never auto-submit applications for you.</p>
        </div>
        <div class="relative">
          <input type="checkbox" id="s-auto-apply" class="sr-only peer" />
          <div class="w-11 h-6 bg-slate-200 peer-focus:ring-2 peer-focus:ring-blue-300 rounded-full peer peer-checked:bg-blue-600 transition-colors"></div>
          <div class="absolute left-[2px] top-[2px] w-5 h-5 bg-white rounded-full shadow peer-checked:translate-x-5 transition-transform"></div>
        </div>
      </label>
      <p class="text-xs text-amber-600 mt-2 hidden" id="auto-apply-warning">\u26A0\uFE0F Standard accounts are limited to 3 automatic applications per day.</p>
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
  const channels = (userData.notification_channel || 'none').split(',');
  channels.forEach(ch => {
    const cb = document.querySelector('input[name="s-notif"][value="'+ch.trim()+'"]');
    if (cb) cb.checked = true;
  });
  channels.forEach(ch => {
    const sec = document.getElementById('sn-'+ch.trim());
    if (sec) sec.classList.remove('hidden');
  });
  updateNotifStyles();
  if (userData.telegram_token)    document.getElementById('sn-tg-token').value   = userData.telegram_token;
  if (userData.telegram_chat_id)  document.getElementById('sn-tg-chat-id').value = userData.telegram_chat_id;
  if (userData.twilio_account_sid) document.getElementById('sn-wa-sid').value    = userData.twilio_account_sid;
  if (userData.twilio_auth_token)  document.getElementById('sn-wa-token').value  = userData.twilio_auth_token;
  if (userData.whatsapp_number)    document.getElementById('sn-wa-number').value = userData.whatsapp_number;
  if (userData.email_address) document.getElementById('sn-email-addr').value = userData.email_address;

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
          if (userData.auto_apply_enabled) document.getElementById('s-auto-apply').checked = true;
          if (!isAdmin) document.getElementById('auto-apply-warning').classList.remove('hidden');
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

function toggleNotifSection(cb) {
  document.getElementById('sn-telegram').classList.toggle('hidden', !document.querySelector('input[name="s-notif"][value="telegram"]').checked);
  document.getElementById('sn-whatsapp').classList.toggle('hidden', !document.querySelector('input[name="s-notif"][value="whatsapp"]').checked);
  const emailEl = document.getElementById('sn-email');
  if (emailEl) emailEl.classList.toggle('hidden', !document.querySelector('input[name="s-notif"][value="email"]').checked);
  updateNotifStyles();
}
function showNotifSection(ch) {
  // Legacy compat for onboarding
  document.querySelectorAll('input[name="s-notif"]').forEach(cb => { cb.checked = cb.value === ch; });
  toggleNotifSection(null);
}
function updateNotifStyles() {
  document.querySelectorAll('input[name="s-notif"]').forEach(cb => {
    const label = cb.closest('label');
    if (!label) return;
    const colors = {telegram:'border-blue-500 bg-blue-50',whatsapp:'border-green-500 bg-green-50',email:'border-orange-500 bg-orange-50'};
    if (cb.checked) {
      label.style.borderColor = cb.value==='telegram'?'#3b82f6':cb.value==='whatsapp'?'#22c55e':'#f97316';
      label.style.backgroundColor = cb.value==='telegram'?'#eff6ff':cb.value==='whatsapp'?'#f0fdf4':'#fff7ed';
    } else {
      label.style.borderColor = '#e2e8f0';
      label.style.backgroundColor = '';
    }
  });
}

// Tags (same as onboarding)
function addTag(wrapId, value) {
  const v = value.trim().replace(/,$/,'').trim();
  if (!v) return;
  const wrap = document.getElementById(wrapId);
  const input = wrap.querySelector('.tag-input');
  const existing = Array.from(wrap.querySelectorAll('.tag [contenteditable]')).map(s => s.textContent.trim().toLowerCase());
  if (existing.includes(v.toLowerCase())) { input.value=''; return; }
  const tag = document.createElement('span');
  tag.className = 'tag';
  const label = document.createElement('span');
  label.contentEditable = 'true';
  label.textContent = v;
  label.title = 'Tap to edit';
  label.style.cssText = 'outline:none;cursor:text;min-width:1ch;white-space:nowrap;-webkit-user-select:text;user-select:text;';
  const stopAndFocus = e => { e.stopPropagation(); e.preventDefault(); label.focus(); };
  label.addEventListener('click', e => e.stopPropagation());
  label.addEventListener('touchstart', e => e.stopPropagation(), {passive:false});
  label.addEventListener('touchend', stopAndFocus, {passive:false});
  label.addEventListener('keydown', e => {
    e.stopPropagation();
    if (e.key === 'Enter') { e.preventDefault(); label.blur(); }
    if (e.key === 'Escape') { label.textContent = v; label.blur(); }
  });
  label.addEventListener('blur', () => {
    const nv = label.textContent.trim();
    if (!nv) tag.remove(); else label.textContent = nv;
  });
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.textContent = '×';
  btn.addEventListener('click', e => { e.stopPropagation(); tag.remove(); });
  btn.addEventListener('touchend', e => { e.stopPropagation(); e.preventDefault(); tag.remove(); }, {passive:false});
  tag.appendChild(label);
  tag.appendChild(btn);
  wrap.insertBefore(tag, input);
  input.value = '';
}
function tagKeyDown(e, wrapId) {
  if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); addTag(wrapId, e.target.value); }
}
function getTags(wrapId) {
  return Array.from(document.getElementById(wrapId).querySelectorAll('.tag [contenteditable]')).map(s => s.textContent.trim()).filter(Boolean);
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
  const checked = Array.from(document.querySelectorAll('input[name="s-notif"]:checked')).map(cb => cb.value);
  const ch = checked.length > 0 ? checked.join(',') : 'none';
  const body = {notification_channel: ch};
  if (checked.includes('telegram')) {
    body.telegram_token = document.getElementById('sn-tg-token').value;
    body.telegram_chat_id = document.getElementById('sn-tg-chat-id').value;
  }
  if (checked.includes('whatsapp')) {
    body.twilio_account_sid = document.getElementById('sn-wa-sid').value;
    body.twilio_auth_token  = document.getElementById('sn-wa-token').value;
    body.whatsapp_number    = document.getElementById('sn-wa-number').value;
  }
  if (checked.includes('email')) {
    body.email_address = document.getElementById('sn-email-addr').value;
  }
  await fetch('/api/save-notifications', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  showToast();
}

async function testNotification(channel) {
  const body = {channel};
  if (channel === 'telegram') {
    body.telegram_token = document.getElementById('sn-tg-token').value;
    body.telegram_chat_id = document.getElementById('sn-tg-chat-id').value;
  } else if (channel === 'whatsapp') {
    body.twilio_account_sid = document.getElementById('sn-wa-sid').value;
    body.twilio_auth_token  = document.getElementById('sn-wa-token').value;
    body.whatsapp_number    = document.getElementById('sn-wa-number').value;
  } else if (channel === 'email') {
    body.email_address = document.getElementById('sn-email-addr').value;
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
          auto_apply_enabled: document.getElementById('s-auto-apply')?.checked ? 1 : 0,
    })});
  showToast();
}

function uploadCV(file) {
  if (!file || !file.name.endsWith('.pdf')) {
    showCVStatus('Please upload a PDF file.', 'error'); return;
  }
  if (file.size > 10 * 1024 * 1024) {
    showCVStatus('❌ File too large (max 10 MB).', 'error'); return;
  }
  showCVStatus('Uploading…', 'info');
  const reader = new FileReader();
  reader.onload = function() {
    const base64 = reader.result.split(',')[1];
    const payload = JSON.stringify({filename: file.name, data: base64});
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/upload-cv', true);
    xhr.setRequestHeader('Content-Type', 'application/json');
    xhr.timeout = 30000;
    xhr.onload = function() {
      try {
        const d = JSON.parse(xhr.responseText);
        if (d.success) {
          showCVStatus('✅ CV uploaded successfully!', 'success');
          document.getElementById('cv-current').textContent = '✅ New CV on file.';
          document.getElementById('cv-analyze-btn').classList.remove('hidden');
        } else {
          showCVStatus('❌ ' + (d.error||'Upload failed.'), 'error');
        }
      } catch(e) {
        showCVStatus('❌ Server returned invalid response.', 'error');
      }
    };
    xhr.onerror = function() { showCVStatus('❌ Network error. Check your connection.', 'error'); };
    xhr.ontimeout = function() { showCVStatus('❌ Upload timed out. Try a smaller file.', 'error'); };
    xhr.send(payload);
  };
  reader.onerror = function() { showCVStatus('❌ Could not read file.', 'error'); };
  reader.readAsDataURL(file);
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

// Fix: replace Tailwind has-[:checked] (not in compiled CSS) with JS-driven styles
function initRadioStyles() {
  document.querySelectorAll('input[type="radio"], input[type="checkbox"]').forEach(inp => {
    const label = inp.closest('label');
    if (!label || !label.classList.contains('rounded-xl')) return;
    function upd() {
      const isOn = inp.checked;
      // Find the color from the has-[:checked] class hints
      const cls = label.className;
      let onBorder = '#3b82f6', onBg = '#eff6ff';
      if (cls.includes('green')) { onBorder = '#22c55e'; onBg = '#f0fdf4'; }
      else if (cls.includes('orange')) { onBorder = '#f97316'; onBg = '#fff7ed'; }
      else if (cls.includes('slate')) { onBorder = '#94a3b8'; onBg = '#f8fafc'; }
      label.style.borderColor = isOn ? onBorder : '#e2e8f0';
      label.style.backgroundColor = isOn ? onBg : '';
    }
    inp.addEventListener('change', () => {
      // For radios, reset siblings first
      if (inp.type === 'radio' && inp.name) {
        document.querySelectorAll('input[name="'+inp.name+'"]').forEach(sib => {
          const sl = sib.closest('label');
          if (sl) { sl.style.borderColor = '#e2e8f0'; sl.style.backgroundColor = ''; }
        });
      }
      upd();
    });
    upd(); // Initial state
  });
}
setTimeout(initRadioStyles, 500);
</script>

<div id="cv-optimizer-overlay" class="hidden" style="position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,.45);display:none;align-items:flex-start;justify-content:center;overflow-y:auto;padding:40px 16px;">
  <div style="background:#fff;border-radius:16px;box-shadow:0 20px 60px rgba(0,0,0,.2);max-width:540px;width:100%;padding:28px;position:relative;margin:auto;">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;">
      <h3 style="font-weight:700;font-size:18px;color:#1e293b;margin:0;">CV Analysis</h3>
      <button onclick="closeCvOptimizer()" style="color:#94a3b8;background:none;border:none;cursor:pointer;font-size:22px;line-height:1;padding:0;">&#215;</button>
    </div>
    <div id="cvo-loading" style="text-align:center;padding:40px 0;">
      <div style="width:40px;height:40px;border:3px solid #e2e8f0;border-top-color:#6366f1;border-radius:50%;animation:spin 0.8s linear infinite;margin:0 auto 16px;"></div>
      <p style="color:#64748b;font-size:14px;">Analyzing your CV with Gemini AI&#8230;</p>
    </div>
    <div id="cvo-result" style="display:none;">
      <div style="text-align:center;margin-bottom:24px;">
        <div id="cvo-score-badge" style="width:84px;height:84px;border-radius:50%;display:flex;flex-direction:column;align-items:center;justify-content:center;margin:0 auto 8px;font-weight:700;border:3px solid #e2e8f0;">
          <span id="cvo-score-num" style="font-size:30px;line-height:1;"></span>
          <span style="font-size:11px;opacity:.6;">/100</span>
        </div>
        <div id="cvo-score-label" style="font-weight:600;font-size:15px;margin-bottom:8px;"></div>
        <p id="cvo-summary" style="color:#64748b;font-size:13px;line-height:1.6;max-width:420px;margin:0 auto;"></p>
      </div>
      <div style="margin-bottom:16px;">
        <h4 style="font-size:12px;font-weight:700;color:#059669;margin:0 0 8px;text-transform:uppercase;letter-spacing:.06em;">&#10003; Strengths</h4>
        <ul id="cvo-strengths" style="margin:0;padding:0;list-style:none;"></ul><div style="margin-top:16px"><h4 style="font-size:14px;font-weight:600;color:#374151;margin-bottom:8px">Improvements</h4><div id="cvo-improvements"></div></div><div style="margin-top:16px"><h4 style="font-size:14px;font-weight:600;color:#374151;margin-bottom:8px">ATS Tips</h4><ul id="cvo-ats" style="list-style:none;padding:0;margin:0"></ul></div><div id="cvo-date" style="margin-top:12px;font-size:12px;color:#9ca3af;text-align:right"></div><div id="cvo-error" style="display:none;margin-top:12px;padding:12px;background:#fef2f2;border:1px solid #fecaca;border-radius:8px;color:#dc2626"><strong>Error:</strong> <span id="cvo-error-msg"></span></div>
      </div>

<script>
async function analyzeCvWithAI(forceRefresh) {
  const overlay = document.getElementById('cv-optimizer-overlay');
  overlay.style.display = 'flex';
  overlay.classList.remove('hidden');
  document.getElementById('cvo-loading').style.display = 'block';
  document.getElementById('cvo-result').style.display = 'none';
  document.getElementById('cvo-error').style.display = 'none';
  try {
    const resp = await fetch('/api/cv-optimizer-analyze', {method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    const data = await resp.json();
    if (data.error) throw new Error(data.error);
    renderCvoResult(data);
  } catch(e) {
    document.getElementById('cvo-loading').style.display = 'none';
    document.getElementById('cvo-result').style.display = 'block'; document.getElementById('cvo-error').style.display = 'block';
    document.getElementById('cvo-error-msg').textContent = e.message || 'Analysis failed. Please try again.';
  }
}
function renderCvoResult(d) {
  document.getElementById('cvo-loading').style.display = 'none';
  document.getElementById('cvo-result').style.display = 'block';
  const score = d.score || 0;
  const col = score >= 80 ? '#059669' : score >= 60 ? '#d97706' : '#ef4444';
  const bg  = score >= 80 ? '#ecfdf5' : score >= 60 ? '#fffbeb' : '#fef2f2';
  const badge = document.getElementById('cvo-score-badge');
  badge.style.background = bg; badge.style.color = col; badge.style.border = '3px solid '+col;
  document.getElementById('cvo-score-num').textContent = score;
  const lbl = document.getElementById('cvo-score-label');
  lbl.textContent = d.score_label || ''; lbl.style.color = col;
  document.getElementById('cvo-summary').textContent = d.summary || '';
  document.getElementById('cvo-strengths').innerHTML = (d.strengths||[]).map(s=>
    '<li style="font-size:13px;color:#374151;padding:4px 0 4px 20px;position:relative;"><span style="position:absolute;left:0;color:#059669;">&#10003;</span>'+s+'</li>').join('');
  document.getElementById('cvo-improvements').innerHTML = (d.improvements||[]).map((imp,idx)=>
    '<div style="background:#fffbeb;border-left:3px solid #d97706;border-radius:4px;padding:10px 12px;margin-bottom:8px;"><div style="font-weight:600;font-size:13px;color:#92400e;margin-bottom:3px;">'+(idx+1)+'. '+imp.title+'</div><div style="font-size:13px;color:#374151;line-height:1.5;">'+imp.detail+'</div></div>').join('');
  document.getElementById('cvo-ats').innerHTML = (d.ats_notes||[]).map(a=>
    '<li style="font-size:13px;color:#374151;padding:4px 0 4px 20px;position:relative;"><span style="position:absolute;left:0;color:#6366f1;">&#9670;</span>'+a+'</li>').join('');
  if (d.analyzed_date) {
    const dt = new Date(d.analyzed_date);
    document.getElementById('cvo-date').textContent = 'Last analyzed: '+dt.toLocaleDateString();
  }
}
function closeCvOptimizer() {
  document.getElementById('cv-optimizer-overlay').style.display = 'none';
}
document.addEventListener('click', e => {
  if (!e.target.closest('.dropdown')) document.querySelectorAll('.dropdown').forEach(d => d.classList.remove('open'));
});


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
  <div class="max-w-4xl mx-auto px-5 py-3 flex items-center justify-between">
    <div class="flex items-center gap-3">
      <span class="text-xl">🎯</span>
      <span class="font-bold">Job Hunter</span>
      <span class="text-xs bg-amber-500 text-white px-2 py-0.5 rounded-full font-bold ml-1">ADMIN</span>
    </div>
    <a href="/dashboard" class="btn btn-secondary text-sm px-4 py-2 min-h-0 h-9">← Dashboard</a>
  </div>
</header>

<div class="max-w-4xl mx-auto px-5 py-8">
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
    .desc-expanded { -webkit-line-clamp:unset !important; display:block !important; }
    .expand-hint { font-size:0.7rem;color:#94a3b8;margin-top:0.25rem;cursor:pointer; }
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
    <button onclick="setTab('new')"      id="tab-new"      role="tab" aria-selected="true"  class="tab-btn tab-active flex-1 px-3 py-2 rounded-lg text-sm transition-all whitespace-nowrap">New <span id="cnt-new"></span></button>
    <button onclick="setTab('approved')" id="tab-approved" role="tab" aria-selected="false" class="tab-btn text-slate-600 flex-1 px-3 py-2 rounded-lg text-sm transition-all whitespace-nowrap">Approved <span id="cnt-approved"></span></button>
    <button onclick="setTab('applied')"  id="tab-applied"  role="tab" aria-selected="false" class="tab-btn text-slate-600 flex-1 px-3 py-2 rounded-lg text-sm transition-all whitespace-nowrap">Applied <span id="cnt-applied"></span></button>
    <button onclick="setTab('rejected')" id="tab-rejected" class="tab-btn text-slate-600 flex-1 px-3 py-2 rounded-lg text-sm transition-all whitespace-nowrap">Passed <span id="cnt-rejected"></span></button>
    <button onclick="setTab('activity')" id="tab-activity" class="tab-btn text-slate-600 flex-1 px-3 py-2 rounded-lg text-sm transition-all whitespace-nowrap">Activity</button>
  </div>
  <p id="schedule-hint" class="text-xs text-slate-400 italic text-right mt-2"></p>
</div>

<!-- Sort + Bulk controls -->
<div id="sort-bar" class="max-w-4xl mx-auto px-4 mt-2 flex items-center gap-2 flex-wrap">
  <span class="text-xs text-slate-400 font-medium shrink-0">Sort:</span>
  <button onclick="setSort('date')"    id="sort-date"    class="sort-btn text-xs px-3 py-1.5 rounded-lg border border-slate-200 text-slate-600 font-medium hover:border-blue-400">📅 Date</button>
  <button onclick="setSort('match')"   id="sort-match"   class="sort-btn active-sort text-xs px-3 py-1.5 rounded-lg border font-medium">🎯 Match</button>
  <button onclick="setSort('company')" id="sort-company" class="sort-btn text-xs px-3 py-1.5 rounded-lg border border-slate-200 text-slate-600 font-medium hover:border-blue-400">🏢 Company</button>
  <button onclick="toggleSelect()" id="bulk-toggle" class="hidden text-xs px-3 py-1.5 rounded-lg border border-slate-200 text-slate-500 font-medium hover:border-blue-400 transition-all">☐ Select</button>
</div>

<div id="cv-warning" class="hidden max-w-4xl mx-auto px-4 pt-3">
  <div class="flex items-center gap-3 bg-amber-50 border border-amber-200 rounded-xl px-4 py-3 text-sm text-amber-800">
    <span class="text-xl">⚠️</span>
    <span><strong>No CV uploaded.</strong> Auto-apply will fail without a CV. Go to <a href="#profile" onclick="showTab('profile')" class="underline font-semibold">Profile → CV</a> and paste your resume.</span>
    <button onclick="document.getElementById('cv-warning').classList.add('hidden')" class="ml-auto text-amber-500 hover:text-amber-700 text-lg leading-none">✕</button>
  </div>
</div>

<!-- Onboarding Popup -->
<div id="onboarding-overlay" class="hidden" style="position:fixed;inset:0;z-index:9998;background:rgba(0,0,0,.35);display:none;align-items:center;justify-content:center;">
  <div style="background:#fff;border-radius:16px;box-shadow:0 20px 60px rgba(0,0,0,.18);max-width:420px;width:90%;padding:28px 28px 22px;">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:18px;">
      <h3 style="font-weight:700;font-size:17px;color:#1e293b;margin:0;">Getting Started</h3>
      <button onclick="dismissOnboarding()" style="font-size:12px;color:#94a3b8;background:none;border:none;cursor:pointer;">Skip for now</button>
    </div>
    <div id="ob-milestones" style="display:flex;flex-direction:column;gap:12px;">
      <label data-key="cv_uploaded" style="display:flex;align-items:flex-start;gap:10px;cursor:pointer;">
        <input type="checkbox" class="ob-check" style="margin-top:3px;width:17px;height:17px;accent-color:#3b82f6;cursor:pointer;" />
        <div><div style="font-size:14px;font-weight:500;color:#334155;">Upload your CV</div><div style="font-size:12px;color:#94a3b8;">So we can match you with the right jobs</div></div>
      </label>
      <label data-key="search_configured" style="display:flex;align-items:flex-start;gap:10px;cursor:pointer;">
        <input type="checkbox" class="ob-check" style="margin-top:3px;width:17px;height:17px;accent-color:#3b82f6;cursor:pointer;" />
        <div><div style="font-size:14px;font-weight:500;color:#334155;">Set up your search criteria</div><div style="font-size:12px;color:#94a3b8;">Titles, locations, salary range</div></div>
      </label>
      <label data-key="first_job_reviewed" style="display:flex;align-items:flex-start;gap:10px;cursor:pointer;">
        <input type="checkbox" class="ob-check" style="margin-top:3px;width:17px;height:17px;accent-color:#3b82f6;cursor:pointer;" />
        <div><div style="font-size:14px;font-weight:500;color:#334155;">Review your first job</div><div style="font-size:12px;color:#94a3b8;">Approve or pass to train the matcher</div></div>
      </label>
      <label data-key="auto_apply_choice_made" style="display:flex;align-items:flex-start;gap:10px;cursor:pointer;">
        <input type="checkbox" class="ob-check" style="margin-top:3px;width:17px;height:17px;accent-color:#3b82f6;cursor:pointer;" />
        <div><div style="font-size:14px;font-weight:500;color:#334155;">Choose Manual or Automatic mode</div><div style="font-size:12px;color:#94a3b8;">In Settings &gt; Schedule</div></div>
      </label>
    </div>
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
<div id="pass-modal" class="hidden fixed inset-0 z-50 bg-black/40 items-center justify-center p-4" onclick="if(event.target===this)skipReason()">
  <div class="bg-white rounded-2xl shadow-2xl w-full max-w-xs p-4 fade">
    <h3 class="font-bold text-slate-900 mb-0.5">Why are you passing?</h3>
    <p class="text-xs text-slate-400 mb-4">Helps improve future matches</p>
    <div class="space-y-2 mb-3">
      <button onclick="selectReason('Not a good fit')"        class="reason-btn w-full text-left px-3 py-2.5 rounded-xl border border-slate-200 text-sm text-slate-700 transition-all">🤔 Not a good fit</button>
      <button onclick="selectReason('Wrong seniority level')" class="reason-btn w-full text-left px-3 py-2.5 rounded-xl border border-slate-200 text-sm text-slate-700 transition-all">📊 Wrong seniority level</button>
      <button onclick="selectReason('Salary too low')"        class="reason-btn w-full text-left px-3 py-2.5 rounded-xl border border-slate-200 text-sm text-slate-700 transition-all">💰 Salary too low</button>
      <button onclick="selectReason('Bad company')"           class="reason-btn w-full text-left px-3 py-2.5 rounded-xl border border-slate-200 text-sm text-slate-700 transition-all">🏢 Bad company</button>
      <button onclick="selectReason('Wrong location')"        class="reason-btn w-full text-left px-3 py-2.5 rounded-xl border border-slate-200 text-sm text-slate-700 transition-all">📍 Wrong location</button>
      <button onclick="selectReason('Already applied elsewhere')"       class="reason-btn w-full text-left px-3 py-2.5 rounded-xl border border-slate-200 text-sm text-slate-700 transition-all">✓ Already applied elsewhere</button>
      <button onclick="selectReason('Not relevant to my search')" class="reason-btn w-full text-left px-3 py-2.5 rounded-xl border border-slate-200 text-sm text-slate-700 transition-all">🔍 Not relevant to my search</button>
    </div>
    <button onclick="skipReason()" class="w-full text-sm text-slate-400 hover:text-slate-600 py-2 transition-all">Skip — no reason</button>
  </div>
</div>

  <!-- Cover Letter Modal (admin only) -->
  <div id="cl-modal" class="hidden fixed inset-0 z-50 bg-black/40 items-center justify-center p-4" onclick="if(event.target===this)closeCoverLetter()">
    <div class="bg-white rounded-2xl shadow-2xl w-full max-w-md p-5 fade max-h-[85vh] flex flex-col">
      <div class="flex items-center justify-between mb-3">
        <h3 class="font-bold text-slate-900" id="cl-title">Cover Letter</h3>
        <button onclick="closeCoverLetter()" class="text-slate-400 hover:text-slate-600 text-xl">&times;</button>
      </div>
      <textarea id="cl-text" class="flex-1 w-full border border-slate-200 rounded-xl p-3 text-sm text-slate-700 resize-none min-h-[200px] focus:ring-2 focus:ring-blue-300 focus:border-blue-400 outline-none" placeholder="Click Generate to create a cover letter..."></textarea>
      <div class="flex gap-2 mt-3">
        <button onclick="generateCoverLetter()" id="cl-gen-btn" class="flex-1 btn bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium py-2.5 rounded-xl transition-colors">Generate</button>
        <button onclick="saveCoverLetter()" class="flex-1 btn bg-slate-100 hover:bg-slate-200 text-slate-700 text-sm font-medium py-2.5 rounded-xl transition-colors">Save</button>
        <button onclick="copyCoverLetter()" class="btn bg-slate-100 hover:bg-slate-200 text-slate-700 text-sm font-medium py-2.5 px-4 rounded-xl transition-colors">\U0001F4CB Copy</button>
      </div>
      <p id="cl-status" class="text-xs text-slate-400 mt-2 text-center hidden"></p>
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
    let _dashAdmin = false;
    fetch('/api/me').then(r=>r.json()).then(u=>{ _dashAdmin = !!(u.role === 'admin' || u.is_admin); }).catch(()=>{});
let tab = 'new';
let me = {};
let sortBy = 'match';
var applyFilter = 'all';
var approvedFilter = 'all';
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

    // ── Cover Letter (admin) ──
    let _clJobId = null;
    function openCoverLetter(id) {
      _clJobId = id;
      const m = document.getElementById('cl-modal');
      document.getElementById('cl-text').value = '';
      document.getElementById('cl-status').classList.add('hidden');
      m.classList.remove('hidden');
      m.classList.add('flex');
      // Pre-load existing cover letter if any
      fetch('/api/jobs?status=all').then(r=>r.json()).then(jobs=>{
        const j = (jobs.jobs||jobs).find(x=>x.id===id);
        if(j && j.cover_letter) document.getElementById('cl-text').value = j.cover_letter;
      }).catch(()=>{});
    }
    function closeCoverLetter() {
      _clJobId = null;
      const m = document.getElementById('cl-modal');
      m.classList.remove('flex');
      m.classList.add('hidden');
    }
    async function generateCoverLetter() {
      const btn = document.getElementById('cl-gen-btn');
      const status = document.getElementById('cl-status');
      btn.disabled = true; btn.textContent = 'Generating...';
      status.textContent = 'Calling AI...'; status.classList.remove('hidden');
      try {
        const r = await fetch('/api/jobs/'+_clJobId+'/cover-letter', {
          method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({action:'generate'})
        });
        const d = await r.json();
        if(d.letter) {
          document.getElementById('cl-text').value = d.letter;
          status.textContent = 'Generated! Edit as needed.';
        } else {
          status.textContent = d.error || 'Generation failed';
        }
      } catch(e) { status.textContent = 'Error: '+e.message; }
      btn.disabled = false; btn.textContent = 'Generate';
    }
    async function saveCoverLetter() {
      const status = document.getElementById('cl-status');
      try {
        await fetch('/api/jobs/'+_clJobId+'/cover-letter', {
          method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({action:'save', letter: document.getElementById('cl-text').value})
        });
        status.textContent = 'Saved!'; status.classList.remove('hidden');
      } catch(e) { status.textContent = 'Save failed'; status.classList.remove('hidden'); }
    }
    function copyCoverLetter() {
      const text = document.getElementById('cl-text').value;
      navigator.clipboard.writeText(text).then(()=>{
        const status = document.getElementById('cl-status');
        status.textContent = 'Copied to clipboard!'; status.classList.remove('hidden');
        setTimeout(()=> status.classList.add('hidden'), 2000);
      });
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
    const detailsFailed = item.details && /failed|error/i.test(item.details);
    const detailsSuccess = item.details && /submitted|success/i.test(item.details);
    const isFail = detailsFailed || (!detailsSuccess && ['job_rejected','bulk_reject'].includes(item.event_type));
    const isSuccess = !isFail && (detailsSuccess || ['job_approved','job_applied','bulk_approve','cv_uploaded','cv_analyzed'].includes(item.event_type));
    const rowBg = isSuccess ? 'bg-emerald-50 border-emerald-200' : isFail ? 'bg-red-50 border-red-200' : 'bg-white border-slate-100';
    const detailColor = isSuccess ? 'text-emerald-600' : isFail ? 'text-red-500' : 'text-slate-500';
    const labelColor = isSuccess ? 'text-emerald-800' : isFail ? 'text-red-700' : 'text-slate-800';
    return `<div class="${rowBg} rounded-xl border px-4 py-3 flex items-center gap-3 fade">
      <span class="text-xl w-8 text-center shrink-0">${icon}</span>
      <div class="flex-1 min-w-0">
        <p class="text-sm font-semibold ${labelColor}">${label}</p>
        ${item.details ? `<p class="text-xs ${detailColor} mt-0.5 truncate">${item.details}</p>` : ''}
      </div>
      <span class="text-xs text-slate-400 shrink-0">${dateStr}</span>
    </div>`;
  }).join('');
}

async function loadStats() {
  const s = await api('/api/stats');
  const set = (id, n) => { const el = document.getElementById(id); if (el) el.textContent = n > 0 ? '(' + n + ')' : ''; };
  set('cnt-new', s.new);
  set('cnt-approved', s.approved);
  set('cnt-applied', s.applied);
  set('cnt-rejected', s.rejected);
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
      <button onclick="openPassModal(${job.id})" class="btn-touch w-full bg-red-50 hover:bg-red-100 text-red-600 text-sm font-medium rounded-xl px-4">❌ Pass</button>
          ${_dashAdmin ? '<button onclick="openCoverLetter('+job.id+')" class="btn-touch w-full bg-purple-50 hover:bg-purple-100 text-purple-600 text-sm font-medium rounded-xl px-4 py-2.5 mt-1">\u270D\uFE0F Cover Letter</button>' : ''}
    </div>`;
  if (job.status === 'approved') {
    const _ftLabels = {captcha:'🤖 Captcha',timeout:'⏱ Timeout',login_wall:'🔐 Login Wall',form_validation:'📋 Form Error',network_error:'🌐 Network Error',other:'❌ Other'};
    const failTypeBadge = job.apply_failure_type ? `<span class="inline-flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full bg-amber-50 border border-amber-200 text-amber-700 mt-1">${_ftLabels[job.apply_failure_type] || job.apply_failure_type}</span>` : '';
    const failInfo = job.apply_status === 'failed' ? `<div class="text-xs text-red-600 bg-red-50 rounded-lg px-3 py-2 mb-2">⚠️ Auto-apply failed${job.apply_error ? ': '+job.apply_error.substring(0,80) : ''}. Apply manually or remove.</div>` : '';
    return `
    <div class="mt-4 pt-4 border-t border-slate-100 space-y-2">
      ${failInfo}
      ${failTypeBadge}
      <div class="space-y-2">
        <button onclick="markApplied(${job.id})" class="btn-touch w-full bg-green-600 hover:bg-green-700 text-white text-sm font-semibold rounded-xl px-4 py-2.5">Mark as Applied</button>
        <button onclick="openPassModal(${job.id})" class="btn-touch w-full bg-red-50 hover:bg-red-100 text-red-600 text-sm font-medium rounded-xl px-4 py-2.5">Remove</button>
          ${_dashAdmin ? '<button onclick="openCoverLetter('+job.id+')" class="btn-touch w-full bg-purple-50 hover:bg-purple-100 text-purple-600 text-sm font-medium rounded-xl px-4 py-2.5 mt-1">\u270D\uFE0F Cover Letter</button>' : ''}
      </div>
      <p class="text-xs text-slate-400 text-center">⏰ Auto-apply scheduled at ${me.apply_hour ? (me.apply_hour > 12 ? (me.apply_hour-12)+' PM' : me.apply_hour+' AM') : '2 PM'}</p>
    </div>`;
  }
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
  if (job.status === 'rejected') return `
    <div class="mt-4 pt-4 border-t border-slate-100">
      <button onclick="restoreToNew(${job.id})" class="btn-touch w-full bg-slate-100 hover:bg-slate-200 text-slate-700 text-sm font-medium rounded-xl px-4 py-2.5 transition-all">♻️ Restore to New</button>
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
  const st = job.status || 'new';
  const map = {
    'new':      {bg:'bg-sky-50 text-sky-600 border-sky-200', label:'New', next:'approve'},
    'approved': {bg:'bg-emerald-50 text-emerald-600 border-emerald-200', label:'Approved', next:'reject'},
    'applied':  {bg:'bg-purple-50 text-purple-700 border-purple-200', label:'Applied', next:null},
    'rejected': {bg:'bg-red-50 text-red-500 border-red-200', label:'Passed', next:'approve'}
  };
  const info = map[st] || map['new'];
  const click = info.next ? ` onclick="cycleStatus(${job.id},'${info.next}')" style="cursor:pointer" title="Click to change status"` : ' title="Status: Applied"';
  return `<span class="inline-flex items-center text-xs px-2 py-0.5 rounded-full border ${info.bg} select-none transition-all hover:shadow-sm"${click}>${info.label}</span>`;
}
async function cycleStatus(id, action) {
  await api('/api/jobs/'+id+'/'+action, 'POST', {});
  loadAll();
}
function markApplied(id) {
  // Show confirmation modal
  const overlay = document.createElement('div');
  overlay.id = 'apply-modal-overlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.4);z-index:100;display:flex;align-items:center;justify-content:center;padding:1rem;';
  overlay.innerHTML = `
    <div class="bg-white rounded-2xl shadow-2xl w-full max-w-xs p-4 fade">
      <h3 class="text-lg font-bold text-slate-900 mb-2">Did you apply?</h3>
      <p class="text-sm text-slate-500 mb-4">Did you complete the application for this job?</p>
      <div class="space-y-2">
        <button onclick="confirmApply(${id}, true)" class="w-full bg-green-600 hover:bg-green-700 text-white text-sm font-semibold py-2.5 rounded-xl transition-all">Yes, I applied</button>
        <button onclick="confirmApply(${id}, false)" class="w-full bg-red-50 hover:bg-red-100 text-red-600 text-sm font-medium py-2.5 rounded-xl transition-all">No, couldn't apply — Remove</button>
        <button onclick="closeApplyModal()" class="w-full text-slate-400 text-sm py-2 hover:text-slate-600 transition-all">Cancel</button>
      </div>
    </div>
  `;
  overlay.addEventListener('click', (e) => { if (e.target === overlay) closeApplyModal(); });
  document.body.appendChild(overlay);
}

function closeApplyModal() {
  const m = document.getElementById('apply-modal-overlay');
  if (m) m.remove();
}

async function confirmApply(id, success) {
  closeApplyModal();
  const card = document.getElementById('job-'+id);
  if (card) {
    card.style.transition = 'all 0.3s ease';
    card.style.opacity = '0';
    card.style.transform = 'translateX(100px)';
    setTimeout(() => card.remove(), 400);
  }
  if (success) {
    await api('/api/jobs/'+id+'/applied', 'POST', {notes:'Manually applied by user'});
  } else {
    await api('/api/jobs/'+id+'/reject', 'POST', {reason:'Could not complete application'});
  }
  loadStats();
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
  const isDead = job.url_verified === 0;
  return `
  <div class="card bg-white rounded-2xl shadow-sm border ${isDead?'border-red-200 opacity-60':'border-slate-100'} p-4 sm:p-5 fade ${isSelected?'ring-2 ring-blue-400':''}" id="job-${job.id}"
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
    ${job.publish_date ? `<span class="text-slate-400 text-xs">📅 Published ${ago(job.publish_date)}</span>` : ''}
    ${job.description ? `<div class="mt-3"><p class="text-sm text-slate-600 leading-relaxed">${job.description}</p>${job.full_description && job.full_description.length > 60 && job.full_description !== job.description ? `<div class="cursor-pointer" onclick="event.stopPropagation();toggleDesc(this)"><div class="clamp3 text-sm text-slate-500 leading-relaxed mt-2 border-t border-slate-100 pt-2">${job.full_description}</div><p class="expand-hint">▼ Tap to expand full description</p></div>` : `<div class="mt-1"><a href="${job.url}" target="_blank" class="text-xs text-blue-500 hover:text-blue-700">View full description ↗</a></div>`}</div>` : ''}
    ${isSelectable ? '' : actionBar(job)}
  </div>`;
}

async function loadJobs(status) {
  const list  = document.getElementById('jobs-list');
  const empty = document.getElementById('empty-state');
  list.innerHTML = '<div class="text-center py-10 text-slate-300 text-sm animate-pulse">Loading…</div>';
  let jobs = await api('/api/jobs?status=' + status + '&sort=' + sortBy);
  if (status === 'new') jobs = (jobs||[]).filter(j => j.url_verified !== 0);
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
    if (status === 'approved') {
      const aCounts = {all: jobs.length, fresh: 0, retry: 0};
      jobs.forEach(j => { if (j.apply_status && (j.apply_status === 'failed' || j.apply_status === 'manual_required')) aCounts.retry++; else aCounts.fresh++; });
      const aPill = (key, label, cls) => {
        const active = approvedFilter === key;
        return '<button onclick="setApprFilter(' + "'" + key + "'" + ')" class="px-3 py-1 rounded-full text-xs font-medium border transition-all '
          + (active ? cls + ' ring-1 shadow-sm' : 'bg-white text-slate-500 border-slate-200 hover:border-slate-300') + '">'
          + label + '</button>';
      };
      html += '<div class="flex flex-wrap gap-2 mb-3 mt-1">'
        + aPill('all', 'All (' + aCounts.all + ')', 'bg-slate-100 text-slate-700 border-slate-300')
        + aPill('fresh', 'New (' + aCounts.fresh + ')', 'bg-blue-100 text-blue-700 border-blue-300')
        + aPill('retry', 'Retry (' + aCounts.retry + ')', 'bg-amber-100 text-amber-700 border-amber-300')
        + '</div>';
      if (approvedFilter === 'fresh') jobs = jobs.filter(j => !j.apply_status || (j.apply_status !== 'failed' && j.apply_status !== 'manual_required'));
      if (approvedFilter === 'retry') jobs = jobs.filter(j => j.apply_status && (j.apply_status === 'failed' || j.apply_status === 'manual_required'));
      html += `<div class="bg-gradient-to-r from-green-50 to-emerald-50 border border-green-200 rounded-2xl p-4 flex items-center justify-between fade"><div><p class="font-bold text-green-800 text-sm sm:text-base">${jobs.length} position${jobs.length>1?'s':''} queued</p><p class="text-xs sm:text-sm text-green-600 mt-0.5">Auto-apply runs at your scheduled time</p></div><button id="run-apply-btn" onclick="runApply()" class="flex items-center gap-2 bg-green-600 hover:bg-green-700 active:bg-green-800 text-white text-sm font-semibold px-4 py-2 rounded-xl shadow-sm transition-all">🚀 Apply Now</button></div>`;
    }
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


function setApprFilter(k) { approvedFilter = k; loadAll(); }

function retryApply(id) {
  if (!confirm('Retry auto-apply for this job?')) return;
  var card = document.getElementById('job-'+id);
  if (card) { card.style.opacity='.35'; card.style.pointerEvents='none'; }
  api('/api/jobs/'+id+'/retry', 'POST', {}).then(function() {
    showToast('Job moved back to Approved queue for retry');
    loadAll();
  });
}

const _PASS_REASONS = ['Bad fit','Wrong location','Salary too low','Bad company','Already applied','Other'];
function _doReject(id, reason) {
  const m = document.getElementById('pass-modal'); if (m) m.remove();
  act(id, 'reject', reason);
}
function showRejectModal(id) {
  const ex = document.getElementById('pass-modal'); if (ex) ex.remove();
  const m = document.createElement('div');
  m.id = 'pass-modal';
  m.className = 'fixed inset-0 bg-black/50 flex items-center justify-center z-50';
  let html = '<div class="bg-white rounded-2xl p-6 max-w-sm w-full mx-4 shadow-xl">';
  html += '<h3 class="font-semibold text-slate-800 mb-3">Pass on this job — why?</h3>';
  html += '<div class="flex flex-col gap-2">';
  _PASS_REASONS.forEach(function(r) {
    html += '<button data-id="' + id + '" data-reason="' + r + '" class="pass-reason-btn text-left px-4 py-2 rounded-lg bg-slate-50 hover:bg-red-50 hover:text-red-600 border border-slate-200 text-sm transition-colors">' + r + '</button>';
  });
  html += '<button data-id="' + id + '" data-reason="" class="pass-reason-btn mt-1 text-sm text-slate-400 hover:text-slate-600 text-left px-2 py-1">No reason</button>';
  html += '</div><button id="pass-cancel" class="mt-3 w-full text-xs text-center text-slate-400 hover:text-slate-600">Cancel</button></div>';
  m.innerHTML = html;
  document.body.appendChild(m);
  m.querySelectorAll('.pass-reason-btn').forEach(function(btn) {
    btn.addEventListener('click', function() { _doReject(Number(btn.dataset.id), btn.dataset.reason); });
  });
  const cancelBtn = document.getElementById('pass-cancel'); if (cancelBtn) cancelBtn.addEventListener('click', function() { m.remove(); });
  m.addEventListener('click', function(e) { if (e.target === m) m.remove(); });
}

async function act(id, action, reason='') {
  if (action === 'reject' && !reason) { showRejectModal(id); return; }
  const card = document.getElementById('job-'+id);
  if (card) {
    card.style.transition = 'all 0.3s ease';
    card.style.opacity = '0';
    card.style.transform = 'translateX(100px)';
    card.style.maxHeight = card.offsetHeight + 'px';
    setTimeout(() => { card.style.maxHeight = '0'; card.style.padding = '0'; card.style.margin = '0'; card.style.overflow = 'hidden'; }, 300);
    setTimeout(() => card.remove(), 500);
  }
  try {
    await api('/api/jobs/'+id+'/'+action, 'POST', {notes: reason});
    loadStats();
  } catch(e) {
    console.error('Action failed:', e);
    loadAll();
  }
}

function escHtml(s) { if (!s) return ""; const d = document.createElement("div"); d.textContent = s; return d.innerHTML.replace(/\\n/g,"<br>"); }

function toggleDesc(el) {
  const p = el.querySelector('.clamp3, .desc-expanded');
  const hint = el.querySelector('.expand-hint');
  if (!p) return;
  if (p.classList.contains('clamp3')) {
    p.classList.remove('clamp3');
    p.classList.add('desc-expanded');
    if (hint) hint.textContent = '▲ Tap to collapse';
  } else {
    p.classList.add('clamp3');
    p.classList.remove('desc-expanded');
    if (hint) hint.textContent = '▼ Tap to expand';
  }
}

async function restoreToNew(id) {
  const card = document.getElementById('job-'+id);
  if (card) {
    card.style.transition = 'all 0.3s ease';
    card.style.opacity = '0';
    card.style.transform = 'translateX(-100px)';
    setTimeout(() => card.remove(), 400);
  }
  try {
    await api('/api/jobs/'+id+'/restore', 'POST', {});
    showToast('Job restored to New tab');
    setTab('new');
  } catch(e) {
    console.error('Restore failed:', e);
    loadAll();
  }
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
  // search button is in empty state only
  if (!isNew && selectMode) clearSelect();

  if (isActivity) {
    loadActivity();
  } else {
    loadJobs(t);
  }
}

async function loadAll() {
  await Promise.all([loadStats(), tab === 'activity' ? loadActivity() : loadJobs(tab)]);
      loadOnboarding();
}


async function loadOnboarding() {
  try {
    const [meR, statsR] = await Promise.all([fetch('/api/me'), fetch('/api/stats')]);
    const u = await meR.json();
    const stats = await statsR.json();
    const dismissed = u.onboarding_dismissed;
    if (dismissed) return;
    // Only show for first-time or zero-activity users
    const totalJobs = (stats.approved||0) + (stats.applied||0) + (stats.passed||0) + (stats.new||0);
    if (totalJobs > 0) return;           // active user
    if (u.cv_path && u.job_titles) return; // configured user
    // Show popup
    const overlay = document.getElementById('onboarding-overlay');
    overlay.style.display = 'flex';
    overlay.classList.remove('hidden');
    // Wire checkbox changes — auto-close + persist when all checked
    document.querySelectorAll('.ob-check').forEach(cb => {
      cb.addEventListener('change', async () => {
        if (cb.checked) cb.closest('label').style.opacity = '0.5';
        const allChecked = [...document.querySelectorAll('.ob-check')].every(c => c.checked);
        if (allChecked) {
          try { await fetch('/api/dismiss-onboarding', {method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}); } catch(e){}
          overlay.style.display = 'none';
        }
      });
    });
  } catch(e) { console.log('onboarding', e); }
}
async function dismissOnboarding() {
  document.getElementById('onboarding-overlay').style.display = 'none';
  try { await fetch('/api/dismiss-onboarding', {method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}); } catch(e) {}
}
async function runSearch() {
  if (window.__searchRunning) return; // prevent concurrent searches
  window.__searchRunning = true;
  const setBtn = (disabled, html) => {
    const b = document.getElementById('empty-search-cta');
    if (b) { b.disabled = disabled; b.innerHTML = html; }
  };
  setBtn(true, '⏳');
  try {
    const r = await fetch('/api/run-search', {method:'POST', headers:{'Content-Type':'application/json'}});
    if (!r.ok) {
      showToast('Failed to start search. Please try again.');
      setBtn(false, '🔍');
      window.__searchRunning = false;
      return;
    }
    // Poll activity log until a new jobs_searched entry appears
    // Activity dates are stored as SQL "YYYY-MM-DD HH:MM:SS" UTC — convert for correct comparison
    const startTime = Date.now();
    const poll = async () => {
      if (Date.now() - startTime > 180000) {
        showToast('Search is taking longer than usual. Check the Activity tab for results.');
        setBtn(false, '🔍');
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
          setBtn(false, '🔍');
          window.__searchRunning = false;
        } else { setTimeout(poll, 5000); }
      } catch(e) { setTimeout(poll, 5000); }
    };
    setTimeout(poll, 8000);
  } catch(e) {
    showToast('Connection error — could not start search.');
    setBtn(false, '🔍');
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

loadMe().then(() => loadAll());
setInterval(loadAll, 5 * 60 * 1000);
</script>
</body>
</html>"""

# ─────────────────────────────────────────────────────────────────────────────
# HTTP HANDLER
# ─────────────────────────────────────────────────────────────────────────────

def _extract_cv_text(cv_path, cv_summary):
    if cv_path and os.path.exists(cv_path):
        try:
            if cv_path.lower().endswith('.pdf'):
                try:
                    import pypdf
                    _pages = pypdf.PdfReader(cv_path).pages
                    _t = '\n'.join(p.extract_text() or '' for p in _pages)
                    if len(_t.strip()) > 100:
                        return _t
                except Exception:
                    pass
            elif cv_path.lower().endswith(('.docx', '.doc')):
                try:
                    from docx import Document as _Docx
                    _t = '\n'.join(p.text for p in _Docx(cv_path).paragraphs)
                    if len(_t.strip()) > 100:
                        return _t
                except Exception:
                    pass
        except Exception:
            pass
    return cv_summary or ''


def _call_gemini_cv_optimizer(cv_text):
    import urllib.request as _ureq
    _key = os.environ.get('GEMINI_API_KEY', '')
    if not _key:
        raise ValueError('GEMINI_API_KEY not set in environment')
    _cv = cv_text[:8000]
    _p1 = 'You are an expert CV/resume coach. Analyze the CV below and return ONLY valid JSON.'
    _p2 = ' No markdown, no backticks. Output must be parseable by json.loads().'
    _p3 = ' CV language may vary but output MUST be in English.'
    _p4 = ' Score 0-100 (clarity 20, impact 25, ATS 20, structure 20, quality 15).'
    _p5 = ' Provide 3-5 improvements and 2-4 strengths.'
    _fmt = '{"score":72,"score_label":"Good","summary":"...","strengths":["..."],"improvements":[{"title":"...","detail":"..."}],"ats_notes":["..."]}' 
    _prompt = _p1 + _p2 + _p3 + _p4 + _p5 + '\n\nFormat: ' + _fmt + '\n\nCV:\n' + _cv
    _body = json.dumps({
        'contents': [{'parts': [{'text': _prompt}]}],
        'generationConfig': {'temperature': 0.3, 'maxOutputTokens': 8192}
    }).encode('utf-8')
    _url = 'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=' + _key
    _req = _ureq.Request(_url, data=_body, headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with _ureq.urlopen(_req, timeout=30) as _r:
            _d = json.loads(_r.read().decode('utf-8'))
    except Exception as _he:
        _eb = b''
        try: _eb = _he.read()
        except: pass
        raise Exception('Gemini API error: ' + str(_he) + (' - ' + _eb.decode('utf-8', errors='replace')[:300] if _eb else ''))
    _t = _d['candidates'][0]['content']['parts'][0]['text'].strip()
    if _t.startswith('```'):
        _lines = _t.split('\n')
        _t = '\n'.join(_lines[1:-1] if _lines[-1].strip() == '```' else _lines[1:])
    return json.loads(_t)


class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] {fmt % args}")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def send_html(self, html: str, code: int = 200):
        # Strip mojibake chars (U+0080-U+00FF: garbled box-drawing artifacts in templates)
        import re as _re_mj
        html = _re_mj.sub(r'[\x80-\xff]+', '', html)
        for _repair_pass in range(8):  # undo multiple layers of mojibake
            try:
                html = html.encode('latin-1').decode('utf-8', errors='strict')
            except Exception:
                break
        body = html.encode('utf-8')
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
        try:
            cl_header = self.headers.get("Content-Length", "")
            length = int(cl_header) if cl_header.strip() else 0
        except (ValueError, TypeError):
            length = 0
        if length > 0:
            data = self.rfile.read(length)
            print(f"[read_body] Content-Length={length}, read {len(data)} bytes")
            return data
        # Handle chunked transfer encoding (common on mobile browsers)
        te = self.headers.get("Transfer-Encoding", "")
        if "chunked" in te.lower():
            chunks = []
            while True:
                size_line = self.rfile.readline().strip()
                if not size_line:
                    break
                try:
                    size = int(size_line, 16)
                except ValueError:
                    break
                if size == 0:
                    break
                chunks.append(self.rfile.read(size))
                self.rfile.read(2)  # consume trailing CRLF
            result = b"".join(chunks)
            print(f"[read_body] chunked, total {len(result)} bytes")
            return result
        # Fallback: try to read whatever is available (some proxies strip headers)
        import select
        try:
            if hasattr(self.rfile, 'fileno'):
                ready, _, _ = select.select([self.rfile], [], [], 2.0)
                if ready:
                    data = self.rfile.read(10 * 1024 * 1024)  # up to 10MB
                    print(f"[read_body] fallback read {len(data)} bytes")
                    return data
        except Exception as e:
            print(f"[read_body] fallback select error: {e}")
        print("[read_body] no Content-Length, not chunked, fallback empty")
        return b""

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

        # Compiled Tailwind CSS
        if path == "/static/tw.css":
            self.send_response(200)
            self.send_header("Content-Type", "text/css; charset=utf-8")
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
            self.send_header("Content-Length", str(len(_TW_CSS)))
            self.end_headers()
            self.wfile.write(_TW_CSS)
            return

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

        if path in ("/admin", "/admin/"):
            user = self.require_auth()
            if not user:
                return
            if user.get("role") != "admin":
                self.redirect("/dashboard")
                return
            self.send_html(ADMIN_HTML)
            return

        # Health check (no auth required)
        if path == "/api/health":
            import time as _ht
            conn = database.get_db()
            user_count = conn.execute("SELECT COUNT(*) FROM users WHERE is_active=1").fetchone()[0]
            job_count = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            last_search = conn.execute(
                "SELECT details, created_date FROM activity_log "
                "WHERE event_type='jobs_searched' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            last_apply = conn.execute(
                "SELECT details, created_date FROM activity_log "
                "WHERE event_type='job_applied' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            conn.close()
            self.send_json({
                "status": "ok",
                "uptime_info": "server running",
                "active_users": user_count,
                "total_jobs": job_count,
                "last_search": {"detail": repair_mojibake(last_search[0]) if last_search else None, "date": last_search[1] if last_search else None},
                "last_apply": {"detail": repair_mojibake(last_apply[0]) if last_apply else None, "date": last_apply[1] if last_apply else None},
                "scheduler": "active (checks every 60s)",
            })
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
            status  = qs.get("status", ["new"])[0]
            sort_by = qs.get("sort",   ["date"])[0]
            order_map = {
                "match":   "COALESCE(match_score, -1) DESC",
                "company": "company ASC",
                "date":    "found_date DESC",
            }
            order = order_map.get(sort_by, "found_date DESC")
            conn  = database.get_db()
            database.expire_old_jobs(conn, user["id"])
            if status == "all":
                rows = conn.execute(
                    f"SELECT * FROM jobs WHERE user_id=? ORDER BY {order}",
                    (user["id"],)
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT * FROM jobs WHERE user_id=? AND status=? ORDER BY {order}",
                    (user["id"], status)
                ).fetchall()
            jobs_list = [dict(r) for r in rows]

            # Auto-compute match/candidate scores for any unscored jobs (async)
            try:
                from ai_analysis import compute_match_score, compute_candidate_score
                profile = conn.execute(
                    "SELECT * FROM user_profiles WHERE user_id=?", (user["id"],)
                ).fetchone()
                if profile and profile["cv_analyzed"]:
                    profile_dict = dict(profile)
                    unscored_ids = [j["id"] for j in jobs_list if j.get("match_score") is None]
                    if unscored_ids:
                        def _bg_score(job_ids, uid, pd):
                            try:
                                c2 = database.get_db()
                                for jid in job_ids:
                                    row = c2.execute(
                                        "SELECT * FROM jobs WHERE id=? AND user_id=?",
                                        (jid, uid)
                                    ).fetchone()
                                    if not row:
                                        continue
                                    jd = dict(row)
                                    if jd.get("match_score") is not None:
                                        continue
                                    try:
                                        ms = compute_match_score(jd, pd)
                                        cs = compute_candidate_score(jd, pd)
                                        c2.execute(
                                            "UPDATE jobs SET match_score=?, candidate_score=? WHERE id=?",
                                            (ms, cs, jid)
                                        )
                                        c2.commit()
                                    except Exception as ie:
                                        print(f"[score-bg] job {jid}: {ie}")
                                c2.close()
                            except Exception as be:
                                print(f"[score-bg] fatal: {be}")
                        threading.Thread(
                            target=_bg_score,
                            args=(unscored_ids, user["id"], profile_dict),
                            daemon=True
                        ).start()
            except Exception as e:
                print(f"[score] Error spawning score thread: {e}")

            conn.close()
            self.send_json(jobs_list)
            return

        if path == "/api/activity":
            user = self.require_auth()
            if not user:
                return
            items = database.get_activity(user["id"], limit=100)
            for _it in items:
                if "details" in _it and _it["details"]:
                    _it["details"] = repair_mojibake(_it["details"])
            self.send_json(items)
            return

        if path == "/api/admin/dedup":
            user = self.require_auth()
            if not user or user.get("role") != "admin":
                self.send_json({"error": "forbidden"}, status=403)
                return
            conn = database.get_db()
            # Find duplicate jobs: same user_id + company + title, keep the one with lowest id
            dupes = conn.execute("""
                SELECT j.id FROM jobs j
                INNER JOIN (
                    SELECT user_id, LOWER(TRIM(company)) as c, LOWER(TRIM(title)) as t, MIN(id) as min_id
                    FROM jobs
                    GROUP BY user_id, LOWER(TRIM(company)), LOWER(TRIM(title))
                    HAVING COUNT(*) > 1
                ) d ON j.user_id = d.user_id AND LOWER(TRIM(j.company)) = d.c AND LOWER(TRIM(j.title)) = d.t AND j.id != d.min_id
            """).fetchall()
            dupe_ids = [r[0] for r in dupes]
            if dupe_ids:
                conn.execute("DELETE FROM jobs WHERE id IN (%s)" % ",".join(str(i) for i in dupe_ids))
                conn.commit()
            conn.close()
            self.send_json({"removed": len(dupe_ids), "ids": dupe_ids})
            return

        if path == "/api/admin/users":
            user = self.require_auth()
            if not user or user.get("role") != "admin":
                self.send_json({"error": "Forbidden"}, 403)
                return
            conn = database.get_db()
            rows = conn.execute("""
                SELECT u.id, u.name, u.email, u.role, u.is_active, u.created_date,
                       (SELECT COUNT(*) FROM jobs j WHERE j.user_id=u.id AND j.status='new')      AS stats_new,
                       (SELECT COUNT(*) FROM jobs j WHERE j.user_id=u.id AND j.status='approved') AS stats_approved,
                       (SELECT COUNT(*) FROM jobs j WHERE j.user_id=u.id AND j.status='applied')  AS stats_applied,
                       (SELECT COUNT(*) FROM jobs j WHERE j.user_id=u.id)                          AS stats_total
                FROM users u ORDER BY u.created_date DESC
            """).fetchall()
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
        try:
            self._do_POST_inner()
        except Exception as exc:
            import traceback
            print(f"[do_POST] ❌ Unhandled exception on {self.path}: {exc}\n{traceback.format_exc()}")
            try:
                self.send_json({"error": f"Server error: {exc}"}, code=500)
            except Exception:
                pass

    def _do_POST_inner(self):
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
            # Notify admin about new signup (non-blocking)
            try:
                notify_admin_new_user(new_user_email=email, new_user_name=name)
            except Exception as e:
                print(f"[admin-notify] registration hook error: {e}")
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
            try:
                import base64 as b64mod
                ct = self.headers.get("Content-Type", "")
                cl = self.headers.get("Content-Length", "0")
                print(f"[cv/upload] Content-Type={ct!r}  Content-Length={cl}")
                body = self.read_body()
                print(f"[cv/upload] body size = {len(body)} bytes")
                if not body:
                    self.send_json({"error": "No data received. Try again."})
                    return

                # Accept JSON with base64 data (mobile-safe) or multipart
                if "application/json" in ct:
                    payload = json.loads(body)
                    filename = payload.get("filename", "upload.pdf")
                    b64data = payload.get("data", "")
                    if not b64data:
                        self.send_json({"error": "No file data received."})
                        return
                    file_data = b64mod.b64decode(b64data)
                    print(f"[cv/upload] JSON mode: file={filename}  decoded={len(file_data)} bytes")
                else:
                    parts = parse_multipart(self.headers, body)
                    cv_part = parts.get("cv")
                    if not cv_part or not isinstance(cv_part, dict):
                        print(f"[cv/upload] parse_multipart keys: {list(parts.keys())}")
                        self.send_json({"error": "No file received."})
                        return
                    filename = cv_part["filename"]
                    file_data = cv_part["data"]
                    print(f"[cv/upload] multipart mode: file={filename}  size={len(file_data)} bytes")

                if not filename.lower().endswith(".pdf"):
                    self.send_json({"error": "Only PDF files are accepted."})
                    return
                user_upload_dir = os.path.join(UPLOADS_DIR, str(user_id))
                os.makedirs(user_upload_dir, exist_ok=True)
                cv_path = os.path.join(user_upload_dir, "cv.pdf")
                with open(cv_path, "wb") as f:
                    f.write(file_data)
                auth.update_profile(user_id, cv_path=cv_path, cv_analyzed=0)
                database.log_activity(user_id, "cv_uploaded", "Uploaded new CV PDF")
                print(f"[cv] ✅ Saved CV for user {user_id}: {cv_path}")
                self.send_json({"success": True, "path": cv_path})
                bump_onboarding(user_id, "cv_uploaded")
            except Exception as exc:
                import traceback
                print(f"[cv/upload] ❌ Exception: {exc}\n{traceback.format_exc()}")
                try:
                    self.send_json({"error": f"Server error: {exc}"}, code=500)
                except Exception:
                    pass
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
                database.log_activity(user_id, "cv_analyzed",
                    f"AI extracted {len(data.get('job_titles',[]))} job titles, "
                    f"{len(data.get('keywords',[]))} keywords")
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
            bump_onboarding(user_id, "search_configured")
            return

        # ── Save notifications ──
        if path == "/api/save-notifications":
            data = self.read_json()
            kwargs = {}
            for field in ("notification_channel", "telegram_token", "telegram_chat_id",
                          "twilio_account_sid", "twilio_auth_token", "whatsapp_number",
                          "email_address", "email_smtp_host", "email_smtp_port",
                          "email_smtp_user", "email_smtp_pass"):
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
            user    = self.require_auth()
            if not user:
                return
            user_id = user["id"]
            msg     = f"✅ Job Hunter test message — connection works!"
            try:
                if channel == "telegram":
                    send_telegram(data.get("telegram_token",""), data.get("telegram_chat_id",""), msg)
                    _log_notification(user_id, "telegram", "Test OK")
                elif channel == "whatsapp":
                    send_whatsapp(data.get("twilio_account_sid",""), data.get("twilio_auth_token",""),
                                  data.get("whatsapp_number",""), msg)
                    _log_notification(user_id, "whatsapp", "Test OK")
                elif channel == "email":
                    send_email(
                        to_addr=data.get("email_address",""),
                        subject="Job Hunter Test",
                        body=msg,
                    )
                    _log_notification(user_id, "email", "Test OK")
                self.send_json({"success": True})
            except Exception as e:
                _log_notification(user_id, channel, "Test FAILED", str(e))
                self.send_json({"success": False, "error": str(e)})
            return

        # ── Save schedule ──
        if path == "/api/save-schedule":
            data = self.read_json()
            kwargs = {}
            for int_field in ("search_hour", "apply_hour", "search_day_of_week",
                              "apply_day_of_week", "onboarding_complete", "weekdays_only", "auto_apply_enabled"):
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
            bump_onboarding(user_id, "auto_apply_choice_made")
            self.send_json({"success": True})
            return

        # ── Dismiss onboarding ──
        if path == "/api/dismiss-onboarding":
            auth.update_profile(user_id, onboarding_dismissed=1)
            self.send_json({"success": True})
            return

        elif self.path == '/api/cv-optimizer-analyze':
            if self.command == 'GET':
                with database.get_db() as _conn:
                    _prof = _conn.execute('SELECT cv_optimizer_result, cv_optimizer_date FROM user_profiles WHERE user_id=?', (user_id,)).fetchone()
                if _prof and _prof['cv_optimizer_result']:
                    _res = json.loads(_prof['cv_optimizer_result'])
                    _res['cached'] = True; _res['analyzed_date'] = _prof['cv_optimizer_date']
                    self.send_json(_res); return
                self.send_json({'cached': False}); return
            if self.command == 'POST':
                try:
                    from datetime import datetime as _dt, timedelta as _td
                    with database.get_db() as _conn:
                        _prof = _conn.execute('SELECT cv_path, cv_summary, cv_optimizer_result, cv_optimizer_date FROM user_profiles WHERE user_id=?', (user_id,)).fetchone()
                    if not _prof:
                        self.send_json({'error': 'Profile not found'}, 404); return
                    if _prof['cv_optimizer_result'] and _prof['cv_optimizer_date']:
                        try:
                            if _dt.now() - _dt.fromisoformat(_prof['cv_optimizer_date']) < _td(days=7):
                                _res = json.loads(_prof['cv_optimizer_result'])
                                _res['cached'] = True; _res['analyzed_date'] = _prof['cv_optimizer_date']
                                self.send_json(_res); return
                        except Exception:
                            pass
                    _cv_text = _extract_cv_text(_prof['cv_path'], _prof['cv_summary'])
                    if not _cv_text or len(_cv_text.strip()) < 30:
                        self.send_json({'error': 'No CV content found. Please upload your CV first.'}); return
                    _result = _call_gemini_cv_optimizer(_cv_text)
                    _result['cached'] = False
                    _now = _dt.now().isoformat()
                    _result['analyzed_date'] = _now
                    with database.get_db() as _conn:
                        _conn.execute('UPDATE user_profiles SET cv_optimizer_result=?, cv_optimizer_date=? WHERE user_id=?',
                                      (json.dumps(_result), _now, user_id))
                        _conn.commit()
                    self.send_json(_result)
                except Exception as _e:
                    self.send_json({'error': str(_e)}, 500)
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
        m = re.match(r"^/api/jobs/(\d+)/(approve|reject|later|applied|failed|retry|restore)$", path)
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

            status_map = {"approve":"approved","reject":"rejected","applied":"applied","failed":"approved","retry":"approved","restore":"new"}
            if action == "restore":
                conn.execute(
                    "UPDATE jobs SET status='new', apply_status=NULL, "
                    "apply_error=NULL, apply_confirmation=NULL, "
                    "apply_failure_type=NULL, apply_failure_detail=NULL, "
                    "apply_attempts=0, applied_date=NULL, notes='', url_verified=NULL "
                    "WHERE id=? AND user_id=?",
                    (job_id, user_id)
                )
                # Remove rejected pattern so it won't be filtered in future searches
                conn.execute(
                    "DELETE FROM rejected_patterns WHERE user_id=? AND LOWER(TRIM(company))=LOWER(TRIM(?)) AND LOWER(TRIM(title))=LOWER(TRIM(?))",
                    (user_id, job["company"] or "", job["title"] or "")
                )
                conn.commit()
                conn.close()
                database.log_activity(user_id, "job_restored",
                    f"Restored {job['title']} at {job['company']} to New")
                self.send_json({"success": True})
                return

            if action == "retry":
                conn.execute(
                    "UPDATE jobs SET status='approved', apply_status=NULL, "
                    "apply_error=NULL, apply_confirmation=NULL, "
                    "apply_failure_type=NULL, apply_failure_detail=NULL, "
                    "apply_attempts=0, applied_date=NULL, notes='' "
                    "WHERE id=? AND user_id=?",
                    (job_id, user_id)
                )
                conn.commit()
                conn.close()
                database.log_activity(user_id, "job_retry",
                    f"Retrying {job['title']} at {job['company']}")
                self.send_json({"success": True})
                return

            new_status = status_map[action]
            reason     = data.get("reason", "") or data.get("notes", "")

            if action in ("applied", "failed"):
                conn.execute("UPDATE jobs SET status=?, applied_date=?, notes=?, apply_status=? WHERE id=?",
                             (new_status, datetime.now().isoformat(), reason,
                              "submitted" if action == "applied" else "failed", job_id))
            else:
                conn.execute("UPDATE jobs SET status=?, notes=? WHERE id=?",
                             (new_status, reason, job_id))

            if action == "reject":
                conn.execute(
                    "INSERT INTO rejected_patterns (user_id,company,title,notes,created_date) VALUES (?,?,?,?,?)",
                    (user_id, job["company"], job["title"],
                     reason or "No reason given", datetime.now().isoformat())
                )
                detail = f"Passed on {job['title']} at {job['company']}"
                if reason:
                    detail += f" — {reason}"
                database.log_activity(user_id, "job_rejected", detail)
                if reason:
                    database.record_pass_reason_stat(conn, user_id, reason)
            elif action == "approve":
                database.log_activity(user_id, "job_approved",
                    f"Approved {job['title']} at {job['company']}")

            conn.commit()
            conn.close()
            database.write_approved_jobs(BASE_DIR)
            bump_onboarding(user_id, "first_job_reviewed")
            self.send_json({"success": True})
            return

        # ── Check if job is still open (calls Claude + fetches URL) ──────────
        m = re.match(r"^/api/jobs/(\d+)/check-status$", path)
        if m:
            user = self.require_auth()
            if not user:
                return
            job_id = int(m.group(1))
            conn = database.get_db()
            job = conn.execute(
                "SELECT * FROM jobs WHERE id=? AND user_id=?", (job_id, user["id"])
            ).fetchone()
            if not job:
                conn.close()
                self.send_json({"error": "Job not found"}, 404)
                return
            if not job["url"]:
                conn.close()
                self.send_json({"error": "No URL for this job"}, 400)
                return
            try:
                from ai_analysis import check_job_status
                result = check_job_status(
                    job["url"], job["title"], job["company"], ANTHROPIC_KEY
                )
                status_str = result.get("status_check", "unknown")
                conn.execute(
                    "UPDATE jobs SET status_check=?, status_checked_date=? WHERE id=?",
                    (status_str, datetime.now().isoformat(), job_id)
                )
                conn.commit()
                database.log_activity(user["id"], "job_status_checked",
                    f"{job['title']} at {job['company']} — {status_str}")
            except Exception as e:
                result = {"error": str(e), "status_check": "unknown", "reason": str(e)}
            conn.close()
            self.send_json(result)
            return

        # ── Cover Letter (admin only) ──
        m = re.match(r"^/api/jobs/(\d+)/cover-letter$", path)
        if m:
            job_id = int(m.group(1))
            # Admin check
            if not user.get("email") or user["email"].lower() != (ADMIN_EMAIL or "").lower():
                self.send_json({"error": "Admin only"}, 403)
                return
            data = self.read_json()
            conn = database.get_db()
            job = conn.execute(
                "SELECT * FROM jobs WHERE id=? AND user_id=?", (job_id, user_id)
            ).fetchone()
            if not job:
                conn.close()
                self.send_json({"error": "Not found"}, 404)
                return
            action = data.get("action", "generate")
            if action == "save":
                # Save edited cover letter
                conn.execute("UPDATE jobs SET cover_letter=? WHERE id=?", (data.get("letter", ""), job_id))
                conn.commit()
                conn.close()
                self.send_json({"success": True})
                return
            # Generate
            profile = conn.execute(
                "SELECT * FROM user_profiles WHERE user_id=?", (user_id,)
            ).fetchone()
            conn.close()
            from ai_analysis import generate_cover_letter
            letter = generate_cover_letter(dict(job), dict(profile) if profile else {}, ANTHROPIC_KEY)
            # Persist
            c2 = database.get_db()
            c2.execute("UPDATE jobs SET cover_letter=? WHERE id=?", (letter, job_id))
            c2.commit()
            c2.close()
            self.send_json({"success": True, "letter": letter})
            return

        # ── Update applied-job pipeline stage ───────────────────────────────────
        if path == "/api/set-stage":
            user = self.get_user()
            if not user:
                self.send_json({"error": "auth"}, 401); return
            data   = self.read_json()
            job_id = data.get("id")
            stage  = data.get("stage")
            if not job_id or stage not in ("screening","interviewing","offer","rejected"):
                self.send_json({"error": "invalid"}, 400); return
            conn = database.get_db()
            conn.execute(
                "UPDATE jobs SET apply_status=? WHERE id=? AND user_id=?",
                (stage, job_id, user["id"])
            )
            conn.commit(); conn.close()
            database.log_activity(user["id"], "stage_update",
                f"Stage updated to {stage} for job {job_id}")
            self.send_json({"ok": True}); return

        # ── Bulk job actions ──────────────────────────────────────────────────────
        if path == "/api/jobs/bulk":
            data   = self.read_json()
            action = data.get("action", "")
            ids    = [int(i) for i in data.get("ids", []) if str(i).isdigit()]
            if not ids or action not in ("approve", "reject"):
                self.send_json({"error": "Invalid"}, 400)
                return
            conn       = database.get_db()
            new_status = "approved" if action == "approve" else "rejected"
            done       = 0
            for job_id in ids:
                job = conn.execute(
                    "SELECT * FROM jobs WHERE id=? AND user_id=?", (job_id, user_id)
                ).fetchone()
                if not job:
                    continue
                conn.execute("UPDATE jobs SET status=? WHERE id=?", (new_status, job_id))
                if action == "reject":
                    conn.execute(
                        "INSERT INTO rejected_patterns (user_id,company,title,notes,created_date) VALUES (?,?,?,?,?)",
                        (user_id, job["company"], job["title"], "Bulk pass", datetime.now().isoformat())
                    )
                done += 1
            conn.commit()
            conn.close()
            label = "Approved" if action == "approve" else "Passed on"
            database.log_activity(user_id, f"bulk_{action}", f"{label} {done} job(s) at once")
            database.write_approved_jobs(BASE_DIR)
            self.send_json({"success": True, "updated": done})
            return

        # ── Admin: toggle user active state ───────────────────────────────────
        m = re.match(r"^/api/admin/users/(\d+)/toggle$", path)
        if m:
            if user.get("role") != "admin":
                self.send_json({"error": "Forbidden"}, 403)
                return
            target_id = int(m.group(1))
            conn = database.get_db()
            conn.execute("UPDATE users SET is_active = 1 - is_active WHERE id=?", (target_id,))
            conn.commit()
            conn.close()
            self.send_json({"success": True})
            return

        # ── Run Search Now ────────────────────────────────────────────────────────
        if path == "/api/run-search":
            if not user:
                self.send_json({"error": "Unauthorized"}, 401)
                return
            if not ANTHROPIC_KEY:
                self.send_json({"error": "Anthropic API key not configured"}, 400)
                return
            uid = user["id"]
            threading.Thread(target=run_job_search, args=(uid,), daemon=True).start()
            self.send_json({"status": "started"})
            return

        # ── Run Apply Now ─────────────────────────────────────────────────────────
        if path == "/api/run-apply":
            if not user:
                self.send_json({"error": "Unauthorized"}, 401)
                return
            result = run_job_apply(user["id"])
            self.send_json(result)
            return

        # ── Admin job inject — session-authenticated, admin only ────────────────
        if path == "/api/admin/inject-jobs":
            if not user or user.get("role") != "admin":
                self.send_json({"error": "Forbidden"}, 403)
                return
            payload = self.read_json()
            jobs = payload.get("jobs", [])
            conn = database.get_db()
            inserted = 0
            for j in jobs:
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO jobs "
                        "(user_id,title,company,location,url,description,why_relevant,source,"
                        "found_date,match_score,candidate_score,status) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,'new')",
                        (user["id"], j.get("job_title",""), j.get("company",""),
                         j.get("location",""), j.get("url",""), j.get("description",""),
                         j.get("fit_reason",""), j.get("source",""), j.get("found_date",""),
                         j.get("match_score",0), j.get("candidate_score",0)))
                    inserted += 1
                except Exception as e:
                    print(f"[inject] {e}")
            conn.commit()
            conn.close()
            database.log_activity(user["id"], "jobs_injected",
                                  f"{inserted} jobs added via admin inject")
            self.send_json({"inserted": inserted})
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
            if inserted > 0:
                for uid in set(j.get("user_id", 1) for j in jobs):
                    cnt = sum(1 for j in jobs if j.get("user_id", 1) == uid)
                    database.log_activity(uid, "jobs_searched", f"Relay synced {cnt} new job(s)")
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
            if updates:
                # Log activity per user — look up user_id for each updated job
                conn2 = database.get_db()
                uid_counts: dict = {}
                for u in updates:
                    row = conn2.execute("SELECT user_id FROM jobs WHERE id=?", (u["id"],)).fetchone()
                    if row:
                        uid_counts[row["user_id"]] = uid_counts.get(row["user_id"], 0) + 1
                conn2.close()
                for uid, cnt in uid_counts.items():
                    database.log_activity(uid, "job_applied", f"Auto-applied to {cnt} job(s)")
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

    # Migrate expired jobs to rejected (expired tab removed)
    try:
        _mconn = database.get_db()
        _mconn.execute("UPDATE jobs SET status='rejected', notes=COALESCE(notes,'') || ' [expired]' WHERE status='expired'")
        _mconn.execute("UPDATE jobs SET status='approved' WHERE status='applied' AND apply_status IN ('failed','manual_required')")
        _mconn.commit()
        _mconn.close()
    except Exception:
        pass

    # Process any waiting files
    database.import_pending_jobs(BASE_DIR)
    database.import_applied_updates(BASE_DIR)
    # Notifications delivered exclusively via relay.py → /api/sync/notify

    # Background watcher
    t = threading.Thread(target=file_watcher, daemon=True)
    t.start()

    ai_status = "✅ Configured" if ANTHROPIC_KEY else "⚠️  Not set — add to config.json"

    print(f"\n📂  Folder:        {BASE_DIR}")
    print(f"🗄️   Database:      jobs.db")
    print(f"🤖  Anthropic AI:  {ai_status}")
    print(f"\n🖥️   Desktop:       http://localhost:{PORT}")
    print(f"📱  Mobile:        {MOBILE_URL}   ← open on your phone")
    print(f"⌨️   Ctrl+C to stop\n")

    class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
        daemon_threads = True

    server = ThreadedHTTPServer(("0.0.0.0", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋  Stopped.")
