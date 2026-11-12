"""'Why this fits' is shown to the job seeker, so it says "you" (the owner,
2026-11-12: "says 'the candidate' where the app should say 'you'"). Observed on
prod: 503 of 527 stored reasons third person, 0 second person. Fixtures are the
production sentences."""
import pathlib
import sqlite3

import migrations
import textclean
import voice
from voice import second_person

ROOT = pathlib.Path(__file__).resolve().parent.parent

PROD = [
    ("The candidate's experience as Head of Product at Trax Retail, securing ARR renewals, directly aligns with the "
     "retail focus of this role. Their AI/ML expertise and product leadership background are strong matches.",
     "Your experience as Head of Product at Trax Retail, securing ARR renewals, directly aligns with the "
     "retail focus of this role. Your AI/ML expertise and product leadership background are strong matches."),
    ("The candidate has strong product management experience and the location is a good match",
     "You have strong product management experience and the location is a good match"),
    ("Seniority is one level off, as the candidate is an Executive Product Leader, not a Senior Product Manager",
     "Seniority is one level off, as you are an Executive Product Leader, not a Senior Product Manager"),
    ("Excellent match in Location, as the candidate specializes in AI product transformation and operationalization",
     "Excellent match in Location, as you specialize in AI product transformation and operationalization"),
    ("The candidate's experience in AI personalization aligns well, despite the seniority being one level below their target.",
     "Your experience in AI personalization aligns well, despite the seniority being one level below your target."),
    ("This role aligns perfectly with the candidate's target roles, and their extensive experience matches the job requirements.",
     "This role aligns perfectly with your target roles, and your extensive experience matches the job requirements."),
]


def test_production_sentences_say_you():
    for before, after in PROD:
        assert second_person(before) == after


def test_coordinated_verbs_and_untouched_text():
    assert (second_person("The candidate brings strong G2M experience and has led teams.")
            == "You bring strong G2M experience and have led teams.")
    assert second_person("Matched by title and skills") == "Matched by title and skills"
    # no mention of the candidate: "their" is someone else's and stays
    assert second_person("The company grew fast; their office is in TLV.") == "The company grew fast; their office is in TLV."
    assert second_person("") == "" and second_person(None) == ""


def test_every_prompt_that_writes_the_reason_carries_the_rule():
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "citing which dimensions scored highest" not in src
    assert src.count("voice.FIT_REASON_RULE") == 3        # search, rescore, description re-score
    assert "the job seeker, in the second person" in voice.FIT_REASON_RULE


def test_new_jobs_are_stored_in_the_second_person():
    j = textclean.clean_job({"job_title": "PM", "fit_reason": "The candidate's background fits; they have led teams."})
    assert j["fit_reason"] == "Your background fits; you have led teams."


def test_migration_35_rewrites_stored_reasons():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    saved = migrations.MIGRATIONS
    migrations.MIGRATIONS = [m for m in saved if m[0] <= 34]
    try:
        migrations.run(c)
    finally:
        migrations.MIGRATIONS = saved
    c.execute("INSERT INTO users (id, name, email, password_hash, salt, created_date) VALUES (1,'u','u@x.test','h','s','2026')")
    for i, w in enumerate([PROD[0][0], "Matched by title and skills"]):
        c.execute("INSERT INTO jobs (user_id, title, company, url, why_relevant, status, found_date) "
                  "VALUES (1,'PM','Co',?,?,'new','2026-11-12')", ("https://x.test/%d" % i, w))
    c.commit()
    migrations.run(c)
    assert [r[0] for r in c.execute("SELECT why_relevant FROM jobs ORDER BY id")] == [PROD[0][1], "Matched by title and skills"]
