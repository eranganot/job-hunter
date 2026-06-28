"""
Schema/normalization tests: Big-Tech endpoint payload vs aggregator payload both
normalize into the same unified shape, and tier gating works.
"""
import pytest

pytest.importorskip("pydantic")

from ingestion.models import (
    NormalizedJob, CanonicalJob, JobSource, SourceTier, ApplyType,
    canonical_url, normalize_title, normalize_company, infer_apply_type,
)
from ingestion.registry import build_adapters, is_admin


def test_canonical_url_strips_tracking_and_www():
    a = canonical_url("https://WWW.Greenhouse.io/acme/jobs/5?utm_source=li&gh_src=x")
    b = canonical_url("https://greenhouse.io/acme/jobs/5/")
    assert a == b


def test_normalize_title_removes_noise():
    assert normalize_title("Sr. Product Manager (Tel Aviv) - Req #12345") == \
           normalize_title("Senior Product Manager".replace("Senior", "Sr"))  # sanity
    assert "tel aviv" not in normalize_title("PM (Tel Aviv)")


def test_normalize_company_drops_legal_suffix():
    assert normalize_company("SimilarWeb Ltd.") == normalize_company("Similarweb")


def test_apply_type_classification():
    assert infer_apply_type("https://boards.greenhouse.io/x/jobs/1") == ApplyType.DIRECT_ATS
    assert infer_apply_type("https://www.linkedin.com/jobs/view/1") == ApplyType.MANUAL_REQUIRED
    assert infer_apply_type("https://acme.com/careers/pm") == ApplyType.DIRECT_ATS


def test_bigtech_payload_normalizes_into_unified_shape():
    # shape mimicking the Microsoft endpoint mapping
    job = NormalizedJob(
        title="Principal Product Manager",
        company="Microsoft",
        location="Herzliya, Israel",
        url="https://jobs.careers.microsoft.com/global/en/job/167788",
        full_description="Build cloud products.",
        source=JobSource.MICROSOFT,
        source_tier=SourceTier.FREE,
        apply_type=ApplyType.DIRECT_ATS,
        source_job_id="167788",
    )
    legacy = CanonicalJob.from_normalized(job).to_legacy_dict()
    assert legacy["job_title"] == "Principal Product Manager"
    assert legacy["company"] == "Microsoft"
    assert legacy["_apply_type"] == "direct_ats"


def test_aggregator_payload_normalizes_into_same_shape():
    job = NormalizedJob(
        title="Senior PM",
        company="Acme",
        location="Remote",
        url="https://api.theirstack.com/job/9",
        full_description="Tech stack: python, react.",
        source=JobSource.THEIRSTACK,
        source_tier=SourceTier.PAID,
        apply_type=ApplyType.UNKNOWN,
    )
    legacy = CanonicalJob.from_normalized(job).to_legacy_dict()
    # identical key set to the big-tech one above → unified schema
    assert set(legacy.keys()) >= {
        "job_title", "company", "location", "url",
        "description", "full_description", "source", "publish_date",
    }


def test_from_legacy_dict_roundtrip():
    d = {"job_title": "Group PM", "company": "Monday", "location": "Tel Aviv",
         "url": "https://boards.greenhouse.io/monday/jobs/55",
         "description": "x", "full_description": "xx", "source": "greenhouse"}
    nj = NormalizedJob.from_legacy_dict(d)
    assert nj.title == "Group PM"
    assert nj.source == JobSource.GREENHOUSE
    assert nj.apply_type == ApplyType.DIRECT_ATS


def test_tier_gating_admin_vs_user():
    user_adapters = build_adapters("user")
    admin_adapters = build_adapters("admin")
    user_sources = {a.name for a in user_adapters}
    admin_sources = {a.name for a in admin_adapters}
    # free big-tech available to everyone
    assert JobSource.MICROSOFT in user_sources
    # paid sources gated out for normal users, present for admin
    assert JobSource.THEIRSTACK not in user_sources
    assert JobSource.THEIRSTACK in admin_sources
    assert JobSource.APIFY in admin_sources
    assert len(admin_adapters) > len(user_adapters)


def test_is_admin():
    assert is_admin("admin") and is_admin("ADMIN ")
    assert not is_admin("user") and not is_admin(None)


def test_paid_adapter_without_creds_skips_cleanly():
    from ingestion.adapters import TheirStackAdapter
    from ingestion.models import SearchQuery
    res = TheirStackAdapter().fetch(SearchQuery(titles=["PM"]))
    # no THEIRSTACK_API_KEY in test env → graceful skip, not a crash
    assert res.status.value == "skipped_no_creds"
    assert res.count == 0
