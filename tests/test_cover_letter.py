"""Cover letters written from the full CV, with a number check (Eran, 2026-11-08:
"should be written more accurate and in more human way")."""
from ai_analysis import write_cover_letter

CV = ("ERAN GANOT\nDAZN TLV, Israel\nHead of Product 2025-Present\n"
      "Spearheaded personalization - boosting engagement +22% and retention +36%\n"
      "Executive MBA from Columbia Business School\n")
JOB = {"title": "Director of Product", "company": "Payoneer",
       "description": "Own onboarding for 2,000,000 businesses. Experimentation and data. " * 3}


def test_the_letter_is_written_from_the_full_cv_and_the_users_name():
    seen = []
    out = write_cover_letter(JOB, {"cv_summary": "short summary"}, cv_text=CV, name="Eran Ganot",
                             llm=lambda p: seen.append(p) or "Dear Hiring Team,\nRetention rose 36% at DAZN.\nBest regards,\nEran Ganot")
    assert "DAZN TLV, Israel" in seen[0] and "short summary" not in seen[0]
    assert "from Eran Ganot" in seen[0] and "No em dashes" in seen[0]
    assert len(seen) == 1 and out["notes"] == []                     # 36 is in the CV: no rewrite


def test_an_invented_number_gets_one_rewrite_naming_it():
    seen = []
    drafts = ["Revenue grew 40% under me.", "Retention rose 36%, and 2,000,000 businesses is a scale I know."]
    out = write_cover_letter(JOB, {}, cv_text=CV, name="Eran", llm=lambda p: seen.append(p) or drafts[len(seen) - 1])
    assert len(seen) == 2 and "40" in seen[1].split("Your previous draft")[1]
    assert out["letter"].startswith("Retention rose 36%") and out["notes"] == []   # job's own number is fine


def test_a_number_that_survives_the_rewrite_is_removed_and_named():
    out = write_cover_letter(JOB, {}, cv_text=CV, name="Eran",
                             llm=lambda p: "I led 12 squads. Retention rose 36% at DAZN.")
    assert out["letter"] == "Retention rose 36% at DAZN."
    assert out["notes"] and "12 squads" in out["notes"][0]


def test_a_word_cv_is_read_with_its_tables_and_header(tmp_path):
    """Roles in a table were invisible to the tailored CV and the letter (2026-11-08)."""
    from docx import Document
    import app as A
    d = Document()
    d.add_paragraph("Product leader with a long and detailed career summary. " * 3)
    t = d.add_table(rows=1, cols=2)
    t.cell(0, 0).text = "Head of Product - DAZN"
    t.cell(0, 1).text = "2025-Present"
    d.sections[0].header.paragraphs[0].text = "eran@example.test"
    d.add_paragraph("Product Group Lead - Moon Active (2023-2025)")
    path = tmp_path / "cv.docx"
    d.save(path)
    text = A._extract_cv_text(str(path), "")
    assert "DAZN" in text and "2025-Present" in text and "eran@example.test" in text
    assert text.index("DAZN") < text.index("Moon Active")
