"""
Big-Tech career adapters — reverse-engineered internal JSON endpoints.

These hit the same backend JSON APIs the companies' own career sites call, so we
get structured data without parsing HTML. All are FREE tier (no API key), but
they're the most fragile sources (undocumented, rotate without notice, bot-
protected), so every one is wrapped to degrade gracefully and runs behind the
rotating residential proxy pool when configured (``requires_proxy = True``).

Endpoint notes (verify periodically — these are undocumented and DO drift).
Re-discovered live 2026-10-25, when three of them had been returning nothing:
  Microsoft  GET  apply.careers.microsoft.com/api/pcsx/search   (Eightfold; the old
                  gcsservices.careers.microsoft.com host now fails TLS - retired.
                  Rate-limits hard: 429 on several of a handful of calls)
  Amazon     GET  www.amazon.jobs/en/search.json               (unchanged, working)
  Google     GET  www.google.com/about/careers/applications/jobs/results  (HTML page;
                  results are embedded as AF_initDataCallback 'ds:1'. The v3 JSON
                  API answers 404)
  Apple      GET  jobs.apple.com/en-us/search?location=israel-ISR  (HTML page; results
                  in window.__staticRouterHydrationData. /api/role/search redirects to
                  /pagenotfound, and /api/v1/search ignores the location filter)
  Meta       POST www.metacareers.com/graphql   (doc_id rotates → META_GRAPHQL_DOC_ID)

A failed request now RAISES (status ERROR with the HTTP code) instead of
returning [] - an empty list reported as OK is how these three broke silently.

The mapping each adapter does (raw endpoint payload → unified NormalizedJob) is
the concrete example of "normalize a Big-Tech endpoint into the unified schema".
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from typing import Any

from .base import SourceAdapter
from ..models import JobSource, SourceTier, SourceCategory, ApplyType, _coerce_date
from ..config import config


def _q(query) -> str:
    """Build a single search phrase from the query's titles/keywords."""
    parts = (query.titles or []) + (query.keywords or [])
    return (parts[0] if parts else "product manager")


def _loc(query) -> str:
    """First GEOGRAPHIC location (skip work-mode words like 'hybrid'); default Israel."""
    _workmode = {"hybrid", "remote", "onsite", "on-site", "office", "flexible", "anywhere"}
    for l in (query.locations or []):
        if l and l.strip().lower() not in _workmode:
            return l
    return "Israel"


class _BigTechBase(SourceAdapter):
    tier = SourceTier.FREE
    category = SourceCategory.BIG_TECH
    requires_proxy = True               # use residential proxy if one is configured

    def _fetch(self, query):
        if not config.BIGTECH_ENABLED:
            return []
        return self._fetch_bigtech(query)

    def _fetch_bigtech(self, query) -> list:
        raise NotImplementedError

    def _get_ok(self, url, params=None, headers=None):
        """GET that raises on a non-2xx answer, so a moved endpoint shows up as
        status ERROR "HTTP 404 from ..." rather than as an empty OK."""
        r = self.http.get(url, params=params, headers=headers)
        if not r.ok:
            raise RuntimeError("HTTP %s from %s" % (r.status_code, url.split("/")[2]))
        return r


def _israel(query) -> bool:
    """These sources are only queried for Israel - the app's relevance gate
    keeps Israel roles anyway, and Apple's location filter takes a fixed id."""
    text = " ".join(query.locations or []).lower()
    return (not text.strip()) or any(w in text for w in (
        "israel", "tel aviv", "tel-aviv", "herzliya", "haifa", "jerusalem", "ramat gan",
        "petah tikva", "raanana", "netanya", "beer sheva", "yokneam", "rehovot", "hybrid", "remote"))


# ─────────────────────────── Microsoft ─────────────────────────────────────

class MicrosoftAdapter(_BigTechBase):
    name = JobSource.MICROSOFT
    BASE = "https://apply.careers.microsoft.com/api/pcsx/search"
    SITE = "https://apply.careers.microsoft.com"

    def _fetch_bigtech(self, query) -> list:
        out, seen, start = [], set(), 0
        while len(out) < query.limit_per_source and start < 100:
            r = self._get_ok(self.BASE, params={
                "domain": "microsoft.com", "query": _q(query), "location": _loc(query),
                "start": start, "sort_by": "relevance",
            }, headers={"Accept": "application/json",
                        "Referer": self.SITE + "/careers?domain=microsoft.com"})
            data = (r.json() or {}).get("data") or {}
            page = data.get("positions") or []
            for j in page:
                pid = str(j.get("id") or "")
                if not pid or pid in seen:
                    continue
                seen.add(pid)
                locs = j.get("standardizedLocations") or j.get("locations") or []
                ts = j.get("postedTs") or j.get("creationTs")
                out.append(self._mk(
                    title=j.get("name", ""),
                    company="Microsoft",
                    location=(locs[0] if locs else ""),
                    url=self.SITE + (j.get("positionUrl") or "/careers/job/%s" % pid),
                    description=j.get("department") or "",
                    full_description="",
                    posted_at=(datetime.fromtimestamp(int(ts), timezone.utc).date() if ts else None),
                    apply_type=ApplyType.DIRECT_ATS,
                    source_job_id=str(j.get("displayJobId") or pid),
                ))
            total = int(data.get("count") or 0)
            start += len(page)
            if not page or start >= total:
                break
            time.sleep(1.0)       # Eightfold answers 429 to a quick second call
        return out[: query.limit_per_source]


# ─────────────────────────── Amazon ────────────────────────────────────────

class AmazonAdapter(_BigTechBase):
    name = JobSource.AMAZON
    BASE = "https://www.amazon.jobs/en/search.json"

    def _fetch_bigtech(self, query) -> list:
        out = []
        data = self._get_ok(self.BASE, params={
            "base_query": _q(query), "loc_query": _loc(query),
            "result_limit": query.limit_per_source, "offset": 0,
            "sort": "recent",
        }).json() or {}
        for j in (data.get("jobs") or [])[: query.limit_per_source]:
            path = j.get("job_path") or ""
            url = f"https://www.amazon.jobs{path}" if path else (j.get("url") or "")
            out.append(self._mk(
                title=j.get("title", ""),
                company="Amazon",
                location=j.get("location") or j.get("normalized_location") or "",
                url=url,
                description=(j.get("description_short") or "")[:600],
                full_description=j.get("description") or j.get("description_short") or "",
                posted_at=_coerce_date(j.get("posted_date")),
                apply_type=ApplyType.DIRECT_ATS,
                source_job_id=str(j.get("id_icims") or j.get("id") or ""),
            ))
        return out


# ─────────────────────────── Google ────────────────────────────────────────

def _google_results(html: str) -> list:
    """The jobs array Google embeds in the results page (AF_initDataCallback
    key 'ds:1'): each row is [id, title, apply_url, ..., company(7), locale(8),
    locations(9) = [[display, ...], ...], about(10) = [None, html], ...]."""
    i = html.find("AF_initDataCallback({key: 'ds:1'")
    if i < 0:
        return []
    j = html.index("data:", i) + len("data:")
    data, _ = json.JSONDecoder().raw_decode(html[j:])
    return (data[0] if data and isinstance(data[0], list) else []) or []


class GoogleAdapter(_BigTechBase):
    name = JobSource.GOOGLE
    BASE = "https://www.google.com/about/careers/applications/jobs/results"

    def _fetch_bigtech(self, query) -> list:
        if not _israel(query):
            return []
        out, seen = [], set()
        for page in (1, 2, 3):
            r = self._get_ok(self.BASE, params={"q": _q(query), "location": "Israel", "page": page},
                             headers={"Accept": "text/html"})
            html = r.text or ""
            rows = _google_results(html)
            if not rows:
                if page == 1 and "AF_initDataCallback" not in html:
                    raise RuntimeError("Google results page carried no ds:1 data - layout changed")
                break
            slugs = dict(re.findall(r"jobs/results/(\d{10,})-([a-z0-9-]+)", html))
            for j in rows:
                jid = str(j[0] if j else "")
                if not jid or jid in seen:
                    continue
                seen.add(jid)
                title = j[1] if len(j) > 1 else ""
                slug = slugs.get(jid) or re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")
                locs = j[9] if len(j) > 9 and isinstance(j[9], list) else []
                loc = locs[0][0] if locs and isinstance(locs[0], list) and locs[0] else ""
                about = j[10][1] if len(j) > 10 and isinstance(j[10], list) and len(j[10]) > 1 else ""
                about = re.sub(r"<[^>]+>", " ", about or "").strip()
                out.append(self._mk(
                    title=title,
                    company=(j[7] if len(j) > 7 and j[7] else "Google"),
                    location=loc,
                    url="%s/%s-%s" % (self.BASE, jid, slug),
                    description=about[:600],
                    full_description=about,
                    apply_type=ApplyType.DIRECT_ATS,
                    source_job_id=jid,
                ))
            if len(out) >= query.limit_per_source:
                break
        return out[: query.limit_per_source]


# ─────────────────────────── Apple ─────────────────────────────────────────

def _apple_results(html: str) -> list:
    """searchResults from the page's window.__staticRouterHydrationData, which
    is a JSON.parse("...") of a JSON string - so two decodes."""
    marker = "window.__staticRouterHydrationData = JSON.parse("
    i = html.find(marker)
    if i < 0:
        return []
    s, _ = json.JSONDecoder().raw_decode(html[i + len(marker):])
    data = json.loads(s)
    return (((data.get("loaderData") or {}).get("search") or {}).get("searchResults") or [])


class AppleAdapter(_BigTechBase):
    name = JobSource.APPLE
    BASE = "https://jobs.apple.com/en-us/search"

    def _fetch_bigtech(self, query) -> list:
        # Apple's search mostly ignores the text and returns every Israel role,
        # newest first; the app's relevance gate keeps the matching titles.
        if not _israel(query):
            return []
        out, seen = [], set()
        for page in (1, 2, 3):
            r = self._get_ok(self.BASE, params={"search": _q(query), "location": "israel-ISR", "page": page},
                             headers={"Accept": "text/html"})
            html = r.text or ""
            rows = _apple_results(html)
            if not rows:
                if page == 1 and "__staticRouterHydrationData" not in html:
                    raise RuntimeError("Apple search page carried no hydration data - layout changed")
                break
            for j in rows:
                pid = str(j.get("positionId") or "")
                if not pid or pid in seen:   # one role is listed once per location
                    continue
                seen.add(pid)
                slug = j.get("transformedPostingTitle") or re.sub(
                    r"[^a-z0-9]+", "-", (j.get("postingTitle") or "").lower()).strip("-")
                locs = j.get("locations") or []
                out.append(self._mk(
                    title=j.get("postingTitle") or "",
                    company="Apple",
                    location=(locs[0].get("name", "") if locs and isinstance(locs[0], dict) else ""),
                    url="https://jobs.apple.com/en-us/details/%s/%s" % (pid, slug),
                    description=(j.get("jobSummary") or "")[:600],
                    full_description=j.get("jobSummary") or "",
                    posted_at=_coerce_date(j.get("postDateInGMT")),
                    apply_type=ApplyType.DIRECT_ATS,
                    source_job_id=pid,
                ))
            if len(out) >= query.limit_per_source:
                break
        return out[: query.limit_per_source]


# ─────────────────────────── Meta ──────────────────────────────────────────

class MetaAdapter(_BigTechBase):
    name = JobSource.META
    BASE = "https://www.metacareers.com/graphql"

    def _fetch_bigtech(self, query) -> list:
        # Meta's careers search is a GraphQL POST whose persisted-query doc_id
        # rotates. We can only run it when META_GRAPHQL_DOC_ID is supplied;
        # otherwise we skip cleanly (still FREE tier, just config-gated).
        doc_id = config.META_GRAPHQL_DOC_ID
        if not doc_id:
            return []
        out = []
        variables = {
            "search_input": {
                "q": _q(query),
                "divisions": [], "offices": [], "roles": [], "teams": [],
                "page": 1, "results_per_page": query.limit_per_source,
            }
        }
        import json as _json
        data = self.http.post_json(self.BASE, data={
            "doc_id": doc_id,
            "variables": _json.dumps(variables),
        }, headers={"Origin": "https://www.metacareers.com"})
        if not data:
            return out
        results = (((data or {}).get("data") or {}).get("job_search") or [])
        for j in results[: query.limit_per_source]:
            pid = j.get("id") or ""
            url = f"https://www.metacareers.com/jobs/{pid}/" if pid else ""
            locs = j.get("locations") or []
            loc = ", ".join(locs) if isinstance(locs, list) else str(locs or "")
            out.append(self._mk(
                title=j.get("title", ""),
                company="Meta",
                location=loc,
                url=url,
                description="",
                full_description="",
                apply_type=ApplyType.DIRECT_ATS,
                source_job_id=str(pid),
            ))
        return out
