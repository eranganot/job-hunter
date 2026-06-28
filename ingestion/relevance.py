"""
Relevance gate for externally-sourced jobs.

The Big-Tech endpoints (and some aggregators) are GLOBAL and do not honor the
user's location / title preferences server-side, so without a gate they flood the
candidate pool with worldwide, off-target roles — which then dilute the user's
preference-aligned results even after AI scoring.

This module applies a lightweight geography + title sanity check so external jobs
respect the user's settings BEFORE they reach the scorer. It is intentionally
permissive (it drops clearly-wrong geography/role, not borderline cases — the AI
scorer still makes the fine-grained call).

ON by default. Set INGEST_RELEVANCE_GATE=0 to disable and fall back to pure AI
scoring (the original "no pre-filter" behavior).
"""
from __future__ import annotations

import os

from .models import normalize_title

try:
    from rapidfuzz import fuzz as _f

    def _ratio(a: str, b: str) -> float:
        return float(_f.token_set_ratio(a, b))
except Exception:  # pragma: no cover
    from difflib import SequenceMatcher

    def _ratio(a: str, b: str) -> float:
        return SequenceMatcher(None, a, b).ratio() * 100.0


# Israeli locations we treat as "in Israel" so a user targeting Tel Aviv still
# matches a Herzliya / Ramat Gan / remote-Israel posting.
_IL_TOKENS = {
    "israel", "ישראל", "tel aviv", "tel-aviv", "telaviv", "tlv", "תל אביב",
    "herzliya", "herzeliya", "ramat gan", "petah tikva", "petach tikva",
    "haifa", "jerusalem", "netanya", "raanana", "ra'anana", "rehovot",
    "beer sheva", "be'er sheva", "yokneam", "caesarea", "kiryat", "or yehuda",
    "hod hasharon", "givatayim", "modiin", "modi'in", "ness ziona",
}

# Remote-ANYWHERE indicators (NOT "hybrid" — hybrid implies a physical office).
_REMOTE_ANYWHERE = ("remote", "anywhere", "work from home", "work-from-home", "wfh", "מרחוק")

# Work-mode words that must NOT be read as geography. Without this, a user who
# put "Hybrid" in their preferences would match a "Hybrid - London" posting.
_WORKMODE_WORDS = {"hybrid", "remote", "onsite", "on-site", "office", "wfh",
                   "anywhere", "flexible", "relocation", "fulltime", "parttime"}

# generic title words that should NOT count as a meaningful role-noun overlap
_GENERIC = {
    "senior", "junior", "lead", "head", "of", "the", "and", "vp", "director",
    "manager", "mgr", "sr", "jr", "staff", "principal", "chief", "officer",
    "specialist", "associate", "executive", "global", "team", "group", "ii",
    "iii", "i", "iv",
}


def gate_enabled() -> bool:
    return (os.environ.get("INGEST_RELEVANCE_GATE", "1").strip().lower()
            not in ("0", "false", "no", "off"))


def _user_is_israel(user_locations: list[str]) -> bool:
    for ul in user_locations:
        u = (ul or "").strip().lower()
        if not u:
            continue
        if "israel" in u or any(tok in u for tok in _IL_TOKENS):
            return True
    return False


def _accepts_remote(user_locations: list[str]) -> bool:
    """True only if the user explicitly wants remote-anywhere (NOT 'hybrid')."""
    for ul in user_locations:
        u = (ul or "").lower()
        if any(t in u for t in _REMOTE_ANYWHERE):
            return True
    return False


def passes_location(location: str, user_locations: list[str],
                    accept_remote: "bool | None" = None) -> bool:
    if not user_locations:
        return True
    loc = (location or "").strip().lower()
    if not loc:
        return True                      # unknown location → let the AI decide
    if accept_remote is None:
        accept_remote = _accepts_remote(user_locations)
    # Israel-aware geographic match (covers Tel Aviv / Herzliya / "..., Israel" / Hybrid-in-Israel)
    if _user_is_israel(user_locations) and any(t in loc for t in _IL_TOKENS):
        return True
    # user-listed PLACE tokens — work-mode words excluded so "hybrid" never
    # counts as a location (a "Hybrid - London" job must NOT match an IL user).
    toks: set[str] = set()
    for ul in user_locations:
        for w in (ul or "").lower().replace(",", " ").replace("-", " ").split():
            if len(w) >= 3 and w not in _WORKMODE_WORDS:
                toks.add(w)
    if any(w in loc for w in toks):
        return True
    # remote-anywhere only if the user actually opted into remote (not hybrid)
    if accept_remote and any(t in loc for t in _REMOTE_ANYWHERE):
        return True
    return False


def passes_title(title: str, user_titles: list[str],
                 user_keywords: list[str], threshold: float = 80.0) -> bool:
    if not user_titles and not user_keywords:
        return True
    nt = normalize_title(title)
    if not nt:
        return True
    nt_tokens = set(nt.split())

    for t in user_titles:
        tg = normalize_title(t)
        if not tg:
            continue
        if tg in nt or nt in tg:         # substring (e.g. "product manager" ⊂ "senior product manager")
            return True
        if _ratio(nt, tg) >= threshold:  # fuzzy whole-title
            return True

    # meaningful role-noun overlap (drops generic words like "senior"/"manager")
    key_tokens: set[str] = set()
    for t in user_titles:
        for w in normalize_title(t).split():
            if len(w) >= 4 and w not in _GENERIC:
                key_tokens.add(w)
    for k in user_keywords:
        for w in (k or "").lower().split():
            if len(w) >= 3:
                key_tokens.add(w)
    return bool(key_tokens & nt_tokens)


def passes(job_title: str, job_location: str,
           user_titles: list[str], user_locations: list[str],
           user_keywords: list[str], accept_remote: "bool | None" = None) -> bool:
    """Both gates must pass for an external job to be kept. ``accept_remote`` is
    derived from the user's own preferences when left as None (so a user who
    listed 'hybrid' but not 'remote' does NOT get remote-anywhere jobs)."""
    return (passes_location(job_location, user_locations, accept_remote)
            and passes_title(job_title, user_titles, user_keywords))
