"""
Cross-platform fuzzy deduplication.

The problem this solves: the *same* product role can appear as
  - a posting on the company's Greenhouse board,
  - a mirror on LinkedIn,
  - a row from a third-party aggregator (Adzuna / TheirStack),
each with a different URL, slightly different title ("Sr. PM" vs
"Senior Product Manager (Tel Aviv)"), and a different description blob.

We must collapse all three into ONE job before the AI scorer runs, otherwise we
pay to score the same role 3× and the user swipes it 3×.

Algorithm (multi-stage, cheap → expensive):

  Stage 0  Exact canonical-URL match           — free, catches referral-tag dupes
  Stage 1  Exact fingerprint (company|title)    — free, catches identical text
  Stage 2  Blocking by normalized company       — keeps Stage 3 near-linear
  Stage 3  Fuzzy title match (rapidfuzz token-set ratio) within a block,
           confirmed by a description / company similarity check
  Stage 4  Union-find merge → pick canonical record (prefers auto-submittable
           ATS URLs + richest description) → CanonicalJob with full provenance

rapidfuzz is used when available (C-accelerated); we transparently fall back to
the stdlib ``difflib`` so the module never hard-fails if the dep is missing.
"""
from __future__ import annotations

from datetime import date
from typing import Iterable
from urllib.parse import urlsplit

from .models import (
    NormalizedJob,
    CanonicalJob,
    SourceProvenance,
    normalize_title,
    normalize_company,
)

_MIN_DATE = date(1970, 1, 1)


def _host(url: str) -> str:
    try:
        return (urlsplit(url).netloc or "").lower()
    except Exception:
        return ""


# -- fuzzy backend: rapidfuzz if present, else difflib ----------------------
try:
    from rapidfuzz import fuzz as _rf_fuzz

    def _token_set_ratio(a: str, b: str) -> float:
        return float(_rf_fuzz.token_set_ratio(a, b))

    def _partial_ratio(a: str, b: str) -> float:
        return float(_rf_fuzz.partial_ratio(a, b))

    _FUZZ_BACKEND = "rapidfuzz"
except Exception:  # pragma: no cover - exercised only when rapidfuzz absent
    from difflib import SequenceMatcher

    def _token_set_ratio(a: str, b: str) -> float:
        sa = " ".join(sorted(set(a.split())))
        sb = " ".join(sorted(set(b.split())))
        return SequenceMatcher(None, sa, sb).ratio() * 100.0

    def _partial_ratio(a: str, b: str) -> float:
        return SequenceMatcher(None, a, b).ratio() * 100.0

    _FUZZ_BACKEND = "difflib"


class _UnionFind:
    """Tiny disjoint-set for clustering duplicate indices."""

    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:      # path compression
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


class Deduplicator:
    """
    Collapse many NormalizedJobs into CanonicalJobs.

    Parameters
    ----------
    title_threshold:
        token-set ratio (0-100) above which two same-company titles on
        *different* hosts are deemed the same role. 88 is conservative.
    desc_threshold:
        secondary gate — if cross-host titles are borderline we additionally
        require description similarity to confirm.
    borderline:
        width of the "needs description confirmation" band below the threshold.
    """

    # Two DISTINCT listings on the SAME site (a company's own board lists each
    # role once, with its own id) should only merge on a near-identical title —
    # otherwise "Engineer II" and "Engineer III" wrongly collapse. The same role
    # MIRRORED across DIFFERENT hosts is the case we *do* want to merge at the
    # normal threshold.
    SAME_HOST_THRESHOLD = 97.0

    def __init__(
        self,
        title_threshold: float = 88.0,
        desc_threshold: float = 60.0,
        borderline: float = 8.0,
    ) -> None:
        self.title_threshold = title_threshold
        self.desc_threshold = desc_threshold
        self.borderline = borderline
        self.backend = _FUZZ_BACKEND

    # ---- public API -------------------------------------------------------

    def merge(self, jobs: Iterable[NormalizedJob]) -> list[CanonicalJob]:
        jobs = [j for j in jobs if (j.title or "").strip()]
        n = len(jobs)
        if n == 0:
            return []
        if n == 1:
            return [CanonicalJob.from_normalized(jobs[0])]

        uf = _UnionFind(n)

        # Stage 0 — exact canonical URL.
        url_map: dict[str, int] = {}
        for i, j in enumerate(jobs):
            cu = j.canonical_url
            if not cu:
                continue
            if cu in url_map:
                uf.union(url_map[cu], i)
            else:
                url_map[cu] = i

        # Stage 1 — exact fingerprint (normalized company|title).
        fp_map: dict[str, int] = {}
        for i, j in enumerate(jobs):
            fp = j.fingerprint
            if fp == "|":
                continue
            if fp in fp_map:
                uf.union(fp_map[fp], i)
            else:
                fp_map[fp] = i

        # Stage 2 — block by company so Stage 3 stays near-linear.
        blocks: dict[str, list[int]] = {}
        for i, j in enumerate(jobs):
            blocks.setdefault(j.blocking_key, []).append(i)

        # Stage 3 — fuzzy compare within each block only.
        for _, idxs in blocks.items():
            if len(idxs) < 2:
                continue
            norm_titles = {i: normalize_title(jobs[i].title) for i in idxs}
            for a_pos in range(len(idxs)):
                ia = idxs[a_pos]
                ta = norm_titles[ia]
                if not ta:
                    continue
                for b_pos in range(a_pos + 1, len(idxs)):
                    ib = idxs[b_pos]
                    if uf.find(ia) == uf.find(ib):
                        continue                  # already merged via URL/fp
                    if self._same_role(jobs[ia], jobs[ib], ta, norm_titles[ib]):
                        uf.union(ia, ib)

        # Stage 4 — build clusters → canonical records.
        clusters: dict[int, list[int]] = {}
        for i in range(n):
            clusters.setdefault(uf.find(i), []).append(i)

        out: list[CanonicalJob] = []
        for members in clusters.values():
            out.append(self._build_canonical([jobs[i] for i in members]))
        out.sort(key=lambda c: (c.duplicate_count, c.posted_at or _MIN_DATE),
                 reverse=True)
        return out

    # ---- internals --------------------------------------------------------

    def _same_role(
        self, ja: NormalizedJob, jb: NormalizedJob, ta: str, tb: str
    ) -> bool:
        """Decide whether two postings (already same company block) are one role."""
        if not tb:
            return False
        comp_a, comp_b = normalize_company(ja.company), normalize_company(jb.company)
        # If both have a company and they differ materially, never merge.
        if comp_a and comp_b and comp_a != comp_b:
            if _token_set_ratio(comp_a, comp_b) < 90:
                return False

        # host-aware threshold (see SAME_HOST_THRESHOLD note above)
        ha, hb = _host(ja.canonical_url), _host(jb.canonical_url)
        cu_a, cu_b = ja.canonical_url, jb.canonical_url
        same_host_distinct = bool(ha and hb and ha == hb and cu_a and cu_b and cu_a != cu_b)
        threshold = self.SAME_HOST_THRESHOLD if same_host_distinct else self.title_threshold

        tscore = _token_set_ratio(ta, tb)
        if tscore >= threshold:
            return True
        # borderline band (cross-host only): require description corroboration
        if not same_host_distinct and tscore >= threshold - self.borderline:
            da = (ja.full_description or ja.description or "")[:1500]
            db = (jb.full_description or jb.description or "")[:1500]
            if da and db and _partial_ratio(da, db) >= self.desc_threshold:
                return True
        return False

    def _build_canonical(self, members: list[NormalizedJob]) -> CanonicalJob:
        # canonical record = richest member (prefers ATS/auto-submittable URLs)
        winner = max(members, key=lambda j: j.richness())
        canon = CanonicalJob.from_normalized(winner)

        # merge provenance from every member, dedup by (source, canonical_url)
        seen: set[tuple] = set()
        provs: list[SourceProvenance] = []
        best_desc = winner.full_description or winner.description or ""
        earliest = winner.posted_at
        for m in members:
            key = (m.source.value, m.canonical_url)
            if key not in seen:
                seen.add(key)
                provs.append(m.provenance())
            cand = m.full_description or m.description or ""
            if len(cand) > len(best_desc):
                best_desc = cand
            if m.posted_at and (earliest is None or m.posted_at < earliest):
                earliest = m.posted_at

        canon.sources = provs
        canon.duplicate_count = len(members)
        canon.full_description = best_desc
        if earliest:
            canon.posted_at = earliest
 
        canon.dedup_confidence = 1.0 if len(members) == 1 else self._cluster_conf(members)
        return canon

    @staticmethod
    def _cluster_conf(members: list[NormalizedJob]) -> float:
        urls = {m.canonical_url for m in members if m.canonical_url}
        fps = {m.fingerprint for m in members}
        if len(urls) == 1 and urls != {""}:
            return 1.0          # all share a URL
        if len(fps) == 1:
            return 0.95         # identical normalized text
        return 0.85            # fuzzy-only merge
