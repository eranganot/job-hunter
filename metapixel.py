"""
metapixel.py - measuring our own Meta (Facebook / Instagram) ads (GTM row 16).

Two halves, both off unless configured, both only for a visitor who clicked
Accept on the landing page's cookie banner:

- The browser half is `consent_js()`: Meta's pixel script, loaded on the
  landing page ONLY after Accept on a banner that names Meta. Nothing of
  Meta's is requested before that. It sends a PageView and sets Meta's own
  first-party cookies (_fbp, and _fbc when the visit came from an ad click).
  "No thanks" removes those cookies again.
- The server half is `send()`: the two events the campaign is judged on,
  sent through the Conversions API - CompleteRegistration at sign-up and
  FirstApprove at the first approved job (activation). Sent only when the
  request carries the _fbp cookie (so: the pixel ran, so: consent was given)
  or the account kept one from sign-up (`users.meta_ids`).

What Meta receives with a server event: the event name and time, the page
address, Meta's own cookie values, the browser's user agent and IP address,
and the account's email as a SHA-256 hash (owner's decision 2026-11-16: it lets
Meta match the event to the person who saw the ad, across devices; the Privacy
Policy says so). Never a name, the email itself, a CV, or anything about jobs.

META_PIXEL_ID alone turns on the browser half; the server half also needs
META_CAPI_TOKEN (Railway env, never in the repo). META_TEST_EVENT_CODE sends
server events to Events Manager's "Test events" tab instead of live data.
"""
import hashlib
import json
import os
import queue
import re
import threading
import time
import urllib.parse
import urllib.request
import uuid

PIXEL_ID = re.sub(r"\D", "", os.environ.get("META_PIXEL_ID", ""))
TOKEN = os.environ.get("META_CAPI_TOKEN", "").strip()
TEST_CODE = os.environ.get("META_TEST_EVENT_CODE", "").strip()
API_VERSION = os.environ.get("META_API_VERSION", "").strip() or "v25.0"

EVENTS = ("CompleteRegistration", "FirstApprove")
CONSENT_KEY = "jh_ads_ok"                       # localStorage, landing page; listed in the Privacy Policy
_FB = re.compile(r"^fb\.\d\.\d{6,}\.[A-Za-z0-9_\-.]{1,400}$")


def browser_enabled() -> bool:
    return bool(PIXEL_ID)


def server_enabled() -> bool:
    return bool(PIXEL_ID and TOKEN)


def ids_from_cookies(cookie_header: str) -> dict:
    """{'fbp':..., 'fbc':...} from a request's Cookie header; only well-formed
    values, and nothing at all without _fbp (no pixel ran = no consent)."""
    found = {}
    for part in (cookie_header or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k in ("_fbp", "_fbc") and _FB.match(v.strip()):
            found[k[1:]] = v.strip()
    return found if found.get("fbp") else {}


def clean_ids(raw) -> dict:
    """The same check for ids read back from users.meta_ids."""
    try:
        d = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
    except (ValueError, TypeError):
        return {}
    out = {k: d[k] for k in ("fbp", "fbc") if isinstance(d.get(k), str) and _FB.match(d[k])}
    return out if out.get("fbp") else {}


def hashed_email(email: str) -> str:
    """Meta's normalisation: trimmed, lower-cased, SHA-256 hex. '' for no email."""
    e = (email or "").strip().lower()
    return hashlib.sha256(e.encode("utf-8")).hexdigest() if "@" in e else ""


def payload(event: str, ids: dict, user_agent: str, ip: str, url: str, now: float | None = None,
            email: str = "") -> dict:
    user = {"fbp": ids["fbp"], "client_user_agent": (user_agent or "")[:512]}
    if hashed_email(email):
        user["em"] = [hashed_email(email)]
    if ids.get("fbc"):
        user["fbc"] = ids["fbc"]
    if ip and ip != "?":
        user["client_ip_address"] = ip
    body = {"data": [{"event_name": event, "event_time": int(now or time.time()),
                      "event_id": uuid.uuid4().hex, "action_source": "website",
                      "event_source_url": url, "user_data": user}]}
    if TEST_CODE:
        body["test_event_code"] = TEST_CODE
    return body


# ── sending: a background thread, like analytics.capture ────────────────────
_q: "queue.Queue" = queue.Queue(maxsize=1000)
_worker = [None]
_sent = {"ok": 0, "failed": 0, "dropped": 0, "last_error": "", "last": None}


def _post(body: dict) -> None:
    url = "https://graph.facebook.com/%s/%s/events?%s" % (
        API_VERSION, PIXEL_ID, urllib.parse.urlencode({"access_token": TOKEN}))
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        raw = r.read()
    # What Meta said, for /api/health (owner's test 2026-11-17: an accepted event did
    # not show in Test events, and a bare 200 cannot say why). No token in here.
    try:
        ans = json.loads(raw or b"{}")
    except ValueError:
        ans = {}
    _sent["last"] = {"event": body["data"][0]["event_name"], "test": bool(body.get("test_event_code")),
                     "test_code_tail": str(body.get("test_event_code", ""))[-3:],
                     "events_received": ans.get("events_received"), "messages": ans.get("messages"),
                     "fbtrace_id": ans.get("fbtrace_id"), "at": int(time.time())}


def _run():
    while True:
        body = _q.get()
        for attempt in (1, 2):
            try:
                _post(body)
                _sent["ok"] += 1
                break
            except Exception as exc:
                if attempt == 2:
                    _sent["failed"] += 1
                    # The token rides in the URL: never print the exception's URL.
                    _sent["last_error"] = "%s %s" % (type(exc).__name__, getattr(exc, "code", ""))
                    print("[meta] %s not sent: %s" % (body["data"][0]["event_name"], _sent["last_error"]))
                else:
                    time.sleep(2)


def send(event: str, ids: dict, user_agent: str, ip: str, url: str, email: str = "") -> bool:
    """Queue one server event. False (and nothing sent) when the server half is
    off, the event is not one of ours, or there is no consent-bearing id."""
    if not server_enabled() or event not in EVENTS or not (ids or {}).get("fbp"):
        return False
    try:
        if _worker[0] is None or not _worker[0].is_alive():
            _worker[0] = threading.Thread(target=_run, name="meta-capi", daemon=True)
            _worker[0].start()
        _q.put_nowait(payload(event, ids, user_agent, ip, url, email=email))
        return True
    except queue.Full:
        _sent["dropped"] += 1
    except Exception as exc:
        print("[meta] %s not queued: %s" % (event, type(exc).__name__))
    return False


def health() -> dict:
    return {"browser": browser_enabled(), "server": server_enabled(), "test_mode": bool(TEST_CODE),
            **_sent, "queued": _q.qsize()}


# ── the browser half ────────────────────────────────────────────────────────
def consent_js() -> str:
    """JS for the landing page: `jhMeta(true)` after Accept loads the pixel and
    remembers the choice ("1"); `jhMeta(false)` remembers the No ("0") and removes
    Meta's cookies. `jhMetaAsk()` is true while there is no answer about Meta yet,
    so the banner is shown again to someone who answered the older analytics-only
    banner (PostHog consent stored, nothing about Meta) - found 2026-11-17.
    On a later visit the pixel loads only if that choice is still stored.
    autoConfig is off: Meta's automatic events (it reported the Accept click as
    "SubscribedButtonClick" with the page's button texts, seen in the owner's
    test 2026-11-17) are not among the events the Privacy Policy names.
    Empty when no pixel is configured."""
    if not PIXEL_ID:
        return ""
    return """
function jhMetaLoad(){if(window.fbq)return;
!function(f,b,e,v,n,t,s){if(f.fbq)return;n=f.fbq=function(){n.callMethod?n.callMethod.apply(n,arguments):n.queue.push(arguments)};
if(!f._fbq)f._fbq=n;n.push=n;n.loaded=!0;n.version='2.0';n.queue=[];t=b.createElement(e);t.async=!0;t.src=v;
s=b.getElementsByTagName(e)[0];s.parentNode.insertBefore(t,s)}(window,document,'script','https://connect.facebook.net/en_US/fbevents.js');
fbq('set','autoConfig',false,%(id)s);fbq('init',%(id)s);fbq('track','PageView')}
function jhMetaAsk(){try{return localStorage.getItem(%(key)s)===null}catch(e){return false}}
function jhMeta(yes){try{if(yes){localStorage.setItem(%(key)s,"1");jhMetaLoad()}else{localStorage.setItem(%(key)s,"0");
["_fbp","_fbc"].forEach(function(c){document.cookie=c+"=; Max-Age=0; path=/";document.cookie=c+"=; Max-Age=0; path=/; domain=."+location.hostname.replace(/^www\\./,"")})}}catch(e){}}
try{if(localStorage.getItem(%(key)s)==="1")jhMetaLoad()}catch(e){}
""" % {"id": json.dumps(PIXEL_ID), "key": json.dumps(CONSENT_KEY)}
