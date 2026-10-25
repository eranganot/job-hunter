#!/usr/bin/env python3
"""
build_web.py - build the SPA, verify it, and publish it into web_bundle/.
Python port of scripts/build_web.ps1 (2026-10-25), for building from the Mac or
the cloud workspace that opens the PRs.

Railway does NOT build the frontend: web_bundle/ is committed and served as-is,
so a broken build is not a red deploy, it is a live unstyled app (it happened:
a 393-byte stylesheet of literal "@tailwind" lines, and Vite exited 0). Hence:
build, assert, publish, assert again. scripts/verify_web_bundle.py holds the
assertions and the test suite runs it too.

Verified 2026-10-25: a build of unchanged web/ from the cloud workspace
reproduced the committed bundle byte for byte (same content-hashed names), so
builds from here do not ship unrelated differences.

Usage:
    python3 scripts/build_web.py              # bump SW version, build, publish, verify
    python3 scripts/build_web.py --no-bump    # leave the SW version alone
    python3 scripts/build_web.py --verify-only
    python3 scripts/build_web.py --clean-install
"""
import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WEB = REPO / "web"
BUNDLE = REPO / "web_bundle"
SW_SRC = WEB / "public" / "sw.js"


def fail(msg):
    print(f"FAIL: {msg}")
    sys.exit(1)


def ok(msg):
    print(f"OK:   {msg}")


def verify():
    r = subprocess.run([sys.executable, str(REPO / "scripts" / "verify_web_bundle.py"), "--bundle", str(BUNDLE)],
                       cwd=str(REPO))
    return r.returncode == 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-bump", action="store_true")
    ap.add_argument("--verify-only", action="store_true")
    ap.add_argument("--clean-install", action="store_true")
    a = ap.parse_args(argv)

    if not (WEB / "package.json").exists():
        fail("web/package.json not found")
    if a.verify_only:
        if not verify():
            fail("the committed bundle does not pass verification.")
        ok("committed bundle verified")
        return 0

    if not a.no_bump:
        sw = SW_SRC.read_text()
        m = re.search(r'VERSION\s*=\s*"jh-v(\d+)"', sw)
        if not m:
            fail('could not find a VERSION = "jh-vN" line in web/public/sw.js')
        nxt = int(m.group(1)) + 1
        SW_SRC.write_text(re.sub(r'VERSION\s*=\s*"jh-v\d+"', f'VERSION = "jh-v{nxt}"', sw, count=1))
        ok(f"service worker cache: jh-v{m.group(1)} -> jh-v{nxt}")

    if a.clean_install or not (WEB / "node_modules").exists():
        if subprocess.run(["npm", "ci", "--no-audit", "--no-fund"], cwd=str(WEB)).returncode != 0:
            fail("npm ci failed.")
        ok("dependencies installed")

    if subprocess.run(["npm", "run", "build"], cwd=str(WEB)).returncode != 0:
        fail("the build failed.")
    if not (WEB / "dist" / "index.html").exists():
        fail("the build produced no dist/index.html.")
    ok("built")

    assets = BUNDLE / "assets"
    if assets.exists():
        n = len([p for p in assets.iterdir() if p.is_file()])
        shutil.rmtree(assets)
        print(f"      cleared {n} old asset file(s)")
    BUNDLE.mkdir(exist_ok=True)
    shutil.copytree(WEB / "dist", BUNDLE, dirs_exist_ok=True)
    ok("published to web_bundle/")

    if not verify():
        fail("the published bundle did not pass verification - do NOT commit it.")
    ok("verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
