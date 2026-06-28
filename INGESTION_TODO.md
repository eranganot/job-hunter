# Ingestion Pipeline — Your To-Do List

_Tracking what **you** need to do to take the multi-source ingestion pipeline from
"deployed" to "fully live and tuned." Sorted by value ÷ effort (quick wins first)._

**Status legend:** ☐ todo  ◐ in progress  ☑ done
**How env vars work:** add them in Railway → your service → **Variables**. Saving a
variable auto-triggers a redeploy. Leave any key blank → that source stays dormant
(never errors). Paid sources only run for the **admin** account (you).

---

## TIER 1 — Do now (high value, low effort)

### ☐ 1. Confirm the deploy is live and send me one search's logs
**Value: Critical · Effort: ~10 min.** This unblocks everything: it proves the free
Big-Tech sources work and tells me which (if any) endpoints drifted so I can fix them.
1. Railway → your Job Hunter service → **Deployments** → latest → confirm badge is **Active** (not Crashed/Failed).
2. In **Deploy Logs**, confirm pip installed the new deps (search the log for `rapidfuzz`, `pydantic`, `httpx`).
3. Trigger a job search from the app (or wait for the daily run).
4. In the logs, copy every line containing `[ingest]` or `[search] Multi-source fuzzy dedup` and paste them back to me.
   - Expect lines like: `external sources (role=admin): amazon:ok(60), apple:ok(0), google:ok(0), meta:skipped_no_creds(0), microsoft:error(0)` and `Multi-source fuzzy dedup: 320 -> 271 canonical jobs`.
**Send me:** those log lines. → I'll tell you which Big-Tech endpoints need a fix.

### ☐ 2. Decide the retain threshold + confirm admin email
**Value: Medium · Effort: ~2 min.**
1. In Railway Variables, confirm `ADMIN_EMAIL=eran.ganot@gmail.com` (gives you the paid sources).
2. Set `INGEST_SCORE_THRESHOLD=30` (you asked for 30%+). Raise later if too noisy.
**Send me:** nothing — just set and save.

### ☐ 3. Sign up for Adzuna (free tier) — global/remote roles
**Value: Medium · Effort: ~5 min · Free.** Note: Adzuna has **no Israel feed**, so this only adds global/remote jobs. Skip if you only want Israel.
1. Register: https://developer.adzuna.com/signup
2. Copy your **app_id** and **app_key** from the dashboard: https://developer.adzuna.com/
3. (Optional) Test endpoints interactively: https://developer.adzuna.com/activedocs
4. In Railway add: `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`, and `ADZUNA_COUNTRY` (e.g. `gb` or `us` — pick where you'd consider remote roles).
**Send me:** nothing — it activates itself once keys are set.

### ☐ 4. Sign up for TheirStack (free 50 credits) — technographic + Israel coverage
**Value: High · Effort: ~10 min · Free trial.** Best paid source for "companies using X stack, hiring in Israel."
1. Create an account + see plans: https://theirstack.com/en/pricing (free trial = 50 credits).
2. Build a test query in their app, then grab your API key (Bearer token). API reference: https://api.theirstack.com/
3. In Railway add: `THEIRSTACK_API_KEY`.
4. Tell me the **tech stacks / companies** you want to target (e.g. "companies using Segment + Amplitude in Israel") so I can tune the technographic filter.
**Send me:** your target stacks/keywords (decision #D2 below).

---

## TIER 2 — High value, moderate effort

### ☐ 5. Apify — localized Israeli boards (AllJobs, Drushim)
**Value: High (best Israel coverage) · Effort: ~30–45 min.** Each Apify "Actor" has its **own input schema**, so I need to map our adapter to whichever actor you pick.
1. Create an account: https://console.apify.com/
2. Pick the Israeli board scrapers you want from the store (jobs category: https://apify.com/store/categories/jobs):
   - AllJobs.co.il → https://apify.com/agentx/all-jobs-scraper
   - Drushim.co.il → https://apify.com/blackfalcondata/drushim-scraper (alt: https://apify.com/swerve/drushim-scraper)
3. Open each actor → run it once manually with a sample search to confirm it works and note its **Actor ID** (the `user~actor` slug in the URL) and its **Input** fields (the JSON on the "Input" tab).
4. Get your API token: https://console.apify.com/account/integrations
5. In Railway add: `APIFY_TOKEN` and `APIFY_ACTORS=agentx~all-jobs-scraper,blackfalcondata~drushim-scraper` (comma-separated, `~` not `/`).
**Send me:** for each actor — its slug + a screenshot/paste of its **Input** schema (field names). → I'll wire the adapter's input mapping to match (our current mapping is a generic guess and will need per-actor tweaks).

### ☐ 6. Residential proxies (Bright Data or similar)
**Value: High if you scale scraping · Effort: ~30 min + KYC wait.** Needed so aggressive Big-Tech scraping and JobSpy don't get the server IP banned. Without it, Big-Tech runs direct (best-effort).
1. Sign up: https://brightdata.com/ (alt: https://oxylabs.io/). Choose a **Residential** proxy zone.
2. Complete KYC (can take a few hours — start early).
3. From the zone, copy: username (looks like `brd-customer-xxxx-zone-yyyy`), password, host (`brd.superproxy.io`), port (`22225`).
4. In Railway add: `PROXY_USERNAME`, `PROXY_PASSWORD`, `PROXY_HOST`, `PROXY_PORT`, and keep `PROXY_ENABLED=1`.
**Send me:** nothing — proxies activate on admin runs automatically. (Cost note: residential proxies bill per GB — start with a small cap.)

### ☐ 7. Unlock Meta careers (GraphQL doc_id)
**Value: Medium · Effort: ~10 min.** Meta's search uses a rotating persisted-query id; it self-skips until you supply one.
1. Open https://www.metacareers.com/jobs in Chrome.
2. Open DevTools (F12) → **Network** tab → filter for `graphql`.
3. Type a search (e.g. "product manager") on the page so a `graphql` request fires.
4. Click that request → **Payload/Form Data** → find `doc_id` → copy the long number.
5. In Railway add: `META_GRAPHQL_DOC_ID=<that number>`.
**Send me:** the `doc_id` if you want me to verify the Meta adapter against it. (It rotates every few weeks — re-grab if Meta goes quiet.)

---

## TIER 3 — Optional / higher effort / lower marginal value

### ☐ 8. JobSpy (LinkedIn / Indeed / Glassdoor aggregation)
**Value: Medium (overlaps existing LinkedIn/Indeed) · Effort: ~20 min.** Adds a second path into the big boards; pulls in `pandas` (heavier image) and really wants proxies (task #6 first).
1. Review the library: https://github.com/speedyapply/JobSpy · https://pypi.org/project/python-jobspy/
2. Decide if you want it (it duplicates some of what app.py's LinkedIn/Indeed already do — value is mainly Glassdoor + richer fields).
3. If yes: tell me, and I'll add `python-jobspy` to `requirements.txt` (I left it out to keep the image lean). Set `JOBSPY_ENABLED=1` (already default).
**Send me:** a yes/no.

### ☐ 9. Coresignal (normalized jobs dataset)
**Value: Medium · Effort: ~30 min, usually a sales call.** Bulk normalized datasets; most useful if you want firmographic enrichment, not just listings.
1. Request access / pricing: https://coresignal.com/
2. Get your API key, add `CORESIGNAL_API_KEY` in Railway.
**Send me:** nothing — self-activates. Lowest priority unless you specifically want their dataset.

---

## DECISIONS I need from you (these tune the pipeline, not code)

- **D1 — Budget:** rough monthly $ you're willing to spend on paid sources? (Sets the credit caps `*_MONTHLY_CAP` so we never overspend.)
- **D2 — Targets:** your exact target **titles**, **seniority**, **keywords**, and **must-have companies / tech stacks**. The paid sources (esp. TheirStack) are only as good as this.
- **D3 — Geography:** Israel-only, or Israel + specific remote/global? (Decides whether Adzuna is worth it.)
- **D4 — Free-user proxies:** keep proxies admin-only (current), or let the whole user base benefit from your proxy spend? (Default: admin-only.)

---

## ONGOING (after it's live)

- ☐ **Tune the dedup threshold** — once I see real cross-source volume in the logs, I may nudge the fuzzy threshold (default 88) so we don't over- or under-merge. No action from you beyond sending logs.
- ☐ **Watch credit usage** — the pipeline logs each paid source's status (`ok` / `no_credits` / `skipped_no_creds` / `unavailable`); glance at these weekly.
- ☐ **Re-grab Meta `doc_id`** if Meta results dry up (it rotates).

---

### Fastest path to value
Do **#1** (logs) → **#2** (threshold) → **#4** (TheirStack, free, best Israel signal). That's ~25 minutes and gets you real incremental coverage before spending on proxies or Apify.
