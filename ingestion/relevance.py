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

_REMOTE_TOKENS = ("remote", "anywhere", "work from home", "wfh", "hybrid", "מרחוק")

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


def passes_location(location: str, user_locations: list[str],
                    remote_ok: bool = True) -> bool:
    if not user_locations:
        return True
    loc = (location or "").strip().lower()
    if not loc:
        return True                      # unknown location → let the AI decide
    if remote_ok and any(t in loc for t in _REMOTE_TOKENS):
        return True
    if _user_is_israel(user_locations) and any(t in loc for t in _IL_TOKENS):
        return True
    # token overlap with the user's stated locations
    toks: set[str] = set()
    for ul in user_locations:
        for w in (ul or "").lower().replace(",", " ").split():
            if len(w) >= 3:
                toks.add(w)
    return any(w in loc for w in toks)


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
           user_keywords: list[str], remote_ok: bool = True) -> bool:
    """Both gates must pass for an external job to be kept."""
    return (passes_location(job_location, user_locations, remote_ok)
            and passes_title(job_title, user_titles, user_keywords))
