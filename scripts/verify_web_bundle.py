#!/usr/bin/env python3
"""
Verify web_bundle/ is a real build and not a broken one.

Why this exists, with the evidence that prompted it
---------------------------------------------------
`web_bundle/` is committed, and Railway does not build the frontend - so a
broken build ships as-is and the only symptom is an unstyled page. That has
already happened here: `web_bundle/assets/index-8bxnukfH.css` is **393 bytes**
and its contents are

    @tailwind base;@tailwind components;@tailwind utilities;html,body,#root{...}

- the Tailwind **directives, verbatim**. PostCSS never ran, so every utility
class in the app resolved to nothing. Vite still emitted a valid CSS file and
still exited 0. That is the shape of this failure: nothing errors, the page
loads, and it just looks wrong.

So the size check the plan asked for is here, but the *content* check is the one
that actually names the fault: a build whose CSS still contains `@tailwind` is
broken at any size.

Also checked, because both were found in the committed bundle on 2026-09-15:
  * **stale assets** - 22 files in assets/ of which index.html referenced 2.
    Vite's `emptyOutDir` cleans `dist/`, not `web_bundle/`, so every build since
    the beginning has left its predecessor behind. 5.0 MB of dead weight in the
    repo and in the Docker image.
  * **dangling references** - index.html naming a file that is not there is the
    one failure that takes the app fully down rather than merely unstyled.

Run:  python scripts/verify_web_bundle.py [--bundle web_bundle]
Exit: 0 all good, 1 a check failed.
"""
from __future__ import annotations

import argparse
import os
import re
import sys

MIN_CSS_BYTES = 15000       # a real Tailwind build is ~27KB; the broken one was 393
MIN_JS_BYTES = 100000       # the app bundle is ~345KB
TAILWIND_DIRECTIVE = re.compile(r"@tailwind\s+(base|components|utilities)")


def _fail(problems, msg):
    problems.append(msg)


def verify(bundle: str) -> list:
    problems = []
    index = os.path.join(bundle, "index.html")
    assets = os.path.join(bundle, "assets")

    if not os.path.isfile(index):
        return ["%s does not exist - the bundle was never copied" % index]
    if not os.path.isdir(assets):
        return ["%s does not exist" % assets]

    html = open(index, encoding="utf-8").read()
    referenced = set(re.findall(r'(?:src|href)="[^"]*?(assets/[^"]+)"', html))
    if not referenced:
        _fail(problems, "index.html references no assets/ file at all")

    # 1. Everything index.html names must be there.
    for rel in sorted(referenced):
        if not os.path.isfile(os.path.join(bundle, rel)):
            _fail(problems, "index.html references %s, which does not exist" % rel)

    css = [r for r in referenced if r.endswith(".css")]
    js = [r for r in referenced if r.endswith(".js")]
    if not css:
        _fail(problems, "index.html loads no stylesheet")
    if not js:
        _fail(problems, "index.html loads no script")

    # 2. The stylesheet is a real Tailwind build.
    for rel in css:
        path = os.path.join(bundle, rel)
        if not os.path.isfile(path):
            continue
        text = open(path, encoding="utf-8", errors="replace").read()
        size = os.path.getsize(path)
        m = TAILWIND_DIRECTIVE.search(text)
        if m:
            _fail(problems,
                  "%s still contains the literal directive %r - PostCSS did not "
                  "run, so every utility class in the app resolves to nothing "
                  "(this is the 393-byte failure, and it exits 0)"
                  % (rel, m.group(0)))
        if size < MIN_CSS_BYTES:
            _fail(problems,
                  "%s is %d bytes; a real build is ~27,000. Under %d means the "
                  "content globs in tailwind.config.js matched nothing."
                  % (rel, size, MIN_CSS_BYTES))

    # 3. The script is the app, not a stub.
    for rel in js:
        path = os.path.join(bundle, rel)
        if os.path.isfile(path) and os.path.getsize(path) < MIN_JS_BYTES:
            _fail(problems, "%s is %d bytes; the app bundle is ~345,000"
                            % (rel, os.path.getsize(path)))

    # 4. No stale assets. Every build writes new content-hashed names, so
    #    anything index.html does not reference is a previous build's corpse.
    on_disk = {"assets/" + n for n in os.listdir(assets)
               if os.path.isfile(os.path.join(assets, n))}
    stale = sorted(on_disk - referenced)
    if stale:
        _fail(problems,
              "%d stale asset(s) in %s that index.html does not reference: %s%s. "
              "Vite's emptyOutDir cleans dist/, not web_bundle/ - the publish "
              "step has to clear assets/ before copying."
              % (len(stale), assets, ", ".join(os.path.basename(s) for s in stale[:6]),
                 " ..." if len(stale) > 6 else ""))

    # 5. The service worker has a version to bump.
    sw = os.path.join(bundle, "sw.js")
    if not os.path.isfile(sw):
        _fail(problems, "sw.js is missing from the bundle")
    elif not re.search(r'VERSION\s*=\s*"[^"]+"', open(sw, encoding="utf-8").read()):
        _fail(problems, "sw.js has no VERSION constant to bump, so returning "
                        "users keep the old cached bundle")
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", default="web_bundle")
    args = ap.parse_args()

    problems = verify(args.bundle)
    if problems:
        print("FAIL: %s is not a usable build" % args.bundle)
        for p in problems:
            print("  - %s" % p)
        return 1
    print("OK: %s verified" % args.bundle)
    return 0


if __name__ == "__main__":
    sys.exit(main())
