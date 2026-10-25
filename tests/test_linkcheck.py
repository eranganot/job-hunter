"""linkcheck.classify against the shapes observed live on 2026-10-12.

Every case here is a real URL from Eran's queue that the old 'answers 200'
check called alive. Offline: fetch is injected, nothing touches the network.
"""
import urllib.error

import linkcheck as L


def _f(status=200, final=None, body="<html><body><h1>Senior PM</h1>Apply now</body></html>"):
    def fetch(url, timeout):
        return status, (final or url), body
    return fetch


def _http_error(code):
    def fetch(url, timeout):
        raise urllib.error.HTTPError(url, code, "x", {}, None)
    return fetch


# ── closed postings that answer 200 by redirecting somewhere generic ─────────
def test_comeet_closed_posting_redirects_to_comeet_root():
    v, why, _ = L.classify("https://www.comeet.com/jobs/cynet/54.00A/chief-product-technology-officer/D700000000",
                           fetch=_f(final="https://www.comeet.com/"))
    assert v == "dead" and "redirected" in why


def test_greenhouse_closed_posting_redirects_to_board_error():
    v, _, _ = L.classify("https://job-boards.eu.greenhouse.io/unframe/jobs/4975334101",
                         fetch=_f(final="https://job-boards.eu.greenhouse.io/unframe?error=true"))
    assert v == "dead"


def test_greenhouse_closed_posting_redirects_to_company_careers_home():
    v, _, _ = L.classify("https://boards.greenhouse.io/lightricks/jobs/4320763004",
                         fetch=_f(final="https://careers.lightricks.com/"))
    assert v == "dead"
    v, _, _ = L.classify("https://boards.greenhouse.io/mongodb/jobs/7960937",
                         fetch=_f(final="https://www.mongodb.com/company/careers/teams"))
    assert v == "dead"


# ── closed banners ───────────────────────────────────────────────────────────
def test_linkedin_closed_banner_in_hebrew_is_dead():
    body = ('<html>' + 'x' * 35000 + '<span class="closed-job closed-job__flavor topcard__flavor-row">'
            ' כבר לא מקבלים בקשות </span></html>')
    v, why, _ = L.classify("https://il.linkedin.com/jobs/view/vp-product-at-alictus-4450003803", fetch=_f(body=body))
    assert v == "dead" and "closed" in why


def test_hebrew_closed_phrase_without_markup():
    body = "<html><body><p>תַפְקִיד זה כבר לא מקבלים בקשות</p></body></html>"
    assert L.classify("https://example.co.il/jobs/123", fetch=_f(body=body))[0] == "dead"


def test_live_linkedin_job_is_alive():
    body = '<html><h1 class="top-card-layout__title">Head of Product</h1><button>Apply</button></html>'
    assert L.classify("https://il.linkedin.com/jobs/view/head-of-product-at-chamelio-4475387937",
                      fetch=_f(body=body))[0] == "alive"


def test_secrettelaviv_emptied_listing_is_dead_live_listing_is_alive():
    url = "https://jobs.secrettelaviv.com/job/product-manager-886/"
    shell = "<html><title>Product Manager | Secret Tel Aviv Jobs</title><body>menu menu</body></html>"
    live = "<html><body>Published: July 31, 2026 ... Apply online. Your first name *</body></html>"
    assert L.classify(url, fetch=_f(body=shell))[0] == "dead"
    assert L.classify(url, fetch=_f(body=live))[0] == "alive"


# ── not a posting at all ─────────────────────────────────────────────────────
def test_careers_home_pages_are_generic_without_fetching():
    def boom(url, timeout):
        raise AssertionError("must not fetch a URL that is generic by shape")
    for u in ("https://www.appsflyer.com/careers/", "https://www.monday.com/careers",
              "https://www.tipalti.com/company/careers/", "https://www.salesforce.com/careers",
              "https://boards.greenhouse.io/doubleverify?gh_src=d2113c382", "https://okoora.com/careers"):
        assert L.classify(u, fetch=boom)[0] == "generic", u


def test_posting_urls_are_not_generic():
    for u in ("https://boards.greenhouse.io/riskified/jobs/8591838002",
              "https://www.comeet.com/jobs/skai/22.00A/director-of-ai-enablement/BD.C68",
              "https://www.playtika.com/position/?gh_jid=123",
              "https://welltech.com/careers/vp-senior-director-of-product/",
              "https://jobs.lever.co/acme/1a2b3c",
              "https://apply.workable.com/zyg/j/D1B132170C/"):
        assert not L.is_generic_url(u), u


def test_placeholder_ids_invented_by_the_model():
    assert L.classify("https://www.monday.com/careers/job-details/?id=67890", fetch=_f())[0] != "alive"
    assert L.classify("https://builtin.com/jobs/product/product-manager/senior-ai-product-manager-tel-aviv-isr-123456789",
                      fetch=_f())[0] == "dead"


# ── status codes and the must-not-remove cases ───────────────────────────────
def test_404_and_expired_grounding_redirect_are_dead():
    assert L.classify("https://boards.greenhouse.io/acme/jobs/1", fetch=_http_error(404))[0] == "dead"
    assert L.classify("https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQ",
                      fetch=_http_error(404))[0] == "dead"


def test_bot_blocks_and_rate_limits_are_unknown_never_dead():
    for code in (401, 403, 429, 503):
        assert L.classify("https://il.linkedin.com/jobs/view/x-1", fetch=_http_error(code))[0] == "unknown"


def test_redirect_between_two_postings_is_alive():
    v, _, _ = L.classify("https://www.wix.com/jobs/locations/tel-aviv/positions/director-of-product-management",
                         fetch=_f(final="https://careers.wix.com/location/tel-aviv/positions/director-of-product-management"))
    assert v == "alive"


def test_empty_url_is_dead():
    assert L.classify("")[0] == "dead"


# ── 2026-10-25: a made-up posting id that redirects to a not-found page ──────

def test_redirect_to_a_not_found_page_is_dead():
    # Observed live: wiz.io/careers/job/<made-up id>/... answers 200 after
    # redirecting to /careers/not-found ("We couldn't find the role...").
    url = "https://www.wiz.io/careers/job/1111111111/senior-product-manager"
    v, why, _ = L.classify(url, fetch=_f(200, final="https://www.wiz.io/careers/not-found",
                                          body="<title>We couldn't find the role | Wiz Careers</title>"))
    assert v == "dead" and "not-found" in why


def test_real_wiz_posting_is_still_alive():
    url = "https://www.wiz.io/careers/job/4621534006/:title?gh_jid=4621534006"
    v, _, _ = L.classify(url, fetch=_f(200, body="<title>Applied Data Scientist Expert | Wiz Careers</title>"))
    assert v == "alive"


def test_a_posting_slug_merely_containing_404_digits_is_not_dead():
    url = "https://jobs.lever.co/acme/4041-product-manager"
    v, _, _ = L.classify(url, fetch=_f(200, body="<title>Product Manager</title>"))
    assert v == "alive"
