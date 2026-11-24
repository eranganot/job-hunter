#!/usr/bin/env python3
"""Eight made-up persona CVs for UAT and E2E tests (2026-11-24, launch item C12).

    python3 scripts/make_persona_cvs.py            # writes tests/fixtures/personas/*.pdf + personas.json

One per job family Job Hunter serves, 3+ years' experience (the campaign's
audience), Israeli tech. Every person, company and contact detail is invented;
emails use the test domain so they never count as real users. The JSON beside
the PDFs says what a good result looks like for each (titles Noa should search,
roles that must NOT show up), so a tester can judge the matches.
"""
import json
import os

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

OUT = os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures", "personas")

PERSONAS = [
    {"key": "product", "name": "Maya Levinson", "title": "Senior Product Manager", "years": 7,
     "city": "Tel Aviv", "family": "Product",
     "summary": "Product manager for B2C subscription apps; owns growth and onboarding.",
     "roles": [("Senior Product Manager", "Brightloop", "2021 - now",
                ["Led onboarding redesign that lifted day-7 retention from 31% to 38%",
                 "Ran 40+ A/B tests a year with a team of 6 engineers and 1 designer"]),
               ("Product Manager", "Cartwheel Labs", "2018 - 2021",
                ["Launched in-app payments used by 120,000 users",
                 "Defined KPIs and dashboards with the data team"])],
     "skills": ["Product strategy", "A/B testing", "SQL", "Amplitude", "Roadmapping", "Jira"],
     "expect_titles": ["Product Manager", "Senior Product Manager", "Group Product Manager"],
     "must_not_see": ["Software Engineer", "QA Engineer", "Account Executive"]},
    {"key": "design", "name": "Noam Shapira", "title": "Senior Product Designer", "years": 6,
     "city": "Tel Aviv", "family": "Design",
     "summary": "Product designer for complex B2B tools; design systems and user research.",
     "roles": [("Senior Product Designer", "Datagrove", "2020 - now",
                ["Built the design system used by 4 product teams",
                 "Ran 30 usability sessions that reshaped the reporting module"]),
               ("UX/UI Designer", "Pixelpine", "2018 - 2020", ["Designed mobile banking flows"])],
     "skills": ["Figma", "Design systems", "User research", "Prototyping", "UX writing"],
     "expect_titles": ["Product Designer", "UX Designer", "UI/UX Designer"],
     "must_not_see": ["Graphic Designer (print)", "Product Manager", "Frontend Developer"]},
    {"key": "marketing", "name": "Tamar Ben-David", "title": "Growth Marketing Manager", "years": 5,
     "city": "Herzliya", "family": "Marketing",
     "summary": "Performance and lifecycle marketer for SaaS; owns paid acquisition budgets.",
     "roles": [("Growth Marketing Manager", "Leadwise", "2021 - now",
                ["Managed a $1.2M yearly paid budget across Google and Meta",
                 "Cut cost per signup by 27% in 2 quarters"]),
               ("Performance Marketing Specialist", "Hopscotch", "2019 - 2021",
                ["Ran lifecycle email programs to 200,000 users"])],
     "skills": ["Google Ads", "Meta Ads", "HubSpot", "Lifecycle", "Attribution", "SQL"],
     "expect_titles": ["Growth Marketing Manager", "Performance Marketing Manager", "Digital Marketing Manager"],
     "must_not_see": ["Sales Development Representative", "Product Manager"]},
    {"key": "data", "name": "Daniel Katz", "title": "Data Scientist", "years": 5,
     "city": "Tel Aviv", "family": "Data",
     "summary": "Data scientist for fraud and risk models; Python, SQL, production ML.",
     "roles": [("Data Scientist", "Securepay", "2020 - now",
                ["Built a fraud model that cut chargebacks by 18%",
                 "Moved 6 models to production with the platform team"]),
               ("Data Analyst", "Shopnest", "2019 - 2020", ["Owned weekly business reviews"])],
     "skills": ["Python", "SQL", "scikit-learn", "XGBoost", "Airflow", "BigQuery"],
     "expect_titles": ["Data Scientist", "Senior Data Scientist", "Machine Learning Engineer"],
     "must_not_see": ["Data Entry", "Product Manager", "QA Engineer"]},
    {"key": "qa", "name": "Ronit Azulay", "title": "QA Automation Engineer", "years": 6,
     "city": "Petah Tikva", "family": "QA",
     "summary": "QA automation engineer for web and API products; owns CI test suites.",
     "roles": [("QA Automation Engineer", "Cloudkeep", "2020 - now",
                ["Built a Playwright suite of 900 tests running in CI",
                 "Cut release regressions by half in one year"]),
               ("QA Engineer", "Medisoft", "2018 - 2020", ["Manual and API testing for clinical software"])],
     "skills": ["Playwright", "Selenium", "Python", "Postman", "CI/CD", "Jira"],
     "expect_titles": ["QA Automation Engineer", "Automation Engineer", "SDET"],
     "must_not_see": ["Software Engineer (backend only)", "Product Manager"]},
    {"key": "software", "name": "Yoav Mizrahi", "title": "Senior Backend Engineer", "years": 8,
     "city": "Tel Aviv", "family": "Software",
     "summary": "Backend engineer for high-traffic APIs; Go and Python, distributed systems.",
     "roles": [("Senior Backend Engineer", "Streamly", "2019 - now",
                ["Owns a payments API serving 3,000 requests a second",
                 "Led the move from a monolith to 12 services"]),
               ("Software Engineer", "Ticketbox", "2016 - 2019", ["Python services and PostgreSQL"])],
     "skills": ["Go", "Python", "PostgreSQL", "Kafka", "Kubernetes", "AWS"],
     "expect_titles": ["Backend Engineer", "Senior Software Engineer", "Backend Developer"],
     "must_not_see": ["QA Engineer", "Product Manager", "Frontend Developer (only)"]},
    {"key": "devops", "name": "Gal Peretz", "title": "DevOps Engineer", "years": 5,
     "city": "Ra'anana", "family": "DevOps",
     "summary": "DevOps engineer for cloud platforms; Terraform, Kubernetes, observability.",
     "roles": [("DevOps Engineer", "Infralink", "2021 - now",
                ["Moved 40 services to Kubernetes on AWS with Terraform",
                 "Cut cloud spend by 22% through rightsizing"]),
               ("System Administrator", "Netwise", "2019 - 2021", ["Linux fleet and on-call"])],
     "skills": ["AWS", "Terraform", "Kubernetes", "Prometheus", "GitHub Actions", "Linux"],
     "expect_titles": ["DevOps Engineer", "Platform Engineer", "Site Reliability Engineer"],
     "must_not_see": ["IT Helpdesk", "Software Engineer (frontend)"]},
    {"key": "sales_cs", "name": "Shira Cohen", "title": "Customer Success Manager", "years": 4,
     "city": "Tel Aviv", "family": "Sales and customer success",
     "summary": "Customer success manager for mid-market SaaS accounts; renewals and expansion.",
     "roles": [("Customer Success Manager", "Teamflow", "2021 - now",
                ["Owns 45 accounts worth $3M ARR with 96% gross retention",
                 "Ran quarterly business reviews with VP-level customers"]),
               ("Account Manager", "Printly", "2020 - 2021", ["Upsold add-ons to SMB customers"])],
     "skills": ["Gainsight", "Salesforce", "Renewals", "Onboarding", "QBRs"],
     "expect_titles": ["Customer Success Manager", "Account Manager", "Senior CSM"],
     "must_not_see": ["Customer Support Agent (entry)", "Software Engineer"]},
]


def build(p, path):
    ss = getSampleStyleSheet()
    h = ParagraphStyle("h", parent=ss["Heading1"], fontSize=18, spaceAfter=2)
    sub = ParagraphStyle("s", parent=ss["Normal"], fontSize=10, textColor="#555555")
    h2 = ParagraphStyle("h2", parent=ss["Heading3"], spaceBefore=10, spaceAfter=4)
    body = ss["Normal"]
    email = "%s@job-hunter.test" % p["name"].lower().replace(" ", ".").replace("-", "")
    s = [Paragraph(p["name"], h),
         Paragraph("%s · %s, Israel · %s · +972-50-000-0000" % (p["title"], p["city"], email), sub),
         Paragraph("Summary", h2), Paragraph("%s %d years of experience." % (p["summary"], p["years"]), body),
         Paragraph("Experience", h2)]
    for title, company, dates, bullets in p["roles"]:
        s.append(Paragraph("<b>%s</b>, %s (%s)" % (title, company, dates), body))
        for b in bullets:
            s.append(Paragraph("• " + b, body))
        s.append(Spacer(1, 6))
    s += [Paragraph("Skills", h2), Paragraph(", ".join(p["skills"]), body),
          Paragraph("Education", h2), Paragraph("B.Sc., Tel Aviv University", body),
          Paragraph("Languages", h2), Paragraph("Hebrew (native), English (fluent)", body)]
    SimpleDocTemplate(path, pagesize=A4, title="%s CV" % p["name"], author=p["name"]).build(s)
    return email


def main():
    os.makedirs(OUT, exist_ok=True)
    meta = []
    for p in PERSONAS:
        path = os.path.join(OUT, "%s.pdf" % p["key"])
        email = build(p, path)
        meta.append({k: p[k] for k in ("key", "name", "title", "years", "city", "family",
                                       "expect_titles", "must_not_see")} | {"email": email, "cv": "%s.pdf" % p["key"]})
    with open(os.path.join(OUT, "personas.json"), "w") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    print("wrote %d persona CVs to %s" % (len(meta), os.path.normpath(OUT)))


if __name__ == "__main__":
    main()
