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
- 2026-06-23 (pm) — Mobile SPA CV + queue fixes. (1) CV card now shows the **original filename + upload date** (new `cv_filename`/`cv_uploaded_date` columns; surfaced via `/api/me`; upload endpoint persists + returns them). (2) Added **"Analyze my CV with AI"** button in Settings → runs `/api/cv-optimizer-analyze` (coach: score/strengths/improvements/ATS notes), loads last cached result on open. (3) Added **"Check links"** button in the queue → `POST /api/validate-links` checks New+Approved URLs concurrently and auto-rejects only *definitively* dead ones (404/410/DNS-gone); 403/429/timeouts kept as "unverified" to avoid nuking bot-blocked live jobs. Files: `db.py`, `auth.py`, `app.py`, `web/src/App.tsx`, `web/src/api/client.ts`; SPA bundle rebuilt (`web_bundle/assets/index-9DQLmPVR.js`).
  - ⚠️ **Edit-tool writes to this mount truncate files mid-write.** db.py/app.py/auth.py/App.tsx/client.ts/STATUS.md were all silently truncated this session. Workaround used: reconstruct from `git show HEAD:<file>`, re-apply changes via Python, write back with `cp`, then verify line counts + py_compile/esbuild. Prefer bash `cp`/Python writes over the Edit tool here until root-caused.
- 2026-06-23 — Landing/refresh behavior. Cold launch routes by new-jobs count: swipe if there are new jobs, dashboard (queue tab) if none. A page **refresh now keeps you on the same screen** (current view+tab persisted in `sessionStorage` under `jh.nav`); the new-jobs rule only fires on a fresh session. Push deep-links (`?view=`) still win. (`web/src/App.tsx`)
  - **Rebuilt the SPA bundle** (`web_bundle/`, now `assets/index-B-6hBYTU.js`). NOTE: the Railway deploy does NOT build the frontend — `web_bundle/` is committed and served as-is, so any `web/src` change requires a rebuild + commit of `web_bundle/`. (Old unreferenced `web_bundle/assets/index-*.js|css` can be pruned on Windows; the mount blocked deletion.)
- 2026-06-22 — STATUS.md + CLAUDE.md seeded.
- (prior) — Gemini-only migration, apply mislabel fix, amber manual badge + honest summary, direct-ATS sourcing, apply-freeze fix.
