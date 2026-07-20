"""
SecretJobs (secretjobs.ai) — PAID, admin-only, AUTHENTICATED adapter.

secretjobs.ai is a paid subscription product with NO public job feed: the list
is served only to a logged-in, paying account. This adapter therefore reads the
account holder's OWN session, supplied via Railway env (never hard-coded, never
logged):

    SECRETJOBS_JOBS_URL     the JSON endpoint the site's frontend calls for the
                            job feed. Capture it from your logged-in session:
                            DevTools → Network → the request that returns the
                            job list → "Copy link address".
    SECRETJOBS_AUTH_HEADER  header carrying your auth. Default "Cookie"; use
                            "Authorization" if the request uses a Bearer token.
    SECRETJOBS_AUTH_VALUE   the header value (your full cookie string, or
                            "Bearer <token>").

Because the JSON shape is private and may change, the parser is shape-tolerant:
it locates the job array wherever it lives in the envelope and maps fields by a
list of common aliases. Leave the env vars unset and the adapter cleanly reports
SKIPPED_NO_CREDS (the pipeline carries on).

This is the account holder's own data accessed with their own credentials — the
adapter only runs on an admin search and only when the env is configured.
"""
from __future__ import annotations

from .base import SourceAdapter
from ..models import (
    JobSource, SourceTier, SourceCategory, ApplyType, infer_apply_type, _coerce_date,
)
from ..config import config

_BASE = "https://www.secretjobs.ai"


def _first(d: dict, *keys, default=""):
    """Return the first non-empty value among `keys` (case-sensitive aliases)."""
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if v not in (None, "", [], {}):
            return v
    return default


def _find_job_list(data):
    """Locate the array of job dicts inside an arbitrary JSON envelope."""
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for k in ("jobs", "results", "items", "data", "hits",
                  "postings", "matches", "edges", "records", "list"):
            v = data.get(k)
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
            if isinstance(v, dict):
                inner = _find_job_list(v)
                if inner:
                    return inner
        # last resort: first list-of-dicts value anywhere in the object
        for v in data.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
    return []


def _node(j):
    """Unwrap a GraphQL-style {"node": {...}} row."""
    if isinstance(j, dict) and isinstance(j.get("node"), dict):
        return j["node"]
    return j


class SecretJobsAdapter(SourceAdapter):
    name = JobSource.SECRETJOBS
    tier = SourceTier.PAID
    category = SourceCategory.BOARD
    required_env = ("SECRETJOBS_JOBS_URL", "SECRETJOBS_AUTH_VALUE")
    credit_key = "secretjobs"

    def _fetch(self, query):
        out = []
        url = (config.SECRETJOBS_JOBS_URL or "").strip()
        if not url:
            return out
        header_name = (config.SECRETJOBS_AUTH_HEADER or "Cookie").strip()
        headers = {
            header_name: config.SECRETJOBS_AUTH_VALUE,
            "Accept": "application/json",
        }
        # Best-effort query params; harmless if the endpoint ignores them.
        params = {}
        if query.titles:
            params["q"] = query.titles[0]
        data = self.http.get_json(url, headers=headers, params=params or None)
        if not data:
            return out

        for raw in _find_job_list(data)[: query.limit_per_source]:
            j = _node(raw)
            if not isinstance(j, dict):
                continue
            title = _first(j, "title", "job_title", "jobTitle", "position", "role", "name")
            if not title:
                continue
            company = _first(j, "company", "company_name", "companyName",
                             "organization", "employer", "companyTitle", default="")
            if isinstance(company, dict):
                company = _first(company, "name", "title", "display_name", default="")
            location = _first(j, "location", "city", "job_location", "jobLocation",
                              "locationName", "region", default="Israel")
            if isinstance(location, dict):
                location = _first(location, "name", "city", "addressLocality",
                                  "display_name", default="Israel")
            jurl = _first(j, "apply_url", "applyUrl", "url", "link", "job_url",
                          "jobUrl", "href", "permalink", "sourceUrl", default="")
            if isinstance(jurl, str) and jurl.startswith("/"):
                jurl = _BASE + jurl
            desc = _first(j, "description", "desc", "summary", "snippet", default="")
            if isinstance(desc, (dict, list)):
                desc = ""
            posted = _first(j, "posted_at", "datePosted", "postedAt",
                            "published_at", "created_at", "date", default=None)
            out.append(self._mk(
                title=str(title),
                company=str(company or "SecretJobs (company hidden)"),
                location=str(location or "Israel"),
                url=str(jurl or ""),
                description=str(desc or "")[:600],
                full_description=str(desc or ""),
                posted_at=_coerce_date(posted) if posted else None,
                apply_type=infer_apply_type(str(jurl or "")),
                source_job_id=str(_first(j, "id", "job_id", "jobId", "_id",
                                         "slug", "uuid", default="")),
            ))
        return out
