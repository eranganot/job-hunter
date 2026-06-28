"""
Verification for the cross-platform fuzzy deduplication layer.

The headline test (`test_same_role_across_three_platforms`) is the requirement
made concrete: the SAME product role posted on a Greenhouse career page,
mirrored on LinkedIn, and scraped via an aggregator must collapse to ONE
canonical job — so the AI scorer sees it once.
"""
import pytest

pytest.importorskip("pydantic")

from ingestion.models import NormalizedJob, JobSource, SourceTier, SourceCategory, ApplyType
from ingestion.dedup import Deduplicator
from ingestion.integration import deduplicate_raw


def _job(title, company, url, source, desc="", cat=SourceCategory.ATS,
         tier=SourceTier.FREE, apply=ApplyType.DIRECT_ATS, loc="Tel Aviv"):
    return NormalizedJob(
        title=title, company=company, url=url, location=loc,
        description=desc, full_description=desc,
        source=source, source_tier=tier, source_category=cat, apply_type=apply,
    )


def test_same_role_across_three_platforms_collapses_to_one():
    jobs = [
        # 1) company Greenhouse board (auto-submittable ATS URL)
        _job("Senior Product Manager",
             "SimilarWeb",
             "https://boards.greenhouse.io/similarweb/jobs/12345",
             JobSource.GREENHOUSE,
             desc="Own the product roadmap for our analytics suite. 7+ years PM."),
        # 2) mirrored on LinkedIn — different URL, noisier title, manual-apply
        _job("Sr. Product Manager (Tel Aviv)",
             "Similarweb",
             "https://www.linkedin.com/jobs/view/987654321",
             JobSource.LINKEDIN,
             desc="Own the product roadmap for our analytics suite.",
             cat=SourceCategory.BOARD, apply=ApplyType.MANUAL_REQUIRED),
        # 3) via a paid aggregator — yet another URL, legal-suffix company name
        _job("Senior Product Manager",
             "SimilarWeb Ltd.",
             "https://api.theirstack.com/job/abc",
             JobSource.THEIRSTACK,
             desc="Roadmap ownership for analytics products.",
             cat=SourceCategory.AGGREGATOR, tier=SourceTier.PAID,
             apply=ApplyType.UNKNOWN),
    ]
    canon = Deduplicator().merge(jobs)
    assert len(canon) == 1, f"expected 1 canonical job, got {len(canon)}"
    c = canon[0]
    assert c.duplicate_count == 3
    # canonical record should prefer the auto-submittable ATS URL
    assert "greenhouse.io" in c.url
    assert c.apply_type == ApplyType.DIRECT_ATS
    # provenance remembers ALL three platforms
    seen = {p.source for p in c.sources}
    assert seen == {JobSource.GREENHOUSE, JobSource.LINKEDIN, JobSource.THEIRSTACK}


def test_referral_tag_only_difference_is_deduped():
    jobs = [
        _job("Group Product Manager", "Monday",
             "https://boards.greenhouse.io/monday/jobs/55?utm_source=linkedin&gh_src=abc",
             JobSource.GREENHOUSE),
        _job("Group Product Manager", "Monday",
             "https://boards.greenhouse.io/monday/jobs/55",
             JobSource.LINKEDIN, cat=SourceCategory.BOARD),
    ]
    canon = Deduplicator().merge(jobs)
    assert len(canon) == 1
    assert canon[0].duplicate_count == 2


def test_different_roles_same_company_are_kept_separate():
    jobs = [
        _job("Senior Product Manager", "Wix",
             "https://boards.greenhouse.io/wix/jobs/1", JobSource.GREENHOUSE),
        _job("Senior Data Engineer", "Wix",
             "https://boards.greenhouse.io/wix/jobs/2", JobSource.GREENHOUSE),
        _job("VP Marketing", "Wix",
             "https://boards.greenhouse.io/wix/jobs/3", JobSource.GREENHOUSE),
    ]
    canon = Deduplicator().merge(jobs)
    assert len(canon) == 3


def test_same_title_different_companies_not_merged():
    jobs = [
        _job("Product Manager", "Gong",
             "https://jobs.lever.co/gong/1", JobSource.LEVER),
        _job("Product Manager", "Taboola",
             "https://jobs.lever.co/taboola/1", JobSource.LEVER),
    ]
    canon = Deduplicator().merge(jobs)
    assert len(canon) == 2


def test_canonical_prefers_richest_description():
    short = _job("Staff Product Manager", "Payoneer",
                 "https://www.linkedin.com/jobs/view/1", JobSource.LINKEDIN,
                 desc="PM role.", cat=SourceCategory.BOARD,
                 apply=ApplyType.MANUAL_REQUIRED)
    rich_text = "Lead payments product. " * 50
    rich = _job("Staff Product Manager", "Payoneer",
                "https://boards.greenhouse.io/payoneer/jobs/9", JobSource.GREENHOUSE,
                desc=rich_text)
    canon = Deduplicator().merge([short, rich])
    assert len(canon) == 1
    assert len(canon[0].full_description) > 100   # kept the rich description


def test_deduplicate_raw_on_legacy_dicts():
    """The app.py-facing wrapper works on legacy dicts and collapses dupes."""
    all_raw = [
        {"job_title": "Senior Product Manager", "company": "SimilarWeb",
         "url": "https://boards.greenhouse.io/similarweb/jobs/12345",
         "description": "Roadmap ownership.", "source": "greenhouse"},
        {"job_title": "Sr. Product Manager (Tel Aviv)", "company": "Similarweb",
         "url": "https://www.linkedin.com/jobs/view/987",
         "description": "Roadmap ownership.", "source": "linkedin"},
    ]
    out = deduplicate_raw(all_raw)
    assert len(out) == 1
    assert out[0]["_duplicate_count"] == 2
    # the legacy shape app.py expects is intact
    assert "job_title" in out[0] and "company" in out[0] and "url" in out[0]


def test_empty_and_single_inputs():
    assert Deduplicator().merge([]) == []
    one = [_job("PM", "X", "https://x.co/1", JobSource.GREENHOUSE)]
    assert len(Deduplicator().merge(one)) == 1


def test_scale_blocking_keeps_it_linear_enough():
    # 300 distinct jobs across 30 companies should stay 300 after dedup
    jobs = []
    for c in range(30):
        for n in range(10):
            jobs.append(_job(f"Role {n} Engineer", f"Company{c}",
                             f"https://boards.greenhouse.io/c{c}/jobs/{n}",
                             JobSource.GREENHOUSE))
    canon = Deduplicator().merge(jobs)
    assert len(canon) == 300
