"""Job descriptions shown as raw HTML (the owner, 2026-11-12: "<h2>Role
Overview</h2><p>We are looking for..." under About the role). Fixtures are the
production strings: Ashby raw HTML, Greenhouse escaped HTML, an entity in text,
and the half-stripped "div class=&quot;content-intro&quot; p" that the
board-first Greenhouse reader produced."""
import pathlib
import re
import sqlite3

import migrations
import textclean
from textclean import html_to_text

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_ashby_raw_html_becomes_readable_text():
    raw = ("<h2>Role Overview</h2><p>We are looking for a Vertical Product Manager to own Wonderful's "
           "solutions for retail.</p><h2>What You Will Do</h2><ul><li>Drive the product vision</li>"
           "<li>Own the roadmap</li></ul>")
    assert html_to_text(raw) == ("Role Overview\nWe are looking for a Vertical Product Manager to own "
                                 "Wonderful's solutions for retail.\n\nWhat You Will Do\n"
                                 "• Drive the product vision\n• Own the roadmap")


def test_greenhouse_escaped_html_is_decoded_then_stripped():
    esc = ("&lt;div class=&quot;content-intro&quot;&gt;&lt;p&gt;&lt;strong&gt;About Payoneer&lt;/strong&gt;"
           "&lt;/p&gt;&lt;p&gt;Systems &amp;amp; Automation&lt;/p&gt;&lt;/div&gt;")
    assert html_to_text(esc) == "About Payoneer\nSystems & Automation"


def test_entities_in_plain_text_and_prose_angle_brackets():
    assert html_to_text("As the world&#39;s largest &amp; best") == "As the world's largest & best"
    assert html_to_text("R&D; a < b > c; <3") == "R&D; a < b > c; <3"
    assert html_to_text("Head of CX &amp; Success", keep_lines=False) == "Head of CX & Success"
    assert html_to_text("") == "" and html_to_text(None) == ""


def test_board_first_greenhouse_reader_no_longer_leaves_tag_debris(monkeypatch):
    import boardsearch
    content = ("&lt;div class=&quot;content-intro&quot;&gt;&lt;p&gt;ScaleOps is redefining cloud "
               "infrastructure.&lt;/p&gt;&lt;/div&gt;")
    src = (ROOT / "boardsearch.py").read_text(encoding="utf-8")
    assert 're.sub(r"&lt;|&gt;", " "' not in src
    out = textclean.html_to_text(content)
    assert out == "ScaleOps is redefining cloud infrastructure." and "content-intro" not in out


def test_every_job_insert_cleans_the_text_first():
    """No path may store a job without textclean.clean_job (four INSERTs today)."""
    offenders = []
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith(("tests/", "scripts/")) or rel == "migrations.py":
            continue
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        for i, line in enumerate(lines):
            if re.search(r"INSERT (OR IGNORE )?INTO jobs\b", line):
                window = "\n".join(lines[max(0, i - 12):i])
                if "textclean.clean_job(" not in window:
                    offenders.append("%s:%d" % (rel, i + 1))
    assert offenders == []


def _db(upto):
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    saved = migrations.MIGRATIONS
    migrations.MIGRATIONS = [m for m in saved if m[0] <= upto]
    try:
        migrations.run(c)
    finally:
        migrations.MIGRATIONS = saved
    return c


def test_migration_33_cleans_stored_jobs_and_leaves_clean_ones():
    c = _db(32)
    c.execute("INSERT INTO users (id, name, email, password_hash, salt, created_date) VALUES (1,'u','u@x.test','h','s','2026')")
    rows = [
        ("Vertical PM", "Wonderful", "<p>Short</p>", "<h2>Role Overview</h2><p>Own retail.</p>"),
        ("Senior PM", "Payoneer", "&lt;p&gt;About&lt;/p&gt;", "&lt;p&gt;About&lt;/p&gt;"),
        ("Clean PM", "R&D Co", "Plain text a < b", "Plain text a < b"),
    ]
    for i, (t, co, d, fd) in enumerate(rows):
        c.execute("INSERT INTO jobs (user_id, title, company, url, description, full_description, status, found_date) "
                  "VALUES (1,?,?,?,?,?,'new','2026-11-12')", (t, co, "https://x.test/%d" % i, d, fd))
    c.commit()
    migrations.run(c)
    got = [tuple(r) for r in c.execute("SELECT company, description, full_description FROM jobs ORDER BY id")]
    assert got == [("Wonderful", "Short", "Role Overview\nOwn retail."),
                   ("Payoneer", "About", "About"),
                   ("R&D Co", "Plain text a < b", "Plain text a < b")]
