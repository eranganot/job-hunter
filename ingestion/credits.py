"""
API credit usage monitoring + per-provider availability (circuit breaker).

Two responsibilities the brief calls for:

  * "credit usage monitoring" — track how many calls/credits each paid provider
    has spent this month against a configurable cap, and refuse to spend past it.
  * "fallback logic if an API becomes unavailable" — a lightweight circuit
    breaker: after N consecutive failures a provider is marked unavailable for a
    cooldown window, so the pipeline skips it and relies on the other sources.

The ledger is persisted to a JSON file best-effort (survives restarts on a
persistent volume); if the path isn't writable it degrades to in-memory only.
"""
from __future__ import annotations

import json
import time
import threading
from datetime import datetime

from .config import config


class CreditManager:
    def __init__(self, ledger_path: str | None = None) -> None:
        self._lock = threading.Lock()
        self._path = ledger_path or config.CREDIT_LEDGER_PATH
        self._caps: dict[str, int] = {
            "adzuna": config.ADZUNA_MONTHLY_CAP,
            "theirstack": config.THEIRSTACK_MONTHLY_CAP,
            "coresignal": config.CORESIGNAL_MONTHLY_CAP,
            "apify": config.APIFY_MONTHLY_CAP,
        }
        self._spend: dict[str, int] = {}          # "provider:YYYY-MM" -> credits
        self._failures: dict[str, int] = {}       # provider -> consecutive fails
        self._unavailable_until: dict[str, float] = {}
        self._load()

    # ---- credit accounting ------------------------------------------------

    def _month_key(self, provider: str) -> str:
        return f"{provider}:{datetime.utcnow().strftime('%Y-%m')}"

    def remaining(self, provider: str) -> int:
        cap = self._caps.get(provider)
        if cap is None:
            return 1 << 30                        # uncapped
        with self._lock:
            used = self._spend.get(self._month_key(provider), 0)
        return max(0, cap - used)

    def can_spend(self, provider: str, n: int = 1) -> bool:
        return self.remaining(provider) >= n and self.is_available(provider)

    def record(self, provider: str, n: int = 1) -> None:
        with self._lock:
            k = self._month_key(provider)
            self._spend[k] = self._spend.get(k, 0) + n
            self._save_locked()

    # ---- circuit breaker --------------------------------------------------

    def is_available(self, provider: str) -> bool:
        with self._lock:
            until = self._unavailable_until.get(provider, 0)
            return until <= time.time()

    def report_success(self, provider: str) -> None:
        with self._lock:
            self._failures[provider] = 0
            self._unavailable_until.pop(provider, None)

    def report_failure(self, provider: str, threshold: int = 3,
                        cooldown_s: int = 600) -> None:
        with self._lock:
            self._failures[provider] = self._failures.get(provider, 0) + 1
            if self._failures[provider] >= threshold:
                self._unavailable_until[provider] = time.time() + cooldown_s

    # ---- snapshot for observability --------------------------------------

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "spend": dict(self._spend),
                "caps": dict(self._caps),
                "unavailable": {
                    p: round(t - time.time(), 1)
                    for p, t in self._unavailable_until.items()
                    if t > time.time()
                },
            }

    # ---- persistence (best effort) ---------------------------------------

    def _load(self) -> None:
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._spend = data.get("spend", {})
        except Exception:
            self._spend = {}

    def _save_locked(self) -> None:
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump({"spend": self._spend}, f)
        except Exception:
            pass            # in-memory only; non-fatal
