"""
tests/test_jsx_escapes.py - a \\uXXXX that the user reads as "\\u2026".

JavaScript processes backslash escapes inside string literals. **JSX does not**,
in two places:

  * a text node        <span>Applying\\u2026</span>
  * an attribute value placeholder="Send alerts to\\u2026"

Both reach the browser as the literal seven characters. It is invisible in
review - the source looks like every other escape on the page - and invisible in
a type check, because it is a perfectly valid string. It is only visible to
someone looking at the running app, which is how the first one shipped: Eran
spotted `Queued \\u00b7 ready to apply` in his own screenshot on 2026-09-15.

Fixing that one found two more the same day, one of them written that morning.
Hence this file: the pattern is cheap to detect and evidently not cheap to
notice.
"""
import pathlib
import re

import pytest

SRC = pathlib.Path(__file__).resolve().parent.parent / "web" / "src"

ESC = r"\\u[0-9a-fA-F]{4}"
# An attribute whose value is a plain quoted string - `foo={...}` is a JS
# expression and is fine, `foo="..."` is a JSX string literal and is not.
ATTR = re.compile(r'\b[a-zA-Z-]+="[^"\n]*' + ESC + r'[^"\n]*"')
# Text sitting directly between tags.
TEXT = re.compile(r'>[^<>{}\n]*' + ESC)


def _tsx_files():
    return sorted(SRC.rglob("*.tsx"))


@pytest.mark.skipif(not SRC.is_dir(), reason="no web/src checked out")
def test_no_escape_sequence_is_left_for_jsx_to_not_process():
    offenders = []
    for f in _tsx_files():
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            for rx, what in ((ATTR, "JSX attribute"), (TEXT, "JSX text")):
                m = rx.search(line)
                if m:
                    offenders.append("%s:%d (%s) %s"
                                     % (f.name, n, what, m.group(0).strip()[:70]))
    assert not offenders, (
        "These render the literal characters \\uXXXX to the user, because JSX "
        "does not process escapes in text nodes or attribute strings. Use the "
        "real character, or move the string into a {\"...\"} expression:\n  - "
        + "\n  - ".join(offenders))


@pytest.mark.skipif(not SRC.is_dir(), reason="no web/src checked out")
def test_the_detector_catches_both_shapes(tmp_path):
    """Without this the test above passes on a detector that matches nothing -
    which is exactly what its first version did, against a file containing two
    real instances."""
    bad = tmp_path / "Bad.tsx"
    bad.write_text(
        'const a = <span>Applying\\u2026</span>;\n'
        'const b = <input placeholder="Send alerts to\\u2026" />;\n'
        'const ok = "this \\u2026 is a real JS string and is fine";\n'
        'const ok2 = <input placeholder={`e.g. ${x}`} />;\n',
        encoding="utf-8")
    lines = bad.read_text(encoding="utf-8").splitlines()
    assert ATTR.search(lines[1]), "the attribute shape is not detected"
    assert TEXT.search(lines[0]), "the text-node shape is not detected"
    assert not ATTR.search(lines[2]) and not TEXT.search(lines[2]), \
        "a plain JS string was flagged - those are processed and are correct"
    assert not ATTR.search(lines[3]), "an expression attribute was flagged"
