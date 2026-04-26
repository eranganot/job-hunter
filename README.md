# Job Hunter

Automated job search and application tool with AI-powered CV analysis, multi-source job aggregation, direct ATS application via Playwright, and WhatsApp/Telegram notifications.

## Features

- **Multi-Source Job Search** - Aggregates jobs from 8+ sources: 65 Greenhouse boards, 8 Lever boards, TechMap product jobs, Comeet, SpeakNow, LinkedIn, Glassdoor, and Wellfound
- **AI-Powered Scoring** - Each job is scored against your full CV PDF and preferences using a cascade: Gemini 2.5 Flash (primary, with Google Search grounding) → Claude Haiku (fallback) → heuristic keyword match. Full job descriptions and your CV sent as a PDF attachment for accurate match percentage and fit explanation
- **Smart Filtering** - ATS jobs (Greenhouse/Lever) bypass title pre-filter and go directly to AI scorer; dead links are automatically filtered out; ATS cap of 300 prevents scorer flooding
- **Expandable Job Descriptions** - Full source descriptions stored and viewable inline with tap-to-expand/collapse; double-encoded HTML entities are stripped via two-pass DOM decode so plain text always renders correctly
- **Scheduled Automation** - Configure daily or weekly search/apply cycles with day-of-week and time controls; weekday-only option available; scheduler runs on Israel time (GMT+3)
- **Auto-Apply via Company ATS** - Approved jobs are submitted directly through the company's own careers page (bypasses LinkedIn/Indeed), automatically detecting and handling Greenhouse, Lever, and Workday ATS platforms using headless Playwright
- **Real-Time Apply Status** - UI polls every 4 seconds while applying, showing a spinner; on completion the card updates to Confirmed / Failed / Manual Required
- **Failed Job Management** - Failed applications are moved to Retry queue; filter pills allow viewing All/New/Retry; retry button re-triggers auto-apply
- **Cover Letter Generator** - Per-job AI-generated cover letters (Claude Haiku) based on your CV summary and the job description; editable and copyable from the job card
- **WhatsApp & Telegram Notifications** - Get notified when new jobs are found or applications are submitted
- **Multi-User Auth** - Secure login with per-user profiles, preferences, and job history
- **CV Analysis** - Upload your PDF resume for AI-powered skill extraction and job matching

## How It Works

### 1. Multi-Source Job Search

The search engine queries multiple job sources in parallel:

- **Greenhouse API** (65 Israeli & global tech companies) - Direct public API queries to boards-api.greenhouse.io with full job descriptions; all results go to AI scorer without pre-filtering
- **Lever API** (8 companies) - Direct public API queries to api.lever.co; all results go to AI scorer without pre-filtering
- **TechMap CSV** - Curated Israeli product/management jobs from github.com/mluggy/techmap
- **Comeet** - Israeli company job boards via Comeet API
- **SpeakNow** - Career opportunities from speaknow.co/careers
- **Gemini Google Search grounding** (supplemental) - LinkedIn, Glassdoor, and Wellfound via Gemini 2.5 Flash with parallel CV-aware queries

Jobs from ATS sources (Greenhouse/Lever) are capped at 300 before scoring to keep AI costs predictable. All jobs are then scored by the AI cascade against your CV and keywords. Only jobs scoring 30%+ are kept.

### 2. AI Scoring Cascade

Job scoring uses a three-tier cascade for reliability and cost efficiency:

1. **Gemini 2.5 Flash** (primary) — Full CV PDF sent as inlineData attachment alongside job descriptions; batch-scores up to 50 jobs per call; threshold 30%+
2. **Claude Haiku** (fallback) — Used if Gemini Flash is unavailable or fails
3. **Heuristic keyword match** (last resort) — Local scoring using keyword overlap, no API required

### 3. Supplemental Web Search

After ATS scoring, Gemini runs parallel Google Search queries to catch jobs not listed on tracked boards:

- One query per job title (up to 4 titles)
- One broad seniority + role query targeting LinkedIn and Wellfound
- Each query is CV-aware: includes seniority level, target locations, and a CV snippet
- Results pass through the same AI scorer before being added

### 4. Automatic Scheduler

The built-in scheduler runs every 60 seconds and checks if it's time to trigger a search or apply cycle:

- Respects **daily vs weekly** frequency setting
- Honors **search_day_of_week** and **apply_day_of_week** for weekly schedules
- Skips weekends when **weekdays_only** is enabled
- Prevents duplicate runs on the same day

### 5. URL Verification

Every job URL is automatically verified after search. Dead links are:
- Marked with a "Dead link" badge in the dashboard
- Excluded from new search inserts (won't clutter your job list)

### 6. Review & Approve

Jobs appear in the dashboard sorted by match score. Each card shows:
- **Match percentage** - How well the job fits your profile (0-100%)
- **Fit reason** - AI explanation of why this job matches
- **Verification status** - Whether the job URL is still active
- **Cover Letter** button (admin only) - Generate, edit, and copy a personalised cover letter

Actions: Approve to Apply, Pass (with optional reason).

### 7. Direct ATS Auto-Apply

When a job is approved, the engine:

1. Checks if the URL is a job board aggregator (LinkedIn, Indeed, etc.)
2. If so, runs a Google search to find the **company's direct careers page** for that role
3. Detects the ATS platform from the URL pattern:
   - **Greenhouse** — `boards.greenhouse.io`
   - **Lever** — `jobs.lever.co`
   - **Workday** — `myworkdayjobs.com` / `myworkday.com`
4. Fills and submits the application form using platform-specific Playwright selectors
5. Handles Workday multi-page forms (up to 12 pages) with CV upload on page 1
6. Falls back to `manual_required` status if a CAPTCHA or login wall is encountered
7. Updates apply status in real time; UI polls every 4 seconds (with 3-minute timeout)
8. Runs in a background thread — API returns immediately, avoiding Railway HTTP timeouts

A **double-submit guard** prevents re-submitting jobs already in `applying`, `confirmed`, or `submitted` state. Any jobs stuck in `applying` state from a crashed run are automatically reset on server startup.

### 8. Failed Job Recovery

The Applied tab includes tools for managing failed applications:
- **Filter pills** to view All, Failed, Submitted, or Confirmed applications
- **Retry Auto-Apply** button on each failed job
- **Apply Manually** button linking directly to the job posting
- Application status tracking with progress badges (Failed, Screening, Interviewing, Offer, Rejected)

### 9. Cover Letter Generator

Admin users can generate, edit, and copy a personalised cover letter for any job:
- **Generate** — Calls Claude Haiku with the job description and your CV summary; ≤250 words, professional-conversational tone
- **Edit** — Textarea (full height, properly sized) for manual edits
- **Save** — Persists the letter to the job record in the database
- **Copy** — Copies to clipboard for pasting into application forms

### 10. Notifications

WhatsApp (Twilio) or Telegram notifications for:
- New jobs found (with count and top matches)
- Application results (submitted, confirmed, or manual required)

## Setup

### Environment Variables (Railway)

| Variable | Description |
|----------|-------------|
| ANTHROPIC_API_KEY | Anthropic API key for Claude AI scoring and cover letter generation |
| GEMINI_API_KEY | Google Gemini API key for primary AI scoring and web search grounding |
| DATABASE_URL | SQLite path (auto-created on Railway volume) |
| SECRET_KEY | Session encryption key |
| PORT | Server port (default: 8080) |

### Profile Setup (Dashboard > Settings)

1. Add your **job titles** (e.g., VP Product, Director of Product, Senior PM)
2. Add **keywords** (e.g., AI/ML, Enterprise SaaS, Product-Led Growth)
3. Set **preferred locations** (e.g., Tel Aviv, Hybrid)
4. Upload your **CV** (PDF) for AI matching

### Schedule (Dashboard > Settings > Schedule)

- Choose **daily** or **weekly** frequency
- Set search and apply hours
- Pick specific days for weekly schedules
- Enable weekdays-only to skip weekends

## Tech Stack

- **Backend**: Python 3.10+ with BaseHTTPRequestHandler (no framework dependencies)
- **Database**: SQLite on Railway persistent volume
- **AI Scoring**: Gemini 2.5 Flash (primary, full CV PDF + Google Search grounding) → Claude Haiku (fallback) → heuristic
- **AI Generation**: Claude Haiku for cover letters; Claude Sonnet for CV analysis
- **Job Sources**: 65 Greenhouse boards, 8 Lever boards, TechMap CSV, Comeet API, Gemini Google Search
- **ATS Support**: Greenhouse, Lever, Workday (multi-page)
- **Browser Automation**: Playwright (headless Chromium) for auto-apply
- **Notifications**: Twilio (WhatsApp), Telegram Bot API
- **Containerization**: Docker (Dockerfile with Chromium dependencies for Railway)
- **Hosting**: Railway with auto-deploy from GitHub (`dev` branch → staging, `main` branch → production)

## Database Schema - Key Columns

**users**: id, email, password_hash, name, role, is_active  
**user_profiles**: user_id, job_titles, keywords, locations, cv_path, cv_summary, schedule_frequency, search_hour, apply_hour, search_day_of_week, apply_day_of_week, weekdays_only, notification_channel  
**jobs**: id, user_id, title, company, location, url, description, full_description, why_relevant, source, match_score, candidate_score, status, apply_status, apply_confirmation, apply_error, apply_failure_type, cover_letter, url_verified, found_date, publish_date  
**activity_log**: id, user_id, event_type, details, created_date

## Recent Updates (April 2026)

### Search Quality — April 2026

- **65 Greenhouse boards** (was 47) — added 13 new verified companies: Torq, Augury, Couchbase, Dremio, Tenable, SolarWinds, Recorded Future, Rubrik, Elastic, MongoDB, Datadog, Cloudflare, Commvault. Fixed 2 broken slugs (gong-io→gongio, armis→armissecurity). Removed 9 slugs that returned 404 (CyberArk, Varonis, Cellebrite, SeaLights, Bizzabo, Lusha, Perion, Akamai, Illumio)
- **Removed Ashby ATS** — all company boards return HTTP 401; companies moved to verified Greenhouse/Lever lists where available
- **CV PDF in scoring** — full CV uploaded as PDF inlineData to Gemini 2.5 Flash for accurate per-job matching (not just extracted text snippets)
- **Loosened ATS pre-filter** — Greenhouse and Lever jobs no longer pre-filtered by title match; all jobs go to AI scorer. ATS_CAP=300 prevents scorer flooding
- **Upgraded to Gemini 2.5 Flash** — both scoring and web search now use gemini-2.5-flash (gemini-2.0-flash returned HTTP 404 for web search queries)
- **Scoring threshold 40% → 30%** — less aggressive cutoff; prompt updated from "be strict" to score generously and exclude only clearly different job functions
- **Web search improvements** — queries run in parallel; each prompt includes seniority level, location, and a CV snippet; fixed "Senior Senior PM" word duplication in the broad query

### Auto-Apply Fixes — April 2026

- **Background thread** — `/api/run-apply` now fires a daemon thread and returns immediately (`{"started": true}`), preventing Railway's HTTP timeout from surfacing as "Server error" to the user
- **Stuck spinner fix** — on server startup, any jobs left in `apply_status='applying'` from a previous crashed run are automatically reset to `NULL`
- **Client-side timeout** — apply poller gives up after 3 minutes and refreshes the UI, preventing infinite spinners if a job silently fails

### Admin Tools — April 2026

- **`POST /api/admin/clear-applied`** — admin-only endpoint (no UI button) to delete applied jobs from the DB. Accepts optional `user_id` to target one user or clears all users if omitted. Deleting applied rows lifts the `UNIQUE(user_id, url)` constraint so those jobs can be re-discovered in future searches

### Auto-Apply Engine — Earlier April 2026

- **Direct ATS application** — bypasses LinkedIn/Indeed and applies through the company's own careers page
- **Job board detection** — recognises 20+ aggregator domains and triggers Google search to find the real ATS URL
- **Workday support** — new `_apply_workday()` handler navigates up to 12 form pages, uploads CV once on page 1, detects Submit vs Next button per page
- **Greenhouse & Lever support** — dedicated handlers with platform-specific selectors
- **Double-submit guard** — jobs already in `applying`, `confirmed`, or `submitted` state are skipped
- **Failure classification** — apply errors tagged as `captcha`, `timeout`, `login_wall`, `form_validation`, `network_error`, or `other`

### AI Scoring — Earlier April 2026

- **Gemini Flash primary scorer** — replaces Claude web_search supplemental; uses Google Search grounding for live job data
- **Three-tier scoring cascade** — Gemini Flash → Claude Haiku → heuristic keyword match
- **Improved match accuracy** — full descriptions (up to 5000 chars) sent to scorer

### Bug Fixes — Earlier April 2026

- Fixed `cv_summary` crash in cover letter generation
- Removed dead unreachable `check_job_status` code block
- Fixed Claude model name reference (`claude-sonnet-4-6`)
- Fixed job description rendering (two-pass HTML strip for double-encoded entities)
- Fixed cover letter modal textarea height

## Local Development

```bash
# Clone and setup
git clone https://github.com/eranganot/job-hunter.git
cd job-hunter

# Set environment variables
export ANTHROPIC_API_KEY=your_key
export GEMINI_API_KEY=your_key
export SECRET_KEY=your_secret

# Install dependencies
pip install playwright
playwright install chromium --with-deps

# Run
python app.py
```

Server starts on http://localhost:8080. First user to register becomes admin.
