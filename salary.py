"""Reading a salary out of a job's text (Step 13, 2026-11-07).

No job source gives a salary field; when a posting states one it is in the
description ("Salary $200,000 - $250,000 USD", "₪30,000-35,000 per month",
"120k-140k EUR"). parse() returns the range normalised to a MONTHLY amount in
the stated currency, or None when the text names no salary. Conservative on
purpose: a number without a currency, or one that cannot be a salary, is not
read - a wrong salary would demote a job unfairly.
"""
from __future__ import annotations

import re

_CUR = {"$": "USD", "usd": "USD", "us$": "USD", "€": "EUR", "eur": "EUR", "£": "GBP", "gbp": "GBP",
        "₪": "ILS", "ils": "ILS", "nis": "ILS", "shekel": "ILS", "shekels": "ILS"}
_CUR_RX = r"(?:US\$|\$|€|£|₪|USD|EUR|GBP|ILS|NIS|shekels?)"
_NUM = r"(\d{1,3}(?:[,.\s]\d{3})+|\d+(?:\.\d+)?)\s*([kK])?"
_RANGE = re.compile(
    r"(?P<c1>" + _CUR_RX + r")?\s*" + _NUM.replace("(", "(?P<a>", 1).replace("([kK])", "(?P<ak>[kK])", 1)
    + r"\s*(?:-|–|—|to)\s*(?P<c2>" + _CUR_RX + r")?\s*"
    + _NUM.replace("(", "(?P<b>", 1).replace("([kK])", "(?P<bk>[kK])", 1)
    + r"\s*(?P<c3>" + _CUR_RX + r")?", re.I)
_SINGLE = re.compile(r"(?P<c1>" + _CUR_RX + r")\s*" + _NUM.replace("(", "(?P<a>", 1).replace("([kK])", "(?P<ak>[kK])", 1)
                     + r"|" + _NUM.replace("(", "(?P<a2>", 1).replace("([kK])", "(?P<ak2>[kK])", 1)
                     + r"\s*(?P<c3>" + _CUR_RX + r")", re.I)
_MONTH = re.compile(r"per\s+month|/\s*mo(?:nth)?\b|monthly|a\s+month|לחודש", re.I)
_YEAR = re.compile(r"per\s+(?:year|annum)|/\s*y(?:ea)?r\b|annual|a\s+year|yearly|p\.a\.", re.I)
_CONTEXT = re.compile(r"salary|compensation|pay|base|ote|range|\bwage|שכר", re.I)


def _num(s: str, k: str | None) -> float:
    s = s.strip()
    if re.fullmatch(r"\d{1,3}(?:[,.\s]\d{3})+", s):
        s = re.sub(r"[,.\s]", "", s)
    v = float(s)
    return v * 1000 if k else v


def _currency(*tokens) -> str | None:
    for t in tokens:
        if t:
            return _CUR.get(t.lower().rstrip("s") if t.lower().startswith("shekel") else t.lower(), None) \
                or _CUR.get(t.lower())
    return None


def parse(text: str) -> dict | None:
    """{'currency', 'monthly_min', 'monthly_max', 'period'} or None."""
    if not text:
        return None
    for rx, is_range in ((_RANGE, True), (_SINGLE, False)):
        for m in rx.finditer(text):
            g = m.groupdict()
            cur = _currency(g.get("c1"), g.get("c2"), g.get("c3"))
            if not cur:
                continue
            if is_range:
                lo, hi = _num(g["a"], g.get("ak") or g.get("bk")), _num(g["b"], g.get("bk"))
            else:
                v = _num(g.get("a") or g.get("a2"), g.get("ak") or g.get("ak2"))
                lo = hi = v
            if lo > hi:
                lo, hi = hi, lo
            window = text[max(0, m.start() - 80): m.end() + 40]
            if not _CONTEXT.search(window) and not is_range:
                continue                       # "$5M funding" is not a salary
            if _MONTH.search(window):
                period = "month"
            elif _YEAR.search(window):
                period = "year"
            else:
                # Unstated: amounts this large are annual; Israeli salaries are quoted monthly.
                period = "year" if hi >= 120_000 or (cur != "ILS" and hi >= 20_000) else "month"
            div = 12.0 if period == "year" else 1.0
            mlo, mhi = lo / div, hi / div
            if not (300 <= mhi <= 200_000):
                continue                       # not a plausible monthly salary
            return {"currency": cur, "monthly_min": round(mlo), "monthly_max": round(mhi), "period": period}
    return None
