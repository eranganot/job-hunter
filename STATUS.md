# Job Hunter — Status

_Last updated: 2026-06-22 by Claude (session "review tasks / optimize")._
_Seeded from git history + prior transcripts._

## Now (live on Railway, branch main)
- All LLM calls on Gemini (Anthropic fully removed) — apply form-filling, confirmation, CV, cover letters, search.
- Apply mislabeling fixed: only marks "applied" when no error; auto-cleans errored "applied" jobs back to the queue.
- Apply-freeze fixed: 30s Playwright timeouts + deadline guard + guarded submit wrapper (no more stuck spinner).
- Manual-apply rows render **amber** with a person icon ("Manual Apply Needed"), not green "Applied"; Telegram/notification summary reads honestly (e.g. "0 auto-submitted — 15 need manual apply").
- Sourcing prefers **direct-ATS URLs** (`site:greenhouse.io OR lever.co OR comeet.com OR myworkdayjobs.com …`) over job-board aggregators.
- SPA queue actions: mark applied / remove / open, 6 reject reasons, apply-result detail + retry.
- HEAD `e24418d`.

## ⚠️ Folder hygiene (do this)
The OneDrive copy (`…\Eran's dev\Job Hunter`, branch `master`, commit `870c4eb`) has drifted. Salvage any unsynced edits, then archive it. See `migration-plans\PLAN_unify-job-hunter.md`.

## Next (confirm priority)
- Verify direct-ATS sourcing actually fills the Approved queue with auto-submittable jobs (was the open question last session).
- Optionally enable `APPLY_RESOLVE_CAREER_PAGE=1` and measure rescue rate on LinkedIn listings.

## Known sharp edges
Two-folder drift (being retired); sandbox can't push; Playwright browser binaries must be present in the Railway image for real submits.

## Changelog (newest first)
- 2026-06-23 — Landing page auto-routes on first load: swipe if there are new jobs, dashboard (queue tab) if none. Deep-links (`?view=`) still win. Initial load only — the "All Done" screen after a swipe session is unchanged. (`web/src/App.tsx`)
- 2026-06-22 — STATUS.md + CLAUDE.md seeded.
- (prior) — Gemini-only migration, apply mislabel fix, amber manual badge + honest summary, direct-ATS sourcing, apply-freeze fix.
