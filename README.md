# Job Hunter

Automated job search and application tool with AI-powered CV analysis, multi-source job aggregation, direct ATS application via Playwright, and WhatsApp/Telegram notifications.

## Features

- **Multi-Source Job Search** - Aggregates jobs from 8+ sources: Greenhouse boards, Lever boards, TechMap product jobs, Comeet, SpeakNow, LinkedIn, Glassdoor, and Wellfound
- **AI-Powered Scoring** - Each job is scored against your CV and preferences using a cascade: Gemini Flash (primary, with Google Search grounding) → Claude Haiku (fallback) → heuristic keyword match. Full job descriptions (up to 2000 chars) used for accurate match percentage and fit explanation
- **Smart Filtering** - Only relevant jobs matching your titles and keywords are shown; dead links are automatically filtered out
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

- **Greenhouse API** (47 Israeli tech companies) - Direct public API queries to boards-api.greenhouse.io with full job descriptions extracted from HTML content
- **Lever API** (2+ companies) - Direct public API queries to api.lever.co
- **TechMap CSV** - Curated Israeli product/management jobs from github.com/mluggy/techmap
- **Comeet** - Israeli company job boards via Comeet API
- **SpeakNow** - Career opportunities from speaknow.co/careers
- **Gemini Google Search grounding** (supplemental) - LinkedIn, Glassdoor, and Wellfound via Gemini Flash with `google_search` tool grounding

Jobs are pre-filtered by title match against your preferences, then scored by the AI cascade against your CV and keywords. Only jobs scoring 40%+ are kept.

### 2. AI Scoring Cascade

Job scoring uses a three-tier cascade for reliability and cost efficiency:

1. **Gemini Flash** (primary) — Google Search grounding enabled, batch-scores up to 10 jobs per call with full descriptions
2. **Claude Haiku** (fallback) — Used if Gemini Flash is unavailable or fails
3. **Heuristic keyword match** (last resort) — Local scoring using keyword overlap, no API required

### 3. Automatic Scheduler

The built-in scheduler runs every 60 seconds and checks if it's time to trigger a search or apply cycle:

- Respects **daily vs weekly** frequency setting
- Honors **search_day_of_week** and **apply_day_of_week** for weekly schedules
- Skips weekends when **weekdays_only** is enabled
- Prevents duplicate runs on the same day

### 4. URL Verification

Every job URL is automatically verified after search. Dead links are:
- Marked with a "Dead link" badge in the dashboard
- Excluded from new search inserts (won't clutter your job list)

### 5. Review & Approve

Jobs appear in the dashboard sorted by match score. Each card shows:
- **Match percentage** - How well the job fits your profile (0-100%)
- **Fit reason** - AI explanation of why this job matches
- **Verification status** - Whether the job URL is still active
- **Cover Letter** button (admin only) - Generate, edit, and copy a personalised cover letter

Actions: Approve to Apply, Pass (with optional reason).

### 6. Direct ATS Auto-Apply

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
7. Updates apply status in real time; UI polls every 4 seconds

A **double-submit guard** prevents re-submitting jobs already in `applying`, `confirmed`, or `submitted` state.

### 7. Failed Job Recovery

The Applied tab includes tools for managing failed applications:
- **Filter pills** to view All, Failed, Submitted, or Confirmed applications
- **Retry Auto-Apply** button on each failed job
- **Apply Manually** button linking directly to the job posting
- Application status tracking with progress badges (Failed, Screening, Interviewing, Offer, Rejected)

### 8. Cover Letter Generator

Admin users can generate, edit, and copy a personalised cover letter for any job:
- **Generate** — Calls Claude Haiku with the job description and your CV summary; ≤250 words, professional-conversational tone
- **Edit** — Textarea (full height, properly sized) for manual edits
- **Save** — Persists the letter to the job record in the database
- **Copy** — Copies to clipboard for pasting into application forms

### 9. Notifications

WhatsApp (Twilio) or Telegram notifications for:
- New jobs found (with count and top matches)
- Application results (submitted, confirmed, or manual required)

## Setup

### Environment Variables (Railway)

| Variable | Description |
|----------|-------------|
| ANTHROPIC_API_KEY | Anthropic API key for Claude AI scoring and cover letter generation |
| GEMINI_API_KEY | Google Gemini API key for primary AI scoring (with Google Search grounding) |
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
- **AI Scoring**: Gemini Flash (primary, Google Search grounding) → Claude Haiku (fallback) → heuristic
- **AI Generation**: Claude Haiku for cover letters; Claude Sonnet for CV analysis
- **Job Sources**: Greenhouse API, Lever API, TechMap CSV, Comeet API, Gemini Google Search
- **ATS Support**: Greenhouse, Lever, Workday (multi-page)
- **Browser Automation**: Playwright (headless Chromium) for auto-apply
- **Notifications**: Twilio (WhatsApp), Telegram Bot API
- **Containerization**: Docker (Dockerfile with Chromium dependencies for Railway)
- **Hosting**: Railway with auto-deploy from GitHub

## Database Schema - Key Columns

**users**: id, email, password_hash, name, role, is_active  
**user_profiles**: user_id, job_titles, keywords, locations, cv_path, cv_summary, schedule_frequency, search_hour, apply_hour, search_day_of_week, apply_day_of_week, weekdays_only, notification_channel  
**jobs**: id, user_id, title, company, location, url, description, full_description, why_relevant, source, match_score, candidate_score, status, apply_status, apply_confirmation, apply_error, apply_failure_type, cover_letter, url_verified, found_date, publish_date  
**activity_log**: id, user_id, event_type, details, created_date

## Recent Updates (April 2026)

### Auto-Apply Engine (apply_engine.py)
- **Direct ATS application** — bypasses LinkedIn/Indeed and applies through the company's own careers page
- **Job board detection** — recognises 20+ aggregator domains and triggers Google search to find the real ATS URL
- **Workday support** — new `_apply_workday()` handler navigates up to 12 form pages, uploads CV once on page 1, detects Submit vs Next button per page
- **Greenhouse & Lever support** — dedicated handlers with platform-specific selectors
- **Double-submit guard** — jobs already in `applying`, `confirmed`, or `submitted` state are skipped
- **Failure classification** — apply errors tagged as `captcha`, `timeout`, `login_wall`, `form_validation`, `network_error`, or `other`

### AI Scoring (app.py + ai_analysis.py)
- **Gemini Flash primary scorer** — replaces Claude web_search supplemental; uses Google Search grounding for live job data; batch-scores up to 10 jobs per call
- **Three-tier scoring cascade** — Gemini Flash → Claude Haiku → heuristic keyword match
- **Improved match accuracy** — full descriptions (up to 2000 chars) sent to scorer

### UI Fixes (app.py)
- **Job description rendering** — two-pass `stripHtml()` function handles both raw HTML (`<h4>`) and double-encoded HTML (`&lt;h4&gt;`) stored in the database; plain text always displayed correctly
- **Cover letter modal** — textarea height fixed (was collapsed to 1 line due to Tailwind JIT class not available in CDN); now uses inline `min-height: 260px`
- **Cover letter open speed** — removed full-jobs fetch on modal open; letter data passed directly from job card (instant)
- **Real-time apply status** — spinner shown while applying; UI polls every 4 seconds; card updates to result badge on completion
- **Apply failure badges** — failure type (Captcha, Timeout, Login Wall, etc.) shown as coloured badge on failed job cards

### Bug Fixes
- Fixed `cv_summary` crash in cover letter generation — `cv_analyzed` integer column was used as fallback, causing `TypeError: 'int' object is not subscriptable` when CV had been analyzed
- Removed dead unreachable `check_job_status` code block in cover letter endpoint
- Fixed Claude model name reference (`claude-sonnet-4-6` instead of deprecated string)

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
