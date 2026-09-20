"""
tests/test_contrast.py - the dark palette stays readable.

Phase 4 item 6 asked for a contrast check. Running one found that
`text-gray-500` on `bg-gray-800` is **3.04:1** against a 4.5:1 bar - and it was
used 52 times, so most of the secondary copy in the app (timestamps, sub-labels,
"Ready to apply", every "Recorded before the app tracked this") sat below AA.
A dark theme is exactly where this hides: it looks fine to whoever picked the
colour on a good monitor, and is unreadable on a phone outdoors.

The colours are Tailwind defaults, so the values are exact and no browser is
needed - which is what lets this be a test instead of a one-off audit.
"""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import contrast_audit as ca  # noqa: E402


def _ratio_hex(a, b):
    la, lb = ca.luminance(a), ca.luminance(b)
    lo, hi = sorted((la, lb))
    return (hi + 0.05) / (lo + 0.05)


def test_every_rendered_pair_meets_aa():
    bad = ca.audit()
    assert not bad, "below AA:\n  " + "\n  ".join(
        "%s on %s is %.2f:1, needs %.1f as %s" % (fg, bg, r, need, role)
        for fg, bg, role, r, need in bad)


def test_the_audit_can_actually_fail():
    """Without this, a bug that made audit() always return [] would leave the
    test above green forever while the palette rotted."""
    assert ca.ratio("gray-500", "gray-800") < 4.5
    assert ca.ratio("gray-400", "gray-800") >= 4.5


@pytest.mark.parametrize("known", [
    ("white", "gray-900", 17.74),   # published WCAG values for these hexes
    ("gray-400", "gray-800", 5.78),
])
def test_the_maths_matches_published_values(known):
    """A contrast checker with a wrong formula passes everything. sRGB
    linearisation is easy to get subtly wrong; these are the arithmetic pinned
    against values anyone can verify in a browser devtools panel."""
    fg, bg, expected = known
    assert abs(ca.ratio(fg, bg) - expected) < 0.05, ca.ratio(fg, bg)


def test_the_low_contrast_grey_does_not_come_back():
    """The specific regression. text-gray-500 reads as "muted" and is the
    natural thing to reach for next time someone wants a quieter label."""
    src = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    assert "text-gray-500" not in src, (
        "text-gray-500 is back - it is 3.04:1 on gray-800, below the 4.5:1 bar. "
        "Use text-gray-400 (5.78:1) for muted copy.")


def _auth_css():
    import re
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    m = re.search(r'_AUTH_HEAD = """(.*?)"""', src, re.S)
    assert m, "the shared auth page head is gone"
    # Strip CSS comments: the first version of this test banned a hex outright
    # and tripped on the word inside a comment EXPLAINING why it was replaced -
    # and on the same hex used legitimately as a border, where 3.67:1 clears
    # the 3.0 bar. A check has to know which declaration it is looking at.
    return re.sub(r"/\*.*?\*/", "", m.group(1), flags=re.S)


def _decl(css, selector, prop):
    import re
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert m, "no rule for %r" % selector
    d = re.search(re.escape(prop) + r"\s*:\s*([^;]+);", m.group(1))
    assert d, "no %r in %r" % (prop, selector)
    return d.group(1).strip()


@pytest.mark.parametrize("selector, prop, against, floor, what", [
    ("input::placeholder", "color",        "#111827", 4.5, "placeholder text on the field fill"),
    (".or span",           "color",        "#0f172a", 4.5, "the 'or' divider label on the page"),
    ("label",              "color",        "#1f2937", 4.5, "field labels on the card"),
    (".alt",               "color",        "#1f2937", 4.5, "the 'create one' line on the card"),
])
def test_the_sign_in_pages_meet_aa(selector, prop, against, floor, what):
    """Hand-written CSS, so the Tailwind audit cannot see it - and these are the
    only screens a stranger sees before they have an account."""
    css = _auth_css()
    colour = _decl(css, selector, prop)
    assert colour.startswith("#"), "expected a hex for %s, got %r" % (what, colour)
    r = _ratio_hex(colour, against)
    assert r >= floor, "%s is %.2f:1 (%s on %s), needs %.1f" % (what, r, colour, against, floor)


def test_the_sign_in_field_border_carries_its_own_boundary():
    """A #111827 field inside a #1f2937 card differ by 1.22:1, so the border is
    the only thing saying where the input is. WCAG 1.4.11 wants 3:1 for that."""
    border = _decl(_auth_css(), "input", "border")
    hexes = [t for t in border.split() if t.startswith("#")]
    assert hexes, "the field border has no colour: %r" % border
    r = _ratio_hex(hexes[0], "#111827")
    assert r >= 3.0, "the field border is %.2f:1 against its own fill, needs 3.0" % r


def test_the_shipped_bundle_carries_no_low_contrast_grey():
    """The source check above is where the fix lives; this is the backstop on
    the artefact users receive. A rebuild that never ran would leave the old
    classes in web_bundle/ with the source looking perfectly correct - which is
    a failure mode this repo has already had (2026-09-16)."""
    import re
    html = (ROOT / "web_bundle" / "index.html").read_text(encoding="utf-8")
    rels = re.findall(r'src="[^"]*?(assets/[^"]+\.js)"', html)
    assert rels, "index.html loads no script"
    js = "\n".join((ROOT / "web_bundle" / r).read_text(encoding="utf-8", errors="replace")
                   for r in rels)
    assert "text-gray-500" not in js, (
        "the shipped bundle still contains text-gray-500 (3.04:1) - "
        "web_bundle/ was not rebuilt after the contrast fix")
