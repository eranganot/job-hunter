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

Hard rules:
- Same people facts: every employer, job title, date, degree, school, number, percentage,
  team size, revenue figure, product name and tool must come from the CV text. Copy numbers
  exactly as written there. Never round, convert or estimate.
- Keep every role from the CV, in the CV's order (most recent first). You may drop weaker
  bullets of older roles, never a role.
- Use the job's wording only where the CV supports it (e.g. the CV says "A/B tests", the job
  says "experimentation": you may say "experimentation (A/B tests)").
- Skills: only skills, methods and tools that appear in the CV, most relevant to this job first.
- No first person. No buzzwords ("dynamic", "results-driven", "passionate", "synergy").
- The summary is 3-4 sentences aimed at this job.

Return ONLY JSON:
{{"headline": "a title line built from the CV's own titles",
  "summary": "...",
  "skills": ["..."],
  "experience": [{{"title": "...", "company": "...", "dates": "...", "bullets": ["..."]}}],
  "education": ["one line each, from the CV"],
  "other": ["certifications, languages, etc. from the CV"],
  "changes": ["3-6 short notes: what you put first for this job, and why"]}}

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


def verify(tailored: dict, cv_text: str) -> dict:
    """Remove what the CV does not support; return {"cv", "removed", "flags"}."""
    cv_norm = _norm(cv_text)
    cv_nums = _numbers(cv_text)
    removed, flags = [], []

    def numbers_ok(text):
        bad = sorted(_numbers(text) - cv_nums)
        return not bad, bad

    out = {"headline": str(tailored.get("headline") or "").strip(), "summary": "", "skills": [],
           "experience": [], "education": [], "other": [],
           "changes": [str(c).strip() for c in (tailored.get("changes") or []) if str(c).strip()][:6]}
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
            removed.append({"what": "role", "text": f"{title} at {company}", "why": "employer not in your CV"})
            continue
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
    return {"cv": out, "removed": removed, "flags": flags}


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
    return verify(raw, cv_text)


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
