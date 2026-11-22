# Job Hunter — Claude working notes

Job-application automation. Python (stdlib `http.server`, no Flask) + SQLite, React PWA frontend, deployed on Railway. Remote: `github.com/eranganot/job-hunter.git`, branch `main`.

## Where the work happens — since 2026-10-25
The owner moved to a Mac (October 2026); the Windows clones (`C:\dev\job-hunter` and the two archived before it) are history.
Claude works in a cloud workspace with **push access to `github.com/eranganot/job-hunter`** and ships through pull requests.

## How to work in this repo
- **Read `STATUS.md` first**; update it in the same PR that ships.
- Current plan: **`EXECUTION_PLAN_NEXT.md`** (steps 0–13, approved 2026-10-25 for steps 0–3). `EXECUTION_PLAN_PUBLIC_LAUNCH.md` stays the reference for Phases 5–7. `BACKLOG.md` is the index.
- **Every change is a PR, smoke-tested twice** (plan section 3):
  1. Branch from `main` (`step-<n>-<slug>`), change + tests, full suite green.
  2. Before merge: `python3 scripts/smoke.py --boot-local` (boots this checkout on a throwaway DB, runs the suite and every smoke check). Paste the summary into the PR.
  3. Merge → Railway deploys `main`.
  4. After merge: `python3 scripts/smoke.py --skip-local --expect-commit <merge sha> --wait-for-deploy-minutes 8` against production; post the result on the PR. A red production smoke = revert PR first, investigate second.
- `scripts/smoke.ps1` / `scripts/build_web.ps1` are the original Windows versions; PRs use `scripts/smoke.py` and `scripts/build_web.py` (a workspace build of unchanged `web/` reproduced the committed bundle byte for byte, 2026-10-25).
- Use the `investigate-issue` skill for any root-cause work, `app-bug-triage` for production bugs.

## One UI — `/app`
- **`/app`** — React + Vite + TS + Tailwind PWA, source in `web/`, shipped as the **committed `web_bundle/`**. Railway does NOT build the frontend: any `web/src` change needs `python3 scripts/build_web.py` (builds, checks the CSS size, bumps the service-worker cache, verifies) and a commit of `web_bundle/`.
- The legacy server-rendered UI (`/dashboard`, `/settings`, `/onboarding`, `/admin`, the frozen Tailwind blob, `LEGACY_UI`) was **deleted in Step 9 (2026-11-05)**. Those URLs only redirect into `/app`. `/login` and `/register` are still server-rendered (`LOGIN_HTML` / `REGISTER_HTML`, self-contained styles) — they are not legacy.

## Domain facts
- **No plan has auto-apply since 2026-11-16** (`plans.py`; users see no control for it; the owner: "cannot be launched to users"). Do not give it back to a plan without the owner.
- **The engine presses Submit only on `APPLY_AUTO_SUBMIT_SYSTEMS` (default Greenhouse) since 2026-11-22**; elsewhere it fills the form and returns the prepared sheet (`manual_required` / `prepared`). Widen only with the owner.
- **Plan B test B0 (2026-11-22):** `device.py` + `tools/b0-extension/` send applications from the owner's own Chrome; admin-only device tokens. Mobile app (B3) parked by the owner.
- Auto-apply is **on for admins only in production** since 2026-11-09 (`APPLY_ENGINE_ENABLED` set; `_engine_on_for(user)` = the switch AND (admin OR `APPLY_ENGINE_FOR_ALL`)). It still fails on most jobs (0 of 21 sent on 2026-11-12; Comeet answers 423 to the app's automated browser - settled 2026-11-15, reported as a bot check) - see `STATUS.md` and `APPLY_ENGINE_INVESTIGATION.md`. Do not set `APPLY_ENGINE_FOR_ALL` or widen it without the owner.
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