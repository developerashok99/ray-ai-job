Clean up the JobPilot AI database — remove junk entries and optionally purge old jobs.

Operations available (ask the user which they want if not specified in $ARGUMENTS):

1. **Delete score=0 jobs** — AI failed to score these, they're useless clutter
2. **Delete score=1 jobs older than 7 days** — pre-filtered rejects, no longer needed
3. **Delete all jobs older than 30 days** — stale postings, already closed

Run the appropriate cleanup:

```python
import os
os.chdir(r"c:\Users\ashok\Desktop\JobPilot_AI")
from storage.database import get_db
from datetime import datetime, timedelta

db = get_db()
cutoff_7d = (datetime.now() - timedelta(days=7)).isoformat()

# Always safe: remove score=0
zero_deleted = db.jobs.delete_many({"relevance_score": 0}).deleted_count

# Remove score=1 rejects older than 7 days
old_rejects = db.jobs.delete_many({"relevance_score": 1, "date_scraped": {"$lt": cutoff_7d}}).deleted_count

# Show what's left
total   = db.jobs.count_documents({})
matched = db.jobs.count_documents({"relevance_score": {"$gte": 7}})

print(f"Deleted {zero_deleted} score=0 jobs")
print(f"Deleted {old_rejects} old score=1 rejects (>7 days)")
print(f"DB now has {total} total jobs, {matched} matches (score>=7)")
```

Execute with the Bash tool and report the results. If the user also wants to delete all jobs older than 30 days, confirm first since that's more destructive, then run:
```python
cutoff_30d = (datetime.now() - timedelta(days=30)).isoformat()
db.jobs.delete_many({"date_scraped": {"$lt": cutoff_30d}})
```
