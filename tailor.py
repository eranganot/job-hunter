"""Job-tailored CV (Step 14, Eran 2026-11-05).

One model call rewrites the user's CV for one job description: the same roles,
employers, dates, education and facts, re-ordered and re-worded so what this
job asks for comes first. It may not add anything. Because a CV with one
invented number is worse than no CV, the model's output is then CHECKED
against the original text, deterministically:

  - a bullet or summary sentence with a number, percentage or year that is not
    in the CV is removed;
  - a role whose employer is not in the CV is removed;
  - a skill that does not appear in the CV is removed;
  - a job title whose words are not in the CV is kept but flagged for the user.

Everything removed or flagged is returned with the result, so the user sees
what the model tried and the check refused. The contact header comes from the
account and the profile, never from the model.
"""
import io
import json
import re

import gemini

_STOP = {"and", "the", "for", "of", "at", "in", "to", "a", "an", "&", "with", "on", "ltd", "inc",
         "llc", "co", "corp", "group", "senior", "sr", "jr", "-", "–", "—"}

_PROMPT = """You rewrite a CV for ONE job. You may re-order, re-word, shorten, and choose
emphasis. You may NOT add anything that is not in the CV.

First read the job description and pick its 4-6 most important requirements. Then:

- match: for each requirement, the CV's strongest evidence for it, quoted or closely
  paraphrased from the CV (a role, a result, a skill). If the CV has nothing for it, set
  evidence to "" - an honest gap is useful to the candidate; never stretch.
- headline: one line built from the CV's own titles and domains, pointed at this job
  (e.g. CV titles "Head of Product" and "Senior PM", job "Director of Product, Payments":
  "Product leader - payments and B2B platforms" only if the CV shows payments and B2B).
- summary: 3-4 sentences WRITTEN FOR THIS JOB - not the CV's own summary re-used. Open
  with the experience that answers the job's top requirement, then the next one or two,
  each backed by a concrete fact from the CV. Do not copy sentences from the CV.
- experience: inside each role, put first the bullets that prove this job's requirements,
  and start each bullet with what was achieved. Use the job's words where the CV supports
  them (the CV says "A/B tests", the job says "experimentation": "experimentation (A/B
  tests)"). Older, less relevant roles may be cut to their 1-3 strongest bullets.
- skills: only skills, methods and tools that appear in the CV, this job's first.

Hard rules:
- Same facts: every employer, job title, date, degree, school, number, percentage, team
  size, revenue figure, product name and tool must come from the CV text. Copy numbers
  exactly as written there. Never round, convert or estimate.
- Keep every role from the CV, in the CV's order (most recent first). Never drop a role.
- No first person. No buzzwords ("dynamic", "results-driven", "passionate", "synergy").

Return ONLY JSON:
{{"match": [{{"need": "a requirement of the job, in a few words", "evidence": "from the CV, or empty"}}],
  "headline": "...",
  "summary": "...",
  "skills": ["..."],
  "experience": [{{"title": "...", "company": "...", "dates": "...", "bullets": ["..."]}}],
  "education": ["one line each, from the CV"],
  "other": ["certifications, languages, etc. from the CV"]}}

JOB: {title} at {company}
JOB DESCRIPTION:
{jd}

CV:
{cv}"""


def _norm(s: str) -> str:
    s = (s or "").lower().replace("–", "-").replace("—", "-").replace("’", "'")
    return re.sub(r"\s+", " ", s)


_NUM = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")


def _numbers(text: str) -> set:
    """Numbers as written, without thousands separators: "1,200" -> "1200"."""
    return {m.group(0).replace(",", "").rstrip(".") for m in _NUM.finditer(text or "")}


def _words(text: str) -> list:
    return [w for w in re.findall(r"[a-z0-9+#./']+", _norm(text)) if w not in _STOP and len(w) > 1]


def _in_cv(phrase: str, cv_norm: str) -> bool:
    """Every significant word of `phrase` appears in the CV."""
    ws = _words(phrase)
    return bool(ws) and all(w in cv_norm for w in ws)



# ── Roles read from the CV itself, without the model (Eran, 2026-11-08: the
# tailored CV left out DAZN, his current job - "DAZN is my current job and
# cannot be removed"). Whatever the model does, every role found here must be
# in the result: missing ones get one retry naming them, then are put back
# from the CV as written.
_DATES = re.compile(r"((?:19|20)\d{2})\s*[-\u2013\u2014to]+\s*((?:19|20)\d{2}|present|current|now|today)", re.I)
_SECTION_END = re.compile(r"^\s*(education|military|skills|certifications?|languages|projects|publications|"
                          r"volunteer\w*|awards|interests|references|core competencies)\b", re.I)
_SECTION_START = re.compile(r"^\s*(experience|professional experience|work experience|employment|work history|career)\b", re.I)
_BULLET = re.compile(r"^\s*[\u25cf\u2022\u25aa\u25e6*\-]\s+")


def cv_roles(cv_text: str) -> list:
    """[{company, title, dates, bullets, key}] in CV order. key: the employer's
    first word, used to find the role in the model's output.

    A role starts at a non-bullet line ending in a date range ("Head of Product
    2025-Present"). Its employer is on that line ("Senior PM - Wix (2016 -
    2021)") or the line before ("DAZN TLV, Israel"). Lines after it are its
    description and bullets; a line continues the previous one unless that one
    ended a sentence. Reading stops at Education, Skills and similar headings,
    which use the same date pattern."""
    raw = [l.strip() for l in (cv_text or "").splitlines()]
    lines = [l for l in raw if l]
    start = next((i + 1 for i, l in enumerate(lines) if _SECTION_START.match(l) and not _DATES.search(l)), 0)
    end = next((i for i in range(start, len(lines)) if _SECTION_END.match(lines[i]) and not _DATES.search(lines[i])),
               len(lines))
    is_role = [i for i in range(start, end) if _DATES.search(lines[i]) and not _BULLET.match(lines[i])]
    roles, employer_lines = [], set()
    for i in is_role:
        line = lines[i]
        m = _DATES.search(line)
        head = line[:m.start()].strip(" \t|,(-\u2013")
        parts = [x.strip() for x in re.split(r"\s+[-\u2013|@]\s+|\s+at\s+", head, maxsplit=1) if x.strip()]
        if len(parts) == 2:
            title, company = parts
        else:
            title, company = head, ""
            if i - 1 >= start and not _BULLET.match(lines[i - 1]) and not _DATES.search(lines[i - 1]):
                company = lines[i - 1]
                employer_lines.add(i - 1)
        roles.append({"company": company, "title": title, "dates": m.group(0), "bullets": [],
                      "key": (_words(company) or _words(title) or [""])[0], "_at": i})
    for n, r in enumerate(roles):
        stop = roles[n + 1]["_at"] if n + 1 < len(roles) else end
        prev_end = True
        for i in range(r["_at"] + 1, stop):
            if i in employer_lines:
                continue
            line = lines[i]
            if _BULLET.match(line):
                r["bullets"].append(_BULLET.sub("", line))
            elif r["bullets"] and not prev_end:
                r["bullets"][-1] += " " + line            # a wrapped line
            else:
                r["bullets"].append(line)
            prev_end = line.rstrip().endswith((".", "!", "?", ":"))
    for r in roles:
        r.pop("_at")
    return [r for r in roles if r["key"]]


def _covers(role_out: dict, key: str) -> bool:
    return key in _words(" ".join([role_out.get("company") or "", role_out.get("title") or ""]))


def missing_roles(tailored: dict, roles: list) -> list:
    out = tailored.get("experience") or []
    return [r for r in roles if not any(_covers(o, r["key"]) for o in out)]


def restore_roles(result: dict, roles: list) -> dict:
    """Put back, from the CV as written and in CV order, any role still missing."""
    exp = result["cv"]["experience"]
    for r in missing_roles(result["cv"], roles):
        pos = len(exp)
        later = [x["key"] for x in roles[roles.index(r) + 1:]]
        for k, o in enumerate(exp):
            if any(_covers(o, key) for key in later):
                pos = k
                break
        exp.insert(pos, {"title": r["title"], "company": r["company"], "dates": r["dates"],
                         "bullets": list(r["bullets"])})
        result["flags"].append(f"{r['company'] or r['title']} was left out by the model; it is back, as your CV has it.")
    return result


def _grams(text: str) -> list:
    w = re.findall(r"[a-z0-9+#']+", _norm(text))
    return [" ".join(w[i:i + 3]) for i in range(len(w) - 2)]


def copied_share(text: str, cv_text: str) -> float:
    """How much of `text` is the CV word for word: the share of its three-word
    runs that also occur in the CV. 1.0 = copied, ~0.2 = rewritten in the CV's
    vocabulary."""
    g = _grams(text)
    if not g:
        return 0.0
    cv = set(_grams(cv_text))
    return sum(1 for x in g if x in cv) / len(g)


COPIED = 0.6          # at or above: the summary is the CV's own, not written for the job


def _backed(evidence: str, cv_norm: str) -> bool:
    """Most of the evidence's significant words are in the CV (a close
    paraphrase passes; an invented claim does not)."""
    ws = _words(evidence)
    return bool(ws) and sum(1 for w in ws if w in cv_norm) / len(ws) >= 0.75


def verify_match(match, cv_text: str) -> list:
    """[{need, evidence}] with evidence the CV does not back, or with a number
    the CV does not have, turned into a gap ("") - never shown as a strength."""
    cv_norm, cv_nums = _norm(cv_text), _numbers(cv_text)
    out = []
    for m in (match or [])[:6]:
        if not isinstance(m, dict) or not str(m.get("need") or "").strip():
            continue
        ev = str(m.get("evidence") or "").strip()
        if ev and (not _backed(ev, cv_norm) or _numbers(ev) - cv_nums):
            ev = ""
        out.append({"need": str(m["need"]).strip()[:120], "evidence": ev[:300]})
    return out


def describe_changes(cv: dict, cv_text: str, roles: list) -> list:
    """What actually differs from the CV, measured - not the model's account of
    what it did (which could claim a change it never made)."""
    out = []
    if cv.get("summary"):
        out.append("Summary written for this job." if copied_share(cv["summary"], cv_text) < COPIED
                   else "Summary is close to your CV's own wording.")
    if cv.get("skills"):
        out.append("Skills led by: " + ", ".join(cv["skills"][:3]) + ".")
    for r in roles:
        o = next((x for x in cv.get("experience") or [] if _covers(x, r["key"])), None)
        if not o or not r["bullets"]:
            continue
        orig = [_norm(b) for b in r["bullets"]]
        new = o.get("bullets") or []
        reworded = sum(1 for b in new if copied_share(b, cv_text) < COPIED)
        first = new[0] if new else ""
        first_moved = bool(first) and copied_share(first, r["bullets"][0]) < COPIED and \
            any(copied_share(first, b) >= COPIED for b in r["bullets"][1:])
        bits = []
        if first_moved:
            bits.append("a later point moved to the top")
        if reworded:
            bits.append("%d of %d point%s reworded" % (reworded, len(new), "" if len(new) == 1 else "s"))
        if len(new) < len(orig):
            bits.append("%d less relevant point%s left out" % (len(orig) - len(new), "" if len(orig) - len(new) == 1 else "s"))
        if bits:
            out.append("%s: %s." % (r["company"] or r["title"], ", ".join(bits)))
    return out


def verify(tailored: dict, cv_text: str) -> dict:
    """Remove what the CV does not support; return {"cv", "removed", "flags"}."""
    cv_norm = _norm(cv_text)
    cv_nums = _numbers(cv_text)
    removed, flags = [], []

    def numbers_ok(text):
        bad = sorted(_numbers(text) - cv_nums)
        return not bad, bad

    out = {"headline": str(tailored.get("headline") or "").strip(), "summary": "", "skills": [],
           "experience": [], "education": [], "other": [], "changes": []}
    ok, bad = numbers_ok(out["headline"])
    if not ok:
        removed.append({"what": "headline", "text": out["headline"], "why": "numbers not in your CV: " + ", ".join(bad)})
        out["headline"] = ""

    kept = []
    for sent in re.split(r"(?<=[.!?])\s+", str(tailored.get("summary") or "").strip()):
        if not sent:
            continue
        ok, bad = numbers_ok(sent)
        if ok:
            kept.append(sent)
        else:
            removed.append({"what": "summary", "text": sent, "why": "numbers not in your CV: " + ", ".join(bad)})
    out["summary"] = " ".join(kept)

    for sk in tailored.get("skills") or []:
        sk = str(sk).strip()
        if not sk:
            continue
        if _norm(sk) in cv_norm or _in_cv(sk, cv_norm):
            out["skills"].append(sk)
        else:
            removed.append({"what": "skill", "text": sk, "why": "not in your CV"})

    for role in tailored.get("experience") or []:
        if not isinstance(role, dict):
            continue
        company = str(role.get("company") or "").strip()
        title = str(role.get("title") or "").strip()
        dates = str(role.get("dates") or "").strip()
        if company and not _in_cv(company, cv_norm):
            # "DAZN - sports streaming": the employer is real, the extra words
            # are the model's. Keep the role under the CV's own employer line.
            match = next((r for r in cv_roles(cv_text) if r["key"] in _words(company)), None)
            if match is None:
                removed.append({"what": "role", "text": f"{title} at {company}", "why": "employer not in your CV"})
                continue
            company = match["company"]
        ok, bad = numbers_ok(dates)
        if not ok:
            flags.append(f"Dates for {company or title} ({dates}) don't match your CV - left out.")
            dates = ""
        if title and not _in_cv(title, cv_norm):
            flags.append(f'Check the title "{title}" at {company}: its wording isn\'t in your CV.')
        bullets = []
        for b in role.get("bullets") or []:
            b = str(b).strip()
            if not b:
                continue
            ok, bad = numbers_ok(b)
            if ok:
                bullets.append(b)
            else:
                removed.append({"what": "bullet", "text": b, "why": "numbers not in your CV: " + ", ".join(bad)})
        out["experience"].append({"title": title, "company": company, "dates": dates, "bullets": bullets})

    for key in ("education", "other"):
        for line in tailored.get(key) or []:
            line = str(line).strip()
            if not line:
                continue
            ok, bad = numbers_ok(line)
            if ok:
                out[key].append(line)
            else:
                removed.append({"what": key, "text": line, "why": "numbers not in your CV: " + ", ".join(bad)})
    return {"cv": out, "removed": removed, "flags": flags, "match": verify_match(tailored.get("match"), cv_text)}


def _parse(text: str) -> dict:
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    m = re.search(r"\{.*\}", t, re.S)
    return json.loads(m.group(0) if m else t)


def tailor(cv_text: str, job: dict, *, user_id=None, key: str = "", llm=None) -> dict:
    """Rewrite, then verify. llm(prompt) -> parsed dict is for tests; production
    calls Gemini. Raises RuntimeError with a readable reason on failure."""
    if not (cv_text or "").strip():
        raise RuntimeError("No CV text to work from - upload your CV first.")
    jd = (job.get("full_description") or job.get("description") or "").strip()
    if len(jd) < 80:
        raise RuntimeError("This job has no description to tailor to.")
    prompt = _PROMPT.format(title=job.get("title") or "", company=job.get("company") or "",
                            jd=jd[:6000], cv=cv_text[:12000])
    if llm is not None:
        raw = llm(prompt)
    else:
        body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.3, "maxOutputTokens": 6000,
                                     "responseMimeType": "application/json",
                                     "thinkingConfig": {"thinkingBudget": 0}}}
        try:
            raw = _parse(gemini.generate_text(body, purpose="tailored_cv", user_id=user_id, key=key, timeout=90))
        except (ValueError, KeyError, IndexError) as exc:
            raise RuntimeError(f"The model's answer could not be read ({type(exc).__name__}). Try again.")
    if not isinstance(raw, dict) or not raw.get("experience"):
        raise RuntimeError("The model returned no experience section. Try again.")
    roles = cv_roles(cv_text)
    res = verify(raw, cv_text)
    # One retry, naming what was wrong: a role left out (Eran, 2026-11-08: DAZN),
    # or a summary that is the CV's own text rather than written for the job
    # (2026-11-09: the headline and summary came back nearly verbatim).
    problems = []
    gone = missing_roles(res["cv"], roles)
    if gone:
        names = "; ".join(f"{r['title']} at {r['company']} ({r['dates']})" for r in gone)
        problems.append("It left out these roles from the CV: " + names +
                        ". Every role must be included, in the CV's order.")
    if res["cv"]["summary"] and copied_share(res["cv"]["summary"], cv_text) >= COPIED:
        problems.append("Its summary copies the CV's own sentences. Write the summary for this job: "
                        "open with the experience that answers its top requirement.")
    if problems:
        retry = prompt + "\n\nYour previous answer had problems. " + " ".join(problems) + " Answer again in full."
        try:
            raw2 = llm(retry) if llm is not None else _parse(gemini.generate_text(
                {**body, "contents": [{"role": "user", "parts": [{"text": retry}]}]},
                purpose="tailored_cv", user_id=user_id, key=key, timeout=90))
            if isinstance(raw2, dict) and raw2.get("experience"):
                res = verify(raw2, cv_text)
        except (ValueError, KeyError, IndexError) as exc:
            print(f"[tailor] retry failed ({type(exc).__name__}); keeping the first answer")
    res = restore_roles(res, roles)
    res["cv"]["changes"] = describe_changes(res["cv"], cv_text, roles)
    return res


def to_docx(cv: dict, *, name: str, contact: list) -> bytes:
    """An editable Word file: header from the account, body from the checked CV."""
    from docx import Document
    from docx.shared import Pt

    doc = Document()
    st = doc.styles["Normal"]
    st.font.name = "Calibri"
    st.font.size = Pt(10.5)
    h = doc.add_paragraph()
    r = h.add_run(name or "")
    r.bold = True
    r.font.size = Pt(18)
    line = " | ".join(c for c in contact if c)
    if line:
        doc.add_paragraph(line)
    if cv.get("headline"):
        p = doc.add_paragraph()
        p.add_run(cv["headline"]).bold = True

    def section(title):
        p = doc.add_paragraph()
        rr = p.add_run(title.upper())
        rr.bold = True
        rr.font.size = Pt(11.5)

    if cv.get("summary"):
        section("Summary")
        doc.add_paragraph(cv["summary"])
    if cv.get("skills"):
        section("Skills")
        doc.add_paragraph(" · ".join(cv["skills"]))
    if cv.get("experience"):
        section("Experience")
        for role in cv["experience"]:
            p = doc.add_paragraph()
            p.add_run(" - ".join(x for x in (role.get("title"), role.get("company")) if x)).bold = True
            if role.get("dates"):
                p.add_run("    " + role["dates"])
            for b in role.get("bullets") or []:
                doc.add_paragraph(b, style="List Bullet")
    for key, title in (("education", "Education"), ("other", "Additional")):
        if cv.get(key):
            section(title)
            for line in cv[key]:
                doc.add_paragraph(line)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
