"""
Unified Pydantic schema for the ingestion pipeline.

The whole point of this module: every source — whether it's a reverse-engineered
Big-Tech JSON endpoint or a commercial aggregator API — produces wildly different
payloads. We normalize them all into ONE shape so the deduper and the AI scorer
never have to care where a job came from.

Three layers:

    RawPayload      a source's raw dict (untyped, whatever the API returned)
       │  adapter._normalize(...)
       ▼
    NormalizedJob   one validated job from ONE source (provenance length == 1)
       │  Deduplicator.merge([...])
       ▼
    CanonicalJob    one logical job merged from N sources (provenance length >= 1)

`CanonicalJob.to_legacy_dict()` emits the exact dict keys app.py already consumes,
so the new pipeline drops into the existing scorer/persist code with no rewrite.
"""
from __future__ import annotations

import re
import hashlib
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

try:
    from pydantic import BaseModel, Field, field_validator, ConfigDict
    _PYDANTIC = True
except Exception:  # pragma: no cover - pydantic is a hard dep, but degrade loudly
    _PYDANTIC = False
    raise


# ─────────────────────────── enums ────────────────────────────────────────

class SourceTier(str, Enum):
    """Access tier. FREE runs for every user; PAID runs for the admin only."""
    FREE = "free"
    PAID = "paid"


class SourceCategory(str, Enum):
    ATS = "ats"                  # Greenhouse / Lever / Comeet / Workday / Ashby ...
    BIG_TECH = "big_tech"        # MSFT / Google / Meta / Amazon / Apple internal APIs
    AGGREGATOR = "aggregator"    # TheirStack / Adzuna / Coresignal
    MANAGED_SCRAPE = "managed_scrape"   # Apify actors / JobSpy
    BOARD = "board"              # LinkedIn / Indeed / Glassdoor public surfaces
    SEARCH = "search"            # LLM/web-search sourced


class JobSource(str, Enum):
    # FREE — ATS / direct
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    SMARTRECRUITERS = "smartrecruiters"
    COMEET = "comeet"
    WORKDAY = "workday"
    ASHBY = "ashby"
    CAREER_PAGE = "career_page"
    # FREE — Big Tech
    MICROSOFT = "microsoft"
    GOOGLE = "google"
    META = "meta"
    AMAZON = "amazon"
    APPLE = "apple"
    # FREE — public boards
    LINKEDIN = "linkedin"
    INDEED = "indeed"
    GLASSDOOR = "glassdoor"
    GEMINI_SEARCH = "gemini_search"
    # PAID — aggregators
    THEIRSTACK = "theirstack"
    ADZUNA = "adzuna"
    CORESIGNAL = "coresignal"
    # PAID — managed scraping
    APIFY = "apify"
    JOBSPY = "jobspy"
    # fallback
    UNKNOWN = "unknown"


class ApplyType(str, Enum):
    """Mirrors app.py's domain rule: only DIRECT_ATS is auto-submittable."""
    DIRECT_ATS = "direct_ats"          # Greenhouse/Lever/Workday/Comeet/Ashby/SR/careers
    MANUAL_REQUIRED = "manual_required"  # LinkedIn/Indeed/Glassdoor listings
    UNKNOWN = "unknown"


# ATS hostnames that the apply engine can auto-submit to.
_DIRECT_ATS_HOSTS = (
    "greenhouse.io", "boards.greenhouse.io", "job-boards.greenhouse.io",
    "lever.co", "jobs.lever.co",
    "myworkdayjobs.com", "wd1.myworkdayjobs.com", "wd3.myworkdayjobs.com",
    "comeet.com", "comeet.co",
    "smartrecruiters.com", "jobs.smartrecruiters.com",
    "ashbyhq.com", "jobs.ashbyhq.com",
)
_BOARD_HOSTS = ("linkedin.com", "indeed.com", "glassdoor.com", "ziprecruiter.com")

# Tracking params we strip when canonicalizing URLs (kills false "different URL"
# duplicates where the only difference is a utm/referral tag).
_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gh_src", "gh_jid", "src", "source", "ref", "referrer", "trk", "trackingId",
    "refId", "gclid", "fbclid", "mc_cid", "mc_eid", "lipi",
}


# ─────────────────────────── helpers ───────────────────────────────────────

def canonical_url(url: str) -> str:
    """
    Normalize a posting URL so the same job from two referral paths collapses:
      - lowercase scheme + host, drop ``www.``
      - strip tracking query params
      - drop fragment, trailing slash
    """
    if not url:
        return ""
    url = url.strip()
    try:
        parts = urlsplit(url)
    except Exception:
        return url.lower()
    scheme = (parts.scheme or "https").lower()
    host = (parts.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = parts.path.rstrip("/")
    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False)
            if k.lower() not in _TRACKING_PARAMS]
    query = urlencode(sorted(kept))
    return urlunsplit((scheme, host, path, query, ""))


def _host_of(url: str) -> str:
    try:
        return (urlsplit(url).netloc or "").lower()
    except Exception:
        return ""


def infer_apply_type(url: str, source: "JobSource | str | None" = None) -> ApplyType:
    """Classify auto-submittable vs manual based on host (matches app.py's rule)."""
    host = _host_of(url)
    if any(host == h or host.endswith("." + h) or h in host for h in _BOARD_HOSTS):
        return ApplyType.MANUAL_REQUIRED
    if any(h in host for h in _DIRECT_ATS_HOSTS):
        return ApplyType.DIRECT_ATS
    # company career pages (`/careers`, `/jobs`) are auto-submittable best-effort
    if host and ("career" in url.lower() or "/jobs" in url.lower()):
        return ApplyType.DIRECT_ATS
    return ApplyType.UNKNOWN


def normalize_title(title: str) -> str:
    """
    Aggressive title normalization used both for the fingerprint and as a
    fuzzy-match pre-clean. Strips parentheticals, location/req suffixes,
    seniority noise, punctuation; lowercases; collapses whitespace.
    """
    t = (title or "").lower()
    t = re.sub(r"[\(\[\{].*?[\)\]\}]", " ", t)        # (Tel Aviv), [Remote], {f/m/d}
    t = re.sub(r"\b(req|requisition|job)?\s*#?\s*\d{3,}\b", " ", t)  # req ids
    t = re.sub(r"[-–—|/,]+", " ", t)
    # drop common gendered / location / contract tails
    t = re.sub(r"\b(m\W?f\W?d|f\W?m\W?d|w\W?m\W?d|all genders?)\b", " ", t)
    t = re.sub(r"[^a-z0-9֐-׿ ]", " ", t)     # keep Hebrew range too
    t = re.sub(r"\s+", " ", t).strip()
    return t


def normalize_company(company: str) -> str:
    """Normalize company names: drop legal suffixes + punctuation, lowercase."""
    c = (company or "").lower()
    c = re.sub(r"[^a-z0-9֐-׿ ]", " ", c)
    c = re.sub(
        r"\b(inc|ltd|llc|gmbh|corp|co|company|technologies|technology|labs|"
        r"israel|global|group|holdings|plc|sa|ag|bv)\b", " ", c)
    c = re.sub(r"\s+", " ", c).strip()
    return c


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ─────────────────────────── models ────────────────────────────────────────

class SearchQuery(BaseModel):
    """A normalized search request handed to every adapter."""
    model_config = ConfigDict(extra="ignore")

    titles: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=lambda: ["Israel"])
    keywords: list[str] = Field(default_factory=list)
    remote_ok: bool = True
    limit_per_source: int = 60
    # URLs already in the user's history — adapters may skip these early.
    existing_urls: set[str] = Field(default_factory=set)

    @field_validator("titles", "locations", "keywords", mode="before")
    @classmethod
    def _clean_lists(cls, v: Any) -> list[str]:
        if not v:
            return []
        if isinstance(v, str):
            v = [v]
        return [str(x).strip() for x in v if str(x).strip()]


class SourceProvenance(BaseModel):
    """One sighting of a job on one platform. A CanonicalJob keeps every sighting."""
    model_config = ConfigDict(extra="ignore")

    source: JobSource
    tier: SourceTier
    category: SourceCategory
    url: str = ""
    source_job_id: Optional[str] = None
    posted_at: Optional[date] = None
    fetched_at: datetime = Field(default_factory=_utcnow)
    extra: dict[str, Any] = Field(default_factory=dict)


class NormalizedJob(BaseModel):
    """One validated job from ONE source. Unit of input to the deduper."""
    model_config = ConfigDict(extra="ignore", validate_assignment=True)

    title: str
    company: str = ""
    location: Optional[str] = None
    remote: Optional[bool] = None

    url: str = ""
    description: str = ""
    full_description: str = ""

    posted_at: Optional[date] = None
    seniority: Optional[str] = None
    employment_type: Optional[str] = None

    apply_type: ApplyType = ApplyType.UNKNOWN
    source: JobSource = JobSource.UNKNOWN
    source_tier: SourceTier = SourceTier.FREE
    source_category: SourceCategory = SourceCategory.SEARCH
    source_job_id: Optional[str] = None

    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("title", "company", mode="before")
    @classmethod
    def _strip(cls, v: Any) -> str:
        return ("" if v is None else str(v)).strip()

    # -- derived keys used by the deduper (computed, never trusted from input) --

    @property
    def canonical_url(self) -> str:
        return canonical_url(self.url)

    @property
    def fingerprint(self) -> str:
        """Exact-match key: normalized company|title. Cheap first-pass dedup."""
        return f"{normalize_company(self.company)}|{normalize_title(self.title)}"

    @property
    def blocking_key(self) -> str:
        """
        Coarse bucket so fuzzy comparison is O(n²) only *within* a bucket, not
        across the whole corpus. Company is the strongest blocker; fall back to
        the first 4 chars of the normalized title when company is missing.
        """
        comp = normalize_company(self.company)
        if comp:
            return comp
        nt = normalize_title(self.title)
        return nt[:4] if nt else "_"

    def provenance(self) -> SourceProvenance:
        return SourceProvenance(
            source=self.source, tier=self.source_tier, category=self.source_category,
            url=self.url, source_job_id=self.source_job_id, posted_at=self.posted_at,
        )

    def richness(self) -> int:
        """How 'complete' this record is — used to pick the canonical winner."""
        score = len(self.full_description or "") + len(self.description or "")
        if self.apply_type == ApplyType.DIRECT_ATS:
            score += 5000   # strongly prefer auto-submittable URLs as canonical
        elif self.apply_type == ApplyType.UNKNOWN:
            score += 1000
        if self.location:
            score += 200
        if self.posted_at:
            score += 200
        return score

    @classmethod
    def from_legacy_dict(cls, d: dict[str, Any]) -> "NormalizedJob":
        """
        Bring an existing app.py source dict (Greenhouse/Lever/LinkedIn/etc.)
        into the unified model so old + new sources dedup together.
        """
        url = (d.get("url") or "").strip()
        src_raw = (d.get("source") or "").strip().lower()
        try:
            source = JobSource(src_raw)
        except ValueError:
            source = JobSource.UNKNOWN
        return cls(
            title=d.get("job_title") or d.get("title") or "",
            company=d.get("company") or "",
            location=d.get("location") or None,
            url=url,
            description=d.get("description") or "",
            full_description=d.get("full_description") or d.get("description") or "",
            posted_at=_coerce_date(d.get("publish_date") or d.get("posted_at")),
            apply_type=infer_apply_type(url, source),
            source=source,
            source_category=_CATEGORY_BY_SOURCE.get(source, SourceCategory.SEARCH),
            source_tier=_TIER_BY_SOURCE.get(source, SourceTier.FREE),
            extra={"_legacy": {k: d[k] for k in d
                               if k not in {"job_title", "title", "company", "location",
                                            "url", "description", "full_description",
                                            "source", "publish_date"}}},
        )


class CanonicalJob(BaseModel):
    """
    One logical job after cross-source fuzzy merge. Carries every place it was
    seen (provenance) plus dedup metadata, and emits the legacy dict the rest
    of app.py consumes.
    """
    model_config = ConfigDict(extra="ignore")

    title: str
    company: str = ""
    location: Optional[str] = None
    url: str = ""
    description: str = ""
    full_description: str = ""
    posted_at: Optional[date] = None
    apply_type: ApplyType = ApplyType.UNKNOWN

    primary_source: JobSource = JobSource.UNKNOWN
    sources: list[SourceProvenance] = Field(default_factory=list)
    duplicate_count: int = 1
    dedup_confidence: float = 1.0          # 1.0 = exact URL, lower = fuzzy match

    @property
    def fingerprint(self) -> str:
        return f"{normalize_company(self.company)}|{normalize_title(self.title)}"

    @classmethod
    def from_normalized(cls, job: NormalizedJob) -> "CanonicalJob":
        return cls(
            title=job.title, company=job.company, location=job.location,
            url=job.url, description=job.description,
            full_description=job.full_description, posted_at=job.posted_at,
            apply_type=job.apply_type, primary_source=job.source,
            sources=[job.provenance()], duplicate_count=1, dedup_confidence=1.0,
        )

    def to_legacy_dict(self) -> dict[str, Any]:
        """Exact key shape app.py's scorer + persist code already expects."""
        return {
            "job_title": self.title,
            "title": self.title,
            "company": self.company,
            "location": self.location or "",
            "url": self.url,
            "description": self.description or self.title,
            "full_description": self.full_description or self.description or "",
            "source": self.primary_source.value,
            "publish_date": self.posted_at.isoformat() if self.posted_at else None,
            # observability extras (ignored by old code, handy for debugging/UI)
            "_duplicate_count": self.duplicate_count,
            "_sources_seen": [p.source.value for p in self.sources],
            "_apply_type": self.apply_type.value,
        }


# ──────────────── source → tier / category lookup tables ───────────────────

_TIER_BY_SOURCE: dict[JobSource, SourceTier] = {
    # FREE
    JobSource.GREENHOUSE: SourceTier.FREE, JobSource.LEVER: SourceTier.FREE,
    JobSource.SMARTRECRUITERS: SourceTier.FREE, JobSource.COMEET: SourceTier.FREE,
    JobSource.WORKDAY: SourceTier.FREE, JobSource.ASHBY: SourceTier.FREE,
    JobSource.CAREER_PAGE: SourceTier.FREE,
    JobSource.MICROSOFT: SourceTier.FREE, JobSource.GOOGLE: SourceTier.FREE,
    JobSource.META: SourceTier.FREE, JobSource.AMAZON: SourceTier.FREE,
    JobSource.APPLE: SourceTier.FREE,
    JobSource.LINKEDIN: SourceTier.FREE, JobSource.INDEED: SourceTier.FREE,
    JobSource.GLASSDOOR: SourceTier.FREE, JobSource.GEMINI_SEARCH: SourceTier.FREE,
    # PAID (admin only)
    JobSource.THEIRSTACK: SourceTier.PAID, JobSource.ADZUNA: SourceTier.PAID,
    JobSource.CORESIGNAL: SourceTier.PAID, JobSource.APIFY: SourceTier.PAID,
    JobSource.JOBSPY: SourceTier.PAID,
}

_CATEGORY_BY_SOURCE: dict[JobSource, SourceCategory] = {
    JobSource.GREENHOUSE: SourceCategory.ATS, JobSource.LEVER: SourceCategory.ATS,
    JobSource.SMARTRECRUITERS: SourceCategory.ATS, JobSource.COMEET: SourceCategory.ATS,
    JobSource.WORKDAY: SourceCategory.ATS, JobSource.ASHBY: SourceCategory.ATS,
    JobSource.CAREER_PAGE: SourceCategory.ATS,
    JobSource.MICROSOFT: SourceCategory.BIG_TECH, JobSource.GOOGLE: SourceCategory.BIG_TECH,
    JobSource.META: SourceCategory.BIG_TECH, JobSource.AMAZON: SourceCategory.BIG_TECH,
    JobSource.APPLE: SourceCategory.BIG_TECH,
    JobSource.LINKEDIN: SourceCategory.BOARD, JobSource.INDEED: SourceCategory.BOARD,
    JobSource.GLASSDOOR: SourceCategory.BOARD,
    JobSource.GEMINI_SEARCH: SourceCategory.SEARCH,
    JobSource.THEIRSTACK: SourceCategory.AGGREGATOR, JobSource.ADZUNA: SourceCategory.AGGREGATOR,
    JobSource.CORESIGNAL: SourceCategory.AGGREGATOR,
    JobSource.APIFY: SourceCategory.MANAGED_SCRAPE, JobSource.JOBSPY: SourceCategory.MANAGED_SCRAPE,
}


def _coerce_date(v: Any) -> Optional[date]:
    if not v:
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%dT%H:%M:%S.%fZ", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s[:len(fmt) + 6], fmt).date()
        except Exception:
            continue
    # ISO-ish fallback
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except Exception:
        return None


def stable_id(*parts: str) -> str:
    """Deterministic short id from arbitrary parts (used by adapters/tests)."""
    h = hashlib.sha1("||".join(p or "" for p in parts).encode("utf-8")).hexdigest()
    return h[:16]
