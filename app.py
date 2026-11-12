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

import analytics
import hmac
import html as html_lib
import json
import os
import re
import shutil
import socket
import threading
import time
import urllib.parse
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, urlparse

import functools

import auth
import crypto
import db as database
import textclean
import voice
import dbdriver
import entitlements
import accountdata
import errors
import schedlock
import paidpool
import gemini
import jobqueue
import links as _links_mod
import ratelimit
import worker
import storage
from ai_analysis import analyze_cv

# ── Compiled Tailwind CSS (gzipped + base64, 19KB → 4KB stored) ───────────────
import gzip as _gz, base64 as _b64

# ── Config ────────────────────────────────────────────────────────────────────

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))

# -- Structured logging. The shim that used to live here (route print() through
#    the logging module, so ~153 call sites get timestamps and levels without
#    being edited) moved to log.py, where it is shared with the other twelve
#    modules that were still printing to bare stdout - and where it is tested.
#    Every record now also carries the request id and user id of the request in
#    flight; see log.py for why that is the half of this item worth having. --
import log as _log
_LOG = _log.setup()
# Error reporting, before anything can throw. A no-op unless JH_SENTRY_DSN is
# set, so local runs and the suite are unaffected.
_LOG.info("[errors] %s", errors.init())
_builtin_print = print
print = _log.make_print("app")  # noqa: A001 - intentional module-level logging shim
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
GEMINI_KEY    = _cfg("GEMINI_API_KEY",     "gemini_api_key")
# Job search runs on Gemini only by default. Set SEARCH_USE_ANTHROPIC=1 to
# re-enable the Anthropic Claude fallback for search discovery/scoring.
SEARCH_USE_ANTHROPIC = _cfg("SEARCH_USE_ANTHROPIC", "search_use_anthropic", "0") == "1"
ADMIN_EMAIL   = _cfg("ADMIN_EMAIL",        "admin_email")

# Where a brand-new account is sent. The setup flow is a view inside the PWA now;
# the app decides whether to show it from the onboarding flags on /api/me.
ONBOARDING_ENTRY = "/app"

# Where a signed-in user lands: /app, the only UI since Step 9 (2026-11-05)
# deleted the legacy server-rendered pages and the LEGACY_UI switch that could
# bring them back. Kept as a function so the many call sites read the same.
FUNNEL_STEPS = ("signup", "verified", "cv", "first_search", "first_approve", "paywall")


def _funnel(user_id, step: str) -> bool:
    """Record the first time a user reaches a funnel step (Step 10: signup ->
    verified -> CV -> first search -> first approve -> paywall hit). Only the
    first occurrence counts; never fails the request that triggered it."""
    if not user_id or step not in FUNNEL_STEPS:
        return False
    try:
        c = database.get_db()
        try:
            _cur = c.execute("INSERT INTO funnel_events (user_id, step, created_at) VALUES (?,?,?) "
                             "ON CONFLICT (user_id, step) DO NOTHING",
                             (user_id, step, datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")))
            c.commit()
            first_time = (getattr(_cur, "rowcount", 0) or 0) > 0
        finally:
            c.close()
    except Exception as exc:
        print("[funnel] %s for user %s not recorded: %s" % (step, user_id, exc))
        return False
    if first_time and step in analytics.STEP_EVENTS:
        _track(user_id, analytics.STEP_EVENTS[step])
    return first_time


_ANALYTICS_SKIP: dict = {}


def _track(user_id, event: str, props: dict | None = None, **kw) -> None:
    """Send a product-analytics event for a user (analytics.py), except for the
    resettable test account. The admin's events carry internal=true so the
    dashboard can leave them out."""
    if not analytics.enabled() or not user_id:
        return
    try:
        if user_id not in _ANALYTICS_SKIP:
            c = database.get_db()
            try:
                r = c.execute("SELECT email, role FROM users WHERE id=?", (user_id,)).fetchone()
            finally:
                c.close()
            import testaccount
            _ANALYTICS_SKIP[user_id] = ("skip" if (not r or testaccount.is_test_address(r["email"]))
                                        else "internal" if str(r["role"] or "") == "admin" else "")
        mode = _ANALYTICS_SKIP[user_id]
        if mode == "skip":
            return
        p = dict(props or {})
        if mode == "internal":
            p["internal"] = True
        analytics.capture(user_id, event, p, **kw)
    except Exception as exc:
        print("[analytics] %s for user %s not sent: %s" % (event, user_id, exc))


def _start_first_search(user_id) -> bool:
    """Queue a user's FIRST search the moment setup ends (2026-11-11).

    Finishing onboarding used to save the profile and the schedule and queue
    nothing, so a new account met an empty deck until its scheduled hour. Only
    when there is something to search for (at least one title) and no search
    has ever run for this account - a replayed onboarding starts nothing.
    Never fails the request that triggered it."""
    try:
        c = database.get_db()
        try:
            prof = c.execute("SELECT job_titles FROM user_profiles WHERE user_id=?", (user_id,)).fetchone()
            ran = c.execute("SELECT 1 FROM job_runs WHERE user_id=? AND kind='search' LIMIT 1",
                            (user_id,)).fetchone()
        finally:
            c.close()
        raw = (prof["job_titles"] if prof else "") or ""
        try:
            titles = json.loads(raw) if raw.strip().startswith("[") else [t for t in raw.split(",") if t.strip()]
        except ValueError:
            titles = []
        if ran or not titles:
            return False
        return bool(jobqueue.enqueue(user_id, "search"))
    except jobqueue.DailyCapReached as cap:
        print("[onboarding] first search for user %s not queued: %s" % (user_id, cap))
    except Exception as exc:
        print("[onboarding] first search for user %s not queued: %s" % (user_id, exc))
    return False


def funnel_report(days: int = 30) -> dict:
    """Signups in the last `days` days, and how many of THOSE users reached
    each later step. A cohort, so a step can never exceed the one before it
    by counting older users. Accounts from before 2026-11-09 have no signup
    event and are not in it."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    c = database.get_db()
    try:
        rows = c.execute(
            "SELECT f.step, COUNT(*) AS n FROM funnel_events f "
            "WHERE f.user_id IN (SELECT user_id FROM funnel_events WHERE step='signup' AND created_at >= ?) "
            "GROUP BY f.step", (since,)).fetchall()
    finally:
        c.close()
    got = {r["step"]: r["n"] for r in rows}
    return {"days": days, "since": since,
            "steps": [{"step": s, "users": int(got.get(s, 0))} for s in FUNNEL_STEPS]}


def _register_page(error_html: str, attr: dict) -> str:
    """The sign-up form, carrying where the visitor came from (utm_*, ref, the
    landing page's Noa variant) as hidden fields and on the Google link."""
    page = REGISTER_HTML.replace("{error_block}", error_html)
    if attr:
        hidden = "".join('<input type="hidden" name="%s" value="%s"/>' % (k, html_lib.escape(v, quote=True))
                         for k, v in attr.items())
        page = page.replace('<form method="POST" action="/register">',
                            '<form method="POST" action="/register">' + hidden, 1)
        page = page.replace('href="/auth/google/start"',
                            'href="/auth/google/start?%s"' % html_lib.escape(analytics.encode_attribution(attr), quote=True), 1)
    return page


def landing_html(noa: str = "a") -> str:
    """The public page at `/` (landing.py). Canonical/OG URL only when
    JH_PUBLIC_URL is set, so a Railway preview never claims to be the site."""
    import landing
    # The activity line is the latest search's own count (every source's
    # "fetched"), so it is true whenever it is shown; none yet = no line.
    try:
        checked = sum(int((v or {}).get("fetched") or 0) for v in _source_health().values())
    except Exception as exc:
        print("[landing] activity line left out: %s" % exc)
        checked = 0
    return landing.landing_html(os.environ.get("JH_PUBLIC_URL", "").strip(), postings_checked=checked, noa=noa)


def _paywall_hit(_reason, user):
    # _funnel never raises; the entitlements caller logs anything else.
    _funnel((user or {}).get("id"), "paywall")


entitlements.ON_UPGRADE_REQUIRED.append(_paywall_hit)


def home_url() -> str:
    return "/app"


SYNC_API_KEY  = _cfg("SYNC_API_KEY",       "sync_api_key")   # shared secret for relay↔server calls
PORT          = int(_cfg("PORT", "port", "5001"))
VAPID_PUBLIC_KEY      = _cfg("VAPID_PUBLIC_KEY", "vapid_public_key")
VAPID_PRIVATE_PEM_B64 = _cfg("VAPID_PRIVATE_PEM_B64", "vapid_private_pem_b64")
# Web-push contact. No personal default in the code: VAPID_SUBJECT, else the
# admin's address, else the product's own support address.
VAPID_SUBJECT         = (_cfg("VAPID_SUBJECT", "vapid_subject")
                         or ("mailto:%s" % ADMIN_EMAIL if ADMIN_EMAIL else "mailto:support@job-hunter.work"))

# ── Google Sign-In (OAuth 2.0) ──
GOOGLE_CLIENT_ID     = _cfg("GOOGLE_CLIENT_ID",     "google_client_id")
GOOGLE_CLIENT_SECRET = _cfg("GOOGLE_CLIENT_SECRET", "google_client_secret")
GOOGLE_AUTH_URI      = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URI     = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URI  = "https://openidconnect.googleapis.com/v1/userinfo"

# -- Login brute-force rate limiting (in-memory; single-process threaded server) --
import time as _time_rl
import threading as _threading_rl
_LOGIN_FAILS = {}
_LOGIN_LOCK = _threading_rl.Lock()
_LOGIN_WINDOW = 900   # rolling 15-minute window
_LOGIN_MAX = 8        # max failed attempts per window before lockout

# Login's limiter moved into ratelimit.py unchanged (still 8 failures per 15
# minutes, still keyed on IP *and* email). These three names are kept so the
# call sites read the same; the behaviour is identical, the bookkeeping is now
# shared with register and run-search, and stale keys are swept rather than
# accumulating forever under IP rotation.
def _login_check(key):
    """Seconds to wait if this key is currently locked out, else 0."""
    return ratelimit.retry_after("login", key)

def _login_fail(key):
    ratelimit.record("login", key)

def _login_ok(key):
    ratelimit.clear("login", key)


def _effectively_onboarded(u):
    """A registered user who already has a CV or target titles should not be
    forced back through onboarding on every login/refresh."""
    if not u:
        return False
    if u.get("onboarding_complete"):
        return True
    if u.get("cv_path"):
        return True
    jt = u.get("job_titles")
    if not jt:
        return False
    try:
        parsed = json.loads(jt) if isinstance(jt, str) else jt
        return any(str(t).strip() for t in (parsed or []))
    except Exception:
        return bool(str(jt).strip() and str(jt).strip() not in ("[]", "{}"))

# Persistent paths — override with env vars on Railway (point to a mounted volume)
DB_FILE     = _cfg("DATABASE_PATH", "_db_path", os.path.join(BASE_DIR, "jobs.db"))
UPLOADS_DIR = _cfg("UPLOADS_DIR",   "_uploads",  os.path.join(BASE_DIR, "uploads"))
os.makedirs(UPLOADS_DIR, exist_ok=True)
# Phase 2d: the CV bytes live in the database; this directory is now a cache
# that any container can rebuild. See storage.py.
storage.set_uploads_dir(UPLOADS_DIR)

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
# JH_PUBLIC_URL (2026-11-09, job-hunter.work): the address the app gives out -
# notification and push links, the cross-origin header. Unset: the Railway one.
_public_url = os.environ.get('JH_PUBLIC_URL', '').strip().rstrip('/')
MOBILE_URL = (_public_url or (f"https://{_railway_domain}" if _railway_domain
              else f"http://{LOCAL_IP}:{PORT}"))
# Every address the app answers on. Google sign-in returns to the address it
# started from: its state cookie is per address, so sending someone who began
# on the Railway address back to the new one would fail the security check.
_SERVED_HOSTS = {h for h in (urllib.parse.urlparse(_public_url).netloc if _public_url else "",
                             _railway_domain) if h}


def _base_for(host: str) -> str:
    """https://<host> when the request came to one of our addresses, else MOBILE_URL."""
    h = (host or "").strip().lower()
    # *.up.railway.app too: with a custom domain added, RAILWAY_PUBLIC_DOMAIN no
    # longer named the Railway address - observed 2026-11-09, sign-in started on
    # web-production-192b7.up.railway.app was sent back to job-hunter.work and
    # would fail the state check. Google still only accepts registered URIs.
    ok = h in _SERVED_HOSTS or h.endswith(".up.railway.app")
    return f"https://{h}" if h and ok else MOBILE_URL.rstrip("/")

# ── DB + Auth init ────────────────────────────────────────────────────────────

database.set_db_path(DB_FILE)
# Phase 2b: Postgres is opt-in via DB_BACKEND=postgres; DATABASE_URL alone
# never switches engines (Railway injects reference vars freely).
database.set_database_url(os.environ.get("DATABASE_URL", ""))
auth.set_db_getter(database.get_db)
auth.set_admin_email(ADMIN_EMAIL)

# ── Notifications ─────────────────────────────────────────────────────────────

def send_telegram(token: str, chat_id: str, message: str):
    """Send a Telegram message; RAISES when Telegram does not accept it.

    It used to catch every error and print it, so deliver_notification and the
    Settings test logged "Sent OK" for a message Telegram refused (bad token,
    chat not started) - found 2026-11-08 while proving notifications live
    before the security-code flow depends on them."""
    if not (token and chat_id):
        raise RuntimeError("Telegram bot token or chat id missing")
    url  = f"https://api.telegram.org/bot{token}/sendMessage"
    clean = re.sub(r"<[^>]+>", "", message)
    data  = urllib.parse.urlencode({"chat_id": chat_id, "text": clean}).encode()
    req   = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:200]
        raise RuntimeError(f"Telegram refused the message: HTTP {e.code} {detail}")
    if not result.get("ok"):
        raise RuntimeError(f"Telegram refused the message: {str(result)[:200]}")
    print("[telegram] ✅ Sent")


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
                raise RuntimeError(f"Twilio did not accept the message: {str(result)[:200]}")
    except RuntimeError:
        raise
    except Exception as e:
        # Raised, not printed: the callers log the failure (it used to read "Sent OK").
        raise RuntimeError(f'WhatsApp send failed: {e}' + ((' | Twilio: ' + e.read().decode('utf-8', 'ignore')[:200]) if hasattr(e, 'read') else ''))


RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")


# The Resend account's own verified address (the one inbox its sandbox sender
# can deliver to). Defaults to ADMIN_EMAIL - no personal address in the code.
RESEND_VERIFIED_EMAIL = os.environ.get("RESEND_VERIFIED_EMAIL", "") or (ADMIN_EMAIL or "")


# Resend's shared sandbox sender. It can only deliver to the Resend account's
# own verified address, which is why the override below ever existed.
RESEND_SANDBOX_FROM = "onboarding@resend.dev"
RESEND_FROM = os.environ.get("RESEND_FROM", "Job Hunter <%s>" % RESEND_SANDBOX_FROM)
# Job-search mail comes from Noa (the owner, 2026-11-12): "Noa from Job Hunter
# <noa@job-hunter.work>", replies forwarded to him by a Cloudflare Email Routing
# rule. Unset = everything stays on RESEND_FROM, so no reply can bounce before
# that rule exists. Account and security mail always stays on RESEND_FROM.
NOA_FROM = os.environ.get("NOA_FROM", "").strip()
# Where "Contact support" messages go (2026-11-09). Defaults to the Resend
# account's own address, which the sandbox sender can always deliver to - so the
# form works before the domain is verified in Resend.
SUPPORT_EMAIL = os.environ.get("JH_SUPPORT_EMAIL", "") or RESEND_VERIFIED_EMAIL
SUPPORT_MAX_CHARS = 5000


def send_support_message(user: dict, message: str, subject: str = "", user_agent: str = "") -> None:
    """Email one "Contact support" message to SUPPORT_EMAIL, Reply-To the user,
    so answering is a plain Reply. Raises on failure - the route must not say
    "sent" when it was not."""
    import entitlements
    email = (user.get("email") or "").strip()
    subj = (subject or "").strip().replace("\n", " ")[:120] or "Message from the app"
    body = (
        "%s\n\n"
        "---\n"
        "From: %s <%s>\n"
        "Account id: %s\n"
        "Plan: %s\n"
        "App commit: %s\n"
        "Device: %s\n"
        "Reply to this email to answer them.\n"
    ) % (message.strip(), user.get("name") or "", email, user.get("id"),
         entitlements.plan_info(user)["label"],
         os.environ.get("RAILWAY_GIT_COMMIT_SHA", "")[:7] or "local", (user_agent or "")[:200])
    send_email(SUPPORT_EMAIL, "[Job Hunter support] %s" % subj, body, reply_to=email)


# The day-3 note from the owner (lifecycle.py): "Eran from Job Hunter
# <eran@job-hunter.work>", replies forwarded to his Gmail. Unset = that email
# is skipped, never sent from someone else's address.
ERAN_FROM = os.environ.get("ERAN_FROM", "").strip()


def _sender(as_noa=False, who: str = "") -> tuple:
    """(from, reply_to) for a message. Noa (or Eran) only when NOA_FROM (or
    ERAN_FROM) is set and the regular sender is on a verified domain (not
    Resend's sandbox)."""
    who = who or ("noa" if as_noa else "")
    addr = {"noa": NOA_FROM, "eran": ERAN_FROM}.get(who, "")
    if addr and RESEND_SANDBOX_FROM not in RESEND_FROM:
        m = re.search(r"<([^>]+)>", addr)
        return addr, (m.group(1) if m else addr)
    return RESEND_FROM, ""


def send_email(to_addr: str, subject: str, body: str, reply_to: str = "", as_noa: bool = False,
               sender: str = "", headers: dict | None = None, **_kwargs):
    """Send an email notification via the Resend SDK.

    This function used to set `actual_to = RESEND_VERIFIED_EMAIL` and DISCARD
    the address it was given. Every user's email notification went to the admin.
    Proven 2026-09-20 by driving deliver_notification for a user whose address is
    dana@somewhere-else.test: Resend was handed to=['admin@example.com'], and
    both the caller and the notification log recorded "Sent OK".

    It was a leftover from the unverified-domain sandbox, where only the account's
    own address can receive mail - true, and the wrong response to it. With nine
    users who know each other it was an annoyance; with a public signup it is one
    tenant's job alerts in another tenant's inbox, and the log says delivered.

    So: while the sender is still the shared sandbox address, refuse anything
    addressed elsewhere instead of redirecting it. The callers already catch and
    log a failure, so a refusal shows up as "FAILED" where a redirect showed up as
    "Sent OK" - loud instead of wrong. Set RESEND_FROM to an address on a domain
    verified in Resend and this sends to the real recipient with no code change.
    """
    if not RESEND_API_KEY:
        raise RuntimeError("RESEND_API_KEY not configured")
    # The resettable test account (testaccount.py) lives on the reserved .test
    # domain (@job-hunter.test): mail to it can only bounce, and bounces cost the
    # sending domain.
    if str(to_addr or "").strip().lower().endswith("@job-hunter.test"):
        raise RuntimeError("not sent: %s is a test address" % to_addr)
    if not to_addr:
        raise RuntimeError("no recipient address")
    try:
        import resend
    except ImportError:
        raise RuntimeError("resend package not installed")
    if RESEND_SANDBOX_FROM in RESEND_FROM and \
            to_addr.strip().lower() != (RESEND_VERIFIED_EMAIL or "").strip().lower():
        raise RuntimeError(
            "email is still on Resend's shared sandbox sender (%s), which can only "
            "deliver to %s. Refusing to send %s's notification to someone else. "
            "Verify a domain in Resend and set RESEND_FROM."
            % (RESEND_SANDBOX_FROM, RESEND_VERIFIED_EMAIL or "<unset>", to_addr))
    actual_to = to_addr
    resend.api_key = RESEND_API_KEY
    _from, _noa_reply = _sender(as_noa, sender)
    reply_to = reply_to or _noa_reply
    msg = {
        "from": _from,
        "to": [actual_to],
        "subject": subject,
        "text": body,
    }
    if reply_to:
        msg["reply_to"] = reply_to
    if headers:
        msg["headers"] = dict(headers)
    result = resend.Emails.send(msg)
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


_APPLY_OUTCOME = {"confirmed": "sent", "submitted": "sent", "needs_answers": "held", "needs_code": "held",
                  "awaiting_code": "held", "manual_required": "manual", "failed": "failed"}


def _apply_rates(conn, user_id=None) -> dict:
    """Step 8.3: what the engine achieved per application system. Counts jobs
    the engine attempted (apply_attempts > 0) by their latest outcome:
    sent (confirmed/submitted), held for the user (questions / security code),
    manual (the engine cannot submit there by design) and failed.
    success = sent / (sent + failed): held and manual are not engine failures."""
    sql = ("SELECT url, apply_resolved_url, apply_status FROM jobs WHERE COALESCE(apply_attempts,0) > 0"
           + (" AND user_id=?" if user_id else ""))
    import apply_engine as _ae
    by = {}
    for r in conn.execute(sql, (user_id,) if user_id else ()).fetchall():
        sys_ = _ae.ats_name(r["apply_resolved_url"] or r["url"] or "")
        row = by.setdefault(sys_, {"system": sys_, "attempted": 0, "sent": 0, "held": 0, "manual": 0,
                                   "failed": 0, "other": 0})
        row["attempted"] += 1
        row[_APPLY_OUTCOME.get((r["apply_status"] or "").strip(), "other")] += 1
    rows = sorted(by.values(), key=lambda x: -x["attempted"])
    for row in rows:
        d = row["sent"] + row["failed"]
        row["success_rate"] = round(100.0 * row["sent"] / d) if d else None
        row["small_sample"] = d < 5
    return {"systems": rows, "engine_on": _engine_on(),
            "note": "success = sent / (sent + failed); held (your answers or a security code) and "
                    "manual (job boards, Workday accounts) are not counted as failures."}


def _flag(name: str) -> bool:
    return os.environ.get(name, "0").strip().lower() in ("1", "true", "yes", "on")


def _engine_on() -> bool:
    """The server-wide switch only. Whether it sends for a given user is
    _engine_on_for."""
    return _flag("APPLY_ENGINE_ENABLED")


def _engine_on_for(user) -> bool:
    """APPLY_ENGINE_ENABLED is on AND this user may use it (The owner, 2026-11-09:
    "for now I want it to be applicable only for me"). Admins only until
    APPLY_ENGINE_FOR_ALL is also set - so switching the engine on for testing
    can never send applications for other users who turned auto-apply on.
    user: a user dict or a user id."""
    if not _engine_on():
        return False
    uid = user.get("id") if isinstance(user, dict) else user
    conn = database.get_db()
    try:
        r = conn.execute("SELECT role, email_verified_at FROM users WHERE id=?", (uid,)).fetchone()
    finally:
        conn.close()
    if not r:
        return False
    # Applications go out under the account's email: it must be confirmed
    # first (Step 5, 2026-11-09). Google sign-ins and pre-Step-5 accounts are.
    if not r["email_verified_at"]:
        return False
    if _flag("APPLY_ENGINE_FOR_ALL"):
        return True
    return r["role"] == "admin"


def _tailor_inputs(user_id, user):
    """(cv_text, name, contact lines) for a tailored CV: the CV itself, and a
    header from the account and profile - never from the model."""
    conn = database.get_db()
    try:
        prof = conn.execute("SELECT cv_summary, cv_path, phone, linkedin_url FROM user_profiles WHERE user_id=?",
                            (user_id,)).fetchone()
    finally:
        conn.close()
    cv_text = _extract_cv_text(_cv_file(user_id, (prof["cv_path"] or "") if prof else ""),
                               (prof["cv_summary"] or "") if prof else "")
    contact = [user.get("email") or "", (prof["phone"] or "").strip() if prof else "",
               (prof["linkedin_url"] or "").strip() if prof else ""]
    return cv_text or "", (user.get("name") or "").strip(), contact


def _tailored_cv_row(user_id, job_id):
    conn = database.get_db()
    try:
        r = conn.execute("SELECT content, created_at, edited, use_for_apply FROM tailored_cvs "
                         "WHERE user_id=? AND job_id=?", (user_id, job_id)).fetchone()
    finally:
        conn.close()
    if not r:
        return None
    out = json.loads(r["content"])
    out["created_at"] = r["created_at"]
    out["edited"] = bool(r["edited"])
    out["use"] = "tailored" if r["use_for_apply"] else "original"
    return out


def _tailored_cv_checked(user_id, job_id, user):
    """The stored tailored CV, with every CV role guaranteed (tailor.restore_roles)
    - also for drafts saved before that check existed (The owner still saw no Northwind Media
    on one, 2026-11-08). A draft the user edited is left as they made it.
    A repaired draft is saved back. No model call."""
    got = _tailored_cv_row(user_id, job_id)
    if not got or got.get("edited"):
        return got
    try:
        import tailor as _tl
        cv_text, _n, _c = _tailor_inputs(user_id, user)
        roles = _tl.cv_roles(cv_text)
        if roles and _tl.missing_roles(got["cv"], roles):
            got.setdefault("flags", [])
            _tl.restore_roles(got, roles)
            c = database.get_db()
            try:
                c.execute("UPDATE tailored_cvs SET content=? WHERE user_id=? AND job_id=?",
                          (json.dumps({"cv": got["cv"], "removed": got.get("removed", []),
                                       "flags": got["flags"]}), user_id, job_id))
                c.commit()
            finally:
                c.close()
            print(f"[tailor] user {user_id} job {job_id}: restored roles missing from a saved draft")
    except Exception as exc:
        print(f"[tailor] WARNING role check on a saved draft failed: {exc}")
    return got


def _clean_cv(cv) -> dict:
    """The user's edited CV, shape-checked and size-capped. Not fact-checked:
    the user is the author of their own edits."""
    if not isinstance(cv, dict):
        raise ValueError("cv must be an object")
    s = lambda v, n: str(v or "").strip()[:n]
    lst = lambda v, n, k: [s(x, n) for x in (v or []) if s(x, n)][:k] if isinstance(v, list) else []
    roles = []
    for r in (cv.get("experience") or [])[:30]:
        if isinstance(r, dict):
            roles.append({"title": s(r.get("title"), 200), "company": s(r.get("company"), 200),
                          "dates": s(r.get("dates"), 80), "bullets": lst(r.get("bullets"), 600, 20)})
    return {"headline": s(cv.get("headline"), 300), "summary": s(cv.get("summary"), 2500),
            "skills": lst(cv.get("skills"), 120, 60), "experience": roles,
            "education": lst(cv.get("education"), 400, 15), "other": lst(cv.get("other"), 400, 20),
            "changes": lst(cv.get("changes"), 300, 6)}


def _cv_for_apply(user_id, job_id, default_path):
    """The CV file to send for this job: the tailored one, as a Word file,
    only if the user chose it for this job; otherwise the original."""
    if job_id is None:
        return default_path
    try:
        c0 = database.get_db()
        try:
            u0 = c0.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        finally:
            c0.close()
        got = _tailored_cv_checked(user_id, job_id, dict(u0) if u0 else {})
        if not got or got.get("use") != "tailored":
            return default_path
        import tailor as _tl
        import tempfile as _tf
        c = database.get_db()
        try:
            u = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        finally:
            c.close()
        _cvt, name, contact = _tailor_inputs(user_id, dict(u) if u else {})
        d = _tf.mkdtemp(prefix="jh_tcv_")
        path = os.path.join(d, re.sub(r"[^A-Za-z0-9]+", "_", (name or "CV")).strip("_") + "_CV.docx")
        with open(path, "wb") as f:
            f.write(_tl.to_docx(got["cv"], name=name, contact=contact))
        print(f"[apply] user {user_id} job {job_id}: sending the tailored CV")
        return path
    except Exception as exc:
        # Never send nothing: fall back to the original, and say so in the log.
        print(f"[apply] WARNING tailored CV for job {job_id} unavailable ({exc}); sending the original")
        return default_path


def _notify_status(user_id) -> dict:
    """What can reach this user: server config, their devices and channels,
    and the last notification outcomes logged for them."""
    st = {"push": push_configured(), "email_sender_is_sandbox": RESEND_SANDBOX_FROM in RESEND_FROM,
          "email_key": bool(RESEND_API_KEY), "devices": [], "channels": [], "recent": []}
    conn = database.get_db()
    try:
        for r in conn.execute("SELECT endpoint, created_date FROM push_subscriptions WHERE user_id=?",
                              (user_id,)).fetchall():
            host = urllib.parse.urlparse(r["endpoint"] or "").netloc
            st["devices"].append({"service": {"fcm.googleapis.com": "Chrome / Android",
                                              "web.push.apple.com": "Safari / iPhone",
                                              "updates.push.services.mozilla.com": "Firefox"}.get(host, host),
                                  "since": r["created_date"]})
        p = conn.execute("SELECT notification_channel FROM user_profiles WHERE user_id=?", (user_id,)).fetchone()
        st["channels"] = [c.strip() for c in ((p["notification_channel"] if p else "") or "none").split(",")
                          if c.strip() and c.strip() != "none"]
        st["recent"] = [{"at": r["created_date"], "detail": r["details"]} for r in conn.execute(
            "SELECT details, created_date FROM activity_log WHERE user_id=? AND event_type='notification' "
            "ORDER BY id DESC LIMIT 10", (user_id,)).fetchall()]
    finally:
        conn.close()
    return st


def deliver_notification(user_id: int, message: str, url_suffix: str = "", push_url=None) -> dict:
    """Deliver to every channel the user has; return {channel: "sent" | "FAILED: ..." | ...}.
    Each outcome is also logged (activity_log, type notification)."""
    message = repair_mojibake(message)
    results = {}
    conn = database.get_db()
    try:
        p = conn.execute(
            "SELECT * FROM user_profiles WHERE user_id=?", (user_id,)
        ).fetchone()
    finally:
        conn.close()
    # Web push is the primary channel: it fires on EVERY notification, before
    # (and independent of) the email/Telegram/WhatsApp channels.
    try:
        pr = send_web_push_to_user(user_id, message, url_suffix, url=push_url, title="Noa")   # job news is Noa's (2026-11-12)
        if pr["sent"]:
            results["push"] = "sent to %d device(s)" % pr["sent"] + (
                ", %d failed" % pr["failed"] if pr["failed"] else "")
            _log_notification(user_id, "push", "Sent OK", results["push"])
        elif pr["devices"] or not pr["configured"]:
            results["push"] = "FAILED: " + "; ".join(pr["errors"])[:200]
            _log_notification(user_id, "push", "FAILED", "; ".join(pr["errors"]))
        else:
            results["push"] = "no device has notifications on"
    except Exception as _pe:
        results["push"] = f"FAILED: {_pe}"
        print(f"[push] deliver error: {_pe}")
    if not p:
        print(f"[notify] No profile found for user {user_id}")
        return results
    p = crypto.decrypt_row(p)
    channels = [ch.strip() for ch in (p["notification_channel"] or "none").split(",") if ch.strip()]
    if not channels or channels == ["none"]:
        return results
    dashboard_url = f"{MOBILE_URL}{url_suffix}"
    msg_with_link = message + f"\n\n\U0001F4F1 Dashboard: {dashboard_url}"
    for channel in channels:
        try:
            if channel == "telegram":
                if not (p["telegram_token"] and p["telegram_chat_id"]):
                    raise RuntimeError("Telegram is selected but the bot token or chat id is missing")
                send_telegram(p["telegram_token"], p["telegram_chat_id"], msg_with_link)
            elif channel == "whatsapp":
                if not (p["twilio_account_sid"] and p["whatsapp_number"]):
                    raise RuntimeError("WhatsApp is selected but the Twilio details are missing")
                send_whatsapp(p["twilio_account_sid"], p["twilio_auth_token"],
                              p["whatsapp_number"], msg_with_link)
            elif channel == "email":
                if not p["email_address"]:
                    raise RuntimeError("Email is selected but no address is set")
                send_email(to_addr=p["email_address"], subject="Job Hunter Notification", body=msg_with_link,
                           as_noa=True)
            else:
                continue
            results[channel] = "sent"
            _log_notification(user_id, channel, "Sent OK")
            print(f"[notify] {channel} sent to user {user_id}")
        except Exception as _notif_err:
            results[channel] = f"FAILED: {_notif_err}"[:240]
            _log_notification(user_id, channel, "FAILED", str(_notif_err))
            print(f"[notify] Error on {channel}: {_notif_err}")
    return results


def _push_payload_url():
    return MOBILE_URL.rstrip("/") + "/app"


def _push_link(url_suffix=""):
    base = MOBILE_URL.rstrip("/") + "/app"
    if "applied" in (url_suffix or ""):
        return base + "?view=applied"
    return base


def push_configured() -> dict:
    """Whether web push can send at all: keys set and pywebpush importable."""
    try:
        import pywebpush  # noqa: F401
        lib = True
    except Exception as exc:
        print(f"[push] pywebpush unavailable: {exc}")
        lib = False
    return {"keys": bool(VAPID_PUBLIC_KEY and VAPID_PRIVATE_PEM_B64), "library": lib}


def send_web_push_to_user(user_id, message, url_suffix="", url=None, title="Job Hunter") -> dict:
    """Web Push to a user's registered devices. Returns what happened -
    {configured, devices, sent, failed, errors} - instead of nothing: it used
    to return None on missing keys, a missing library and every failed send
    alike, so nobody could tell whether push worked (2026-11-08). Never raises."""
    out = {"configured": False, "devices": 0, "sent": 0, "failed": 0, "errors": []}
    if not (VAPID_PUBLIC_KEY and VAPID_PRIVATE_PEM_B64):
        out["errors"].append("VAPID keys not configured on the server")
        return out
    try:
        from pywebpush import webpush  # noqa
    except Exception as _imp:
        out["errors"].append(f"pywebpush unavailable: {_imp}")
        return out
    import base64 as _b64, os as _os, tempfile as _tf
    try:
        pem = _b64.b64decode(VAPID_PRIVATE_PEM_B64).decode("utf-8")
        key_path = _os.path.join(_tf.gettempdir(), "jh_vapid.pem")
        with open(key_path, "w") as _kf:
            _kf.write(pem)
    except Exception as _pe:
        out["errors"].append(f"bad VAPID private key: {_pe}")
        return out
    out["configured"] = True
    conn = database.get_db()
    try:
        rows = conn.execute(
            "SELECT id, subscription FROM push_subscriptions WHERE user_id=?", (user_id,)
        ).fetchall()
    finally:
        conn.close()
    out["devices"] = len(rows)
    if not rows:
        return out
    body = (message or "").strip().split("\n")[0][:160]
    payload = json.dumps({"title": title, "body": body, "url": url or _push_link(url_suffix)})
    for r in rows:
        try:
            sub = json.loads(r["subscription"])
            webpush(subscription_info=sub, data=payload,
                    vapid_private_key=key_path, vapid_claims={"sub": VAPID_SUBJECT})
            out["sent"] += 1
        except Exception as _se:
            emsg = str(_se)
            out["failed"] += 1
            out["errors"].append(emsg[:200])
            if "410" in emsg or "404" in emsg:
                # The browser dropped this subscription; it re-registers on the next app open.
                try:
                    c2 = database.get_db()
                    try:
                        c2.execute("DELETE FROM push_subscriptions WHERE id=?", (r["id"],))
                        c2.commit()
                    finally:
                        c2.close()
                except Exception as _de:
                    print(f"[push] could not drop an expired subscription: {_de}")
            print(f"[push] send failed: {emsg}")
    return out


def admin_alert_target():
    """(admin user id, how it was found) for the new-user alert, or (None, why not).
    ADMIN_EMAIL first; else the oldest account with the admin role. Until
    2026-11-11 ADMIN_EMAIL was never set on production, and the alert returned
    early on every signup without a trace - so it no longer depends on it alone,
    and /api/health reports the result (smoke fails when it is None)."""
    conn = database.get_db()
    try:
        if ADMIN_EMAIL:
            row = conn.execute("SELECT id FROM users WHERE lower(email)=lower(?)",
                               (ADMIN_EMAIL,)).fetchone()
            if row:
                return row["id"], "ADMIN_EMAIL"
        row = conn.execute("SELECT id FROM users WHERE role='admin' AND is_active=1 "
                           "ORDER BY id LIMIT 1").fetchone()
        if row:
            return row["id"], "admin role" + ("" if ADMIN_EMAIL else " (ADMIN_EMAIL unset)")
        return None, "no admin account" + ("" if ADMIN_EMAIL else " and ADMIN_EMAIL unset")
    finally:
        conn.close()


def _admin_alerts_health():
    try:
        admin_id, how = admin_alert_target()
        return {"target": admin_id is not None, "via": how}
    except Exception as e:
        return {"target": False, "via": "error: %s" % e}


def notify_admin_new_user(new_user_email: str, new_user_name: str, provider: str = ""):
    """Send a new-signup alert to the admin via ALL their configured channels.
    Called from auth's new-account hook, so every signup path reaches it."""
    admin_id, how = admin_alert_target()
    if admin_id is None:
        print(f"[admin-notify] ERROR no one to alert about new user {new_user_email}: {how}")
        return
    display = new_user_name or new_user_email
    via = {"google": " with Google", "password": " with email and password"}.get(provider, "")
    message = f"\U0001F680 New User Alert: {display} ({new_user_email}) has joined Job-Hunter{via}."
    try:
        res = deliver_notification(admin_id, message, url_suffix=home_url())
        print(f"[admin-notify] new user {new_user_email}: {res}")
    except Exception as e:
        print(f"[admin-notify] ERROR delivery failed for new user {new_user_email}: {e}")


# The route harness (tests/test_routes.py `stack`) stubs the public name to keep
# the suite offline; tests of the alert itself call this one.
_notify_admin_new_user_impl = notify_admin_new_user


@auth.on_user_created
def _alert_admin_on_signup(user_id, email, name, provider):
    # The resettable test account is not a signup: the owner asked to hear
    # about every REAL user (2026-11-12), and each reset creates it again.
    import testaccount
    if testaccount.is_test_address(email):
        return
    # Looked up at call time, so a test (or a later reassignment) can replace it.
    globals()["notify_admin_new_user"](new_user_email=email, new_user_name=name, provider=provider)


_LLM_PRUNED_ON = [""]


def _llm_ledger_write(day, user_id, purpose, model, calls, ptok, otok, ttok, ok, error):
    """One row per Gemini call. Called by gemini.py, which never imports this."""
    conn = database.get_db()
    try:
        conn.execute(
            "INSERT INTO llm_usage (day, user_id, purpose, model, calls, prompt_tokens, "
            "output_tokens, total_tokens, ok, error) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (day, user_id, purpose, model, calls, ptok, otok, ttok, ok, error))
        conn.commit()
    finally:
        conn.close()


def _llm_usage_today(day):
    """Re-seed the in-process counters from the ledger.

    This is what stops a restart from handing the current day a fresh budget —
    which matters most in the one case the ceiling exists for, since a runaway
    loop is also the thing most likely to have restarted the process.
    """
    conn = database.get_db()
    try:
        if _LLM_PRUNED_ON[0] != day:
            _LLM_PRUNED_ON[0] = day
            try:
                keep = int(os.environ.get("JH_LLM_RETENTION_DAYS", "90"))
                if keep > 0:
                    cutoff = (datetime.utcnow() - timedelta(days=keep)).strftime("%Y-%m-%d")
                    conn.execute("DELETE FROM llm_usage WHERE day < ?", (cutoff,))
                    conn.commit()
            except Exception as _pe:
                print(f"[llm-ledger] prune skipped: {_pe}")
        g = conn.execute(
            "SELECT COALESCE(SUM(calls),0), COALESCE(SUM(total_tokens),0) "
            "FROM llm_usage WHERE day=?", (day,)).fetchone()
        rows = conn.execute(
            "SELECT user_id, COALESCE(SUM(calls),0), COALESCE(SUM(total_tokens),0) "
            "FROM llm_usage WHERE day=? AND user_id IS NOT NULL GROUP BY user_id",
            (day,)).fetchall()
        return {
            "global": {"calls": g[0] or 0, "tokens": g[1] or 0},
            "users": {r[0]: {"calls": r[1] or 0, "tokens": r[2] or 0} for r in rows},
        }
    finally:
        conn.close()


def _llm_usage_history(days: int = 14):
    """Per-day ledger totals, newest first. Read-only; feeds the ceiling call.

    Column note: the ledger stores prompt_tokens/output_tokens (m0007), and
    total_tokens is already the sum, so this reads total_tokens rather than
    re-adding the two halves.
    """
    conn = database.get_db()
    try:
        rows = conn.execute(
            "SELECT day, COALESCE(SUM(calls),0), COALESCE(SUM(total_tokens),0), "
            "COUNT(DISTINCT user_id) FROM llm_usage GROUP BY day "
            "ORDER BY day DESC LIMIT ?", (int(days),)).fetchall()
        # The busiest single account per day. JH_LLM_USER_CALLS has to be set
        # from THIS, not from the global total divided by users - one heavy
        # account on an onboarding day is exactly what that average hides.
        peak = {r[0]: int(r[1] or 0) for r in conn.execute(
            "SELECT day, MAX(c) FROM (SELECT day, user_id, SUM(calls) AS c "
            "FROM llm_usage WHERE user_id IS NOT NULL GROUP BY day, user_id) per_user "
            "GROUP BY day").fetchall()}
        return [{"day": r[0], "calls": int(r[1] or 0),
                 "tokens": int(r[2] or 0), "users": int(r[3] or 0),
                 "max_user_calls": peak.get(r[0], 0)}
                for r in rows]
    finally:
        conn.close()


def _llm_breach_alert(message: str):
    """Tell the admin a Gemini ceiling was crossed — on every channel they have.

    Logged to activity_log as well as pushed, because a notification that fails
    to deliver must still leave a record that the ceiling was hit.
    """
    admin_id = None
    try:
        conn = database.get_db()
        try:
            row = conn.execute("SELECT id FROM users WHERE lower(email)=lower(?)",
                               (ADMIN_EMAIL or "",)).fetchone()
            conn.close()
            admin_id = row["id"] if row else None
        finally:
            conn.close()
    except Exception as e:
        print(f"[llm-alert] admin lookup failed: {e}")
    try:
        database.log_activity(admin_id or 0, "llm_budget_breach", message[:500])
    except Exception as e:
        print(f"[llm-alert] log failed: {e}")
    if admin_id:
        try:
            deliver_notification(admin_id, message, url_suffix=home_url())
        except Exception as e:
            print(f"[llm-alert] notify failed (non-fatal): {e}")


gemini.set_ledger_writer(_llm_ledger_write)
gemini.set_sync_source(_llm_usage_today)
gemini.set_alert_sender(_llm_breach_alert)
gemini.set_history_source(_llm_usage_history)


def db_check():
    """A round trip, not the fact that a handle exists.

    get_db() can hand back a pooled connection to a server that has since gone
    away; that reads as perfectly healthy right up until the first real query,
    which is the moment a user finds it instead of the health check.
    """
    import time as _dbt
    t0 = _dbt.time()
    try:
        c = database.get_db()
        try:
            c.execute("SELECT 1").fetchone()
        finally:
            c.close()
        return {"ok": True, "ms": int((_dbt.time() - t0) * 1000)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:160]}


def worker_health():
    """Age of the oldest claimed job. See worker.health()."""
    try:
        return worker.health()
    except Exception as e:
        return {"unavailable": str(e)[:120]}


def bump_onboarding(user_id: int, key: str):
    """Set a single onboarding milestone to true (idempotent)."""
    try:
        conn = database.get_db()
        try:
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
        finally:
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


def _gemini_generate(body_bytes: bytes, timeout: int = 90, retries: int = 4,
                     purpose: str = "search_scoring", user_id=None) -> str:
    """POST to the Gemini REST API and return the raw JSON text.

    The retry/backoff that used to live here moved into gemini.generate(), which
    is now the single door every Gemini call in this app goes through — so the
    seven call sites that never had backoff have it too. This shim stays only
    because three callers already speak "bytes in, JSON text out"; it no longer
    takes a URL, because building the URL is what let a call site slip past the
    ceiling."""
    return json.dumps(gemini.generate(body_bytes, purpose=purpose, user_id=user_id,
                                      timeout=timeout, retries=retries))


def _israel_day_start_utc(today: str) -> str:
    """Israel's midnight for `today` (YYYY-MM-DD, Israel date) as the UTC text
    activity_log stores. Comparing the Israel date straight against UTC
    timestamps missed anything logged between 00:00 and 02:00/03:00 Israel."""
    import datetime as _dt
    try:
        from zoneinfo import ZoneInfo
        start = _dt.datetime.strptime(today, "%Y-%m-%d").replace(tzinfo=ZoneInfo("Asia/Jerusalem"))
        return start.astimezone(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    except Exception as exc:
        print(f"[scheduler] WARNING day start for {today}: {exc}")
        return today


def _scheduler_already_ran(user_id: int, event_type, today: str) -> bool:
    """Check activity_log to see if this scheduled job already fired today
    (Israel day). event_type may be a tuple of types. DB-backed so it survives
    server restarts (unlike an in-memory dict)."""
    types = (event_type,) if isinstance(event_type, str) else tuple(event_type)
    try:
        conn = database.get_db()
        try:
            row = conn.execute(
                "SELECT 1 FROM activity_log "
                "WHERE user_id=? AND event_type IN (%s) AND created_date >= ? LIMIT 1"
                % ",".join("?" for _ in types),
                (user_id, *types, _israel_day_start_utc(today))
            ).fetchone()
            conn.close()
            return row is not None
        finally:
            conn.close()
    except Exception:
        return False


def _plan_search_schedule(freq, s_dow, plan_row):
    """(frequency, search weekday) after the plan's cadence. The plan decides
    (Free: weekly), whatever the profile says - a downgraded user keeps 'daily'
    in their profile. A weekly plan with no chosen day searches on Sunday
    (weekday 6), the start of the Israeli week."""
    if entitlements.limit(plan_row, "search_cadence") == "weekly":
        return "weekly", (6 if s_dow is None else s_dow)
    return freq, s_dow


def israel_now(now_utc=None):
    """Israel wall-clock time, DST-aware. The scheduler used a fixed GMT+3, which
    is only right in summer: from the last Sunday of October Israel is GMT+2, so
    every scheduled search and apply ran an hour early all winter. Falls back to
    the old fixed offset only if the box has no tz database."""
    import datetime as _dt
    now_utc = now_utc or _dt.datetime.now(_dt.timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        return now_utc.astimezone(ZoneInfo("Asia/Jerusalem"))
    except Exception as _tz_exc:
        print(f"[scheduler] WARNING no tz database ({_tz_exc}); using fixed GMT+3")
        return now_utc.astimezone(_dt.timezone(_dt.timedelta(hours=3)))


def scheduler_enabled() -> bool:
    """JH-7 (2026-11-05): JH_SCHEDULER_ENABLED=0 turns the clock off on this box.
    Staging runs on a seeded copy of production's users, so its scheduler ran
    every user's daily search and spent Gemini at production's rate (~120 calls,
    ~1.5M tokens a day) on data nobody reads. Default on: production unchanged."""
    return os.environ.get("JH_SCHEDULER_ENABLED", "1").strip().lower() not in ("0", "false", "off", "no")


def _check_scheduled_jobs() -> None:
    """Auto-trigger search/apply for each active user when their scheduled hour arrives."""
    if not scheduler_enabled():
        return
    # Only one instance may decide that a scheduled run is due. The queue's
    # one-run-per-user rule stops a duplicate SEARCH, but the scheduler is what
    # decides a run happens at all - two instances both seeing the same minute
    # would double every user's Gemini spend before the queue got a say.
    if not schedlock.acquire(database.get_db, database.backend()):
        return
    try:
        now = israel_now()
        today = now.strftime('%Y-%m-%d')
        current_hour = now.hour
        conn = database.get_db()
        try:
            rows = conn.execute(
                "SELECT u.id, p.search_hour, p.apply_hour, "
                "p.schedule_frequency, p.search_day_of_week, p.apply_day_of_week, p.weekdays_only, p.auto_apply_enabled, "
                "u.role, u.plan, u.plan_until, u.plan_after "
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
                freq, s_dow = _plan_search_schedule(
                    freq, s_dow, {"role": row[8], "plan": row[9], "plan_until": row[10], "plan_after": row[11]})
                # Skip weekends if weekdays_only
                if wo and cur_dow >= 5:
                    continue
                # Search: check hour + frequency/day
                if current_hour == sh and not _scheduler_already_ran(uid, 'jobs_searched', today):
                    run_search = True
                    if freq == 'weekly' and s_dow is not None and cur_dow != s_dow:
                        run_search = False
                    if run_search:
                        # Enqueued, not spawned: the queue's one-run-per-user rule
                        # is what stops a second instance double-firing the same
                        # user's daily search, and an unfinished run survives a
                        # redeploy instead of vanishing with the process.
                        try:
                            if jobqueue.enqueue(uid, "search"):
                                print(f'[scheduler] Queued search for user {uid} at hour {sh}')
                            else:
                                print(f'[scheduler] Search already in flight for user {uid}; skipped')
                        except jobqueue.DailyCapReached as _cap:
                            print(f'[scheduler] {_cap} (user {uid}); skipped')
                # Apply: check hour + frequency/day
                # 'apply_scheduled' is written when the scheduler fires, whatever
                # the run then does. It used to look for 'job_applied' alone,
                # which only a real submission writes - so with the engine off, an
                # empty queue or the cap, it queued a new apply run every minute of
                # the apply hour and used up the 20-a-day allowance (The owner,
                # 2026-11-08: "limit reached (20 out of 20)").
                if current_hour == ah and not _scheduler_already_ran(uid, ('apply_scheduled', 'job_applied'), today):
                    run_apply = True
                    if freq == 'weekly' and a_dow is not None and cur_dow != a_dow:
                        run_apply = False
                    if not auto_apply:
                        run_apply = False
                    if run_apply:
                        # Routed through the queue too, so un-parking auto-apply is
                        # a kill-switch change rather than a rewrite. The engine
                        # itself stays off: apply_engine no-ops without
                        # APPLY_ENGINE_ENABLED.
                        try:
                            if jobqueue.enqueue(uid, "apply"):
                                print(f'[scheduler] Queued apply for user {uid} at hour {ah}')
                            else:
                                print(f'[scheduler] Apply already in flight for user {uid}; skipped')
                        except jobqueue.DailyCapReached as _cap:
                            print(f'[scheduler] {_cap} (user {uid}); skipped')
                        database.log_activity(uid, "apply_scheduled", f"Scheduled auto-apply started ({ah:02d}:00 Israel time)")
        finally:
            conn.close()
    except Exception as e:
        print(f'[scheduler] Error: {e}')


_POOL_THREAD = None


def _start_pool_run():
    """Once a day (Israel time), each paid source fills the shared pool
    (paidpool.py). Its own thread: an Apify run can take minutes and must not
    hold up scheduled searches. Only the scheduler-lock holder runs it."""
    global _POOL_THREAD
    if _POOL_THREAD is not None and _POOL_THREAD.is_alive():
        return
    try:
        if not schedlock.acquire(database.get_db, database.backend()):
            return
    except Exception as e:
        print(f"[pool] WARNING scheduler lock check failed: {e}")
        return

    def _run():
        try:
            paidpool.run_daily(database.get_db)
        except Exception as e:
            print(f"[pool] ERROR daily run failed: {e}")
    _POOL_THREAD = threading.Thread(target=_run, name="paid-pool", daemon=True)
    _POOL_THREAD.start()


def _stop_page(title: str, text: str, form: str = "") -> str:
    """The small page behind "Stop these emails" (lifecycle.py). Self-contained styles."""
    return ("<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' "
            "content='width=device-width,initial-scale=1'><title>%s</title><style>"
            "body{margin:0;background:#0c1f17;color:#fbf6e6;font:16px/1.5 system-ui,sans-serif;"
            "display:flex;min-height:100vh;align-items:center;justify-content:center;padding:24px}"
            ".c{max-width:28rem;background:#12291f;border:1px solid #2c4a3c;border-radius:16px;padding:28px}"
            "h1{font-size:1.3rem;margin:0 0 .6rem}p{color:#c4d4c6;margin:0 0 1.2rem}"
            "button{font:inherit;font-weight:650;border:0;border-radius:12px;padding:.7rem 1.2rem;"
            "background:#ffd766;color:#1f1a05;cursor:pointer}a{color:#ffd766}</style></head>"
            "<body><div class='c'><h1>%s</h1><p>%s</p>%s</div></body></html>" % (title, title, text, form))


def _lifecycle_send(to, subject, body, sender, unsub):
    send_email(to, subject, body, sender=sender,
               headers={"List-Unsubscribe": "<%s>" % unsub,
                        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"})


def _run_lifecycle_emails(now=None) -> dict | None:
    """Welcome / day 1 / day 3 / day 7 (lifecycle.py). Scheduler-lock holder
    only, so a second instance never sends twice. Never raises."""
    import lifecycle
    import testaccount
    if not lifecycle.enabled():
        return None
    try:
        if not schedlock.acquire(database.get_db, database.backend()):
            return None
        senders = {"noa": _sender(who="noa")[0] != RESEND_FROM, "eran": _sender(who="eran")[0] != RESEND_FROM}
        out = lifecycle.run(database.get_db, _lifecycle_send, MOBILE_URL, track=_track, now=now,
                            is_test=testaccount.is_test_address, senders=senders)
        if out["sent"] or out["failed"]:
            print("[lifecycle] sent %(sent)d, skipped %(skipped)d, failed %(failed)d" % out)
        return out
    except Exception as exc:
        print("[lifecycle] ERROR sweep failed: %s" % exc)
        return None


def file_watcher():
    _tick = 0
    while True:
        database.import_pending_jobs(BASE_DIR)
        database.import_applied_updates(BASE_DIR)
        _check_scheduled_jobs()
        _start_pool_run()
        _tick += 1
        if _tick % 60 == 1:  # ~hourly, first one a minute after boot
            # Plan lifecycle: promotion-end pushes, then expired promotions moved
            # to their fallback plan. Only the scheduler-lock holder runs it, so
            # a second instance never sends a second push.
            try:
                if schedlock.acquire(database.get_db, database.backend()):
                    _pl = entitlements.run_lifecycle(database.get_db, send_web_push_to_user)
                    if _pl["notices"] or _pl["moved"]:
                        print(f"[plans] notices sent: {_pl['notices']}; promotions ended: {_pl['moved']}")
                    # Accounts whose 14-day deletion grace is over (Step 6).
                    _pc = database.get_db()
                    try:
                        _gone = accountdata.purge_due(_pc)
                    finally:
                        _pc.close()
                    for _gid in _gone:
                        try:
                            storage.delete(_gid)
                        except Exception as _sde:
                            print(f"[account] WARNING CV cache for deleted user {_gid}: {_sde}")
                    if _gone:
                        print(f"[account] deleted {len(_gone)} account(s) after their grace period: {_gone}")
                        database.write_users_config(BASE_DIR)
            except Exception as _ple:
                print(f"[plans] ERROR plan lifecycle sweep failed: {_ple}")
        if _tick % 10 == 2:  # every ~10 minutes: lifecycle emails (off unless LIFECYCLE_EMAILS=1)
            _run_lifecycle_emails()
        if _tick % 60 == 0:  # ~hourly
            try:
                _purged = auth.cleanup_expired_sessions()
                if _purged:
                    print(f"[sessions] purged {_purged} expired session(s)")
            except Exception as _se:
                print(f"[sessions] cleanup error: {_se}")
        # This thread never ends, so a connection one of the calls above
        # leaked would otherwise be lost to the pool for the life of the
        # process. schedlock's lock connection is detached and survives this.
        dbdriver.release_thread_connections("scheduler tick")
        time.sleep(60)


_search_running: set = set()


def _attribute_gemini_to(fn):
    """Bind this thread's Gemini calls to the user the run belongs to.

    A decorator rather than a `with` block inside each function: run_job_search
    and run_job_apply are long, and re-indenting them to add attribution would
    bury the change in whitespace. See gemini.bind_user for the thread-local's
    limits — most relevantly, work these functions hand to a NEW thread records
    against no user rather than the wrong one.
    """
    @functools.wraps(fn)
    def _wrapped(user_id, *a, **kw):
        with gemini.bind_user(user_id):
            return fn(user_id, *a, **kw)
    return _wrapped



class _SourceTally:
    """Per-run health for a legacy search-loop source (LinkedIn, Indeed,
    Sparkhire, SpeakNow). Every request either answers or is counted as a
    failure with its reason, and the result lands in /api/health `sources` the
    way the ingestion adapters' do - so a source that has died shows up there
    instead of printing "0 jobs matched" forever (Step 7: Comeet and Indeed had
    done exactly that)."""

    def __init__(self, name):
        self.name, self.calls, self.failed, self.fetched, self.kept = name, 0, [], 0, 0

    def fail(self, exc):
        self.calls += 1
        why = getattr(exc, "code", None)
        self.failed.append("HTTP %s" % why if why else type(exc).__name__)

    def ok(self, fetched):
        self.calls += 1
        self.fetched += fetched

    def done(self):
        if not self.calls:
            return
        if not self.failed:
            status, msg = "ok", ""
        else:
            status = "error" if len(self.failed) == self.calls else "degraded"
            msg = "%d of %d requests failed: %s" % (len(self.failed), self.calls, ", ".join(sorted(set(self.failed)))[:150])
            print(f"[search] WARNING {self.name}: {msg}")
        try:
            from ingestion.integration import _remember
            _remember(self.name, status, self.fetched, self.kept, msg)
        except Exception as e:
            print(f"[search] WARNING could not record {self.name} health: {e}")


def _json_array_from_text(text):
    """The first JSON array in a model's reply, or [] if there is none.

    Sentry 2026-11-08 ("Web search error ... Expecting value: line 1 column 2"):
    callers sliced from text.rfind('[') to text.rfind(']'), so any bracket after
    the array's start - "[Hybrid]" in the last description, or a trailing
    "see [source]" note - made the slice "[Hybrid]" and the whole reply was
    thrown away. This decodes from each '[' in order and keeps the first that
    is a real JSON array."""
    dec = json.JSONDecoder()
    i = text.find("[")
    while i >= 0:
        try:
            val, _ = dec.raw_decode(text, i)
            if isinstance(val, list):
                return val
        except ValueError:
            pass
        i = text.find("[", i + 1)
    return []


_LI_RETRY_WAITS = (2, 5)   # seconds; LinkedIn's guest search throttles at random


def _linkedin_fetch(url, timeout=12, waits=_LI_RETRY_WAITS, _sleep=time.sleep):
    """GET a LinkedIn guest-search page, retrying HTTP 429.

    Sentry 2026-11-08 ("linkedin: 1 of 5 requests failed: HTTP 429"). Replayed
    from the workspace: 8 of 15 requests got a 429 on the first try with no
    Retry-After, spacing them 2 s apart did not help, and every one answered
    200 after one retry at 2 s or a second at 5 s (8 of 8)."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
    for attempt in range(len(waits) + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == len(waits):
                raise
            _sleep(waits[attempt])


def _plan_ai_cap(user_id):
    """gemini.py asks this for a user's daily AI-call cap from their plan."""
    conn = database.get_db()
    try:
        row = conn.execute("SELECT role, plan, plan_until, plan_after FROM users WHERE id=?",
                           (user_id,)).fetchone()
    finally:
        conn.close()
    return entitlements.limit(dict(row), "ai_calls_per_day") if row else None


gemini.set_plan_cap_resolver(_plan_ai_cap)


def _source_health():
    try:
        import ingestion as _ing
        return _ing.source_health()
    except Exception as exc:  # never take health down for a statistic
        return {"error": ("%s: %s" % (type(exc).__name__, exc))[:160]}


def _paid_pool_health():
    """/api/health -> paid_pool: per paid source, key present, month used vs
    cap, free allowance, pool size, last run (paidpool.status). No secrets."""
    try:
        _c = database.get_db()
        try:
            return paidpool.status(_c)
        finally:
            _c.close()
    except Exception as exc:  # never take health down for a statistic
        return {"error": ("%s: %s" % (type(exc).__name__, exc))[:160]}

@_attribute_gemini_to
def run_job_search(user_id: int):
    """Search for new jobs via multi-round Anthropic web-search (one call per job title)."""
    if user_id in _search_running:
        return
    _search_running.add(user_id)
    try:
        conn = database.get_db()
        try:
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
            # NOTE: no hardcoded title default. If the user has no target titles,
            # leave the list empty so the title filter does not restrict to a single
            # role (previously this forced everyone into "Senior Product Manager").
            # Collection runs unfiltered and the CV-aware AI scoring decides fit.
            if not titles:
                print(f"[run-search] user {user_id} has no target titles — running without title filter (CV-driven scoring)")
            today = datetime.now().strftime("%Y-%m-%d")

            # ── Load all existing URLs to dedup against full history ─────────
            conn = database.get_db()
            try:
                # Dedup against EVERY job URL we've ever stored for this user (any status,
                # any age). This guarantees a job the user already swiped — approved,
                # passed/rejected, deferred, applied, or expired — is never re-added as
                # "new" by a later search.
                existing_urls = {r[0] for r in conn.execute(
                    "SELECT url FROM jobs WHERE user_id=? AND url!=''", (user_id,)
                ).fetchall()}
                # A job whose link was resolved to the company's posting keeps
                # the board URL it was found at in source_url (Step 1b); that
                # URL is still "already stored" for dedup purposes.
                existing_urls |= {r[0] for r in conn.execute(
                    "SELECT source_url FROM jobs WHERE user_id=? AND source_url IS NOT NULL AND source_url!=''",
                    (user_id,)).fetchall()}
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
                        'snyk': 'Snyk', 'sentinelone': 'SentinelOne',
                        'fiverr': 'Fiverr', 'tipalti': 'Tipalti',
                        'checkmarx': 'Checkmarx', 'rapyd': 'Rapyd', 'lemonade': 'Lemonade',
                        'papayaglobal': 'Papaya Global', 'deel': 'Deel', 'drata': 'Drata',
                        'hibob': 'HiBob', 'ironclad': 'Ironclad', 'nextinsurance': 'Next Insurance',
                        'playtika': 'Playtika', 'gett': 'Gett', 'outbrain': 'Outbrain',
                        'guardicore': 'Guardicore', 'earnix': 'Earnix', 'pentera': 'Pentera',
                        'drivenets': 'DriveNets', 'orcasecurity': 'Orca Security',
                        'aquasecurity': 'Aqua Security', 'seekingalpha': 'Seeking Alpha',
                        'fundbox': 'Fundbox', 'ironsource': 'ironSource',
                        'torq': 'Torq', 'augury': 'Augury',
                        # Global tech companies with Israeli offices (Greenhouse)
                        'zscaler': 'Zscaler', 'sisense': 'Sisense',
                        'gongio': 'Gong', 'armissecurity': 'Armis',
                        'safebreach': 'SafeBreach', 'datarails': 'DataRails',
                        'couchbaseinc': 'Couchbase', 'dremio': 'Dremio',
                        'tenableinc': 'Tenable', 'solarwinds': 'SolarWinds',
                        'recordedfuture': 'Recorded Future', 'rubrik': 'Rubrik',
                        'elastic': 'Elastic', 'mongodb': 'MongoDB',
                        'datadog': 'Datadog', 'cloudflare': 'Cloudflare',
                        'commvault': 'Commvault',
                        # ── Extended Israeli tech list (previously missing) ──────────────
                        'walkme': 'WalkMe', 'monday': 'monday.com', 'wix': 'Wix',
                        'imperva': 'Imperva', 'perion': 'Perion', 'amdocs': 'Amdocs',
                        'allot': 'Allot', 'cellebrite': 'Cellebrite', 'varonis': 'Varonis',
                        'cyvera': 'Cyvera', 'cyberark': 'CyberArk', 'checkpoint': 'Check Point',
                        'radware': 'Radware', 'gilat': 'Gilat', 'elbit': 'Elbit Systems',
                        'verint': 'Verint', 'atera': 'Atera', 'salto': 'Salto',
                        'gloat': 'Gloat', 'dynamic-yield': 'Dynamic Yield',
                        'overwolf': 'Overwolf', 'buildots': 'Buildots',
                        'elementor': 'Elementor', 'syte': 'Syte', 'trigo': 'Trigo',
                        'apolicy': 'Apolicy', 'cyolo': 'Cyolo', 'sealights': 'SeaLights',
                        'regotechnology': 'Rego', 'memphis': 'Memphis.dev',
                        'finout': 'Finout', 'cloudinary': 'Cloudinary',
                        'zerto': 'Zerto', 'protai': 'Protai', 'otterly': 'Otterly',
                        'glassbox': 'Glassbox', 'lusha': 'Lusha', 'demostack': 'Demostack',
                        'reef': 'Reef', 'wilco': 'Wilco', 'guesty': 'Guesty',
                        'healthy-io': 'Healthy.io', 'panorays': 'Panorays',
                        'silverfort': 'Silverfort', 'hunters': 'Hunters',
                        'sygnia': 'Sygnia', 'cymulate': 'Cymulate',
                        'morphisec': 'Morphisec', 'veriti': 'Veriti',
                        'also': 'ALSO', 'granulate': 'Granulate',
                        'coralogix': 'Coralogix', 'logz': 'Logz.io',
                        'anodot': 'Anodot', 'spot': 'Spot by NetApp',
                        'env0': 'env0', 'cyclops': 'Cyclops',
                        'orca': 'Orca Security', 'laminar': 'Laminar',
                        'normalyze': 'Normalyze', 'dig-security': 'Dig Security',
                    }

                    # ── Role-aware TechMap category selection ─────────────────────────
                    # Pick which TechMap job-category CSV(s) to pull based on the user's
                    # target titles + keywords. Previously hardcoded to 'product.csv',
                    # which forced product roles on every user regardless of their CV.
                    _TM_ALL_CATEGORIES = {
                        'admin', 'business', 'data-science', 'design', 'devops', 'finance',
                        'frontend', 'hardware', 'hr', 'legal', 'marketing',
                        'procurement-operations', 'product', 'project-management', 'qa',
                        'sales', 'security', 'software', 'support',
                    }

                    def _pick_techmap_categories(titles_list, keywords_list):
                        _txt = ' '.join((titles_list or []) + (keywords_list or [])).lower()
                        _cats: set = set()
                        _rules = [
                            (('product owner', 'product manager', 'product lead', 'head of product', 'cpo', 'product'), ['product', 'project-management']),
                            (('project manager', 'program manager', 'scrum', 'delivery manager', 'pmo'), ['project-management']),
                            (('frontend', 'front-end', 'front end', 'react', 'angular', 'vue'), ['frontend']),
                            (('devops', 'sre', 'platform engineer', 'infrastructure', 'site reliability'), ['devops']),
                            (('software', 'developer', 'engineer', 'backend', 'full stack', 'fullstack', 'sde', 'programmer'), ['software', 'frontend', 'devops']),
                            (('data scientist', 'data analyst', 'machine learning', 'analytics', 'data engineer', 'ml engineer'), ['data-science']),
                            (('designer', 'ux', 'ui designer', 'product design'), ['design']),
                            (('marketing', 'growth', 'seo', 'content', 'brand', 'demand gen'), ['marketing']),
                            (('sales', 'account executive', 'sdr', 'bdr', 'account manager', 'business development'), ['sales', 'business']),
                            (('security', 'infosec', 'soc analyst', 'ciso', 'appsec'), ['security']),
                            (('finance', 'accountant', 'controller', 'fp&a', 'cfo'), ['finance']),
                            (('recruiter', 'people', 'talent', 'human resources'), ['hr']),
                            (('legal', 'counsel', 'compliance'), ['legal']),
                            (('qa', 'quality assurance', 'test engineer', 'automation engineer'), ['qa']),
                            (('support', 'customer success', 'csm', 'customer experience'), ['support']),
                            (('operations', 'procurement', 'supply chain'), ['procurement-operations', 'business']),
                            (('hardware', 'firmware', 'electrical', 'embedded'), ['hardware']),
                            (('business', 'strategy', 'bizdev', 'general manager', 'chief of staff'), ['business']),
                        ]
                        for _kw_tuple, _cat_list in _rules:
                            if any(_k in _txt for _k in _kw_tuple):
                                _cats.update(_cat_list)
                        _cats &= _TM_ALL_CATEGORIES
                        if not _cats:
                            # No recognizable role family — pull a broad default set so the
                            # candidate pool isn't empty (CV-aware scoring filters later).
                            _cats = {'product', 'software', 'business', 'data-science', 'project-management'}
                        return sorted(_cats)[:4]  # cap to limit fetch latency

                    _tm_categories = _pick_techmap_categories(titles_, kws_)
                    print(f"[search] TechMap categories for this user: {_tm_categories}")

                    # -- Israeli company slugs (Lever) --
                    _LV_COMPANIES = {
                        'walkme': 'WalkMe', 'cloudinary': 'Cloudinary',
                        # Global tech with Israeli offices (Lever)
                        'kaltura': 'Kaltura', 'namogoo': 'Namogoo', 'guesty': 'Guesty',
                        'skai': 'Skai', 'nexthink': 'Nexthink', 'bringg': 'Bringg',
                    }

                    # ── Dynamic Greenhouse discovery from TechMap CSV(s) ──────────────
                    # Fetch TechMap now (before ATS threads launch) to discover additional
                    # Greenhouse board slugs from job URLs. This keeps coverage self-updating.
                    try:
                        import csv as _csv_pre, io as _io_pre, re as _re_gh
                        _tm_disc_rows = []
                        for _cat in _tm_categories:
                            try:
                                _tm_disc_url  = f'https://raw.githubusercontent.com/mluggy/techmap/main/jobs/{_cat}.csv'
                                _tm_disc_req  = _ur2.Request(_tm_disc_url, headers={"User-Agent": "JobHunter/1.0"})
                                with _ur2.urlopen(_tm_disc_req, timeout=15) as _tm_disc_resp:
                                    _tm_disc_text = _tm_disc_resp.read().decode('utf-8', errors='replace')
                                _tm_disc_rows.extend(list(_csv_pre.DictReader(_io_pre.StringIO(_tm_disc_text))))
                            except Exception as _cat_err:
                                print(f"[search] TechMap category '{_cat}' fetch error: {_cat_err}")
                        _gh_slug_re = _re_gh.compile(r'boards\.greenhouse\.io/([a-zA-Z0-9_-]+)/')
                        _lv_slug_re = _re_gh.compile(r'jobs\.lever\.co/([a-zA-Z0-9_-]+)/')
                        _new_gh = 0
                        _new_lv = 0
                        for _dr in _tm_disc_rows:
                            _durl = (_dr.get('url') or '').strip()
                            _dco  = (_dr.get('company') or '').strip()
                            _gm = _gh_slug_re.search(_durl)
                            if _gm:
                                _slug = _gm.group(1).lower()
                                if _slug not in _GH_COMPANIES:
                                    _GH_COMPANIES[_slug] = _dco or _slug
                                    _new_gh += 1
                            _lm = _lv_slug_re.search(_durl)
                            if _lm:
                                _slug = _lm.group(1).lower()
                                if _slug not in _LV_COMPANIES:
                                    _LV_COMPANIES[_slug] = _dco or _slug
                                    _new_lv += 1
                        print(f"[search] TechMap discovery: +{_new_gh} Greenhouse, +{_new_lv} Lever boards "
                              f"(total: {len(_GH_COMPANIES)} GH, {len(_LV_COMPANIES)} LV)")
                        # Cache the rows so the later TechMap block can reuse them
                        _tm_prefetched_rows = _tm_disc_rows
                    except Exception as _tm_disc_err:
                        print(f"[search] TechMap discovery error (non-fatal): {_tm_disc_err}")
                        _tm_prefetched_rows = None
                    # -- SmartRecruiters public boards --
                    _SR_COMPANIES = {
                        'servicenow': 'ServiceNow',
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

                    # ── Synonym map (pass 3) ──────────────────────────────────────────
                    # Maps canonical form → list of synonyms (bidirectional at match time)
                    _SYNONYM_MAP = {
                        "product manager":          ["pm", "product owner", "po", "product lead"],
                        "senior product manager":   ["senior pm", "sr pm", "sr. pm", "spm"],
                        "group product manager":    ["group pm", "gpm"],
                        "principal product manager":["principal pm", "staff pm"],
                        "vp product":               ["vp of product", "chief product officer", "cpo",
                                                     "head of product", "gm product", "gm of product"],
                        "vp r&d":                   ["head of r&d", "r&d director", "director of r&d",
                                                     "vp engineering", "vp of engineering"],
                        "director of product":      ["product director", "head of product", "gm product"],
                        "head of product":          ["product lead", "director of product", "vp product",
                                                     "product group lead"],
                        "chief product officer":    ["cpo", "vp product", "head of product"],
                        "general manager":          ["gm", "business unit manager", "bu manager",
                                                     "country manager"],
                        "product lead":             ["lead pm", "product manager lead", "head of product"],
                        "growth":                   ["growth lead", "head of growth", "vp growth",
                                                     "growth manager", "growth product manager"],
                    }
                    # Build synonym phrase set from user titles
                    _synonym_phrases = set()
                    for _p in _phrases:
                        # Forward lookup
                        for _key, _syns in _SYNONYM_MAP.items():
                            if _key in _p or _p in _key:
                                _synonym_phrases.update(_syns)
                                _synonym_phrases.add(_key)
                        if _p in _SYNONYM_MAP:
                            _synonym_phrases.update(_SYNONYM_MAP[_p])
                        # Reverse lookup
                        for _key, _syns in _SYNONYM_MAP.items():
                            if _p in _syns:
                                _synonym_phrases.add(_key)
                                _synonym_phrases.update(_syns)
                    _synonym_phrases -= set(_phrases)  # don't duplicate what's already in _phrases

                    print(f"[search] Phrases: {len(_phrases)} | bigrams: {len(_bigrams)} | synonyms: {len(_synonym_phrases)}")

                    def _title_match(title):
                        import difflib as _dl
                        import re as _re_tm
                        # No target titles configured → don't filter by title at all.
                        # Let every collected job through and rely on CV-aware AI scoring.
                        if not _phrases:
                            return True
                        tl = _re_tm.sub(r"[,/&|]", " ", title.lower()).strip()

                        # Pass 1 — exact phrase match (original)
                        if any(phrase in tl for phrase in _phrases):
                            return True

                        # Pass 2 — bigram match (original)
                        for w1, w2 in _bigrams:
                            if w1 in tl and w2 in tl:
                                return True

                        # Pass 3 — synonym match
                        if any(syn in tl for syn in _synonym_phrases):
                            return True

                        # Pass 4 — fuzzy match via difflib (catches abbreviations & minor variants)
                        # Compare each user phrase against a sliding window in the job title
                        for _phrase in _phrases:
                            if len(_phrase) < 4:
                                continue  # skip very short phrases to avoid false positives
                            _ph_len = len(_phrase)
                            # Try the whole title first (works for short titles)
                            if _dl.SequenceMatcher(None, _phrase, tl).ratio() >= 0.80:
                                return True
                            # Sliding window for longer titles
                            for _start in range(0, max(1, len(tl) - _ph_len + 6), 3):
                                _window = tl[_start: _start + _ph_len + 6]
                                if _dl.SequenceMatcher(None, _phrase, _window).ratio() >= 0.82:
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
                            loc = j.get("location", {}).get("name", "") if isinstance(j.get("location"), dict) else ""
                            jurl = f"https://boards.greenhouse.io/{slug}/jobs/{j.get('id', '')}"
                            # Escaped HTML: the old tag regex ran before decoding and matched nothing (2026-11-12).
                            _gh_content = textclean.html_to_text(j.get("content", ""))[:5000] if j.get("content") else ""
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
                    # -- Query SmartRecruiters boards --
                    def _query_sr(slug, company_name):
                        data = _get_json(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100")
                        if not data: return
                        for j in data.get("content", []):
                            t = j.get("name", "")
                            if not t: continue
                            loc_obj = j.get("location") or {}
                            city = loc_obj.get("city", "")
                            country = loc_obj.get("country", "")
                            loc = f"{city}, {country}".strip(", ") if city or country else ""
                            jurl = f"https://jobs.smartrecruiters.com/{slug}/{j.get('id', '')}"
                            with _lk:
                                all_raw.append({"job_title": t, "company": company_name,
                                                "location": loc, "url": jurl,
                                                "description": t, "full_description": t,
                                                "source": "smartrecruiters"})
                    # -- Run all API queries in parallel --
                    print(f"[search] Querying {len(_GH_COMPANIES)} Greenhouse + {len(_LV_COMPANIES)} Lever + {len(_SR_COMPANIES)} SmartRecruiters boards...")
                    threads = []
                    for slug, name in _GH_COMPANIES.items():
                        threads.append(_thr.Thread(target=_query_gh, args=(slug, name), daemon=True))
                    for slug, name in _LV_COMPANIES.items():
                        threads.append(_thr.Thread(target=_query_lv, args=(slug, name), daemon=True))
                    for slug, name in _SR_COMPANIES.items():
                        threads.append(_thr.Thread(target=_query_sr, args=(slug, name), daemon=True))

                    # Launch in batches of 30
                    for i in range(0, len(threads), 30):
                        batch = threads[i:i+30]
                        for t in batch: t.start()
                        for t in batch: t.join(timeout=25)

                    # -- Step 16: board-first search (2026-11-08). Every job board the
                    # app knows - behind stored direct links (any user) and found by
                    # the careers-map - filtered by title and Israel BEFORE scoring,
                    # so these jobs arrive with a direct link. --
                    try:
                        import boardsearch as _bs
                        _known_static = {u for u in (j.get("url") for j in all_raw) if u}
                        _bjobs, _bstats = _bs.search(database.get_db, _title_match)
                        _bjobs = [j for j in _bjobs if j["url"] not in _known_static]
                        all_raw.extend(_bjobs)
                        print(f"[search] Board-first: {_bstats['boards']} boards, {_bstats['asked']} answered, "
                              f"{_bstats['postings']} postings -> {len(_bjobs)} matching ({_bstats['by_ats']}), "
                              f"{_bstats['not_reached']} not reached, {_bstats['seconds']}s")
                        try:
                            from ingestion.integration import _remember
                            _remember("boards", "degraded" if _bstats["not_reached"] else "ok",
                                      _bstats["postings"], len(_bjobs),
                                      ("%d of %d boards not reached in time" % (_bstats["not_reached"], _bstats["boards"]))
                                      if _bstats["not_reached"] else "")
                        except Exception as _bre:
                            print(f"[search] WARNING could not record board-first health: {_bre}")
                    except Exception as _bse:
                        print(f"[search] ERROR board-first search failed: {_bse}")

                    # -- TechMap CSV(s) (reuse prefetched rows from discovery step) --
                    try:
                        import csv as _csv, io as _io
                        _tm_rows_src = _tm_prefetched_rows
                        if _tm_rows_src is None:
                            # Fallback: fetch again (role-aware categories) if discovery failed
                            _tm_rows_src = []
                            for _cat in _tm_categories:
                                try:
                                    _tm_url = f'https://raw.githubusercontent.com/mluggy/techmap/main/jobs/{_cat}.csv'
                                    _tm_req = _ur2.Request(_tm_url, headers={"User-Agent": "JobHunter/1.0"})
                                    with _ur2.urlopen(_tm_req, timeout=15) as _tm_resp:
                                        _tm_text = _tm_resp.read().decode('utf-8', errors='replace')
                                    _tm_rows_src.extend(list(_csv.DictReader(_io.StringIO(_tm_text))))
                                except Exception as _cat_err2:
                                    print(f"[search] TechMap fallback category '{_cat}' error: {_cat_err2}")
                        _tm_count = 0
                        for _row in _tm_rows_src:
                            _tm_title = (_row.get('title') or '').strip()
                            if _title_match(_tm_title):
                                _tm_url_j = (_row.get('url') or '').strip()
                                if _tm_url_j:
                                    all_raw.append({"job_title": _tm_title, "company": _row.get('company',''),
                                                    "location": _row.get('city',''), "url": _tm_url_j,
                                                    "description": _tm_title, "source": "techmap"})
                                    _tm_count += 1
                        print(f"[search] TechMap CSV ({','.join(_tm_categories)}): {_tm_count} jobs matched")
                    except Exception as _tme:
                        print(f"[search] TechMap CSV error: {_tme}")

                    # -- Comeet by company name: REMOVED 2026-11-05 (Step 7). All five slugs
                    #    (monday, ironsource, gong, yotpo2, lightricks2) answered 404 to the
                    #    exact request, every run, behind `except Exception: pass`. Comeet's
                    #    API is not addressable by company name (STATUS 2026-10-25); Comeet
                    #    postings come through links.learned_boards with real company ids.
                    
                    # -- SpeakNow and SparkHire: REMOVED 2026-11-07 (Sentry). speaknow.co/careers
                    #    answers 500 to any client, and the scraper only ever added untitled
                    #    "SpeakNow Career Opportunity" placeholders; candidate.sparkhire.com no
                    #    longer exists in DNS (no A record), so all six company pages failed.

                    # -- Workday tenants (workday.py: verified tenants, Israel only, failures reported) --
                    _workday_count = 0
                    try:
                        import workday as _workday
                        _wd_jobs, _wd_stats = _workday.search_israel(titles_, _title_match)
                        for _wd_j in _wd_jobs:
                            all_raw.append({"title": _wd_j["title"], "company": _wd_j["company"],
                                            "location": _wd_j["location"], "url": _wd_j["url"],
                                            "description": "", "source": "workday"})
                        _workday_count = len(_wd_jobs)
                        _wd_status, _wd_msg = _workday.health_line(_wd_stats)
                        if _wd_stats["failed"]:
                            print(f"[search] WARNING Workday: {_wd_msg}")
                    except Exception as _wd_exc:
                        _wd_status, _wd_stats = "error", {"fetched": 0, "kept": 0}
                        _wd_msg = f"{type(_wd_exc).__name__}: {_wd_exc}"
                        print(f"[search] ERROR Workday source failed: {_wd_msg}")
                    try:
                        from ingestion.integration import _remember as _src_remember
                        _src_remember("workday", _wd_status, _wd_stats.get("fetched", 0),
                                      _wd_stats.get("kept", 0), _wd_msg)
                    except Exception as _wd_hexc:
                        print(f"[search] WARNING could not record Workday health: {_wd_hexc}")
                    print(f"[search] Workday: {_workday_count} jobs matched")

                    # -- LinkedIn public guest endpoint --
                    _linkedin_count = 0
                    _li_tally = _SourceTally("linkedin")
                    import urllib.parse as _urp2
                    import html as _html_li
                    for _li_kw in titles_[:5]:
                        try:
                            _li_q = _urp2.quote(_li_kw)
                            _li_l = _urp2.quote("Israel")
                            _li_url = f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords={_li_q}&location={_li_l}&start=0"
                            _li_html = _linkedin_fetch(_li_url)
                            import re as _re_li
                            _li_cards = _re_li.findall(r'<a[^>]+class="base-card__full-link[^"]*"[^>]+href="([^"?]+)', _li_html)
                            _li_titles = _re_li.findall(r'<h3[^>]+class="base-search-card__title"[^>]*>\s*([^<]+?)\s*</h3>', _li_html)
                            _li_comps = _re_li.findall(r'<h4[^>]+class="base-search-card__subtitle"[^>]*>\s*<a[^>]*>\s*([^<]+?)\s*</a>', _li_html)
                            _li_locs = _re_li.findall(r'<span[^>]+class="job-search-card__location"[^>]*>\s*([^<]+?)\s*</span>', _li_html)
                            # A 200 with no cards is LinkedIn changing its markup or
                            # serving a challenge - a failure, not "no jobs today".
                            if not _li_cards and "base-card" not in _li_html:
                                raise ValueError("no job cards in the page (markup changed or blocked)")
                            _li_tally.ok(len(_li_cards))
                            for _li_i in range(min(20, len(_li_cards), len(_li_titles))):
                                # The guest page is HTML: "&" arrives as "&amp;". Regex
                                # captures keep the entity, so decode before storing
                                # ("Head of Product &amp; Growth", "AT&amp;T"; 2026-11-11).
                                _li_t = _html_li.unescape(_li_titles[_li_i]).strip()
                                if _title_match(_li_t):
                                    _li_c = _html_li.unescape(_li_comps[_li_i]).strip() if _li_i < len(_li_comps) else ""
                                    _li_lc = _html_li.unescape(_li_locs[_li_i]).strip() if _li_i < len(_li_locs) else ""
                                    all_raw.append({"title": _li_t, "company": _li_c, "location": _li_lc, "url": _li_cards[_li_i], "description": "", "source": "linkedin"})
                                    _linkedin_count += 1
                        except Exception as _li_e:
                            _li_tally.fail(_li_e)
                    _li_tally.kept = _linkedin_count
                    _li_tally.done()
                    print(f"[search] LinkedIn guest: {_linkedin_count} jobs matched")

                    # -- Indeed HTML scrape: REMOVED 2026-11-07. il.indeed.com answers 403
                    #    from Cloudflare's bot wall (401 earlier) on every request, so it never
                    #    returned a job; Indeed reaches us through JobSpy when that is installed.

                    # ── Multi-source ingestion + cross-platform fuzzy dedup ──────────
                    # New pipeline (ingestion/): pulls Big-Tech internal endpoints (free,
                    # everyone) + paid aggregators/scrapers (admin only), then fuzzy-merges
                    # the WHOLE union so a role mirrored across a career page + LinkedIn +
                    # an aggregator reaches the AI scorer ONCE. Falls back to the legacy
                    # exact-fingerprint dedup if the package is unavailable (never fatal).
                    _ingest_ok = False
                    try:
                        import ingestion as _ingestion
                        # role gates the paid sources (admin-only)
                        _role = "user"
                        _paid_ok = False
                        _plan_row = None
                        try:
                            _conn_r = database.get_db()
                            try:
                                _rr = _conn_r.execute(
                                    "SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
                                _role = (_rr["role"] if _rr and _rr["role"] else "user")
                                if _rr:
                                    _plan_row = dict(_rr)
                                    # Paid sources: Premium and Expert, while this
                                    # month's credits last (plans.py); admin always.
                                    if entitlements.limit(_plan_row, "paid_sources"):
                                        _paid_ok = entitlements.allowed(
                                            _conn_r, _plan_row, "paid_source_credit")[0]
                                _conn_r.close()
                            finally:
                                _conn_r.close()
                        except Exception:
                            _role = "user"

                        def _spend_paid_credit(_src, _row=_plan_row):
                            if _row is None:
                                return
                            _cc = database.get_db()
                            try:
                                entitlements.try_consume(_cc, _row, "paid_source_credit")
                            finally:
                                _cc.close()
                        _ext = _ingestion.collect_external_sources(
                            role=_role, titles=titles_, locations=locs_,
                            keywords=kws_, existing_urls=existing_urls,
                            allow_paid=_paid_ok, on_paid_call=_spend_paid_credit)
                        all_raw.extend(_ext)
                        # The shared paid-source pool (paidpool.py): fetched once a
                        # day for everyone, read here at no cost. Same plan gate as
                        # paid sources (Beta/Premium/Expert, admin); no per-user credit.
                        if _role == "admin" or (_plan_row and entitlements.limit(_plan_row, "paid_sources")):
                            try:
                                _pc = database.get_db()
                                try:
                                    _pool = paidpool.jobs_for_search(_pc)
                                finally:
                                    _pc.close()
                                _pool = [p for p in _pool if p["url"] not in existing_urls]
                                all_raw.extend(_pool)
                                print(f"[search] paid pool: {len(_pool)} job(s) not seen before")
                            except Exception as _pe:
                                print(f"[search] ERROR paid pool unreadable: {_pe}")
                        _before = len(all_raw)
                        all_raw = _ingestion.deduplicate_raw(all_raw)
                        print(f"[search] Multi-source fuzzy dedup: {_before} -> {len(all_raw)} "
                              f"canonical jobs (role={_role})")
                        _ingest_ok = True
                    except Exception as _ing_err:
                        print(f"[search] ingestion module unavailable ({_ing_err}); legacy dedup")

                    if not _ingest_ok:
                        # -- legacy exact company+title fingerprint dedup (fallback) --
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
                    print(f"[search] Pre-filter: {len(all_raw)} jobs from {len(_GH_COMPANIES)+len(_LV_COMPANIES)} ATS boards + external sources")

                    if not all_raw:
                        return []

                    # -- Score against user CV + preferences --
                    # Cascading fallback: Gemini Flash (free) → Anthropic Haiku → rule-based heuristic

                    # Fetch CV summary + path from DB for richer scoring context
                    _cv_text = ""
                    _cv_path = ""
                    try:
                        _conn2 = database.get_db()
                        try:
                            _prof2 = _conn2.execute(
                                "SELECT cv_summary, cv_path FROM user_profiles WHERE user_id=?",
                                (user_id,)
                            ).fetchone()
                            _conn2.close()
                            _cv_text = (_prof2["cv_summary"] or "") if _prof2 else ""
                            _cv_path = _cv_file(user_id, (_prof2["cv_path"] or "") if _prof2 else "")
                        finally:
                            _conn2.close()
                    except Exception:
                        _cv_text = ""
                        _cv_path = ""

                    profile_text = (
                        f"Target roles: {', '.join(titles_[:4])}\n"
                        f"Key skills: {', '.join(kws_[:10])}\n"
                        f"Locations: {', '.join(locs_)} or Remote\n"
                        f"Seniority: Senior / Director / VP / Head-of"
                    )
                    if _cv_text:
                        profile_text += f"\n\nCV Summary:\n{_cv_text[:4000]}"

                    def _parse_scored_response(text_):
                        """Extract a valid JSON array of scored jobs from an AI response string."""
                        t = text_.strip()
                        if "```" in t:
                            t = t.split("```")[1]
                            if t.startswith("json"): t = t[4:]
                        si = t.find("[")
                        if si < 0:
                            print(f"[search] ⚠️  AI response had no JSON array — skipping batch")
                            return []
                        ei = t.rfind("]")
                        parsed = None
                        if ei > si:
                            try:
                                parsed = _js2.loads(t[si:ei+1])
                            except Exception:
                                parsed = None
                        if parsed is None:
                            # Robust salvage: read complete objects one-by-one with
                            # raw_decode. Handles truncation (output cut off), a missing
                            # comma between objects, and trailing junk — we keep every
                            # object that parses and stop at the first broken one, instead
                            # of throwing away the whole batch.
                            _frag = t[si:]
                            _dec = _js2.JSONDecoder()
                            parsed = []
                            _k = _frag.find("{")
                            while _k != -1:
                                try:
                                    _obj, _end = _dec.raw_decode(_frag, _k)
                                except Exception:
                                    break
                                if isinstance(_obj, dict):
                                    parsed.append(_obj)
                                _k = _frag.find("{", _end)
                            if parsed:
                                print(f"[search] ⚠️  Recovered {len(parsed)} job(s) from a malformed/truncated AI response")
                            else:
                                print(f"[search] ⚠️  AI response had no parseable JSON — skipping batch")
                                return []
                        out = []
                        for j in parsed:
                            if isinstance(j, dict) and j.get("url") and j.get("candidate_score", 0) >= 30:
                                j.setdefault("match_score", j.get("candidate_score", 0))
                                j.setdefault("found_date", today)
                                j.setdefault("source", "greenhouse/lever")
                                out.append(j)
                        return out

                    def _keep_source(scored_, batch_):
                        """The scorer returns the jobs it kept without their source,
                        and "greenhouse/lever" was stamped on all of them - LinkedIn
                        and aggregator jobs included - so the link report's by-source
                        split measured nothing (found 2026-11-08, Step 16)."""
                        _src = {b.get("url"): b.get("source") for b in batch_ if b.get("url")}
                        for _j in scored_:
                            if _src.get(_j.get("url")):
                                _j["source"] = _src[_j["url"]]
                        return scored_

                    def _score_batch(batch_, profile_text_):
                        """Score a batch of jobs via Gemini → Anthropic → heuristic fallback."""
                        import os as _os_sb
                        _GEMINI_KEY = _os_sb.environ.get('GEMINI_API_KEY', '')

                        jobs_json_ = _js2.dumps(
                            [{"job_title": j.get("job_title",""), "company": j.get("company",""),
                              "location": j.get("location",""), "url": j.get("url",""),
                              "description": (j.get("description") or ""),
                              "full_description": (j.get("full_description") or j.get("description") or "")[:2000]}
                             for j in batch_], ensure_ascii=False)

                        # Build a lean candidate context — CV PDF carries the full detail
                        _titles_str = profile_text_.split('\n')[0] if profile_text_ else ''
                        _locs_str_sc = next((line.replace('Locations: ','') for line in profile_text_.split('\n') if line.startswith('Locations:')), 'Israel')
                        _seniority_str = next((line.replace('Seniority: ','') for line in profile_text_.split('\n') if line.startswith('Seniority:')), 'Senior / Director')
                        # Include the CV text directly in the prompt so the non-Gemini
                        # paths (Anthropic / heuristic) actually see the candidate's
                        # background. Only the Gemini branch attaches the full PDF.
                        _cv_block = ""
                        if _cv_text:
                            _cv_block = f"\nCandidate CV (summary / extracted text):\n{_cv_text[:4000]}\n"
                        _roles_hint = _titles_str.replace('Target roles: ', '').strip() or "(not specified — infer the candidate's field and role family from the CV below)"
                        prompt_ = (
                            "You are a precise, role-agnostic job matching assistant. Your job is to match "
                            "openings to THIS candidate based on their actual CV and stated target roles — "
                            "regardless of profession. Do not assume any particular field.\n\n"
                            "If the candidate's full CV is attached as a PDF, read it carefully. "
                            "Otherwise use the CV text provided below.\n\n"
                            f"Candidate target roles: {_roles_hint}\n"
                            f"Target locations: {_locs_str_sc}\n"
                            f"Seniority level: {_seniority_str}\n"
                            f"{_cv_block}\n"
                            f"Job listings (JSON):\n{jobs_json_}\n\n"
                            "Score each job 0-100 using EXACTLY these four dimensions:\n\n"
                            "TITLE / FUNCTION MATCH (0-30 pts):\n"
                            "  27-30: Title matches the candidate's target roles, or their actual role/profession from the CV, exactly or near-exactly\n"
                            "  15-26: Same field, adjacent or variant title (different specialization within the candidate's profession)\n"
                            "   5-14: Related field where the candidate's experience is transferable\n"
                            "   0-4:  Clearly a different profession from the candidate's background — EXCLUDE this job (do not return it)\n\n"
                            "SENIORITY MATCH (0-20 pts):\n"
                            "  17-20: Exact seniority level match\n"
                            "  10-16: One level off (e.g. Senior vs Lead/Director)\n"
                            "   0-9:  Major mismatch (junior/IC for a senior candidate, or executive for mid-level)\n\n"
                            "LOCATION (0-20 pts):\n"
                            "  17-20: Matches the candidate's target locations / Hybrid / explicitly Remote-friendly\n"
                            "  10-16: Remote with no location restriction specified\n"
                            "   0-9:  Requires relocation away from target locations, or on-site elsewhere\n\n"
                            "CV vs JOB REQUIREMENTS MATCH (0-30 pts) — most important dimension:\n"
                            "  Read the job's required skills, years of experience, and responsibilities from full_description.\n"
                            "  Cross-reference against the candidate's ACTUAL experience in the CV.\n"
                            "  27-30: Candidate's background directly covers most stated requirements\n"
                            "  15-26: Strong overlap with minor gaps\n"
                            "   5-14: Moderate overlap — candidate could do the role but has notable experience gaps\n"
                            "   0-4:  Fundamentally mismatched requirements (completely different background needed)\n\n"
                            "EXCLUDE (score 0, omit from output entirely):\n"
                            "  Any role in a profession or function clearly unrelated to the candidate's CV and "
                            "  target roles. Determine the candidate's actual field FROM THE CV — do NOT assume "
                            "  product management or any other specific field.\n\n"
                            "Total score = Title + Seniority + Location + CV-Requirements. "
                            "Include jobs scoring >= 30. "
                            "For fit_reason: one sentence on why this job fits the job seeker. " + voice.FIT_REASON_RULE + " "
                            "Return ONLY a valid JSON array with fields: "
                            "job_title, company, location, url, publish_date (null if unknown), "
                            "full_description (copy from input), description (2-3 sentences), "
                            "candidate_score (0-100), fit_reason. No markdown."
                        )

                        # --- Try Gemini Flash first (free tier: 1500 req/day) ---
                        if _GEMINI_KEY:
                            try:
                                import base64 as _b64_sc, os as _os_sc
                                _parts = []
                                # Attach full CV PDF if available — much richer context than text summary
                                _cv_path_sc = _cv_path  # closure from outer scope
                                if _cv_path_sc and _os_sc.path.exists(_cv_path_sc) and _cv_path_sc.lower().endswith('.pdf'):
                                    with open(_cv_path_sc, 'rb') as _cvf:
                                        _cv_b64 = _b64_sc.b64encode(_cvf.read()).decode()
                                    _parts.append({'inlineData': {'mimeType': 'application/pdf', 'data': _cv_b64}})
                                    print(f"[search] Scoring with CV PDF ({len(_cv_b64)//1024}KB b64)")
                                else:
                                    print(f"[search] Scoring with CV text (no PDF at '{_cv_path_sc}')")
                                _parts.append({'text': prompt_})
                                _g_body = _js2.dumps({
                                    'contents': [{'parts': _parts}],
                                    'generationConfig': {'temperature': 0.2, 'maxOutputTokens': 12288, 'thinkingConfig': {'thinkingBudget': 0}}
                                }).encode('utf-8')
                                _g_data = _js2.loads(_gemini_generate(
                                    _g_body, timeout=90, purpose="search_scoring", user_id=user_id))
                                _g_text = _g_data['candidates'][0]['content']['parts'][0]['text']
                                result_ = _parse_scored_response(_g_text)
                                print(f"[search] Gemini scored {len(batch_)} -> {len(result_)} passed")
                                return result_
                            except Exception as _ge:
                                print(f"[search] Gemini scoring error: {_ge} — trying Anthropic")
                                database.log_activity(user_id, "scoring_warning",
                                    f"Gemini scoring failed: {_ge}. Falling back to Anthropic.")

                        # --- Try Anthropic Claude Haiku (secondary; off unless SEARCH_USE_ANTHROPIC) ---
                        if ANTHROPIC_KEY and SEARCH_USE_ANTHROPIC:
                            try:
                                _a_body = _js2.dumps({"model": "claude-haiku-4-5", "max_tokens": 4096,
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
                                import urllib.error as _ue
                                _ae_body = ""
                                if isinstance(_ae, _ue.HTTPError):
                                    try: _ae_body = " | " + _ae.read().decode("utf-8", errors="replace")[:200]
                                    except Exception: pass
                                print(f"[search] Anthropic scoring error: {_ae}{_ae_body} — falling back to heuristic")
                                database.log_activity(user_id, "scoring_warning",
                                    f"Anthropic scoring failed: {_ae}. Falling back to keyword heuristic.")

                        # --- Rule-based heuristic (no API needed) ---
                        print(f"[search] ⚠️  HEURISTIC scoring {len(batch_)} jobs — AI keys missing or failed.")
                        print(f"[search]    To enable AI scoring, set GEMINI_API_KEY or ANTHROPIC_API_KEY in Railway.")
                        database.log_activity(user_id, "scoring_warning",
                            "Using keyword heuristic scoring — results may be limited. "
                            "Set GEMINI_API_KEY or ANTHROPIC_API_KEY in Railway to enable AI scoring.")
                        out_ = []
                        for j in batch_:
                            _t = (j.get("job_title") or "").lower()
                            _desc = (j.get("full_description") or j.get("description") or "").lower()
                            _loc = (j.get("location") or "").lower()
                            _score = 0
                            # Title match — use same 4-pass logic as collection filter
                            if _title_match(j.get("job_title", "")):
                                _score += 50
                            # Keyword match in description (up to +20)
                            for _kw in kws_:
                                if _kw.lower() in _desc:
                                    _score += 5
                                if _score >= 70:
                                    break
                            # Location match
                            for _lp in locs_:
                                if _lp.lower() in _loc or "remote" in _loc or "israel" in _loc:
                                    _score += 10
                                    break
                            if _score >= 30:
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

                    # Score all collected jobs in batches of 50
                    # Cap ATS jobs at 300 to avoid excessive API calls; supplemental sources are uncapped
                    _ats_sources = {'greenhouse', 'lever', 'smartrecruiters'}
                    _ats_jobs = [j for j in all_raw if j.get('source','') in _ats_sources]
                    _other_jobs = [j for j in all_raw if j.get('source','') not in _ats_sources]
                    ATS_CAP = 300
                    if len(_ats_jobs) > ATS_CAP:
                        print(f"[search] Capping ATS jobs {len(_ats_jobs)} -> {ATS_CAP} (keeping all {len(_other_jobs)} from other sources)")
                        _ats_jobs = _ats_jobs[:ATS_CAP]
                    _jobs_to_score = _ats_jobs + _other_jobs
                    print(f"[search] Scoring {len(_jobs_to_score)} jobs ({len(_ats_jobs)} ATS + {len(_other_jobs)} other)")
                    scored_jobs = []
                    for batch_i in range(0, len(_jobs_to_score), 20):
                        batch = _jobs_to_score[batch_i:batch_i+20]
                        scored_jobs.extend(_keep_source(_score_batch(batch, profile_text), batch))

                    # -- Supplemental: Gemini + Google Search (parallel, CV-aware, scored) --
                    import os as _os_ws
                    _GEMINI_KEY_WS = _os_ws.environ.get('GEMINI_API_KEY', '')
                    if _GEMINI_KEY_WS:
                        # Build a rich candidate context for the search prompt
                        _seniority_hint = "VP / Director / Head-of / Senior" if any(
                            w in " ".join(titles_).lower() for w in ("vp", "director", "head", "senior", "lead", "principal")
                        ) else "Senior / Lead"
                        _cv_snippet = _cv_text[:600].replace('\n', ' ') if _cv_text else ""
                        _locs_str = ", ".join(locs_[:2]) or "Israel"

                        def _ws_one_search(query_hint_):
                            """Run one Gemini Google Search and return raw job dicts."""
                            _ws_prompt = (
                                f'Search for current job openings matching this query: "{query_hint_}". '
                                f'STRONGLY PREFER direct application URLs on company career pages and applicant-tracking systems '
                                f'(boards.greenhouse.io, jobs.lever.co, *.myworkdayjobs.com, comeet.com, smartrecruiters.com, '
                                f'ashbyhq.com, or a single posting on a company careers site). These let the candidate apply directly. '
                                f'AVOID job-board aggregator links (linkedin.com/jobs, indeed.com, glassdoor.com) unless no direct URL exists, '
                                f'because those require manual application. Return the direct apply URL whenever possible. '
                                f'Every url MUST be the page of that ONE posting, copied exactly from a search result - never a '
                                f'careers home page (e.g. company.com/careers), a job-board index, or a URL you composed yourself. '
                                f'If you cannot find the posting\'s own URL, leave that job out. '
                                f'Candidate context — seniority: {_seniority_hint}; location: {_locs_str}. '
                                + (f'Background: {_cv_snippet} ' if _cv_snippet else '')
                                + 'Return ONLY a JSON array of up to 8 results. '
                                'Each item: {"job_title":"...","company":"...","location":"...","url":"...","description":"2-3 sentences about the role"}'
                            )
                            try:
                                _ws_body = _js2.dumps({
                                    'contents': [{'parts': [{'text': _ws_prompt}]}],
                                    'tools': [{'google_search': {}}],
                                    'generationConfig': {'temperature': 0.1, 'maxOutputTokens': 2048, 'thinkingConfig': {'thinkingBudget': 0}}
                                }).encode('utf-8')
                                _ws_data = _js2.loads(_gemini_generate(
                                    _ws_body, timeout=60, purpose="web_search", user_id=user_id))
                                _ws_cand = (_ws_data.get('candidates') or [{}])[0]
                                _ws_parts = (_ws_cand.get('content') or {}).get('parts') or []
                                if not _ws_parts:
                                    # Sentry 2026-11-07: this was a bare KeyError 'content'.
                                    # Gemini ends some grounded searches with no text (a
                                    # safety or recitation stop, or nothing found); say which.
                                    print(f"[search] Web search for '{query_hint_}' returned no text "
                                          f"(finishReason={_ws_cand.get('finishReason') or 'none'}, "
                                          f"blockReason={(_ws_data.get('promptFeedback') or {}).get('blockReason') or 'none'})")
                                    return []
                                _ws_text = (_ws_parts[0].get('text') or '').strip()
                                return [j for j in _json_array_from_text(_ws_text)
                                        if isinstance(j, dict) and j.get('url')]
                            except Exception as _wse:
                                print(f"[search] Web search error for '{query_hint_}': {_wse}")
                            return []

                        # Build search queries: one per title + one broad seniority+location query
                        _ws_queries = [f'{t} jobs in {_locs_str}' for t in titles_[:4]]
                        # Strip seniority prefixes from titles to avoid "Senior Senior PM" duplication
                        _level_words = {'senior', 'sr', 'sr.', 'lead', 'principal', 'staff', 'vp', 'director', 'head', 'vice', 'president', 'chief'}
                        _clean_titles = [
                            ' '.join(w for w in t.split() if w.lower() not in _level_words)
                            for t in titles_[:2]
                        ]
                        _clean_titles = [t for t in _clean_titles if t.strip()]
                        if _clean_titles:
                            _ws_queries.append(
                                f'{_seniority_hint} {" OR ".join(_clean_titles)} '
                                f'startup Israel site:greenhouse.io OR site:lever.co OR site:comeet.com OR site:myworkdayjobs.com'
                            )

                        # Run all queries in parallel
                        _ws_raw = []
                        _ws_lock = _thr.Lock()
                        def _ws_worker(q_):
                            results_ = _ws_one_search(q_)
                            with _ws_lock:
                                _ws_raw.extend(results_)

                        _ws_threads = [_thr.Thread(target=_ws_worker, args=(q,), daemon=True) for q in _ws_queries]
                        for _wt in _ws_threads: _wt.start()
                        for _wt in _ws_threads: _wt.join(timeout=70)

                        print(f"[search] Web search: {len(_ws_queries)} queries → {len(_ws_raw)} raw results")

                        # Pass web search results through the same AI scorer (not just blindly add them)
                        if _ws_raw:
                            # Normalise field names to match what _score_batch expects
                            for _wsj in _ws_raw:
                                _wsj.setdefault('job_title', _wsj.get('title', ''))
                                _wsj.setdefault('full_description', _wsj.get('description', ''))
                                _wsj['source'] = 'web_search'
                            _ws_scored = _keep_source(_score_batch(_ws_raw, profile_text), _ws_raw)
                            for _wsj in _ws_scored:
                                _wsj.setdefault('fit_reason', 'Found via Google Search')
                                _wsj.setdefault('found_date', today)
                            scored_jobs.extend(_ws_scored)
                            print(f"[search] Web search: {len(_ws_raw)} raw → {len(_ws_scored)} passed scoring")
                    else:
                        print("[search] Gemini web search skipped — no GEMINI_API_KEY")


                    # ── Track B: Description-first scoring (activates when Track A < 5 results) ──
                    if len(scored_jobs) < 5:
                        print(f"[search] Track A returned {len(scored_jobs)} results — activating Track B (description matching)")
                        import os as _os_tb
                        _TB_GEMINI = _os_tb.environ.get('GEMINI_API_KEY', '')
                        _TB_ANTH   = (_os_tb.environ.get('ANTHROPIC_API_KEY', '') if SEARCH_USE_ANTHROPIC else '')
                        if _TB_GEMINI or _TB_ANTH:
                            # Collect jobs with meaningful descriptions that Track A didn't already return
                            _scored_urls = {j.get('url','') for j in scored_jobs}
                            _trackb_pool = [
                                j for j in all_raw
                                if len(j.get('full_description') or j.get('description','')) > 100
                                and j.get('url','') not in _scored_urls
                            ]
                            _trackb_pool = _trackb_pool[:150]  # cap to control API cost
                            print(f"[search] Track B pool: {len(_trackb_pool)} jobs with descriptions")

                            if _trackb_pool:
                                def _score_batch_desc(batch_d_, profile_text_d_):
                                    """Score jobs primarily on description content vs CV."""
                                    _batch_indexed = [
                                        {"id": _bi, "title": _bj.get("job_title",""),
                                         "company": _bj.get("company",""),
                                         "location": _bj.get("location",""),
                                         "description": (_bj.get("full_description") or _bj.get("description",""))[:2500]}
                                        for _bi, _bj in enumerate(batch_d_)
                                    ]
                                    _batch_json_d = _js2.dumps(_batch_indexed, ensure_ascii=False)
                                    _titles_d = profile_text_d_.split('\n')[0] if profile_text_d_ else ''
                                    _locs_d   = next((ln.replace('Locations: ','') for ln in profile_text_d_.split('\n') if ln.startswith('Locations:')), 'Israel')
                                    _prompt_d = (
                                        "You are evaluating job openings for a senior product candidate.\n"
                                        "The candidate's CV text is provided below — use it as the primary signal.\n\n"
                                        f"Candidate profile:\n{profile_text_d_}\n\n"
                                        "Score each job 0-100 on these FOUR dimensions (description is the main signal):\n\n"
                                        "TITLE/ROLE FIT (0-10 pts — loose gate):\n"
                                        "  8-10: Direct match to target role or clear leadership adjacent\n"
                                        "   4-7: Adjacent role worth exploring based on description\n"
                                        "   0-3: Clearly wrong function (exclude)\n\n"
                                        "SENIORITY MATCH (0-20 pts):\n"
                                        "  17-20: Exact seniority match\n"
                                        "  10-16: One level off\n"
                                        "   0-9:  Major mismatch\n\n"
                                        "LOCATION (0-20 pts):\n"
                                        "  17-20: Israel / Tel Aviv / Hybrid / Remote-friendly\n"
                                        "  10-16: Remote, no restriction\n"
                                        "   0-9:  Requires relocation outside Israel\n\n"
                                        "DESCRIPTION vs CV MATCH (0-50 pts — PRIMARY SIGNAL):\n"
                                        "  Read the job description carefully. Compare required skills,\n"
                                        "  responsibilities, and experience to the candidate's background.\n"
                                        "  45-50: Near-perfect match — candidate's background directly fits most requirements\n"
                                        "  30-44: Strong overlap with minor gaps\n"
                                        "  15-29: Moderate overlap — candidate could stretch into this role\n"
                                        "   0-14: Significant mismatch in required background\n\n"
                                        "EXCLUDE only clearly wrong roles: pure engineering IC, quota-sales, finance/legal/HR. Product-adjacent leadership is OK.\n\n"
                                        "Include jobs scoring >= 25 (permissive — this is a last-resort fallback). "
                                        "Return ONLY a JSON array. Each item: "
                                        "{id (from input), score (0-100), reason (one sentence why)}\n"
                                        + voice.FIT_REASON_RULE.replace("fit_reason", "reason") + "\n\n"
                                        f"Jobs:\n{_batch_json_d}"
                                    )

                                    def _parse_desc_resp(txt_):
                                        t = txt_.strip()
                                        if "```" in t:
                                            t = t.split("```")[1]
                                            if t.startswith("json"): t = t[4:]
                                        si = t.find("[");  ei = t.rfind("]")
                                        if si < 0 or ei <= si: return []
                                        try:
                                            return _js2.loads(t[si:ei+1])
                                        except Exception:
                                            return []

                                    def _apply_desc_scores(scored_ids_, batch_d2_, reason_default="Matched by description"):
                                        # coerce IDs to int — AI often returns string "0" not integer 0
                                        _id_map_d = {}
                                        for _item_d in scored_ids_:
                                            if isinstance(_item_d, dict) and "score" in _item_d:
                                                try: _id_map_d[int(_item_d["id"])] = _item_d
                                                except (KeyError, ValueError, TypeError): pass
                                        out_d_ = []
                                        for _di, _dj in enumerate(batch_d2_):
                                            _ds = _id_map_d.get(_di)
                                            # threshold 25 — permissive, Track B is last-resort fallback
                                            if _ds and _ds.get("score", 0) >= 25:
                                                _jc_d = dict(_dj)
                                                _jc_d["candidate_score"] = min(_ds["score"], 95)
                                                _jc_d["match_score"]     = _jc_d["candidate_score"]
                                                _jc_d["fit_reason"]      = _ds.get("reason", reason_default)
                                                _jc_d["source"]          = "description_match"
                                                _jc_d.setdefault("found_date", today)
                                                _jc_d.setdefault("publish_date", None)
                                                _jc_d.setdefault("description", _dj.get("description") or _dj.get("job_title",""))
                                                out_d_.append(_jc_d)
                                        return out_d_

                                    _out_d = []
                                    _ai_d_used = False

                                    if _TB_GEMINI:
                                        try:
                                            import base64 as _b64_d, os as _os_d2
                                            _parts_d = [{"text": _prompt_d}]
                                            if _cv_path:
                                                try:
                                                    with open(_cv_path, "rb") as _cvf_d:
                                                        _parts_d.insert(0, {
                                                            "inline_data": {
                                                                "mime_type": "application/pdf",
                                                                "data": _b64_d.b64encode(_cvf_d.read()).decode()
                                                            }
                                                        })
                                                except Exception:
                                                    pass
                                            _body_d = _js2.dumps({
                                                "contents": [{"parts": _parts_d}],
                                                "generationConfig": {"temperature": 0.1, "maxOutputTokens": 8192, "thinkingConfig": {"thinkingBudget": 0}}
                                            }).encode()
                                            _resp_d = _js2.loads(_gemini_generate(
                                                _body_d, timeout=90, purpose="search_scoring_desc",
                                                user_id=user_id))
                                            _text_d = _resp_d["candidates"][0]["content"]["parts"][0]["text"]
                                            _scored_d = _parse_desc_resp(_text_d)
                                            _out_d = _apply_desc_scores(_scored_d, batch_d_)
                                            _ai_d_used = True
                                            print(f"[search] Track B Gemini: {len(batch_d_)} -> {len(_out_d)} passed")
                                        except Exception as _de:
                                            print(f"[search] Track B Gemini error: {_de}")

                                    if not _ai_d_used and _TB_ANTH:
                                        try:
                                            _body_d2 = _js2.dumps({
                                                "model": "claude-haiku-4-5-20251001",
                                                "max_tokens": 4096,
                                                "messages": [{"role": "user", "content": _prompt_d}]
                                            }).encode()
                                            _req_d2 = _ur2.Request(
                                                "https://api.anthropic.com/v1/messages",
                                                data=_body_d2,
                                                headers={"Content-Type": "application/json",
                                                         "x-api-key": _TB_ANTH,
                                                         "anthropic-version": "2023-06-01"},
                                                method="POST"
                                            )
                                            with _ur2.urlopen(_req_d2, timeout=90) as _rd2:
                                                _resp_d2 = _js2.loads(_rd2.read().decode())
                                            _text_d2 = _resp_d2["content"][0]["text"]
                                            _scored_d2 = _parse_desc_resp(_text_d2)
                                            _out_d = _apply_desc_scores(_scored_d2, batch_d_)
                                            _ai_d_used = True
                                            print(f"[search] Track B Anthropic: {len(batch_d_)} -> {len(_out_d)} passed")
                                        except Exception as _de2:
                                            print(f"[search] Track B Anthropic error: {_de2}")

                                    if not _ai_d_used:
                                        print("[search] Track B: AI call failed — no description scores added")
                                        database.log_activity(user_id, "track_b_search",
                                            "Track B AI call failed. Check GEMINI_API_KEY / ANTHROPIC_API_KEY in Railway.")

                                    return _out_d

                                _tb_scored = []
                                for _tb_i in range(0, len(_trackb_pool), 50):
                                    _tb_batch = _trackb_pool[_tb_i:_tb_i+50]
                                    _tb_scored.extend(_score_batch_desc(_tb_batch, profile_text))

                                print(f"[search] Track B: {len(_trackb_pool)} -> {len(_tb_scored)} passed description matching")
                                database.log_activity(user_id, "track_b_search",
                                    f"Track B description matching: {len(_trackb_pool)} candidates -> {len(_tb_scored)} passed")
                                scored_jobs.extend(_tb_scored)
                        else:
                            print("[search] Track B: no AI keys — skipping")

                    print(f"[search] Final: {len(scored_jobs)} scored jobs (from {len(all_raw)} pre-filtered)")
                    return scored_jobs

                all_jobs_data = []
                seen_urls     = set(existing_urls)
                seen_key      = set()

                # Discover jobs via company APIs (Greenhouse/Lever) + Gemini search/scoring.
                jobs_data = _search_jobs_with_claude_websearch(titles, locations, keywords)
                print(f"[run-search] Found {len(jobs_data)} jobs via AI search (Gemini)")

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
                        deliver_notification(user_id, f"🔍 Search Complete — {today}\n\nNo new jobs found this run.", url_suffix=home_url() + "#new")
                    except Exception as _dn_err:
                        print(f"[run-search] deliver_notification error: {_dn_err}")
                    return

                # ── URL check for new jobs ───────────────────────────────────────
                import apply_engine as _ae
                from concurrent.futures import ThreadPoolExecutor as _TPE
                _new_urls = {j.get("url","").strip() for j in all_jobs_data if j.get("url")}
                _url_ok   = {}
                _url_final = {}
                import linkcheck as _lc

                def _insert_check(u_):
                    v_, why_, fin_ = _lc.classify(u_, 12)
                    return v_, why_, fin_
                with _TPE(max_workers=8) as _ex:
                    _futs = {_ex.submit(_insert_check, u): u for u in _new_urls}
                    for _f, _u in _futs.items():
                        try:
                            _v, _why, _fin = _f.result(timeout=25)
                        except Exception:
                            _v, _why, _fin = "unknown", "timeout", ""
                        # Same bar as before for unknown (skip): only 'alive' is inserted.
                        _url_ok[_u] = 1 if _v == "alive" else 0
                        if _v != "alive":
                            print(f"[run-search] skipped at insert ({_v}): {_why} - {_u[:120]}")
                        # Gemini grounding links 404 within days; keep where they LAND,
                        # or the next sweep retires a live job for its expired redirect.
                        if _v == "alive" and _fin and "grounding-api-redirect" in _u:
                            _url_final[_u] = _fin
                _chk_date = datetime.now().isoformat()

                # ── Load rejected patterns to filter out ────────────────────────
                conn = database.get_db()
                try:
                    _rej_patterns = conn.execute(
                        "SELECT LOWER(TRIM(company)) as c, LOWER(TRIM(title)) as t FROM rejected_patterns WHERE user_id=?",
                        (user_id,)
                    ).fetchall()
                    _rej_set = {(r["c"], r["t"]) for r in _rej_patterns}
                    _rej_companies = {r["c"] for r in _rej_patterns if r["c"]}
                    _block_set = {(c or "").strip().lower() for c in database.get_blocklist(conn, user_id)}
                    # ── Feedback learning signals (for pre-scoring new jobs) ─────────
                    _fb_signals = database.get_feedback_signals(conn, user_id)
                    _fb_prof_row = conn.execute(
                        "SELECT * FROM user_profiles WHERE user_id=?", (user_id,)
                    ).fetchone()
                    _fb_prof = dict(_fb_prof_row) if _fb_prof_row else {}
                    conn.close()
                    from ai_analysis import compute_feedback_penalty as _compute_fb_penalty

                    # ── Insert new jobs (skip rejected patterns) ─────────────────────
                    conn = database.get_db()
                    try:
                        inserted = 0; new_jobs_info = []; skipped_rej = 0
                        _rel_fail = 0; _rel_err = ""
                        # Phase 5b: this search's jobs are the ones above this id.
                        _batch_after = conn.execute("SELECT COALESCE(MAX(id), 0) FROM jobs").fetchone()[0]
                        for j in all_jobs_data:
                            _jc = j.get("company","").strip().lower()
                            _jt = j.get("job_title","").strip().lower()
                            if (_jc, _jt) in _rej_set:
                                skipped_rej += 1
                                continue
                            if _jc in _block_set:
                                skipped_rej += 1
                                continue
                            if j.get("match_score", 0) <= 0:
                                continue
                            # ── Relevance gate: only insert/notify jobs aligned with the user's
                            #    titles + locations, so off-target roles never reach the queue. ──
                            try:
                                from ingestion.relevance import passes as _rel_passes, gate_enabled as _rel_gate
                                if _rel_gate() and not _rel_passes(
                                        j.get("job_title") or j.get("title") or "",
                                        j.get("location") or "", titles, locations, keywords):
                                    skipped_rej += 1
                                    continue
                            except Exception as _rel_e:
                                # Fails OPEN (the job goes in unfiltered) - a broken
                                # gate must not empty the queue - but no longer silently.
                                _rel_fail += 1
                                _rel_err = f"{type(_rel_e).__name__}: {_rel_e}"
                            try:
                                _jurl = (j.get("url") or "").strip()
                                if _jurl and _url_ok.get(_jurl) == 0:
                                    continue  # skip dead links
                                if _jurl in _url_final:
                                    _url_ok[_url_final[_jurl]] = 1
                                    _jurl = _url_final[_jurl]
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
                                # ── Learned feedback penalty (deterministic; demotes, never hides) ──
                                _fb_pen, _fb_rsn = _compute_fb_penalty(
                                    {"company": j.get("company",""), "title": j.get("job_title","")},
                                    _fb_signals, _fb_prof
                                )
                                j = textclean.clean_job(j)   # plain text, whatever HTML the source sent
                                conn.execute(
                                    "INSERT OR IGNORE INTO jobs "
                                    "(user_id,title,company,location,url,description,why_relevant,source,"
                                    "found_date,match_score,candidate_score,status,url_verified,url_check_date,publish_date,full_description,"
                                    "feedback_penalty,feedback_reason) "
                                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,'new',?,?,?,?,?,?)",
                                    (user_id, j.get("job_title",""), j.get("company",""), j.get("location",""),
                                     _jurl, j.get("description",""), j.get("fit_reason",""), j.get("source",""),
                                     j.get("found_date",today), j.get("match_score",0), j.get("candidate_score",0),
                                     _url_ok.get(_jurl) if _jurl else None, _chk_date if _jurl else None,
                                     j.get("publish_date"), (j.get("full_description") or "")[:5000],
                                     _fb_pen, _fb_rsn))
                                inserted += 1
                                new_jobs_info.append({"title":j.get("job_title",""),"company":j.get("company",""),"url":_jurl,"url_ok":_url_ok.get(_jurl) if _jurl else None})
                            except Exception as e: print(f"[run-search] insert error: {e}")
                        conn.commit()
                        if _rel_fail:
                            print(f"[run-search] ERROR relevance gate failed for {_rel_fail} job(s) of user "
                                  f"{user_id}; they were inserted unfiltered. Last error: {_rel_err[:200]}")
                        # ── Plan: matches shown per search (Phase 5b) ──────────────────
                        # The plan's top N new jobs stay 'new'; the rest are 'locked' and
                        # reach the user only as a count. A failure here leaves every job
                        # visible (logged), never hides the whole search.
                        locked_n = 0
                        try:
                            _plan_user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
                            _locked = entitlements.lock_matches_beyond_limit(
                                conn, dict(_plan_user), _batch_after, _batch_after + 1)
                            locked_n = len(_locked)
                            if locked_n:
                                _locked_urls = {u for _i, u in _locked}
                                new_jobs_info = [i for i in new_jobs_info if i.get("url") not in _locked_urls]
                                inserted -= locked_n
                                print(f"[run-search] user {user_id}: {locked_n} match(es) beyond the "
                                      f"'{entitlements.tier_of(dict(_plan_user))}' plan locked")
                        except Exception as _lk:
                            print(f"[run-search] ERROR could not apply the plan's match limit: {_lk}")
                        # ── Daily picks (2026-11-12): this search's jobs go to Noa's
                        # reserve, then today's picks come out of it, best first, up to
                        # the plan's matches_per_day. What this run shows the user is
                        # what was released now. A failure here releases everything.
                        held_n = 0
                        try:
                            _dp_user = dict(conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())
                            if entitlements.daily_cap(_dp_user) is not None:
                                entitlements.hold_new_matches(conn, _dp_user, _batch_after)
                                _released = entitlements.release(conn, _dp_user)
                                _new_ids = {r[0] for r in conn.execute(
                                    "SELECT id FROM jobs WHERE user_id=? AND id>? AND status=?",
                                    (user_id, _batch_after, entitlements.HELD)).fetchall()}
                                held_n = len(_new_ids)
                                # what the user gets now = the released jobs, whichever search found them
                                new_jobs_info = [{"title": r["title"], "company": r["company"], "url": r["url"],
                                                  "url_ok": r["url_verified"]} for r in (conn.execute(
                                    ("SELECT title, company, url, url_verified FROM jobs WHERE id IN (%s) "
                                     % ",".join("?" * len(_released))) + "ORDER BY " + entitlements._RANK + " DESC",
                                    [i for i, _u in _released]).fetchall()
                                    if _released else [])]
                                inserted = len(_released)
                                print(f"[run-search] user {user_id}: daily picks - {inserted} released, "
                                      f"{held_n} new job(s) kept in the reserve")
                        except Exception as _dp:
                            print(f"[run-search] ERROR daily picks failed, releasing this search: {_dp}")
                            try:
                                conn.execute("UPDATE jobs SET status='new' WHERE user_id=? AND id>? AND status=?",
                                             (user_id, _batch_after, entitlements.HELD))
                                conn.commit()
                            except Exception as _dp2:
                                print(f"[run-search] ERROR could not release held jobs: {_dp2}")
                        conn.close()

                        # ── URL check ALL historical unverified jobs ─────────────────────
                        conn = database.get_db()
                        try:
                            unverified = conn.execute(
                                "SELECT id, url, status FROM jobs WHERE user_id=? AND url_verified IS NULL AND url!=''", (user_id,)
                            ).fetchall()
                            conn.close()
                            hist_alive = hist_dead = 0
                            if unverified:
                                # _link_verdict, not check_url_alive: a rate limit or a
                                # bot-block is 'unknown' and must not count as dead.
                                with _TPE(max_workers=8) as _ex2:
                                    _futs2 = {_ex2.submit(_link_verdict, r["url"]): r for r in unverified}
                                    hist_results = {}
                                    for _f2, _r2 in _futs2.items():
                                        try:    hist_results[_r2["id"]] = (_r2, _f2.result(timeout=20))
                                        except Exception: hist_results[_r2["id"]] = (_r2, ("unknown", "timeout"))
                                _autoreject_h = os.environ.get("LINK_AUTOREJECT_DEAD", "1").strip().lower() not in ("0", "false", "no", "off")
                                conn = database.get_db()
                                try:
                                    for job_id, (_hr, (_hst, _hwhy)) in hist_results.items():
                                        if _hst == "alive":
                                            conn.execute("UPDATE jobs SET url_verified=1, url_check_date=? WHERE id=?", (_chk_date, job_id))
                                            hist_alive += 1
                                        elif _hst == "dead":
                                            hist_dead += 1
                                            # /app shows a url_verified=0 job like any other, so a
                                            # flag alone still put dead postings in front of him.
                                            if _autoreject_h and (_hr["status"] or "") in ("new", "approved"):
                                                conn.execute(
                                                    "UPDATE jobs SET status='rejected', rejected_by='system', url_verified=0, "
                                                    "url_check_date=?, notes=COALESCE(notes,'') || ? WHERE id=?",
                                                    (_chk_date, " [auto-removed: link dead/closed - %s]" % _hwhy[:80], job_id))
                                            else:
                                                conn.execute("UPDATE jobs SET url_verified=0, url_check_date=? WHERE id=?", (_chk_date, job_id))
                                    conn.commit(); conn.close()
                                finally:
                                    conn.close()

                            # ── Re-validate already-"Verified" rows (catch parked/expired domains) ──
                            # Conservative: only DEMOTE a verified row to dead when _link_status proves
                            # it is definitively gone (404/410/dead-DNS/parked). Alive/unknown stay 1,
                            # so a transient bot-block never nukes a good posting.
                            conn = database.get_db()
                            try:
                                prev_ok = conn.execute(
                                    "SELECT id, url, status FROM jobs WHERE user_id=? AND url_verified=1 AND url!=''", (user_id,)
                                ).fetchall()
                                conn.close()
                                reval_dead = 0
                                reval_removed = 0
                                if prev_ok:
                                    _autoreject = os.environ.get("LINK_AUTOREJECT_DEAD", "1").strip().lower() not in ("0", "false", "no", "off")
                                    with _TPE(max_workers=8) as _ex3:
                                        _futs3 = {_ex3.submit(_link_verdict, r["url"]): r for r in prev_ok}
                                        _demote = []
                                        for _f3, _r3 in _futs3.items():
                                            try:    _st, _why3 = _f3.result(timeout=20)
                                            except Exception: _st, _why3 = "unknown", "timeout"
                                            if _st == "dead":
                                                _demote.append((_r3, _why3))
                                    if _demote:
                                        conn = database.get_db()
                                        try:
                                            for _r, _why in _demote:
                                                _jid = _r["id"]; _rst = (_r["status"] or "")
                                                # A definitively-dead link in the REVIEW queue is useless — move it
                                                # out so the owner stops seeing expired postings. Applied/rejected rows
                                                # are only re-flagged (url_verified=0), never re-statused.
                                                if _autoreject and _rst in ("new", "approved"):
                                                    conn.execute(
                                                        "UPDATE jobs SET status='rejected', url_verified=0, url_check_date=?, "
                                                        "rejected_by='system', "
                                                        "notes=COALESCE(notes,'') || ? WHERE id=?",
                                                        (_chk_date, " [auto-removed: link dead/closed - %s]" % _why[:80], _jid))
                                                    reval_removed += 1
                                                else:
                                                    conn.execute("UPDATE jobs SET url_verified=0, url_check_date=? WHERE id=?", (_chk_date, _jid))
                                            conn.commit(); conn.close()
                                            reval_dead = len(_demote)
                                        finally:
                                            conn.close()

                                if _funnel(user_id, "first_search"):
                                    _first_matches_email(user_id, inserted)
                                database.log_activity(user_id, "jobs_searched", f"Found {inserted} new job(s) across {len(titles)} title search(es) ({skipped_rej} rejected-pattern matches skipped)"
                                                      + (f"; {locked_n} more need a higher plan" if locked_n else "")
                                                      + (f"; {held_n} kept for the coming days" if held_n else ""))

                                # ── Consolidated search notification ─────────────────────────────
                                # The FIRST line is what the phone push banner shows, so summarize the
                                # result there (count + top companies) instead of just "Search Complete".
                                if inserted > 0:
                                    _uniq_co = []
                                    for _ci in new_jobs_info:
                                        _cco = (_ci.get("company") or "").strip()
                                        if _cco and _cco not in _uniq_co:
                                            _uniq_co.append(_cco)
                                    _co_str = ", ".join(_uniq_co[:4]) + ("…" if len(_uniq_co) > 4 else "")
                                    _headline = f"🎯 {inserted} new role(s) for review"
                                    if _co_str:
                                        _headline += f" — {_co_str}"
                                    notif_lines = [_headline, ""]
                                    for info in new_jobs_info[:10]:
                                        icon = "🔗" if info["url_ok"]==1 else ("⚠️" if info["url_ok"]==0 else "")
                                        notif_lines.append(f"  • {info['title']} @ {info['company']} {icon}".rstrip())
                                    if len(new_jobs_info)>10: notif_lines.append(f"  … and {len(new_jobs_info)-10} more")
                                elif locked_n:
                                    notif_lines = [f"🔒 {locked_n} new match(es) found — upgrade your plan to see them"]
                                elif held_n:
                                    notif_lines = ["✨ Noa found more matches for you. You've had today's picks; "
                                                   "the next ones come in tomorrow."]
                                else:
                                    notif_lines = [f"🔍 Search done — no new matches today ({today})"]
                                if inserted > 0 and locked_n:
                                    notif_lines.append(f"\n🔒 +{locked_n} more found — upgrade your plan to see them")
                                if (hist_alive+hist_dead)>0:
                                    notif_lines.append(f"\n🔄 Re-checked {hist_alive+hist_dead} existing job URL(s):")
                                    notif_lines.append(f"  ✅ {hist_alive} alive  ❌ {hist_dead} dead")
                                if reval_dead>0:
                                    _rmsg = f"⚠️ {reval_dead} previously-'Verified' link(s) now dead (parked/closed/expired)"
                                    if reval_removed>0:
                                        _rmsg += f" — {reval_removed} auto-removed from review"
                                    notif_lines.append(_rmsg)
                                try:
                                    deliver_notification(user_id, "\n".join(notif_lines), url_suffix=home_url() + "#new")
                                except Exception as _dn_err:
                                    print(f"[run-search] deliver_notification error: {_dn_err}")
                                print(f"[run-search] user {user_id}: inserted={inserted} hist_checked={hist_alive+hist_dead}")
                            finally:
                                conn.close()
                        finally:
                            conn.close()
                    finally:
                        conn.close()
                finally:
                    conn.close()
            finally:
                conn.close()
        finally:
            conn.close()

    except Exception as e:
        import traceback as _tb
        _tb_str = _tb.format_exc()
        print(f"[run-search] Error: {type(e).__name__}: {e}\n{_tb_str}")
        err_summary = f"{type(e).__name__}: {str(e)[:120]}"
        database.log_activity(user_id, "jobs_searched", f"Job search failed — {err_summary}")
        try:
            deliver_notification(user_id, f"\u274c Job search failed\n\n{err_summary}\n\nWill retry at next scheduled time.", url_suffix=home_url())
        except Exception as _ne:
            print(f"[run-search] Notification also failed: {_ne}")
    finally:
        _search_running.discard(user_id)
        # Step 1b: give the new (and still-open) jobs the company's own link.
        # Its own queued run, so a slow ATS never lengthens the search itself.
        try:
            jobqueue.enqueue(user_id, "links", cap=False)
        except Exception as _lq:
            print(f"[run-search] could not queue link resolve for user {user_id}: {_lq}")


@_attribute_gemini_to
def run_link_resolve(user_id: int):
    """Worker handler for kind 'links' (links.resolve_open_jobs)."""
    import links as _links
    # Gemini slug guesses are OFF by default: each one counts against the
    # user's daily Gemini ceiling, and a first backfill of a full queue could
    # crowd out their scoring. JH_LINKS_LLM=1 turns them on once the link
    # report shows they earn their cost.
    use_llm = (os.environ.get("JH_LINKS_LLM", "0") or "0").strip().lower() in ("1", "true", "yes", "on")
    # Option 3, the web search for the posting (The owner, 2026-11-07). Per run: a
    # tenth of the user's daily AI calls, at most JH_LINKS_WEBSEARCH_PER_RUN
    # (default 30) - a Free plan's 30 calls a day must still score its jobs.
    try:
        _ws_max = int(os.environ.get("JH_LINKS_WEBSEARCH_PER_RUN", "30") or 0)
    except ValueError:
        _ws_max = 30
    _ai = _plan_ai_cap(user_id)
    _ws_cap = _ws_max if _ai is None or _ai >= 10 * _ws_max else max(0, min(_ws_max, int(_ai) // 10))
    counts = _links.resolve_open_jobs(database.get_db, user_id, use_llm=use_llm,
                                      searcher=_links.search_company_posting, search_cap=_ws_cap)
    detail = _links.run_detail(counts)
    print(f"[links] user {user_id}: {detail}")
    if counts.get("left_for_next_run"):
        # A backfill bigger than one run's budget carries on in 5 minutes
        # rather than waiting for tomorrow's search.
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        try:
            jobqueue.enqueue(user_id, "links", cap=False, dedupe=False,
                             run_after=_links._utc_stamp(_dt.now(_tz.utc) + _td(minutes=5)))
        except Exception as _lq:
            print(f"[links] could not queue the follow-up run for user {user_id}: {_lq}")
    return detail

import concurrent.futures as _cf

# Cap concurrent browser-based applies. Each submit_application launches a
# headless Chromium; without a cap, approving several jobs at once spawns many
# Chromium processes, exhausts host memory, and wedges every apply in the
# 'applying' state (the frozen spinner). 2 keeps memory bounded.
_APPLY_SEM = threading.Semaphore(int(os.environ.get("APPLY_MAX_CONCURRENT", "2")))
# Single shared pool used purely to impose a hard wall-clock deadline on each
# apply. Orphaned timed-out tasks keep running but are bounded by Playwright's
# own per-step + launch timeouts, so they die on their own.
_APPLY_POOL = _cf.ThreadPoolExecutor(max_workers=4)
_APPLY_DEADLINE_S = int(os.environ.get("APPLY_DEADLINE_S", "150"))


def _submit_application_guarded(job_url, job_title, company, applicant, cv_path, api_key="", job_location="",
                                user_id=None, job_id=None):
    """Run apply_engine.submit_application with a concurrency cap and an overall
    wall-clock deadline so a single apply can never hang the job forever.

    With user_id and job_id the apply can stop for an emailed security code
    (2026-11-08): the user is notified and has _CODE_WAIT_S to paste it, and
    that waiting does not count against the deadline - otherwise the engine
    would be abandoned mid-wait and could still submit after the job was
    marked failed."""
    import apply_engine  # imported lazily (module is imported locally elsewhere)
    run = None
    if user_id is not None and job_id is not None:
        cv_path = _cv_for_apply(user_id, job_id, cv_path)
        run = _new_apply_run(user_id, job_id, job_title, company)
        applicant = {**(applicant or {}), "_await_code": _await_code_for(run, notify=True)}
    # The engine stops itself 15 s before this deadline, unsent (2026-11-12):
    # it used to run on after the deadline and could still press Submit.
    applicant = {**(applicant or {}), "_deadline_s": max(30, _APPLY_DEADLINE_S - 15)}
    try:
        with _APPLY_SEM:
            fut = _APPLY_POOL.submit(
                apply_engine.submit_application,
                job_url, job_title, company, applicant, cv_path, api_key, job_location,
            )
            t0 = time.time()
            while True:
                try:
                    return fut.result(timeout=2)
                except _cf.TimeoutError:
                    if time.time() - t0 - (_run_waited(run) if run else 0) <= _APPLY_DEADLINE_S:
                        continue
                print(f"[apply] DEADLINE exceeded ({_APPLY_DEADLINE_S}s) for {job_title} @ {company}")
                # The engine thread is NOT stopped here and may still press
                # Submit, so this is "unconfirmed" (never auto-retried), not
                # "timeout": a retry could send the application twice
                # (2026-11-09).
                return {
                    "success": False, "status": "failed",
                    "confirmation_text": "",
                    "error": (f"Stopped waiting after {_APPLY_DEADLINE_S}s - it may have been sent. "
                              "Check the company's site or your email before applying again."),
                    "apply_failure_type": "unconfirmed",
                    "apply_failure_detail": f"Overall apply deadline ({_APPLY_DEADLINE_S}s) exceeded",
                    "resolved_url": job_url,
                }
    finally:
        if run is not None:
            run["state"] = "done"


def _supervised_live_apply(run, user, job, applicant, _cv_path, _jloc, out):
    """Background body of a supervised LIVE apply (one job, past the kill
    switch). Stores the page's view of the result on the run."""
    import apply_engine as _ae
    try:
        applicant["_await_code"] = _await_code_for(run)
        _cv_path = _cv_for_apply(user["id"], job["id"], _cv_path)
        res = _ae.submit_application(
            job["url"] or "", job["title"], job["company"], applicant, _cv_path,
            GEMINI_KEY, _jloc, True,   # api_key, job_location, force=True
        )
        out["result"] = {
            "status": res.get("status"), "success": res.get("success"),
            "resolved_url": res.get("resolved_url"),
            "confirmation_text": (res.get("confirmation_text") or "")[:500],
            "error": (res.get("error") or "")[:500],
            "apply_failure_type": res.get("apply_failure_type"),
            "apply_failure_detail": (res.get("apply_failure_detail") or "")[:500],
            "questions": res.get("questions") or [],
        }
        try:
            _st = res.get("status")
            c3 = database.get_db()
            try:
                if _st in ("submitted", "confirmed") and not (res.get("error") or "").strip():
                    c3.execute(
                        "UPDATE jobs SET status='applied', applied_date=?, apply_status=?, "
                        "apply_confirmation=?, apply_error='', "
                        "apply_attempts=COALESCE(apply_attempts,0)+1 WHERE id=? AND user_id=?",
                        (datetime.now().strftime('%Y-%m-%d'), _st,
                         (res.get('confirmation_text') or '')[:1000], job["id"], user["id"]))
                else:
                    # The held questions go on the job, where the Queue shows them.
                    c3.execute(
                        "UPDATE jobs SET apply_status=?, apply_error=?, apply_questions=?, "
                        "apply_attempts=COALESCE(apply_attempts,0)+1 WHERE id=? AND user_id=?",
                        (_st, (res.get('error') or '')[:500],
                         json.dumps(res["questions"]) if res.get("questions") else None,
                         job["id"], user["id"]))
                c3.commit(); c3.close()
                out["persisted"] = True
            finally:
                c3.close()
        except Exception as _e:
            out["persist_error"] = str(_e)[:200]
    except Exception as exc:
        print(f"[apply] supervised run {run['id']} crashed: {exc}")
        out["result"] = {"status": "failed", "success": False, "error": f"The run crashed: {exc}"[:500]}
    run["out"] = out
    run["state"] = "done"


# Supervised live applies run in the background so the page can hand over the
# security code a site emails after Submit (Greenhouse, 2026-11-08). One
# server instance (STATUS), so an in-process registry is enough.
_APPLY_RUNS: dict = {}
_APPLY_RUNS_LOCK = threading.Lock()
_CODE_WAIT_S = int(os.environ.get("APPLY_CODE_WAIT_S", "600"))


def _new_apply_run(user_id, job_id, title="", company="") -> dict:
    import secrets as _sec
    run = {"id": _sec.token_hex(8), "user_id": user_id, "job_id": job_id, "title": title or "",
           "company": company or "", "state": "running", "out": None, "code": None,
           "code_event": threading.Event(), "code_asked_at": None, "waited": 0.0,
           "started": time.time(), "notified": None}
    with _APPLY_RUNS_LOCK:
        for k in [k for k, r in _APPLY_RUNS.items() if time.time() - r["started"] > 3600]:
            _APPLY_RUNS.pop(k, None)
        _APPLY_RUNS[run["id"]] = run
    return run


def _set_job_apply_status(run, status, only_if=None):
    try:
        c = database.get_db()
        try:
            sql = "UPDATE jobs SET apply_status=? WHERE id=? AND user_id=?"
            args = [status, run["job_id"], run["user_id"]]
            if only_if:
                sql += " AND apply_status=?"
                args.append(only_if)
            c.execute(sql, tuple(args))
            c.commit()
        finally:
            c.close()
    except Exception as exc:
        print(f"[apply] could not mark job {run['job_id']} {status}: {exc}")


def _await_code_for(run, notify=False):
    """The engine's applicant["_await_code"]: a site emailed a security code.
    Marks the job "awaiting_code", tells the user (push and their channels,
    with a link that opens the code prompt), waits up to _CODE_WAIT_S for it.
    The supervised admin run passes notify=False: the admin is on the page."""
    def wait():
        # Order matters: the job is marked first, then the run is listed as
        # waiting (the app shows it from then on), then the user is told - so
        # whatever they open already agrees. Listing first raced the job's
        # status (seen on the SQLite smoke run, 2026-11-08).
        _set_job_apply_status(run, "awaiting_code")
        run["code_asked_at"] = time.time()
        run["state"] = "awaiting_code"
        if notify:
            who = run["company"] or "The employer"
            what = (" for " + run["title"]) if run["title"] else ""
            try:
                run["notified"] = deliver_notification(
                    run["user_id"],
                    f"\U0001F510 {who} emailed you a security code{what}. Open Job Hunter within "
                    f"{_CODE_WAIT_S // 60} minutes and paste it to send your application.",
                    url_suffix="/app", push_url=MOBILE_URL.rstrip("/") + "/app?finish=" + run["id"])
            except Exception as exc:
                print(f"[apply] code notification failed for run {run['id']}: {exc}")
        t0 = time.time()
        got = run["code_event"].wait(_CODE_WAIT_S)
        run["waited"] += time.time() - t0
        run["state"] = "running"
        _set_job_apply_status(run, "applying", only_if="awaiting_code")
        return run["code"] if got else None
    return wait


def _run_waited(run) -> float:
    """Seconds this run has spent waiting for a code (including right now)."""
    w = run.get("waited", 0.0)
    if run.get("state") == "awaiting_code" and run.get("code_asked_at"):
        w += time.time() - run["code_asked_at"]
    return w


def _apply_run_view(run) -> dict:
    v = {"run_id": run["id"], "state": run["state"], "job_id": run["job_id"],
         "title": run.get("title", ""), "company": run.get("company", "")}
    if run["state"] == "awaiting_code":
        v["code_seconds_left"] = max(0, int(_CODE_WAIT_S - (time.time() - (run["code_asked_at"] or time.time()))))
    if run["state"] == "done":
        v.update(run["out"] or {})
    return v


def _apply_failure_fields(failure_type, attempts_after, engine_status=None):
    """Pick (apply_status, apply_next_attempt_at) for a failed apply using the
    retry policy: route manual failures to manual_required, cap attempts, and
    schedule exponential backoff for transient failures."""
    import apply_engine as _ae
    if engine_status == "manual_required":
        return ("manual_required", None)
    if engine_status == "needs_answers":
        return ("needs_answers", None)        # held for the user, never retried on its own
    if engine_status == "needs_code":
        # The site emailed a security code and nobody entered it in time. Held
        # for the user's "Apply now" - an automatic retry would only send more
        # code emails (2026-11-08).
        return ("needs_code", None)
    action, backoff = _ae.retry_decision(failure_type, attempts_after)
    if action == "manual":
        return ("manual_required", None)
    if action == "giveup":
        return ("failed", None)
    from datetime import timedelta as _td
    return ("failed", (datetime.now() + _td(seconds=backoff)).isoformat())


@_attribute_gemini_to
def run_job_apply(user_id: int) -> int:
    """Submit applications to all approved jobs using browser automation + Claude."""
    # Global kill-switch: apply engine disabled by default (2026-07-20).
    # Admins only until APPLY_ENGINE_FOR_ALL (2026-11-09).
    if not _engine_on_for(user_id):
        return {"applied": 0, "error": "", "skipped": "apply_engine_disabled"}
    # ── Rate-limit + auto_apply gate ──────────────────────────────────────────
    from datetime import timezone as _tz
    today_utc = datetime.now(_tz.utc).strftime("%Y-%m-%d")
    _rc = database.get_db()
    try:
        _prof = _rc.execute(
            "SELECT auto_apply_enabled, applications_sent_today, applications_reset_date "
            "FROM user_profiles WHERE user_id=?", (user_id,)
        ).fetchone()
        _urow = _rc.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        _is_admin = (_urow and _urow["email"] and _urow["email"].lower() == (ADMIN_EMAIL or "").lower())

        # SILENT MODE: auto-apply off => return immediately, no notification
        if not _prof or not _prof["auto_apply_enabled"]:
            _rc.close()
            return {"applied": 0, "error": "", "skipped": "auto_apply_disabled"}

        # The paid gate, AT THE CHOKEPOINT. It used to live only at the two call
        # sites a developer exercises by hand - saving the toggle (/api/save-profile)
        # and the apply triggered by approving a job - and this function, which every
        # scheduled run and every /api/run-apply goes through, checked the FLAG and
        # not the PLAN. The flag and the plan come apart on their own: m0010 gave
        # every existing row plan='free' while leaving auto_apply_enabled as it was,
        # and a downgrade does the same thing later. Proven 2026-09-20 against a
        # plan='free' user with the flag set: can_auto_apply False, run_job_apply
        # returned applied=1. Invisible until now only because APPLY_ENGINE_ENABLED
        # is off in production - i.e. exactly until the thing we are launching.
        if not entitlements.can_auto_apply(_urow):
            _rc.close()
            print(f"[apply] user {user_id}: auto-apply flag set but plan "
                  f"'{entitlements.plan_of(_urow)}' does not include it - not applying")
            return {"applied": 0, "error": "", "skipped": "not_entitled"}

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
            try:
                jobs = conn.execute(
                    "SELECT id, title, company, url, COALESCE(apply_attempts,0) AS attempts "
                    "FROM jobs WHERE user_id=? AND status='approved' "
                    "AND COALESCE(apply_status,'') NOT IN ('manual_required','needs_answers','needs_code','awaiting_code') "
                    "AND COALESCE(apply_attempts,0) < ? "
                    "AND (apply_next_attempt_at IS NULL OR apply_next_attempt_at <= ?)",
                    (user_id, apply_engine.MAX_APPLY_ATTEMPTS, datetime.now().isoformat())
                ).fetchall()

                # Cap jobs to daily remaining quota (non-admin only)
                if _remaining is not None:
                    jobs = jobs[:_remaining]

                # First-live-run safety valve: bound applies per RUN even for admin, so a
                # freshly re-enabled engine can't fire dozens of real submits at once.
                # Raise/disable with Railway env APPLY_MAX_PER_RUN (0 = unlimited).
                try:
                    _per_run = int(os.environ.get("APPLY_MAX_PER_RUN", "5") or "5")
                except ValueError:
                    _per_run = 5
                if _per_run > 0:
                    jobs = jobs[:_per_run]

                if not jobs:
                    conn.close()
                    return {"applied": 0, "error": ""}

                # Gather user + CV data for form filling
                user    = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
                profile = conn.execute(
                    "SELECT cv_summary, cv_path FROM user_profiles WHERE user_id=?", (user_id,)
                ).fetchone()
                conn.close()

                email     = user["email"] if user else ""
                applicant = _applicant_for(user_id, email, (user["name"] if user else ""),
                                           (profile["cv_path"] if profile else ""),
                                           (profile["cv_summary"] if profile else ""))

                # The apply engine hands this path to a browser file input, so the file
                # has to exist on disk - rebuilt from the database if the volume is cold.
                cv_path = _cv_file(user_id, (profile["cv_path"] or "") if profile else "") or None

                today = datetime.now().strftime("%Y-%m-%d")
                count = 0
                confirmed_list, submitted_list, manual_list, failed_list = [], [], [], []

                for j in jobs:
                    job_url = j["url"] or ""
                    res = {}                 # the no-URL branch below has no engine result
                    if job_url:
                        # Shown as "Applying…" while it is being sent (The owner,
                        # 2026-11-09: "the user will see a real indication about
                        # the status"). A restart mid-send is reset at boot.
                        _mk = database.get_db()
                        try:
                            _mk.execute("UPDATE jobs SET apply_status='applying' WHERE id=? AND user_id=?",
                                        (j["id"], user_id))
                            _mk.commit()
                        finally:
                            _mk.close()
                        res = _submit_application_guarded(
                            job_url, j["title"], j["company"],
                            applicant, cv_path, GEMINI_KEY,
                            (j["location"] if "location" in j.keys() else ""),
                            user_id=user_id, job_id=j["id"],
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
                    try:
                        if apply_status in ("submitted", "confirmed") and not (apply_error or "").strip():
                            c2.execute(
                                "UPDATE jobs SET status='applied', applied_date=?, notes=?, "
                                "apply_status=?, apply_confirmation=?, apply_error=?, "
                                "apply_failure_type=?, apply_failure_detail=?, "
                                # Provenance, set at the moment of truth. Backfilling this
                                # from note strings worked once (migration 9) and must
                                # never be needed again.
                                "applied_via=CASE WHEN ?='' THEN 'no_url' ELSE 'engine' END, "
                                "apply_attempts=COALESCE(apply_attempts,0)+1 "
                                "WHERE id=? AND user_id=?",
                                (today, notes, apply_status, apply_confirmation,
                                 apply_error, apply_failure_type, apply_failure_detail,
                                 job_url, j["id"], user_id)
                            )
                        else:
                            # Failed — keep status='approved' so user can retry
                            _attempts_after = (j["attempts"] or 0) + 1
                            _fstatus, _next_at = _apply_failure_fields(apply_failure_type, _attempts_after, apply_status)
                            c2.execute(
                                "UPDATE jobs SET notes=?, "
                                "apply_status=?, apply_error=?, "
                                "apply_failure_type=?, apply_failure_detail=?, "
                                "apply_attempts=COALESCE(apply_attempts,0)+1, apply_next_attempt_at=?, "
                                "apply_questions=?, apply_prepared=? "
                                "WHERE id=? AND user_id=?",
                                (notes, _fstatus, apply_error, apply_failure_type, apply_failure_detail, _next_at,
                                 json.dumps(res.get("questions")) if res.get("questions") else None,
                                 json.dumps(res.get("prepared")) if res.get("prepared") else None,
                                 j["id"], user_id)
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
                            try:
                                _uc.execute("UPDATE user_profiles SET applications_sent_today = applications_sent_today + 1 WHERE user_id=?", (user_id,))
                                _uc.commit()
                                _uc.close()
                            finally:
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
                    finally:
                        c2.close()

                # ── Notifications ────────────────────────────────────────────────────────────────────────────────
                # ── Single consolidated apply notification ──────────────────────────────
                today_str = datetime.now().strftime("%Y-%m-%d")
                _real_submits = len(confirmed_list) + len(submitted_list)
                if _real_submits > 0:
                    _headline = f"📊 {_real_submits} auto-submitted · {len(manual_list)} need manual · {len(failed_list)} failed\n"
                elif manual_list:
                    _headline = f"📊 0 auto-submitted — {len(manual_list)} job(s) need manual apply (job-board listings)\n"
                else:
                    _headline = f"📊 {count} job(s) processed\n"
                notif_lines = [f"🚀 Apply Run Complete — {today_str}", _headline]
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
                deliver_notification(user_id, "\n".join(notif_lines), url_suffix=home_url() + "#applied")
                print(f"[run-apply] user {user_id}: {count} — confirmed={len(confirmed_list)} submitted={len(submitted_list)} manual={len(manual_list)} failed={len(failed_list)}")
                return {"applied": count, "error": ""}
            finally:
                conn.close()

        except Exception as e:
            print(f"[run-apply] Error: {e}")
            import traceback; traceback.print_exc()
            return {"applied": 0, "error": str(e)}
    finally:
        _rc.close()


# ── Immediate single-job apply (triggered on approval) ───────────────────────

def _trigger_apply_bg(user_id: int, job_id: int):
    """Fire-and-forget: apply to a single job in a background thread.
    Called immediately when a user approves a job so they don't wait for the
    scheduled batch window.
    """
    threading.Thread(target=_apply_one, args=(user_id, job_id), daemon=True).start()


_SELECTED_MAX = 15


def _apply_selected_bg(user_id: int, job_ids: list):
    """The queue's "Auto-apply selected" (The owner, 2026-11-09): the jobs one after
    another in one thread - so a selection of ten is not ten browsers waiting
    on the engine's semaphore - each exactly as Apply now would send it."""
    def _run():
        for jid in job_ids:
            try:
                _apply_one(user_id, jid)
            except Exception as exc:          # _apply_one catches its own; never stop the batch
                print(f"[apply-selected] job {jid} error: {exc}")
        print(f"[apply-selected] user {user_id}: {len(job_ids)} done")
    threading.Thread(target=_run, daemon=True).start()


def _apply_one(user_id: int, job_id: int):
    """One job through the engine and its result onto the row. The thread body
    of _trigger_apply_bg, and each step of _apply_selected_bg.

    The SELECT now carries location: the posting location the engine uses for a
    sponsorship question was always "" here, because `"location" in job.keys()`
    was asked of a row that never selected it (found 2026-11-09)."""
    try:
        import apply_engine
        if not _engine_on_for(user_id):
            return  # engine off, or not yet open to this user (2026-11-09)
        conn = database.get_db()
        try:
            job  = conn.execute(
                "SELECT id, title, company, url, location, apply_status, COALESCE(apply_attempts,0) AS apply_attempts FROM jobs "
                "WHERE id=? AND user_id=?", (job_id, user_id)
            ).fetchone()
            if not job:
                conn.close()
                return
            # Guard: already applied or currently in flight
            if job["apply_status"] in ("applying", "confirmed", "submitted"):
                conn.close()
                return

            # Mark as 'applying' so the UI shows a spinner
            conn.execute(
                "UPDATE jobs SET apply_status='applying' WHERE id=? AND user_id=?",
                (job_id, user_id)
            )
            conn.commit()

            user    = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            profile = conn.execute(
                "SELECT cv_summary, cv_path FROM user_profiles WHERE user_id=?", (user_id,)
            ).fetchone()
            conn.close()

            email     = user["email"] if user else ""
            applicant = _applicant_for(user_id, email, (user["name"] if user else ""),
                                       (profile["cv_path"] if profile else ""),
                                       (profile["cv_summary"] if profile else ""))
            cv_path   = _cv_file(user_id, (profile["cv_path"] or "") if profile else "") or None

            res = _submit_application_guarded(
                job["url"], job["title"], job["company"],
                applicant, cv_path, GEMINI_KEY,
                (job["location"] if "location" in job.keys() else ""),
                user_id=user_id, job_id=job_id,
            )

            apply_status         = res["status"]
            apply_confirmation   = res.get("confirmation_text", "")[:1000]
            apply_error          = res.get("error", "")[:500]
            apply_failure_type   = res.get("apply_failure_type")
            apply_failure_detail = (res.get("apply_failure_detail") or "")[:300]
            resolved_url         = res.get("resolved_url", job["url"])
            today                = datetime.now().strftime("%Y-%m-%d")

            c2 = database.get_db()
            try:
                if apply_status in ("submitted", "confirmed") and not (apply_error or "").strip():
                    c2.execute(
                        "UPDATE jobs SET status='applied', applied_date=?, notes=?, "
                        "apply_status=?, apply_confirmation=?, apply_error=?, "
                        "apply_failure_type=?, apply_failure_detail=?, "
                        # Provenance, set at the moment of truth. Backfilling this
                        # from note strings worked once (migration 9) and must
                        # never be needed again.
                        "applied_via=CASE WHEN ?='' THEN 'no_url' ELSE 'engine' END, "
                        "apply_attempts=COALESCE(apply_attempts,0)+1 "
                        "WHERE id=? AND user_id=?",
                        (today, f"Applied via Job Hunter — {apply_status}",
                         apply_status, apply_confirmation, apply_error,
                         apply_failure_type, apply_failure_detail,
                         job["url"] or "", job_id, user_id)
                    )
                else:
                    # Failed / manual_required — keep status='approved' so user can retry
                    _attempts_after = (job["apply_attempts"] or 0) + 1
                    _fstatus, _next_at = _apply_failure_fields(apply_failure_type, _attempts_after, apply_status)
                    c2.execute(
                        "UPDATE jobs SET apply_status=?, apply_error=?, "
                        "apply_failure_type=?, apply_failure_detail=?, "
                        "apply_attempts=COALESCE(apply_attempts,0)+1, apply_next_attempt_at=?, "
                        "apply_questions=?, apply_prepared=? "
                        "WHERE id=? AND user_id=?",
                        (_fstatus, apply_error, apply_failure_type,
                         apply_failure_detail, _next_at,
                         json.dumps(res.get("questions")) if res.get("questions") else None,
                         json.dumps(res.get("prepared")) if res.get("prepared") else None,
                         job_id, user_id)
                    )
                c2.commit()
                c2.close()

                database.log_activity(
                    user_id, "job_applied",
                    f"{job['title']} @ {job['company']} — {apply_status}"
                    + (f" (via {resolved_url})" if resolved_url != job['url'] else "")
                )
                print(f"[apply-bg] job {job_id}: {apply_status}")
            finally:
                c2.close()
        finally:
            conn.close()

    except Exception as e:
        import traceback
        print(f"[apply-bg] job {job_id} error: {e}")
        traceback.print_exc()
        # Make sure we don't leave the job stuck in 'applying'
        try:
            c3 = database.get_db()
            try:
                c3.execute(
                    "UPDATE jobs SET apply_status='failed', apply_error=? "
                    "WHERE id=? AND user_id=? AND apply_status IN ('applying','awaiting_code')",
                    (str(e)[:500], job_id, user_id)
                )
                c3.commit()
                c3.close()
            finally:
                c3.close()
        except Exception:
            pass



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


# ── Auth pages ────────────────────────────────────────────────────────────────

_AUTH_HEAD = """
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover"/>
  <meta name="theme-color" content="#0f172a"/>
  <style>
    /* Deliberately self-contained: this page links no external stylesheet.
       These two pages are the first thing a stranger sees, and the legacy
       Tailwind file they used to link is a frozen gzip+base64 blob in app.py
       that no build step regenerates - so the sign-in screen was the one
       surface whose look depended on an artifact nobody can rebuild. The
       palette is /app's, matched by value. */
    *, *::before, *::after { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
    body { margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
           padding:1.25rem; color:#e5e7eb; background:#0f172a;
           background-image: radial-gradient(60rem 40rem at 15% -10%, rgba(99,102,241,.18), transparent 60%),
                             radial-gradient(50rem 40rem at 110% 110%, rgba(56,89,248,.14), transparent 60%);
           font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; }
    .wrap { width:100%; max-width:26rem; }
    .brand { text-align:center; margin-bottom:1.75rem; }
    .mark { width:3.5rem; height:3.5rem; margin:0 auto .9rem; border-radius:1rem;
            display:flex; align-items:center; justify-content:center;
            background:linear-gradient(135deg,#6366f1,#4338ca); box-shadow:0 10px 30px rgba(79,70,229,.35); }
    .mark svg { width:1.65rem; height:1.65rem; stroke:#fff; }
    .brand h1 { margin:0; font-size:1.6rem; font-weight:700; color:#fff; letter-spacing:-.01em; }
    .brand p { margin:.35rem 0 0; font-size:.85rem; color:#94a3b8; }
    .card { background:#1f2937; border:1px solid #374151; border-radius:1.25rem; padding:1.75rem;
            box-shadow:0 24px 60px rgba(0,0,0,.45); }
    .card h2 { margin:0 0 1.25rem; font-size:1.05rem; font-weight:650; color:#fff; }
    label { display:block; font-size:.78rem; font-weight:500; color:#9ca3af; margin-bottom:.4rem; }
    .field { margin-bottom:.9rem; }
    input { width:100%; padding:.75rem 1rem; font-size:.9rem; color:#fff;
            /* the border IS the field boundary here - #374151 on #111827 is 1.29:1 */
            background:#111827; border:1px solid #6b7280; border-radius:.75rem; outline:none; }
    input::placeholder { color:#9ca3af; }   /* AA on the field fill; #6b7280 was 3.67:1 */
    input:focus { border-color:#6366f1; box-shadow:0 0 0 3px rgba(99,102,241,.25); }
    .pw { position:relative; }
    .pw input { padding-right:3rem; }
    .reveal { position:absolute; right:.4rem; top:50%; transform:translateY(-50%);
              background:none; border:0; color:#9ca3af; cursor:pointer; padding:.5rem;
              font-size:.72rem; font-weight:600; }
    .reveal:hover { color:#e5e7eb; }
    button.primary { width:100%; margin-top:.35rem; padding:.85rem 1rem; font-size:.92rem; font-weight:600;
                     color:#fff; border:0; border-radius:.75rem; cursor:pointer;
                     background:linear-gradient(90deg,#4f46e5,#4338ca); }
    button.primary:hover { filter:brightness(1.08); }
    button.primary:active { transform:scale(.99); }
    .google { display:flex; align-items:center; justify-content:center; gap:.65rem; width:100%;
              padding:.75rem 1rem; font-size:.9rem; font-weight:600; color:#e5e7eb; text-decoration:none;
              background:#111827; border:1px solid #374151; border-radius:.75rem; }
    .google:hover { background:#374151; }
    .or { display:flex; align-items:center; gap:.75rem; margin:1.15rem 0; }
    .or span { font-size:.72rem; color:#9ca3af; font-weight:500; }  /* #6b7280 was 3.69:1 */
    .or i { flex:1; height:1px; background:#374151; }
    .alt { text-align:center; font-size:.85rem; color:#9ca3af; margin:1.4rem 0 0; }
    .legal { text-align:center; font-size:.78rem; color:#9ca3af; margin:1rem 0 0; line-height:1.5; }
    .legal a { color:#cbd5e1; }
    .alt a { color:#a5b4fc; font-weight:600; text-decoration:none; }
    .alt a:hover { text-decoration:underline; }
    .err { background:rgba(239,68,68,.12); border:1px solid rgba(239,68,68,.35); color:#fca5a5;
           border-radius:.75rem; padding:.75rem 1rem; font-size:.85rem; margin-bottom:1rem; }
    .ok { background:rgba(34,197,94,.12); border:1px solid rgba(34,197,94,.35); color:#86efac;
          border-radius:.75rem; padding:.75rem 1rem; font-size:.85rem; margin-bottom:1rem; }
    .forgot { display:block; text-align:right; font-size:.78rem; color:#a5b4fc; text-decoration:none; margin:-.4rem 0 .6rem; }
    .forgot:hover { text-decoration:underline; }
    .muted { font-size:.85rem; color:#cbd5e1; line-height:1.5; margin:0 0 1rem; }
  </style>
  <script>
    function revealPw(btn){
      var i = btn.parentNode.querySelector('input');
      var on = i.type === 'password';
      i.type = on ? 'text' : 'password';
      btn.textContent = on ? 'Hide' : 'Show';
      btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    }
  </script>
"""

_MARK = ('<div class="mark"><svg viewBox="0 0 24 24" fill="none" stroke-width="2" '
         'stroke-linecap="round" stroke-linejoin="round">'
         '<rect x="2" y="7" width="20" height="14" rx="2"/>'
         '<path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/></svg></div>')

_GOOGLE_SVG = ('<svg width="18" height="18" viewBox="0 0 18 18" xmlns="http://www.w3.org/2000/svg">'
  '<path fill="#4285F4" d="M17.64 9.2c0-.637-.057-1.251-.164-1.84H9v3.481h4.844a4.14 4.14 0 0 1-1.796 2.716v2.259h2.908c1.702-1.567 2.684-3.875 2.684-6.615z"/>'
  '<path fill="#34A853" d="M9 18c2.43 0 4.467-.806 5.956-2.184l-2.908-2.259c-.806.54-1.837.86-3.048.86-2.344 0-4.328-1.584-5.036-3.711H.957v2.332A8.997 8.997 0 0 0 9 18z"/>'
  '<path fill="#FBBC05" d="M3.964 10.706A5.41 5.41 0 0 1 3.682 9c0-.593.102-1.17.282-1.706V4.962H.957A8.997 8.997 0 0 0 0 9c0 1.452.348 2.827.957 4.038l3.007-2.332z"/>'
  '<path fill="#EA4335" d="M9 3.58c1.321 0 2.508.454 3.44 1.345l2.582-2.58C13.463.891 11.426 0 9 0A8.997 8.997 0 0 0 .957 4.962L3.964 7.294C4.672 5.167 6.656 3.58 9 3.58z"/></svg>')


LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>""" + _AUTH_HEAD + """
  <title>Job Hunter — Sign in</title>
</head>
<body>
<div class="wrap">
  <div class="brand">""" + _MARK + """
    <h1>Job Hunter</h1>
    <p>Your AI-powered job search assistant</p>
  </div>
  <div class="card">
    <h2>Sign in to your account</h2>
    {error_block}
    <a class="google" href="/auth/google/start">""" + _GOOGLE_SVG + """Continue with Google</a>
    <div class="or"><i></i><span>or</span><i></i></div>
    <form method="POST" action="/login">
      <div class="field">
        <label for="email">Email</label>
        <input type="email" name="email" id="email" autocomplete="email" placeholder="you@example.com" required autofocus/>
      </div>
      <div class="field">
        <label for="password">Password</label>
        <div class="pw">
          <input type="password" name="password" id="password" autocomplete="current-password" placeholder="Your password" required/>
          <button type="button" class="reveal" aria-pressed="false" aria-label="Show password" onclick="revealPw(this)">Show</button>
        </div>
      </div>
      <a class="forgot" href="/forgot-password">Forgot password?</a>
      <button type="submit" class="primary">Sign in</button>
    </form>
    <p class="legal">New here? Continuing with Google creates an account, under the <a href="/terms">Terms of Service</a> and <a href="/privacy">Privacy Policy</a>.</p>
    <p class="alt">Don't have an account? <a href="/register">Create one</a></p>
  </div>
</div>
</body>
</html>"""

REGISTER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>""" + _AUTH_HEAD + """
  <title>Job Hunter — Create account</title>
</head>
<body>
<div class="wrap">
  <div class="brand">""" + _MARK + """
    <h1>Job Hunter</h1>
    <p>Let's get you set up</p>
  </div>
  <div class="card">
    <h2>Create your account</h2>
    {error_block}
    <a class="google" href="/auth/google/start">""" + _GOOGLE_SVG + """Continue with Google</a>
    <div class="or"><i></i><span>or</span><i></i></div>
    <form method="POST" action="/register">
      <div class="field">
        <label for="name">Full name</label>
        <input type="text" name="name" id="name" autocomplete="name" placeholder="Jane Cohen" required autofocus/>
      </div>
      <div class="field">
        <label for="email">Email</label>
        <input type="email" name="email" id="email" autocomplete="email" placeholder="you@example.com" required/>
      </div>
      <div class="field">
        <label for="password">Password</label>
        <div class="pw">
          <input type="password" name="password" id="password" autocomplete="new-password" placeholder="At least 8 characters" required minlength="8"/>
          <button type="button" class="reveal" aria-pressed="false" aria-label="Show password" onclick="revealPw(this)">Show</button>
        </div>
      </div>
      <div class="field">
        <label for="password2">Confirm password</label>
        <div class="pw">
          <input type="password" name="password2" id="password2" autocomplete="new-password" placeholder="Type it again" required minlength="8"/>
          <button type="button" class="reveal" aria-pressed="false" aria-label="Show password" onclick="revealPw(this)">Show</button>
        </div>
      </div>
      <button type="submit" class="primary">Create account</button>
    </form>
    <p class="legal">By creating an account you agree to the <a href="/terms">Terms of Service</a> and the <a href="/privacy">Privacy Policy</a>. You must be 18 or older.</p>
    <p class="alt">Already have an account? <a href="/login">Sign in</a></p>
  </div>
</div>
</body>
</html>"""

def notice_block(msg: str) -> str:
    return f"""<div class="ok">{msg}</div>""" if msg else ""


def _auth_page(title: str, heading: str, inner: str) -> str:
    """A small page in the sign-in look (forgot / reset / verify, Step 5)."""
    return ("""<!DOCTYPE html><html lang="en"><head>""" + _AUTH_HEAD + "<title>Job Hunter — " + title + """</title></head>
<body><div class="wrap"><div class="brand">""" + _MARK + """<h1>Job Hunter</h1></div>
<div class="card"><h2>""" + heading + "</h2>" + inner + "</div></div></body></html>")


FORGOT_HTML = _auth_page("Reset your password", "Reset your password", """
    {error_block}
    <p class="muted">Enter the email you signed up with. We'll send you a link to choose a new password.</p>
    <form method="POST" action="/forgot-password">
      <div class="field"><label for="email">Email</label>
        <input type="email" name="email" id="email" autocomplete="email" placeholder="you@example.com" required autofocus/></div>
      <button type="submit" class="primary">Send the link</button>
    </form>
    <p class="alt"><a href="/login">Back to sign in</a></p>""")

RESET_HTML = _auth_page("Choose a new password", "Choose a new password", """
    {error_block}
    <form method="POST" action="/reset-password">
      <input type="hidden" name="token" value="{token}"/>
      <div class="field"><label for="password">New password</label>
        <div class="pw"><input type="password" name="password" id="password" autocomplete="new-password" placeholder="At least 8 characters" required minlength="8" autofocus/>
        <button type="button" class="reveal" aria-pressed="false" aria-label="Show password" onclick="revealPw(this)">Show</button></div></div>
      <div class="field"><label for="password2">Confirm password</label>
        <div class="pw"><input type="password" name="password2" id="password2" autocomplete="new-password" placeholder="Type it again" required minlength="8"/>
        <button type="button" class="reveal" aria-pressed="false" aria-label="Show password" onclick="revealPw(this)">Show</button></div></div>
      <button type="submit" class="primary">Save the new password</button>
    </form>""")

LINK_EXPIRED_HTML = _auth_page("Link expired", "This link doesn't work anymore", """
    <p class="muted">Links are good for one use, and expire (a password link after an hour, an email link after two days).</p>
    <p class="alt"><a href="/forgot-password">Send a new password link</a>&nbsp;&middot; <a href="/login">Sign in</a></p>""")


def _send_verification(user_id: int, email: str, name: str = "") -> None:
    """The "confirm your email" mail. Raises if it could not be sent."""
    raw = auth.issue_token(user_id, "verify", auth.VERIFY_TTL_S)
    link = MOBILE_URL.rstrip("/") + "/verify-email?token=" + raw
    send_email(email, "Confirm your email for Job Hunter",
               "Hi%s,\n\nConfirm this is your email address so Job Hunter can send applications and "
               "notifications with it:\n\n%s\n\nThe link works once and expires in 2 days. If you didn't "
               "create a Job Hunter account, ignore this email.\n\nJob Hunter" % ((" " + name.split()[0]) if name else "", link))


def _release_daily_picks(conn, user) -> int:
    """Move today's picks out of Noa's reserve (entitlements.release). Runs on
    app open and on the job list, so a day without a search still brings the
    next picks. Never raises: a failure leaves the reserve for the next call."""
    try:
        return len(entitlements.release(conn, user))
    except Exception as exc:
        print("[daily-picks] release failed for user %s: %s" % (user.get("id"), exc))
        return 0


def _first_matches_email(user_id: int, found: int) -> None:
    """The email a new user gets when their FIRST search ends (2026-11-12):
    the first matches are the activation moment, and the search takes minutes,
    so most people have left the page by then. In Noa's voice. Never raises."""
    try:
        c = database.get_db()
        try:
            r = c.execute("SELECT name, email FROM users WHERE id=?", (user_id,)).fetchone()
        finally:
            c.close()
        if not r or not r["email"]:
            return
        import testaccount
        if testaccount.is_test_address(r["email"]):
            return
        first = (" " + (r["name"] or "").split()[0]) if (r["name"] or "").strip() else ""
        link = MOBILE_URL.rstrip("/") + "/app"
        if found > 0:
            subject = "Noa found your first %d match%s" % (found, "" if found == 1 else "es")
            body = ("Hi%s,\n\nI read today's openings against your CV and kept %d that fit you. "
                    "Each one comes with my reasons.\n\nHave a look and swipe right on the ones you like. Every "
                    "pass teaches me what you want, so tomorrow's list gets closer to you:\n\n%s\n\nNoa\n"
                    "Your personal AI headhunter agent at Job Hunter" % (first, found, link))
        else:
            subject = "Noa's first search for you came back empty"
            body = ("Hi%s,\n\nI searched today's openings and none fit what you asked for yet. That usually "
                    "means the job titles or locations are narrower than the market.\n\nAdd a title or two (for "
                    "example the same role at one level up or down) and I'll search again tomorrow, or start a "
                    "search now:\n\n%s\n\nNoa\nYour personal AI headhunter agent at Job Hunter" % (first, link))
        send_email(r["email"], subject, body, as_noa=True)
        _track(user_id, "first_matches_email_sent", {"found": int(found)})
    except Exception as exc:
        print("[first-matches] email for user %s not sent: %s" % (user_id, exc))


def _send_reset(user_id: int, email: str) -> None:
    raw = auth.issue_token(user_id, "reset", auth.RESET_TTL_S)
    link = MOBILE_URL.rstrip("/") + "/reset-password?token=" + raw
    send_email(email, "Reset your Job Hunter password",
               "Someone (hopefully you) asked to reset the password for this Job Hunter account.\n\n"
               "Choose a new password here:\n\n%s\n\nThe link works once and expires in 1 hour. "
               "If you didn't ask, ignore this email - your password stays as it is.\n\nJob Hunter" % link)


def error_block(msg: str) -> str:
    if not msg:
        return ""
    return f"""<div class="err">{msg}</div>"""

# ── Onboarding ────────────────────────────────────────────────────────────────


# ── Settings
# ── Settings ──────────────────────────────────────────────────────────────────


# ── Admin Panel ───────────────────────────────────────────────────────────────


# ── Dashboard (user-aware) ────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# HTTP HANDLER
# ─────────────────────────────────────────────────────────────────────────────

def _cv_file(user_id, recorded_path=""):
    """
    A readable path to this user's CV, or "".

    Phase 2d: user_profiles.cv_path is still written and still honoured, but it
    is no longer the thing that decides whether the file is there. storage
    rebuilds the cached file from the database when the volume is cold - a
    fresh container, a restored backup, a redeploy that lost its volume - so
    the callers below keep passing a path around and simply stop being wrong
    about it. The recorded path is the fallback for the window before a user's
    legacy upload has been adopted.
    """
    try:
        path = storage.file_path(user_id)
        if path:
            return path
    except Exception as exc:
        print("[cv] storage lookup failed for user %s: %s" % (user_id, exc))
    if recorded_path and os.path.exists(recorded_path):
        return recorded_path
    return ""


def _applicant_for(user_id, email, name, cv_path_raw, cv_summary):
    """Applicant details for a form, from the CV itself (rebuilt from the
    database when the cache is cold), falling back to the summary only when the
    file has no text, and to the account name for a missing name. Every apply
    path used cv_summary alone - 2-3 sentences with no name or phone - so the
    model returned its prompt's example, "First Last" (2026-11-07)."""
    import apply_engine as _ae_ap
    text = _extract_cv_text(_cv_file(user_id, cv_path_raw or ""), cv_summary)
    out = _ae_ap.extract_applicant_data(text, email or "", fallback_name=name or "")
    out["cv_excerpt"] = (text or "")[:2500]          # material for open questions
    out["answer_bank"] = _answer_bank(user_id)
    _sa = _standing_answers(user_id)
    out["notice_period"] = "%d days" % _sa["notice_period_days"]
    if _sa["standing_notes"].strip():
        out["standing_instructions"] = _sa["standing_notes"].strip()
        # A GitHub address in the instructions fills GitHub fields directly
        # (The owner, 2026-11-08: "if the application asks for my GH url - give it
        # to them"); kept in his instructions, never in the code.
        _gh = re.search(r"https?://(?:www\.)?github\.com/[A-Za-z0-9_.-]+", out["standing_instructions"])
        if _gh:
            out["github_url"] = _gh.group(0)
    if _sa["salary_expectation_min"]:
        out["salary_expectation"] = "at least %s %s gross per month" % (
            format(_sa["salary_expectation_min"], ","), _sa["salary_expectation_currency"])
    # The Profile page is the source of truth for who is applying (The owner,
    # 2026-11-07: "You have everything you need in my profile page"): its
    # name, email, phone and LinkedIn win over anything read from the CV; the
    # CV only fills what the profile does not hold (title, location, summary).
    phone = linkedin = ""
    try:
        _c = database.get_db()
        try:
            _r = _c.execute("SELECT phone, linkedin_url, sponsorship_local, sponsorship_abroad "
                            "FROM user_profiles WHERE user_id=?", (user_id,)).fetchone()
        finally:
            _c.close()
        if _r:
            phone, linkedin = (_r["phone"] or "").strip(), (_r["linkedin_url"] or "").strip()
            # Not answered = no sponsorship needed (The owner, 2026-11-07).
            out["home_country"] = "Israel"
            out["needs_sponsorship_in_home_country"] = (_r["sponsorship_local"] or "").lower() == "yes"
            out["needs_sponsorship_abroad"] = (_r["sponsorship_abroad"] or "").lower() == "yes"
    except Exception as _pe:
        print(f"[apply] WARNING could not read the profile for user {user_id}: {_pe}")
    nm = (name or "").strip()
    if nm:
        parts = nm.split()
        out.update(full_name=nm, first_name=parts[0], last_name=" ".join(parts[1:]))
    if email:
        out["email"] = email
    if phone:
        out["phone"] = phone
    if out.get("phone"):
        # Phone pickers default to US +1; a local 054-... must go international.
        out["phone"] = _ae_ap.intl_phone(out["phone"], out.get("home_country") or "Israel")
    if linkedin:
        out["linkedin_url"] = linkedin
    return out


def _standing_answers(user_id) -> dict:
    """application_answers for the user, with the owner's defaults: notice period 30
    days, currency ILS."""
    out = {"salary_expectation_min": None, "salary_expectation_currency": "ILS", "notice_period_days": 30,
           "standing_notes": ""}
    try:
        c = database.get_db()
        try:
            r = c.execute("SELECT salary_expectation_min, salary_expectation_currency, notice_period_days, "
                          "standing_notes FROM application_answers WHERE user_id=?", (user_id,)).fetchone()
        finally:
            c.close()
        if r:
            if r["salary_expectation_min"]:
                out["salary_expectation_min"] = int(r["salary_expectation_min"])
            if r["salary_expectation_currency"]:
                out["salary_expectation_currency"] = r["salary_expectation_currency"]
            if r["notice_period_days"] is not None:
                out["notice_period_days"] = int(r["notice_period_days"])
            out["standing_notes"] = r["standing_notes"] or ""
    except Exception as exc:
        print(f"[apply] WARNING could not read application answers for user {user_id}: {exc}")
    return out


def _answer_bank(user_id) -> dict:
    """{question key: answer} the user gave before (application_qa)."""
    try:
        c = database.get_db()
        try:
            rows = c.execute("SELECT qkey, answer FROM application_qa WHERE user_id=?", (user_id,)).fetchall()
        finally:
            c.close()
        out = {}
        for r in rows:
            try:
                out[r["qkey"]] = json.loads(r["answer"])
            except ValueError:
                out[r["qkey"]] = r["answer"]
        return out
    except Exception as exc:
        print(f"[apply] WARNING could not read saved answers for user {user_id}: {exc}")
        return {}


def _docx_text(doc) -> str:
    """All the text of a Word CV: header, body paragraphs AND tables, in order.
    It read .paragraphs only, which skips tables, headers and nested cells -
    and many CV templates put roles and dates in a table (reproduced
    2026-11-08: a role in a table never reached the tailored CV or the letter)."""
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    out = []
    for sec in doc.sections:
        for p in sec.header.paragraphs:
            if p.text.strip():
                out.append(p.text)

    def walk(parent):
        for child in parent.iter_inner_content():
            if isinstance(child, Paragraph):
                out.append(child.text)
            elif isinstance(child, Table):
                for row in child.rows:
                    seen = []
                    for cell in row.cells:
                        if cell._tc in seen:          # merged cells repeat
                            continue
                        seen.append(cell._tc)
                        walk(cell)
    walk(doc)
    return "\n".join(out)


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
                except Exception as _pdf_e:
                    # It was `pass`: pypdf was not even installed in production,
                    # and nothing said so (2026-11-07).
                    print(f"[cv] WARNING could not read the CV PDF, using the summary: {_pdf_e!r}")
            elif cv_path.lower().endswith(('.docx', '.doc')):
                try:
                    from docx import Document as _Docx
                    _t = _docx_text(_Docx(cv_path))
                    if len(_t.strip()) > 100:
                        return _t
                except Exception as _docx_e:
                    print(f"[cv] WARNING could not read the CV document, using the summary: {_docx_e!r}")
        except Exception:
            pass
    return cv_summary or ''


def _call_gemini_cv_optimizer(cv_text: str = "", cv_path: str = "", user_id=None):
    import urllib.request as _ureq, base64 as _b64
    _key = os.environ.get('GEMINI_API_KEY', '')
    if not _key:
        raise ValueError('GEMINI_API_KEY not set in environment')
    _p1 = 'You are an expert CV/resume coach. Analyze the CV and return ONLY valid JSON.'
    _p2 = ' No markdown, no backticks. Output must be parseable by json.loads().'
    _p3 = ' CV language may vary but output MUST be in English.'
    _p4 = ' Score 0-100 (clarity 20, impact 25, ATS 20, structure 20, quality 15).'
    _p5 = ' Provide 3-5 improvements and 2-4 strengths.'
    _fmt = '{"score":72,"score_label":"Good","summary":"...","strengths":["..."],"improvements":[{"title":"...","detail":"..."}],"ats_notes":["..."]}'
    _instruction = _p1 + _p2 + _p3 + _p4 + _p5 + '\n\nFormat: ' + _fmt + '\n\nAnalyze the CV:'
    # Prefer sending the PDF directly to Gemini (avoids pypdf dependency)
    if cv_path and os.path.exists(cv_path) and cv_path.lower().endswith('.pdf'):
        with open(cv_path, 'rb') as _f:
            _pdf_b64 = _b64.b64encode(_f.read()).decode()
        _parts = [
            {'inlineData': {'mimeType': 'application/pdf', 'data': _pdf_b64}},
            {'text': _instruction},
        ]
    elif cv_text:
        _parts = [{'text': _instruction + '\n\nCV:\n' + cv_text[:8000]}]
    else:
        raise ValueError('No CV content available.')
    _body = json.dumps({
        'contents': [{'parts': _parts}],
        'generationConfig': {
            'thinkingConfig': {'thinkingBudget': 0},
            'temperature': 0.3,
            'maxOutputTokens': 1024,
        },
    }).encode('utf-8')
    _d = gemini.generate(_body, purpose='cv_optimizer', user_id=user_id,
                         key=_key, timeout=60)
    _t = _d['candidates'][0]['content']['parts'][0]['text'].strip()
    if _t.startswith('```'):
        _lines = _t.split('\n')
        _t = '\n'.join(_lines[1:-1] if _lines[-1].strip() == '```' else _lines[1:])
    return json.loads(_t)


_LINK_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _link_status(url: str, timeout: int = 8) -> str:
    """Classify a job URL for the sweepers: 'alive' | 'dead' | 'unknown'.

    Delegates to linkcheck.classify (2026-10-12). 'generic' - the URL is a
    careers/board home page, not a posting - counts as dead here: it is what
    "the link sent me to the company's general website" meant, and a queue
    entry nobody can apply from. Bot-blocks, rate limits and timeouts stay
    'unknown' so a live posting is never removed for refusing a robot.
    """
    import linkcheck
    try:
        verdict = linkcheck.classify(url, max(timeout, 10))[0]
    except Exception:
        return "unknown"
    return "dead" if verdict in ("dead", "generic") else verdict


def _link_verdict(url: str, timeout: int = 10):
    """(status, reason) for callers that record WHY a link was retired."""
    import linkcheck
    try:
        verdict, reason, _final = linkcheck.classify(url, timeout)
    except Exception as e:
        return "unknown", type(e).__name__
    return ("dead" if verdict in ("dead", "generic") else verdict), reason


# JH-1 (2026-11-05): /api/health is public, and until now it published the user
# count, job count, the last applied job and company, the credentials-key
# fingerprint, the Gemini ledger and the Postgres host. Anonymous callers now get
# only what an outside check needs to tell "up and doing its job" from "up and
# idle"; the full payload needs an admin session or the X-Health-Key header
# matching JH_HEALTH_KEY (scripts/smoke.py sends it from the same env var).
HEALTH_PUBLIC_KEYS = (
    "status", "uptime_info", "scheduler", "schema_version", "commit", "db_backend",
    "credentials_encrypted", "error_reporting", "sources", "queue", "db_check",
    "worker", "db_backend_refused", "last_search", "paid_pool", "support_inbox",
    "admin_alerts",
)


def public_health(payload: dict) -> dict:
    """The anonymous view of /api/health: an allowlist, so a field added later
    stays private until someone decides otherwise."""
    out = {k: payload[k] for k in HEALTH_PUBLIC_KEYS if k in payload}
    out["detail"] = "public"
    return out


# Sent on every response (JH-1). No script-src CSP yet: the pages carry inline
# scripts; frame-ancestors alone stops clickjacking without touching them.
SECURITY_HEADERS = (
    ("Strict-Transport-Security", "max-age=31536000; includeSubDomains"),
    ("X-Frame-Options", "DENY"),
    ("Content-Security-Policy", "frame-ancestors 'none'"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "strict-origin-when-cross-origin"),
)



class Handler(BaseHTTPRequestHandler):

    # BaseHTTPRequestHandler's own access line, superseded by the one log.end()
    # emits (which knows the user and the duration). Kept at DEBUG rather than
    # removed: it is the only thing that speaks when a request dies before the
    # wrapper's finally can run.
    def log_message(self, fmt, *args):
        _log.get("http").debug(fmt, *args)

    def log_error(self, fmt, *args):
        _log.get("http").warning(fmt, *args)

    def handle_one_request(self):
        # The one point every request passes through after its handler has
        # finished, whatever the method and however it ended. Any pooled
        # connection the handler borrowed and did not close - typically
        # because an exception skipped `conn.close()` - goes back here, so a
        # buggy route costs one failed request instead of a pool slot for the
        # life of the process (the 2026-09-25 outage). See dbdriver.
        try:
            BaseHTTPRequestHandler.handle_one_request(self)
        finally:
            dbdriver.release_thread_connections(
                "end of request %s" % (getattr(self, "path", "") or "?")[:120])

    def send_response(self, code, *a, **kw):
        # Every reply in this app goes through here (send_json, send_html and
        # redirect all call it), so it is the one place the access line can
        # learn what status was actually sent.
        _log.set_status(code)
        return BaseHTTPRequestHandler.send_response(self, code, *a, **kw)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def send_html(self, html: str, code: int = 200, headers=()):
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
        for _hn, _hv in headers:
            self.send_header(_hn, _hv)
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        for _name, _value in SECURITY_HEADERS:
            self.send_header(_name, _value)
        super().end_headers()

    def _health_detail_allowed(self) -> bool:
        """Full /api/health for the smoke key holder or an admin session."""
        key = os.environ.get("JH_HEALTH_KEY", "")
        sent = self.headers.get("X-Health-Key", "") if self.headers else ""
        if key and sent and hmac.compare_digest(key.encode(), sent.encode()):
            return True
        try:
            user = self.get_user()
        except Exception as _hu_exc:
            print(f"[health] WARNING session lookup failed, serving the public view: {_hu_exc}")
            return False
        return bool(user) and user.get("role") == "admin"

    def send_json(self, data, code: int = 200):
        body = json.dumps(data, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", MOBILE_URL)
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
        user = auth.get_session_user(token)
        if user:
            # From here on, every line this request emits names the user. This
            # is the field that turns "a 500 happened at 14:03" into a question
            # with an answer.
            _log.set_user(user.get("id") if hasattr(user, "get") else user["id"])
        return user
    def client_ip(self) -> str:
        """
        The caller's address as well as it can be known.

        Railway terminates TLS and appends X-Forwarded-For, so the socket peer
        is the proxy and would put every user in one bucket - a global lockout
        the first time anyone typo'd a password.

        JH-3 (2026-11-05): the LAST entry, not the first. The first entry is
        whatever the client typed - sending a fresh X-Forwarded-For per request
        put every signup in a new register-limiter bucket. The proxy appends the
        address it actually saw, so the last entry is the one the client cannot
        choose (and with no client header, first and last are the same).
        Anything that matters is still limited on a second key as well (login
        on the email, run-search on the user id).
        """
        fwd = (self.headers.get("X-Forwarded-For", "") or "").split(",")[-1].strip()
        return fwd or (self.client_address[0] if self.client_address else "?")

    def reject_cross_site(self) -> bool:
        """
        Refuse a state-changing request that a browser made from another site.

        The session cookie is already `SameSite=Lax` (auth.make_session_cookie),
        which stops the browser sending it on a cross-site POST. What Lax does
        NOT stop is a top-level GET navigation - the cookie rides along - and on
        2026-09-14 an audit found two admin GETs that change state:
        /api/admin/dedup deletes rows, and /api/admin/apply-test?mode=live
        submits a real application and deliberately bypasses the kill switch.
        A link in an email, clicked while signed in as admin, was enough.

        `Sec-Fetch-Site` is the check because it needs no client change: every
        browser that would send the cookie cross-site also sends this header,
        and the live PWA bundle still calls dedup as a GET - so requiring POST
        would have meant a bundle rebuild to close a hole that this closes now.

        Absent header means the caller is not a browser (curl, a script, the owner
        testing by hand). Those cannot be CSRF - the attack needs a browser
        holding someone else's cookie - so they pass.
        """
        site = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
        if site and site not in ("same-origin", "none"):
            print(f"[security] refused cross-site {self.command} {self.path} (Sec-Fetch-Site: {site})")
            self.send_json({"error": "Cross-site state-changing requests are refused."}, 403)
            return True
        return False

    def require_auth(self):
        """Returns user dict or None (and sends redirect if not authed)."""
        user = self.get_user()
        if not user:
            self.redirect("/login")
        return user

    def _check_sync_key(self, key: str = "") -> bool:
        """Validate the shared secret used by relay.py and scheduled tasks.

        JH-2 (2026-11-05): the X-Sync-Key header is preferred - a key in the
        query string is written to every access log line. The body / query
        argument is still accepted so an older relay keeps working until it is
        updated. Compared in constant time."""
        if not SYNC_API_KEY:
            return False
        sent = (self.headers.get("X-Sync-Key", "") if self.headers else "") or (key or "")
        return bool(sent) and hmac.compare_digest(sent.encode(), SYNC_API_KEY.encode())

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", MOBILE_URL)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    # ── GET ───────────────────────────────────────────────────────────────────

    def do_GET(self):
        with _log.request("GET", self.path, self.client_ip()):
            try:
                self._do_GET_inner()
            except Exception as exc:
                import traceback
                print(f"[do_GET] Unhandled exception on {self.path}: {exc}\n"
                      f"{traceback.format_exc()}")
                errors.capture(exc, where="do_GET", path=self.path,
                               user_id=_log.current_user_id())
                try:
                    self.send_json({"error": f"Server error: {exc}"}, code=500)
                except Exception:
                    pass

    def _do_GET_inner(self):
        parsed = urlparse(self.path)
        path   = parsed.path
        qs     = parse_qs(parsed.query)

        # -- Mobile PWA SPA (served under /app). Strictly additive: legacy
        #    routes (/dashboard, /login, /api/*) are untouched. The built
        #    bundle lives in web_bundle/ and is committed to the repo. --
        if path == "/app" or path.startswith("/app/"):
            import mimetypes as _mt
            bundle = os.path.join(BASE_DIR, "web_bundle")
            rel = path[len("/app"):].lstrip("/")
            if rel:
                fpath = os.path.normpath(os.path.join(bundle, rel))
                if fpath.startswith(bundle) and os.path.isfile(fpath):
                    ctype, _ = _mt.guess_type(fpath)
                    if fpath.endswith(".js"):
                        ctype = "text/javascript"
                    elif fpath.endswith(".webmanifest"):
                        ctype = "application/manifest+json"
                    elif fpath.endswith(".css"):
                        ctype = "text/css"
                    ctype = ctype or "application/octet-stream"
                    if ctype.startswith("text/") or "json" in ctype or "javascript" in ctype:
                        ctype += "; charset=utf-8"
                    with open(fpath, "rb") as fh:
                        data = fh.read()
                    self.send_response(200)
                    self.send_header("Content-Type", ctype)
                    if "/assets/" in path:
                        self.send_header("Cache-Control", "public, max-age=31536000, immutable")
                    else:
                        self.send_header("Cache-Control", "no-cache")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
            user = self.require_auth()
            if not user:
                return
            index_path = os.path.join(bundle, "index.html")
            try:
                with open(index_path, "rb") as fh:
                    body = fh.read()
            except FileNotFoundError:
                self.send_response(404)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"App bundle not found")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # Public routes
        if path in ("/login", "/login/"):
            user = self.get_user()
            if user:
                self.redirect(home_url())
            else:
                _note = ""
                if qs.get("deleted", ["0"])[0] == "1":
                    _note = error_block("Your account will be deleted in %d days. Changed your mind? "
                                        "Sign in before then and nothing is deleted." % accountdata.GRACE_DAYS)
                _n = qs.get("notice", [""])[0]
                if _n == "reset":
                    _note = notice_block("Your password is changed. Sign in with the new one.")
                elif _n == "verified":
                    _note = notice_block("Your email is confirmed. Sign in to continue.")
                self.send_html(LOGIN_HTML.replace("{error_block}", _note))
            return

        # ── Step 5: forgot / reset password, confirm email (2026-11-09) ──
        if path in ("/forgot-password", "/forgot-password/"):
            self.send_html(FORGOT_HTML.replace("{error_block}", ""))
            return

        if path in ("/reset-password", "/reset-password/"):
            _tok = qs.get("token", [""])[0]
            if not _tok or not auth.peek_token(_tok, "reset"):
                self.send_html(LINK_EXPIRED_HTML, 410)
                return
            self.send_html(RESET_HTML.replace("{error_block}", "").replace("{token}", html_lib.escape(_tok, quote=True)))
            return

        if path in ("/verify-email", "/verify-email/"):
            _uid = auth.use_token(qs.get("token", [""])[0], "verify")
            if not _uid:
                self.send_html(LINK_EXPIRED_HTML, 410)
                return
            auth.mark_email_verified(_uid)
            _funnel(_uid, "verified")
            _me = self.get_user()
            self.redirect((home_url() + ("&" if "?" in home_url() else "?") + "verified=1")
                          if _me and _me["id"] == _uid else "/login?notice=verified")
            return

        if path in ("/register", "/register/"):
            user = self.get_user()
            if user:
                self.redirect(home_url())
            else:
                self.send_html(_register_page("", analytics.clean_attribution(qs)))
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

        # ── Google Sign-In: start (full-page redirect to Google; PWA-safe) ──
        if path in ("/auth/google/start", "/auth/google/start/"):
            if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
                self.send_html(LOGIN_HTML.replace("{error_block}", error_block(
                    "Google sign-in isn't configured on this server yet.")))
                return
            import secrets as _secrets_g, hashlib as _hashlib_g, base64 as _b64_g
            _state    = _secrets_g.token_urlsafe(32)
            _g_attr = analytics.encode_attribution(analytics.clean_attribution(qs))
            if _g_attr:   # where a new account came from, carried through Google in `state`
                _state += "." + _b64_g.urlsafe_b64encode(_g_attr.encode()).decode().rstrip("=")
            _verifier = _secrets_g.token_urlsafe(64)
            _challenge = _b64_g.urlsafe_b64encode(
                _hashlib_g.sha256(_verifier.encode()).digest()).rstrip(b"=").decode()
            _redirect_uri = _base_for(self.headers.get("Host", "")) + "/auth/google/callback"
            _params = urllib.parse.urlencode({
                "client_id": GOOGLE_CLIENT_ID,
                "redirect_uri": _redirect_uri,
                "response_type": "code",
                "scope": "openid email profile",
                "state": _state,
                "code_challenge": _challenge,
                "code_challenge_method": "S256",
                "access_type": "online",
                "prompt": "select_account",
            })
            _sec = "; Secure" if MOBILE_URL.startswith("https") else ""
            self.send_response(302)
            self.send_header("Set-Cookie", f"g_state={_state}; Path=/; HttpOnly{_sec}; Max-Age=600; SameSite=Lax")
            self.send_header("Set-Cookie", f"g_verifier={_verifier}; Path=/; HttpOnly{_sec}; Max-Age=600; SameSite=Lax")
            self.send_header("Location", GOOGLE_AUTH_URI + "?" + _params)
            self.end_headers()
            return

        # ── Google Sign-In: callback (verify state, exchange code, sign in) ──
        if path in ("/auth/google/callback", "/auth/google/callback/"):
            def _google_fail(msg):
                self.send_html(LOGIN_HTML.replace("{error_block}", error_block(msg)))
            _code  = (qs.get("code")  or [""])[0]
            _state = (qs.get("state") or [""])[0]
            if qs.get("error"):
                _google_fail("Google sign-in was cancelled."); return
            from http.cookies import SimpleCookie as _SC_g
            _ck = _SC_g(); _ck.load(self.headers.get("Cookie", ""))
            _cookie_state    = _ck["g_state"].value    if "g_state"    in _ck else ""
            _cookie_verifier = _ck["g_verifier"].value  if "g_verifier" in _ck else ""
            if not _code or not _state or not _cookie_state or _state != _cookie_state:
                _google_fail("Google sign-in failed a security check. Please try again."); return
            _redirect_uri = _base_for(self.headers.get("Host", "")) + "/auth/google/callback"
            try:
                _tok_body = urllib.parse.urlencode({
                    "code": _code,
                    "client_id": GOOGLE_CLIENT_ID,
                    "client_secret": GOOGLE_CLIENT_SECRET,
                    "redirect_uri": _redirect_uri,
                    "grant_type": "authorization_code",
                    "code_verifier": _cookie_verifier,
                }).encode()
                _tok_req = urllib.request.Request(GOOGLE_TOKEN_URI, data=_tok_body,
                    headers={"Content-Type": "application/x-www-form-urlencoded"})
                with urllib.request.urlopen(_tok_req, timeout=15) as _r:
                    _tok = json.loads(_r.read().decode())
                _access = _tok.get("access_token")
                if not _access:
                    _google_fail("Google sign-in failed (no token). Please try again."); return
                _ui_req = urllib.request.Request(GOOGLE_USERINFO_URI,
                    headers={"Authorization": "Bearer " + _access})
                with urllib.request.urlopen(_ui_req, timeout=15) as _r:
                    _info = json.loads(_r.read().decode())
            except Exception as _ge:
                print(f"[google-oauth] token/userinfo error: {_ge}")
                _google_fail("Couldn't reach Google to complete sign-in. Please try again."); return
            _sub      = _info.get("sub")
            _email    = _info.get("email", "")
            _verified = _info.get("email_verified", False)
            _name     = _info.get("name", "")
            _pic      = _info.get("picture", "")
            if not _sub or not _email or not _verified:
                _google_fail("Your Google account email isn't verified, so we can't sign you in."); return
            _user, _is_new, _err = auth.find_or_create_google_user(_sub, _email, _name, _pic)
            if _is_new and _user:
                _funnel(_user["id"], "signup")
                _funnel(_user["id"], "verified")       # Google already confirmed the address
                _g_attr = {}
                if "." in _state:
                    try:
                        _enc = _state.split(".", 1)[1]
                        _g_attr = analytics.decode_attribution(
                            _b64.urlsafe_b64decode(_enc + "=" * (-len(_enc) % 4)).decode())
                    except (ValueError, UnicodeDecodeError):
                        _g_attr = {}
                _track(_user["id"], "user_signed_up", dict(_g_attr, method="google"), set_once=_g_attr)
            if _err or not _user:
                _google_fail(_err or "Sign-in failed. Please try again."); return
            _token = auth.create_session(_user["id"])
            self.send_response(302)
            self.send_header("Set-Cookie", auth.make_session_cookie(_token))
            self.send_header("Set-Cookie", "g_state=; Path=/; HttpOnly; Max-Age=0; SameSite=Lax")
            self.send_header("Set-Cookie", "g_verifier=; Path=/; HttpOnly; Max-Age=0; SameSite=Lax")
            self.send_header("Location", ONBOARDING_ENTRY if _is_new else home_url())
            self.end_headers()
            return

        # The bare domain: signed in -> the app; signed out -> the public
        # landing page (Step 10 of EXECUTION_PLAN_NEXT.md, 2026-11-09). It used
        # to send strangers straight to a sign-in form.
        if path in ("/", ""):
            user = self.get_user()
            # home_url(), not "/dashboard": this was the one redirect Phase 4's
            # flip missed, and it is the bare domain - the link people click.
            if user:
                self.redirect(home_url())
            else:
                # A/B test of the three Noa illustrations (2026-11-12): a fresh
                # pick on every view, no cookie. The variant rides on the
                # sign-up links (landing.py) into the signup event, and every
                # view is counted server-side with it - so signups per view, per
                # variant, need no consent. ?noa= forces one (for reviewing).
                import landing, random
                _noa = (qs.get("noa") or [""])[0]
                if _noa not in landing.NOA_VARIANTS:
                    _noa = random.choice(landing.NOA_VARIANTS)
                analytics.capture_anonymous("landing_viewed", dict(analytics.clean_attribution(qs), noa=_noa,
                                            referrer=urlparse(self.headers.get("Referer", "")).netloc[:80]),
                                            self.headers.get("User-Agent", ""))
                self.send_html(landing_html(_noa))
            return

        # Step 6 (2026-11-11): Terms of Service and Privacy Policy, public.
        if path in ("/terms", "/privacy"):
            import legal
            pub = os.environ.get("JH_PUBLIC_URL", "").strip()
            self.send_html(legal.terms_html(pub) if path == "/terms" else legal.privacy_html(pub))
            return

        # "Stop these emails" from a lifecycle email: a confirm page, never a
        # change on GET (mail scanners open links). Token = HMAC of the user id.
        if path == "/email/stop":
            import lifecycle
            _u, _t = (qs.get("u") or [""])[0], (qs.get("t") or [""])[0]
            if not lifecycle.unsubscribe_ok(_u, _t):
                self.send_html(_stop_page("This link doesn't work", "It may be incomplete. You can also reply "
                                          "to any of our emails and ask us to stop."), 400)
                return
            self.send_html(_stop_page("Stop these emails?",
                "You'll stop getting Noa's tips and check-ins. Account emails (like password resets) still come.",
                "<form method='post' action='/email/stop?u=%s&amp;t=%s'><button type='submit'>Stop these emails"
                "</button></form>" % (int(_u), _t)))
            return

        if path.startswith("/landing-assets/"):
            # The landing page's pictures and fonts (assets/landing/). Only the
            # names landing.ASSETS lists are served - never a path from the URL.
            import landing
            _name = path[len("/landing-assets/"):]
            _type = landing.ASSETS.get(_name)
            if not _type:
                self.send_json({"error": "Not found"}, 404)
                return
            try:
                with open(os.path.join(BASE_DIR, "assets", "landing", _name), "rb") as _fh:
                    _img = _fh.read()
            except OSError:
                self.send_json({"error": "Not found"}, 404)
                return
            self.send_response(200)
            self.send_header("Content-Type", _type)
            self.send_header("Content-Length", str(len(_img)))
            self.send_header("Cache-Control", "public, max-age=604800")
            self.end_headers()
            self.wfile.write(_img)
            return

        if path == "/robots.txt":
            body = ("User-agent: *\nAllow: /$\nDisallow: /app\nDisallow: /api/\n"
                    "Disallow: /reset-password\nDisallow: /verify-email\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/api/admin/test-account":
            user = self.require_auth()
            if not user:
                return
            if user.get("role") != "admin":
                self.send_json({"error": "Forbidden"}, 403)
                return
            import testaccount
            _c = database.get_db()
            try:
                _st = testaccount.status(_c)
            finally:
                _c.close()
            _st["email"] = testaccount.TEST_EMAIL
            self.send_json(_st)
            return

        if path == "/api/admin/funnel":
            user = self.require_auth()
            if not user or user.get("role") != "admin":
                self.send_json({"error": "Forbidden"}, 403)
                return
            try:
                days = max(1, min(365, int(qs.get("days", ["30"])[0])))
            except ValueError:
                days = 30
            self.send_json(funnel_report(days))
            return

        # Auth-required routes
        # The legacy server-rendered pages were deleted in Step 9 (2026-11-05).
        # Their URLs stay as redirects: old bookmarks, home-screen icons and every
        # notification sent before 2026-09-21 point at them. The browser carries a
        # #new / #applied fragment across the redirect, and /app reads it.
        _legacy = {"/dashboard": "/app", "/onboarding": ONBOARDING_ENTRY,
                   "/settings": "/app", "/admin": "/app?view=dashboard",
                   "/admin/test-account": "/app?view=dashboard"}
        if path.rstrip("/") in _legacy:
            user = self.require_auth()
            if not user:
                return
            self.redirect(_legacy[path.rstrip("/")])
            return

        # Health check (no auth required)
        if path == "/api/health":
            def _queue_depth():
                try:
                    return jobqueue.depth()
                except Exception as _qe:
                    return {"unavailable": str(_qe)[:120]}

            import time as _ht
            # Read BEFORE touching the database. db_pool existed to make pool
            # exhaustion visible, but it was computed after get_db() - so when
            # the pool ran dry on 2026-09-25, health hung for 20s and returned
            # a generic 500 like every other route, and the one field built to
            # explain the outage was never sent.
            try:
                _pool_stats = dbdriver.pool_stats()
            except Exception:
                _pool_stats = {}
            try:
                conn = database.get_db()
            except Exception as _dbe:
                _down = {
                    "status": "db_unavailable",
                    "error": str(_dbe)[:200],
                    "db_backend": database.backend(),
                    "db_pool": _pool_stats,
                    "db_leaks_reclaimed": dbdriver.reclaimed_stats(),
                    "scheduler_lock": schedlock.status(),
                    "commit": os.environ.get("RAILWAY_GIT_COMMIT_SHA", "")[:7],
                }
                if not self._health_detail_allowed():
                    _down = {k: _down[k] for k in ("status", "db_backend", "commit")}
                self.send_json(_down, 503)
                return
            try:
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
                try:
                    _schema_version = conn.execute(
                        "SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()[0]
                except Exception:
                    _schema_version = 0
            finally:
                conn.close()
            _health = {
                "status": "ok",
                "uptime_info": "server running",
                "active_users": user_count,
                "total_jobs": job_count,
                "last_search": {"detail": repair_mojibake(last_search[0]) if last_search else None, "date": last_search[1] if last_search else None},
                "last_apply": {"detail": repair_mojibake(last_apply[0]) if last_apply else None, "date": last_apply[1] if last_apply else None},
                "scheduler": "active (checks every 60s)" if scheduler_enabled()
                             else "disabled (JH_SCHEDULER_ENABLED=0)",
                # Phase 2a: lets a deploy be verified from outside - the smoke
                # script asserts the migrations actually ran on the box.
                "schema_version": _schema_version,
                # Which build is answering. Without this a smoke run cannot tell
                # a green deploy from a green *previous* deploy still being
                # swapped out - which is how the Postgres flip on staging first
                # read as a failure (2026-09-07).
                "commit": os.environ.get("RAILWAY_GIT_COMMIT_SHA", "")[:7],
                "db_backend": database.backend(),
                # False means the notification credentials are stored in the
                # clear - and so are in every dump of this database.
                "credentials_encrypted": crypto.available(),
                # Not the key - a hash of it. Lets a migration script prove it
                # holds the SAME key as the app before it writes anything.
                "credentials_key": crypto.fingerprint(),
                # Whether THIS instance is the one running the clock. Without
                # it, "my daily search stopped" and "another instance holds the
                # lock" look identical from outside.
                "scheduler_lock": schedlock.status(),
                # Whether a crash would actually reach anyone. "The DSN is set"
                # and "reporting works" are different claims; this reports the
                # second.
                "error_reporting": errors.status(),
                "rate_limit": ratelimit.snapshot(),
                # Today's Gemini spend against today's ceilings. This is the
                # number that decides what the ceiling should actually be — it
                # ships generous on purpose, because nothing here had ever been
                # measured before the ledger existed.
                "llm": gemini.usage(),
                # The 14-day record behind that ceiling. usage() reports only
                # TODAY, so until this existed the real peak was invisible from
                # outside the box and the ceiling stayed at its placeholder.
                "llm_history": gemini.history(14),
                # Last outcome per job source since this process started (Step 3).
                "sources": _source_health(),
                "paid_pool": _paid_pool_health(),
                # Contact support has somewhere to go (a yes/no; never the address)
                "support_inbox": bool(SUPPORT_EMAIL),
                # Someone receives the new-user alert (a yes/no and how it was
                # resolved; never the address). ADMIN_EMAIL was unset on
                # production until 2026-11-11 and every alert vanished silently.
                "admin_alerts": _admin_alerts_health(),
                # Queue depth and the age of the oldest running job: a wedged
                # worker looks exactly like a healthy one from outside without
                # this. Guarded because a box that predates migration 6 has no
                # such table, and health must answer on every build.
                "queue": _queue_depth(),
                # Liveness is not the same as usefulness. The three below are
                # the ways this box can be up and doing nothing: the database
                # unreachable, the worker wedged, or sessions unenforced.
                "db_check": db_check(),
                "worker": worker_health(),
                # Non-null means a Postgres target was configured and refused;
                # the app is serving SQLite instead. smoke.ps1 asserts on it.
                "db_backend_refused": database.BACKEND_REFUSAL,
                # Pool exhaustion looks like "the app hung" from outside; this
                # makes it visible. Empty on SQLite.
                "db_pool": _pool_stats,
                # Whether web push can send at all (keys + library), 2026-11-08.
                "push": push_configured(),
                # Connections a code path borrowed and never closed, handed
                # back at the end of the request/tick. Should stay 0; a rising
                # count names a route that needs a try/finally.
                "db_leaks_reclaimed": dbdriver.reclaimed_stats(),
            }
            self.send_json(_health if self._health_detail_allowed() else public_health(_health))
            return

        # API routes (all require auth)
        if path == "/api/me/export":
            # Step 6: everything we hold about the user, as one zip (data.json +
            # the CV). Secrets and held-back matches are not in it (accountdata).
            user = self.require_auth()
            if not user:
                return
            try:
                _cv = storage.get_bytes(user["id"])
            except Exception as _ce:
                print("[export] WARNING CV unreadable for user %s: %s" % (user["id"], _ce))
                _cv = None
            conn = database.get_db()
            try:
                _name = "cv.pdf"
                _r = conn.execute("SELECT cv_filename FROM user_profiles WHERE user_id=?", (user["id"],)).fetchone()
                if _r and _r["cv_filename"]:
                    _name = os.path.basename(str(_r["cv_filename"])) or "cv.pdf"
                _zip = accountdata.export_zip(conn, user["id"], _cv, _name)
            finally:
                conn.close()
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", 'attachment; filename="job-hunter-data.zip"')
            self.send_header("Content-Length", str(len(_zip)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(_zip)
            return

        _tm = re.match(r"^/api/jobs/(\d+)/tailored-cv(\.docx)?$", path)
        if _tm:
            # Step 14: the stored tailored CV, as JSON or as a Word file.
            user = self.require_auth()
            if not user:
                return
            _job_id = int(_tm.group(1))
            got = _tailored_cv_checked(user["id"], _job_id, user)
            if not got:
                self.send_json({"error": "No tailored CV for this job yet"}, 404)
                return
            if not _tm.group(2):
                self.send_json(got)
                return
            import tailor as _tl
            _cvt, _name, _contact = _tailor_inputs(user["id"], user)
            _c = database.get_db()
            try:
                _j = _c.execute("SELECT company FROM jobs WHERE id=? AND user_id=?", (_job_id, user["id"])).fetchone()
            finally:
                _c.close()
            data = _tl.to_docx(got["cv"], name=_name, contact=_contact)
            fname = re.sub(r"[^A-Za-z0-9]+", "_", "%s CV %s" % (_name or "My", (_j["company"] if _j else "") or "")).strip("_")
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
            self.send_header("Content-Disposition", 'attachment; filename="%s.docx"' % fname)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        if path == "/api/me":
            user = self.require_auth()
            if not user:
                return
            if analytics.enabled():
                import testaccount
                if not testaccount.is_test_address(user.get("email")):
                    analytics.app_opened(user["id"])
            out = dict(user)
            # Daily picks: a new day brings the next picks from Noa's reserve.
            try:
                _dc = database.get_db()
                try:
                    _release_daily_picks(_dc, user)
                    out["daily_picks"] = entitlements.daily_picks_summary(_dc, user)
                finally:
                    _dc.close()
            except Exception as _de:
                print("[me] daily picks unavailable for user %s: %s" % (user["id"], _de))
                out["daily_picks"] = None
            # Setup ended and the first search has not finished yet: the app
            # shows "Noa is finding your first matches" instead of empty lists.
            try:
                _fc = database.get_db()
                try:
                    _running = _fc.execute("SELECT 1 FROM job_runs WHERE user_id=? AND kind='search' "
                                           "AND status IN ('queued','running') LIMIT 1", (user["id"],)).fetchone()
                    _done = _fc.execute("SELECT 1 FROM funnel_events WHERE user_id=? AND step='first_search'",
                                        (user["id"],)).fetchone()
                finally:
                    _fc.close()
                out["first_search_running"] = bool(_running) and not _done
            except Exception as _fe:
                print("[me] first-search state unknown for user %s: %s" % (user["id"], _fe))
                out["first_search_running"] = False
            try:
                out["plan_info"] = entitlements.plan_info(user)
                _pc = database.get_db()
                try:
                    out["usage"] = entitlements.usage_summary(_pc, user)
                finally:
                    _pc.close()
            except Exception as _pe:      # /api/me must never fail over a statistic
                print(f"[plans] WARNING plan info for user {user.get('id')}: {_pe}")
            # Settings tells a user who switched auto-apply on whether anything
            # is being sent yet (the engine's own server-wide switch).
            out["auto_apply_live"] = _engine_on_for(user)
            out["email_verified"] = auth.is_email_verified(user["id"])
            self.send_json(out)
            return

        if path == "/api/cv":
            user = self.require_auth()
            if not user:
                return
            # Phase 2d: served from the database, so this works on a box whose
            # uploads volume is empty (a fresh container, a restored backup).
            try:
                _data = storage.get_bytes(user["id"])
            except Exception as _se:
                print("[cv] read failed for user %s: %s" % (user["id"], _se))
                self.send_json({"error": "Could not read CV."}, 500)
                return
            if not _data:
                self.send_json({"error": "No CV uploaded yet."}, 404)
                return
            _fname = "cv.pdf"
            try:
                _conn = database.get_db()
                try:
                    _r = _conn.execute("SELECT cv_filename FROM user_profiles WHERE user_id=?", (user["id"],)).fetchone()
                    _conn.close()
                    if _r and _r["cv_filename"]:
                        _fname = _r["cv_filename"]
                finally:
                    _conn.close()
            except Exception:
                pass
            _disp = "attachment" if qs.get("download", ["0"])[0] == "1" else "inline"
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Disposition", f'{_disp}; filename="{_fname}"')
            self.send_header("Content-Length", str(len(_data)))
            self.send_header("Cache-Control", "private, no-cache")
            self.end_headers()
            self.wfile.write(_data)
            return

        if path == "/api/admin/apply-rates":
            user = self.require_auth()
            if not user or user.get("role") != "admin":
                self.send_json({"error": "Forbidden"}, 403)
                return
            conn = database.get_db()
            try:
                self.send_json(_apply_rates(conn, None if (qs.get("scope", ["all"])[0] == "all") else user["id"]))
            finally:
                conn.close()
            return

        if path == "/api/admin/notify-check":
            user = self.require_auth()
            if not user or user.get("role") != "admin":
                self.send_json({"error": "Forbidden"}, 403)
                return
            self.send_json(_notify_status(user["id"]))
            return

        if path == "/api/push/public-key":
            user = self.require_auth()
            if not user:
                return
            self.send_json({"publicKey": VAPID_PUBLIC_KEY})
            return

        if path == "/api/stats":
            user = self.require_auth()
            if not user:
                return
            conn = database.get_db()
            try:
                self.send_json(database.get_stats(conn, user["id"]))
                conn.close()
                return
            finally:
                conn.close()

        if path == "/api/jobs/locked":
            user = self.require_auth()
            if not user:
                return
            conn = database.get_db()
            try:
                out = entitlements.locked_summary(conn, user["id"])
            finally:
                conn.close()
            out["matches_per_search"] = entitlements.limit(user, "matches_per_search")
            out["plan"] = entitlements.tier_of(user)
            self.send_json(out)
            return

        if path == "/api/jobs":
            user = self.require_auth()
            if not user:
                return
            status  = qs.get("status", ["new"])[0]
            sort_by = qs.get("sort",   ["date"])[0]
            order_map = {
                # Effective rank: the score, less the learned feedback penalty,
                # less links.LINK_PENALTY when no direct link was found (Step 1d).
                "match":   "(COALESCE(match_score, -1) - COALESCE(feedback_penalty, 0) - "
                           + _links_mod.LINK_PENALTY_SQL + ") DESC",
                "company": "company ASC",
                "date":    "found_date DESC",
            }
            order = order_map.get(sort_by, "found_date DESC")
            conn  = database.get_db()
            try:
                database.expire_old_jobs(conn, user["id"])
                _release_daily_picks(conn, user)
                # A locked match (beyond the plan's per-search limit) is never
                # sent, whatever status is asked for - only its count, from
                # /api/jobs/locked (Phase 5b).
                # History older than the plan keeps (Free 30 days, Premium 12
                # months) is hidden, not deleted (Step 6) - the open queue never.
                _cut = entitlements.history_cutoff(user)
                _hist = ""
                _hargs = ()
                if _cut:
                    _hs = ",".join("'%s'" % h for h in entitlements.HISTORY_STATUSES)
                    _hist = (" AND (status NOT IN (%s) OR %s >= ?)" % (_hs, entitlements.HISTORY_DATE_SQL))
                    _hargs = (_cut,)
                if status == "all":
                    rows = conn.execute(
                        f"SELECT * FROM jobs WHERE user_id=? AND status NOT IN ('locked','held'){_hist} ORDER BY {order}",
                        (user["id"],) + _hargs
                    ).fetchall()
                elif status in ("locked", "held"):
                    rows = []
                else:
                    rows = conn.execute(
                        f"SELECT * FROM jobs WHERE user_id=? AND status=?{_hist} ORDER BY {order}",
                        (user["id"], status) + _hargs
                    ).fetchall()
                jobs_list = [dict(r) for r in rows]
                import apply_engine as _ae_route
                for _j in jobs_list:
                    _j["link_penalty"] = _links_mod.link_penalty(_j)
                    _j["apply_route"] = _ae_route.apply_route(_j.get("url"))

                # ── Live feedback learning: recompute deterministic penalties so the
                #    New/Approved tabs reflect the user's latest pass feedback on every
                #    load (no API calls - cheap and immediate). Demotes, never hides. ──
                try:
                    from ai_analysis import compute_feedback_penalty as _live_fb_penalty
                    _live_sig = database.get_feedback_signals(conn, user["id"])
                    _live_prof_row = conn.execute(
                        "SELECT * FROM user_profiles WHERE user_id=?", (user["id"],)
                    ).fetchone()
                    _live_prof = dict(_live_prof_row) if _live_prof_row else {}
                    _fb_dirty = False
                    for _j in jobs_list:
                        if _j.get("status") not in ("new", "approved"):
                            continue
                        _pen, _rsn = _live_fb_penalty(_j, _live_sig, _live_prof)
                        if _pen != (_j.get("feedback_penalty") or 0) or (_rsn or "") != (_j.get("feedback_reason") or ""):
                            conn.execute(
                                "UPDATE jobs SET feedback_penalty=?, feedback_reason=? WHERE id=?",
                                (_pen, _rsn, _j["id"])
                            )
                            _j["feedback_penalty"] = _pen
                            _j["feedback_reason"]  = _rsn
                            _fb_dirty = True
                    if _fb_dirty:
                        conn.commit()
                    if sort_by == "match":
                        jobs_list.sort(
                            key=lambda x: ((x.get("match_score") or -1) - (x.get("feedback_penalty") or 0)
                                           - (x.get("link_penalty") or 0)),
                            reverse=True
                        )
                except Exception as _fbe:
                    print(f"[feedback-live] {_fbe}")

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
                                    from ai_analysis import compute_feedback_penalty as _bg_fb_penalty
                                    c2 = database.get_db()
                                    try:
                                        sig = database.get_feedback_signals(c2, uid)
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
                                                ms = compute_match_score(jd, pd, signals=sig, user_id=uid)
                                                cs = compute_candidate_score(jd, pd)
                                                pen, rsn = _bg_fb_penalty(jd, sig, pd)
                                                c2.execute(
                                                    "UPDATE jobs SET match_score=?, candidate_score=?, feedback_penalty=?, feedback_reason=? WHERE id=?",
                                                    (ms, cs, pen, rsn, jid)
                                                )
                                                c2.commit()
                                            except Exception as ie:
                                                print(f"[score-bg] job {jid}: {ie}")
                                        c2.close()
                                    finally:
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
            finally:
                conn.close()

        if path == "/api/activity":
            user = self.require_auth()
            if not user:
                return
            items = database.get_activity(user["id"], limit=100)
            _cut = entitlements.history_cutoff(user)
            if _cut:
                items = [i for i in items if str(i.get("created_date") or "")[:10] >= _cut]
            for _it in items:
                if "details" in _it and _it["details"]:
                    _it["details"] = repair_mojibake(_it["details"])
            self.send_json(items)
            return

        if path == "/api/admin/apply-test":
            # Guarded LIVE single-job apply. Default is a DRY RUN (resolve + input
            # check, no submit). Add &mode=live to actually submit ONE job. Admin
            # only; bypasses the global kill-switch for this one explicit call.
            user = self.require_auth()
            if not user or user.get("role") != "admin":
                self.send_json({"error": "Forbidden"}, 403)
                return
            import apply_engine as _ae
            _jid = (qs.get("job_id", [""])[0] or "").strip()
            _mode = (qs.get("mode", ["dry"])[0] or "dry").strip().lower()
            # The dry run only reads; mode=live submits an application and
            # bypasses the global kill switch, so it must not be reachable by
            # following a link.
            if _mode == "live" and self.reject_cross_site():
                return
            if not _jid.isdigit():
                # No job_id → list candidate jobs (with their IDs) to pick from.
                _lc = database.get_db()
                try:
                    _lrows = _lc.execute(
                        "SELECT id, status, title, company, url FROM jobs "
                        "WHERE user_id=? AND status IN ('new','approved') "
                        "ORDER BY status, id DESC LIMIT 1000", (user["id"],)).fetchall()
                    _lc.close()

                    def _kind(u):
                        # Workday: step 1 is an account on the company's site (2026-11-07),
                        # so it is direct but not submittable; boards are manual by design.
                        if not _ae._is_direct_ats(u or ""):
                            return "job_board"
                        return "account_needed" if _ae._is_workday(u or "") else "submittable"
                    # Every approved job, then the newest NOT-yet-approved jobs the engine can
                    # submit to: his first approved three were 2 Workday + 1 board (2026-11-07),
                    # leaving the supervised test nothing to run on.
                    _appr = [r for r in _lrows if r["status"] == "approved"]
                    _sub = [r for r in _lrows if r["status"] == "new"
                            and _kind(r["url"]) == "submittable"][:30]
                    _lrows = _appr + _sub
                    self.send_json({
                        "note": "Pick a job_id, then open ?job_id=<id> for a DRY RUN, "
                                "and ?job_id=<id>&mode=live to actually submit that one job.",
                        # direct_ats: whether the engine can submit there at all
                        # (job boards are manual_required by design).
                        "jobs": [{"id": r["id"], "status": r["status"], "title": r["title"],
                                  "company": r["company"], "url": r["url"],
                                  "direct_ats": _ae._is_direct_ats(r["url"] or ""),
                                  "kind": _kind(r["url"])} for r in _lrows],
                    })
                    return
                finally:
                    _lc.close()
            conn = database.get_db()
            try:
                job = conn.execute(
                    "SELECT id, title, company, url, location, COALESCE(apply_status,'') AS aps "
                    "FROM jobs WHERE id=? AND user_id=?", (int(_jid), user["id"])).fetchone()
                prof = conn.execute("SELECT cv_summary, cv_path FROM user_profiles WHERE user_id=?",
                                    (user["id"],)).fetchone() if job else None
                conn.close()
                if not job:
                    self.send_json({"error": f"job {_jid} not found for this user"}, 404)
                    return
                _cv_text = (prof["cv_summary"] or "") if prof else ""
                # Step 8.1 (2026-11-06): the same resolver as every other apply
                # path. The raw cv_path names the cached file, which a fresh
                # container does not have - the CV itself is in the database
                # (Phase 2d) - so this route alone would have submitted without one.
                _cv_path = _cv_file(user["id"], (prof["cv_path"] or "") if prof else "") or None
                applicant = _applicant_for(user["id"], user.get("email", ""), user.get("name", ""),
                                           (prof["cv_path"] if prof else ""), _cv_text)
                _jloc = (job["location"] if "location" in job.keys() else "") or ""

                resolved = None
                if not _ae._is_direct_ats(job["url"] or ""):
                    try:
                        resolved = _ae.resolve_ats_application(job["company"], job["title"], location=_jloc)
                    except Exception as _e:
                        resolved = {"error": str(_e)[:200]}
                _target = (resolved or {}).get("url") if isinstance(resolved, dict) else None
                _target = _target or (job["url"] or "")

                out = {
                    "mode": _mode,
                    "job": {"id": job["id"], "title": job["title"], "company": job["company"],
                            "url": job["url"], "location": _jloc, "apply_status": job["aps"]},
                    "resolved": resolved,
                    "target_url": _target,
                    "is_direct_ats": _ae._is_direct_ats(_target),
                    "account_needed": _ae._is_workday(_target),   # Workday: 2026-11-07
                    "applicant_ready": bool(applicant.get("full_name") or applicant.get("first_name")),
                    "applicant_name": applicant.get("full_name", ""),
                    "cv_present": bool(_cv_path),
                }
                if _mode != "live":
                    out["note"] = "DRY RUN — nothing submitted. Add &mode=live to actually submit this ONE job."
                    self.send_json(out)
                    return

                # LIVE: submit exactly this one job, forcing past the kill-switch,
                # in the background (the page polls; a security code may be asked).
                run = _new_apply_run(user["id"], job["id"], job["title"], job["company"])
                threading.Thread(target=_supervised_live_apply, daemon=True, name="supervised-apply",
                                 args=(run, dict(user), dict(job), applicant, _cv_path, _jloc, out)).start()
                self.send_json({**out, **_apply_run_view(run)})
                return
            finally:
                conn.close()

        if path == "/api/apply-runs/waiting":
            # Applications paused on a security code the site emailed this user.
            user = self.require_auth()
            if not user:
                return
            with _APPLY_RUNS_LOCK:
                mine = [r for r in _APPLY_RUNS.values()
                        if r["user_id"] == user["id"] and r["state"] == "awaiting_code"]
            self.send_json({"waiting": [_apply_run_view(r) for r in mine]})
            return

        if path == "/api/admin/apply-run":
            # Status of a supervised live apply started by apply-test?mode=live.
            user = self.require_auth()
            if not user or user.get("role") != "admin":
                self.send_json({"error": "Forbidden"}, 403)
                return
            run = _APPLY_RUNS.get((qs.get("id", [""])[0] or "").strip())
            if not run or run["user_id"] != user["id"]:
                self.send_json({"error": "No such run (the server may have restarted)"}, 404)
                return
            self.send_json(_apply_run_view(run))
            return

        if path == "/api/admin/apply-selftest":
            # Read-only production diagnosis of the apply engine. Does NOT submit
            # anything — it launches a browser and hits example.com to prove the
            # runtime can actually drive Chromium. This is the ground-truth probe
            # for "the engine applies to nothing regardless of code fixes".
            user = self.require_auth()
            if not user or user.get("role") != "admin":
                self.send_json({"error": "Forbidden"}, 403)
                return
            import apply_engine as _ae
            import time as _t
            diag = {
                "playwright_importable": bool(getattr(_ae, "PLAYWRIGHT_AVAILABLE", False)),
                "apply_engine_enabled_env": os.environ.get("APPLY_ENGINE_ENABLED", "(unset)"),
                "gemini_key_present": bool((os.environ.get("GEMINI_API_KEY") or "").strip()),
                # db_file/db_exists named the SQLite file on the volume, which
                # production stopped using at the 2026-09-21 cutover (STATUS
                # 2026-10-07 said to ignore them). Report the database in use.
                "db_backend": database.backend(),
                "uploads_dir": UPLOADS_DIR,
                "uploads_exists": os.path.isdir(UPLOADS_DIR),
            }
            try:
                _c = database.get_db()
                try:
                    _pr = _c.execute("SELECT cv_path FROM user_profiles WHERE user_id=?", (user["id"],)).fetchone()
                    _c.close()
                    _cvp = (_pr["cv_path"] if _pr else "") or ""
                    diag["cv_in_database"] = bool(storage.meta(user["id"]))
                    _cvf = _cv_file(user["id"], _cvp)
                    diag["cv_path"] = _cvf or _cvp
                    diag["cv_exists"] = bool(_cvf)    # rebuilt from the database if the cache was cold
                finally:
                    _c.close()
            except Exception as _e:
                diag["cv_error"] = str(_e)[:300]
            # Queue composition — is there anything AUTO-SUBMITTABLE to apply to?
            # (job-board URLs are manual_required by design; dead links are unusable.)
            try:
                _c2 = database.get_db()
                try:
                    _rows = _c2.execute(
                        "SELECT status, url, COALESCE(apply_status,'') AS aps, COALESCE(url_verified,-1) AS uv, "
                        "COALESCE(apply_attempts,0) AS att FROM jobs WHERE user_id=? AND status IN ('new','approved')",
                        (user["id"],)).fetchall()
                    _allcounts = {r[0]: r[1] for r in _c2.execute(
                        "SELECT status, COUNT(*) FROM jobs WHERE user_id=? GROUP BY status",
                        (user["id"],)).fetchall()}
                    _c2.close()
                    def _bucket(rows):
                        tot = dead = board = manual = exh = sub = 0
                        for _r in rows:
                            tot += 1
                            _u = _r["url"] or ""
                            if _r["uv"] == 0: dead += 1; continue
                            if _r["aps"] == "manual_required": manual += 1; continue
                            if _ae._is_job_board(_u): board += 1; continue
                            if _r["att"] >= _ae.MAX_APPLY_ATTEMPTS: exh += 1; continue
                            sub += 1
                        return {"total": tot, "auto_submittable": sub,
                                "job_board_manual_by_design": board,
                                "flagged_manual_required": manual,
                                "dead_link": dead, "attempts_exhausted": exh}
                    diag["queue_audit"] = {
                        "status_counts": _allcounts,
                        "approved": _bucket([r for r in _rows if r["status"] == "approved"]),
                        "new_unreviewed": _bucket([r for r in _rows if r["status"] == "new"]),
                    }
                finally:
                    _c2.close()
            except Exception as _e:
                diag["queue_audit_error"] = str(_e)[:300]
            # Optional live resolver probe (read-only, no submit):
            #   /api/admin/apply-selftest?company=monday.com&title=Product+Manager
            _rc_company = (qs.get("company", [""])[0] or "").strip()
            _rc_title = (qs.get("title", [""])[0] or "").strip()
            if _rc_company and _rc_title:
                try:
                    _name_slugs = _ae._company_slugs(_rc_company)
                    try:
                        _gem_slugs = _ae._gemini_board_candidates(_rc_company, _rc_title)
                    except Exception as _ge:
                        _gem_slugs = [f"ERR:{type(_ge).__name__}:{_ge}"[:120]]
                    # Reachability: can THIS host reach the ATS APIs at all?
                    _reach = _ae._ats_get("https://boards-api.greenhouse.io/v1/boards/stripe/jobs?content=false", timeout=6)
                    _reach_n = len(_reach.get("jobs", [])) if isinstance(_reach, dict) else -1
                    _slug_counts = {}
                    _cand = _name_slugs + [g for g in _gem_slugs if isinstance(g, str) and g not in _name_slugs]
                    for _sl in _cand[:8]:
                        try:
                            _slug_counts[_sl] = len(_ae._ats_postings_for_slug(_sl))
                        except Exception:
                            _slug_counts[_sl] = -1
                    try:
                        _graw = _ae._claude(
                            f'Return ONLY a JSON array of up to 5 likely lowercase ATS board '
                            f'slugs for the company "{_rc_company}" (e.g. Wiz -> ["wizinc","wiz"]).',
                            max_tokens=128, timeout=20)
                    except Exception as _ge2:
                        _graw = f"ERR {type(_ge2).__name__}: {_ge2}"
                    _rc_loc = (qs.get("location", [""])[0] or "").strip()
                    _rr = _ae.resolve_ats_application(_rc_company, _rc_title, location=_rc_loc)
                    diag["resolver_probe"] = {
                        "company": _rc_company, "title": _rc_title,
                        "name_slugs": _name_slugs,
                        "gemini_slugs": _gem_slugs,
                        "gemini_raw": (_graw or "")[:300],
                        "greenhouse_reachable_stripe_jobs": _reach_n,
                        "slug_posting_counts": _slug_counts,
                        "resolved": _rr or None,
                    }
                except Exception as _e:
                    diag["resolver_probe"] = {"error": str(_e)[:300]}
            probe = {"launched": False}
            if diag["playwright_importable"]:
                _t0 = _t.time()
                try:
                    with _ae.sync_playwright() as _pw:
                        _b = _pw.chromium.launch(headless=True)
                        probe["chromium_version"] = _b.version
                        _pg = _b.new_page()
                        _pg.goto("https://example.com", timeout=20000)
                        probe["nav_title"] = _pg.title()
                        _b.close()
                        probe["launched"] = True
                except Exception as _e:
                    probe["error"] = f"{type(_e).__name__}: {str(_e)[:400]}"
                probe["elapsed_s"] = round(_t.time() - _t0, 1)
            else:
                probe["error"] = "playwright import failed (PLAYWRIGHT_AVAILABLE=False)"
            diag["browser_probe"] = probe
            self.send_json(diag)
            return

        if path == "/api/admin/sentry-test":
            # Proves error reporting end to end: raise a real exception, hand
            # it to errors.capture(), and say whether it was sent. Admin only,
            # read-only, and the request itself still answers 200 - a test of
            # the alarm must not set off a real 500.
            user = self.require_auth()
            if not user or user.get("role") != "admin":
                self.send_json({"error": "Forbidden"}, 403)
                return
            try:
                raise RuntimeError("Sentry test from /api/admin/sentry-test - safe to ignore")
            except RuntimeError as _test_exc:
                _sent = errors.capture(_test_exc, where="sentry-test", path=path, user_id=user.get("id"))
            self.send_json({"sent": _sent, **errors.status()})
            return

        if path == "/api/admin/link-report":
            # Step 1a of EXECUTION_PLAN_NEXT.md: what the open queue links to,
            # and how much of it the existing ATS resolver could already turn
            # into a direct posting. Read-only - no job's URL changes here.
            #   ?scope=me (default) | all     ?sample=25     ?llm=0|1
            user = self.require_auth()
            if not user or user.get("role") != "admin":
                self.send_json({"error": "Forbidden"}, 403)
                return
            import links as _links
            _scope = (qs.get("scope", ["me"])[0] or "me").strip().lower()
            try:
                _sample = max(0, min(60, int(qs.get("sample", ["25"])[0])))
            except ValueError:
                _sample = 25
            _llm = (qs.get("llm", ["0"])[0] or "0").strip() in ("1", "true", "yes")
            report = _links.link_report(database.get_db, None if _scope == "all" else user["id"],
                                        sample=_sample, use_llm=_llm, check_reach=True)
            self.send_json(report)
            return

        if path == "/api/admin/link-audit":
            # Step 2 of EXECUTION_PLAN_NEXT.md: fetch the open jobs whose links
            # are on company or aggregator sites and report what each page
            # actually shows, so the "invented slug" rule is designed from real
            # pages. Read-only.  ?scope=me|all  ?limit=40  ?types=company,aggregator
            user = self.require_auth()
            if not user or user.get("role") != "admin":
                self.send_json({"error": "Forbidden"}, 403)
                return
            # JH-4 / B5: spends Gemini (up to 30 grounded calls); a GET a
            # link elsewhere could make an admin's browser fire.
            if self.reject_cross_site():
                return
            import links as _links
            _scope = (qs.get("scope", ["me"])[0] or "me").strip().lower()
            try:
                _limit = max(0, min(80, int(qs.get("limit", ["40"])[0])))
            except ValueError:
                _limit = 40
            _types = tuple(t for t in (qs.get("types", ["company,aggregator"])[0] or "").split(",")
                           if t in _links.ORDER) or ("company", "aggregator")
            report = _links.link_audit(database.get_db, None if _scope == "all" else user["id"],
                                       types=_types, limit=_limit)
            self.send_json(report)
            return

        if path == "/api/admin/learning-report":
            # Step 12: does the learning work? Read-only, ?scope=me|all.
            user = self.require_auth()
            if not user or user.get("role") != "admin":
                self.send_json({"error": "Forbidden"}, 403)
                return
            import learningreport
            _scope = (qs.get("scope", ["me"])[0] or "me").strip().lower()
            conn = database.get_db()
            try:
                self.send_json(learningreport.report(conn, None if _scope == "all" else user["id"]))
            finally:
                conn.close()
            return

        if path == "/api/application-answers":
            user = self.require_auth()
            if not user:
                return
            self.send_json(_standing_answers(user["id"]))
            return

        if path == "/api/admin/link-search-trial":
            # Step 1c option 3, MEASUREMENT ONLY: for open LinkedIn/aggregator
            # jobs the free lookups miss, ask Gemini (Google Search grounding)
            # for the posting on the company's own site and verify the page.
            # Writes nothing. Costs one Gemini call per searched job.
            #   ?scope=me|all  ?n=20 (max 30)
            user = self.require_auth()
            if not user or user.get("role") != "admin":
                self.send_json({"error": "Forbidden"}, 403)
                return
            # JH-4 / B5: spends Gemini (up to 30 grounded calls); a GET a
            # link elsewhere could make an admin's browser fire.
            if self.reject_cross_site():
                return
            import links as _links
            _scope = (qs.get("scope", ["me"])[0] or "me").strip().lower()
            try:
                _n = max(0, min(30, int(qs.get("n", ["20"])[0])))
            except ValueError:
                _n = 20
            self.send_json(_links.search_trial(database.get_db, None if _scope == "all" else user["id"], n=_n))
            return

        if path == "/api/admin/queue-stats":
            user = self.require_auth()
            if not user or user.get("role") != "admin":
                self.send_json({"error": "Forbidden"}, 403)
                return
            _uid = user["id"]
            conn = database.get_db()
            try:
                # Reuse the SAME source the dashboard/Analytics use so numbers match
                # exactly (get_stats folds in passed_archived_count for the lifetime
                # 'rejected'/'total', so Passed here == Passed on the dashboard).
                base = database.get_stats(conn, _uid)
                def _qs(where):
                    return conn.execute("SELECT COUNT(*) FROM jobs WHERE user_id=? " + where, (_uid,)).fetchone()[0]
                stats = {
                    "new": base.get("new", 0),
                    "approved": base.get("approved", 0),
                    "applied": base.get("applied", 0),
                    "rejected": base.get("rejected", 0),
                    "deferred": base.get("deferred", 0),
                    "total": base.get("total", 0),
                    "manual_required": _qs("AND status='approved' AND COALESCE(apply_status,'')='manual_required'"),
                    "dead_links": _qs("AND status IN ('new','approved') AND url_verified=0"),
                }
                conn.close()
                self.send_json(stats)
                return
            finally:
                conn.close()

        if path == "/api/admin/dedup":
            # Deletes rows on a GET. Guarded until it can become a POST, which
            # needs a web_bundle rebuild (Phase 4).
            if self.reject_cross_site():
                return
            user = self.require_auth()
            if not user or user.get("role") != "admin":
                self.send_json({"error": "forbidden"}, 403)
                return
            conn = database.get_db()
            try:
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
            finally:
                conn.close()

        if path == "/api/admin/users":
            user = self.require_auth()
            if not user or user.get("role") != "admin":
                self.send_json({"error": "Forbidden"}, 403)
                return
            conn = database.get_db()
            try:
                rows = conn.execute("""
                SELECT u.id, u.name, u.email, u.role, u.is_active, u.created_date,
                       u.plan, u.plan_until, u.plan_after, u.plan_source,
                       (SELECT COUNT(*) FROM jobs j WHERE j.user_id=u.id AND j.status='locked')   AS stats_locked,
                       (SELECT COUNT(*) FROM jobs j WHERE j.user_id=u.id AND j.status='new')      AS stats_new,
                       (SELECT COUNT(*) FROM jobs j WHERE j.user_id=u.id AND j.status='approved') AS stats_approved,
                       (SELECT COUNT(*) FROM jobs j WHERE j.user_id=u.id AND j.status='applied')  AS stats_applied,
                       (SELECT COUNT(*) FROM jobs j WHERE j.user_id=u.id)                          AS stats_total
                FROM users u ORDER BY u.created_date DESC
            """).fetchall()
                conn.close()
                self.send_json([dict(r) for r in rows])
                return
            finally:
                conn.close()

        if path == "/api/patterns":
            user = self.require_auth()
            if not user:
                return
            conn = database.get_db()
            try:
                rows = conn.execute(
                    "SELECT * FROM rejected_patterns WHERE user_id=? ORDER BY created_date DESC LIMIT 50",
                    (user["id"],)
                ).fetchall()
                conn.close()
                self.send_json([dict(r) for r in rows])
                return
            finally:
                conn.close()

        if path == "/api/learned":
            user = self.require_auth()
            if not user:
                return
            conn = database.get_db()
            try:
                pr = database.get_pass_reason_stats(conn, user["id"])
                bl = database.get_blocklist(conn, user["id"])
                rows2 = conn.execute(
                    "SELECT id, company, title, notes, created_date FROM rejected_patterns WHERE user_id=? ORDER BY created_date DESC LIMIT 100",
                    (user["id"],)
                ).fetchall()
                conn.close()
                self.send_json({
                    "pass_reasons": [{"reason": k, "count": v} for k, v in pr.items()],
                    "blocklist": bl,
                    "patterns": [dict(r) for r in rows2],
                })
                return
            finally:
                conn.close()

        # ── Sync: export approved jobs for relay/scheduled tasks ──
        if path == "/api/sync/approved":
            qs = parse_qs(parsed.query)
            if not self._check_sync_key(qs.get("api_key", [""])[0]):
                self.send_json({"error": "Unauthorized"}, 401)
                return
            conn = database.get_db()
            try:
                rows = conn.execute("SELECT * FROM jobs WHERE status='approved'").fetchall()
                conn.close()
                self.send_json([dict(r) for r in rows])
                return
            finally:
                conn.close()

        self.send_response(404)
        self.end_headers()

    # ── POST ──────────────────────────────────────────────────────────────────

    def do_POST(self):
        with _log.request("POST", self.path, self.client_ip()):
            self._do_POST_guarded()

    def _do_POST_guarded(self):
        try:
            self._do_POST_inner()
        except Exception as exc:
            import traceback
            print(f"[do_POST] ❌ Unhandled exception on {self.path}: {exc}\n{traceback.format_exc()}")
            errors.capture(exc, where="do_POST", path=self.path,
                           user_id=_log.current_user_id())
            try:
                self.send_json({"error": f"Server error: {exc}"}, code=500)
            except Exception:
                pass

    def _do_POST_inner(self):
        parsed = urlparse(self.path)
        path   = parsed.path

        # "Stop these emails" (lifecycle.py): the confirm button, and mail
        # providers' one-click unsubscribe (RFC 8058), which POSTs from their
        # servers. Authenticated by the link's token, not a cookie, so it sits
        # before the cross-site check.
        if path == "/email/stop":
            import lifecycle
            _q = parse_qs(parsed.query)
            _u, _t = (_q.get("u") or [""])[0], (_q.get("t") or [""])[0]
            if not lifecycle.unsubscribe_ok(_u, _t):
                self.send_html(_stop_page("This link doesn't work", "It may be incomplete."), 400)
                return
            _sc = database.get_db()
            try:
                _sc.execute("UPDATE users SET lifecycle_opt_out=1 WHERE id=?", (int(_u),))
                _sc.commit()
            finally:
                _sc.close()
            _track(int(_u), "lifecycle_unsubscribed")
            self.send_html(_stop_page("Done", "You won't get these emails any more. Noa keeps finding your "
                                      "picks in the app."))
            return

        # SameSite=Lax already stops the browser sending the session cookie on a
        # cross-site POST. This is the second lock: it costs one header read and
        # does not depend on cookie-policy behaviour staying as it is today.
        if self.reject_cross_site():
            return

        # ── Login ──
        if path == "/login":
            body = urllib.parse.parse_qs(self.read_body().decode())
            email    = body.get("email", [""])[0]
            password = body.get("password", [""])[0]
            _ip = (self.headers.get("X-Forwarded-For", "") or "").split(",")[0].strip() or (self.client_address[0] if self.client_address else "?")
            _ipk = "ip:" + _ip
            _emk = "em:" + email.strip().lower()
            _wait = _login_check(_ipk) or _login_check(_emk)
            if _wait:
                mins = max(1, _wait // 60)
                html = LOGIN_HTML.replace("{error_block}", error_block(f"Too many login attempts. Please try again in about {mins} minute(s)."))
                self.send_html(html, 429)
                return
            user, err = auth.authenticate(email, password)
            if err:
                _login_fail(_ipk); _login_fail(_emk)
                html = LOGIN_HTML.replace("{error_block}", error_block(err))
                self.send_html(html)
                return
            _login_ok(_ipk); _login_ok(_emk)
            token = auth.create_session(user["id"])
            self.send_response(302)
            self.send_header("Set-Cookie", auth.make_session_cookie(token))
            dest = home_url()
            self.send_header("Location", dest)
            self.end_headers()
            return

        # ── Forgot password: the same answer whether or not the address has an
        # account, so the page cannot be used to find out who is signed up. ──
        if path == "/forgot-password":
            body = urllib.parse.parse_qs(self.read_body().decode())
            email = (body.get("email", [""])[0] or "").strip().lower()
            _w = ratelimit.check_and_record("reset", "ip:" + self.client_ip()) or \
                 (email and ratelimit.check_and_record("reset", "em:" + email))
            if _w:
                self.send_html(FORGOT_HTML.replace("{error_block}", error_block(
                    "Too many requests. Try again in about %d minute(s)." % max(1, _w // 60))), 429)
                return
            if email:
                _c = database.get_db()
                try:
                    _u = _c.execute("SELECT id, email FROM users WHERE email=? AND is_active=1", (email,)).fetchone()
                finally:
                    _c.close()
                if _u:
                    try:
                        _send_reset(_u["id"], _u["email"])
                    except Exception as _e:
                        print("[auth] reset mail to user %s failed: %s" % (_u["id"], _e))
            self.send_html(FORGOT_HTML.replace("{error_block}", notice_block(
                "If there's an account for that address, we've sent it a link. It works for 1 hour - "
                "check your spam folder if it isn't there in a minute.")))
            return

        if path == "/reset-password":
            body = urllib.parse.parse_qs(self.read_body().decode())
            tok = body.get("token", [""])[0]
            pw, pw2 = body.get("password", [""])[0], body.get("password2", [""])[0]
            def _again(msg):
                self.send_html(RESET_HTML.replace("{error_block}", error_block(msg))
                               .replace("{token}", html_lib.escape(tok, quote=True)))
            if len(pw) < 8:
                _again("Password must be at least 8 characters."); return
            if pw != pw2:
                _again("Passwords don't match."); return
            _uid = auth.use_token(tok, "reset")
            if not _uid:
                self.send_html(LINK_EXPIRED_HTML, 410)
                return
            _n = auth.reset_password(_uid, pw)
            print("[auth] password reset for user %s; %s session(s) ended" % (_uid, _n))
            self.redirect("/login?notice=reset")
            return

        # ── Register ──
        # Lifecycle emails (lifecycle.py): every variant sent to the admin's own
        # inbox, from the sender it would really use, so the copy is reviewed as
        # the user will see it. Admin only; works with LIFECYCLE_EMAILS off.
        if path == "/api/admin/lifecycle-test":
            user = self.require_auth()
            if not user:
                return
            if user.get("role") != "admin":
                self.send_json({"error": "Forbidden"}, 403)
                return
            import lifecycle
            _first = (user.get("name") or "").split()[0] if (user.get("name") or "").strip() else ""
            _sent, _errs = [], []
            for _pv in lifecycle.previews(_first, MOBILE_URL.rstrip("/") + "/app",
                                          lifecycle.unsubscribe_url(MOBILE_URL, user["id"])):
                try:
                    send_email(user["email"], "[%s / %s] %s" % (_pv["step"], _pv["variant"], _pv["subject"]),
                               _pv["body"], sender=_pv["sender"])
                    _sent.append("%s/%s" % (_pv["step"], _pv["variant"]))
                except Exception as _le:
                    _errs.append("%s/%s: %s" % (_pv["step"], _pv["variant"], _le))
            self.send_json({"sent": _sent, "errors": _errs,
                            "noa_from_set": bool(NOA_FROM), "eran_from_set": bool(ERAN_FROM),
                            "enabled": lifecycle.enabled()})
            return

        if path == "/api/admin/test-account/reset":
            user = self.require_auth()
            if not user:
                return
            if user.get("role") != "admin":
                self.send_json({"error": "Forbidden"}, 403)
                return
            import testaccount
            _fresh = testaccount.reset(database.get_db)
            print("[test-account] reset by admin %s: user id %s, removed %s"
                  % (user.get("id"), _fresh["id"], sum(_fresh["removed"].values())))
            self.send_json({"success": True, "email": _fresh["email"], "password": _fresh["password"]})
            return

        if path == "/register":
            # Nothing stopped a script creating accounts in a loop. Attempt-based,
            # because here the call IS the abuse - there is no "failure" to count.
            _rwait = ratelimit.check_and_record("register", self.client_ip())
            if _rwait:
                _mins = max(1, _rwait // 60)
                self.send_html(REGISTER_HTML.replace("{error_block}", error_block(
                    f"Too many accounts created from this address. Try again in about {_mins} minute(s).")), 429)
                return
            body = urllib.parse.parse_qs(self.read_body().decode())
            _attr    = analytics.clean_attribution(body)
            name     = body.get("name", [""])[0].strip()
            email    = body.get("email", [""])[0]
            password = body.get("password", [""])[0]
            password2 = body.get("password2", [""])[0]
            if not name or not email or not password:
                self.send_html(_register_page(error_block("All fields are required."), _attr))
                return
            if password != password2:
                self.send_html(_register_page(error_block("Passwords don't match."), _attr))
                return
            if len(password) < 8:
                self.send_html(_register_page(error_block("Password must be at least 8 characters."), _attr))
                return
            user_id, err = auth.create_user(name, email, password)
            if err:
                self.send_html(_register_page(error_block(err), _attr))
                return
            _funnel(user_id, "signup")
            _track(user_id, "user_signed_up", dict(_attr, method="email"), set_once=_attr)
            try:
                _send_verification(user_id, email.strip().lower(), name)
            except Exception as _ve:
                print("[auth] verification mail for user %s failed: %s" % (user_id, _ve))
            token = auth.create_session(user_id)
            self.send_response(302)
            self.send_header("Set-Cookie", auth.make_session_cookie(token))
            # /app, not /onboarding. The setup flow lives inside the app now and
            # decides for itself whether to show, from onboarding_complete and
            # onboarding_dismissed on /api/me - so a user who abandons it halfway
            # gets it back, and a returning user never sees it. Sending them to
            # the legacy page was also the last route by which a brand-new
            # account met the old design before anything else.
            self.send_header("Location", ONBOARDING_ENTRY)
            self.end_headers()
            return

        # All API POST routes require auth - except /api/sync/*, which relay.py
        # and scheduled tasks call with the sync key and no session. JH-2
        # (2026-11-05): this gate sat in front of the sync routes, so every
        # relay POST got 401 before its key was ever checked; each sync handler
        # below checks the key itself.
        user = self.get_user()
        if not user and not path.startswith("/api/sync/"):
            self.send_json({"error": "Unauthorized"}, 401)
            return
        user_id = user["id"] if user else None

        # A locked match (beyond the plan's per-search limit) cannot be acted on
        # by any per-job route - approve, restore, apply, cover letter, check -
        # whatever the route itself checks. One guard here, so a route added
        # later is covered too (Phase 5b).
        _lm = re.match(r"^/api/jobs/(\d+)/", path)
        if _lm:
            _lc = database.get_db()
            try:
                _is_locked = entitlements.is_locked(_lc, user_id, int(_lm.group(1)))
            finally:
                _lc.close()
            if _is_locked:
                self.send_json(entitlements.upgrade_required(
                    "locked_match", user, message="Upgrade your plan to see this match."), 402)
                return
            # A job still in Noa's reserve (daily picks) does not exist yet for the user.
            _hc = database.get_db()
            try:
                _is_held = entitlements.is_held(_hc, user_id, int(_lm.group(1)))
            finally:
                _hc.close()
            if _is_held:
                self.send_json({"error": "Job not found"}, 404)
                return

        # ── Delete my account (Step 6) ────────────────────────────────────────
        # Scheduled accountdata.GRACE_DAYS ahead; every session ends now, and
        # signing in before the date cancels it. The body must repeat the
        # account's email, so a stray POST cannot schedule it.
        if path == "/api/account/delete":
            data = self.read_json()
            if (user.get("role") or "").lower() == "admin":
                self.send_json({"error": "The admin account cannot be deleted from the app.",
                                "code": "admin_cannot_delete"}, 400)
                return
            if (str(data.get("confirm_email") or "").strip().lower()
                    != (user.get("email") or "").strip().lower()):
                self.send_json({"error": "Type your account's email to confirm.",
                                "code": "confirm_email_mismatch"}, 400)
                return
            conn = database.get_db()
            try:
                when = accountdata.request_deletion(conn, user_id)
            finally:
                conn.close()
            try:
                database.log_activity(user_id, "account_deletion_requested", "Deletion scheduled for %s UTC" % when)
            except Exception:
                pass
            print(f"[account] user {user_id} requested deletion; scheduled {when} UTC")
            body = json.dumps({"success": True, "delete_after": when,
                               "grace_days": accountdata.GRACE_DAYS}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Set-Cookie", auth.clear_session_cookie())
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # ── Send the "confirm your email" mail again (Step 5) ──
        if path == "/api/auth/resend-verification":
            if auth.is_email_verified(user_id):
                self.send_json({"success": True, "already": True})
                return
            _w = ratelimit.check_and_record("verify", "u:%s" % user_id)
            if _w:
                self.send_json({"error": "You've asked for several emails already. Try again in about %d minute(s)."
                                % max(1, _w // 60), "code": "rate_limited"}, 429)
                return
            _c = database.get_db()
            try:
                _u = _c.execute("SELECT email, name FROM users WHERE id=?", (user_id,)).fetchone()
            finally:
                _c.close()
            try:
                _send_verification(user_id, _u["email"], _u["name"] or "")
            except Exception as _e:
                print("[auth] resend verification for user %s failed: %s" % (user_id, _e))
                self.send_json({"error": "The email could not be sent. Try again in a few minutes.", "code": "send_failed"}, 502)
                return
            self.send_json({"success": True, "sent_to": _u["email"]})
            return

        # ── Contact support (2026-11-09) ──────────────────────────────────────
        # Emailed to the admin with Reply-To the user. 5 a day per user; a
        # failed send is reported as a failure, never as "sent".
        if path == "/api/support":
            data = self.read_json() or {}
            message = str(data.get("message") or "").strip()
            if not message:
                self.send_json({"error": "Write a message first.", "code": "empty"}, 400)
                return
            if len(message) > SUPPORT_MAX_CHARS:
                self.send_json({"error": "Keep it under %d characters." % SUPPORT_MAX_CHARS,
                                "code": "too_long"}, 400)
                return
            _swait = ratelimit.check_and_record("support", str(user_id))
            if _swait:
                self.send_json({"error": "You've sent several messages today. Try again tomorrow.",
                                "code": "rate_limited", "retry_after": _swait}, 429)
                return
            try:
                send_support_message(user, message, str(data.get("subject") or ""),
                                     self.headers.get("User-Agent", ""))
            except Exception as e:
                print(f"[support] ERROR message from user {user_id} not sent: {e}")
                self.send_json({"error": "Your message couldn't be sent right now. Please try again later.",
                                "code": "send_failed"}, 503)
                return
            try:
                database.log_activity(user_id, "support_message", "Sent a message to support")
            except Exception as e:
                print(f"[support] WARNING activity log failed for user {user_id}: {e}")
            self.send_json({"success": True})
            return

        if path == "/api/blocklist":
            data = self.read_json()
            company = (data.get("company") or "").strip()
            if company:
                conn = database.get_db()
                try:
                    database.add_to_blocklist(conn, user_id, company, data.get("reason", ""))
                    conn.close()
                finally:
                    conn.close()
            self.send_json({"success": True})
            return

        if path == "/api/blocklist/remove":
            data = self.read_json()
            company = (data.get("company") or "").strip()
            if company:
                conn = database.get_db()
                try:
                    database.remove_from_blocklist(conn, user_id, company)
                    conn.close()
                finally:
                    conn.close()
            self.send_json({"success": True})
            return

        if path == "/api/patterns/forget":
            data = self.read_json()
            pid = data.get("id")
            conn = database.get_db()
            try:
                if pid is not None:
                    conn.execute("DELETE FROM rejected_patterns WHERE id=? AND user_id=?", (pid, user_id))
                conn.commit(); conn.close()
                self.send_json({"success": True})
                return
            finally:
                conn.close()

        if path == "/api/push/subscribe":
            data = self.read_json()
            sub = data.get("subscription") or data
            endpoint = (sub or {}).get("endpoint")
            if not endpoint:
                self.send_json({"error": "no endpoint"}, 400)
                return
            conn = database.get_db()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO push_subscriptions (user_id, endpoint, subscription, created_date) "
                    "VALUES (?,?,?,?)",
                    (user_id, endpoint, json.dumps(sub), datetime.now().isoformat())
                )
                conn.commit(); conn.close()
                self.send_json({"success": True})
                return
            finally:
                conn.close()

        if path == "/api/push/unsubscribe":
            data = self.read_json()
            endpoint = data.get("endpoint")
            if endpoint:
                conn = database.get_db()
                try:
                    conn.execute("DELETE FROM push_subscriptions WHERE user_id=? AND endpoint=?", (user_id, endpoint))
                    conn.commit(); conn.close()
                finally:
                    conn.close()
            self.send_json({"success": True})
            return

        if path == "/api/push/test":
            # Reports what happened; it used to answer success whatever happened.
            r = send_web_push_to_user(user_id, "Test notification from Job Hunter \u2014 push is working!", "/app")
            ok = r["sent"] > 0
            self.send_json({"success": ok, **r, "error": "" if ok else (
                "; ".join(r["errors"]) or "This account has no device with notifications on")}, 200 if ok else 502)
            return

        # ── Admin: prove every notification channel live (2026-11-08) ──
        if path == "/api/admin/notify-check":
            if (user.get("role") or "") != "admin":
                self.send_json({"error": "Forbidden"}, 403)
                return
            res = deliver_notification(
                user_id, "\U0001F514 Job Hunter notification check \u2014 if you can read this, this channel works.",
                url_suffix="/app")
            self.send_json({"results": res or {}, **_notify_status(user_id)})
            return

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
                _orig_name = os.path.basename(filename) or "cv.pdf"
                # Phase 2d: store the bytes first - the volume copy that
                # storage.put() writes afterwards is a cache, and a lost volume
                # must not be able to lose a CV.
                try:
                    _stored = storage.put(user_id, file_data, _orig_name)
                except ValueError as _ve:
                    self.send_json({"error": str(_ve)})
                    return
                cv_path = _stored["path"]
                print("[cv] stored %d bytes for user %s (sha256 %s)"
                      % (_stored["size"], user_id, _stored["sha256"][:12]))
                _uploaded_at = datetime.now().isoformat(timespec="seconds")
                auth.update_profile(user_id, cv_path=cv_path, cv_analyzed=0,
                                    cv_filename=_orig_name, cv_uploaded_date=_uploaded_at)
                database.log_activity(user_id, "cv_uploaded", f"Uploaded new CV: {_orig_name}")
                print(f"[cv] ✅ Saved CV for user {user_id}: {cv_path} (orig={_orig_name})")
                self.send_json({"success": True, "path": cv_path,
                                "filename": _orig_name, "uploaded_date": _uploaded_at})
                bump_onboarding(user_id, "cv_uploaded")
                _funnel(user_id, "cv")
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
            if not GEMINI_KEY:
                self.send_json({"error": "No AI API key configured. Set GEMINI_API_KEY in Railway environment variables."})
                return
            # Get CV path from profile
            conn = database.get_db()
            try:
                row = conn.execute("SELECT cv_path FROM user_profiles WHERE user_id=?", (user_id,)).fetchone()
                conn.close()
                cv_path = _cv_file(user_id, (row["cv_path"] or "") if row else "")
                if not cv_path:
                    self.send_json({"error": "No CV uploaded yet. Please upload your PDF first."})
                    return
                try:
                    data = analyze_cv(cv_path, GEMINI_KEY, user_id=user_id)
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
                    # Profile details the CV carries (2026-11-12, the owner): fill
                    # LinkedIn and phone from the CV, but only where the user has
                    # not entered their own - analyze_cv extracted both all along
                    # and this route dropped them.
                    _pf = database.get_db()
                    try:
                        _cur = _pf.execute("SELECT linkedin_url, phone, email_address FROM user_profiles WHERE user_id=?",
                                           (user_id,)).fetchone()
                        _urow = _pf.execute("SELECT name FROM users WHERE id=?", (user_id,)).fetchone()
                    finally:
                        _pf.close()
                    _fill = {}
                    for _k, _src in (("linkedin_url", "linkedin_url"), ("phone", "phone"), ("email_address", "email")):
                        _v = str(data.get(_src) or "").strip()[:200]
                        if _k == "email_address" and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", _v):
                            _v = ""
                        if _v and not ((_cur[_k] if _cur else "") or "").strip():
                            _fill[_k] = _v
                    if _fill:
                        auth.update_profile(user_id, **_fill)
                    _filled = sorted(_fill)
                    _nm = str(data.get("full_name") or "").strip()[:120]
                    if _nm and not ((_urow["name"] if _urow else "") or "").strip():
                        auth.update_user(user_id, name=_nm)
                        _filled.append("name")
                    data["profile_filled"] = _filled
                    database.write_users_config(BASE_DIR)
                    database.log_activity(user_id, "cv_analyzed",
                        f"AI extracted {len(data.get('job_titles',[]))} job titles, "
                        f"{len(data.get('keywords',[]))} keywords")
                    self.send_json(data)
                except Exception as e:
                    print(f"[analyze] Error: {e}")
                    self.send_json({"error": str(e)})
                return
            finally:
                conn.close()

        # ── Save profile ──
        # ── The security code a site emailed during a supervised apply ──
        if path in ("/api/apply-runs/code", "/api/admin/apply-run/code"):
            # Any user, for their own run (the supervised admin page uses the
            # second path; same rules).
            data = self.read_json() or {}
            run = _APPLY_RUNS.get(str(data.get("run_id") or ""))
            if not run or run["user_id"] != user_id:
                self.send_json({"error": "No such run (the server may have restarted)"}, 404)
                return
            if run["state"] != "awaiting_code":
                self.send_json({"error": "This apply is not waiting for a code"}, 409)
                return
            code = re.sub(r"\s+", "", str(data.get("code") or ""))
            if not re.fullmatch(r"[A-Za-z0-9]{4,12}", code):
                self.send_json({"error": "The code should be 4-12 letters or digits"}, 400)
                return
            run["code"] = code
            run["code_event"].set()
            self.send_json({"success": True, **_apply_run_view(run)})
            return

        # ── Standing answers for application forms (2026-11-07) ──
        # The application_answers table existed since the baseline schema and
        # nothing used it. Salary expectation (minimum, gross per month) and
        # notice period (default 30 days - the owner) feed the question answerer.
        if path == "/api/application-answers":
            data = self.read_json() or {}
            vals = {}
            if "salary_expectation_min" in data:
                v = data["salary_expectation_min"]
                try:
                    v = None if v in (None, "") else int(float(str(v).replace(",", "")))
                except ValueError:
                    self.send_json({"error": "salary_expectation_min must be a number"}, 400)
                    return
                if v is not None and not (0 < v < 10_000_000):
                    self.send_json({"error": "salary_expectation_min is out of range"}, 400)
                    return
                vals["salary_expectation_min"] = v
            if "salary_expectation_currency" in data:
                cur = str(data["salary_expectation_currency"] or "ILS").upper()
                if cur not in ("ILS", "USD", "EUR", "GBP"):
                    self.send_json({"error": "currency must be ILS, USD, EUR or GBP"}, 400)
                    return
                vals["salary_expectation_currency"] = cur
            if "notice_period_days" in data:
                try:
                    n = int(data["notice_period_days"])
                except (TypeError, ValueError):
                    self.send_json({"error": "notice_period_days must be a whole number"}, 400)
                    return
                if not 0 <= n <= 365:
                    self.send_json({"error": "notice_period_days must be 0-365"}, 400)
                    return
                vals["notice_period_days"] = n
            if "standing_notes" in data:
                txt = str(data["standing_notes"] or "").strip()
                if len(txt) > 3000:
                    self.send_json({"error": "Application instructions are limited to 3,000 characters"}, 400)
                    return
                vals["standing_notes"] = txt
            if vals:
                cols = ", ".join(vals)
                conn = database.get_db()
                try:
                    conn.execute(
                        "INSERT INTO application_answers (user_id, %s, updated_date) VALUES (?, %s, ?) "
                        "ON CONFLICT (user_id) DO UPDATE SET %s, updated_date=excluded.updated_date"
                        % (cols, ", ".join("?" for _ in vals), ", ".join("%s=excluded.%s" % (c, c) for c in vals)),
                        (user_id, *vals.values(), datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                    conn.commit()
                finally:
                    conn.close()
            self.send_json({"success": True, **_standing_answers(user_id)})
            return

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
            for field in ("salary_min", "salary_max", "experience_years"):
                if field in data:
                    kwargs[field] = int(data[field])
            if "seniority" in data:
                kwargs["seniority"] = data["seniority"]
            for field in ("sponsorship_local", "sponsorship_abroad"):
                if field in data:
                    val = str(data[field] or "").strip().lower()
                    if val not in ("", "no", "yes"):
                        self.send_json({"error": f"{field} must be yes or no"}, 400)
                        return
                    kwargs[field] = val
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
            # Settings sends only {"channel"}; the route read the credentials
            # from the request body alone, so it tested Telegram/WhatsApp/email
            # with empty values. send_telegram swallowed that and the button said
            # "Sent" - until #58 made it raise ("Failed: telegram", the owner,
            # 2026-11-08). Saved details now fill whatever the request leaves out.
            try:
                _pc = database.get_db()
                try:
                    _saved = _pc.execute("SELECT telegram_token, telegram_chat_id, twilio_account_sid, "
                                         "twilio_auth_token, whatsapp_number, email_address "
                                         "FROM user_profiles WHERE user_id=?", (user_id,)).fetchone()
                finally:
                    _pc.close()
                _saved = dict(crypto.decrypt_row(_saved)) if _saved else {}
            except Exception as _se:
                print(f"[notify] test: could not read saved details for user {user_id}: {_se}")
                _saved = {}
            for _k, _v in _saved.items():
                if not (data.get(_k) or "").strip():
                    data[_k] = _v or ""
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
            # Turning auto-apply ON is an entitlement, so it is checked HERE and
            # not only in the UI: a disabled button is a hint, and the request
            # behind it is three lines of curl. Turning it OFF is always allowed
            # - nobody should need a subscription to stop the robot.
            if kwargs.get("auto_apply_enabled") and not entitlements.can_auto_apply(user):
                self.send_json({
                    "error": "Auto-apply is available on a paid plan.",
                    "code": "upgrade_required",
                    "plan": entitlements.plan_of(user),
                }, 403)
                return
            if "schedule_frequency" in data:
                # Admin is always daily regardless of what was sent
                if user.get("role") == "admin":
                    kwargs["schedule_frequency"] = "daily"
                else:
                    # A daily search is a plan feature (Free is weekly). The
                    # scheduler enforces it too, so a downgrade needs no rewrite.
                    if (data["schedule_frequency"] == "daily"
                            and entitlements.limit(user, "search_cadence") == "weekly"):
                        self.send_json(entitlements.upgrade_required(
                            "search_cadence", user,
                            message="Daily searches are included from the Premium plan."), 402)
                        return
                    kwargs["schedule_frequency"] = data["schedule_frequency"]
            if kwargs:
                auth.update_profile(user_id, **kwargs)
            # Setup just ended (0 -> 1): start the first search now rather than
            # at the scheduled hour. A replay (already complete) starts nothing.
            first_search = False
            if kwargs.get("onboarding_complete") and not user.get("onboarding_complete"):
                first_search = _start_first_search(user_id)
                _track(user_id, "onboarding_completed", {"first_search_started": first_search})
            database.write_users_config(BASE_DIR)
            bump_onboarding(user_id, "auto_apply_choice_made")
            self.send_json({"success": True, "first_search_started": first_search})
            return

        # ── Dismiss onboarding ──
        if path == "/api/dismiss-onboarding":
            auth.update_profile(user_id, onboarding_dismissed=1)
            self.send_json({"success": True})
            return

        elif self.path == '/api/cv-optimizer-analyze':
            if self.command == 'GET':
                _conn = database.get_db()
                try:
                    _prof = _conn.execute('SELECT cv_optimizer_result, cv_optimizer_date FROM user_profiles WHERE user_id=?', (user_id,)).fetchone()
                finally:
                    _conn.close()
                if _prof and _prof['cv_optimizer_result']:
                    _res = json.loads(_prof['cv_optimizer_result'])
                    _res['cached'] = True; _res['analyzed_date'] = _prof['cv_optimizer_date']
                    self.send_json(_res); return
                self.send_json({'cached': False}); return
            if self.command == 'POST':
                try:
                    from datetime import datetime as _dt, timedelta as _td
                    _conn = database.get_db()
                    try:
                        _prof = _conn.execute('SELECT cv_path, cv_summary, cv_optimizer_result, cv_optimizer_date FROM user_profiles WHERE user_id=?', (user_id,)).fetchone()
                    finally:
                        _conn.close()
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
                    # Through _cv_file: the raw path is a cache a fresh container
                    # does not have; the CV is in the database (Phase 2d).
                    _cv_path = _cv_file(user_id, _prof['cv_path'] or '') or ''
                    _cv_text = _extract_cv_text(_cv_path, _prof['cv_summary'])
                    if not _cv_path and (not _cv_text or len(_cv_text.strip()) < 30):
                        self.send_json({'error': 'No CV found. Please upload your CV first.'}); return
                    _qc = database.get_db()
                    try:
                        _ok, _used, _cap = entitlements.allowed(_qc, user, 'cv_review')
                    finally:
                        _qc.close()
                    if not _ok:
                        self.send_json(entitlements.upgrade_required(
                            'cv_review_limit', user, used=_used, limit=_cap,
                            message="You've used all %d CV reviews this month." % (_cap or 0)), 402)
                        return
                    _result = _call_gemini_cv_optimizer(cv_text=_cv_text, cv_path=_cv_path, user_id=user_id)
                    _qc = database.get_db()
                    try:
                        entitlements.try_consume(_qc, user, 'cv_review')
                    finally:
                        _qc.close()
                    _result['cached'] = False
                    _now = _dt.now().isoformat()
                    _result['analyzed_date'] = _now
                    _conn = database.get_db()
                    try:
                        _conn.execute('UPDATE user_profiles SET cv_optimizer_result=?, cv_optimizer_date=? WHERE user_id=?',
                                      (json.dumps(_result), _now, user_id))
                        _conn.commit()
                    finally:
                        _conn.close()
                    self.send_json(_result)
                except Exception as _e:
                    self.send_json({'error': str(_e)}, 500)
        # ── Validate queue/new links: drop dead postings ──
        if path == "/api/validate-links":
            conn = database.get_db()
            try:
                rows = conn.execute(
                    "SELECT id, url, title, company, status FROM jobs "
                    "WHERE user_id=? AND status IN ('new','approved')",
                    (user_id,)
                ).fetchall()
                jobs_to_check = [dict(r) for r in rows]
                conn.close()

                import concurrent.futures as _cf
                results = []
                if jobs_to_check:
                    with _cf.ThreadPoolExecutor(max_workers=8) as _ex:
                        _futs = {_ex.submit(_link_status, j["url"]): j for j in jobs_to_check}
                        for _fut in _cf.as_completed(_futs):
                            results.append((_futs[_fut], _fut.result()))

                checked = len(results)
                removed = alive = unknown = 0
                removed_items = []
                _now_iso = datetime.now().isoformat(timespec="seconds")
                conn = database.get_db()
                try:
                    for j, st in results:
                        if st == "dead":
                            conn.execute(
                                "UPDATE jobs SET status='rejected', rejected_by='system', "
                                "notes=TRIM(COALESCE(notes,'') || ' [auto-removed: dead link]'), "
                                "url_verified=0, url_check_date=? WHERE id=? AND user_id=?",
                                (_now_iso, j["id"], user_id))
                            removed += 1
                            removed_items.append({"title": j.get("title") or "Untitled",
                                                  "company": j.get("company") or ""})
                        elif st == "alive":
                            conn.execute(
                                "UPDATE jobs SET url_verified=1, url_check_date=? WHERE id=? AND user_id=?",
                                (_now_iso, j["id"], user_id))
                            alive += 1
                        else:  # unknown - keep, just timestamp the check
                            conn.execute(
                                "UPDATE jobs SET url_check_date=? WHERE id=? AND user_id=?",
                                (_now_iso, j["id"], user_id))
                            unknown += 1
                    conn.commit()
                    conn.close()
                    if checked:
                        database.log_activity(user_id, "links_validated",
                            f"Checked {checked} link(s): removed {removed} dead, {alive} alive, {unknown} unverified")
                    self.send_json({"checked": checked, "removed": removed, "alive": alive,
                                    "unknown": unknown, "removed_items": removed_items})
                    return
                finally:
                    conn.close()
            finally:
                conn.close()

        # ── Change password ──
        if path == "/api/change-password":
            data = self.read_json()
            # The caller's own token is kept so they are not signed out of the
            # tab they just typed in; every OTHER session for this user dies
            # with the old password.
            err = auth.change_password(user_id, data.get("current_password", ""),
                                       data.get("new_password", ""),
                                       keep_token=auth.get_token_from_request(self.headers))
            if err:
                self.send_json({"success": False, "error": err})
            else:
                self.send_json({"success": True,
                                "signed_out_other_sessions": True})
            return

        # ── Job actions ──
        m = re.match(r"^/api/jobs/(\d+)/(approve|reject|later|applied|failed|retry|restore)$", path)
        if m:
            job_id = int(m.group(1))
            action = m.group(2)
            data   = self.read_json()
            conn   = database.get_db()
            try:

                # Verify ownership
                job = conn.execute(
                    "SELECT * FROM jobs WHERE id=? AND user_id=?", (job_id, user_id)
                ).fetchone()
                if not job:
                    conn.close()
                    self.send_json({"error": "Not found"}, 404)
                    return

                status_map = {"approve":"approved","reject":"rejected","later":"deferred","applied":"applied","failed":"approved","retry":"approved","restore":"new"}
                if action == "later" and not entitlements.limit(user, "deferred_list"):
                    conn.close()
                    self.send_json(entitlements.upgrade_required(
                        "deferred_list", user, message="Saving jobs for later is included from the Premium plan."), 402)
                    return
                if action == "restore":
                    conn.execute(
                        "UPDATE jobs SET status='new', apply_status=NULL, "
                        "apply_error=NULL, apply_confirmation=NULL, "
                        "apply_failure_type=NULL, apply_failure_detail=NULL, "
                        "apply_attempts=0, apply_next_attempt_at=NULL, applied_date=NULL, notes='', url_verified=NULL "
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
                        "apply_attempts=0, apply_next_attempt_at=NULL, applied_date=NULL, notes='' "
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
                    conn.execute(
                        "UPDATE jobs SET status=?, applied_date=?, notes=?, apply_status=?, "
                        "applied_via=? WHERE id=?",
                        (new_status, datetime.now().isoformat(), reason,
                         "submitted" if action == "applied" else "failed",
                         "manual" if action == "applied" else None, job_id))
                else:
                    # rejected_by separates the user's own passes from the system
                    # auto-rejecting dead, expired and already-attempted jobs. The
                    # approval rate is only meaningful over the ones he saw.
                    conn.execute(
                        "UPDATE jobs SET status=?, notes=?, rejected_by=? WHERE id=?",
                        (new_status, reason,
                         "user" if action == "reject" else None, job_id))

                if action == "reject":
                    # Step 13: "Salary too low" keeps the job's salary when its
                    # text states one, so the floor can be learned from it.
                    _sal = None
                    if "salary" in (reason or "").lower():
                        import salary as _salary
                        _sal = _salary.parse(" ".join(str(job[k] or "") for k in ("title", "description")
                                                      if k in job.keys()))
                    conn.execute(
                        "INSERT INTO rejected_patterns (user_id,company,title,notes,location,created_date,"
                        "salary_monthly,salary_currency) VALUES (?,?,?,?,?,?,?,?)",
                        (user_id, job["company"], job["title"],
                         reason or "No reason given",
                         (job["location"] if "location" in job.keys() else None),
                         datetime.now().isoformat(),
                         _sal["monthly_max"] if _sal else None, _sal["currency"] if _sal else None)
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
                if action == "approve":
                    _funnel(user_id, "first_approve")
                    _track(user_id, "job_approved")
                elif action == "reject":
                    _track(user_id, "job_passed", {"reason": (reason or "")[:60]})

                # On approval: kick off a background application ONLY if the user has
                # auto-apply enabled. Otherwise approving just shortlists the job (moves
                # it to the Approved queue); the user applies explicitly via "Apply Now"
                # or the scheduled apply run. This keeps the apply engine OUT of the
                # search/review flow unless auto-apply was deliberately turned on.
                if action == "approve":
                    _aa_conn = database.get_db()
                    try:
                        _aa_row = _aa_conn.execute(
                            "SELECT auto_apply_enabled FROM user_profiles WHERE user_id=?",
                            (user_id,)
                        ).fetchone()
                        _aa_conn.close()
                        if _aa_row and _aa_row["auto_apply_enabled"] and entitlements.can_auto_apply(user):
                            _trigger_apply_bg(user_id, job_id)
                        elif _aa_row and _aa_row["auto_apply_enabled"]:
                            # Flag set, entitlement gone (downgrade, or a row that
                            # predates the gate). Shortlist rather than apply.
                            print(f"[approve] job {job_id} shortlisted (auto-apply flag set but plan does not allow it)")
                        else:
                            print(f"[approve] job {job_id} shortlisted (auto-apply off — not applying)")
                    finally:
                        _aa_conn.close()

                self.send_json({"success": True})
                return
            finally:
                conn.close()

        # ── Manual "Apply Now" trigger (also used by Retry) ─────────────────
        # ── Answers to the questions an application was held on (2026-11-07) ──
        #   {"answers": [{"key", "label", "answer"}]}  answer: text, or a list
        # Saved for reuse (application_qa) and the job released for the next
        # apply run: apply_status cleared, attempts reset.
        m = re.match(r"^/api/jobs/(\d+)/answers$", path)
        if m:
            import questions as _qmod
            job_id = int(m.group(1))
            data = self.read_json() or {}
            items = data.get("answers")
            if not isinstance(items, list) or not items:
                self.send_json({"error": "answers must be a non-empty list"}, 400)
                return
            conn = database.get_db()
            try:
                job = conn.execute("SELECT id FROM jobs WHERE id=? AND user_id=?", (job_id, user_id)).fetchone()
                if not job:
                    self.send_json({"error": "Not found"}, 404)
                    return
                now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                saved = 0
                for it in items[:40]:
                    if not isinstance(it, dict):
                        continue
                    label = str(it.get("label") or it.get("key") or "")[:500]
                    k = _qmod.key(it.get("key") or label)
                    ans = it.get("answer")
                    if not k or ans in (None, "", []):
                        continue
                    conn.execute(
                        "INSERT INTO application_qa (user_id, qkey, question, answer, updated_at) VALUES (?,?,?,?,?) "
                        "ON CONFLICT (user_id, qkey) DO UPDATE SET answer=excluded.answer, "
                        "question=excluded.question, updated_at=excluded.updated_at",
                        (user_id, k, label, json.dumps(ans) if isinstance(ans, list) else str(ans)[:4000], now))
                    saved += 1
                conn.execute("UPDATE jobs SET apply_status=NULL, apply_questions=NULL, apply_attempts=0, "
                             "apply_next_attempt_at=NULL, apply_error=NULL, apply_failure_type=NULL, "
                             "apply_failure_detail=NULL WHERE id=? AND user_id=? AND apply_status='needs_answers'",
                             (job_id, user_id))
                conn.commit()
            finally:
                conn.close()
            database.log_activity(user_id, "answers_saved", f"job {job_id}: {saved} answer(s) saved")
            self.send_json({"success": True, "saved": saved})
            return

        if path == "/api/jobs/apply-selected":
            user = self.require_auth()
            if not user:
                return
            if not _engine_on_for(user):
                self.send_json({"error": "Auto-apply isn't running for your account yet, so nothing "
                                         "would be sent.", "status": "engine_off"}, 409)
                return
            if not entitlements.can_auto_apply(user):
                self.send_json({"error": "Auto-apply is not part of your plan.",
                                "status": "not_entitled"}, 403)
                return
            try:
                raw = (self.read_json() or {}).get("ids") or []
                ids = list(dict.fromkeys(int(x) for x in raw))[:_SELECTED_MAX]
            except (TypeError, ValueError, AttributeError):
                self.send_json({"error": "ids must be job ids"}, 400)
                return
            conn = database.get_db()
            try:
                rows = conn.execute(
                    "SELECT id, apply_status FROM jobs WHERE user_id=? AND status='approved' AND id IN (%s)"
                    % ",".join("?" * len(ids)), (user["id"], *ids)).fetchall() if ids else []
            finally:
                conn.close()
            busy = ("applying", "awaiting_code", "confirmed", "submitted")
            ok = {r["id"] for r in rows if (r["apply_status"] or "") not in busy}
            picked = [i for i in ids if i in ok]          # the order the user sees
            if picked:
                _apply_selected_bg(user["id"], picked)
            self.send_json({"success": True, "started": len(picked), "skipped": len(ids) - len(picked),
                            "max": _SELECTED_MAX})
            return

        m = re.match(r"^/api/jobs/(\d+)/apply-now$", path)
        if m:
            job_id = int(m.group(1))
            conn   = database.get_db()
            try:
                job    = conn.execute(
                    "SELECT id, title, company, url, status, apply_status "
                    "FROM jobs WHERE id=? AND user_id=?", (job_id, user_id)
                ).fetchone()
                conn.close()
                if not job:
                    self.send_json({"error": "Not found"}, 404)
                    return
                if job["apply_status"] in ("applying", "awaiting_code"):
                    self.send_json({"error": "Already applying" if job["apply_status"] == "applying"
                                    else "This application is waiting for your security code"}, 409)
                    return
                # It said "Application started" with the engine off too, and
                # the background thread then returned without a word.
                if not _engine_on_for(user_id):
                    self.send_json({"error": "Auto-apply isn't running for your account yet, so "
                                             "nothing would be sent.", "status": "engine_off"}, 409)
                    return
                _trigger_apply_bg(user_id, job_id)
                self.send_json({"success": True, "message": "Application started"})
                return
            finally:
                conn.close()

        # ── Check if job is still open (calls Claude + fetches URL) ──────────
        m = re.match(r"^/api/jobs/(\d+)/check-status$", path)
        if m:
            user = self.require_auth()
            if not user:
                return
            job_id = int(m.group(1))
            conn = database.get_db()
            try:
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
                conn.close()

                # This endpoint had NO IMPLEMENTATION. It validated the job and the
                # URL and then fell out of the branch: no response, no return, and
                # the connection above left open. A caller got a bare 404 with an
                # empty body on a perfectly valid job - proven by driving it,
                # 2026-09-20. The legacy dashboard's "verify if still open" button
                # has therefore never worked.
                #
                # Built on check_url_alive rather than an AI call (which is what the
                # route comment promised): it already handles a 200-OK page that
                # says the posting is closed, and a parked domain, and it is the
                # same check the link sweeper uses - so one job re-checked by hand
                # and the same job swept overnight cannot disagree.
                today = datetime.now().strftime("%Y-%m-%d")
                try:
                    import apply_engine as _ae
                    alive = bool(_ae.check_url_alive(job["url"]))
                except Exception as _cse:
                    self.send_json({"error": "Could not reach the posting: %s" % str(_cse)[:160]}, 502)
                    return

                c2 = database.get_db()
                try:
                    if alive:
                        c2.execute(
                            "UPDATE jobs SET url_verified=1, url_check_date=?, "
                            "status_check='open', status_checked_date=? WHERE id=? AND user_id=?",
                            (today, today, job_id, user["id"]))
                    else:
                        # Flagged, NOT auto-rejected. A one-off manual check is the user
                        # asking a question, not asking for the job to be thrown away;
                        # the sweeper is the thing allowed to retire a dead link.
                        c2.execute(
                            "UPDATE jobs SET url_verified=0, url_check_date=?, "
                            "status_check='closed', status_checked_date=? WHERE id=? AND user_id=?",
                            (today, today, job_id, user["id"]))
                    c2.commit()
                    c2.close()
                    database.log_activity(
                        user["id"], "job_status_checked",
                        "%s still open: %s at %s" % ("Confirmed" if alive else "Could not confirm",
                                                     job["title"], job["company"]))
                    self.send_json({"success": True, "open": alive,
                                    "checked_date": today,
                                    "message": "Still open" if alive else
                                               "The posting did not respond, or reads as closed"})
                    return
                finally:
                    c2.close()
            finally:
                conn.close()

        # ── Cover Letter (admin only) ──
        m = re.match(r"^/api/jobs/(\d+)/tailored-cv$", path)
        if m:
            # Step 14: rewrite the CV for this job, then check it against the
            # original (tailor.verify). Metered like cover letters: checked
            # first, counted only after a success.
            job_id = int(m.group(1))
            _tdata = self.read_json() or {}
            _act = _tdata.get("action", "generate")
            if _act in ("save", "use"):
                # The user's review: edit the draft, and/or choose which CV
                # goes with this job's application (free - no model call).
                if not _tailored_cv_row(user_id, job_id):
                    self.send_json({"error": "Tailor the CV first"}, 404)
                    return
                c2 = database.get_db()
                try:
                    if _act == "save":
                        try:
                            _cv = _clean_cv(_tdata.get("cv"))
                        except ValueError as _ve:
                            self.send_json({"error": str(_ve)}, 400)
                            return
                        if not _cv["experience"]:
                            self.send_json({"error": "Keep at least one role"}, 400)
                            return
                        _old = _tailored_cv_row(user_id, job_id)
                        _content = {"cv": _cv, "removed": _old.get("removed", []), "flags": _old.get("flags", [])}
                        c2.execute("UPDATE tailored_cvs SET content=?, edited=1 WHERE user_id=? AND job_id=?",
                                   (json.dumps(_content), user_id, job_id))
                    else:
                        _use = _tdata.get("use")
                        if _use not in ("tailored", "original"):
                            self.send_json({"error": "use must be tailored or original"}, 400)
                            return
                        c2.execute("UPDATE tailored_cvs SET use_for_apply=? WHERE user_id=? AND job_id=?",
                                   (1 if _use == "tailored" else 0, user_id, job_id))
                    c2.commit()
                finally:
                    c2.close()
                self.send_json({"success": True, **_tailored_cv_row(user_id, job_id)})
                return
            conn = database.get_db()
            try:
                job = conn.execute("SELECT * FROM jobs WHERE id=? AND user_id=?", (job_id, user_id)).fetchone()
                if not job:
                    self.send_json({"error": "Not found"}, 404)
                    return
                _ok, _used, _cap = entitlements.allowed(conn, user, "tailored_cv")
            finally:
                conn.close()
            if not _ok:
                self.send_json(entitlements.upgrade_required(
                    "tailored_cv_limit", user, used=_used, limit=_cap,
                    message=("Tailored CVs aren't included in your plan." if not _cap else
                             "You've used all %d tailored CVs this month." % _cap)), 402)
                return
            import tailor as _tl
            cv_text, _name, _contact = _tailor_inputs(user_id, user)
            try:
                res = _tl.tailor(cv_text, dict(job), user_id=user_id, key=GEMINI_KEY)
            except Exception as gen_err:
                print(f"[tailor] user {user_id} job {job_id}: {gen_err}")
                self.send_json({"error": str(gen_err)[:300]}, 502)
                return
            # UTC, explicitly: the page shows it in the viewer's local time.
            from datetime import timezone as _tzu
            now = datetime.now(_tzu.utc).strftime("%Y-%m-%d %H:%M:%S")
            c2 = database.get_db()
            try:
                entitlements.try_consume(c2, user, "tailored_cv")
                # A new draft goes back to "original" until the user has reviewed it.
                c2.execute("INSERT INTO tailored_cvs (user_id, job_id, content, created_at, edited, use_for_apply) "
                           "VALUES (?,?,?,?,0,0) ON CONFLICT (user_id, job_id) DO UPDATE SET "
                           "content=excluded.content, created_at=excluded.created_at, edited=0, use_for_apply=0",
                           (user_id, job_id, json.dumps(res), now))
                c2.commit()
            finally:
                c2.close()
            self.send_json({"success": True, **res, "created_at": now, "edited": False, "use": "original"})
            return

        m = re.match(r"^/api/jobs/(\d+)/cover-letter$", path)
        if m:
            job_id = int(m.group(1))
            # Cover letters are a plan feature (approved matrix, 2026-11-05):
            # Free none, Premium 10 / month, Expert 30 / month, admin unmetered.
            # They used to be admin-only. Checked before generating, counted
            # after a success, so a failed generation costs the user nothing.
            data = self.read_json()
            conn = database.get_db()
            try:
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
                _ok, _used, _cap = entitlements.allowed(conn, user, "cover_letter")
                if not _ok:
                    conn.close()
                    self.send_json(entitlements.upgrade_required(
                        "cover_letter_limit", user, used=_used, limit=_cap,
                        message=("Cover letters are included from the Premium plan." if not _cap else
                                 "You've used all %d cover letters this month." % _cap)), 402)
                    return
                # Generate via Gemini Flash
                profile = conn.execute(
                    "SELECT * FROM user_profiles WHERE user_id=?", (user_id,)
                ).fetchone()
                conn.close()
                try:
                    from ai_analysis import write_cover_letter
                    # The full CV and the account name, not the 2-3 sentence
                    # profile summary it used to be written from (2026-11-08).
                    _cvt, _nm, _contact = _tailor_inputs(user_id, user)
                    _wl = write_cover_letter(dict(job), dict(profile) if profile else {},
                                             GEMINI_KEY, user_id=user_id, cv_text=_cvt, name=_nm)
                    letter, _notes = _wl["letter"], _wl["notes"]
                except Exception as gen_err:
                    self.send_json({"error": str(gen_err)}, 500)
                    return
                # Persist only on success
                c2 = database.get_db()
                try:
                    entitlements.try_consume(c2, user, "cover_letter")
                    c2.execute("UPDATE jobs SET cover_letter=? WHERE id=?", (letter, job_id))
                    c2.commit()
                    c2.close()
                    self.send_json({"success": True, "letter": letter, "notes": _notes})
                    return
                finally:
                    c2.close()
            finally:
                conn.close()

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
            try:
                if str(job_id).isdigit() and entitlements.is_locked(conn, user["id"], int(job_id)):
                    self.send_json(entitlements.upgrade_required(
                        "locked_match", user, message="Upgrade your plan to see this match."), 402)
                    return
                if str(job_id).isdigit() and entitlements.is_held(conn, user["id"], int(job_id)):
                    self.send_json({"error": "Job not found"}, 404)
                    return
                # WRITES `stage`, NOT `apply_status`. It wrote apply_status until
                # 2026-09-15, which was wrong in both directions at once:
                #   - the column the UI READS to highlight the selected stage is
                #     `stage` (app.py:4828-4831), and nothing ever wrote it, so a
                #     successful update lit up no button and the choice vanished on
                #     reload while the toast said "Stage updated";
                #   - `apply_status` records whether the application was actually
                #     submitted/confirmed/failed, and overwriting it DESTROYED that.
                #     Moving a confirmed application to "interviewing" erased the
                #     evidence it had ever been sent.
                # Proven by driving the route and reading the row back.
                conn.execute(
                    "UPDATE jobs SET stage=? WHERE id=? AND user_id=?",
                    (stage, job_id, user["id"])
                )
                conn.commit(); conn.close()
                database.log_activity(user["id"], "stage_update",
                    f"Stage updated to {stage} for job {job_id}")
                self.send_json({"ok": True}); return
            finally:
                conn.close()

        # ── Put bulk-marked "applied" jobs back in the review queue ───────────
        if path == "/api/jobs/restore-bulk-marked":
            # A one-time cleanup once marked every queued job as applied to empty
            # the queue, without applying to any of them. Those jobs were never
            # reviewed and never sent anywhere, so they are recoverable: this
            # puts them back where they should have stayed.
            #
            # Scoped tightly on purpose - applied_via='bulk' is written by
            # exactly two cleanup paths and by nothing else, so a real
            # application can never be caught by this. Jobs the user marked
            # applied by hand ('manual') and engine submissions ('engine') are
            # untouchable here whatever anyone passes in.
            conn = database.get_db()
            try:
                rows = conn.execute(
                    "SELECT id FROM jobs WHERE user_id=? AND status='applied' AND applied_via='bulk'",
                    (user_id,)
                ).fetchall()
                ids = [r["id"] for r in rows]
                for job_id in ids:
                    conn.execute(
                        "UPDATE jobs SET status='new', applied_via=NULL, apply_status=NULL, "
                        "apply_error=NULL, apply_confirmation=NULL, apply_failure_type=NULL, "
                        "apply_failure_detail=NULL, apply_attempts=0, apply_next_attempt_at=NULL, "
                        "applied_date=NULL, stage=NULL, notes='' WHERE id=? AND user_id=?",
                        (job_id, user_id)
                    )
                # The cleanup also left a rejected_patterns row for nothing; the jobs
                # were never passed on, so nothing to undo there. But a restored job
                # must not be filtered straight back out by its own company+title.
                conn.commit()
                conn.close()
                if ids:
                    database.log_activity(user_id, "restore_bulk_marked",
                        f"Put {len(ids)} bulk-marked job(s) back in the review queue")
                database.write_approved_jobs(BASE_DIR)
                self.send_json({"success": True, "restored": len(ids)})
                return
            finally:
                conn.close()

        # ── Bulk job actions ──────────────────────────────────────────────────────
        if path == "/api/jobs/bulk":
            data   = self.read_json()
            action = data.get("action", "")
            ids    = [int(i) for i in data.get("ids", []) if str(i).isdigit()]
            if not ids or action not in ("approve", "reject"):
                self.send_json({"error": "Invalid"}, 400)
                return
            conn       = database.get_db()
            try:
                new_status = "approved" if action == "approve" else "rejected"
                done       = 0
                for job_id in ids:
                    job = conn.execute(
                        "SELECT * FROM jobs WHERE id=? AND user_id=? AND status NOT IN ('locked','held')", (job_id, user_id)
                    ).fetchone()
                    if not job:
                        continue
                    # rejected_by: a bulk pass is still the USER'S decision, and
                    # without this every bulk-passed job reads as "origin unknown"
                    # in the analytics that separates his passes from the system's.
                    conn.execute(
                        "UPDATE jobs SET status=?, rejected_by=? WHERE id=?",
                        (new_status, "user" if action == "reject" else None, job_id))
                    if action == "reject":
                        conn.execute(
                            "INSERT INTO rejected_patterns (user_id,company,title,notes,location,created_date) VALUES (?,?,?,?,?,?)",
                            (user_id, job["company"], job["title"], "Bulk pass",
                             (job["location"] if "location" in job.keys() else None),
                             datetime.now().isoformat())
                        )
                    done += 1
                conn.commit()
                conn.close()
                label = "Approved" if action == "approve" else "Passed on"
                database.log_activity(user_id, f"bulk_{action}", f"{label} {done} job(s) at once")
                if action == "approve" and done:
                    _funnel(user_id, "first_approve")
                if done:
                    _track(user_id, "job_approved" if action == "approve" else "job_passed", {"count": done, "bulk": True})
                database.write_approved_jobs(BASE_DIR)
                self.send_json({"success": True, "updated": done})
                return
            finally:
                conn.close()

        # ── Admin: toggle user active state ───────────────────────────────────
        # ── Admin: set a user's plan, permanently or as a promotion ───────────
        # Body: {"plan": "free"|"premium"|"beta"|"expert", "days": int|null, "after": plan}.
        # days = a promotion that ends after that many days and falls back to
        # `after` (default free); null = permanent. Logged in plan_events with
        # the admin's id. Admin is a role, not a plan, so an admin's own row is
        # refused - it would change nothing they can do.
        m = re.match(r"^/api/admin/users/(\d+)/plan$", path)
        if m:
            if user.get("role") != "admin":
                self.send_json({"error": "Forbidden"}, 403)
                return
            data = self.read_json()
            target_id = int(m.group(1))
            plan = str(data.get("plan") or "").strip().lower()
            days = data.get("days")
            after = str(data.get("after") or "free").strip().lower()
            if plan not in entitlements.PLANS or after not in entitlements.PLANS:
                self.send_json({"error": "plan and after must be one of: %s" % ", ".join(entitlements.PLANS)}, 400)
                return
            if days is not None:
                try:
                    days = int(days)
                except (TypeError, ValueError):
                    days = -1
                if not 1 <= days <= 366:
                    self.send_json({"error": "days must be between 1 and 366, or null for a permanent plan"}, 400)
                    return
            conn = database.get_db()
            try:
                row = conn.execute("SELECT role FROM users WHERE id=?", (target_id,)).fetchone()
                if row is None:
                    self.send_json({"error": "No such user"}, 404)
                    return
                if (row[0] or "").lower() == "admin":
                    self.send_json({"error": "Admin is a role, not a plan - it has every feature already.",
                                    "code": "admin_has_no_plan"}, 400)
                    return
                out = entitlements.grant_plan(conn, target_id, plan, days=days, after=after,
                                              reason="admin", by_user_id=user["id"])
                out["locked"] = entitlements.locked_summary(conn, target_id)["count"]
            finally:
                conn.close()
            self.send_json({"success": True, **out})
            return

        m = re.match(r"^/api/admin/users/(\d+)/toggle$", path)
        if m:
            if user.get("role") != "admin":
                self.send_json({"error": "Forbidden"}, 403)
                return
            target_id = int(m.group(1))
            # Disabling YOURSELF is a one-click, unrecoverable lockout: the
            # session lookup requires is_active=1, so the very next request is a
            # 302 to /login and the undo answers 401. There is one admin
            # account, so the only way back was editing the database by hand.
            # Proven 2026-09-15 by driving the route.
            if target_id == user["id"]:
                self.send_json({
                    "error": "You cannot disable your own account - you would be locked out "
                             "with no way back in.",
                    "code": "cannot_disable_self",
                }, 400)
                return
            conn = database.get_db()
            try:
                # 404 rather than a cheerful success on an id that does not exist:
                # the old handler reported {"success": true} for any number at all.
                row = conn.execute("SELECT is_active FROM users WHERE id=?", (target_id,)).fetchone()
                if row is None:
                    conn.close()
                    self.send_json({"error": "No such user"}, 404)
                    return
                new_state = 0 if row["is_active"] else 1
                conn.execute("UPDATE users SET is_active=? WHERE id=?", (new_state, target_id))
                conn.commit()
                conn.close()
                database.log_activity(user["id"], "admin_user_toggle",
                    f"{'Enabled' if new_state else 'Disabled'} user {target_id}")
                # The new state, so the UI renders what IS rather than what it guessed.
                self.send_json({"success": True, "is_active": new_state})
                return
            finally:
                conn.close()

        # ── Run Search Now ────────────────────────────────────────────────────────
        if path == "/api/run-search":
            if not user:
                self.send_json({"error": "Unauthorized"}, 401)
                return
            if not GEMINI_KEY:
                self.send_json({"error": "No AI key configured. Set GEMINI_API_KEY in this service."}, 400)
                return
            uid = user["id"]
            # Each call spawns a minutes-long thread that spends Gemini quota,
            # and nothing capped how many. Gemini 429s have already degraded
            # scoring in production twice (STATUS, round 8/10). Keyed on the user
            # id, so it cannot be dodged by changing address.
            _swait = ratelimit.check_and_record("run_search", str(uid))
            if _swait:
                _mins = max(1, _swait // 60)
                self.send_json({"error": f"Search was run too many times recently. "
                                         f"Try again in about {_mins} minute(s)."}, 429)
                return
            # On-demand searches are a plan limit (Free 1 / day, Premium 3,
            # Expert 10). Checked first, counted only once a run is actually
            # queued - pressing again while one is in flight costs nothing.
            _sc = database.get_db()
            try:
                _ok, _used, _cap = entitlements.allowed(_sc, user, "on_demand_search")
            finally:
                _sc.close()
            if not _ok:
                self.send_json(entitlements.upgrade_required(
                    "on_demand_search_limit", user, used=_used, limit=_cap,
                    message="You've used today's %d on-demand search%s. Your scheduled search still runs."
                            % (_cap, "" if _cap == 1 else "es")), 402)
                return
            # A row, not a thread. Pressing the button twice while one is in
            # flight is now a no-op rather than a second Gemini-spending search.
            try:
                _run_id = jobqueue.enqueue(uid, "search")
            except jobqueue.DailyCapReached as _cap:
                # 429, not 403: this clears by itself at midnight UTC, and the
                # message says so rather than reading as a permanent refusal.
                self.send_json({"error": f"Daily search limit reached "
                                         f"({_cap.used} of {_cap.limit} today). "
                                         f"Resets at midnight UTC.",
                                "status": "daily_cap"}, 429)
                return
            if _run_id:
                _sc = database.get_db()
                try:
                    entitlements.try_consume(_sc, user, "on_demand_search")
                finally:
                    _sc.close()
            self.send_json({"status": "queued" if _run_id else "already_running",
                            "run_id": _run_id})
            return

        # ── Run Apply Now ─────────────────────────────────────────────────────────
        if path == "/api/run-apply":
            if not user:
                self.send_json({"error": "Unauthorized"}, 401)
                return
            # Same gate as the chokepoint in run_job_apply, repeated here for
            # ONE reason: to give the user an answer. Without it the enqueue
            # succeeds, the UI says "queued", and the worker quietly returns
            # not_entitled minutes later - a button that lies. The gate in
            # run_job_apply is still the one that protects the engine; this one
            # only exists so the screen can say why.
            if not entitlements.can_auto_apply(user):
                self.send_json({"error": "Applying to your whole queue at once is "
                                         "part of a paid plan.",
                                "status": "not_entitled",
                                "plan": entitlements.plan_of(user)}, 403)
                return
            # With the engine off a run ends at once as "apply_engine_disabled"
            # while the button said "Queued" - and each press still spent one
            # of the day's 20 runs (The owner, 2026-11-08). Say so instead.
            if not _engine_on_for(user):
                self.send_json({"error": "Auto-apply isn't running yet, so nothing would be sent. "
                                         "Apply from the job's page on the company site for now"
                                         + (", or use Supervised apply in Admin." if user.get("role") == "admin" else "."),
                                "status": "engine_off"}, 409)
                return
            # Queued, not spawned. This was the last raw thread left: the
            # scheduler's apply already went through the queue, so a manual
            # apply skipped the one-run-per-user rule AND the daily cap - the
            # exact hole the cap exists to close.
            try:
                _arun = jobqueue.enqueue(user["id"], "apply")
            except jobqueue.DailyCapReached as _cap:
                import datetime as _dtm
                _reset = israel_now((_dtm.datetime.now(_dtm.timezone.utc) + _dtm.timedelta(days=1)).replace(
                    hour=0, minute=0, second=0, microsecond=0))       # the cap's day is UTC; shown in Israel time
                self.send_json({"error": f"Daily apply limit reached "
                                         f"({_cap.used} of {_cap.limit} today). "
                                         f"Resets at {_reset.strftime('%H:%M')} Israel time.",
                                "status": "daily_cap"}, 429)
                return
            self.send_json({"started": bool(_arun),
                            "status": "queued" if _arun else "already_running",
                            "run_id": _arun})
            return

        # ── Admin job inject — session-authenticated, admin only ────────────────
        # ── Admin: clear applied jobs ─────────────────────────────────────────────
        if path == "/api/admin/clear-attempted":
            if not user or user.get("role") != "admin":
                self.send_json({"error": "Forbidden"}, 403)
                return
            payload = self.read_json()
            target_uid = payload.get("user_id") or user["id"]
            conn = database.get_db()
            try:
                cur = conn.execute(
                    "UPDATE jobs SET status='rejected', rejected_by='system', "
                    "notes=COALESCE(notes,'') || ' [admin: cleared attempted]' "
                    "WHERE user_id=? AND status='approved' AND COALESCE(apply_attempts,0) >= 1 "
                    "AND COALESCE(apply_status,'') IN ('manual_required','failed')",
                    (int(target_uid),)
                )
                cleared = cur.rowcount
                conn.commit(); conn.close()
                database.log_activity(user["id"], "admin_clear_attempted",
                                      f"Cleared {cleared} attempted job(s) from queue")
                print(f"[admin] clear-attempted: {cleared} rows")
                self.send_json({"cleared": cleared})
                return
            finally:
                conn.close()

        if path == "/api/admin/clear-applied":
            if not user or user.get("role") != "admin":
                self.send_json({"error": "Forbidden"}, 403)
                return
            payload   = self.read_json()
            target_uid = payload.get("user_id")   # None → all users
            conn = database.get_db()
            try:
                if target_uid:
                    cur = conn.execute(
                        "DELETE FROM jobs WHERE status='applied' AND user_id=?",
                        (int(target_uid),)
                    )
                else:
                    cur = conn.execute("DELETE FROM jobs WHERE status='applied'")
                deleted = cur.rowcount
                conn.commit()
                conn.close()
                database.log_activity(
                    user["id"], "admin_clear_applied",
                    f"Deleted {deleted} applied job(s)"
                    + (f" for user {target_uid}" if target_uid else " for all users")
                )
                print(f"[admin] clear-applied: {deleted} rows deleted"
                      + (f" (user {target_uid})" if target_uid else " (all users)"))
                self.send_json({"deleted": deleted})
                return
            finally:
                conn.close()

        # ── Admin: re-score existing 'new' jobs with current model ───────────────
        if path == "/api/admin/rescore":
            if not user or user.get("role") != "admin":
                self.send_json({"error": "Forbidden"}, 403)
                return
            payload = self.read_json()
            target_uid = payload.get("user_id", user["id"])
            conn = database.get_db()
            try:
                rows = conn.execute(
                    "SELECT id, title, company, location, url, description, full_description "
                    "FROM jobs WHERE user_id=? AND status='new'",
                    (int(target_uid),)
                ).fetchall()
                conn.close()
                if not rows:
                    self.send_json({"rescored": 0, "message": "No new jobs to rescore"})
                    return
                # Build jobs list for scoring
                jobs_to_rescore = [
                    {"job_title": r[1], "company": r[2], "location": r[3] or "",
                     "url": r[4] or "", "description": r[5] or "", "full_description": r[6] or "",
                     "source": "rescore", "_db_id": r[0]}
                    for r in rows
                ]
                # Call Gemini directly for rescoring
                import os as _os_rs, json as _js_rs, urllib.request as _ur_rs, base64 as _b64_rs
                GEMINI_KEY_RS = os.environ.get('GEMINI_API_KEY', '')
                # Was `database.get_db().execute(...)`: the connection was never
                # closed at all, on any path - on Postgres, one pool slot per call.
                _pc_admin = database.get_db()
                try:
                    profile_row = _pc_admin.execute(
                        "SELECT job_titles, keywords, locations, cv_summary, cv_path FROM user_profiles WHERE user_id=?",
                        (int(target_uid),)
                    ).fetchone()
                finally:
                    _pc_admin.close()
                titles_rs = (profile_row[0] or '').replace('\n', ', ') if profile_row else ''
                locs_rs = (profile_row[2] or 'Israel') if profile_row else 'Israel'
                cv_path_rs = (profile_row[4] or '') if profile_row else ''
                rescored = 0
                failed = 0
                batch_size = 20
                for i in range(0, len(jobs_to_rescore), batch_size):
                    batch = jobs_to_rescore[i:i+batch_size]
                    jobs_json_rs = _js_rs.dumps(
                        [{"job_title": j["job_title"], "company": j["company"],
                          "location": j["location"], "url": j["url"],
                          "description": j["description"],
                          "full_description": j["full_description"][:2000]} for j in batch],
                        ensure_ascii=False)
                    prompt_rs = (
                        "You are a precise job matching assistant. The candidate's full CV is attached as a PDF.\n"
                        f"Target roles: {titles_rs}\nTarget locations: {locs_rs}\n\n"
                        f"Jobs:\n{jobs_json_rs}\n\n"
                        "Score each job 0-100: Title match (0-30) + Seniority (0-20) + Location (0-20) + CV-Requirements match (0-30). "
                        "Exclude non-PM roles (engineering, sales, design, finance, HR). "
                        "Return JSON array: job_title, company, url, candidate_score (0-100), fit_reason (1 sentence). No markdown.\n"
                        + voice.FIT_REASON_RULE
                    )
                    try:
                        _parts_rs = []
                        if cv_path_rs and os.path.exists(cv_path_rs) and cv_path_rs.lower().endswith('.pdf'):
                            with open(cv_path_rs, 'rb') as _f:
                                _parts_rs.append({'inlineData': {'mimeType': 'application/pdf', 'data': _b64_rs.b64encode(_f.read()).decode()}})
                        _parts_rs.append({'text': prompt_rs})
                        _body_rs = _js_rs.dumps({'contents': [{'parts': _parts_rs}],
                                                 'generationConfig': {'temperature': 0.1, 'maxOutputTokens': 4096}}).encode()
                        # Attributed to the user whose jobs are rescored, not to the
                        # admin who pressed the button — the spend is theirs.
                        _data_rs = gemini.generate(_body_rs, purpose='resume_search',
                                                   user_id=int(target_uid), key=GEMINI_KEY_RS,
                                                   timeout=90)
                        _text_rs = _data_rs['candidates'][0]['content']['parts'][0]['text'].strip()
                        scored_rs = _json_array_from_text(_text_rs)   # same rfind trap as web search
                        if scored_rs:
                            url_to_score = {j.get('url', ''): j for j in scored_rs if isinstance(j, dict)}
                            conn2 = database.get_db()
                            try:
                                for orig in batch:
                                    scored_j = url_to_score.get(orig['url'])
                                    if scored_j and scored_j.get('candidate_score', 0) >= 30:
                                        conn2.execute(
                                            "UPDATE jobs SET candidate_score=?, match_score=?, why_relevant=? WHERE id=?",
                                            (scored_j['candidate_score'], scored_j['candidate_score'],
                                             voice.second_person(scored_j.get('fit_reason', '')), orig['_db_id'])
                                        )
                                        rescored += 1
                                    else:
                                        failed += 1
                                conn2.commit()
                                conn2.close()
                            finally:
                                conn2.close()
                    except Exception as _e_rs:
                        print(f"[rescore] batch error: {_e_rs}")
                        failed += len(batch)
                database.log_activity(user["id"], "admin_rescore",
                    f"Rescored {rescored} jobs, {failed} unchanged for user {target_uid}")
                print(f"[admin] rescore: {rescored} updated, {failed} unchanged (user {target_uid})")
                self.send_json({"rescored": rescored, "unchanged": failed, "total": len(jobs_to_rescore)})
                return
            finally:
                conn.close()

        if path == "/api/admin/inject-jobs":
            if not user or user.get("role") != "admin":
                self.send_json({"error": "Forbidden"}, 403)
                return
            payload = self.read_json()
            jobs = payload.get("jobs", [])
            conn = database.get_db()
            try:
                inserted = 0
                for j in jobs:
                    j = textclean.clean_job(j)
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
            finally:
                conn.close()

        # ── Sync endpoints — called by relay.py on Mac, no session needed ──────

        if path == "/api/sync/jobs":
            data = self.read_json()
            if not self._check_sync_key(data.get("api_key", "")):
                self.send_json({"error": "Unauthorized"}, 401)
                return
            jobs = data.get("jobs", [])
            conn = database.get_db()
            try:
                inserted = 0
                for j in jobs:
                    uid = j.get("user_id", 1)
                    j = textclean.clean_job(j)
                    try:
                        # The INSERT's own rowcount (0 when the row already
                        # existed). It used `SELECT changes()`, a SQLite-only
                        # function: on Postgres it raised every time, the
                        # handler swallowed it, and every sync reported 0
                        # inserted - so its notification never fired (Step 7).
                        _cur = conn.execute("""
                        INSERT OR IGNORE INTO jobs
                          (user_id,title,company,location,url,description,
                           why_relevant,company_info,source,found_date,status)
                        VALUES (?,?,?,?,?,?,?,?,?,?,'new')
                    """, (uid, j.get("title",""), j.get("company",""),
                              j.get("location","Tel Aviv"), j.get("url",""),
                              j.get("description",""), j.get("why_relevant",""),
                              j.get("company_info",""), j.get("source",""),
                              j.get("found_date", datetime.now().isoformat())))
                        if (_cur.rowcount or 0) > 0:
                            inserted += 1
                    except Exception as _sj_e:
                        print(f"[sync] WARNING job not inserted ({j.get('url','')[:80]}): {_sj_e}")
                conn.commit()
                conn.close()
                if inserted > 0:
                    for uid in set(j.get("user_id", 1) for j in jobs):
                        cnt = sum(1 for j in jobs if j.get("user_id", 1) == uid)
                        database.log_activity(uid, "jobs_searched", f"Relay synced {cnt} new job(s)")
                print(f"[sync] {inserted} new jobs ingested via API")
                self.send_json({"success": True, "inserted": inserted})
                return
            finally:
                conn.close()

        if path == "/api/sync/updates":
            data = self.read_json()
            if not self._check_sync_key(data.get("api_key", "")):
                self.send_json({"error": "Unauthorized"}, 401)
                return
            updates = data.get("updates", [])
            conn = database.get_db()
            try:
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
                    try:
                        uid_counts: dict = {}
                        for u in updates:
                            row = conn2.execute("SELECT user_id FROM jobs WHERE id=?", (u["id"],)).fetchone()
                            if row:
                                uid_counts[row["user_id"]] = uid_counts.get(row["user_id"], 0) + 1
                        conn2.close()
                        for uid, cnt in uid_counts.items():
                            database.log_activity(uid, "job_applied", f"Auto-applied to {cnt} job(s)")
                    finally:
                        conn2.close()
                print(f"[sync] {len(updates)} job statuses updated via API")
                self.send_json({"success": True, "updated": len(updates)})
                return
            finally:
                conn.close()

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

        # ── One-time queue cleanup (admin/maintenance) ─────────────────────────
        # Marks every job currently in the review queue as "applied (manual)" —
        # EXCEPT a keep-list — so jobs the user already handled outside the app
        # leave the queue and are remembered. Auth: SYNC_API_KEY. Defaults to a
        # DRY RUN (preview only); pass {"dry_run": false} to actually commit.
        if path == "/api/sync/queue-cleanup":
            data = self.read_json()
            if not self._check_sync_key(data.get("api_key", "")):
                self.send_json({"error": "Unauthorized"}, 401)
                return
            _uid = data.get("user_id")
            if not _uid:
                _c0 = database.get_db()
                try:
                    _arow = _c0.execute(
                        "SELECT id FROM users WHERE lower(email)=lower(?)", (ADMIN_EMAIL,)
                    ).fetchone()
                    _c0.close()
                    _uid = _arow["id"] if _arow else 1
                finally:
                    _c0.close()
            _uid = int(_uid)
            dry_run = data.get("dry_run", True)
            if isinstance(dry_run, str):
                dry_run = dry_run.strip().lower() not in ("0", "false", "no", "off")
            mark_status = data.get("mark_status", "applied")
            keepers = data.get("keep") or [
                {"company": "limy", "title": "senior product manager"},
                {"company": "riskified", "title": "product strategy director"},
            ]
            statuses = data.get("statuses") or ["new", "approved", "deferred"]
            import re as _re_qc
            def _qc_norm(s):
                return _re_qc.sub(r"[^a-z0-9 ]", " ", (s or "").lower()).strip()
            def _is_keeper(_co, _ti):
                _con, _tin = _qc_norm(_co), _qc_norm(_ti)
                for k in keepers:
                    kc, kt = _qc_norm(k.get("company", "")), _qc_norm(k.get("title", ""))
                    if kc and kc not in _con:
                        continue
                    if kt and not all(w in _tin for w in kt.split() if len(w) >= 3):
                        continue
                    return True
                return False
            conn = database.get_db()
            try:
                _ph = ",".join("?" for _ in statuses)
                rows = conn.execute(
                    "SELECT id, title, company, status FROM jobs "
                    "WHERE user_id=? AND status IN (" + _ph + ")",
                    (_uid, *statuses)
                ).fetchall()
                keep_list, clear_list = [], []
                for r in rows:
                    rec = {"id": r["id"], "title": r["title"],
                           "company": r["company"], "status": r["status"]}
                    (keep_list if _is_keeper(r["company"], r["title"]) else clear_list).append(rec)
                if dry_run:
                    conn.close()
                    self.send_json({
                        "dry_run": True, "user_id": _uid,
                        "would_keep": keep_list, "would_keep_count": len(keep_list),
                        "would_clear_count": len(clear_list), "would_clear": clear_list,
                    })
                    return
                today = datetime.now().strftime("%Y-%m-%d")
                cleared = 0
                for c in clear_list:
                    conn.execute(
                        "UPDATE jobs SET status=?, applied_date=?, notes=?, "
                        "apply_status='manual', applied_via='bulk' WHERE id=? AND user_id=?",
                        (mark_status, today, "Marked applied manually (bulk queue cleanup)",
                         c["id"], _uid)
                    )
                    cleared += 1
                conn.commit()
                conn.close()
                database.log_activity(
                    _uid, "job_applied",
                    f"Bulk cleanup: marked {cleared} queued job(s) applied (manual); kept {len(keep_list)}"
                )
                print(f"[cleanup] user {_uid}: kept {len(keep_list)}, marked {cleared} applied(manual)")
                self.send_json({"success": True, "kept": len(keep_list),
                                "cleared": cleared, "kept_jobs": keep_list})
                return
            finally:
                conn.close()

        self.send_response(404)
        self.end_headers()

# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"\n🎯  Job Hunter (Multi-User) starting…")
    # Before init_db(), deliberately: on 2026-09-07 init_db() is what built the
    # schema on a database nobody had chosen, which is what made the wrong
    # target look healthy. The guard has to run first or it guards nothing.
    database.preflight()
    crypto.warn_if_unconfigured()
    database.init_db()
    # Handlers are registered from here, not imported by the worker, so that
    # worker.py stays free of app.py - which is what makes moving it to its own
    # process later a CMD change rather than a refactor.
    worker.register("search", lambda user_id, _payload=None: run_job_search(user_id))
    worker.register("apply", lambda user_id, _payload=None: run_job_apply(user_id))
    worker.register("links", lambda user_id, _payload=None: run_link_resolve(user_id))
    worker.start_background()
    try:
        _exp = auth.cleanup_expired_sessions()
        if _exp:
            print(f"[startup] purged {_exp} expired session(s)")
    except Exception as _se:
        print(f"[startup] session cleanup error: {_se}")
    # Ensure the configured admin email always holds the 'admin' role.
    try:
        if ADMIN_EMAIL:
            _ac = database.get_db()
            try:
                _promoted = _ac.execute(
                    "UPDATE users SET role='admin' WHERE lower(email)=lower(?) AND COALESCE(role,'') != 'admin'",
                    (ADMIN_EMAIL,)
                ).rowcount
                _ac.commit(); _ac.close()
                if _promoted:
                    print(f"[startup] promoted {ADMIN_EMAIL} to admin role")
            finally:
                _ac.close()
    except Exception as _ae:
        print(f"[startup] admin-role ensure failed: {_ae}")

    # One-time cleanup: an 'applied' job that recorded an error never actually
    # succeeded -> move it back to the queue as a retryable failure. Idempotent.
    try:
        _cc = database.get_db()
        try:
            _moved = _cc.execute(
                "UPDATE jobs SET status='approved', apply_status='failed' "
                "WHERE status='applied' AND COALESCE(TRIM(apply_error), '') != '' "
                "AND COALESCE(apply_status,'') NOT IN ('manual_required','confirmed','submitted') "
                "AND COALESCE(apply_attempts,0) < 3"
            ).rowcount
            _cc.commit(); _cc.close()
            if _moved:
                print(f"[startup] moved {_moved} errored 'applied' job(s) back to the queue")
        finally:
            _cc.close()
    except Exception as _ce:
        print(f"[startup] applied-cleanup error: {_ce}")

    # Migrate expired jobs to rejected (expired tab removed)
    try:
        _mconn = database.get_db()
        try:
            # The expiry rule is gone (db.expire_old_jobs, 2026-09-15), so there is
            # nothing left to convert. Migration 11 restores the rows it made.
            _mconn.execute("UPDATE jobs SET status='approved' WHERE status='applied' AND apply_status='failed' AND COALESCE(apply_attempts,0) < 3")
            # Reset any jobs stuck in 'applying' from a crashed/restarted Playwright run
            _stuck = _mconn.execute(
                # 'awaiting_code' too: a restart loses the waiting run (in memory).
                "UPDATE jobs SET apply_status=NULL WHERE apply_status IN ('applying','awaiting_code')"
            ).rowcount
            if _stuck:
                print(f"[startup] Reset {_stuck} job(s) stuck in 'applying' state")
            _mconn.commit()
            _mconn.close()
        finally:
            _mconn.close()
    except Exception:
        pass

    # ── One-time prune: retire jobs already ATTEMPTED (manual_required/failed with
    #    >=1 attempt) out of the Approved queue. These were being resurfaced on every
    #    redeploy by the migrations above, so already-handled jobs kept reappearing.
    #    Runs exactly once (guarded by an app_flags token). Reversible: moved to
    #    'rejected' with a note, not deleted. Bump the token (v2) to re-run. ──
    try:
        _pc = database.get_db()
        try:
            _pc.execute("CREATE TABLE IF NOT EXISTS app_flags "
                        "(key TEXT PRIMARY KEY, value TEXT, set_date TEXT DEFAULT (datetime('now')))")
            _pdone = _pc.execute("SELECT value FROM app_flags WHERE key='prune_attempted_v1'").fetchone()
            if not _pdone:
                _pn = _pc.execute(
                    "UPDATE jobs SET status='rejected', rejected_by='system', "
                    "notes=COALESCE(notes,'') || ' [auto-removed: already attempted]' "
                    "WHERE status='approved' AND COALESCE(apply_attempts,0) >= 1 "
                    "AND COALESCE(apply_status,'') IN ('manual_required','failed')"
                ).rowcount
                _pc.execute("INSERT OR REPLACE INTO app_flags (key, value) VALUES ('prune_attempted_v1', ?)",
                            (str(_pn),))
                _pc.commit()
                print(f"[startup] one-time prune: retired {_pn} already-attempted job(s) from the queue")
            _pc.close()
        finally:
            _pc.close()
    except Exception as _pe:
        print(f"[startup] prune-attempted error: {_pe}")

    # ── One-time queue cleanup (set QUEUE_CLEANUP=1, or =force to re-run) ──
    # Runs LAST (after the migrations above) and clears apply_error on the jobs it
    # marks applied, so the "move errored applied back to queue" step can't bounce
    # them. Keeper match is an ORDERED substring so "Product Strategy Director" is
    # kept but "Director, Product Strategy" is not. Guarded by a DB marker:
    # QUEUE_CLEANUP=1 runs once; QUEUE_CLEANUP=force re-runs. Override the keep-list
    # with QUEUE_CLEANUP_KEEP="limy:senior product manager;riskified:product strategy director".
    try:
        _qc_flag = (os.environ.get("QUEUE_CLEANUP", "") or "").strip().lower()
        if _qc_flag in ("1", "true", "yes", "on", "force"):
            _qc = database.get_db()
            try:
                _qc.execute("CREATE TABLE IF NOT EXISTS app_flags "
                            "(key TEXT PRIMARY KEY, value TEXT, set_date TEXT DEFAULT (datetime('now')))")
                _tok_row = _qc.execute("SELECT value FROM app_flags WHERE key='queue_cleanup_token'").fetchone()
                _last_tok = _tok_row[0] if _tok_row else ""
                if _qc_flag == _last_tok:
                    _qc.close()
                    print(f"[cleanup] QUEUE_CLEANUP='{_qc_flag}' already ran for this value — skipping (it will NOT touch new search results). Change the value to re-run (e.g. =force2), or remove the var.")
                else:
                    _arow = _qc.execute("SELECT id FROM users WHERE lower(email)=lower(?)",
                                        (ADMIN_EMAIL,)).fetchone()
                    _cuid = _arow["id"] if _arow else 1
                    import re as _re_qc2
                    _keep_env = (os.environ.get("QUEUE_CLEANUP_KEEP", "") or "").strip()
                    if _keep_env:
                        _keepers = []
                        for _pair in _keep_env.split(";"):
                            if ":" in _pair:
                                _c, _t = _pair.split(":", 1)
                                _keepers.append((_c.strip().lower(), _t.strip().lower()))
                    else:
                        _keepers = [("limy", "senior product manager"),
                                    ("riskified", "product strategy director")]
                    def _qnorm(_s):
                        return _re_qc2.sub(r"\s+", " ",
                            _re_qc2.sub(r"[^a-z0-9 ]", " ", (_s or "").lower())).strip()
                    def _qkeep(_co, _ti):
                        _con, _tin = _qnorm(_co), _qnorm(_ti)
                        for _kc, _kt in _keepers:
                            if _kc and _kc not in _con:
                                continue
                            if _kt and _qnorm(_kt) not in _tin:   # ORDERED substring
                                continue
                            return True
                        return False
                    _rows = _qc.execute(
                        "SELECT id, title, company FROM jobs WHERE user_id=? "
                        "AND status IN ('new','approved','deferred')", (_cuid,)
                    ).fetchall()
                    _today = datetime.now().strftime("%Y-%m-%d")
                    _kept = 0
                    _cleared = 0
                    _kept_names = []
                    for _r in _rows:
                        if _qkeep(_r["company"], _r["title"]):
                            _kept += 1
                            _kept_names.append(f"{_r['title']} @ {_r['company']}")
                            continue
                        _qc.execute(
                            "UPDATE jobs SET status='applied', applied_date=?, notes=?, "
                            "apply_status='manual', applied_via='bulk', apply_error=NULL, apply_failure_type=NULL, "
                            "apply_failure_detail=NULL, apply_next_attempt_at=NULL "
                            "WHERE id=? AND user_id=?",
                            (_today, "Marked applied manually (one-time queue cleanup)",
                             _r["id"], _cuid)
                        )
                        _cleared += 1
                    _qc.execute("INSERT OR REPLACE INTO app_flags (key, value) VALUES "
                                "('queue_cleanup_done', ?)",
                                (f"kept={_kept} cleared={_cleared} {_today}",))
                    _qc.execute("INSERT OR REPLACE INTO app_flags (key, value) VALUES "
                                "('queue_cleanup_token', ?)", (_qc_flag,))
                    _qc.commit()
                    _qc.close()
                    try:
                        database.log_activity(_cuid, "job_applied",
                            f"One-time cleanup: marked {_cleared} queued job(s) applied (manual); kept {_kept}")
                    except Exception:
                        pass
                    print(f"[cleanup] ONE-TIME DONE: user {_cuid} kept {_kept} ({_kept_names}), "
                          f"marked {_cleared} applied(manual). >>> Remove the QUEUE_CLEANUP env var now. <<<")
            finally:
                _qc.close()
    except Exception as _qce:
        import traceback as _tb_qc
        print(f"[cleanup] one-time queue cleanup error: {_qce}")
        _tb_qc.print_exc()
        try:
            _qc.close()
        except Exception:
            pass

    # Process any waiting files
    database.import_pending_jobs(BASE_DIR)
    database.import_applied_updates(BASE_DIR)
    # Notifications delivered exclusively via relay.py → /api/sync/notify

    # Background watcher
    t = threading.Thread(target=file_watcher, daemon=True)
    t.start()

    ai_status = "✅ Configured" if GEMINI_KEY else "⚠️  Not set — add to config.json"

    gemini_status = "✅ Configured" if GEMINI_KEY else "⚠️  Not set — add GEMINI_API_KEY"
    print(f"\n📂  Folder:        {BASE_DIR}")
    print(f"🗄️   Database:      {DB_FILE}")
    print(f"📁  Uploads:       {UPLOADS_DIR}")
    print(f"🤖  Anthropic AI:  {ai_status}")
    print(f"🤖  Gemini AI:     {gemini_status}")
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
