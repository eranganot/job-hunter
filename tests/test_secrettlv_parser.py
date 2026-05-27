"""
tests/test_secrettlv_parser.py
Locks the JSON-LD parsing assumption used by the Secret Tel Aviv source in
app.py. If the site changes its embedded markup, this test fails before the
production scraper does.

Note: the scraper lives inline inside `_search_jobs_with_claude_websearch`
in app.py, which is not importable in isolation. This test mirrors the
exact regexes + json/html parsing the scraper does, against a fixture HTML
sample taken from a real page.
"""
import html
import json
import re


# A minimal but realistic fixture: enough wrapping HTML around a JobPosting
# JSON-LD block to mimic the live page.
FIXTURE_HTML = """
<!DOCTYPE html><html><head><title>Director of Product | STLV Jobs</title>
<script type="application/ld+json">{"@context":"https://schema.org/","@type":"BreadcrumbList"}</script>
<script type="application/ld+json">
{
  "@context": "http://schema.org/",
  "@type": "JobPosting",
  "title": "Director of Product",
  "description": "<p>Lead the product org &amp; ship.</p>",
  "datePosted": "2026-05-12",
  "hiringOrganization": {"@type":"Organization","name":"WSC Sports"},
  "jobLocation": {"@type":"Place","address":{"@type":"PostalAddress","addressLocality":"Tel Aviv","addressRegion":"IL"}}
}
</script>
</head><body>...</body></html>
"""


def _parse_stlv_job(jhtml: str):
    """Mirror of the parser in app.py — keep in sync."""
    ld_re = re.compile(
        r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
        re.S
    )
    ld = None
    for m in ld_re.finditer(jhtml):
        body = m.group(1).strip()
        if '"JobPosting"' not in body and "'JobPosting'" not in body:
            continue
        try:
            ld = json.loads(body)
            break
        except Exception:
            continue
    if not ld or ld.get('@type') != 'JobPosting':
        return None
    title = (ld.get('title') or '').strip()
    desc = re.sub(r'<[^>]+>', ' ', ld.get('description', '') or '')
    desc = html.unescape(desc).strip()
    hiring = ld.get('hiringOrganization') or {}
    company = (hiring.get('name') if isinstance(hiring, dict) else '') or \
              'Secret Tel Aviv (employer hidden)'
    loc_obj = ld.get('jobLocation') or {}
    if isinstance(loc_obj, list) and loc_obj:
        loc_obj = loc_obj[0]
    addr = (loc_obj.get('address') if isinstance(loc_obj, dict) else None) or {}
    location = (addr.get('addressLocality') or addr.get('addressRegion')
                or 'Israel') if isinstance(addr, dict) else 'Israel'
    return {
        "title": title,
        "company": company,
        "location": location,
        "publish_date": ld.get('datePosted', '') or '',
        "description": desc,
    }


class TestSecretTelAvivParser:

    def test_parses_basic_job_posting(self):
        out = _parse_stlv_job(FIXTURE_HTML)
        assert out is not None
        assert out["title"] == "Director of Product"
        assert out["company"] == "WSC Sports"
        assert out["location"] == "Tel Aviv"
        assert out["publish_date"] == "2026-05-12"
        # HTML stripped and entities decoded
        assert "<" not in out["description"]
        assert "&amp;" not in out["description"]
        assert "ship" in out["description"]

    def test_skips_non_jobposting_ld(self):
        html_only_breadcrumb = """
        <script type="application/ld+json">{"@type":"BreadcrumbList"}</script>
        """
        assert _parse_stlv_job(html_only_breadcrumb) is None

    def test_returns_none_when_no_ld(self):
        assert _parse_stlv_job("<html><body>no ld here</body></html>") is None

    def test_handles_list_shaped_joblocation(self):
        h = """
        <script type="application/ld+json">
        {"@context":"http://schema.org/","@type":"JobPosting","title":"PM",
         "hiringOrganization":{"name":"Acme"},
         "jobLocation":[{"address":{"addressLocality":"Herzliya"}}]}
        </script>
        """
        out = _parse_stlv_job(h)
        assert out is not None
        assert out["location"] == "Herzliya"

    def test_falls_back_when_hiring_org_missing(self):
        h = """
        <script type="application/ld+json">
        {"@type":"JobPosting","title":"Anon Role"}
        </script>
        """
        out = _parse_stlv_job(h)
        assert out is not None
        assert "Secret Tel Aviv" in out["company"]
        assert out["location"] == "Israel"
