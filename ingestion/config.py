"""
Central env-var configuration for the ingestion pipeline.

Every paid integration and the proxy layer is wired here so credentials live in
ONE place (Railway env vars). Nothing here is required — absent keys simply make
the corresponding adapter report SKIPPED_NO_CREDS and the pipeline carries on.

See .env.example for the full list with descriptions.
"""
from __future__ import annotations

import os


def _env(key: str, default: str = "") -> str:
    return (os.environ.get(key) or default).strip()


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


def _env_bool(key: str, default: bool = False) -> bool:
    v = (os.environ.get(key) or "").strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on")


class Config:
    # ── retain threshold (user wants 30%+) ────────────────────────────────
    SCORE_THRESHOLD = _env_int("INGEST_SCORE_THRESHOLD", 30)

    # ── proxy (rotating residential) ──────────────────────────────────────
    # Either a single URL, a comma-separated pool, or Bright-Data-style creds.
    PROXY_URL = _env("RESIDENTIAL_PROXY_URL")              # http://user:pass@host:port
    PROXY_POOL = _env("PROXY_POOL")                        # csv of proxy urls
    PROXY_USERNAME = _env("PROXY_USERNAME")                # e.g. brd-customer-...-zone-...
    PROXY_PASSWORD = _env("PROXY_PASSWORD")
    PROXY_HOST = _env("PROXY_HOST", "brd.superproxy.io")
    PROXY_PORT = _env_int("PROXY_PORT", 22225)
    PROXY_STICKY_SESSIONS = _env_bool("PROXY_STICKY_SESSIONS", True)
    PROXY_ENABLED = _env_bool("PROXY_ENABLED", True)

    # ── aggregators (PAID — admin only) ───────────────────────────────────
    ADZUNA_APP_ID = _env("ADZUNA_APP_ID")
    ADZUNA_APP_KEY = _env("ADZUNA_APP_KEY")
    ADZUNA_COUNTRY = _env("ADZUNA_COUNTRY", "gb")          # Adzuna has no IL feed
    ADZUNA_MONTHLY_CAP = _env_int("ADZUNA_MONTHLY_CAP", 2000)

    THEIRSTACK_API_KEY = _env("THEIRSTACK_API_KEY")
    THEIRSTACK_MONTHLY_CAP = _env_int("THEIRSTACK_MONTHLY_CAP", 500)

    CORESIGNAL_API_KEY = _env("CORESIGNAL_API_KEY")
    CORESIGNAL_MONTHLY_CAP = _env_int("CORESIGNAL_MONTHLY_CAP", 200)

    # ── managed scraping (PAID — admin only) ──────────────────────────────
    APIFY_TOKEN = _env("APIFY_TOKEN")
    # comma-separated Apify actor ids for Israeli boards (alljobs/drushim/etc.)
    APIFY_ACTORS = _env("APIFY_ACTORS")
    APIFY_MONTHLY_CAP = _env_int("APIFY_MONTHLY_CAP", 1000)

    JOBSPY_SITES = _env("JOBSPY_SITES", "linkedin,indeed,glassdoor")
    JOBSPY_ENABLED = _env_bool("JOBSPY_ENABLED", True)     # still needs python-jobspy installed

    # ── big-tech (FREE — public internal endpoints) ───────────────────────
    BIGTECH_ENABLED = _env_bool("BIGTECH_ENABLED", True)
    # Meta's GraphQL doc_id rotates; expose it as config so it can be refreshed
    # without a code change.
    META_GRAPHQL_DOC_ID = _env("META_GRAPHQL_DOC_ID")

    # ── general HTTP ──────────────────────────────────────────────────────
    HTTP_TIMEOUT = _env_int("INGEST_HTTP_TIMEOUT", 15)
    HTTP_MAX_RETRIES = _env_int("INGEST_HTTP_RETRIES", 2)
    # where the credit ledger is persisted (best-effort; in-memory if unwritable)
    CREDIT_LEDGER_PATH = _env("INGEST_CREDIT_LEDGER", "/tmp/ingest_credits.json")


config = Config()
