"""
Description filler — fetches missing job descriptions and gets them a real AI score.

Covers two buckets, both identified by get_jobs_missing_description(min_score=5):
  - score=5 "no description — verify manually" holdouts from Pass 1 (scheduler.py) —
    these never got a real AI opinion at all, because there was no description to
    score against at scrape time
  - score>=7 matches whose description happened to be missing when first scored
    (rarer edge case) — re-checked in case a full description reveals a hidden
    experience requirement that should downgrade them

This script:
  1. Finds jobs in either bucket with no description
  2. Fetches the description from the job_url using requests + BeautifulSoup
  3. Re-scores each job with Groq using the full description
  4. Updates the DB — if new score < threshold, marks it appropriately
  5. Rebuilds the matched-jobs export at the end

Foundit is a lost cause for this: its job pages sit behind Akamai bot-protection that
returns a hard 403 to any headless-browser request, so `_fetch_description_playwright`
below never actually succeeds against it in practice (confirmed against live Foundit
URLs). It's left in place since it's harmless (fails closed, no crash) and costs nothing
extra to keep, but don't expect it to work, and don't try to make it work by evading the
block — that's Foundit's explicit anti-automation control, not a bug to route around.

Safe to run concurrently with the scheduler (reads/writes different columns).
"""
import os
import re
import sys
import time
import random

import requests
import yaml
from bs4 import BeautifulSoup
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

from agent.exp_filter import has_experience_requirement, _EXP_FLOOR
from storage.database import get_jobs_missing_description, update_job_description_and_score

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _load_config():
    with open("config.yaml") as f:
        return yaml.safe_load(f)


def _fetch_description(job_url: str, source: str) -> str:
    """Fetch job description from the job listing page."""
    if not job_url or not job_url.startswith("http"):
        return ""

    # Foundit job detail pages are JS-rendered — use Playwright directly
    if "foundit.in" in job_url:
        return _fetch_description_playwright(job_url)

    try:
        resp = requests.get(job_url, headers=HEADERS, timeout=15, allow_redirects=True)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Remove script/style/nav noise
        for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
            tag.decompose()

        # Source-specific extraction
        if "linkedin.com" in job_url:
            el = (soup.select_one(".description__text")
                  or soup.select_one(".jobs-description__content")
                  or soup.select_one("[class*='description']"))
        elif "indeed.com" in job_url:
            el = soup.select_one("#jobDescriptionText") or soup.select_one(".jobsearch-jobDescriptionText")
        elif "naukri.com" in job_url:
            el = soup.select_one(".job-desc") or soup.select_one("[class*='jd-desc']")
        elif "internshala.com" in job_url:
            el = soup.select_one(".about_the_job") or soup.select_one("#about_company")
        elif "efinancialcareers.com" in job_url:
            el = soup.select_one(".job-description") or soup.select_one("[class*='job-description']")
        else:
            # Generic: find the largest text block
            el = None
            best_len = 0
            for tag in soup.find_all(["div", "section", "article"]):
                txt = tag.get_text(" ", strip=True)
                if len(txt) > best_len and len(txt) < 8000:
                    best_len = len(txt)
                    el = tag

        if el:
            text = el.get_text(" ", strip=True)
            text = re.sub(r"\s{3,}", "\n", text)
            return text[:3000]

    except Exception:
        pass
    return ""


def _fetch_description_playwright(job_url: str) -> str:
    """Playwright-based fetch for JS-rendered pages (Foundit)."""
    try:
        import asyncio
        from playwright.async_api import async_playwright

        async def _run():
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page(user_agent=HEADERS["User-Agent"])
                try:
                    await page.goto(job_url, wait_until="domcontentloaded", timeout=20000)
                    await page.wait_for_timeout(3000)
                    # Try Foundit-specific selectors first
                    for sel in [".job-desc-container", ".jd-container", "[class*='jobDescription']",
                                 "[class*='job-description']", "[class*='desc-container']",
                                 ".description", "section.details"]:
                        el = await page.query_selector(sel)
                        if el:
                            text = await el.inner_text()
                            if len(text) > 100:
                                return re.sub(r"\s{3,}", "\n", text.strip())[:3000]
                    # Fallback: largest text block on page
                    blocks = await page.query_selector_all("div, section, article")
                    best_text = ""
                    for block in blocks:
                        try:
                            txt = await block.inner_text()
                            if 200 < len(txt) < 6000 and len(txt) > len(best_text):
                                best_text = txt
                        except Exception:
                            continue
                    return re.sub(r"\s{3,}", "\n", best_text.strip())[:3000] if best_text else ""
                finally:
                    await browser.close()

        return asyncio.run(_run())
    except Exception:
        return ""


def run_filler(limit: int = 50, rescore: bool = True, dry_run: bool = False) -> list:
    """
    Fetch descriptions and optionally re-score matched jobs with no description.

    Args:
        limit:    Max jobs to process per run (keep low to avoid rate limits)
        rescore:  If True, batch re-score with AI after fetching descriptions
        dry_run:  If True, print results but don't update DB

    Returns the job_ids actually updated — the caller (scheduler.py) needs this to catch
    postings that started as a score=5 "no description" placeholder and got newly upgraded
    to a real match here. Without it, a job like this would sit in the DB with a good score
    but never appear in any notification, since it's not "new" to future runs and wasn't in
    the current run's original relevant[] list either (confirmed missing from a real run).
    """
    config    = _load_config()
    threshold = config["matching"]["relevance_threshold"]

    # min_score=5 catches both the "no description, never AI-scored" holdouts
    # (Pass 1 parks those at exactly 5) and genuine 7+ matches missing a description.
    # Score=1 pre-filter rejects (senior/internship/irrelevant/5+yrs) are correctly
    # excluded since they never reach 5.
    rows = get_jobs_missing_description(min_score=5, limit=limit)

    if not rows:
        print("[DescFiller] No no-description matched jobs found.")
        return []

    print(f"[DescFiller] Processing {len(rows)} no-description matched jobs...")

    # Phase 1: fetch all descriptions (with delays to avoid rate limits)
    # fetched = list of (row, desc, pre_score_or_None, pre_reason_or_None)
    fetched = []
    for i, row in enumerate(rows, 1):
        title     = row["title"]
        company   = row["company"]
        old_score = row["relevance_score"]

        print(f"  [{i:>3}/{len(rows)}] Fetching: {title[:40]} @ {company[:25]}")
        desc = _fetch_description(row["job_url"], row["source"])

        if not desc:
            print(f"    -> Could not fetch description, skipping")
            time.sleep(1)
            continue

        if has_experience_requirement(desc):
            pre_score  = 2
            pre_reason = f"Description requires {_EXP_FLOOR}+ years experience (fetched post-insert)"
            print(f"    -> EXP REQUIRED — downgrading {old_score} -> {pre_score}")
            fetched.append((row, desc, pre_score, pre_reason))
        else:
            # Mark for AI rescoring
            fetched.append((row, desc, None, None))

        time.sleep(2 + random.random() * 2)

    if not fetched:
        print("[DescFiller] Nothing fetched successfully.")
        return

    # Phase 2: batch AI rescore for jobs without pre-determined score
    if rescore:
        from agent.resume_matcher import score_jobs_batch, load_resume
        load_resume(config["resume"]["path"])

        needs_ai = [(row, desc) for row, desc, ps, _ in fetched if ps is None]
        if needs_ai:
            print(f"\n[DescFiller] AI-rescoring {len(needs_ai)} jobs in batches of 5...")
            ai_jobs = []
            for row, desc in needs_ai:
                jd = dict(row)
                jd["description"] = desc
                ai_jobs.append(jd)
            results = score_jobs_batch(ai_jobs, batch_size=5)

            ai_idx   = 0
            resolved = []
            for row, desc, pre_score, pre_reason in fetched:
                if pre_score is not None:
                    resolved.append((row, desc, pre_score, pre_reason))
                else:
                    result    = results[ai_idx]
                    ai_idx   += 1
                    new_score = int(result.get("score", row["relevance_score"]))
                    reason    = result.get("reason", "")
                    print(f"    Re-scored {row['title'][:40]}: {row['relevance_score']} -> {new_score}  {reason[:60]}")
                    resolved.append((row, desc, new_score, reason))
            fetched = resolved
    else:
        # Keep old scores when rescore=False
        fetched = [(row, desc, row["relevance_score"], "") for row, desc, ps, _ in fetched]

    # Phase 3: apply DB updates
    downgraded = 0
    enriched   = 0
    upgraded   = 0
    touched_ids = []
    for row, desc, new_score, reason in fetched:
        old_score = row["relevance_score"]
        job_id    = row["job_id"]
        touched_ids.append(job_id)

        if not dry_run:
            update_job_description_and_score(job_id, desc, new_score, reason)

        if new_score < threshold and old_score >= threshold:
            downgraded += 1
        elif new_score >= threshold and old_score < threshold:
            upgraded += 1
        enriched += 1

    print(f"\n[DescFiller] Done — enriched: {enriched}, downgraded: {downgraded}, newly upgraded: {upgraded}")
    if downgraded > 0 and not dry_run:
        print("[DescFiller] Rebuilding matched-jobs export...")
        from export_fresher_jobs import export
        export()

    return touched_ids


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)) + "/..")
    run_filler(limit=100, rescore=True)
