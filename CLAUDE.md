# Job Hunter — Claude working notes

Job-application automation. Python (stdlib `http.server`, no Flask) + SQLite, React PWA frontend, deployed on Railway. Remote: `github.com/eranganot/job-hunter.git`, branch `main`.

## Folder situation — settled 2026-09-07
**`C:\dev\job-hunter` is the single source of truth.** It is the repo Railway deploys from. Two older copies were retired the same day:
- `C:\Users\erang\job-hunter` — was canonical until 2026-09-07; its uncommitted work was carried into this clone. Archive it.
- OneDrive `…\Eran's dev\Job Hunter` — divergent `master` @ `870c4eb`. Archive it.

Do not edit either. If you find yourself in a Job Hunter clone that is not `C:\dev\job-hunter`, stop and check `git log -1` against `origin/main` before touching anything.

## How to work in this repo
- **Read `STATUS.md` first**; update it after shipping or at session end.
- Current phase plan: **`EXECUTION_PLAN_PUBLIC_LAUNCH.md`** (public/paid multi-user launch, approved 2026-09-07). Phase numbering and locked decisions live there.
- The sandbox has **no push credentials**. Make edits, then hand Eran a PowerShell block to commit + push. Every phase also ships a `scripts/smoke_*.ps1`.
- Deploy = `git add -A && git commit && git push` from this clone; Railway builds on push.
- Use the `ship-it` skill for Railway verify steps, `app-bug-triage` for production bugs, `investigate-issue` for any root-cause work, `safe-windows-edits` for edits to files >200 lines (this mount can truncate mid-write).

## Two UIs — know which one you're in
- **`/app`** — React + Vite + TS + Tailwind PWA, source in `web/`, shipped as the **committed `web_bundle/`**. Railway does NOT build the frontend: any `web/src` change needs a local rebuild, a check that the built CSS is ~25KB (not ~400 bytes), a service-worker cache bump, and a commit of `web_bundle/`.
- **`/dashboard`** — legacy server-rendered HTML, `app.py` lines ~2345–5380, styled by a frozen gzip+base64 Tailwind blob at `app.py:33` that no build regenerates. Being retired in Phase 4 of the execution plan.

## Domain facts
- Auto-apply is **OFF in production** (`APPLY_ENGINE_ENABLED` unset ⇒ `apply_engine.submit_application` no-ops), root cause open — see `APPLY_ENGINE_INVESTIGATION.md`. That workstream is parked by Eran's decision; do not re-enable it without him.
- The apply engine can only auto-submit to **direct ATS URLs** (Greenhouse, Lever, Workday, Comeet, SmartRecruiters, Ashby, company `/careers`). Job-board listings (LinkedIn/Indeed/Glassdoor) are correctly flagged `manual_required` — by design, not a bug.
- "Submitted N" in summaries must reflect real submits, not manual-flagged jobs — keep wording honest.
- All LLM calls route through **Gemini** (Anthropic removed).
- `APPLY_RESOLVE_CAREER_PAGE=1` (Railway env) lets it try to resolve a company career page from a job-board URL — slower, hit-or-miss, fallback only.

## Response style (token-saving)
Short checklist summaries (files changed + commands + smoke check). Don't paste whole logs — last ~20 lines. Edit in place. Use the Explore subagent for broad searches.
