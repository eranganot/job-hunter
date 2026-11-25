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


ROWS3 = [_row("Senior Product Manager", "Lumen Health", "https://boards.greenhouse.io/lumen/jobs/1"),
         _row("Product Manager, Growth", "Orbitly", "https://jobs.lever.co/orbitly/2"),
         _row("Product Owner", "Kesef Labs", "https://kesef.example/careers/3"),
         _row("Group Product Manager", "Tavor", "https://tavor.example/careers/4"),
         _row("Product Lead", "Alma", "https://alma.example/careers/5"),
         _row("Backend Developer", "Kesef Labs", "https://kesef.example/careers/9")]     # one role: not a post


def _post(f, voice, lang):
    return [p for p in f["posts"] if (p["voice"], p["lang"]) == (voice, lang)][0]


def test_each_family_gets_four_posts_two_voices_two_languages():
    out = noaspicks.build(ROWS3, "https://job-hunter.work", week=0)
    (f,) = out["families"]
    assert f["key"] == "product" and f["listed"] == 5 and out["too_few"] == [{"key": "software", "count": 1}]
    assert [(p["voice"], p["lang"]) for p in f["posts"]] == [("page", "he"), ("page", "en"),
                                                           ("personal", "he"), ("personal", "en")]
    he, en = _post(f, "page", "he")["text"], _post(f, "page", "en")["text"]
    # S3 (2026-11-25): each role links through job-hunter.work/r/<id>
    rid_he = noaspicks.link_id("https://boards.greenhouse.io/lumen/jobs/1", "product", "facebook", "page")
    assert "\u2022 Senior Product Manager | Lumen Health\nhttps://job-hunter.work/r/%s" % rid_he in he
    assert "\u2022 Senior Product Manager, Lumen Health\nhttps://job-hunter.work/r/" in en
    assert "boards.greenhouse.io" not in he
    # each place has its own source tags, so the funnel can tell the page from the owner's profile
    assert he.endswith("?utm_source=facebook&utm_medium=page&utm_campaign=noas_picks&utm_content=product")
    assert en.endswith("?utm_source=linkedin&utm_medium=page&utm_campaign=noas_picks&utm_content=product")
    assert _post(f, "personal", "he")["text"].endswith("utm_source=facebook&utm_medium=group&utm_campaign=noas_picks&utm_content=product")
    assert _post(f, "personal", "en")["text"].endswith("utm_source=linkedin&utm_medium=post&utm_campaign=noas_picks&utm_content=product")


def test_the_page_speaks_as_noa_and_the_owner_speaks_about_her():
    """The owner, 2026-11-16: posted from his own account it must not be Noa's voice; from the
    Job Hunter page it should be. And Noa always says she is an AI."""
    for v in noaspicks.VERSIONS["page"]:
        he, en = v["he_open"] + v["he_close"], v["en_open"] + v["en_close"]
        assert ("מצאתי" in he or "עברתי" in he) and ("I found" in en or "I read" in en), v["key"]     # first person
        assert "AI" in he and "AI" in en, v["key"]
    for v in noaspicks.VERSIONS["personal"]:
        he, en = v["he_open"] + v["he_close"], v["en_open"] + v["en_close"]
        assert "שהיא מצאה" in he and "she found" in en and "בניתי" in he and "I built" in en, v["key"]
        assert "אני נועה" not in he and "I'm Noa" not in en, v["key"]
    for voice in noaspicks.VERSIONS.values():
        for v in voice:                           # found, never "posted": the app does not know the posting date
            for text in v.values():
                assert "posted" not in text.lower() and "פורסמ" not in text
                assert "{" not in text.format(n=10, family="Product Management", family_he="ניהול מוצר")


def test_the_wording_rotates_week_by_week_and_on_request():
    keys = lambda **kw: _post(noaspicks.build(ROWS3, "https://x.test", **kw)["families"][0], "page", "en")["version"]  # noqa: E731
    seen = [keys(week=w) for w in range(6)]
    assert seen[:3] == seen[3:] and len(set(seen[:3])) == 3            # three versions, each in turn
    assert keys(week=0, shift=1) == keys(week=1)                       # "Next wording" = next week's


def test_the_number_in_the_post_is_the_number_listed_never_more_than_ten():
    """The owner, 2026-11-16: "22 picks is too much - how about 10?"."""
    rows = [_row("Product Manager", "Co %d" % i, "https://co%d.example/careers/1" % i) for i in range(13)]
    f = noaspicks.build(rows, "https://x.test", week=0)["families"][0]
    assert f["found"] == 13 and f["listed"] == 10 and noaspicks.LIST_MAX == 10
    for p in f["posts"]:
        assert p["text"].count("\u2022") == 10
        assert "10 " in p["text"].split("\n")[0] and "13" not in p["text"]
        assert "more this week" not in p["text"] and "ועוד" not in p["text"]


def test_the_admin_gets_this_weeks_posts_and_nobody_else_does(stack, users):  # noqa: F811
    db = stack["db"]
    conn = db.get_db()
    try:
        uid = conn.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()[0]
        for i, (title, company, age) in enumerate([("Senior Product Manager", "Lumen Health", "-1 day"),
                                                   ("Product Manager", "Orbitly", "-2 day"),
                                                   ("Product Owner", "Kesef Labs", "-3 day"),
                                                   ("Group Product Manager", "Tavor", "-2 day"),
                                                   ("Product Manager, Growth", "Alma", "-1 day"),
                                                   ("Product Lead", "Old Co", "-20 day")]):
            conn.execute("INSERT INTO jobs (user_id, title, company, url, status, found_date) "
                         "VALUES (?,?,?,?, 'held', datetime('now', ?))",
                         (uid, title, company, "https://%s.example/careers/%d" % (company.split()[0].lower(), i), age))
        conn.commit()
    finally:
        conn.close()
    st, _l, body = users["admin"].get("/api/admin/noas-picks")
    out = json.loads(body)
    assert st == 200 and out["days"] == 7
    f = [x for x in out["families"] if x["key"] == "product"][0]
    assert f["listed"] == 5 and len(f["posts"]) == 4
    assert all("Old Co" not in p["text"] for p in f["posts"])            # three weeks old: not "this week"
    st, _l, body = users["admin"].get("/api/admin/noas-picks?shift=1")
    assert st == 200 and json.loads(body)["families"][0]["posts"][0]["version"] != f["posts"][0]["version"]
    assert users["a"].get("/api/admin/noas-picks")[0] == 403
    assert Client(stack["port"]).get("/api/admin/noas-picks")[0] in (302, 401)


def test_posts_stay_positive_no_not_for_the_companies():
    """The owner, 2026-11-16: the posts end "...she works for you." - without the contrast."""
    import os
    f = noaspicks.build(ROWS3, "https://x.test", week=0)["families"][0]
    for shift in range(3):
        for p in noaspicks.build(ROWS3, "https://x.test", week=0, shift=shift)["families"][0]["posts"]:
            assert "not for the companies" not in p["text"] and "לא בשביל החברות" not in p["text"], p["version"]
    assert any("work for you." in p["text"] or "works for you." in p["text"] for p in f["posts"])
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "web", "src", "App.tsx"), encoding="utf-8").read()
    draw = src[src.index("async function drawPicks("):src.index("function NoasPicksCard")]
    assert "not for the companies" not in draw and "לא בשביל החברות" not in draw
    card = src[src.index("function NoasPicksCard"):src.index("function TestAccountCard")]
    assert "readOnly" not in card and "edits[id] ?? p.text" in card          # the post can be edited before copying


def test_a_link_must_be_the_employers_own():
    """First production run, 2026-11-16: bayitil.com (a directory of companies) passed as
    i-BrainTech's page, and JRG Partners (a recruitment agency) as the employer of a CPO role."""
    yes = [("https://monday.com/careers/d3b5", "monday.com"), ("https://www.comeet.com/jobs/buildots/36", "Buildots"),
           ("https://careers.google.com/jobs/1", "Google"), ("https://www.checkpoint.com/careers/1", "Check Point"),
           ("https://www.amazon.jobs/en/jobs/1", "Amazon"), ("https://www.metacareers.com/jobs/1", "Meta"),
           ("https://www.nice.com/careers/x", "NICE Ltd.")]
    no = [("https://www.bayitil.com/companies/i-braintech", "i-BrainTech"),           # a directory
          ("https://www.jrgpartners.com/jobs/chief-product-officer", "JRG Partners"),   # an agency
          ("https://acme.example/jobs/1", "Talent Recruiting Ltd"),                       # an agency by name
          ("https://www.linkedin.com/jobs/view/3", "Orbitly")]                           # a job board
    assert all(noaspicks.employer_link(u, c) for u, c in yes)
    assert not any(noaspicks.employer_link(u, c) for u, c in no)


def test_a_family_needs_five_roles_for_a_post():
    """The owner, 2026-11-16: below 4 or 5 roles a post looks thin - no post for that family."""
    four = [_row("Product Manager %d" % i, "Co%d" % i, "https://co%d.example/careers/1" % i) for i in range(4)]
    out = noaspicks.build(four, "https://x.test")
    assert out["families"] == [] and out["too_few"] == [{"key": "product", "count": 4}] and out["min_roles"] == 5


def test_the_mixed_post_takes_a_few_from_each_family():
    rows = [_row(t, "%s%d" % (t.split()[0], i), "https://%s%d.example/careers/1" % (t.split()[0].lower(), i))
            for t in ("Product Manager", "Data Analyst", "Backend Developer") for i in range(6)]
    rows += [_row("QA Engineer", "Qa%d" % i, "https://qa%d.example/careers/1" % i) for i in range(2)]   # too few: not mixed in
    out = noaspicks.build(rows, "https://job-hunter.work", week=0)
    m = out["families"][0]
    assert m["key"] == "mixed" and m["listed"] == 10
    assert m["headline_en"] == "10 tech roles" and m["headline_he"] == "10 משרות בהייטק"
    first3 = [r["title"].split()[0] for r in m["roles"][:3]]
    assert sorted(first3) == ["Backend", "Data", "Product"]                     # one from each family in turn
    assert not any(r["title"].startswith("QA") for r in m["roles"])
    assert "10 משרות בהייטק" in _post(m, "page", "he")["text"] and "utm_content=mixed" in _post(m, "page", "he")["text"]
    # one qualifying family: no mixed post (it would be the same as that family's)
    assert noaspicks.build(rows[:6], "https://x.test")["families"][0]["key"] == "product"


def test_the_instagram_caption_names_roles_without_links():
    f = noaspicks.build(ROWS3, "https://x.test", week=0)["families"][0]
    cap = f["instagram"]
    assert "http" not in cap and "\u2022 Senior Product Manager | Lumen Health" in cap
    assert "הקישור להרשמה בביו." in cap and "#דרושים" in cap
    assert "לא בשביל החברות" not in cap


def test_the_story_is_its_own_layout_not_the_square_on_a_taller_canvas():
    """The owner, 2026-11-16: the first Story was the square image with more background around it."""
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "web", "src", "App.tsx"), encoding="utf-8").read()
    story = src[src.index("function drawPicksStory"):src.index("function drawPicksImage")]
    assert 'drawPicks(lang, fam, "story")' in story and "drawImage(square" not in src
    draw = src[src.index("async function drawPicks("):src.index('/** "Noa\'s picks" (noaspicks.py)')]
    assert "H = story ? 1920 : 1080" in draw and "rows: 6" in draw
