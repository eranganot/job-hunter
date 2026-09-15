# How the match percentage is calculated

_2026-09-15. Written from the code, with line references. There is a real bug at
the end — §4._

A job carries **three** numbers. Only one is shown as "N% match".

| Column | Range | Who writes it | Shown as |
|---|---|---|---|
| `match_score` | 0–100 | the search scorer, or the background rescorer | **"N% match"** |
| `candidate_score` | 0–100 | same, usually identical to `match_score` | nothing in `/app` |
| `feedback_penalty` | 0–60 | `compute_feedback_penalty` on every jobs load | **nothing in `/app`** ← the bug |

---

## 1. The normal path — the search scorer (`app.py:1417`)

Almost every job you see was scored here, by Gemini, against your CV (the PDF
itself when one is on file) and your stated targets. The rubric is given to the
model explicitly and sums to 100:

| Dimension | Points | Top band |
|---|---:|---|
| **Title / function match** | 30 | 27–30: matches a target role or your CV's actual role |
| **Seniority match** | 20 | 17–20: exact level |
| **Location** | 20 | 17–20: your target locations, hybrid, or explicitly remote |
| **CV vs job requirements** | 30 | "most important dimension" per the prompt |

Jobs scoring **< 30** are dropped and never inserted (`app.py:1376`).
`match_score` is then set to `candidate_score` — they are the same number
(`app.py:1536`).

**So a 97% is: title ~30 + seniority ~20 + location ~20 + CV fit ~27.**

## 2. The fallback — rule-based (`app.py:~1500`)

If Gemini fails or is out of budget, an accumulator runs instead: points for
title-word hits, skill hits, and +10 for a location match, **capped at 95**.
Different arithmetic, same column. A job scored this way is not comparable with
one scored by §1 — and nothing in the UI says which happened.

## 3. The other rubric — `compute_match_score` (`ai_analysis.py:330`)

A **completely different** weighting, used by the background rescorer at
`app.py:6175` for any job whose `match_score` is NULL:

| Dimension | Points |
|---|---:|
| Skills / keyword fit | 60 |
| Job title relevance | 30 |
| Seniority | 10 |

**Location is worth 0 here and 20 points in §1.** Both write `match_score`.
In practice §1 covers most jobs (it always sets a score, so §3's NULL gate rarely
fires), but a job inserted by the ingestion adapters without a score gets §3 —
and its percentage means something else entirely.

## 4. The bug: `/app` throws away the penalty 🔴

`compute_feedback_penalty` (`ai_analysis.py:169`) demotes a job by up to **60
points** based on what you have passed on before:

| Signal | Penalty |
|---|---:|
| Company you flagged as bad | 45 |
| Passed on this company repeatedly (weight ≥ 1.5) | 20 |
| Passed on this company once (weight ≥ 0.5) | 8 |
| …plus title and location signals | up to 60 total |

**The API does this correctly.** `/api/jobs` orders by
`(COALESCE(match_score,-1) - COALESCE(feedback_penalty,0)) DESC` in SQL
(`app.py:6096`), re-sorts in Python by the same expression (`:6142`), and sends
the whole row — penalty included.

**The legacy UI does this correctly.** It shows `match_score` as the percentage
*and* renders a separate orange badge — `⬇ You've passed on this company before`
— from `feedback_penalty` / `feedback_reason` (`app.py:4845`).

**`/app` does neither.** `toUiJob` (`web/src/api/client.ts:203`) maps
`why_relevant`, `match_score`, `candidate_score` … and **never maps
`feedback_penalty` or `feedback_reason`**. So the PWA:

1. **cannot show the demotion badge** — the fields are not in its model; and
2. **re-sorts client-side by raw `matchScore`** (`App.tsx:497`), which
   **overrides the effective-score order the API just produced.**

That is what Eran was seeing. In the legacy screenshot the same job reads
**97% match** *and* `⬇ Location you've passed on`. In `/app` it reads 97% and
sits at the top of the queue, with the demotion invisible and undone.

**Worked example.** *Head of Product – Portfolio*, match 97, in a location you
have passed on (penalty 20):

```
API order      : effective 97 - 20 = 77   → ranked below an unpenalised 82
/app order     : 97                       → ranked first
/app display   : "97% match", no badge
legacy display : "97% match"  +  ⬇ Location you've passed on
```

**The fix** is to carry the two fields through `toUiJob`, sort by
`matchScore - feedbackPenalty`, and show the reason on the card. The percentage
itself stays as `match_score` so it keeps meaning "fit against your CV", with
the demotion shown beside it rather than baked into the number — which is what
the legacy UI does and what the penalty's own docstring describes.
