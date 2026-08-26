"""
Delete all existing Gmail drafts, then generate + save 5 fresh drafts
using the new human-style email prompt with resume attached.
"""
import os
import sys
import sqlite3

os.chdir(os.path.dirname(os.path.abspath(__file__)))

from agent.gmail_drafts import delete_all_drafts, save_all_drafts, _is_valid_email
from agent.email_drafter import draft_email
from agent.resume_matcher import load_resume
import yaml


def get_5_good_jobs():
    """Pull jobs from DB that have a valid hr_email (no image filenames, no placeholders)."""
    conn = sqlite3.connect("jobs.db")
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT * FROM jobs
           WHERE relevance_score >= 7
             AND hr_email IS NOT NULL
             AND hr_email != ''
           ORDER BY relevance_score DESC, days_old ASC"""
    ).fetchall()
    conn.close()

    good = []
    for row in [dict(r) for r in rows]:
        if _is_valid_email(row["hr_email"]):
            good.append(row)
        if len(good) >= 5:
            break
    return good


def main():
    # ── Step 1: Delete all existing drafts ──────────────────────────────────
    print("\n" + "=" * 55)
    print("  Step 1: Delete all existing Gmail drafts")
    print("=" * 55)
    deleted = delete_all_drafts()
    print(f"  Done — deleted {deleted} drafts\n")

    # ── Step 2: Pick 5 jobs with valid HR emails ─────────────────────────────
    print("=" * 55)
    print("  Step 2: Find 5 jobs with valid HR emails")
    print("=" * 55)
    jobs = get_5_good_jobs()
    if not jobs:
        print("  No jobs with valid HR emails found in DB!")
        sys.exit(1)

    print(f"  Found {len(jobs)} jobs:")
    for j in jobs:
        print(f"    [{j['relevance_score']}/10] {j['title'][:40]} @ {j['company'][:25]} >> {j['hr_email']}")

    # ── Step 3: Re-draft emails with new prompt ──────────────────────────────
    print("\n" + "=" * 55)
    print("  Step 3: Generate fresh emails (new human-style prompt)")
    print("=" * 55)
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)
    resume_text = load_resume(cfg["resume"]["path"])

    for job in jobs:
        try:
            draft = draft_email(job, resume_text, hr_name=job.get("hr_name"))
            job["draft_email"] = draft
            print(f"\n  ── {job['company']} ({job['title']}) ──")
            # Print the draft so user can review
            lines = draft.splitlines()
            for line in lines:
                print(f"    {line}")
        except Exception as e:
            print(f"  Draft error [{job['company']}]: {e}")
            job["draft_email"] = ""

    # ── Step 4: Push to Gmail Drafts with resume attached ────────────────────
    print("\n" + "=" * 55)
    print("  Step 4: Save to Gmail Drafts (with resume attached)")
    print("=" * 55)
    saved = save_all_drafts(jobs)

    print("\n" + "=" * 55)
    print(f"  Done — {saved}/5 drafts saved to Gmail with resume attached")
    print("=" * 55 + "\n")


if __name__ == "__main__":
    main()
