"""
tests/test_no_unmetered_gemini.py - the guard that the door stays the only door.

A spend ceiling enforced at one chokepoint is only worth what the chokepoint's
exclusivity is worth. Before gemini.py there were ten call sites, each building
its own URL; the eleventh, added next month by someone who copies one of the
other ten, would spend money silently and no test elsewhere in this suite would
notice.

So this reads the source. It is a lint, not a unit test, and it is deliberately
the crude kind: any mention of the Gemini host outside gemini.py fails, whatever
it is doing. A false positive costs one line in ALLOWED; a false negative costs
a month of unmetered spend.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
HOST = "generativelanguage.googleapis.com"

# The module that IS the door, plus the tests that describe it.
ALLOWED = {"gemini.py", "test_no_unmetered_gemini.py", "test_gemini_budget.py"}


def _python_files():
    for p in ROOT.rglob("*.py"):
        if "__pycache__" in p.parts or ".git" in p.parts:
            continue
        if "web" in p.parts or "web_bundle" in p.parts:
            continue
        yield p


def test_only_gemini_py_names_the_gemini_host():
    offenders = [
        str(p.relative_to(ROOT))
        for p in _python_files()
        if p.name not in ALLOWED and HOST in p.read_text(encoding="utf-8", errors="ignore")
    ]
    assert not offenders, (
        "These files reach Gemini without going through gemini.py, so their spend "
        "is not counted and no ceiling applies to it: %s. Call gemini.generate() "
        "instead; it takes the request body and returns the parsed response."
        % ", ".join(offenders))


def test_every_metered_call_declares_what_it_is_for():
    """`purpose` is what makes the ledger answerable.

    Without it the admin view can say "4,812 calls today" and nothing about
    which feature spent them, which is the only question worth asking when the
    number is wrong.
    """
    bad = []
    for p in _python_files():
        if p.name in ALLOWED:
            continue
        src = p.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"gemini\.generate(?:_text)?\(", src):
            tail = src[m.end():m.end() + 400]
            if "purpose=" not in tail.split(")\n")[0] and "purpose=" not in tail[:300]:
                line = src[: m.start()].count("\n") + 1
                bad.append("%s:%d" % (p.relative_to(ROOT), line))
    assert not bad, "gemini.generate() without purpose= at: %s" % ", ".join(bad)
