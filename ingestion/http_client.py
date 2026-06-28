"""
Shared HTTP client with proxy rotation, retries, and UA rotation.

Prefers ``httpx`` (clean proxy + timeout handling). If httpx isn't installed it
transparently falls back to stdlib ``urllib`` so the module never hard-fails —
matching the repo's existing urllib-based sourcing.
"""
from __future__ import annotations

import json
import random
import time
from typing import Any, Optional

from .config import config
from .proxies import ProxyManager

try:
    import httpx
    _HTTPX = True
    # httpx logs every request at INFO to stderr, which Railway flags as
    # red "error" lines — silence it (we already log our own summaries).
    import logging as _logging
    _logging.getLogger("httpx").setLevel(_logging.WARNING)
    _logging.getLogger("httpcore").setLevel(_logging.WARNING)
except Exception:
    _HTTPX = False

_USER_AGENTS = [
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
     "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
     "(KHTML, like Gecko) Version/17.4 Safari/605.1.15"),
    ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
     "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"),
]


class HttpResponse:
    __slots__ = ("status_code", "text", "_url")

    def __init__(self, status_code: int, text: str, url: str = "") -> None:
        self.status_code = status_code
        self.text = text
        self._url = url

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> Any:
        return json.loads(self.text) if self.text else None


class HttpClient:
    """A small, dependency-tolerant HTTP wrapper used by every adapter."""

    def __init__(self, proxy_manager: Optional[ProxyManager] = None,
                 timeout: Optional[int] = None,
                 max_retries: Optional[int] = None,
                 use_proxy: bool = False) -> None:
        self.proxies = proxy_manager
        self.timeout = timeout or config.HTTP_TIMEOUT
        self.max_retries = config.HTTP_MAX_RETRIES if max_retries is None else max_retries
        self.use_proxy = use_proxy and (proxy_manager is not None and proxy_manager.active)

    # -- public -------------------------------------------------------------

    def get(self, url: str, headers: dict | None = None,
            params: dict | None = None) -> HttpResponse:
        return self._request("GET", url, headers=headers, params=params)

    def post(self, url: str, headers: dict | None = None,
             json_body: Any = None, data: Any = None) -> HttpResponse:
        return self._request("POST", url, headers=headers, json_body=json_body, data=data)

    def get_json(self, url: str, **kw) -> Any:
        r = self.get(url, **kw)
        return r.json() if r.ok else None

    def post_json(self, url: str, **kw) -> Any:
        r = self.post(url, **kw)
        return r.json() if r.ok else None

    # -- internals ----------------------------------------------------------

    def _headers(self, extra: dict | None) -> dict:
        h = {
            "User-Agent": random.choice(_USER_AGENTS),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9,he;q=0.8",
        }
        if extra:
            h.update(extra)
        return h

    def _request(self, method: str, url: str, headers=None, params=None,
                 json_body=None, data=None) -> HttpResponse:
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            proxy = self.proxies.get() if (self.use_proxy and self.proxies) else None
            try:
                if _HTTPX:
                    resp = self._httpx_call(method, url, headers, params,
                                            json_body, data, proxy)
                else:
                    resp = self._urllib_call(method, url, headers, params,
                                             json_body, data, proxy)
                if resp.status_code in (429, 403, 503) and attempt < self.max_retries:
                    if self.proxies:
                        self.proxies.mark_bad(proxy)
                    time.sleep(0.6 * (attempt + 1) + random.random())
                    continue
                return resp
            except Exception as e:           # network error → rotate + retry
                last_exc = e
                if self.proxies:
                    self.proxies.mark_bad(proxy)
                if attempt < self.max_retries:
                    time.sleep(0.5 * (attempt + 1) + random.random())
                    continue
        raise last_exc if last_exc else RuntimeError("request failed")

    def _httpx_call(self, method, url, headers, params, json_body, data, proxy):
        kwargs: dict[str, Any] = {"timeout": self.timeout, "follow_redirects": True}
        if proxy:
            # httpx >=0.26 uses `proxy=`; older uses `proxies=`. Try both.
            try:
                client = httpx.Client(proxy=proxy, **kwargs)
            except TypeError:
                client = httpx.Client(proxies=proxy, **kwargs)
        else:
            client = httpx.Client(**kwargs)
        try:
            r = client.request(method, url, headers=self._headers(headers),
                               params=params, json=json_body, data=data)
            return HttpResponse(r.status_code, r.text, str(r.url))
        finally:
            client.close()

    def _urllib_call(self, method, url, headers, params, json_body, data, proxy):
        import urllib.request as _ur
        import urllib.parse as _up
        if params:
            sep = "&" if ("?" in url) else "?"
            url = url + sep + _up.urlencode(params)
        body = None
        hdrs = self._headers(headers)
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        elif data is not None:
            body = _up.urlencode(data).encode("utf-8")
            hdrs["Content-Type"] = "application/x-www-form-urlencoded"
        req = _ur.Request(url, data=body, headers=hdrs, method=method)
        opener = _ur.build_opener()
        if proxy:
            opener.add_handler(_ur.ProxyHandler({"http": proxy, "https": proxy}))
        try:
            with opener.open(req, timeout=self.timeout) as resp:
                text = resp.read().decode("utf-8", errors="replace")
                return HttpResponse(resp.status, text, url)
        except Exception as e:
            code = getattr(e, "code", 0) or 0
            text = ""
            try:
                text = e.read().decode("utf-8", errors="replace")  # type: ignore
            except Exception:
                pass
            if code:
                return HttpResponse(code, text, url)
            raise
