# Paid Sources Benchmark v2 — Senior Product Leadership (Israel only)

_Recalibrated to your CV + answers: **Eran Ganot — Head of Product, AI/ML/personalization/data**.
Scope = **Israel only — no remote-global** (Israel-based roles, including Israel-based
hybrid/remote), **leadership + senior IC PM**, **prefer AI/data (not required)**, **price is NOT a
constraint** (you lifted the cap). Researched June 2026._

## What changed from v1 (and why)
v1 recommended Drushim + AllJobs.co.il. **Scrap that for your profile** — those are Hebrew,
mid-market boards; senior English product-leadership roles barely appear there. Your roles live on
**LinkedIn, Indeed, Glassdoor, Wellfound, the company ATSs of tech firms, and technographic
aggregators**. (Note: the Apify actor literally named "All Jobs Scraper" by *agentx* is **not** the
Israeli board — it's a multi-platform English aggregator. Confusing name; great tool.)

---

## TL;DR — recommended stack (build in this order)

1. **agentx/all-jobs-scraper** (Apify) — one actor = LinkedIn + Indeed + Glassdoor + ZipRecruiter +
   ~20 boards, 70+ countries, salary + **remote signals**. Your best single breadth source.
2. **curious_coder/linkedin-jobs-scraper** (Apify) — dedicated LinkedIn with **experience-level**
   (Director/Executive), **remote/hybrid**, and location filters. LinkedIn is where senior product
   roles concentrate; this gives precision the aggregator can't.
3. **TheirStack** (direct API) — **technographic** search: "product-leadership roles at companies
   using ML/LLM/personalization stacks." Perfect for your AI/data preference. Global incl. Israel.
4. **Wellfound** `blackfalcondata/wellfound-scraper` (Apify) — startup/scaleup Head-of-Product / VP
   roles with equity, remote-friendly.
5. Keep the existing **ATS sources** (Greenhouse/Lever/Comeet/Ashby/Workday) — they already cover
   Israeli tech companies' own career boards.

Coresignal is viable but redundant given LinkedIn coverage above — **defer**.

---

## Benchmark table

| Source (actor / API) | What it adds for YOU | Coverage (Israel, English, senior) | Filters that matter | Cost |
|---|---|---|---|---|
| **agentx/all-jobs-scraper** | Breadth in one run: LinkedIn+Indeed+Glassdoor+ZipRecruiter+… | ⭐⭐⭐⭐⭐ query with **location=Israel**; salary + fields | keyword, **location (Israel)**, board selection | **$2.50 / 1,000** |
| **curious_coder/linkedin-jobs-scraper** | LinkedIn depth + seniority precision | ⭐⭐⭐⭐⭐ **location=Israel**; Director/VP/Exec filter | **location (Israel)**, **experience level**, job type, date | **~$1 / 1,000** (some LinkedIn actors $19.99–$29.99/mo) |
| **TheirStack API** | Technographic: AI/ML-stack companies hiring product leaders | ⭐⭐⭐⭐ global incl. Israel; English | job title, country (IL), tech stack, posted-age | $59/mo+ (now in budget) |
| **blackfalcondata/wellfound-scraper** | Startup/scaleup product leadership + equity | ⭐⭐⭐⭐ remote-heavy, English | role filters, salary/equity | ~$2–5 / 1,000 |
| **agentx/glassdoor-scraper** (optional) | Glassdoor depth + company ratings | ⭐⭐⭐ Israel | keyword, location (Israel) | pay-per-result |
| ATS (existing) | Israeli tech companies' own boards | ⭐⭐⭐⭐ Israel | company list (self-updating) | free |
| ~~Drushim / AllJobs.co.il~~ | — | ❌ Hebrew, mid-market — wrong for you | — | — |
| ~~Adzuna~~ | — | ❌ no Israel feed | — | — |

---

## The two big sources, in detail

### A. Apify multi-board aggregator — `agentx/all-jobs-scraper`
- One run pulls **LinkedIn, Indeed, Glassdoor, ZipRecruiter** and ~20 more, **70+ countries** with
  country-aware extraction (set location = Israel). Returns 50+ fields incl. salary, posted date,
  company size/industry, **remote-work signals**, apply URL.
- **$2.50 / 1,000 results.** Up to 10,000 records/run.
- Best for casting the wide English net daily across the boards that matter.

### B. Dedicated LinkedIn — `curious_coder/linkedin-jobs-scraper` (or `valig/…`, `bebity/…`)
- LinkedIn is the #1 home of senior product roles. The dedicated actor exposes the
  **experience-level filter** (Associate → Director → Executive) and **remote/hybrid** + location —
  so you can pull *only* Director/VP/Head-level product roles in Israel.
- ~**$1 / 1,000**; some variants are flat **$19.99–$29.99/month** for steady volume.
- Best for high-precision senior targeting that the aggregator can't filter as tightly.

### C. TheirStack (technographic) — direct API
- Query by **title + country (IL) + technology stack** (e.g., companies using LLMs, recommender
  systems, personalization platforms) — a strong proxy for "AI/data product orgs." Global, so it
  also catches remote roles. Paid plan $59/mo; you've lifted the cap so it's in scope.

---

## Geography: you're already set for Israel-only (no action needed)
Your relevance gate already enforces this. Because your Settings list **Tel Aviv + Hybrid** and
**not** "Remote", the gate keeps Israel-located roles (including Israel-based hybrid/remote) and
**drops every remote-global role** (Remote-US, Remote-EMEA, London, etc.). Verified against test
cases. **Do NOT add "Remote" to your Settings** — that would re-open the global net. We also query
the paid sources with `location = Israel`, so the net stays Israel-focused at both ends.

---

## Phased rollout I'd recommend
1. **Phase 1 (now):** create Apify account → wire `agentx/all-jobs-scraper` + a LinkedIn actor with
   `location = Israel`. `APIFY_ACTORS=agentx~all-jobs-scraper,curious_coder~linkedin-jobs-scraper`.
   This alone should fix "no relevant jobs." (Leave "Remote" OUT of your Settings.)
2. **Phase 2:** add **TheirStack** API key for technographic AI-company targeting.
3. **Phase 3:** add **Wellfound** for startup leadership roles; optionally Glassdoor.

For each Apify actor I need its **Input tab field names** (one screenshot each) to wire the
per-actor input mapping — the adapter currently sends a generic shape.

---

### Sources
- LinkedIn actors overview + pricing: https://apify.com/curious_coder/linkedin-jobs-scraper · https://use-apify.com/docs/best-apify-actors/best-linkedin-scrapers
- Multi-board aggregator: https://apify.com/agentx/all-jobs-scraper
- Wellfound: https://apify.com/blackfalcondata/wellfound-scraper
- Glassdoor: https://apify.com/agentx/glassdoor-scraper
- TheirStack technographic API: https://theirstack.com/en/job-posting-api · https://theirstack.com/en/pricing
