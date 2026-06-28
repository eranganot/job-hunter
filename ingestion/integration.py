"""
Integration surface for app.py.

Two functions, both speaking the *legacy dict* shape app.py already uses, so
wiring the new pipeline into the 6,600-line monolith is a two-line change and
can never break the existing flow (every call is wrapped to fail soft).

    collect_external_sources(...)  -> list[legacy dict]
        Run the NEW sources (Big-Tech free + paid aggregators/scrapers, gated by
        role) and return their jobs as raw legacy dicts to append to app.py's
        existing ``all_raw`` list. Paid sources only run for role == "admin".

    deduplicate_raw(all_raw)       -> list[legacy dict]
        The authoritative fuzzy dedup over the WHOLE union (existing ATS/board
        sources + new sources). Replaces app.py's old exact-fingerprint block so
        a role mirrored across a career page + LinkedIn + an aggregator reaches
        the AI scorer exactly once.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional

from .models import NormalizedJob, CanonicalJob, SearchQuery
from .dedup import Deduplicator
from .registry import build_adapters
from .proxies import ProxyManager
from .credits import CreditManager
from .relevance import gate_enabled, passes as _passes_relevance

# Process-wide singletons so the credit ledger / circuit-breaker state persists
# across searches within a running app process.
_PROXY_MGR: Optional[ProxyManager] = None
_CREDIT_MGR: Optional[CreditManager] = None


def _managers() -> tuple[ProxyManager, CreditManager]:
    global _PROXY_MGR, _CREDIT_MGR
    if _PROXY_MGR is None:
        _PROXY_MGR = ProxyManager()
    if _CREDIT_MGR is None:
        _CREDIT_MGR = CreditManager()
    return _PROXY_MGR, _CREDIT_MGR


def collect_external_sources(
    role: str,
    titles: list[str],
    locations: list[str],
    keywords: list[str],
    existing_urls: Optional[set[str]] = None,
    limit_per_source: int = 60,
) -> list[dict[str, Any]]:
    """Fetch the new sources concurrently; return raw legacy dicts. Fails soft."""
    proxies, credits = _managers()
    query = SearchQuery(
        titles=titles or [], locations=locations or ["Israel"],
        keywords=keywords or [], existing_urls=existing_urls or set(),
        limit_per_source=limit_per_source,
    )
    adapters = build_adapters(role, proxies, credits)
    out: list[dict[str, Any]] = []
    reports: list[str] = []
    _gate = gate_enabled()
    dropped = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(a.fetch, query): a for a in adapters}
        for fut in as_completed(futs):
            a = futs[fut]
            try:
                res = fut.result()
            except Exception as e:
                reports.append(f"{a.name.value}:ERR({type(e).__name__})")
                continue
            reports.append(f"{a.name.value}:{res.status.value}({res.count})")
            for nj in res.jobs:
                # Geography + title gate: external sources are global and ignore
                # the user's preferences server-side, so keep only jobs that match
                # the user's locations/titles before they reach the scorer.
                if _gate and not _passes_relevance(
                        nj.title, nj.location or "", titles or [],
                        locations or [], keywords or []):
                    dropped += 1
                    continue
                out.append(CanonicalJob.from_normalized(nj).to_legacy_dict())
    print(f"[ingest] external sources (role={role}): " + ", ".join(sorted(reports)))
    print(f"[ingest] external sources contributed {len(out)} jobs "
          f"(gated out {dropped} off-preference; gate={'on' if _gate else 'off'})")
    return out


def deduplicate_raw(
    all_raw: list[dict[str, Any]],
    title_threshold: float = 88.0,
) -> list[dict[str, Any]]:
    """
    Authoritative cross-source fuzzy dedup over the union of all sources.

    Input/Output are app.py's legacy dicts. Each output dict additionally carries
    ``_duplicate_count`` and ``_sources_seen`` for observability (old code
    ignores unknown keys).
    """
    if not all_raw:
        return []
    normalized = [NormalizedJob.from_legacy_dict(d) for d in all_raw]
    deduper = Deduplicator(title_threshold=title_threshold)
    canonical = deduper.merge(normalized)
    print(f"[ingest] fuzzy dedup ({deduper.backend}): "
          f"{len(all_raw)} -> {len(canonical)} canonical jobs")
    return [c.to_legacy_dict() for c in canonical]
