"""Job search logic for Job Hunter."""
import json, re, os, time, traceback
import urllib.request, urllib.parse
from datetime import datetime, date
import database
from utils import ANTHROPIC_KEY, UPLOADS_DIR, load_config, deliver_notification, repair_mojibake


def run_job_search(user_id: int):
    """Search for new jobs via multi-round Anthropic web-search (one call per job title)."""
    if user_id in _search_running:
        return
    _search_running.add(user_id)
    try:
        conn = database.get_db()
        profile = conn.execute(
            "SELECT * FROM user_profiles WHERE user_id=?", (user_id,)
        ).fetchone()
        conn.close()
        if not profile:
            print(f"[run-search] No profile for user {user_id}")
            return
        import urllib.request as _ur
        import urllib.error  as _ue
        try:
            titles    = json.loads(profile["job_titles"] or "[]")
            keywords  = json.loads(profile["keywords"]   or "[]")
            locations = json.loads(profile["locations"]  or "[]")
        except Exception:
            titles, keywords, locations = [], [], ["Tel Aviv"]
        if not locations: locations = ["Tel Aviv"]
        if not titles:    titles    = ["Senior Product Manager"]
        today = datetime.now().strftime("%Y-%m-%d")

        # ── Load all existing URLs to dedup against full history ─────────
        conn = database.get_db()
        existing_urls = {r[0] for r in conn.execute(
            "SELECT url FROM jobs WHERE user_id=? AND url!='' AND status NOT IN ('rejected','expired')", (user_id,)
        ).fetchall()}
        conn.close()

        def _search_jobs_with_claude_websearch(titles_: list, locs_: list, kws_: list) -> list:
            """Search Israeli jobs via Greenhouse/Lever APIs, filter by preferences, score against CV."""
            import threading as _thr, urllib.request as _ur2, json as _js2
            all_raw = []
            _lk = _thr.Lock()

            # -- Israeli company slugs (Greenhouse) --
            _GH_COMPANIES = {
                'similarweb': 'SimilarWeb', 'taboola': 'Taboola', 'payoneer': 'Payoneer',
                'forter': 'Forter', 'riskified': 'Riskified', 'appsflyer': 'AppsFlyer',
                'fireblocks': 'Fireblocks', 'cybereason': 'Cybereason', 'jfrog': 'JFrog',
                'wizinc': 'Wiz', 'honeybook': 'HoneyBook', 'optimove': 'Optimove',
                'transmitsecurity': 'Transmit Security', 'via': 'Via', 'nice': 'NICE',
                'yotpo': 'Yotpo', 'bringg': 'Bringg', 'bigid': 'BigID',
                'axonius': 'Axonius', 'lightricks': 'Lightricks', 'catonetworks': 'Cato Networks',
                'snyk': 'Snyk', 'sentinelone': 'SentinelOne', 'monday': 'monday.com',
                'wix': 'Wix', 'fiverr': 'Fiverr', 'tipalti': 'Tipalti',
                'checkmarx': 'Checkmarx', 'rapyd': 'Rapyd', 'lemonade': 'Lemonade',
                'papayaglobal': 'Papaya Global', 'deel': 'Deel', 'drata': 'Drata',
                'hibob': 'HiBob', 'ironclad': 'Ironclad', 'nextinsurance': 'Next Insurance',
                'playtika': 'Playtika', 'gett': 'Gett', 'outbrain': 'Outbrain',
                'guardicore': 'Guardicore', 'earnix': 'Earnix', 'pentera': 'Pentera',
                'drivenets': 'DriveNets', 'orcasecurity': 'Orca Security',
                'aquasecurity': 'Aqua Security', 'seekingalpha': 'Seeking Alpha',
                'fundbox': 'Fundbox', 'ironsource': 'ironSource',
            }
            # -- Israeli company slugs (Lever) --
            _LV_COMPANIES = {
                'walkme': 'WalkMe', 'cloudinary': 'Cloudinary',
            }
            # Build title match terms from user preferences
            _terms = set()
            for _t in titles_:
                for _w in _t.lower().split():
                    if len(_w) > 2: _terms.add(_w)
            # Always include core PM terms
            _terms.update(["product", "director", "head", "lead", "chief", "vp", "group", "senior"])
            _terms = list(_terms)
            print(f"[search] Matching against terms: {_terms}")

            def _title_match(title):
                tl = title.lower()
                return any(t in tl for t in _terms)

            def _get_json(url, timeout=20):
                try:
                    rq = _ur2.Request(url, headers={"User-Agent": "JobHunter/1.0"})
                    with _ur2.urlopen(rq, timeout=timeout) as r:
                        return _js2.loads(r.read().decode("utf-8", errors="replace"))
                except Exception as e:
                    return None

            # -- Query Greenhouse boards --
            def _query_gh(slug, company_name):
                data = _get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
                if not data: return
                for j in data.get("jobs", []):
                    t = j.get("title", "")
                    if not _title_match(t): continue
                    loc = j.get("location", {}).get("name", "") if isinstance(j.get("location"), dict) else ""
                    jurl = f"https://boards.greenhouse.io/{slug}/jobs/{j.get('id', '')}"
                    with _lk:
                        all_raw.append({"job_title": t, "company": company_name,
                                        "location": loc, "url": jurl,
                                        "description": t, "source": "greenhouse"})

            # -- Query Lever boards --
            def _query_lv(slug, company_name):
                data = _get_json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
                if not isinstance(data, list): return
                for j in data:
                    t = j.get("text", "")
                    if not _title_match(t): continue
                    cats = j.get("categories") or {}
                    loc = cats.get("location", "")
                    if not loc:
                        al = cats.get("allLocations") or []
                        loc = al[0] if al else ""
                    jurl = j.get("hostedUrl") or f"https://jobs.lever.co/{slug}/{j.get('id', '')}"
                    desc = (j.get("descriptionPlain") or t)[:300]
                    with _lk:
                        all_raw.append({"job_title": t, "company": company_name,
                                        "location": loc, "url": jurl,
                                        "description": desc, "source": "lever"})
            # -- Run all API queries in parallel --
            print(f"[search] Querying {len(_GH_COMPANIES)} Greenhouse + {len(_LV_COMPANIES)} Lever boards...")
            threads = []
            for slug, name in _GH_COMPANIES.items():
                threads.append(_thr.Thread(target=_query_gh, args=(slug, name), daemon=True))
            for slug, name in _LV_COMPANIES.items():
                threads.append(_thr.Thread(target=_query_lv, args=(slug, name), daemon=True))

            # Launch in batches of 30
            for i in range(0, len(threads), 30):
                batch = threads[i:i+30]
                for t in batch: t.start()
                for t in batch: t.join(timeout=25)

            # -- TechMap product.csv (curated Israeli product jobs) --
            try:
                import csv as _csv, io as _io
                _tm_url = 'https://raw.githubusercontent.com/mluggy/techmap/main/jobs/product.csv'
                _tm_req = _ur2.Request(_tm_url, headers={"User-Agent": "JobHunter/1.0"})
                with _ur2.urlopen(_tm_req, timeout=15) as _tm_resp:
                    _tm_text = _tm_resp.read().decode('utf-8', errors='replace')
                _tm_count = 0
                for _row in _csv.DictReader(_io.StringIO(_tm_text)):
                    _tm_title = (_row.get('title') or '').strip()
                    if _title_match(_tm_title):
                        _tm_url_j = (_row.get('url') or '').strip()
                        if _tm_url_j:
                            all_raw.append({"job_title": _tm_title, "company": _row.get('company',''),
                                            "location": _row.get('city',''), "url": _tm_url_j,
                                            "description": _tm_title, "source": "techmap"})
                            _tm_count += 1
                print(f"[search] TechMap CSV: {_tm_count} product jobs matched")
            except Exception as _tme:
                print(f"[search] TechMap CSV error: {_tme}")

            # -- Comeet boards for Israeli companies --
            _comeet_slugs = {'monday': 'monday.com', 'ironsource': 'ironSource', 'gong': 'Gong',
                             'yotpo2': 'Yotpo', 'lightricks2': 'Lightricks'}
            for _cm_slug, _cm_name in _comeet_slugs.items():
                try:
                    _cm_url = f'https://www.comeet.co/careers/api/{_cm_slug}/positions'
                    _cm_data = _get_json(_cm_url, timeout=12)
                    if isinstance(_cm_data, list):
                        for _cm_j in _cm_data:
                            _cm_t = _cm_j.get('name', '')
                            if _title_match(_cm_t):
                                _cm_loc = ''
                                if _cm_j.get('location'):
                                    _cm_loc = _cm_j['location'].get('name', '') if isinstance(_cm_j['location'], dict) else str(_cm_j['location'])
                                all_raw.append({"job_title": _cm_t, "company": _cm_name,
                                                "location": _cm_loc, "url": _cm_j.get('url',''),
                                                "description": _cm_t, "source": "comeet"})
                except Exception:
                    pass
            print(f"[search] Comeet + SpeakNow queries done")

            # -- SpeakNow careers --
            try:
                _sn_req = _ur2.Request('https://speaknow.co/careers/', headers={"User-Agent": "JobHunter/1.0"})
                with _ur2.urlopen(_sn_req, timeout=12) as _sn_resp:
                    _sn_html = _sn_resp.read().decode('utf-8', errors='replace')
                import re as _re_sn
                # Find job links on the careers page
                _sn_links = _re_sn.findall(r'href=["\'](https?://[^"\'>]*(?:career|job|position|apply)[^"\'>]*)["\'\s>]', _sn_html, _re_sn.IGNORECASE)
                for _sn_url in set(_sn_links[:10]):
                    all_raw.append({"job_title": "SpeakNow Career Opportunity", "company": "SpeakNow",
                                    "location": "Israel", "url": _sn_url,
                                    "description": "Career opportunity at SpeakNow", "source": "speaknow"})
            except Exception as _sne:
                print(f"[search] SpeakNow error: {_sne}")

            print(f"[search] Pre-filter: {len(all_raw)} title-matched jobs from {len(_GH_COMPANIES)+len(_LV_COMPANIES)} companies")

            if not all_raw:
                return []

            # -- Score against user CV + preferences using Claude Haiku --
            # Fetch CV text from DB
            try:
                _conn2 = database.get_db()
                _prof2 = _conn2.execute(
                    "SELECT cv_text FROM user_profiles WHERE user_id=?",
                    (user_id,)
                ).fetchone()
                _conn2.close()
                _cv_text = (_prof2["cv_text"] or "") if _prof2 else ""
            except Exception:
                _cv_text = ""

            profile_text = (
                f"Target roles: {', '.join(titles_[:4])}\n"
                f"Key skills: {', '.join(kws_[:10])}\n"
                f"Locations: {', '.join(locs_)} or Remote\n"
                f"Seniority: Senior / Director / VP / Head-of"
            )
            if _cv_text:
                profile_text += f"\n\nCV Summary (first 1500 chars):\n{_cv_text[:1500]}"
            # Score in batches of 25
            scored_jobs = []
            for batch_i in range(0, len(all_raw), 25):
                batch = all_raw[batch_i:batch_i+25]
                jobs_json = _js2.dumps(
                    [{"job_title": j.get("job_title",""), "company": j.get("company",""),
                      "location": j.get("location",""), "url": j.get("url",""),
                      "description": (j.get("description") or "")[:200]}
                     for j in batch], ensure_ascii=False)

                prompt = (
                    "You are a job matching assistant. Review these job listings and score each "
                    f"for this candidate:\n\n{profile_text}\n\n"
                    f"Job listings (JSON):\n{jobs_json}\n\n"
                    "Return ONLY a JSON array with fields: job_title, company, location, url, "
                    "description (2-3 sentences), candidate_score (0-100), fit_reason (1-2 sentences). "
                    "Only include jobs with candidate_score >= 40. Return ONLY valid JSON, no markdown."
                )
                try:
                    body = _js2.dumps({"model": "claude-haiku-4-5-20251001", "max_tokens": 4096,
                        "messages": [{"role": "user", "content": prompt}]}).encode()
                    req = _ur2.Request("https://api.anthropic.com/v1/messages", data=body, method="POST",
                        headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01",
                                 "content-type": "application/json"})
                    with _ur2.urlopen(req, timeout=60) as resp:
                        result = _js2.loads(resp.read())
                    scored_text = ""
                    for blk in result.get("content", []):
                        if blk.get("type") == "text":
                            scored_text += blk["text"]
                    scored_text = scored_text.strip()
                    if "```" in scored_text:
                        scored_text = scored_text.split("```")[1]
                        if scored_text.startswith("json"): scored_text = scored_text[4:]
                    # Try parse JSON
                    si = scored_text.rfind("["); ei = scored_text.rfind("]")
                    if si >= 0 and ei > si:
                        parsed = _js2.loads(scored_text[si:ei+1])
                        for j in parsed:
                            if isinstance(j, dict) and j.get("url") and j.get("candidate_score", 0) >= 40:
                                j.setdefault("match_score", j.get("candidate_score", 0))
                                j.setdefault("found_date", today)
                                j.setdefault("source", "greenhouse/lever")
                                scored_jobs.append(j)
                    print(f"[search] Batch {batch_i//25+1}: scored {len(batch)} -> {len([j for j in scored_jobs if j not in scored_jobs[:batch_i]])} passed")
                except Exception as e:
                    print(f"[search] Scoring error: {e}")
                    # Fallback: include jobs with default score if scoring fails
                    for j in batch:
                        j["candidate_score"] = 65
                        j["match_score"] = 65
                        j["fit_reason"] = "Title match (scoring unavailable)"
                        j["found_date"] = today
                        j["source"] = "greenhouse/lever"
                        scored_jobs.append(j)


            # -- Supplemental: Claude web_search for LinkedIn/Glassdoor/Wellfound --
            try:
                _ws_sites = 'linkedin.com/jobs, glassdoor.com, wellfound.com/jobs'
                for _ws_title in titles_[:2]:
                    try:
                        _ws_prompt = (
                            f'Search for "{_ws_title}" job listings in {", ".join(locs_[:2]) or "Israel"}. '
                            f'Search these sites: {_ws_sites}. '
                            'Find 5 real current job listings. '
                            'Respond with ONLY a JSON array. '
                            'Each item: {"job_title":"...","company":"...","location":"...","url":"...","description":"..."}'
                        )
                        _ws_body = _js2.dumps({
                            'model': 'claude-sonnet-4-6', 'max_tokens': 4096,
                            'tools': [{'type': 'web_search_20250305', 'name': 'web_search', 'max_uses': 5}],
                            'messages': [{'role': 'user', 'content': _ws_prompt}]
                        }).encode()
                        _ws_req = _ur2.Request('https://api.anthropic.com/v1/messages', data=_ws_body,
                                               headers={'x-api-key': ANTHROPIC_KEY,
                                                        'anthropic-version': '2023-06-01',
                                                        'content-type': 'application/json'})
                        with _ur2.urlopen(_ws_req, timeout=120) as _ws_resp:
                            _ws_result = _js2.loads(_ws_resp.read())
                        for _ws_blk in _ws_result.get('content', []):
                            if _ws_blk.get('type') != 'text': continue
                            _ws_txt = _ws_blk['text'].strip()
                            _ws_si = _ws_txt.rfind('['); _ws_ei = _ws_txt.rfind(']')
                            if _ws_si >= 0 and _ws_ei > _ws_si:
                                try:
                                    for _ws_j in _js2.loads(_ws_txt[_ws_si:_ws_ei+1]):
                                        if isinstance(_ws_j, dict) and _ws_j.get('url'):
                                            _ws_j.setdefault('candidate_score', 70)
                                            _ws_j.setdefault('match_score', 70)
                                            _ws_j.setdefault('fit_reason', 'Found via web search')
                                            _ws_j.setdefault('source', 'web_search')
                                            scored_jobs.append(_ws_j)
                                except Exception:
                                    pass
                        print(f"[search] Web search for '{_ws_title}': added jobs")
                    except Exception as _wse:
                        print(f"[search] Web search error for '{_ws_title}': {_wse}")
            except Exception as _wse2:
                print(f"[search] Web search supplemental skipped: {_wse2}")

            print(f"[search] Final: {len(scored_jobs)} scored jobs (from {len(all_raw)} pre-filtered)")
            return scored_jobs

        all_jobs_data = []
        seen_urls     = set(existing_urls)
        seen_key      = set()

        # Search real job sites via Claude web_search (LinkedIn, AllJobs, Drushim, etc.)
        jobs_data = _search_jobs_with_claude_websearch(titles, locations, keywords)
        print(f"[run-search] Found {len(jobs_data)} jobs via Claude web_search")

        for j in jobs_data:
            jurl = (j.get("url") or "").strip()
            jkey = (j.get("job_title","").lower().strip(), j.get("company","").lower().strip())
            if jurl and jurl in seen_urls: continue
            if not jurl and jkey in seen_key: continue
            if jurl: seen_urls.add(jurl)
            if jkey: seen_key.add(jkey)
            all_jobs_data.append(j)

        if not all_jobs_data:
            database.log_activity(user_id, "jobs_searched", "Search returned no new results")
            deliver_notification(user_id, f"🔍 Search Complete — {today}\n\nNo new jobs found this run.", url_suffix="/dashboard#new")
            return

        # ── URL check for new jobs ───────────────────────────────────────
        import apply_engine as _ae
        from concurrent.futures import ThreadPoolExecutor as _TPE
        _new_urls = {j.get("url","").strip() for j in all_jobs_data if j.get("url")}
        _url_ok   = {}
        with _TPE(max_workers=8) as _ex:
            _futs = {_ex.submit(_ae.check_url_alive, u): u for u in _new_urls}
            for _f, _u in _futs.items():
                try:    _url_ok[_u] = 1 if _f.result(timeout=12) else 0
                except: _url_ok[_u] = 0
        _chk_date = datetime.now().isoformat()

        # ── Insert new jobs ──────────────────────────────────────────────
        conn = database.get_db(); inserted = 0; new_jobs_info = []
        for j in all_jobs_data:
            if j.get("match_score", 0) <= 0:
                continue
            try:
                _jurl = (j.get("url") or "").strip()
                if _jurl and _url_ok.get(_jurl) == 0:
                    continue  # skip dead links
                # Dedup: skip if same company+title already exists for this user
                _jtitle = j.get("job_title", "").strip().lower()
                _jcomp = j.get("company", "").strip().lower()
                if _jtitle and _jcomp:
                    dup = conn.execute(
                        "SELECT id FROM jobs WHERE user_id=? AND LOWER(TRIM(title))=? AND LOWER(TRIM(company))=?",
                        (user_id, _jtitle, _jcomp)
                    ).fetchone()
                    if dup:
                        continue  # duplicate job from different source
                conn.execute(
                    "INSERT OR IGNORE INTO jobs "
                    "(user_id,title,company,location,url,description,why_relevant,source,"
                    "found_date,match_score,candidate_score,status,url_verified,url_check_date) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,'new',?,?)",
                    (user_id, j.get("job_title",""), j.get("company",""), j.get("location",""),
                     _jurl, j.get("description",""), j.get("fit_reason",""), j.get("source",""),
                     j.get("found_date",today), j.get("match_score",0), j.get("candidate_score",0),
                     _url_ok.get(_jurl) if _jurl else None, _chk_date if _jurl else None))
                inserted += 1
                new_jobs_info.append({"title":j.get("job_title",""),"company":j.get("company",""),"url_ok":_url_ok.get(_jurl) if _jurl else None})
            except Exception as e: print(f"[run-search] insert error: {e}")
        conn.commit(); conn.close()

        # ── URL check ALL historical unverified jobs ─────────────────────
        conn = database.get_db()
        unverified = conn.execute(
            "SELECT id, url FROM jobs WHERE user_id=? AND url_verified IS NULL AND url!=''", (user_id,)
        ).fetchall()
        conn.close()
        hist_alive = hist_dead = 0
        if unverified:
            with _TPE(max_workers=8) as _ex2:
                _futs2 = {_ex2.submit(_ae.check_url_alive, r["url"]): r for r in unverified}
                hist_results = {}
                for _f2, _r2 in _futs2.items():
                    try:    hist_results[_r2["id"]] = 1 if _f2.result(timeout=12) else 0
                    except: hist_results[_r2["id"]] = 0
            conn = database.get_db()
            for job_id, ok in hist_results.items():
                conn.execute("UPDATE jobs SET url_verified=?, url_check_date=? WHERE id=?",(ok,_chk_date,job_id))
                if ok: hist_alive += 1
                else:  hist_dead  += 1
            conn.commit(); conn.close()

        database.log_activity(user_id, "jobs_searched", f"Found {inserted} new job(s) across {len(titles)} title search(es)")

        # ── Consolidated search notification ─────────────────────────────
        notif_lines = [f"🔍 Search Complete — {today}"]
        if inserted > 0:
            notif_lines.append(f"\n📋 {inserted} new job(s) added for review:")
            for info in new_jobs_info[:10]:
                icon = "🔗" if info["url_ok"]==1 else ("⚠️" if info["url_ok"]==0 else "")
                notif_lines.append(f"  • {info['title']} @ {info['company']} {icon}".rstrip())
            if len(new_jobs_info)>10: notif_lines.append(f"  … and {len(new_jobs_info)-10} more")
        else:
            notif_lines.append("\nNo new jobs inserted (all already in history).")
        if (hist_alive+hist_dead)>0:
            notif_lines.append(f"\n🔄 Re-checked {hist_alive+hist_dead} existing job URL(s):")
            notif_lines.append(f"  ✅ {hist_alive} alive  ❌ {hist_dead} dead")
        deliver_notification(user_id, "\n".join(notif_lines), url_suffix="/dashboard#new")
        print(f"[run-search] user {user_id}: inserted={inserted} hist_checked={hist_alive+hist_dead}")

    except Exception as e:
        print(f"[run-search] Error: {e}")
        database.log_activity(user_id, "jobs_searched", "Job search failed — will retry at next scheduled time")
    finally:
        _search_running.discard(user_id)

