Clean up the JobPilot AI database — remove junk entries and optionally purge old jobs.

Operations available (ask the user which they want if not specified in $ARGUMENTS):

1. **Delete score=0 jobs** — AI failed to score these, they're useless clutter
2. **Delete score=1 jobs older than 7 days** — pre-filtered rejects, no longer needed
3. **Delete all jobs older than 30 days** — stale postings, already closed

Run the appropriate cleanup:

```python
import sqlite3, os
os.chdir(r"c:\Users\ashok\Desktop\JobPilot_AI")
conn = sqlite3.connect("jobs.db")

# Always safe: remove score=0
cur = conn.execute("DELETE FROM jobs WHERE relevance_score = 0")
zero_deleted = cur.rowcount

# Remove score=1 rejects older than 7 days
cur = conn.execute("DELETE FROM jobs WHERE relevance_score = 1 AND date_scraped < date('now', '-7 days')")
old_rejects = cur.rowcount

conn.commit()

# Show what's left
total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
matched = conn.execute("SELECT COUNT(*) FROM jobs WHERE relevance_score >= 7").fetchone()[0]
conn.close()

print(f"Deleted {zero_deleted} score=0 jobs")
print(f"Deleted {old_rejects} old score=1 rejects (>7 days)")
print(f"DB now has {total} total jobs, {matched} matches (score>=7)")
```

Execute with the Bash tool and report the results. If the user also wants to delete all jobs older than 30 days, confirm first since that's more destructive, then run:
`DELETE FROM jobs WHERE date_scraped < date('now', '-30 days')`
