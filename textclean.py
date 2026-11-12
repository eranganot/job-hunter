"""Plain text from the HTML job sources hand us (2026-11-12).

The app shows a job's description as text. Sources send it as raw HTML (Ashby),
as HTML escaped once more (Greenhouse's API: "&lt;p&gt;"), or with entities in
plain text ("world&#39;s"), and two readers made it worse by stripping tags
before decoding them. Every job is cleaned with this on the way into the
database (all four INSERTs) and migration 33 cleaned the rows already stored."""
import html
import re

_ENTITY = re.compile(r"&(#\d+|#x[0-9a-f]+|[a-z][a-z0-9]{1,8});", re.I)
_TAG = re.compile(r"</?([a-z][a-z0-9]*)\b[^<>]*>", re.I)      # "<b" yes, "< b" (prose) no
_BLOCK_END = re.compile(r"<(br\s*/?|/(p|div|h[1-6]|li|ul|ol|tr|section|article|blockquote))\s*>", re.I)
_LI = re.compile(r"<li\b[^<>]*>", re.I)
_HEADING_START = re.compile(r"<h[1-6]\b[^<>]*>", re.I)


def _decode(s: str) -> str:
    # Escaped more than once happens ("&amp;lt;p&amp;gt;"): decode until stable.
    for _ in range(3):
        if not _ENTITY.search(s):
            break
        s = html.unescape(s)
    return s


def html_to_text(value, keep_lines: bool = True) -> str:
    """Readable text: entities decoded, block tags -> line breaks, list items ->
    bullets, every other tag removed. Text with no HTML comes back unchanged
    apart from decoded entities. keep_lines=False gives one line (titles)."""
    if not value:
        return value or ""
    s = _decode(str(value))
    if _TAG.search(s):
        s = _HEADING_START.sub("\n\n", s)
        s = _LI.sub("\n• ", s)
        s = _BLOCK_END.sub("\n", s)
        s = _TAG.sub("", s)
        s = _decode(s)
    s = s.replace("\xa0", " ").replace("\r", "")
    if not keep_lines:
        return re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"[ \t\f\v]+", " ", s)
    s = re.sub(r" *\n *", "\n", s)
    s = re.sub(r"\n+(?=• )", "\n", s)            # bullets on consecutive lines
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def clean_job(j: dict) -> dict:
    """The same job dict with title/company/location/description/full_description cleaned."""
    if not isinstance(j, dict):
        return j
    out = dict(j)
    for k in ("title", "job_title", "company", "location"):
        if isinstance(out.get(k), str):
            out[k] = html_to_text(out[k], keep_lines=False)
    for k in ("description", "full_description"):
        if isinstance(out.get(k), str):
            out[k] = html_to_text(out[k])
    return out
