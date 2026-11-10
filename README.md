# Job Hunter

Job Hunter finds relevant jobs for you, scores each one against your CV, and links you to the posting on the company's own site. You review them in a swipe deck: approve, pass or save for later. Every decision teaches the next search.

Live at **[job-hunter.work](https://job-hunter.work)**.

## What it does

- **Search on a schedule or on demand.** Jobs come from company job boards (Greenhouse, Lever, Comeet, Workday and others), large-company career sites, LinkedIn's public job pages and, on eligible plans, a shared pool of paid job APIs.
- **Score every job against your CV.** Gemini reads the job description and your CV, gives a match percentage and explains the fit in plain words. A relevance gate drops jobs outside your titles and locations before any AI spend.
- **Link to the company, not the job board.** A job found on a board is resolved to the posting on the employer's own careers page wherever one exists. Dead and expired postings are detected and removed.
- **Review in a swipe deck.** Approve, pass ("not relevant", "wrong location", "already applied elsewhere"...) or defer. Passes and approvals feed back into scoring.
- **Apply with help.** Per-job cover letters and a job-tailored CV (fact-checked against your real CV), plus a supervised apply flow that fills company application forms and answers their questions.
- **Plans.** Free, Beta, Premium and Expert differ by quotas and features; admin is a separate role. Billing is built but switched off.
- **Your data is yours.** Export everything as JSON plus your CV, or delete the account (14-day grace period).

## Architecture

```
Browser / installed PWA ──► /app  (React + Vite + TypeScript + Tailwind, served from web_bundle/)
                              │
                              ▼
                         app.py  (Python stdlib http.server: routes, auth, scheduler)
                    ┌─────────┼──────────────────────────────┐
                    ▼         ▼                              ▼
             ingestion/   worker.py + jobqueue.py      apply_engine.py
             (sources,    (background searches,        (Playwright; supervised
              dedup,       in-process thread)           form filling)
              relevance)
                    │         │
                    ▼         ▼
                 gemini.py (every LLM call: budget ceilings, retries)
                              │
                              ▼
                 PostgreSQL in production (SQLite for local dev and tests)
```

| Area | Modules |
|---|---|
| Web app (PWA) | `web/` (source), `web_bundle/` (committed build; the server does not build it) |
| HTTP server, routes, scheduler | `app.py`, `auth.py`, `ratelimit.py`, `schedlock.py`, `landing.py` |
| Job sources | `ingestion/` (adapters, dedup, relevance gate), `boardsearch.py`, `workday.py`, `paidpool.py` |
| Links | `links.py` (board link → company posting), `linkcheck.py` (is the posting still live) |
| AI | `gemini.py` (single Gemini client), `ai_analysis.py` (CV analysis, scoring, cover letters), `tailor.py`, `questions.py` |
| Applying | `apply_engine.py` |
| Plans and limits | `plans.py` (tier matrix as data), `entitlements.py` |
| Data | `db.py`, `dbdriver.py` (SQLite / Postgres), `migrations.py`, `storage.py` (CVs in the database), `crypto.py`, `accountdata.py` |
| Ops | `errors.py` (Sentry), `log.py`, `scripts/smoke.py` |

## Running locally (maintainers)

This section is for the project's maintainers. The code is proprietary: see [License](#license) before copying or running it.

Requirements: Python 3.11; Node.js and npm only if you change the web app.

```bash
git clone https://github.com/eranganot/job-hunter.git
cd job-hunter
pip install -r requirements.txt
playwright install chromium        # only needed for the apply engine
cp .env.example .env               # then fill in at least GEMINI_API_KEY and ADMIN_EMAIL
python app.py                      # http://localhost:5001
```

Without `DB_BACKEND=postgres` the app uses a local SQLite file, which is fine for development. Register with the address you set as `ADMIN_EMAIL` to get the admin role.

### Changing the web app

```bash
cd web && npm ci && cd ..
python3 scripts/build_web.py       # builds, checks the CSS size, bumps the service-worker cache, verifies
```

Commit the resulting `web_bundle/` together with the `web/src` change. Production serves the committed bundle and never builds the frontend.

## Configuration

All configuration is environment variables; [.env.example](.env.example) lists them with comments. The main ones:

| Variable | Purpose |
|---|---|
| `GEMINI_API_KEY` | Every AI feature. |
| `ADMIN_EMAIL` | The account that gets the admin role; also the default inbox for support messages. |
| `DB_BACKEND=postgres`, `DATABASE_URL` | Use Postgres (production). Both are required; a `DATABASE_URL` alone is ignored on purpose. |
| `JH_ENCRYPTION_KEY` | Encrypts notification credentials at rest. |
| `RESEND_API_KEY`, `RESEND_FROM`, `JH_SUPPORT_EMAIL` | Email (notifications, Contact support). |
| `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_PEM_B64`, `VAPID_SUBJECT` | Web push. |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | "Continue with Google". Leave empty to offer email and password only. |
| `JH_PAID_POOL_SOURCES` | Which paid job APIs run (for example `hirebase,fantastic`). Empty means none, whatever keys are set. |
| `APPLY_ENGINE_ENABLED` | The apply engine submits only when this is `1`. |
| `JH_SENTRY_DSN` | Error reporting. |
| `JH_HEALTH_KEY` | Unlocks the detailed view of `/api/health`. |

### Paid job sources

The paid sources (TheirStack, Hirebase, Fantastic.jobs, and Apify actors) never run inside a user's search. Once a day each enabled source is asked for new postings, and the results go into a shared pool that every eligible search reads at no extra cost. Spend is counted in the database against each provider's own billing cycle and reserved before every call. Each cap is clamped to the provider's free allowance unless `JH_POOL_ALLOW_ABOVE_FREE=1`. `/api/health` → `paid_pool` shows usage against the cap for each source.

## Tests

```bash
python3 -m pytest -q                        # full suite, SQLite
python3 scripts/smoke.py --boot-local       # boots this checkout on a throwaway database, runs the suite and every smoke check
```

CI runs the suite on SQLite and on Postgres for every pull request.

## Deployment

Railway builds the [Dockerfile](Dockerfile) and deploys `main` on every merge. See [DEPLOY.md](DEPLOY.md) for the release flow and [RAILWAY_RUNBOOK.md](RAILWAY_RUNBOOK.md) for operations. After a deploy, `/api/health` reports the running commit.

## Project docs

- [STATUS.md](STATUS.md): changelog with root causes, and the current state
- [EXECUTION_PLAN_NEXT.md](EXECUTION_PLAN_NEXT.md), [EXECUTION_PLAN_PUBLIC_LAUNCH.md](EXECUTION_PLAN_PUBLIC_LAUNCH.md), [BACKLOG.md](BACKLOG.md): plans and backlog
- [SCORING.md](SCORING.md): how jobs are scored
- [ingestion/README.md](ingestion/README.md): the job-source pipeline

## Support

In the app: **Contact support** (sidebar, under Settings).

## License

Proprietary, source-visible. Copyright (c) 2026 Eran Ganot, all rights reserved. You may read the code here; you may not copy, modify, run, host or distribute it without written permission. Full terms in [LICENSE](LICENSE). Using the service at [job-hunter.work](https://job-hunter.work) is governed by its Terms of Service.
