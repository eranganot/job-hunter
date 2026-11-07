"""Tests for feedback decay (#18) and location capture/penalty (#17).

Uses the real schema via db.init_db() on a temp file so the rejected_patterns
.location migration column exists.
"""
import datetime as _dt
import pytest
import db as database
from ai_analysis import compute_feedback_penalty


@pytest.fixture
def uid(tmp_path):
    database.set_db_path(str(tmp_path / "t.db"))
    database.init_db()
    conn = database.get_db()
    conn.execute(
        "INSERT INTO users (name,email,password_hash,salt) VALUES (?,?,?,?)",
        ("T", "t@e.com", "h", "s"),
    )
    conn.commit()
    u = conn.execute("SELECT id FROM users WHERE email=?", ("t@e.com",)).fetchone()[0]
    conn.close()
    return u


def _add(uid, company, title="PM", notes="", location=None, age_days=0):
    created = (_dt.datetime.now() - _dt.timedelta(days=age_days)).isoformat()
    conn = database.get_db()
    conn.execute(
        "INSERT INTO rejected_patterns (user_id,company,title,notes,location,created_date) VALUES (?,?,?,?,?,?)",
        (uid, company, title, notes, location, created),
    )
    conn.commit()
    conn.close()


def _signals(uid):
    conn = database.get_db()
    try:
        return database.get_feedback_signals(conn, uid)
    finally:
        conn.close()


class TestDecay:
    def test_recent_passes_weigh_more_than_old(self, uid):
        _add(uid, "FreshCo", age_days=1)
        _add(uid, "FreshCo", age_days=2)
        _add(uid, "StaleCo", age_days=400)
        pc = _signals(uid)["passed_companies"]
        assert pc["freshco"] > 1.5
        assert pc["staleco"] < 0.2

    def test_old_pass_no_longer_penalizes_but_recent_does(self, uid):
        _add(uid, "StaleCo", age_days=400)
        pen, _ = compute_feedback_penalty({"company": "StaleCo", "title": "PM"}, _signals(uid))
        assert pen == 0
        _add(uid, "HotCo", age_days=1)
        _add(uid, "HotCo", age_days=1)
        pen2, _ = compute_feedback_penalty({"company": "HotCo", "title": "PM"}, _signals(uid))
        assert pen2 >= 20


class TestLocation:
    def test_location_only_penalizes_with_location_reason(self, uid):
        _add(uid, "A", notes="Wrong seniority level", location="Berlin, Germany", age_days=1)
        _add(uid, "B", notes="Wrong seniority level", location="Berlin, Germany", age_days=1)
        assert not _signals(uid)["disliked_locations"]
        _add(uid, "C", notes="Wrong location", location="Berlin, Germany", age_days=1)
        _add(uid, "D", notes="Wrong location", location="Berlin, Germany", age_days=1)
        sig = _signals(uid)
        assert any("berlin" in l for l in sig["disliked_locations"])
        pen, reason = compute_feedback_penalty(
            {"company": "Z", "title": "PM", "location": "Berlin, Germany"}, sig
        )
        assert pen >= 10 and "Location" in reason


# ── The location penalty must not fight the user's own preferred city ────────
#
# Found on staging 2026-09-15: a 97% Tel Aviv role carried the badge
# "Location you've passed on" while Tel Aviv was the user's FIRST preferred
# location. The comment above that branch claimed it "never fights their own
# preferred city"; nothing checked. The title branch two blocks up does guard
# against the user's own targets - the location branch never did.

def _loc_signals(disliked):
    """A hand-built signal bundle. Deliberately NOT the module's _signals(uid),
    which reads real rows - these tests are about the penalty's arithmetic, not
    about how signals are gathered."""
    return {"bad_companies": set(), "passed_companies": {},
            "disliked_title_tokens": set(), "disliked_locations": set(disliked)}


def test_a_job_in_a_preferred_location_is_not_demoted_for_its_location():
    from ai_analysis import compute_feedback_penalty
    job = {"company": "Acme", "title": "VP Product", "location": "Tel Aviv, Israel"}
    profile = {"locations": '["Tel Aviv", "Remote"]', "job_titles": '["VP Product"]'}
    penalty, reason = compute_feedback_penalty(job, _loc_signals({"tel aviv, israel"}), profile)
    assert penalty == 0, (penalty, reason)
    assert "Location" not in reason


def test_a_job_outside_the_preferred_locations_is_still_demoted():
    """The guard must not switch the penalty off altogether - a location the
    user keeps passing on, and never asked for, should still rank lower."""
    from ai_analysis import compute_feedback_penalty
    job = {"company": "Acme", "title": "VP Product", "location": "Haifa, Israel"}
    profile = {"locations": '["Tel Aviv"]', "job_titles": '["VP Product"]'}
    penalty, reason = compute_feedback_penalty(job, _loc_signals({"haifa"}), profile)
    assert penalty == 10, (penalty, reason)
    assert reason == "Location you've passed on"


def test_with_no_stated_locations_the_old_behaviour_stands():
    """A user who never said where they want to work has no preference for the
    guard to protect, so the signal is all we have."""
    from ai_analysis import compute_feedback_penalty
    job = {"company": "Acme", "title": "VP Product", "location": "Tel Aviv, Israel"}
    penalty, _r = compute_feedback_penalty(job, _loc_signals({"tel aviv"}), {"locations": None})
    assert penalty == 10


class TestAppliedElsewhere:
    """Eran, 2026-11-05: "if I click on already applied elsewhere -> learn that
    this company interests me and do not pass on this company"."""

    def test_it_is_interest_not_a_pass(self, uid):
        for _ in range(3):
            _add(uid, "Wix", title="Senior Product Manager", notes="Already applied elsewhere",
                 location="Tel Aviv", age_days=1)
        sig = _signals(uid)
        assert "wix" in sig["interested_companies"]
        assert "wix" not in sig["passed_companies"]
        assert not sig["disliked_title_tokens"]
        assert not sig["examples"]

    def test_the_company_is_never_demoted_even_after_real_passes(self, uid):
        _add(uid, "Wix", notes="Not a good fit", age_days=1)
        _add(uid, "Wix", notes="Not a good fit", age_days=1)
        pen, why = compute_feedback_penalty({"company": "Wix", "title": "PM"}, _signals(uid))
        assert pen == 20 and "passed on this company" in why          # the old behaviour
        _add(uid, "Wix", notes="Already applied elsewhere", age_days=1)
        pen, why = compute_feedback_penalty({"company": "Wix", "title": "PM"}, _signals(uid))
        assert pen == 0 and "passed" not in why

    def test_bad_company_still_wins(self, uid):
        _add(uid, "Acme", notes="Already applied elsewhere", age_days=1)
        _add(uid, "Acme", notes="Bad company", age_days=1)
        pen, _ = compute_feedback_penalty({"company": "Acme", "title": "PM"}, _signals(uid))
        assert pen == 45

    def test_the_ai_is_told_it_is_a_company_he_wants(self, uid):
        from ai_analysis import _feedback_notes
        _add(uid, "Wix", notes="Already applied elsewhere", age_days=1)
        conn = database.get_db()
        try:
            database.record_pass_reason_stat(conn, uid, "Already applied elsewhere")
        finally:
            conn.close()
        sig = _signals(uid)
        assert "Already applied elsewhere" not in sig["reason_counts"]
        notes = _feedback_notes(sig)
        assert "applied to on their own" in notes and "wix" in notes
        assert "rejected: " not in notes


class TestSalaryAtPass:
    """Step 13: "Salary too low" keeps the job's stated salary; jobs paying no
    more than that (same currency) are demoted. Unstated salaries: no effect."""

    def _pass_with_salary(self, uid, monthly, currency, age_days=1, notes="Salary too low"):
        created = (_dt.datetime.now() - _dt.timedelta(days=age_days)).isoformat()
        conn = database.get_db()
        conn.execute("INSERT INTO rejected_patterns (user_id,company,title,notes,created_date,"
                     "salary_monthly,salary_currency) VALUES (?,?,?,?,?,?,?)",
                     (uid, "LowPay", "PM", notes, created, monthly, currency))
        conn.commit()
        conn.close()

    def test_the_floor_demotes_jobs_paying_no_more(self, uid):
        self._pass_with_salary(uid, 28000, "ILS")
        sig = _signals(uid)
        assert sig["salary_floor"] == {"ILS": 28000.0}
        low = {"company": "Acme", "title": "PM", "description": "Salary ₪25,000-27,000 per month"}
        high = {"company": "Acme", "title": "PM", "description": "Salary ₪40,000-45,000 per month"}
        none = {"company": "Acme", "title": "PM", "description": "Great team, Tel Aviv"}
        usd = {"company": "Acme", "title": "PM", "description": "Salary $5,000 - $6,000 per month"}
        assert compute_feedback_penalty(low, sig) == (15, "Pays below your salary floor")
        assert compute_feedback_penalty(high, sig)[0] == 0
        assert compute_feedback_penalty(none, sig)[0] == 0
        assert compute_feedback_penalty(usd, sig)[0] == 0          # other currency: not compared

    def test_old_and_other_reasons_do_not_set_a_floor(self, uid):
        self._pass_with_salary(uid, 28000, "ILS", age_days=400)
        self._pass_with_salary(uid, 50000, "ILS", notes="Not a good fit")
        assert _signals(uid)["salary_floor"] == {}

    def test_the_profile_minimum_is_a_floor_too(self, uid):
        conn = database.get_db()
        conn.execute("INSERT INTO application_answers (user_id, salary_expectation_min, "
                     "salary_expectation_currency) VALUES (?,?,?)", (uid, 45000, "ILS"))
        conn.commit()
        conn.close()
        sig = _signals(uid)
        job = {"company": "Acme", "title": "PM", "description": "Salary ₪40,000-44,000 per month"}
        at_min = {"company": "Acme", "title": "PM", "description": "Salary ₪40,000-45,000 per month"}
        assert compute_feedback_penalty(job, sig)[0] == 15
        assert compute_feedback_penalty(at_min, sig)[0] == 0         # reaches his minimum
