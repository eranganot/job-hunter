"""
Rotating residential proxy management.

Supports three configuration styles (checked in order):

  1. PROXY_POOL          — comma-separated list of full proxy URLs, round-robined
  2. PROXY_USERNAME/...  — Bright-Data / Oxylabs style creds; we synthesize a URL
                           and (optionally) inject a per-request sticky session id
  3. RESIDENTIAL_PROXY_URL — a single gateway URL

If none are set, ``get()`` returns ``None`` and callers connect directly. A simple
failure cooldown takes a bad exit IP out of rotation for a while so one banned IP
doesn't sink the whole run.
"""
from __future__ import annotations

import time
import random
import threading

from .config import config


class ProxyManager:
    def __init__(self, enabled: bool = True) -> None:
        self._lock = threading.Lock()
        self._rr = 0
        self._cooldown: dict[str, float] = {}     # proxy_url -> retry_after epoch
        self._enabled = enabled and config.PROXY_ENABLED
        self._pool: list[str] = []
        if config.PROXY_POOL:
            self._pool = [p.strip() for p in config.PROXY_POOL.split(",") if p.strip()]
        elif config.PROXY_URL:
            self._pool = [config.PROXY_URL]
        self._has_credentials = bool(
            config.PROXY_USERNAME and config.PROXY_PASSWORD
        )

    @property
    def active(self) -> bool:
        """True if we have *any* proxy to hand out."""
        return self._enabled and (bool(self._pool) or self._has_credentials)

    def get(self) -> str | None:
        """Return a proxy URL (or None for a direct connection)."""
        if not self.active:
            return None
        with self._lock:
            now = time.time()
            # credential mode: synthesize a (sticky) session per call
            if self._has_credentials and not self._pool:
                user = config.PROXY_USERNAME
                if config.PROXY_STICKY_SESSIONS:
                    sess = random.randint(10_000_000, 99_999_999)
                    user = f"{user}-session-{sess}"
                return (f"http://{user}:{config.PROXY_PASSWORD}"
                        f"@{config.PROXY_HOST}:{config.PROXY_PORT}")
            # pool mode: round-robin, skipping cooled-down entries
            for _ in range(len(self._pool)):
                p = self._pool[self._rr % len(self._pool)]
                self._rr += 1
                if self._cooldown.get(p, 0) <= now:
                    return p
            # everything cooling down — return the soonest-available anyway
            return self._pool[self._rr % len(self._pool)]

    def mark_bad(self, proxy: str | None, cooldown_s: int = 120) -> None:
        if not proxy:
            return
        with self._lock:
            self._cooldown[proxy] = time.time() + cooldown_s

    def as_httpx_proxies(self, proxy: str | None) -> dict | None:
        if not proxy:
            return None
        return {"http://": proxy, "https://": proxy}
