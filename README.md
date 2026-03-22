# 🎯 Job Hunter

An AI-powered personal job search assistant that runs entirely on **Railway** (cloud). It automatically finds relevant positions, lets you review and approve them, then applies on your behalf — and notifies you via WhatsApp every step of the way.

---

## Features

- **AI job search** — Claude scans job boards daily and surfaces roles matching your CV, job titles, and keywords
- **Smart scoring** — every job card shows a **match %** (how well the role fits your skills) and a **candidate score** (how strong a candidate you are)
- **🔗 URL verification** — every job URL is automatically checked on import; dead links are flagged with a ⚠️ badge before you ever review them
- **Job status check** — one-click 🔍 button fetches the original posting and asks Claude if the role is still open
- **Review queue** — approve, pass, or defer jobs from a clean mobile-friendly dashboard
- **🤖 Auto-apply** — approved jobs are submitted directly by Railway using a Playwright browser + Claude AI: fills forms, uploads your CV, and verifies the result page confirms submission
- **Apply status badges** — each Applied card shows ✅ Confirmed / 📤 Submitted / 👤 Manual needed / ❌ Failed
- **Interview pipeline** — track applied jobs through Screening, Interviewing, Offer, and Rejected stages directly from the Applied tab
- **WhatsApp notifications** — get alerted on your phone for every key event: new jobs found, apply confirmed, and manual-apply warnings
- **Scheduled runs** — configure daily search and apply windows; view the next run time from the dashboard

---

## How It Works

### 1. Job Search
Claude is given your CV, job titles, and search keywords. It queries job boards, scores each result against your profile, and saves new positions to a SQLite database on Railway.

### 2. URL Verification (Automatic)
When jobs are imported, every URL is checked in parallel (8 concurrent workers, 8 s timeout). Each job is marked:
- **🔗 URL OK** — page responds with HTTP 2xx/3xx
- **⚠️ Dead link** — timeout, 404, or connection error

Dead links are visible immediately in the dashboard so you don't waste time reviewing phantom postings.

### 3. Review & Approve
Open the dashboard → **Review** tab. Each card shows the job title, company, location, match %, candidate score, and URL status. Approve, pass, or defer each job.

### 4. Application Submission (Playwright + Claude)
When the scheduled apply window runs, approved jobs are submitted directly from Railway:

1. **Extract applicant data** — Claude reads your CV and profile to pull name, email, phone, LinkedIn, GitHub, and a short bio
2. **Navigate to job URL** — Playwright opens a headless Chromium browser on Railway
3. **Blocker detection** — login walls and CAPTCHAs are detected and flagged as `manual_required`
4. **Find Apply button** — clicks common apply-button patterns if the form isn't already visible
5. **Claude-powered form fill** — the full page HTML is sent to Claude, which returns a JSON plan of `fill`, `select`, `upload`, and `submit` instructions
6. **Execute instructions** — Playwright follows the plan: types text, selects options, uploads your CV file
7. **Result verification** — after submit, Claude checks the result page for confirmation phrases ("application received", "thank you for applying", etc.)

Result is stored as one of:
| Status | Meaning |
|--------|---------|
| `confirmed` ✅ | Result page confirmed the application was received |
| `submitted` 📤 | Form was submitted but no clear confirmation detected |
| `manual_required` 👤 | Login wall, CAPTCHA, or unrecoverable blocker found |
| `failed` ❌ | Playwright error or unexpected exception |

### 5. Notifications
WhatsApp messages are sent via Twilio for:
- 🔍 New jobs found after a search run
- ✅ Application confirmed (with confirmation snippet)
- 👤 Manual apply needed (with reason and job URL)

---

## Setup

### Environment Variables (Railway)

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Claude API key (required) |
| `TWILIO_SID` | Twilio account SID for WhatsApp |
| `TWILIO_TOKEN` | Twilio auth token |
| `TWILIO_FROM` | Twilio WhatsApp sender number (`whatsapp:+14155238886`) |
| `TWILIO_TO` | Your WhatsApp number (`whatsapp:+972...`) |
| `APP_PASSWORD` | Dashboard login password |
| `SECRET_KEY` | Flask session secret key |

### Profile Setup (Dashboard → Profile)
Fill in:
- **CV / Resume** — plain text or paste your full CV; used by Claude for job matching and form-filling
- **Job Titles** — comma-separated target roles (e.g. `VP Product, Head of Product, CPO`)
- **Keywords** — extra search terms
- **Email** — used when filling application forms
- **CV File** — upload a PDF/DOCX for file-upload fields on application forms

### Schedule Setup (Dashboard → Schedule)
Set your preferred search time and apply window. Railway keeps the process running 24/7.

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Backend | Python / Flask |
| AI | Anthropic Claude (claude-opus-4-5) |
| Browser automation | Playwright (Chromium, headless) |
| Database | SQLite (persistent Railway volume) |
| Notifications | Twilio WhatsApp API |
| Hosting | Railway (always-on) |

---

## Database Schema

Jobs table key columns:

| Column | Description |
|--------|-------------|
| `url_verified` | 1 = URL alive, 0 = dead link (checked on import) |
| `url_check_date` | ISO timestamp of last URL check |
| `apply_status` | `confirmed` / `submitted` / `manual_required` / `failed` |
| `apply_confirmation` | Confirmation text snippet from the result page |
| `apply_attempts` | Number of submission attempts made |
| `apply_error` | Error or blocker description if status is not confirmed |

---

## Development

```bash
git clone https://github.com/eranganot/job-hunter
cd job-hunter
pip install -r requirements.txt
playwright install chromium
python app.py
```

Set the environment variables in a `.env` file or export them in your shell before running locally.
