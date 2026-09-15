"""
tests/test_web_bundle.py - the committed SPA bundle is a real build.

web_bundle/ is committed and Railway does not build the frontend, so whatever
is in this directory is what users get. A broken build is therefore not a red
deploy - it is a live, unstyled app, and it has happened: the bundle carried a
393-byte stylesheet whose entire content was the Tailwind directives, verbatim,
because PostCSS never ran. Vite exited 0 and nothing said a word.

The suite is the last gate before that ships, which is why the check lives here
and not only in the build script - the build script is the thing a person can
forget to run.
"""
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
BUNDLE = ROOT / "web_bundle"

sys.path.insert(0, str(ROOT / "scripts"))
import verify_web_bundle as vwb  # noqa: E402


@pytest.mark.skipif(not BUNDLE.is_dir(), reason="no web_bundle/ checked out")
def test_the_committed_bundle_is_a_real_build():
    problems = vwb.verify(str(BUNDLE))
    assert not problems, "web_bundle/ would ship broken:\n  - " + "\n  - ".join(problems)


# ── The verifier itself, tested rather than trusted ──────────────────────────
#
# A verifier that cannot fail is a rubber stamp. Each of these plants the exact
# fault that was found in the real bundle and asserts it is caught.

def _fixture_bundle(tmp_path, css_body, js_bytes=200000, stale=(), href=None):
    b = tmp_path / "web_bundle"
    (b / "assets").mkdir(parents=True)
    css_name, js_name = "index-AAA.css", "index-BBB.js"
    (b / "assets" / css_name).write_text(css_body, encoding="utf-8")
    (b / "assets" / js_name).write_bytes(b"x" * js_bytes)
    for s in stale:
        (b / "assets" / s).write_bytes(b"old")
    (b / "sw.js").write_text('const VERSION = "jh-v9";', encoding="utf-8")
    (b / "index.html").write_text(
        '<html><head><link rel="stylesheet" href="/app/assets/%s">'
        '<script src="/app/assets/%s"></script></head></html>'
        % (href or css_name, js_name), encoding="utf-8")
    return str(b)


def test_a_healthy_bundle_passes(tmp_path):
    """Without this the other tests could all pass on a verifier that always
    fails, which is the same rubber stamp facing the other way."""
    assert vwb.verify(_fixture_bundle(tmp_path, "a{color:red}" + "/*pad*/" * 3000)) == []


def test_unprocessed_tailwind_directives_are_caught(tmp_path):
    """The real fault, byte for byte: the directives survive into the output
    because PostCSS did not run."""
    css = ("@tailwind base;@tailwind components;@tailwind utilities;"
           "html,body,#root{height:100%}" + "/*pad*/" * 3000)
    problems = vwb.verify(_fixture_bundle(tmp_path, css))
    assert any("@tailwind" in p for p in problems), problems


def test_a_tiny_stylesheet_is_caught_even_without_the_directives(tmp_path):
    """The other way Tailwind produces nothing: the content globs match no
    files, so it emits a valid but nearly empty sheet."""
    problems = vwb.verify(_fixture_bundle(tmp_path, "a{color:red}"))
    assert any("bytes" in p for p in problems), problems


def test_a_stub_script_is_caught(tmp_path):
    problems = vwb.verify(_fixture_bundle(tmp_path, "a{}" + "/*pad*/" * 3000, js_bytes=500))
    assert any("app bundle" in p for p in problems), problems


def test_stale_assets_are_caught(tmp_path):
    """22 of them were sitting in the real bundle - every build since the first
    one, because Vite's emptyOutDir cleans dist/ and not web_bundle/."""
    problems = vwb.verify(_fixture_bundle(
        tmp_path, "a{}" + "/*pad*/" * 3000, stale=("index-OLD1.js", "index-OLD2.css")))
    assert any("stale" in p for p in problems), problems


def test_a_dangling_reference_is_caught(tmp_path):
    """The one failure that takes the app fully down rather than unstyling it."""
    problems = vwb.verify(_fixture_bundle(
        tmp_path, "a{}" + "/*pad*/" * 3000, href="index-GONE.css"))
    assert any("does not exist" in p for p in problems), problems


def test_a_service_worker_with_no_version_is_caught(tmp_path):
    b = pathlib.Path(_fixture_bundle(tmp_path, "a{}" + "/*pad*/" * 3000))
    (b / "sw.js").write_text("// no version here", encoding="utf-8")
    assert any("VERSION" in p for p in vwb.verify(str(b)))


# ── The desktop layout is actually in the shipped CSS ────────────────────────
#
# Tailwind only emits a class it found in the source, so the built stylesheet is
# evidence about the layout rather than a proxy for it: if someone removes the
# sidebar's classes, or the content globs stop matching App.tsx, these rules
# vanish from the output and this fails. No browser needed, so it runs
# everywhere - a Playwright test would skip on most machines, and a skip is
# never a pass.

# Plain substrings, not regexes. Tailwind escapes the special characters in a
# class name, so the file contains `.max-w-\[1600px\]` with real backslashes -
# and a regex written to match that is exactly the sort of thing that silently
# matches nothing. (It did, on the first run.)
DESKTOP_RULES = [
    ("max-w-\\[1600px\\]{max-width:1600px}", "the shell's width cap"),
    ("lg\\:flex{",         "the sidebar row at lg"),
    ("lg\\:w-56{",         "the sidebar's width"),
    ("lg\\:sticky{",       "the sidebar staying put while content scrolls"),
    ("lg\\:hidden{",       "hiding the small-screen tab grid once the rail exists"),
    ("xl\\:grid-cols-2{",  "two job columns from xl"),
    ("2xl\\:grid-cols-3{", "three job columns from 2xl"),
]


def _bundle_css() -> str:
    import re
    html = (BUNDLE / "index.html").read_text(encoding="utf-8")
    rels = re.findall(r'href="[^"]*?(assets/[^"]+\.css)"', html)
    assert rels, "index.html loads no stylesheet"
    return "\n".join((BUNDLE / r).read_text(encoding="utf-8") for r in rels)


@pytest.mark.skipif(not BUNDLE.is_dir(), reason="no web_bundle/ checked out")
@pytest.mark.parametrize("pattern, what", DESKTOP_RULES,
                         ids=[w for _p, w in DESKTOP_RULES])
def test_the_desktop_layout_survived_the_build(pattern, what):
    css = _bundle_css()
    assert pattern in css, (
        "%s is missing from the built CSS. Tailwind only emits classes it finds "
        "in the source, so either the markup lost them or tailwind.config.js's "
        "content globs stopped matching - and the app would render as a narrow "
        "column again with no error anywhere." % what)


@pytest.mark.skipif(not BUNDLE.is_dir(), reason="no web_bundle/ checked out")
def test_the_phone_layout_is_not_collateral_damage():
    """The sidebar is additive. If the base (unprefixed) grid utilities ever
    disappeared, desktop would look fine and the phone - the primary device -
    would not."""
    import re
    css = _bundle_css()
    for pattern in (r"\.grid\{", r"\.grid-cols-3\{", r"\.grid-cols-2\{"):
        assert re.search(pattern, css), "base layout utility %s missing" % pattern


# ── The settings sheet is not a phone column on a desktop ────────────────────

SETTINGS_RULES = [
    ("lg\\:max-w-4xl{",  "the settings sheet widening past a phone column"),
    ("lg\\:columns-2{",  "its sections flowing into two columns"),
]


@pytest.mark.skipif(not BUNDLE.is_dir(), reason="no web_bundle/ checked out")
@pytest.mark.parametrize("pattern, what", SETTINGS_RULES,
                         ids=[w for _p, w in SETTINGS_RULES])
def test_the_settings_sheet_uses_the_screen(pattern, what):
    assert pattern in _bundle_css(), (
        "%s is missing from the built CSS - settings would open as a 576px "
        "column in the middle of a 1280px screen, which is the thing the "
        "desktop pass was for." % what)


# ── The onboarding flow made it into the bundle ──────────────────────────────
#
# Tailwind only emits what it finds in the source, so these rules are evidence
# that OnboardingView is compiled in rather than tree-shaken or unreferenced.
# A new signup hitting an unstyled or absent wizard is the one failure nobody
# would see in testing, because nobody on the team signs up twice.

ONBOARDING_RULES = [
    ("max-w-3xl{",   "the setup flow's column width"),
    # Tailwind escapes the dot, so the file says `.gap-1\.5{`. Written without
    # the backslash this matched nothing and passed on a bundle that had the
    # rule - the exact trap the note above the desktop rules describes, walked
    # straight into one screen later.
    ("gap-1\\.5{",  "the step-progress row"),
]


@pytest.mark.skipif(not BUNDLE.is_dir(), reason="no web_bundle/ checked out")
@pytest.mark.parametrize("pattern, what", ONBOARDING_RULES,
                         ids=[w for _p, w in ONBOARDING_RULES])
def test_the_onboarding_flow_survived_the_build(pattern, what):
    assert pattern in _bundle_css(), "%s is missing from the built CSS" % what


@pytest.mark.skipif(not BUNDLE.is_dir(), reason="no web_bundle/ checked out")
def test_the_onboarding_copy_is_in_the_shipped_script():
    """The CSS proves the layout compiled; this proves the flow itself did.
    Checked on the built JS because that is the artefact users receive."""
    import re
    html = (BUNDLE / "index.html").read_text(encoding="utf-8")
    rels = re.findall(r'src="[^"]*?(assets/[^"]+\.js)"', html)
    assert rels, "index.html loads no script"
    js = "\n".join((BUNDLE / r).read_text(encoding="utf-8", errors="replace") for r in rels)
    for needle in ("Set up Job Hunter", "Upload your CV", "Your job profile"):
        assert needle in js, "onboarding string %r is not in the shipped bundle" % needle


@pytest.mark.skipif(not BUNDLE.is_dir(), reason="no web_bundle/ checked out")
def test_no_app_string_ships_a_literal_backslash_u():
    """The source check in tests/test_jsx_escapes.py names the file and line;
    this is the backstop on the artefact users actually receive.

    A JS escape becomes the real character at build time, so anything still
    spelled \\uXXXX in the OUTPUT is a string someone will read that way. Only
    QUOTED strings are inspected: a broken JSX text node or attribute compiles
    to one, while React's own escapes live in regex literals
    (/^[:A-Z_a-z\\u00C0-.../). That distinction is what makes this precise
    without an allowlist - a first version tried to allowlist the regexes by
    their surrounding characters and reported nine false positives.
    """
    import re
    html = (BUNDLE / "index.html").read_text(encoding="utf-8")
    rels = re.findall(r'src="[^"]*?(assets/[^"]+\.js)"', html)
    js = "\n".join((BUNDLE / r).read_text(encoding="utf-8", errors="replace") for r in rels)

    offenders = re.findall(r'"[^"\n]{0,80}?\\u[0-9a-fA-F]{4}[^"\n]{0,40}?"', js)
    assert not offenders, (
        "the shipped bundle contains string(s) a user will read as a literal "
        "escape sequence:\n  - " + "\n  - ".join(dict.fromkeys(offenders)))
