"""analytics.py - product analytics through PostHog (EU), for the launch campaign.

Two halves:

- The browser half (landing page only) is `browser_snippet()`: posthog-js in
  cookieless "on_reject" mode. Nothing is captured and nothing is stored until
  the visitor answers the consent banner. Accept = normal analytics cookies;
  decline = counted with PostHog's cookieless hash, no cookie, no storage
  except the "declined" choice itself.
- The server half is `capture()`: the funnel events (signup, CV, first search,
  first approve = activation, daily app opens for retention) sent from the
  server, keyed by a pseudonymous id ("jh-user-<id>"), never a name or email.
  These are the numbers the campaign is judged on, so they must not depend on
  an ad blocker or a banner.

Off unless JH_POSTHOG_KEY is set (tests, staging, local). capture() never
raises and never blocks a request: events go to a background thread.

Attribution travels in the URL, not a cookie: the landing page appends the
visitor's utm_* / ref parameters and the Noa variant to its sign-up links,
/register carries them as hidden fields (and into Google sign-in's `state`),
and the signup event sets them as person properties, so every later event can
be broken down by channel.
"""
import json
import os
import queue
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

KEY = os.environ.get("JH_POSTHOG_KEY", "").strip()
HOST = (os.environ.get("JH_POSTHOG_HOST", "") or "https://eu.i.posthog.com").strip().rstrip("/")

ATTR_KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "ref", "noa")
_SAFE = re.compile(r"[^A-Za-z0-9_.\-]")

# Funnel step (app._funnel) -> PostHog event. "signup" is sent by the signup
# paths themselves, because they carry the attribution.
STEP_EVENTS = {
    "verified": "email_verified",
    "cv": "cv_uploaded",
    "first_search": "first_search_completed",
    "first_approve": "activated",
    "paywall": "paywall_hit",
}


def enabled() -> bool:
    return bool(KEY)


def distinct_id(user_id) -> str:
    return "jh-user-%s" % int(user_id)


def clean_attribution(source) -> dict:
    """utm_* / ref / noa from a query or form dict (values may be lists).
    Only short, plain values survive - nothing else is passed along."""
    out = {}
    for k in ATTR_KEYS:
        v = (source or {}).get(k)
        if isinstance(v, (list, tuple)):
            v = v[0] if v else ""
        v = _SAFE.sub("", str(v or ""))[:64]
        if v:
            out[k] = v
    return out


def encode_attribution(attr: dict) -> str:
    return urllib.parse.urlencode(clean_attribution(attr))


def decode_attribution(text: str) -> dict:
    return clean_attribution(urllib.parse.parse_qs(text or ""))


# ── server-side capture ─────────────────────────────────────────────────────
_q: "queue.Queue" = queue.Queue(maxsize=5000)
_worker = [None]
_sent = {"ok": 0, "failed": 0, "dropped": 0}


def _post(batch):
    body = json.dumps({"api_key": KEY, "batch": batch}).encode()
    req = urllib.request.Request(HOST + "/batch/", data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        r.read()


def _run():
    while True:
        batch = [_q.get()]
        try:
            while len(batch) < 50:
                batch.append(_q.get_nowait())
        except queue.Empty:
            pass
        for attempt in (1, 2):
            try:
                _post(batch)
                _sent["ok"] += len(batch)
                break
            except Exception as exc:
                if attempt == 2:
                    _sent["failed"] += len(batch)
                    print("[analytics] %d event(s) not sent: %s" % (len(batch), exc))
                else:
                    time.sleep(2)


def capture(user_id, event: str, props: dict | None = None, set_props: dict | None = None,
            set_once: dict | None = None) -> None:
    """Queue one server-side event for a user. No-op when analytics is off."""
    if not KEY or not user_id:
        return
    try:
        p = dict(props or {})
        p["$lib"] = "job-hunter-server"
        p["$process_person_profile"] = True
        if set_props:
            p["$set"] = set_props
        if set_once:
            p["$set_once"] = set_once
        item = {"event": event, "distinct_id": distinct_id(user_id), "properties": p,
                "timestamp": datetime.now(timezone.utc).isoformat()}
        if _worker[0] is None or not _worker[0].is_alive():
            _worker[0] = threading.Thread(target=_run, name="posthog", daemon=True)
            _worker[0].start()
        _q.put_nowait(item)
    except queue.Full:
        _sent["dropped"] += 1
    except Exception as exc:
        print("[analytics] %s for user %s not queued: %s" % (event, user_id, exc))


_BOT = re.compile(r"bot|crawl|spider|slurp|preview|facebookexternalhit|embedly|monitor|curl|python|headless", re.I)


def capture_anonymous(event: str, props: dict | None = None, user_agent: str = "") -> None:
    """An event with no person behind it (a landing-page view): a random id,
    no profile, no cookie. Bots and link previews are skipped."""
    if not KEY or _BOT.search(user_agent or ""):
        return
    try:
        import uuid
        p = dict(props or {})
        p["$lib"] = "job-hunter-server"
        p["$process_person_profile"] = False
        item = {"event": event, "distinct_id": "anon-" + uuid.uuid4().hex, "properties": p,
                "timestamp": datetime.now(timezone.utc).isoformat()}
        if _worker[0] is None or not _worker[0].is_alive():
            _worker[0] = threading.Thread(target=_run, name="posthog", daemon=True)
            _worker[0].start()
        _q.put_nowait(item)
    except queue.Full:
        _sent["dropped"] += 1
    except Exception as exc:
        print("[analytics] %s not queued: %s" % (event, exc))


def health() -> dict:
    return {"enabled": enabled(), "host": HOST if enabled() else "", **_sent, "queued": _q.qsize()}


_opened: dict = {}


def app_opened(user_id) -> None:
    """At most one `app_opened` per user per (Israel) day: the retention signal."""
    if not KEY or not user_id:
        return
    try:
        from zoneinfo import ZoneInfo
        day = datetime.now(ZoneInfo("Asia/Jerusalem")).strftime("%Y-%m-%d")
    except (ImportError, KeyError, ValueError):   # no tz database: UTC days are close enough
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if _opened.get(user_id) == day:
        return
    _opened[user_id] = day
    capture(user_id, "app_opened")


# ── browser snippet (landing page) ──────────────────────────────────────────
_BANNER_CSS = """
.cc{position:fixed;left:1rem;right:1rem;bottom:1rem;z-index:50;max-width:34rem;margin:0 auto;background:#12291f;
    border:1px solid #2c4a3c;border-radius:1rem;padding:1rem 1.1rem;color:#c4d4c6;font-size:.95rem;line-height:1.45;
    box-shadow:0 10px 30px rgba(0,0,0,.35);display:none}
.cc.on{display:block}
.cc p{margin:0 0 .8rem}.cc a{color:#ffd766}
.cc .row{display:flex;gap:.6rem;flex-wrap:wrap}
.cc button{font:inherit;font-weight:650;border-radius:.7rem;padding:.55rem 1rem;cursor:pointer;border:1.5px solid #2c4a3c;
    background:transparent;color:#fbf6e6}
.cc button.yes{background:#ffd766;border-color:#ffd766;color:#1f1a05}
"""


def browser_snippet(page: str, extra: dict | None = None) -> str:
    """posthog-js + consent banner + attribution on sign-up links, for a public
    page. Always includes the link-attribution part (it only rewrites URLs);
    the PostHog part only when analytics is on."""
    props = json.dumps(dict(page=page, **(extra or {})))
    link_js = """
(function(){
  var q=new URLSearchParams(location.search),keep=new URLSearchParams();
  ["utm_source","utm_medium","utm_campaign","utm_content","utm_term","ref"].forEach(function(k){var v=q.get(k);if(v)keep.set(k,v)});
  var noa=document.body.getAttribute("data-noa");if(noa)keep.set("noa",noa);
  var s=keep.toString();
  if(s){document.querySelectorAll('a[href="/register"]').forEach(function(a){a.setAttribute("href","/register?"+s)})}
})();"""
    if not KEY:
        return "<script>%s</script>" % link_js
    ph = """
!function(t,e){var o,n,p,r;e.__SV||(window.posthog=e,e._i=[],e.init=function(i,s,a){function g(t,e){var o=e.split(".");2==o.length&&(t=t[o[0]],e=o[1]),t[e]=function(){t.push([e].concat(Array.prototype.slice.call(arguments,0)))}}(p=t.createElement("script")).type="text/javascript",p.crossOrigin="anonymous",p.async=!0,p.src=s.api_host.replace(".i.posthog.com","-assets.i.posthog.com")+"/static/array.js",(r=t.getElementsByTagName("script")[0]).parentNode.insertBefore(p,r);var u=e;for(void 0!==a?u=e[a]=[]:a="posthog",u.people=u.people||[],u.toString=function(t){var e="posthog";return"posthog"!==a&&(e+="."+a),t||(e+=" (stub)"),e},u.people.toString=function(){return u.toString(1)+".people (stub)"},o="init capture register register_once unregister opt_out_capturing has_opted_out_capturing opt_in_capturing has_opted_in_capturing get_explicit_consent_status reset get_distinct_id".split(" "),n=0;n<o.length;n++)g(u,o[n]);e._i.push([i,s,a])},e.__SV=1)}(document,window.posthog||[]);
posthog.init(%(key)s,{api_host:%(host)s,cookieless_mode:"on_reject",person_profiles:"identified_only",
  disable_session_recording:true,disable_surveys:true,autocapture:false,capture_pageview:false,
  loaded:function(ph){ph.register(%(props)s);ph.capture("$pageview");
    if(ph.get_explicit_consent_status()==="pending"){document.getElementById("cc").classList.add("on")}}});
function jhConsent(yes){if(yes){posthog.opt_in_capturing()}else{posthog.opt_out_capturing()}document.getElementById("cc").classList.remove("on")}
document.addEventListener("click",function(e){var a=e.target.closest&&e.target.closest('a[href^="/register"]');
  if(a){posthog.capture("cta_clicked",{label:a.textContent.trim(),place:a.getAttribute("data-place")||""})}});
""" % {"key": json.dumps(KEY), "host": json.dumps(HOST), "props": props}
    banner = ('<div class="cc" id="cc" role="dialog" aria-label="Analytics cookies">'
              '<p>Can Noa use analytics cookies to learn how people use this page? Either way, nothing is sold or used '
              'for ads. <a href="/privacy">Privacy Policy</a></p><div class="row">'
              '<button class="yes" type="button" onclick="jhConsent(true)">Accept</button>'
              '<button type="button" onclick="jhConsent(false)">No thanks</button></div></div>')
    return "<style>%s</style>%s<script>%s%s</script>" % (_BANNER_CSS, banner, link_js, ph)
