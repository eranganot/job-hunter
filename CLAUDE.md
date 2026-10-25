# Job Hunter — Claude working notes

Job-application automation. Python (stdlib `http.server`, no Flask) + SQLite, React PWA frontend, deployed on Railway. Remote: `github.com/eranganot/job-hunter.git`, branch `main`.

## Where the work happens — since 2026-10-25
Eran moved to a Mac (October 2026); the Windows clones (`C:\dev\job-hunter` and the two archived before it) are history.
Claude works in a cloud workspace with **push access to `github.com/eranganot/job-hunter`** and ships through pull requests.

## How to work in this repo
- **Read `STATUS.md` first**; update it in the same PR that ships.
- Current plan: **`EXECUTION_PLAN_NEXT.md`** (steps 0–13, approved 2026-10-25 for steps 0–3). `EXECUTION_PLAN_PUBLIC_LAUNCH.md` stays the reference for Phases 5–7. `BACKLOG.md` is the index.
- **Every change is a PR, smoke-tested twice** (plan section 3):
  1. Branch from `main` (`step-<n>-<slug>`), change + tests, full suite green.
  2. Before merge: `python3 scripts/smoke.py --boot-local` (boots this checkout on a throwaway DB, runs the suite and every smoke check). Paste the summary into the PR.
  3. Merge → Railway deploys `main`.
  4. After merge: `python3 scripts/smoke.py --skip-local --expect-commit <merge sha> --wait-for-deploy-minutes 8` against production; post the result on the PR. A red production smoke = revert PR first, investigate second.
- `scripts/smoke.ps1` is the original Windows version; `scripts/smoke.py` is the one PRs use.
- Use the `investigate-issue` skill for any root-cause work, `app-bug-triage` for production bugs.

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

## Ship rules (added by setup-claude-local.ps1)
- Read STATUS.md first; update it last.
- Before commit: run the repo's checks (see the ship-it skill / README). Never commit failing typecheck or tests.
- One commit per round where practical; check git log -1 so a re-run never duplicates a commit.
- Never force-push. Never commit secrets (.env, agent keys, tokens).
- Deploys happen on merge to main (Railway). After merging, the production smoke confirms `/api/health` reports the merge commit.