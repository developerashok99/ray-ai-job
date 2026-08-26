import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import yaml
from agent.exp_filter import (
    has_experience_requirement, _EXP_FLOOR,
    INTERNSHIP_TITLE_RE, SENIOR_TITLE_RE, IRRELEVANT_TITLE_RE,
)
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger


def load_config():
    with open("config.yaml") as f:
        return yaml.safe_load(f)


def run_pipeline():
    from scrapers.linkedin_indeed import scrape_linkedin_indeed
    from scrapers.foundit         import scrape_foundit
    from scrapers.timesjobs       import scrape_timesjobs
    from scrapers.naukri          import scrape_naukri
    from agent.resume_matcher import score_jobs_batch, check_score_cache, load_resume, reset_llm_state
    from storage.database     import (
        job_exists, insert_job, get_relevant_jobs, was_recently_notified, mark_notified,
    )
    from storage.excel_export import export_to_excel

    config    = load_config()
    threshold = config["matching"]["relevance_threshold"]

    import os as _os
    _log_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "pipeline_log.txt")

    # Log rotation: keep last 2000 lines when file grows past 5000
    try:
        if _os.path.exists(_log_path):
            with open(_log_path, "r", encoding="utf-8") as _lf:
                _lines = _lf.readlines()
            if len(_lines) > 5000:
                with open(_log_path, "w", encoding="utf-8") as _lf:
                    _lf.writelines(_lines[-2000:])
    except Exception:
        pass

    def _plog(msg):
        from datetime import datetime as _dt
        line = f"[{_dt.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
        print(line)
        with open(_log_path, "a", encoding="utf-8") as _f:
            _f.write(line + "\n")

    _plog("=" * 55)
    _plog("  JobPilot AI  Pipeline Starting")
    _plog("=" * 55)

    # 1. Load resume + reset LLM fallback flags for this run
    print("\n[1/4] Loading resume...")
    load_resume(config["resume"]["path"])
    reset_llm_state()

    # 2. Scrape all sources in parallel
    print("\n[2/4] Scraping jobs from all sources (parallel)...")
    all_jobs = []

    sources = [
        ("LinkedIn/Indeed/Google", scrape_linkedin_indeed),
        ("Foundit",        scrape_foundit),
        ("TimesJobs",      scrape_timesjobs),
        ("Naukri",         scrape_naukri),
    ]

    from concurrent.futures import ThreadPoolExecutor
    import threading
    _print_lock  = threading.Lock()
    source_counts = {}

    def _run_scraper(item):
        name, fn = item
        try:
            jobs = fn()
            with _print_lock:
                source_counts[name] = len(jobs)
                print(f"  {name}: {len(jobs)} jobs")
            return jobs
        except Exception as e:
            with _print_lock:
                source_counts[name] = 0
                print(f"  {name}: ERROR — {e}")
            return []

    with ThreadPoolExecutor(max_workers=len(sources)) as _ex:
        for batch in _ex.map(_run_scraper, sources):
            all_jobs.extend(batch)

    # Per-source log
    for name, cnt in source_counts.items():
        _plog(f"  Source [{name}]: {cnt} jobs")

    # Global dedup across all sources
    seen_keys  = set()
    unique_jobs = []
    for job in all_jobs:
        key = f"{job.get('company','').lower()}|{job.get('title','').lower()}"
        if job["job_id"] not in seen_keys and key not in seen_keys:
            seen_keys.add(job["job_id"])
            seen_keys.add(key)
            unique_jobs.append(job)

    new_jobs = [j for j in unique_jobs if not job_exists(j["job_id"])]

    # Drop stale jobs (>14 days old when date is known)
    from datetime import datetime as _dt2, timedelta as _td2
    _cutoff = _dt2.now() - _td2(days=14)

    def _fresh(job):
        dp = str(job.get("date_posted") or "").strip()
        if not dp or dp in ("nan", "None"):
            return True
        try:
            return _dt2.strptime(dp[:10], "%Y-%m-%d") >= _cutoff
        except Exception:
            return True

    before_fresh = len(new_jobs)
    new_jobs     = [j for j in new_jobs if _fresh(j)]
    stale_dropped = before_fresh - len(new_jobs)
    if stale_dropped:
        print(f"  Dropped {stale_dropped} stale jobs (>14 days old)")

    _plog(f"  Total scraped: {len(unique_jobs)} unique | New: {len(new_jobs)}")

    if not new_jobs:
        _plog("  No new jobs. Exporting existing data...")
        export_to_excel(config["storage"]["excel_path"], threshold)
        return

    # 3. Score jobs — 3-pass: pre-filters → cache → batch AI
    total    = len(new_jobs)
    print(f"\n[3/4] Scoring {total} new jobs...")
    relevant = []
    needs_ai = []

    # Pass 1: instant pre-filters (zero API calls)
    for i, job in enumerate(new_jobs, 1):
        title = job.get("title", "")
        desc  = job.get("description", "") or ""

        def _skip(label, score, exp, _i=i, _title=title):
            job["relevance_score"]     = score
            job["match_reason"]        = label
            job["internship_friendly"] = 0
            job["experience_required"] = exp
            insert_job(job)
            tag = f" {score}/10 [skip] [{exp.upper()[:10]}]"
            print(f"  [{_i:>3}/{total}]{tag} {_title[:42]}")

        if INTERNSHIP_TITLE_RE.search(title):
            _skip("Internship — skipped", 1, "internship")
        elif SENIOR_TITLE_RE.search(title):
            _skip("Senior/lead role — skipped", 1, "senior")
        elif IRRELEVANT_TITLE_RE.search(title):
            _skip("Irrelevant tech/role — skipped", 1, "irrelevant")
        elif has_experience_requirement(desc):
            _skip(f"Requires {_EXP_FLOOR}+ years experience — skipped", 1, f"{_EXP_FLOOR}+ years")
        elif not desc or len(desc.strip()) < 50:
            job["relevance_score"]     = 5
            job["match_reason"]        = "No description — verify manually"
            job["internship_friendly"] = 0
            job["experience_required"] = "unknown"
            insert_job(job)
            print(f"  [{i:>3}/{total}]  5/10 [hold] [NO-DESC]  {title[:42]}")
        else:
            needs_ai.append((i, job))

    pre_filtered = total - len(needs_ai)
    print(f"\n  Pre-filtered: {pre_filtered}/{total} jobs (no AI needed)")

    # Pass 2: cache check
    still_needs_ai = []
    cache_hits     = 0
    for orig_i, job in needs_ai:
        cached = check_score_cache(job.get("title", ""), job.get("company", ""))
        if cached:
            score    = cached["score"]
            intern_f = 1 if cached["internship_friendly"] else 0
            job["relevance_score"]     = score
            job["match_reason"]        = cached["reason"]
            job["internship_friendly"] = intern_f
            job["experience_required"] = cached["experience_required"]
            insert_job(job)
            tag = "MATCH" if score >= threshold else "skip"
            print(f"  [{orig_i:>3}/{total}] {score:>2}/10 [{tag}] [CACHED]   {job['title'][:42]}")
            if score >= threshold:
                relevant.append(job)
            cache_hits += 1
        else:
            still_needs_ai.append((orig_i, job))

    if cache_hits:
        print(f"  Cache hits: {cache_hits} jobs reused from last 7 days")

    # Pass 3: batch AI scoring (5 jobs per call)
    if still_needs_ai:
        ai_jobs  = [j for _, j in still_needs_ai]
        n_batches = (len(ai_jobs) + 4) // 5
        print(f"\n  AI scoring: {len(ai_jobs)} jobs in {n_batches} batch(es) of 5...")
        results = score_jobs_batch(ai_jobs, batch_size=5)

        for (orig_i, job), result in zip(still_needs_ai, results):
            score    = int(result.get("score", 0))
            reason   = result.get("reason", "")
            intern_f = 1 if result.get("internship_friendly") else 0
            exp_req  = result.get("experience_required", "")

            job["relevance_score"]     = score
            job["match_reason"]        = reason
            job["internship_friendly"] = intern_f
            job["experience_required"] = exp_req

            if score == 0:
                # AI failed to score — do NOT insert into DB
                print(f"  [{orig_i:>3}/{total}]  0/10 [skip] [SCORE0]  {job['title'][:42]} — not saved")
                continue

            insert_job(job)

            tag = "MATCH" if score >= threshold else "skip"
            inf = " [INTERN-OK]" if intern_f else ""
            print(f"  [{orig_i:>3}/{total}] {score:>2}/10 [{tag}]{inf} {job['title'][:42]} @ {job.get('company','')[:20]}")

            if score >= threshold:
                relevant.append(job)
    else:
        print("  No jobs needed AI scoring this run.")

    _plog(f"  Pre-filtered: {pre_filtered} | Cache: {cache_hits} | AI-scored: {len(still_needs_ai)}")
    _plog(f"  Relevant (score >= {threshold}): {len(relevant)}")

    # 4. Description filler — catch hidden exp requirements before emailing
    try:
        from agent.description_filler import run_filler
        _plog(f"\n[4/4] Auto-filling descriptions for no-desc matched jobs...")
        run_filler(limit=40, rescore=True)
    except Exception as e:
        print(f"[!] Description filler error: {e}")

    # Reload relevant from DB — filler may have downgraded some jobs
    import sqlite3 as _sq3
    _conn = _sq3.connect("jobs.db")
    _conn.row_factory = _sq3.Row
    _rel_ids = [j["job_id"] for j in relevant]
    if _rel_ids:
        _ph   = ",".join("?" * len(_rel_ids))
        _rows = _conn.execute(
            f"SELECT * FROM jobs WHERE job_id IN ({_ph}) AND relevance_score >= ?",
            _rel_ids + [threshold]
        ).fetchall()
        relevant = [dict(r) for r in _rows]
    _conn.close()
    _plog(f"  After description filler: {len(relevant)} still relevant")

    # Export full Excel sheet
    export_to_excel(config["storage"]["excel_path"], threshold)

    # Drop jobs already emailed before under the same title+company — catches the
    # same real posting re-appearing via a different source/URL in a later run.
    fresh_relevant = [
        j for j in relevant
        if not was_recently_notified(j.get("title", ""), j.get("company", ""))
    ]
    dropped_dupes = len(relevant) - len(fresh_relevant)
    if dropped_dupes:
        _plog(f"  Skipped {dropped_dupes} already-notified duplicate(s) (same title+company sent before)")
    relevant = fresh_relevant

    # Send notification email
    if relevant:
        from agent.notify_email import send_report
        recipient = config.get("notify", {}).get("recipient_email")
        _plog(f"[+] Sending notification email ({len(relevant)} jobs) to {recipient or 'self'}...")
        ok = send_report(relevant, recipient=recipient, tag="[APPLY NOW]")
        _plog(f"[+] Email {'sent OK' if ok else 'FAILED'}")
        if ok:
            mark_notified([j["job_id"] for j in relevant])
    else:
        _plog("[+] No relevant jobs this run — skipping email.")

    _plog("=" * 55)
    _plog("  Pipeline Complete")
    _plog("=" * 55)


def start_scheduler():
    config = load_config()
    hours  = config["scraping"]["interval_hours"]

    scheduler = BlockingScheduler()
    scheduler.add_job(
        run_pipeline,
        trigger=IntervalTrigger(hours=hours),
        id="jobpilot_pipeline",
        name="JobPilot Pipeline",
    )

    print(f"Scheduler running — every {hours} hours. Ctrl+C to stop.\n")
    run_pipeline()

    try:
        scheduler.start()
    except KeyboardInterrupt:
        print("\nScheduler stopped.")


if __name__ == "__main__":
    import os
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    start_scheduler()
