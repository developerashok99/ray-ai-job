"""
JobPilot AI — Autonomous Health Monitor
Checks every 30 minutes:
  1. Pipeline process alive → restart if dead
  2. DB quality — sample recent matched jobs for experience slippage
  3. Score distribution — flag if too many high scores (LLM being too generous)
  4. Log tail — look for repeated errors
Writes monitor_log.txt with each check result.
"""
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from agent.exp_filter import has_experience_requirement, find_experience_snippet, SENIOR_TITLE_RE

LOG_FILE  = "monitor_log.txt"
DB_PATH   = "jobs.db"


def _log(msg: str):
    ts  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ── 1. Process check ──────────────────────────────────────────────────────────

def _is_pipeline_running() -> bool:
    """Return True if scheduler.py is running as a Python process."""
    result = subprocess.run(
        ["powershell", "-Command",
         "Get-CimInstance Win32_Process | "
         "Where-Object { $_.Name -like 'python*' -and $_.CommandLine -like '*scheduler.py*' } | "
         "Measure-Object | Select-Object -ExpandProperty Count"],
        capture_output=True, text=True
    )
    try:
        return int(result.stdout.strip()) > 0
    except Exception:
        return False


def _pipeline_last_job_time() -> datetime | None:
    """Return datetime of the most recently scraped job, or None."""
    if not os.path.exists(DB_PATH):
        return None
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT MAX(date_scraped) FROM jobs"
    ).fetchone()
    conn.close()
    if row and row[0]:
        try:
            return datetime.fromisoformat(row[0])
        except Exception:
            return None
    return None


def _restart_pipeline():
    _log("  ACTION: Restarting pipeline (scheduler.py)...")
    subprocess.Popen(
        [sys.executable, "scheduler.py"],
        cwd=os.path.dirname(os.path.abspath(__file__)),
        creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0,
    )
    _log("  ACTION: Pipeline restart issued.")


# ── 2. DB quality check ───────────────────────────────────────────────────────

def _check_db_quality() -> dict:
    """
    Sample recent high-score jobs and look for false positives:
    - Senior roles that slipped through
    - Jobs where description mentions too many years (over the candidate's level) but score >= 7
    Returns dict with findings.
    """
    issues = []
    if not os.path.exists(DB_PATH):
        return {"issues": ["DB file not found"], "total_checked": 0}

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Find the most recent pipeline run's window: max(date_scraped) - 4 hours
    last_scraped_raw = conn.execute("SELECT MAX(date_scraped) FROM jobs").fetchone()[0]
    if not last_scraped_raw:
        conn.close()
        return {"issues": ["No jobs in DB"], "total_checked": 0}
    try:
        last_scraped = datetime.fromisoformat(last_scraped_raw)
        run_start = (last_scraped - timedelta(hours=4)).isoformat()
    except Exception:
        run_start = (datetime.now() - timedelta(hours=6)).isoformat()

    rows = conn.execute("""
        SELECT job_id, title, company, relevance_score, description, experience_required
        FROM jobs
        WHERE relevance_score >= 7
          AND date_scraped >= ?
        ORDER BY date_scraped DESC
        LIMIT 100
    """, (run_start,)).fetchall()
    conn.close()

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
    if not os.path.exists(DB_PATH):
        return {}
    cutoff = (datetime.today() - timedelta(days=3)).strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""
        SELECT relevance_score, COUNT(*) as cnt
        FROM jobs
        WHERE date_scraped >= ?
        GROUP BY relevance_score
        ORDER BY relevance_score DESC
    """, ((datetime.today() - timedelta(days=3)).isoformat(),)).fetchall()
    conn.close()
    dist = {str(r[0]): r[1] for r in rows}
    total = sum(dist.values())
    high  = sum(v for k, v in dist.items() if int(k or 0) >= 7)
    pct   = round(100 * high / total, 1) if total else 0
    return {"distribution": dist, "total": total, "high_score_pct": pct}


# ── 4. Recent DB stats ────────────────────────────────────────────────────────

def _db_stats() -> dict:
    if not os.path.exists(DB_PATH):
        return {}
    conn = sqlite3.connect(DB_PATH)
    total     = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    today_cut = datetime.today().strftime("%Y-%m-%d")
    new_today = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE date_scraped >= ?", (today_cut,)
    ).fetchone()[0]
    with_email = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE hr_email IS NOT NULL AND hr_email != ''"
    ).fetchone()[0]
    no_desc = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE (description IS NULL OR description IN ('nan','None','')) AND relevance_score >= 7"
    ).fetchone()[0]
    conn.close()
    return {"total": total, "new_today": new_today,
            "with_email": with_email, "no_description_matched": no_desc}


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
        _log("  ALERT: Pipeline is DOWN and stale — restarting!")
        _restart_pipeline()
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
