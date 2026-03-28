# Job Hunter

Automated job search and application tool with AI-powered CV analysis, multi-source job aggregation, and WhatsApp/Telegram notifications.

## Features

- **Multi-Source Job Search** - Aggregates jobs from 8+ sources: Greenhouse boards, Lever boards, TechMap product jobs, Comeet, SpeakNow, LinkedIn, Glassdoor, and Wellfound
- **AI-Powered Scoring** - Each job is scored against your CV and preferences using Claude Haiku, with match percentage and fit explanation
- **Smart Filtering** - Only relevant jobs matching your titles and keywords are shown; dead links are automatically filtered out
- **Scheduled Automation** - Configure daily or weekly search/apply cycles with day-of-week and time controls; weekday-only option available
- **Auto-Apply** - Approved jobs are submitted via headless Playwright browser with Claude-assisted form filling
- **WhatsApp & Telegram Notifications** - Get notified when new jobs are found or applications are submitted
- **Multi-User Auth** - Secure login with per-user profiles, preferences, and job history
- **CV Analysis** - Upload your PDF resume for AI-powered skill extraction and job matching

## How It Works

### 1. Multi-Source Job Search

The search engine queries multiple job sources in parallel:

- **Greenhouse API** (47 Israeli tech companies) - Direct public API queries to boards-api.greenhouse.io
- **Lever API** (2+ companies) - Direct public API queries to api.lever.co
- **TechMap CSV** - Curated Israeli product/management jobs from github.com/mluggy/techmap
- **Comeet** - Israeli company job boards via Comeet API
- **SpeakNow** - Career opportunities from speaknow.co/careers
- **Claude Web Search** (supplemental) - LinkedIn, Glassdoor, and Wellfound via Anthropic web_search API

Jobs are pre-filtered by title match against your preferences, then scored by Claude Haiku against your CV and keywords. Only jobs scoring 40%+ are kept.

### 2. Automatic Scheduler

The built-in scheduler runs every 60 seconds and checks if it's time to trigger a search or apply cycle:

- Respects **daily vs weekly** frequency setting
- Honors **search_day_of_week** and **apply_day_of_week** for weekly schedules
- Skips weekends when **weekdays_only** is enabled
- Prevents duplicate runs on the same day

### 3. URL Verification

Every job URL is automatically verified after search. Dead links are:
- Marked with a "Dead link" badge in the dashboard
- Excluded from new search inserts (won't clutter your job list)

### 4. Review & Approve

Jobs appear in the dashboard sorted by match score. Each card shows:
- **Match percentage** - How well the job fits your profile (0-100%)
- **Fit reason** - AI explanation of why this job matches
- **Verification status** - Whether the job URL is still active

Actions: Approve to Apply, Later, or Pass.

### 5. Application Submission (Playwright + Claude)

Approved jobs are auto-applied using headless Chromium:
- Navigates to the job application page
- Uses Claude to understand form fields
- Fills in your details from CV and profile
- Uploads your resume PDF
- Submits and captures confirmation

### 6. Notifications

WhatsApp (Twilio) or Telegram notifications for:
- New jobs found (with count and top matches)
- Application results (submitted, confirmed, or manual required)

## Setup

### Environment Variables (Railway)

| Variable | Description |
|----------|-------------|
| ANTHROPIC_API_KEY | Anthropic API key for Claude AI scoring and web search |
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
- **AI**: Anthropic Claude (Haiku for scoring, Sonnet for web search)
- **Job Sources**: Greenhouse API, Lever API, TechMap CSV, Comeet API, Claude web_search
- **Browser Automation**: Playwright (headless Chromium) for auto-apply
- **Notifications**: Twilio (WhatsApp), Telegram Bot API
- **Hosting**: Railway with auto-deploy from GitHub

## Database Schema - Key Columns

**users**: id, email, password_hash, name, role, is_active
**user_profiles**: user_id, job_titles, keywords, locations, cv_text, cv_filename, schedule_frequency, search_hour, apply_hour, search_day_of_week, apply_day_of_week, weekdays_only, notification_channel
**jobs**: id, user_id, title, company, location, url, description, why_relevant, source, match_score, candidate_score, status, url_verified, found_date
**activity_log**: id, user_id, event_type, details, created_date

## Local Development

```bash
# Clone and setup
git clone https://github.com/eranganot/job-hunter.git
cd job-hunter

# Set environment variables
export ANTHROPIC_API_KEY=your_key
export SECRET_KEY=your_secret

# Install dependencies
pip install playwright
playwright install chromium --with-deps

# Run
python app.py
```

Server starts on http://localhost:8080. First user to register becomes admin.
