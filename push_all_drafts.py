"""
Save Gmail Drafts for all good jobs using the lite personalized template.
No LLM calls — fast, no token limits. Injects up to 2 finance keywords from job description.
"""
import os
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))

from agent.email_drafter import draft_email_lite
from agent.gmail_drafts  import delete_all_drafts, save_all_drafts, _is_valid_email
from storage.database    import get_jobs_with_hr_email


def get_good_jobs(days=7):
    rows = get_jobs_with_hr_email(min_score=7, days=days)
    return [r for r in rows if _is_valid_email(r["hr_email"])]


def main():
    print("\n" + "=" * 60)
    print("  JobPilot AI — Push All Drafts (Lite Personalized Template)")
    print("=" * 60)

    # Step 1: Delete all existing drafts
    print("\n  Step 1: Clearing existing Gmail drafts...")
    delete_all_drafts()

    # Step 2: Get all good jobs with valid emails
    jobs = get_good_jobs()
    print(f"\n  Step 2: Found {len(jobs)} jobs with valid HR emails (score >= 7)")

    if not jobs:
        print("  Nothing to do.")
        sys.exit(0)

    # Step 3: Build personalized draft for each job (no LLM)
    print(f"\n  Step 3: Building lite-personalized emails for all {len(jobs)} jobs...")
    for job in jobs:
        job["draft_email"] = draft_email_lite(job)

    # Step 4: Save all to Gmail Drafts
    print()
    saved = save_all_drafts(jobs)

    print("\n" + "=" * 60)
    print(f"  Done — {saved}/{len(jobs)} drafts saved to Gmail with resume attached")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
