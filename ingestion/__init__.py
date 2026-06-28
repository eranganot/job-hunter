"""
Job Hunter — multi-source ingestion pipeline.

A modular, state-machine-driven ingestion layer that sources jobs from:

  * FREE tier  (everyone): direct ATS endpoints + Big-Tech internal JSON APIs
  * PAID tier  (admin only): TheirStack, Adzuna, Coresignal, Apify, JobSpy

All sources normalize into one unified Pydantic schema (`NormalizedJob`),
get fuzzy-deduplicated across platforms into `CanonicalJob`s (so a role
mirrored on a career page + LinkedIn + an aggregator is scored ONCE), and
are handed back to the existing AI scorer.

Public surface used by app.py:

    from ingestion import collect_external_sources, deduplicate_raw

`collect_external_sources(...)`  -> list[legacy dict]  (new sources only)
`deduplicate_raw(all_raw)`       -> list[legacy dict]  (fuzzy-merged union)

Both speak the legacy dict shape app.py already uses
({job_title, company, location, url, description, full_description, source, ...})
so integration is a two-line change and never breaks the existing flow.
"""
from __future__ import annotations

from .models import (
    NormalizedJob,
    CanonicalJob,
    SourceProvenance,
    SearchQuery,
    SourceTier,
    SourceCategory,
    JobSource,
    ApplyType,
)
from .dedup import Deduplicator
from .pipeline import IngestionPipeline, PipelineState, PipelineResult
from .integration import collect_external_sources, deduplicate_raw

__all__ = [
    "NormalizedJob",
    "CanonicalJob",
    "SourceProvenance",
    "SearchQuery",
    "SourceTier",
    "SourceCategory",
    "JobSource",
    "ApplyType",
    "Deduplicator",
    "IngestionPipeline",
    "PipelineState",
    "PipelineResult",
    "collect_external_sources",
    "deduplicate_raw",
]
