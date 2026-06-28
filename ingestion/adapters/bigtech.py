"""
Big-Tech career adapters — reverse-engineered internal JSON endpoints.

These hit the same backend JSON APIs the companies' own career sites call, so we
get structured data without parsing HTML. All are FREE tier (no API key), but
they're the most fragile sources (undocumented, rotate without notice, bot-
protected), so every one is wrapped to degrade gracefully and runs behind the
rotating residential proxy pool when configured (``requires_proxy = True``).

Endpoint notes (verify periodically — these are undocumented and DO drift):
  Microsoft  GET  gcsservices.careers.microsoft.com/search/api/v1/search
  Amazon     GET  www.amazon.jobs/en/search.json
  Google     GET  careers.google.com/api/v3/search/
  Apple      POST jobs.apple.com/api/role/search
  Meta       POST www.metacareers.com/graphql   (doc_id rotates → META_GRAPHQL_DOC_ID)

The mapping each adapter does (raw endpoint payload → unified NormalizedJob) is
the concrete example of "normalize a Big-Tech endpoint into the unified schema".
"""
from __future__ import annotations

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


# ─────────────────────────── Microsoft ─────────────────────────────────────

class MicrosoftAdapter(_BigTechBase):
    name = JobSource.MICROSOFT
    BASE = "https://gcsservices.careers.microsoft.com/search/api/v1/search"

    def _fetch_bigtech(self, query) -> list:
        out = []
        data = self.http.get_json(self.BASE, params={
            "q": _q(query), "lc": _loc(query), "l": "en_us", "pg": 1,
            "pgSz": query.limit_per_source, "o": "Relevance", "flt": "true",
        })
        if not data:
            return out
        # payload: { operationResult: { result: { jobs: [...] } } }
        result = (((data or {}).get("operationResult") or {}).get("result") or {})
        for j in (result.get("jobs") or [])[: query.limit_per_source]:
            jid = str(j.get("jobId") or j.get("id") or "")
            url = f"https://jobs.careers.microsoft.com/global/en/job/{jid}"
            loc = ""
            props = j.get("properties") or {}
            locs = props.get("locations") or j.get("locations") or []
            if isinstance(locs, list) and locs:
                loc = locs[0] if isinstance(locs[0], str) else (locs[0].get("city", "") if isinstance(locs[0], dict) else "")
            out.append(self._mk(
                title=j.get("title", ""),
                company="Microsoft",
                location=loc or props.get("primaryLocation", ""),
                url=url,
                description=(props.get("description") or "")[:600],
                full_description=props.get("description") or "",
                posted_at=_coerce_date(j.get("postingDate") or props.get("postingDate")),
                apply_type=ApplyType.DIRECT_ATS,
                source_job_id=jid,
            ))
        return out


# ─────────────────────────── Amazon ────────────────────────────────────────

class AmazonAdapter(_BigTechBase):
    name = JobSource.AMAZON
    BASE = "https://www.amazon.jobs/en/search.json"

    def _fetch_bigtech(self, query) -> list:
        out = []
        data = self.http.get_json(self.BASE, params={
            "base_query": _q(query), "loc_query": _loc(query),
            "result_limit": query.limit_per_source, "offset": 0,
            "sort": "recent",
        })
        if not data:
            return out
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

class GoogleAdapter(_BigTechBase):
    name = JobSource.GOOGLE
    BASE = "https://careers.google.com/api/v3/search/"

    def _fetch_bigtech(self, query) -> list:
        out = []
        data = self.http.get_json(self.BASE, params={
            "q": _q(query), "location": _loc(query), "page_size": query.limit_per_source,
            "page": 1,
        })
        if not data:
            return out
        for j in (data.get("jobs") or [])[: query.limit_per_source]:
            jid = str(j.get("id") or "").split("/")[-1]
            slug = (j.get("title") or "").lower().replace(" ", "-")
            url = j.get("apply_url") or f"https://www.google.com/about/careers/applications/jobs/results/{jid}-{slug}"
            locs = j.get("locations") or []
            loc = ""
            if locs and isinstance(locs, list):
                first = locs[0]
                loc = first.get("display") if isinstance(first, dict) else str(first)
            out.append(self._mk(
                title=j.get("title", ""),
                company=j.get("company_name") or "Google",
                location=loc,
                url=url,
                description=(j.get("summary") or "")[:600],
                full_description=j.get("description") or j.get("summary") or "",
                posted_at=_coerce_date(j.get("publish_date") or j.get("created")),
                apply_type=ApplyType.DIRECT_ATS,
                source_job_id=jid,
            ))
        return out


# ─────────────────────────── Apple ─────────────────────────────────────────

class AppleAdapter(_BigTechBase):
    name = JobSource.APPLE
    BASE = "https://jobs.apple.com/api/role/search"

    def _fetch_bigtech(self, query) -> list:
        out = []
        body = {
            "query": _q(query),
            "filters": {"range": {"standardWeeklyHours": {"start": None, "end": None}}},
            "page": 1,
            "locale": "en-us",
            "sort": "newest",
        }
        data = self.http.post_json(self.BASE, json_body=body, headers={
            "Referer": "https://jobs.apple.com/en-us/search",
            "Origin": "https://jobs.apple.com",
        })
        if not data:
            return out
        for j in (data.get("searchResults") or data.get("res") or [])[: query.limit_per_source]:
            pid = j.get("positionId") or j.get("id") or ""
            slug = (j.get("transformedPostingTitle") or j.get("postingTitle") or "").lower().replace(" ", "-")
            url = f"https://jobs.apple.com/en-us/details/{pid}/{slug}" if pid else ""
            locs = j.get("locations") or []
            loc = locs[0].get("name", "") if (locs and isinstance(locs[0], dict)) else ""
            out.append(self._mk(
                title=j.get("postingTitle") or j.get("transformedPostingTitle") or "",
                company="Apple",
                location=loc,
                url=url,
                description=(j.get("jobSummary") or "")[:600],
                full_description=j.get("jobSummary") or "",
                posted_at=_coerce_date(j.get("postingDate")),
                apply_type=ApplyType.DIRECT_ATS,
                source_job_id=str(pid),
            ))
        return out


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
