# Job Hunter — Claude working notes

Job-application automation. Python / Flask, deployed on Railway. Remote: `github.com/eranganot/job-hunter.git`, branch `main`. **This clone (`C:\Users\erang\job-hunter`) is the repo Railway deploys — treat it as the single source of truth.**

## Important: folder situation
There is an older OneDrive copy at `…\Eran's dev\Job Hunter` on a divergent `master` branch. **Do not edit there.** Edit and push from this clone only. (See `migration-plans\PLAN_unify-job-hunter.md` to retire the OneDrive copy.)

## How to work in this repo
- **Read `STATUS.md` first**; update it after shipping or at session end.
- This clone is outside OneDrive, so git works normally here — but the sandbox still has **no push credentials**. Make edits, then hand Eran a copy-paste block to commit + push.
- Deploy = plain `git add -A && git commit && git push` from here. No file-copying between folders (that was the old OneDrive workflow).
- Use the `ship-it` skill for the Railway verify steps, and `app-bug-triage` for production bugs.

## Domain facts
- The apply engine can only **auto-submit to direct ATS URLs** (Greenhouse, Lever, Workday, Comeet, SmartRecruiters, Ashby, company `/careers`). Job-board listings (LinkedIn/Indeed/Glassdoor) are correctly flagged `manual_required` — that's by design, not a bug.
- "Submitted N" in summaries must reflect real submits, not manual-flagged jobs — keep wording honest.
- All LLM calls route through **Gemini** (Anthropic removed).
- `APPLY_RESOLVE_CAREER_PAGE=1` (Railway env) lets it try to resolve a company career page from a job-board URL — slower, hit-or-miss, fallback only.

## Response style (token-saving)
Short checklist summaries (files changed + commands + smoke check). Don't paste whole logs — last ~20 lines. Edit in place. Use Explore subagent for broad searches.
