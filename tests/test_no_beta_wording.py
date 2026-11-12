"""Users never see "beta" (2026-11-11): the plan is called Premium, free for a
limited time. "beta" stays the internal plan id and the Admin's plan picker."""
import glob
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _bundle():
    return "".join(open(f, encoding="utf-8").read() for f in glob.glob(os.path.join(ROOT, "web_bundle", "assets", "*.js")))


def test_the_plan_tab_says_premium_free_for_a_limited_time():
    js = _bundle()
    assert "Everything in Premium, free for a limited time" in js
    assert "free while Job Hunter is in beta" not in js and "one of our beta users" not in js


def test_no_user_facing_sentence_in_the_app_source_says_beta():
    src = open(os.path.join(ROOT, "web", "src", "App.tsx"), encoding="utf-8").read()
    # strings shown to people: JSX text between > and <, outside the admin plan picker
    texts = re.findall(r">([^<>{}]*[A-Za-z][^<>{}]*)<", src)
    offenders = [t for t in texts if re.search(r"\bbeta\b", t, re.I) and t.strip() != "Beta"]
    assert offenders == []
