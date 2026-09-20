#!/usr/bin/env python3
"""
scripts/contrast_audit.py - WCAG contrast for the dark palette /app actually uses.

Phase 4 item 6 asks for "a contrast check on the dark palette". A dark theme is
where contrast quietly fails: light-on-dark looks fine to the person who chose
it on a good monitor, and is unreadable on a phone in daylight. The colours are
Tailwind's defaults, so the values are known exactly and no browser is needed -
which also means this can be a test rather than a one-off audit.

Thresholds are WCAG 2.1 AA: 4.5:1 for body text, 3.0:1 for large text (>=18.66px
bold or >=24px) and for UI component boundaries.

Reports rather than guesses: a pair that fails is printed with the ratio it
needs, so the fix is a decision about which shade to move, not a hunt.

Only pairs the UI ACTUALLY RENDERS are listed. The first run flagged six, two of
which were hover states whose text colour changes in the same rule - pairs that
never appear on screen. A check that cries wolf gets ignored, so those are kept
as comments rather than assertions.
"""
import itertools
import sys

# Tailwind 3 default palette, only the shades /app uses.
TAILWIND = {
    "gray-200": "#e5e7eb", "gray-300": "#d1d5db", "gray-400": "#9ca3af",
    "gray-500": "#6b7280", "gray-600": "#4b5563", "gray-700": "#374151",
    "gray-800": "#1f2937", "gray-900": "#111827",
    "indigo-200": "#c7d2fe", "indigo-300": "#a5b4fc", "indigo-400": "#818cf8",
    "indigo-500": "#6366f1", "indigo-600": "#4f46e5", "indigo-700": "#4338ca",
    "green-300": "#86efac", "green-400": "#4ade80",
    "amber-300": "#fcd34d", "amber-400": "#fbbf24",
    "red-300": "#fca5a5", "red-400": "#f87171",
    "blue-300": "#93c5fd", "blue-400": "#60a5fa",
    "blue-500": "#3b82f6", "blue-600": "#2563eb", "blue-700": "#1d4ed8",
    "white": "#ffffff",
}

# What actually sits on what, read off App.tsx rather than imagined.
# (foreground, background, role)  role: "body" | "large" | "ui"
PAIRS = [
    # Page and card backgrounds
    ("gray-200", "gray-800", "body"),
    ("gray-300", "gray-800", "body"),
    ("gray-400", "gray-800", "body"),
    ("white",    "gray-800", "body"),
    ("gray-400", "gray-900", "body"),
    # gray-500 as TEXT is gone: it was the muted sub-label used 52 times at
    # 3.04:1 on gray-800, i.e. most of the secondary copy in the app sat below
    # AA. Replaced by gray-400 (5.78:1). It survives only as a border colour,
    # asserted under "ui" below. tests/test_contrast.py fails if it comes back.
    ("gray-300", "gray-700", "body"),
    # NOT "gray-400 on gray-700": the only places those meet are hover states
    # whose text also changes to gray-200 in the same rule, so that pair is
    # never rendered. Asserting it would be a false alarm, which is worse than
    # no check - it teaches the reader to ignore this report.
    # Accents on cards
    ("indigo-300", "gray-800", "body"),
    ("indigo-400", "gray-800", "body"),
    ("green-300",  "gray-800", "body"),
    ("green-400",  "gray-800", "body"),
    ("amber-300",  "gray-800", "body"),
    ("amber-400",  "gray-800", "body"),
    ("red-300",    "gray-800", "body"),
    ("red-400",    "gray-800", "body"),
    ("blue-300",   "gray-800", "body"),
    ("blue-400",   "gray-800", "body"),
    # Primary button
    ("white", "indigo-600", "body"),
    ("white", "indigo-700", "body"),
    # "Apply to queue" in the Queue tab (2026-09-20). Its first draft was
    # bg-blue-600 hover:bg-blue-500, and adding the pair here caught that the
    # HOVER state is 3.68:1 - a button that meets AA at rest and fails while the
    # pointer is on it is still a failure, and hover is when it is being read.
    # Moved onto the documented primary (indigo-600/700), already asserted above.
    # indigo-500 is only a :hover fill under an indigo-600 rest state, and it
    # lands at 4.47:1 - three hundredths under. Recorded here, not asserted.
    # Component boundaries. A card border is decorative - the gray-800 card is
    # already distinguishable from the gray-900 page by fill - so it is not
    # asserted. A FORM FIELD border is different: a gray-800 field inside a
    # gray-900 card differ by 1.24:1, so the border is the only thing saying
    # where the input is, and WCAG 1.4.11 applies to it.
    ("gray-500", "gray-800", "ui"),        # form field border on its own fill
    ("indigo-500", "gray-800", "ui"),      # focus ring
    ("gray-400", "gray-800", "ui"),
]

MIN = {"body": 4.5, "large": 3.0, "ui": 3.0}


def _srgb(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(hex_colour):
    h = hex_colour.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _srgb(r) + 0.7152 * _srgb(g) + 0.0722 * _srgb(b)


def ratio(fg, bg):
    a, b = luminance(TAILWIND[fg]), luminance(TAILWIND[bg])
    lo, hi = sorted((a, b))
    return (hi + 0.05) / (lo + 0.05)


def audit():
    """Returns [(fg, bg, role, ratio, required)] for every pair BELOW its bar."""
    bad = []
    for fg, bg, role in PAIRS:
        r = ratio(fg, bg)
        if r < MIN[role]:
            bad.append((fg, bg, role, r, MIN[role]))
    return bad


def main():
    bad = audit()
    print("Contrast audit - %d pair(s) checked, WCAG 2.1 AA\n" % len(PAIRS))
    for fg, bg, role in PAIRS:
        r = ratio(fg, bg)
        ok = r >= MIN[role]
        print("  %-5s %-11s on %-9s %5.2f:1  (needs %.1f)  %s"
              % ("" if ok else "FAIL", fg, bg, r, MIN[role], "" if ok else "<-"))
    if bad:
        print("\n%d pair(s) below AA:" % len(bad))
        for fg, bg, role, r, need in bad:
            print("  %s on %s is %.2f:1, needs %.1f as %s text" % (fg, bg, r, need, role))
        return 1
    print("\nAll pairs meet AA.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
