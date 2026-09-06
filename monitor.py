"""
JobPilot AI — Health Monitor (read-only diagnostics, run on demand via /check-monitor)

Checks:
  1. Data freshness — when did MongoDB last see a new job? Alerts (does NOT auto-restart
     anything) if stale, since the real deployment is GitHub Actions cron now, not a
     persistent local process — auto-spawning a competing local scheduler.py here would
     race the real pipeline on the same MongoDB. If this alerts, check the Actions tab.
  2. DB quality — sample recent matched jobs for experience slippage
  3. Score distribution — flag if too many high scores (LLM being too generous)
  4. Log tail — look for repeated errors
Writes monitor_log.txt with each check result.
"""
import os
import subprocess
import sys
from datetime import datetime, timedelta

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from agent.exp_filter import has_experience_requirement, find_experience_snippet, SENIOR_TITLE_RE
from storage.database import (
    get_last_scraped_time, get_recent_high_score_jobs, get_score_distribution, get_db_stats,
)

LOG_FILE  = "monitor_log.txt"


def _log(msg: str):
    ts  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ── 1. Process check ──────────────────────────────────────────────────────────
#
# Only meaningful if scheduler.py is being run as a persistent local process
# (python scheduler.py). The real deployment is GitHub Actions cron now, which
# runs the pipeline once per trigger in a fresh container and exits — there's
# no persistent process to find there, so this will correctly report False in
# that context. The real health signal in that model is _pipeline_last_job_time()
# below (when did the last run actually write to MongoDB), not this check.

def _is_pipeline_running() -> bool:
    """Return True if scheduler.py is running as a local Python process (Windows only)."""
    try:
        result = subprocess.run(
            ["powershell", "-Command",
             "Get-CimInstance Win32_Process | "
             "Where-Object { $_.Name -like 'python*' -and $_.CommandLine -like '*scheduler.py*' } | "
             "Measure-Object | Select-Object -ExpandProperty Count"],
            capture_output=True, text=True,
        )
    except OSError:
        # No PowerShell on this platform (Linux/Mac, or a GitHub Actions runner) —
        # not an error, just means this particular check doesn't apply here.
        return False
    try:
        return int(result.stdout.strip()) > 0
    except Exception:
        return False


def _pipeline_last_job_time() -> datetime | None:
    """Return datetime of the most recently scraped job, or None."""
    return get_last_scraped_time()


# ── 2. DB quality check ───────────────────────────────────────────────────────

def _check_db_quality() -> dict:
    """
    Sample recent high-score jobs and look for false positives:
    - Senior roles that slipped through
    - Jobs where description mentions too many years (over the candidate's level) but score >= 7
    Returns dict with findings.
    """
    issues = []
    last_scraped = get_last_scraped_time()
    if last_scraped is None:
        return {"issues": ["No jobs in DB"], "total_checked": 0}
    run_start = (last_scraped - timedelta(hours=4)).isoformat()

    rows = get_recent_high_score_jobs(min_score=7, since=run_start, limit=100)

    exp_slipped = []
    senior_slipped = []
    no_desc_high = []

    for r in rows:
        title = str(r["title"] or "")
        desc  = str(r["description"] or "").strip()
        score = r["relevance_score"]

        if SENIOR_TITLE_RE.search(title):
            senior_slipped.append(f"{title} @ {r['company']} (score={score})")
            continue

        if desc in ("nan", "", "None"):
            no_desc_high.append(f"{title} @ {r['company']} (score={score})")
            continue

        snippet = find_experience_snippet(desc)
        if snippet:
            exp_slipped.append(f"{title} @ {r['company']} (score={score}) -- [{snippet}]")

    if senior_slipped:
        issues.append(f"SENIOR SLIPPAGE ({len(senior_slipped)} jobs): " + " | ".join(senior_slipped[:3]))
    if exp_slipped:
        issues.append(f"EXP SLIPPAGE ({len(exp_slipped)} jobs): " + " | ".join(exp_slipped[:3]))
    if len(no_desc_high) > 10:
        issues.append(f"NO-DESCRIPTION HIGH SCORE: {len(no_desc_high)} matched jobs have no description (exp unknown)")

    return {"issues": issues, "total_checked": len(rows),
            "exp_slipped": len(exp_slipped), "senior_slipped": len(senior_slipped),
            "no_desc_high": len(no_desc_high)}


# ── 3. Score distribution sanity check ───────────────────────────────────────

def _check_score_distribution() -> dict:
    return get_score_distribution(since_days=3)


# ── 4. Recent DB stats ────────────────────────────────────────────────────────

def _db_stats() -> dict:
    return get_db_stats()


# ── Main ──────────────────────────────────────────────────────────────────────

def run_monitor():
    _log("=" * 60)
    _log("Monitor check starting")

    # 1. Pipeline process
    running = _is_pipeline_running()
    last_job_time = _pipeline_last_job_time()
    _log(f"  Pipeline process running : {running}")
    if last_job_time:
        age = datetime.now() - last_job_time
        _log(f"  Last job scraped         : {last_job_time.strftime('%H:%M')} ({int(age.total_seconds()//60)} min ago)")
    else:
        _log("  Last job scraped         : unknown (DB empty or new)")

    # Restart if not running AND last job was more than interval + 30 min ago
    try:
        import yaml
        with open("config.yaml") as _cf:
            _cfg = yaml.safe_load(_cf)
        _interval_h = _cfg["scraping"]["interval_hours"]
    except Exception:
        _interval_h = 2
    stale_threshold = (_interval_h + 0.5) * 3600
    stale = (last_job_time is None or
             (datetime.now() - last_job_time).total_seconds() > stale_threshold)
    if not running and stale:
        _log("  ALERT: No new jobs scraped recently — check the GitHub Actions "
             "'JobPilot Pipeline' workflow (Actions tab) for a failed/disabled run.")
    elif not running:
        _log("  Pipeline not running but jobs are fresh — pipeline just finished, OK.")

    # 2. DB stats
    stats = _db_stats()
    if stats:
        _log(f"  DB total jobs            : {stats['total']}")
        _log(f"  Scraped today            : {stats['new_today']}")
        _log(f"  Jobs with HR email       : {stats['with_email']}")
        _log(f"  Matched jobs, no desc    : {stats['no_description_matched']} (yellow = verify exp manually)")

    # 3. Quality check — experience and senior slippage
    quality = _check_db_quality()
    _log(f"  Quality check on         : {quality['total_checked']} recent matched jobs")
    if quality["issues"]:
        for issue in quality["issues"]:
            _log(f"  QUALITY ISSUE: {issue}")
        # Log to a separate file for review
        with open("quality_issues.txt", "a", encoding="utf-8") as f:
            f.write(f"\n[{datetime.now()}]\n")
            for iss in quality["issues"]:
                f.write(f"  {iss}\n")
    elif quality["total_checked"] > 0:
        _log("  Quality: OK — no experience/senior slippage detected")
    else:
        _log("  Quality: no recent matched jobs to check")

    # 4. Score distribution
    dist = _check_score_distribution()
    if dist:
        pct = dist.get("high_score_pct", 0)
        _log(f"  High-score (>=7) jobs    : {pct}% of last-3-day jobs")
        if pct > 40:
            _log(f"  WARNING: {pct}% high-score rate is suspicious — LLM may be too generous")
        dist_str = " | ".join(f"{k}:{v}" for k, v in sorted(dist.get("distribution", {}).items(), reverse=True)[:6])
        _log(f"  Score dist (recent)      : {dist_str}")

    _log("Monitor check complete")
    _log("=" * 60)

    return {
        "pipeline_running": running,
        "restarted": not running and stale,
        "quality_issues": quality.get("issues", []),
        "stats": stats,
    }


if __name__ == "__main__":
    result = run_monitor()
    if result.get("quality_issues"):
        print(f"\n[!] {len(result['quality_issues'])} quality issue(s) found -- see quality_issues.txt")
    else:
        print("\n[OK] System healthy")
