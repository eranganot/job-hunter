""""Why this fits" is shown to the job seeker, so it says "you" (2026-11-12).

The scoring prompts described "the candidate" and never said who reads the
sentence, so 503 of 527 stored reasons (and 0 in the second person) read "The
candidate's experience ... Their AI/ML expertise ...". The prompts now carry
FIT_REASON_RULE; second_person() is the safety net at insert and the rewrite
for stored rows (migration 35)."""
import re

FIT_REASON_RULE = (
    "Write fit_reason TO the job seeker, in the second person: \"Your experience as Head of Product "
    "at X matches...\", \"You bring...\". Never write \"the candidate\", \"the applicant\", \"they\" or "
    "\"their\" for the person. Plain words: no scoring terms such as \"dimension\" or \"scored highest\"."
)

_IRREG = {"has": "have", "is": "are", "was": "were", "does": "do", "doesn't": "don't",
          "hasn't": "haven't", "isn't": "aren't", "wasn't": "weren't"}
_SUBJ = r"(?:the candidate|the applicant|this candidate)"


def _base_verb(v: str) -> str:
    lv = v.lower()
    if lv in _IRREG:
        return _IRREG[lv]
    for suf, rep in (("ies", "y"), ("sses", "ss"), ("shes", "sh"), ("ches", "ch"), ("xes", "x")):
        if lv.endswith(suf) and len(lv) > len(suf) + 1:
            return v[: -len(suf)] + rep
    if lv.endswith("s") and not lv.endswith("ss") and len(lv) > 3:
        return v[:-1]
    return v


def _cap(m_text: str, word: str) -> str:
    return word[0].upper() + word[1:] if m_text[:1].isupper() else word


def second_person(text: str) -> str:
    """Third person about the job seeker -> second person. Leaves text that
    never mentions "the candidate"/"the applicant" alone (so "their" about a
    company, in an unrelated sentence, is untouched)."""
    if not text or not re.search(_SUBJ + r"|candidate's|applicant's", text, re.I):
        return text or ""
    s = text
    s = re.sub(r"\b(?:the |this )?(candidate|applicant)'s\b", lambda m: _cap(m.group(0), "your"), s, flags=re.I)
    # "The candidate has/brings/is ..." -> "You have/bring/are ..."
    s = re.sub(r"\b" + _SUBJ + r"\s+(\w+(?:n't)?)\b",
               lambda m: _cap(m.group(0), "you") + " " + _base_verb(m.group(1))
               if re.fullmatch(r"[a-z]+(?:s|'t)?", m.group(1)) and m.group(1).lower() not in ("and", "with", "in", "for")
               and (m.group(1).lower().endswith("s") or m.group(1).lower() in _IRREG)
               else _cap(m.group(0), "you") + " " + m.group(1), s, flags=re.I)
    s = re.sub(r"\b" + _SUBJ + r"\b", lambda m: _cap(m.group(0), "you"), s, flags=re.I)
    s = re.sub(r"\btheir\b", lambda m: _cap(m.group(0), "your"), s, flags=re.I)
    s = re.sub(r"\bthemselves\b", lambda m: _cap(m.group(0), "yourself"), s, flags=re.I)
    s = re.sub(r"\bthem\b", lambda m: _cap(m.group(0), "you"), s, flags=re.I)
    s = re.sub(r"\bthey're\b", lambda m: _cap(m.group(0), "you're"), s, flags=re.I)
    s = re.sub(r"\bthey\b", lambda m: _cap(m.group(0), "you"), s, flags=re.I)
    s = re.sub(r"\bhis or her\b|\bhis/her\b", "your", s, flags=re.I)
    # "You bring X and has led Y" - a second verb in the same clause.
    _co = r"(has|is|was|possesses|brings|demonstrates|holds|lacks|offers|shows|specializes|leads|meets)"
    for _ in range(3):
        s2 = re.sub(r"(\b[Yy]ou [a-z']+\b[^.;:]{0,160}?\band )" + _co + r"\b",
                    lambda m: m.group(1) + _base_verb(m.group(2)), s)
        if s2 == s:
            break
        s = s2
    return s
