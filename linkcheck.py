"""
linkcheck.py - is this job URL still a live posting for THIS job?

The old checks (app._link_status / apply_engine.check_url_alive) asked only
"did the server answer 200?". Live probes on 2026-10-12 of every job Eran had
flagged "Link is broken" or "Job no longer exists" showed that a dead posting
almost never answers 404. It answers 200 from somewhere else:

  * Comeet      - a closed /jobs/<co>/<id> redirects to https://www.comeet.com/
  * Greenhouse  - a closed /<board>/jobs/<id> redirects to the board index
                  (often with ?error=true), or to the company's careers site
  * Company ATS - a closed posting redirects to the careers home page
  * LinkedIn    - stays on the URL and renders a "closed-job" banner, in
                  HEBREW on il.linkedin.com ("כבר לא מקבלים בקשות"), so the
                  English closed-phrase list never matched; the banner also
                  sits ~31KB into a ~220KB page, near the old 40KB read cap
  * Gemini web search hands back company careers HOME pages (/careers) and
                  vertexaisearch grounding redirects that 404 within days

So a URL is judged on where it LANDS and what the page SAYS, not only on the
status code. Verdicts:

  alive    - lands on a posting-shaped page with no closed marker
  dead     - 404/410, dead DNS, parked domain, closed marker, or a posting
             URL that redirected to a generic careers/board/home page
  generic  - the URL itself is a careers/board/home page, not a posting
             (useless to the user: "sent me to the general website")
  unknown  - blocked/rate-limited/timeouts: cannot prove anything; keep

Pure stdlib; no app imports, so it is cheap to test and to call from scripts.
"""
from __future__ import annotations

import re
import socket
import urllib.error
import urllib.request
from urllib.parse import urlparse, parse_qs

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# Enough for LinkedIn's guest page (~220-260KB) with headroom.
READ_BYTES = 400_000

CLOSED_PHRASES = (
    "no longer accepting applications",
    "no longer accepting application",
    "this position has been filled",
    "this job has been filled",
    "position has been closed",
    "this posting is closed",
    "posting is no longer available",
    "job posting is no longer available",
    "this job is no longer available",
    "this position is no longer available",
    "the job you are looking for is no longer",
    "this role is no longer open",
    "we are no longer accepting applications",
    "applications are no longer being accepted",
    "job is not available anymore",
    "this opening has been filled",
    "position is no longer open",
    "this listing has expired",
    "job listing has expired",
    "the page you were looking for doesn't exist",
    "job not found",
    "position not found",
    "oops! that job",
    # Hebrew - il.linkedin.com and Israeli boards render these regardless of
    # Accept-Language.
    "כבר לא מקבלים בקשות",
    "המשרה אינה פעילה",
    "המשרה כבר לא זמינה",
    "המשרה אוישה",
)

# Language-independent markup markers of a closed posting.
CLOSED_MARKUP = (
    'class="closed-job',          # LinkedIn guest job page
    "closed-job__flavor",
)

# Path segments that make a URL a careers/board landing page when they are
# the WHOLE path (optionally under a locale or "company"/"about" prefix).
_GENERIC_SEGMENTS = {
    "careers", "career", "jobs", "job", "join", "join-us", "joinus", "work-with-us",
    "positions", "open-positions", "openings", "vacancies", "opportunities",
    "position", "job-details", "company", "about", "about-us", "en", "en-us",
    "il", "he", "corp", "us", "location", "locations", "tel-aviv", "israel",
    "teams", "team", "life", "culture", "hiring", "apply", "search",
}

# Hosts whose bare root (or a bare board) is never a posting.
_ATS_ROOTS = {
    "www.comeet.com", "comeet.com", "www.comeet.co",
    "boards.greenhouse.io", "job-boards.greenhouse.io", "job-boards.eu.greenhouse.io",
    "jobs.lever.co", "jobs.ashbyhq.com", "apply.workable.com",
    "jobs.smartrecruiters.com", "careers.smartrecruiters.com",
}

# Placeholder ids an LLM writes when it has no real one.
_FAKE_IDS = re.compile(r"(?:^|[^0-9])(?:12345\d*|67890|123456789|0000000|99999)(?:$|[^0-9])")


# Where a site sends a posting that does not exist while still answering 200.
# Observed 2026-10-25: wiz.io/careers/job/<made-up id>/... redirects to
# /careers/not-found ("We couldn't find the role you're looking for"), 200.
_NOT_FOUND_SEGMENTS = {"not-found", "notfound", "page-not-found", "job-not-found",
                       "position-not-found", "404"}

def _segments(path: str) -> list[str]:
    return [s for s in (path or "").lower().split("/") if s]


def is_generic_url(url: str) -> bool:
    """True when the URL is a careers/board/home page rather than one posting."""
    p = urlparse(url or "")
    host = (p.hostname or "").lower()
    segs = _segments(p.path)
    q = parse_qs(p.query or "")
    if "error" in q:                                  # greenhouse ?error=true
        return True
    if not segs:
        return True                                   # bare host
    if host in _ATS_ROOTS:
        # boards.greenhouse.io/<board>  |  jobs.lever.co/<co>  - a board, not a job
        if host.endswith("greenhouse.io"):
            return "jobs" not in segs and "gh_jid" not in q
        if host == "jobs.lever.co" or host == "jobs.ashbyhq.com":
            return len(segs) < 2
        if "comeet" in host:
            return not (len(segs) >= 3 and segs[0] == "jobs")
        if host == "apply.workable.com":
            return "j" not in segs
    if any(k in q for k in ("gh_jid", "jobid", "job_id", "id", "jid", "pid")):
        return False
    # every segment is a generic word -> landing page (/careers, /company/careers/,
    # /en/careers, /jobs/locations/tel-aviv ...)
    return all(s in _GENERIC_SEGMENTS for s in segs)


def _visible_text(body: str) -> str:
    stripped = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", body or "")
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", stripped)).strip().lower()


def closed_reason(body: str) -> str:
    if not body:
        return ""
    for m in CLOSED_MARKUP:
        if m in body:
            return "closed banner (%s)" % m.strip('"=')
    vis = _visible_text(body)
    for ph in CLOSED_PHRASES:
        if ph in vis:
            return "page says: %s" % ph
    return ""


def _emptied_listing(url: str, body: str) -> str:
    """Boards that keep a removed listing's URL alive but strip its content.

    jobs.secrettelaviv.com (WordPress): a live listing carries "Published:"
    and the apply form ("your first name"); a removed one still answers 200
    with the job title in <title> and nothing else. Verified 2026-10-12 on
    10 listings - 5 of each shape.
    """
    host = (urlparse(url).hostname or "").lower()
    if host.endswith("secrettelaviv.com") and "/job/" in url:
        low = (body or "").lower()
        if "your first name" not in low and "published:" not in low:
            return "listing emptied by the board (no description or apply form)"
    return ""


def classify(url: str, timeout: int = 12, fetch=None) -> tuple[str, str, str]:
    """Return (verdict, reason, final_url).

    `fetch` is injectable for tests: fetch(url, timeout) -> (status, final_url,
    body) or raises urllib errors exactly like urlopen.
    """
    url = (url or "").strip()
    if not url:
        return "dead", "no URL", ""
    if "vertexaisearch.cloud.google.com/grounding-api-redirect" in url:
        # A Gemini grounding redirect. Valid for a few days, then 404 forever.
        # Follow it; if it still resolves, judge the destination.
        pass
    elif is_generic_url(url):
        return "generic", "not a job posting (careers/board page)", url
    try:
        status, final, body = (fetch or _fetch)(url, timeout)
    except urllib.error.HTTPError as e:
        if e.code in (404, 410):
            return "dead", "HTTP %d" % e.code, url
        if e.code in (301, 302, 303, 307, 308):
            return "unknown", "redirect not followed (%d)" % e.code, url
        return "unknown", "HTTP %d (blocked/limited)" % e.code, url
    except urllib.error.URLError as e:
        if isinstance(getattr(e, "reason", None), socket.gaierror):
            return "dead", "hostname does not resolve", url
        return "unknown", "network: %s" % (getattr(e, "reason", e),), url
    except Exception as e:  # timeouts, TLS, resets
        return "unknown", "network: %s" % type(e).__name__, url

    if status in (404, 410):
        return "dead", "HTTP %d" % status, final
    if status >= 400:
        return "unknown", "HTTP %d (blocked/limited)" % status, final

    why = closed_reason(body)
    if why:
        return "dead", why, final
    why = _emptied_listing(final or url, body)
    if why:
        return "dead", why, final
    try:
        import apply_engine as _ae  # parked / for-sale domain detection
        if _ae._looks_parked(final, body):
            return "dead", "parked domain", final
    except Exception:
        pass
    if any(seg in _NOT_FOUND_SEGMENTS for seg in _segments(urlparse(final or url).path)):
        return "dead", "landed on a not-found page (%s)" % _short(final or url), final
    if final and final.rstrip("/") != url.rstrip("/") and is_generic_url(final):
        return "dead", "posting redirected to %s" % _short(final), final
    if is_generic_url(final or url):
        return "generic", "not a job posting (careers/board page)", final
    if _FAKE_IDS.search(urlparse(url).path + "?" + urlparse(url).query):
        return "dead", "placeholder id in URL (invented by the search model)", final
    return "alive", "ok", final


def _short(u: str) -> str:
    p = urlparse(u)
    return (p.netloc + p.path)[:80]


class _Redirects(urllib.request.HTTPRedirectHandler):
    """Python < 3.11 refuses to follow 308; Greenhouse and Next.js sites use it."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if code == 308:
            code = 307
        return super().redirect_request(req, fp, code, msg, headers, newurl)

    http_error_308 = urllib.request.HTTPRedirectHandler.http_error_302


_OPENER = urllib.request.build_opener(_Redirects)


def _fetch(url: str, timeout: int):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    with _OPENER.open(req, timeout=timeout) as r:
        return r.status, r.geturl(), r.read(READ_BYTES).decode("utf-8", "ignore")


def resolve_redirect(url: str, timeout: int = 10) -> str:
    """Follow a redirect-only URL (Gemini grounding links) to its destination.
    Returns the destination, or "" when it does not resolve."""
    try:
        status, final, _ = _fetch(url, timeout)
        return final if status < 400 else ""
    except Exception:
        return ""


def is_usable(url: str, timeout: int = 12) -> tuple[bool, str, str]:
    """Gate for jobs about to reach the user: (keep?, reason, final_url).
    'unknown' is kept - a bot-block must never hide a real posting."""
    verdict, reason, final = classify(url, timeout)
    return verdict in ("alive", "unknown"), "%s: %s" % (verdict, reason), final
