""""Noa's picks" posts (GTM Stage 2 row 9, 2026-11-16): this week's new roles per role family,
as ready-to-post text. Every line of a post has to be true: real roles, found this week, with a
company name and a link to the employer's own page."""
import json

import noaspicks
from tests.test_routes import Client, stack, users  # noqa: F401


def _row(title, company, url, found="2026-11-15 08:00:00", **kw):
    return dict({"title": title, "company": company, "url": url, "found_date": found, "location": "Tel Aviv"}, **kw)


def test_titles_land_in_the_right_family():
    fam = lambda t: (noaspicks.family_of(t) or (None,))[0]  # noqa: E731
    assert fam("Senior Product Manager, Growth") == "product"
    assert fam("Product Designer") == "design"                     # not product
    assert fam("Product Marketing Manager") == "marketing"         # not product
    assert fam("Data Engineer") == "data"                          # not software
    assert fam("QA Automation Engineer") == "qa"                   # not software
    assert fam("DevOps Engineer") == "devops"
    assert fam("Senior Backend Developer") == "software" and fam("Full Stack Engineer") == "software"
    assert fam("Customer Success Manager") == "customer"
    assert fam("Office Manager") is None


def test_only_real_employer_links_with_a_company_name_and_one_per_role():
    rows = [
        _row("Senior Product Manager", "Lumen Health", "https://boards.greenhouse.io/lumen/jobs/1"),
        _row("Senior Product Manager", "lumen health", "https://boards.greenhouse.io/lumen/jobs/1?gh_src=x"),   # same role again
        _row("Product Manager", "", "https://boards.greenhouse.io/x/jobs/2"),                                    # no company
        _row("Product Manager", "Orbitly", "https://www.linkedin.com/jobs/view/3"),                              # a job board
        _row("Product Owner", "Kesef Labs", "https://kesef.example/careers/4", status_check="closed"),           # closed
        _row("Group Product Manager", "Tavor", "https://jobs.lever.co/tavor/5", url_verified=0),                 # dead link
        _row("Product Lead", "Alma", "https://alma.example/careers/6"),
        _row("Office Manager", "Alma", "https://alma.example/careers/7"),                                        # no family
    ]
    listed, skipped = noaspicks.pick(rows)
    assert [r["company"] for r in listed["product"]["roles"]] == ["Lumen Health", "Alma"]
    assert skipped == {"no_company": 1, "not_employer_link": 1, "closed": 2, "no_family": 1, "duplicate": 1}


def test_at_most_two_roles_per_company_and_ten_per_post_newest_first():
    rows = [_row("Product Manager %d" % i, "BigCo", "https://bigco.example/careers/%d" % i,
                 found="2026-11-1%d 08:00:00" % (i % 5)) for i in range(6)]
    rows += [_row("Product Manager", "Co %d" % i, "https://co%d.example/careers/1" % i, found="2026-11-10 08:00:00")
             for i in range(12)]
    got = noaspicks.pick(rows)[0]["product"]
    assert got["all"] == 18 and len(got["roles"]) == 10
    assert sum(1 for r in got["roles"] if r["company"] == "BigCo") == 2
    assert got["roles"][0]["found"] == "2026-11-14"


def test_the_posts_say_what_is_true_in_both_languages():
    rows = [_row("Senior Product Manager", "Lumen Health", "https://boards.greenhouse.io/lumen/jobs/1"),
            _row("Product Manager, Growth", "Orbitly", "https://jobs.lever.co/orbitly/2"),
            _row("Product Owner", "Kesef Labs", "https://kesef.example/careers/3"),
            _row("Backend Developer", "Kesef Labs", "https://kesef.example/careers/9")]     # one role: not a post
    out = noaspicks.build(rows, "https://job-hunter.work")
    (f,) = out["families"]
    assert f["key"] == "product" and f["count"] == 3 and out["too_few"] == [{"key": "software", "count": 1}]
    en, he = f["post_en"], f["post_he"]
    assert en.startswith("3 Product Management roles Noa found this week, each on the company's own careers page:")
    assert "• Senior Product Manager, Lumen Health\nhttps://boards.greenhouse.io/lumen/jobs/1" in en
    assert "She works for you, not for the companies." in en and "more this week" not in en
    assert en.endswith("https://job-hunter.work/?utm_source=linkedin&utm_medium=post&utm_campaign=noas_picks&utm_content=product")
    assert he.startswith("3 משרות ניהול מוצר שנועה מצאה השבוע")
    assert "• Senior Product Manager | Lumen Health\nhttps://boards.greenhouse.io/lumen/jobs/1" in he
    assert "היא עובדת בשבילכם, לא בשביל החברות." in he
    assert he.endswith("https://job-hunter.work/?utm_source=facebook&utm_medium=group&utm_campaign=noas_picks&utm_content=product")
    for text in (en, he):                       # found, never "posted": the app does not know the posting date
        assert "posted" not in text.lower() and "פורסמ" not in text


def test_more_than_ten_says_how_many_more():
    rows = [_row("Product Manager", "Co %d" % i, "https://co%d.example/careers/1" % i) for i in range(13)]
    f = noaspicks.build(rows, "https://x.test")["families"][0]
    assert f["count"] == 13 and f["listed"] == 10
    assert "And 3 more this week." in f["post_en"] and "ועוד 3 משרות השבוע." in f["post_he"]


def test_the_admin_gets_this_weeks_posts_and_nobody_else_does(stack, users):  # noqa: F811
    db = stack["db"]
    conn = db.get_db()
    try:
        uid = conn.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()[0]
        for i, (title, company, age) in enumerate([("Senior Product Manager", "Lumen Health", "-1 day"),
                                                   ("Product Manager", "Orbitly", "-2 day"),
                                                   ("Product Owner", "Kesef Labs", "-3 day"),
                                                   ("Product Lead", "Old Co", "-20 day")]):
            conn.execute("INSERT INTO jobs (user_id, title, company, url, status, found_date) "
                         "VALUES (?,?,?,?, 'held', datetime('now', ?))",
                         (uid, title, company, "https://picks%d.example/careers/1" % i, age))
        conn.commit()
    finally:
        conn.close()
    st, _l, body = users["admin"].get("/api/admin/noas-picks")
    out = json.loads(body)
    assert st == 200 and out["days"] == 7
    f = [x for x in out["families"] if x["key"] == "product"][0]
    assert f["count"] == 3 and "Old Co" not in f["post_en"]            # three weeks old: not "this week"
    assert users["a"].get("/api/admin/noas-picks")[0] == 403
    assert Client(stack["port"]).get("/api/admin/noas-picks")[0] in (302, 401)
