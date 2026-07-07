# Apply Engine — Investigation & Fix Plan

_2026-07-07 — investigation of "apply engine isn't working at all". Diagnosis is
code-complete; the single remaining unknown (production DB/volume + env state)
needs one live check on Railway, described in Phase 0._

---

## TL;DR

The apply engine's **code path is structurally sound** — the reason "nothing
gets applied" is almost certainly a combination of:

1. **Infra (most likely):** Railway still has **no persistent volume**, so the
   SQLite DB *and* the uploaded CV are wiped on every redeploy. Result: the
   Approved queue empties and `cv_path` points at a missing file — the engine
   has nothing to apply to and no résumé to upload. (Flagged in STATUS.md on
   2026-06-23; needs a live confirm that the volume is actually attached now.)
2. **Queue composition:** many "approved" jobs were parked/dead domains (the
   GoDaddy bug — now fixed) or job-board listings, all of which correctly return
   `manual_required`. So "0 real auto-submits" was partly *expected*, not a crash.
3. **Two real code bugs** in the ATS handlers that make success reporting
   unreliable and prevent completion of forms with required screening questions.

The local `jobs.db` in this repo has **zero tables**, confirming the real data
lives only on the Railway volume — which is exactly why this must be confirmed
live before deeper code work.

---

## How the pipeline actually works (map)

**Two entry points:**

- **Scheduled batch** — `run_job_apply(user_id)` (app.py). Gated on
  `auto_apply_enabled`; daily cap of 3 (admin unlimited). Selects
  `status='approved' AND apply_status != 'manual_required' AND attempts < 3`.
- **Manual "Apply now"** — `POST /api/jobs/{id}/apply-now` →
  `_trigger_apply_bg` (app.py). **Not** gated on `auto_apply_enabled` — this is
  the deliberate single-job path. Marks `apply_status='applying'`, runs, updates.

Both call `_submit_application_guarded` (120s hard deadline + concurrency cap) →
`apply_engine.submit_application`.

**`submit_application` decision tree:**

1. `PLAYWRIGHT_AVAILABLE` false → `manual_required` ("Playwright not installed").
2. Job-board URL (LinkedIn/Indeed/Glassdoor…) → immediate `manual_required`
   (by design; the career-page resolver is OFF unless `APPLY_RESOLVE_CAREER_PAGE=1`).
3. Launch headless Chromium → navigate → login-wall check.
4. **ATS fast-paths:** Greenhouse / Lever / Workday handlers (fixed selectors).
5. Otherwise: Gemini reads the HTML and returns a fill/submit interaction list.
6. Verify confirmation (phrase match + a Gemini yes/no check).

---

## Findings — ranked root-cause hypotheses

Because the real DB lives on Railway (local copy is empty), the top two need a
live confirm — Phase 0 does that in one shot.

### H1 — Infra: no persistent volume (HIGH)
Railway `job-hunter` had no volume as of 2026-06-23, so redeploys wipe the DB +
`uploads/`. Effects: (a) Approved queue empties → `run_job_apply` finds nothing;
(b) `cv_path` file is gone → Greenhouse/Lever upload silently fails → the form is
submitted with no résumé and fails validation.
**Verify:** Railway → is a Volume mounted at `/data`, and are
`DATABASE_PATH=/data/jobs.db` + `UPLOADS_DIR=/data/uploads` set? Is the CV still
present after the last redeploy?

### H2 — Queue composition (HIGH, partly by design)
With the link bug (now fixed), many "approved" rows were parked/dead domains or
job-board URLs. Every one of those returns `manual_required` or fails, so zero
real submits is partly *expected*. Real remedy: make sourcing yield enough
**live, direct-ATS** postings (already an open item in STATUS.md).

### H3 — `GEMINI_API_KEY` missing in prod (MEDIUM)
If unset, `extract_applicant_data` returns an empty applicant (no name/phone) and
the generic form-fill raises → `manual_required`. Even the GH/Lever fast-paths
would then submit blank required fields and fail validation.
**Verify:** is `GEMINI_API_KEY` set on Railway? (Phase 0 reports this as a bool.)

### H4 — Playwright browser missing at runtime (MEDIUM)
Image builds `playwright install chromium` (system libs apt-installed; note: no
`--with-deps`). If the browser isn't found at runtime (HOME/cache mismatch),
launch throws or returns "Playwright not installed".
**Verify:** deploy logs for `Launching Chromium…` vs. `Executable doesn't exist`.
(`playwright-stealth` is in requirements but unused in apply_engine, so it can't
break the import guard — ruled out.)

---

## Code-level bugs (real, fix regardless of H1–H4)

- **B1 — False success reporting.** `_apply_greenhouse` / `_apply_lever` set
  `success=True, status="submitted"` even when **no** confirmation phrase is found
  and even if required-field validation silently blocked the submit. Lever's
  confirmation keyword `"application"` matches almost any page → false
  `confirmed`. Verification is unreliable in **both** directions — you can't
  trust "submitted".
- **B2 — Required screening questions ignored.** The ATS handlers fill only
  name/email/phone/linkedin/location + résumé. Greenhouse/Lever postings routinely
  require EEO, work-authorization, and custom questions. Missing required answers
  block the submit — but B1 then mislabels it "submitted". **No auto-submit
  actually completes on these forms.**
- **B3 — Generic-path blind submit.** The fallback clicks the first
  Submit/Apply-matching button, which can be the wrong control.
- **B4 — No failure artifact.** On failure nothing captures the page HTML or a
  screenshot, so production failures are undiagnosable without a live rerun.
- **B5 — Dead/parked links reached the queue as "Verified".** ✅ **Fixed this
  session** (parked-domain detection in `check_url_alive` + `_link_status`, plus
  a conservative re-validation pass that demotes already-"Verified" parked rows).

---

## Fix plan (phased)

### Phase 0 — Definitive live diagnosis (do first, ~15 min)
1. Railway dashboard: confirm **Volume at /data**, `DATABASE_PATH`,
   `UPLOADS_DIR`, and `GEMINI_API_KEY` are set; confirm the CV survived the last
   redeploy.
2. Add a temporary admin `apply-selftest` endpoint/script that, for one approved
   job id (or a known-good Greenhouse test posting), runs `submit_application`
   and returns full detail: `PLAYWRIGHT_AVAILABLE`, `GEMINI_KEY` present (bool),
   browser launched?, resolved URL, page-HTML snapshot, each interaction result,
   final status. One run pinpoints which of H1–H4 / B1–B2 is biting.
3. `grep` the deploy logs for `Launching Chromium`, `manual_required`,
   `Playwright not installed`, `DEADLINE`.

### Phase 1 — Infra (if H1 confirmed) — *your action on Railway*
Attach a volume at `/data`, set `DATABASE_PATH=/data/jobs.db` +
`UPLOADS_DIR=/data/uploads`, redeploy, re-upload CV, re-approve a couple of live
direct-ATS jobs.

### Phase 2 — Truthful verification (B1, B4)
Only mark `confirmed`/`submitted` on real evidence (confirmation phrase, known
success URL, or the form disappeared/redirected). Otherwise `failed` with the
captured reason. Drop the over-broad `"application"` keyword. Capture page HTML +
a screenshot artifact on every non-confirmed outcome, stored per job.

### Phase 3 — Complete real forms (B2)
After the standard fields, detect remaining required/invalid fields
(`required`, `aria-invalid`, Greenhouse "✱ required" labels), have Gemini answer
them (work auth = yes, etc.), fill, then submit. Re-check for validation errors
**before** declaring success.

### Phase 4 — Sourcing (H2)
Confirm the ingestion pipeline fills the queue with live, direct-ATS postings;
measure the real submit-success rate after Phases 1–3.

### Phase 5 — Observability
Persist an `apply_debug` blob (last HTML + screenshot path + step log) per job so
future failures are diagnosable without a live rerun.

---

## Already shipped this session (separate from the plan above)
- **Broken-link / parked-domain detection** (fixes the false "Verified" badge)
  — `check_url_alive` + `_link_status` now reject parked/for-sale/JS-redirect
  stub pages; a conservative re-check demotes already-"Verified" parked rows and
  surfaces them in the search notification. Regression tests added.
- **Two queue-removal reasons:** "Link is broken" and "Job no longer exists".

---

## Progress log

**2026-07-07 (round 2) — shipped:**
- **Timeout hardening** (was: frequent "Apply exceeded 120s deadline"). Apply-path
  Gemini calls now use a 25s timeout (`APPLY_LLM_TIMEOUT`); `networkidle` waits
  trimmed to 4–5s; overall deadline default 120→150s (`APPLY_DEADLINE_S`).
- **Truthful verification (B1)** — `_verify_submission`: confirmed/submitted only
  on real evidence; blocked submits reported as failed with the on-page error.
  Greenhouse/Lever/generic no longer report false "submitted".
- **Phase 3 — auto-answer required questions** — on a validation-blocked submit,
  collect the still-required/invalid fields, have Gemini answer them (work-auth =
  authorized/no-sponsorship by default; EEO/demographic = decline), fill, and
  resubmit once (bounded single recovery pass). Wired into Greenhouse + Lever.
- **Phase 5-lite — failure diagnostics** — on non-confirmed outcomes, capture the
  on-page validation text into the failure detail, plus an optional full-page
  screenshot when `APPLY_DEBUG_DIR` is set.
- **Push self-heal** — re-subscribes on load/focus when permission is granted
  (heals rotated endpoints + subscriptions lost on redeploy).

**Still open (needs the Railway volume + a live run):**
- Attach the persistent volume so the queue, CV, and push subscriptions survive
  redeploys (Phase 1). This is the likely reason the queue looked empty.
- Extend the required-question recovery to the generic career-page path and the
  Workday wizard.
- Optional `apply-selftest` admin endpoint for one-tap live diagnosis.
