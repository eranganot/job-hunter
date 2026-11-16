"""
tests/test_admin_sections.py - the Admin page is five sections under Admin in
the side bar (GTM Stage 2 row 10a, the owner's review of 2026-11-16).

Source checks on web/src/App.tsx: the shape is the decision, and a later edit
that quietly drops a section or moves the instructions back into Admin should
fail here rather than in the owner's hands.
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = open(os.path.join(ROOT, "web", "src", "App.tsx"), encoding="utf-8").read()


def _between(start, end):
    return SRC[SRC.index(start):SRC.index(end, SRC.index(start))]


def test_five_sections_in_order():
    block = _between("const ADMIN_SECTIONS", "];")
    order = [s for s in ('"today"', '"growth"', '"content"', '"users"', '"tools"')]
    pos = [block.index(s) for s in order]
    assert pos == sorted(pos)


def test_sections_live_in_the_side_bar_with_a_phone_fallback():
    assert "ADMIN_SECTIONS.map(([sid, slabel, SIcon])" in SRC            # side bar sub-items
    assert 'aria-label="Admin section"' in SRC                           # phone dropdown


def test_tools_until_launch_then_today():
    fn = _between("function defaultAdminSection", "\n}")
    assert 'LAUNCH_DAY ? "tools" : "today"' in fn and 'const LAUNCH_DAY = "2026-12-01"' in SRC
    assert "sessionStorage.getItem(ADMIN_KEY)" in fn                     # remembered for the visit


def test_content_holds_posts_and_emails():
    panel = SRC[SRC.index("function AdminPanel"):]
    content = panel[panel.index('section === "content"'):]
    content = content[:content.index('section === "users"')]
    assert "<NoasPicksCard" in content and "<LifecycleEmailsCard" in content


def test_today_shows_new_and_total_users():
    panel = SRC[SRC.index("function AdminPanel"):]
    today = panel[panel.index('section === "today"'):panel.index('section === "growth"')]
    assert "New in the last 7 days" in today and "Total users" in today


def test_maintenance_is_last_in_tools():
    panel = SRC[SRC.index("function AdminPanel"):]
    tools = panel[panel.index('section === "tools"'):]
    assert tools.index("Maintenance") > tools.index("<TestAccountCard")


def test_application_instructions_left_admin_for_settings():
    panel = SRC[SRC.index("function AdminPanel"):]
    panel = panel[:panel.index("\n}\n")]
    assert "<ApplicationInstructions" not in panel
    assert "{me?.auto_apply_live && <ApplicationInstructions />}" in SRC


def test_the_posting_plan_says_when_and_where():
    plan = _between("function postingPlan", "\n}\n")
    for where in ("Job Hunter page on Facebook", "General job groups on Facebook",
                  "Job Hunter page on LinkedIn"):
        assert where in plan
    card = _between("function NoasPicksCard", "function TestAccountCard")
    assert "This week's plan" in card and "postingPlan(" in card
