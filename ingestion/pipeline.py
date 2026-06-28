"""
IngestionPipeline — the orchestrator + state machine.

States (run in order, per search):

    INIT ──► DISCOVER ──► FETCH ──► NORMALIZE ──► DEDUP ──► SCORE ──► PERSIST ──► DONE
                                                                          │
                                                          (any fatal step)└─► FAILED

  DISCOVER   pick the adapter set for this user's role (free vs admin/paid)
  FETCH      run every adapter concurrently; collect per-source status reports
             (OK / SKIPPED_NO_CREDS / NO_CREDITS / UNAVAILABLE / ERROR)
  NORMALIZE  adapters already emit NormalizedJob; we just flatten the lists
  DEDUP      Deduplicator.merge(...) collapses cross-source duplicates → Canonical
  SCORE      optional callback (in app.py the existing Gemini scorer does this);
             if no scorer is supplied the pipeline stops after DEDUP
  PERSIST    optional callback

The state machine is deliberately fault-tolerant: a single source failing never
fails the run — it's recorded in the report and the others proceed. This is the
"fallback logic if an API becomes unavailable" requirement, at the orchestration
level (the CreditManager handles it at the per-provider level).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

from .models import SearchQuery, NormalizedJob, CanonicalJob, SourceTier
from .dedup import Deduplicator
from .registry import build_adapters, is_admin
from .proxies import ProxyManager
from .credits import CreditManager
from .adapters.base import AdapterResult, AdapterStatus


class PipelineState(str, Enum):
    INIT = "init"
    DISCOVER = "discover"
    FETCH = "fetch"
    NORMALIZE = "normalize"
    DEDUP = "dedup"
    SCORE = "score"
    PERSIST = "persist"
    DONE = "done"
    FAILED = "failed"


@dataclass
class SourceReport:
    source: str
    status: str
    count: int
    message: str = ""


@dataclass
class PipelineResult:
    state: PipelineState
    canonical_jobs: list[CanonicalJob] = field(default_factory=list)
    reports: list[SourceReport] = field(default_factory=list)
    raw_count: int = 0
    deduped_count: int = 0
    role: str = "user"

    def summary(self) -> str:
        ok = sum(1 for r in self.reports if r.status == AdapterStatus.OK.value)
        return (f"[ingest] role={self.role} state={self.state.value} "
                f"sources_ok={ok}/{len(self.reports)} "
                f"raw={self.raw_count} -> canonical={self.deduped_count}")


# Optional callbacks injected by the caller (app.py wires its Gemini scorer here).
ScoreFn = Callable[[list[CanonicalJob], SearchQuery], list[CanonicalJob]]
PersistFn = Callable[[list[CanonicalJob]], int]


class IngestionPipeline:
    def __init__(
        self,
        role: str,
        proxy_manager: Optional[ProxyManager] = None,
        credit_manager: Optional[CreditManager] = None,
        deduper: Optional[Deduplicator] = None,
        max_workers: int = 8,
        on_event: Optional[Callable[[PipelineState, str], None]] = None,
    ) -> None:
        self.role = role or "user"
        self.proxies = proxy_manager or ProxyManager()
        self.credits = credit_manager or CreditManager()
        self.deduper = deduper or Deduplicator()
        self.max_workers = max_workers
        self.on_event = on_event or (lambda st, msg: None)
        self.state = PipelineState.INIT

    def _emit(self, state: PipelineState, msg: str = "") -> None:
        self.state = state
        try:
            self.on_event(state, msg)
        except Exception:
            pass

    # ---- main entry -------------------------------------------------------

    def run(
        self,
        query: SearchQuery,
        seed_jobs: Optional[list[NormalizedJob]] = None,
        score_fn: Optional[ScoreFn] = None,
        persist_fn: Optional[PersistFn] = None,
    ) -> PipelineResult:
        """
        Run the pipeline. ``seed_jobs`` lets the caller fold in jobs collected
        elsewhere (e.g. app.py's existing ATS/board sources) so EVERYTHING is
        deduped together and each logical role reaches the scorer once.
        """
        result = PipelineResult(state=PipelineState.INIT, role=self.role)
        try:
            # DISCOVER
            self._emit(PipelineState.DISCOVER)
            adapters = build_adapters(self.role, self.proxies, self.credits)

            # FETCH (concurrent, fault-isolated)
            self._emit(PipelineState.FETCH)
            collected: list[NormalizedJob] = list(seed_jobs or [])
            with ThreadPoolExecutor(max_workers=self.max_workers) as ex:
                futs = {ex.submit(a.fetch, query): a for a in adapters}
                for fut in as_completed(futs):
                    a = futs[fut]
                    try:
                        res: AdapterResult = fut.result()
                    except Exception as e:
                        result.reports.append(SourceReport(
                            a.name.value, AdapterStatus.ERROR.value, 0, str(e)))
                        continue
                    collected.extend(res.jobs)
                    result.reports.append(SourceReport(
                        res.source.value, res.status.value, res.count, res.message))

            # NORMALIZE (already typed; just count)
            self._emit(PipelineState.NORMALIZE)
            result.raw_count = len(collected)

            # DEDUP — the core step
            self._emit(PipelineState.DEDUP)
            canonical = self.deduper.merge(collected)
            result.canonical_jobs = canonical
            result.deduped_count = len(canonical)

            # SCORE (optional — app.py supplies its Gemini scorer)
            if score_fn is not None:
                self._emit(PipelineState.SCORE)
                result.canonical_jobs = score_fn(result.canonical_jobs, query)

            # PERSIST (optional)
            if persist_fn is not None:
                self._emit(PipelineState.PERSIST)
                persist_fn(result.canonical_jobs)

            self._emit(PipelineState.DONE)
            result.state = PipelineState.DONE
            return result
        except Exception as e:
            self._emit(PipelineState.FAILED, str(e))
            result.state = PipelineState.FAILED
            return result
