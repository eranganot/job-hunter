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
- 2026-06-23 (pm-3) — 🚨 **FIXED unstyled app.** Every SPA bundle I built this session shipped a 393-byte CSS (Tailwind never compiled) → the app rendered as raw unstyled HTML once deployed. Cause: the sandbox build dir was missing `tailwind.config.js` + `postcss.config.js`, so vite ran without Tailwind. Fix: copy BOTH config files (and `src/index.css`) into the build dir before `npm run build`. Correct CSS is ~25KB. Bundle now `web_bundle/assets/index-DQ6xwz81.css` + `index-CKTP69w-.js`.
  - **BUILD REQUIREMENT (do not forget):** the SPA cannot be built on the OneDrive/Windows mount (npm can’t create symlinks there). Build in a sandbox copy that includes: `package.json`, `package-lock.json`, `tsconfig*.json`, `vite.config.ts`, **`tailwind.config.js`**, **`postcss.config.js`**, `index.html`, `src/`, `public/`. Always confirm the built CSS is ~20KB+ (not a few hundred bytes) before syncing to `web_bundle/`.
- 2026-06-23 (pm-2) — More mobile SPA fixes. (1) Swipe card scores now have a **"What’s this?"** explainer clarifying Match Score (job→profile fit) vs Your Strength (fit adjusted for CV/experience; equal when no adjustments apply). (2) **Finishing a non-empty swipe stack now auto-routes to the dashboard** (queue tab) instead of the "All done" screen; empty stacks still show it so the Swipe button isn’t a dead bounce. (3) **View CV**: new authenticated `GET /api/cv` streams the stored PDF inline (`?download=1` to download); added a "View" link on the Settings CV card. (4) **Sort control (Match/Date/Company)** added above Queue/Applied/Deferred lists (client-side; needed new `foundDate` on UiJob). Files: `app.py`, `web/src/App.tsx`, `web/src/api/client.ts`; bundle rebuilt (`web_bundle/assets/index-BnlyXYA3.js`).
- 2026-06-23 (pm) — Mobile SPA CV + queue fixes. (1) CV card now shows the **original filename + upload date** (new `cv_filename`/`cv_uploaded_date` columns; surfaced via `/api/me`; upload endpoint persists + returns them). (2) Added **"Analyze my CV with AI"** button in Settings → runs `/api/cv-optimizer-analyze` (coach: score/strengths/improvements/ATS notes), loads last cached result on open. (3) Added **"Check links"** button in the queue → `POST /api/validate-links` checks New+Approved URLs concurrently and auto-rejects only *definitively* dead ones (404/410/DNS-gone); 403/429/timeouts kept as "unverified" to avoid nuking bot-blocked live jobs. Files: `db.py`, `auth.py`, `app.py`, `web/src/App.tsx`, `web/src/api/client.ts`; SPA bundle rebuilt (`web_bundle/assets/index-9DQLmPVR.js`).
  - ⚠️ **Edit-tool writes to this mount truncate files mid-write.** db.py/app.py/auth.py/App.tsx/client.ts/STATUS.md were all silently truncated this session. Workaround used: reconstruct from `git show HEAD:<file>`, re-apply changes via Python, write back with `cp`, then verify line counts + py_compile/esbuild. Prefer bash `cp`/Python writes over the Edit tool here until root-caused.
- 2026-06-23 — Landing/refresh behavior. Cold launch routes by new-jobs count: swipe if there are new jobs, dashboard (queue tab) if none. A page **refresh now keeps you on the same screen** (current view+tab persisted in `sessionStorage` under `jh.nav`); the new-jobs rule only fires on a fresh session. Push deep-links (`?view=`) still win. (`web/src/App.tsx`)
  - **Rebuilt the SPA bundle** (`web_bundle/`, now `assets/index-B-6hBYTU.js`). NOTE: the Railway deploy does NOT build the frontend — `web_bundle/` is committed and served as-is, so any `web/src` change requires a rebuild + commit of `web_bundle/`. (Old unreferenced `web_bundle/assets/index-*.js|css` can be pruned on Windows; the mount blocked deletion.)
- 2026-06-22 — STATUS.md + CLAUDE.md seeded.
- (prior) — Gemini-only migration, apply mislabel fix, amber manual badge + honest summary, direct-ATS sourcing, apply-freeze fix.
