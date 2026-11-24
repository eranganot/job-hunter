"""C12 (2026-11-24): the eight UAT persona CVs exist, read as text, and stay on the test domain."""
import json
import os

from pypdf import PdfReader

D = os.path.join(os.path.dirname(__file__), "fixtures", "personas")


def test_personas():
    meta = json.load(open(os.path.join(D, "personas.json")))
    assert len(meta) == 8 and len({m["family"] for m in meta}) == 8
    for m in meta:
        assert m["email"].endswith("@job-hunter.test")
        text = PdfReader(os.path.join(D, m["cv"])).pages[0].extract_text()
        assert m["name"] in text and m["title"] in text
        assert m["expect_titles"] and m["must_not_see"]
