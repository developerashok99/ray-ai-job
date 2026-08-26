import argparse
import sys

# Force UTF-8 output on Windows to handle Unicode job titles
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

from storage.database import init_db


def main():
    parser = argparse.ArgumentParser(
        description="JobPilot AI — Automated Job Search Agent",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")

    # --run
    sub.add_parser("run",      help="Run full pipeline once")
    sub.add_parser("schedule", help="Run pipeline on interval (config.yaml)")

    # export
    exp = sub.add_parser("export", help="Export DB to Excel")
    exp.add_argument("--all",       action="store_true", help="Include all jobs (not just matched)")
    exp.add_argument("--min-score", type=int, default=7, help="Minimum score (default 7)")

    # status update
    upd = sub.add_parser("status", help="Update application status for a job")
    upd.add_argument("job_id", help="Job ID from Excel")
    upd.add_argument("status", choices=["new","emailed","response","interview","offer","rejected"])

    # list jobs
    lst = sub.add_parser("list", help="Print top matched jobs in terminal")
    lst.add_argument("--min-score", type=int, default=7)
    lst.add_argument("--limit",     type=int, default=20)

    # draft emails for existing matched jobs
    sub.add_parser("draft", help="Generate email drafts for all matched jobs missing a draft")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    init_db()

    if args.command == "run":
        from scheduler import run_pipeline
        run_pipeline()

    elif args.command == "schedule":
        from scheduler import start_scheduler
        start_scheduler()

    elif args.command == "export":
        from storage.excel_export import export_to_excel
        export_to_excel(min_score=args.min_score, include_all=args.all)

    elif args.command == "status":
        from storage.database import update_status
        update_status(args.job_id, args.status)
        print(f"Updated job {args.job_id} → {args.status}")

    elif args.command == "list":
        from storage.database import list_jobs
        jobs = list_jobs(min_score=args.min_score, limit=args.limit)
        if not jobs:
            print("No matching jobs found.")
            return
        print(f"\n{'#':<4} {'Score':<7} {'IFriendly':<11} {'Days Old':<10} {'Status':<12} {'Title':<40} Company")
        print("-" * 110)
        for i, j in enumerate(jobs, 1):
            friendly = "YES" if j.get("internship_friendly") else "-"
            days = j.get("days_old") or "?"
            print(
                f"{i:<4} {j['relevance_score']:<7} {friendly:<11} {str(days):<10} "
                f"{j.get('status','new'):<12} {j['title'][:38]:<40} {j['company']}"
            )
        print()

    elif args.command == "draft":
        _run_draft_only()


def _run_draft_only():
    from storage.database import get_relevant_jobs, update_draft_email
    from agent.email_drafter import draft_email
    from agent.resume_matcher import load_resume
    import yaml

    with open("config.yaml") as f:
        config = yaml.safe_load(f)

    resume_text = load_resume(config["resume"]["path"])
    jobs = get_relevant_jobs(config["matching"]["relevance_threshold"])
    pending = [j for j in jobs if not j.get("draft_email")]

    print(f"Drafting emails for {len(pending)} jobs...")
    for i, job in enumerate(pending, 1):
        try:
            draft = draft_email(job, resume_text, job.get("hr_name"))
            update_draft_email(job["job_id"], draft)
            print(f"  [{i}/{len(pending)}] Done: {job['title']} @ {job['company']}")
        except Exception as e:
            print(f"  [{i}/{len(pending)}] Error: {e}")

    print("Email drafting complete. Re-export Excel to see drafts.")


if __name__ == "__main__":
    main()
