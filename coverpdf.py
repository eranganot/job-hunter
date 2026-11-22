"""
Cover letter -> PDF, and the short "note" version of it (2026-11-22).

The owner: the tool should write the cover letter, make it a PDF and upload it
as a file; where a form has only a text box (Comeet's "Personal note", Lever's
"Additional information"), the letter goes there. Before this, those boxes got
whatever the general question-answerer wrote - on AT&T and DriveNets, his
"how did you hear about us" instruction ("I found this role through Job
Hunter...") pasted into the Personal note.

The PDF is printed by the Chromium the apply engine already ships (Playwright),
so any script prints (a Hebrew name, curly quotes, dashes) and no PDF library
is added.
"""
import datetime
import html
import re


def paragraphs(letter: str) -> list:
    """The letter's paragraphs, blank-line separated, whitespace tidied."""
    parts = re.split(r"\n\s*\n", (letter or "").strip())
    return [re.sub(r"[ \t]+", " ", p).strip() for p in parts if p.strip()]


def short_note(letter: str, limit: int = 700) -> str:
    """The letter's first body paragraph (after "Dear ..."), at most `limit`
    characters, cut at a sentence end. For a note box next to an attached PDF."""
    paras = [p for p in paragraphs(letter)
             if not re.match(r"^(dear|hi|hello|to whom)\b", p, re.I)
             and not re.match(r"^(sincerely|best|kind regards|regards|thank you|yours)\b", p, re.I)]
    text = paras[0] if paras else (letter or "").strip()
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[:end + 1] if end > limit // 3 else cut.rsplit(" ", 1)[0] + "…"


def display_name(full_name: str) -> str:
    """"<Full name> Cover Letter <year>.pdf" - next to "<Full name> CV <year>.pdf"."""
    name = re.sub(r"[^\w .'-]", "", (full_name or "").strip(), flags=re.UNICODE)
    name = re.sub(r"\s+", " ", name).strip()
    year = datetime.date.today().year
    return (f"{name} Cover Letter {year}" if name else f"Cover Letter {year}") + ".pdf"


def letter_html(letter: str, name: str = "", contact=None) -> str:
    """A plain one-page letter: the name and contact line at the top, the date,
    then the paragraphs as the letter has them."""
    contact = [c for c in (contact or []) if c]
    e = html.escape
    body = "".join("<p>%s</p>" % e(p).replace("\n", "<br>") for p in paragraphs(letter))
    rtl = bool(re.search(r"[֐-׿]", letter or ""))
    return ("<!doctype html><html dir='%s'><head><meta charset='utf-8'><style>"
            "@page{size:A4;margin:22mm 20mm}"
            "body{font-family:Arial,Helvetica,'DejaVu Sans',sans-serif;font-size:11pt;line-height:1.5;color:#111}"
            "h1{font-size:16pt;margin:0 0 2pt;font-weight:600}"
            ".c{color:#444;font-size:9.5pt;margin-bottom:16pt}.d{color:#444;margin-bottom:14pt}"
            "p{margin:0 0 10pt}</style></head><body>"
            "%s%s<div class='d'>%s</div>%s</body></html>") % (
        "rtl" if rtl else "ltr",
        "<h1>%s</h1>" % e(name) if name else "",
        "<div class='c'>%s</div>" % e("  ·  ".join(contact)) if contact else "",
        e(datetime.date.today().strftime("%-d %B %Y")),
        body)


def letter_pdf(letter: str, name: str = "", contact=None) -> bytes:
    """The letter as PDF bytes (headless Chromium)."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            p = b.new_page()
            p.set_content(letter_html(letter, name, contact), wait_until="load")
            return p.pdf(format="A4", print_background=True)
        finally:
            b.close()
