# `ingestion/` — multi-source job ingestion pipeline

A modular, state-machine-driven layer that sources senior business/product roles
from many platforms, normalizes them into one schema, fuzzy-deduplicates across
sources, and hands them to the existing AI scorer **once** per logical job.

## 1. Class structure & state machine

```
IngestionPipeline.run(query, seed_jobs, score_fn, persist_fn)
  INIT ─► DISCOVER ─► FETCH ─► NORMALIZE ─► DEDUP ─► SCORE ─► PERSIST ─► DONE
                                                                  │
                                              (any fatal step) ───┴─► FAILED

  DISCOVER   registry.build_adapters(role)   → free for all, paid for admin only
  FETCH      every SourceAdapter.fetch() run concurrently, fault-isolated;
             each returns AdapterResult(status, jobs, message, credits_used)
  NORMALIZE  adapters already emit NormalizedJob — just flatten
  DEDUP      Deduplicator.merge(...) → CanonicalJob[]   (the core step)
  SCORE      optional callback (app.py supplies its Gemini scorer)
  PERSIST    optional callback
```

Adapter hierarchy (one common contract — adding a source is a ~40-line file):

```
SourceAdapter (base.py)            # fetch() → AdapterResult; env/credit/tier gating
├── _BigTechBase (bigtech.py)      # FREE, requires_proxy=True
│   ├── MicrosoftAdapter           # gcsservices.careers.microsoft.com/search/api/v1
│   ├── AmazonAdapter              # www.amazon.jobs/en/search.json
│   ├── GoogleAdapter              # careers.google.com/api/v3/search
│   ├── AppleAdapter               # jobs.apple.com/api/role/search (POST)
│   └── MetaAdapter                # metacareers.com/graphql (doc_id via env)
├── aggregators.py  (PAID, admin)  # TheirStack / Adzuna / Coresignal
└── managed.py      (PAID, admin)  # ApifyAdapter / JobSpyAdapter (+ proxies)

Support: ProxyManager (rotating residential), CreditManager (caps + circuit
breaker), HttpClient (httpx→urllib fallback, retries, UA rotation).
```

## 2. Unified schema (models.py)

```
RawPayload  (a source's raw dict)
   │ adapter._mk(...) / NormalizedJob.from_legacy_dict(...)
   ▼
NormalizedJob   one validated job from ONE source (provenance == 1)
   │ Deduplicator.merge([...])
   ▼
CanonicalJob    one logical job merged from N sources; .to_legacy_dict() →
                the exact keys app.py already consumes
```

A **Microsoft endpoint payload** and a **TheirStack aggregator payload** both map
onto the same `NormalizedJob` fields (title, company, location, url, description,
full_description, posted_at, apply_type, source, source_tier, source_category,
source_job_id, provenance). See `MicrosoftAdapter._fetch_bigtech` vs
`TheirStackAdapter._fetch` for the two concrete mappings.

`SourceProvenance` records every place a job was seen, so a CanonicalJob built
from 3 sources remembers all 3 (and which one won as canonical).

## 3. Deduplication (dedup.py) — the core

Multi-stage, cheap→expensive, near-linear via company blocking + union-find:

1. **Canonical URL** — strip tracking params / www / trailing slash; exact match.
2. **Fingerprint** — exact `normalize_company|normalize_title`.
3. **Blocking** — bucket by normalized company so fuzzy compare is per-company.
4. **Fuzzy title** — rapidfuzz `token_set_ratio` within a block. Host-aware:
   * different hosts (career page vs LinkedIn vs aggregator) → threshold 88,
     borderline band corroborated by description similarity → **merge** (mirror).
   * same host, different job ids ("Engineer II" vs "III") → threshold 97 →
     **kept separate** (a board lists each role once).
5. **Merge** — union-find clusters; canonical = richest record, *preferring an
   auto-submittable ATS URL*; keeps longest description, earliest posting date,
   full provenance, and a `duplicate_count` / `dedup_confidence`.

## Free vs paid gating (requirement #4)

`registry.build_adapters(role)`: FREE adapters for everyone; PAID adapters only
when `role == "admin"` (matches `users.role`). Proxies (a paid resource) are only
handed to adapters on admin runs. Paid sources with no API key self-report
`SKIPPED_NO_CREDS` and the run proceeds.

## Integration with app.py

`app.py`'s search calls two helpers (`ingestion/integration.py`), fail-soft:
* `collect_external_sources(role, ...)` → new-source jobs as legacy dicts
* `deduplicate_raw(all_raw)` → authoritative fuzzy dedup over the WHOLE union
If the package errors, app.py falls back to its legacy exact-fingerprint dedup.

## Tests
`tests/test_ingestion_dedup.py` (cross-source duplicate fixtures) +
`tests/test_ingestion_models.py` (schema/normalization + tier gating).
