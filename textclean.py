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


_CUT_TAG = re.compile(r"</?[a-z][a-z0-9]*\b[^<>]*$", re.I)   # a tag cut off by a length limit


def html_to_text(value, keep_lines: bool = True) -> str:
    """Readable text: entities decoded, block tags -> line breaks, list items ->
    bullets, every other tag removed. Text with no HTML comes back unchanged
    apart from decoded entities. keep_lines=False gives one line (titles)."""
    if not value:
        return value or ""
    s = _decode(str(value))
    s = _CUT_TAG.sub("", s)          # stored rows were cut at 5,000 chars, sometimes mid-tag
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
    # "Why this fits" is read by the job seeker: "you", not "the candidate" (2026-11-12).
    import voice
    for k in ("fit_reason", "why_relevant"):
        if isinstance(out.get(k), str):
            out[k] = voice.second_person(html_to_text(out[k], keep_lines=False))
    return out


# ── Repair of text whose angle brackets were already removed (2026-11-12) ──────
# The board-first Greenhouse reader turned "&lt;"/"&gt;" into spaces before
# stripping tags, so stored rows read "p strong Who We Are: /strong ... /p p".
# The brackets are gone, so html_to_text cannot see tags; but the closing forms
# ("/p", "/strong") never occur in prose, which makes the damage recognisable.
_STRUCT = r"(?:div|span|p|h[1-6]|ul|ol|li|br|table|tbody|tr|td|th|section|article|header|footer|blockquote)"
_WORDY = ("strong", "em", "b", "i", "u", "a")     # also English words: removed only when paired
_ATTRS = r'(?:\s+[a-z][a-z0-9_:-]*="[^"]*")+'
DEBRIS_SIGNATURE = re.compile(r'(?<!\S)/(?:p|div|li|ul|ol|strong|span|em|h[1-6])(?!\S)|^\s*div class="', re.I)


def repair_stripped_tags(value: str) -> str:
    """Readable text from a row damaged as above. Returns the input unchanged
    when it does not carry the damage signature."""
    if not value or not DEBRIS_SIGNATURE.search(value):
        return value or ""
    s = value
    present_closers = {m.lower() for m in re.findall(r"(?<!\S)/([a-z][a-z0-9]*)(?!\S)", s, re.I)}
    # tags with attributes: any tag name, the attribute list is unambiguous
    s = re.sub(r'(?<!\S)[a-z][a-z0-9]*' + _ATTRS + r'(?!\S)', " ", s, flags=re.I)
    s = re.sub(r"(?<!\S)li(?!\S)", "\n• ", s, flags=re.I)
    s = re.sub(r"(?<!\S)/(?:p|div|h[1-6]|li|ul|ol|tr|section|article|blockquote)(?!\S)", "\n", s, flags=re.I)
    s = re.sub(r"(?<!\S)br(?!\S)", "\n", s, flags=re.I)
    s = re.sub(r"(?<!\S)/?" + _STRUCT + r"(?!\S)", " ", s, flags=re.I)
    for w in _WORDY:
        if w in present_closers:
            s = re.sub(r"(?<!\S)/" + w + r"(?!\S)", " ", s, flags=re.I)
            # The opener only where a tag would sit: before a capitalised word, a
            # digit, a quote, a line break or another opener - so the English
            # word in "a strong team" stays (lowercase follows).
            s = re.sub(r"(?<!\S)" + w + r"(?=\s*(?:\n|$|[A-Z0-9\"“(•]|(?:strong|em|b|i|u|a)\b))", " ", s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r" *\n *", "\n", s)
    s = re.sub(r"\n+(?=• )", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()
