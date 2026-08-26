Show the top matched jobs from the JobPilot AI database — best scores first, most recent first.

If the user passes an argument (e.g. `/top-jobs 20`), use that as the limit. Default limit is 15.

Run this Python snippet:

```python
import os
os.chdir(r"c:\Users\ashok\Desktop\JobPilot_AI")
from storage.database import get_db
LIMIT = $ARGUMENTS if "$ARGUMENTS".strip().isdigit() else 15
db = get_db()
rows = list(db.jobs.find(
    {"relevance_score": {"$gte": 7}},
    {"title": 1, "company": 1, "location": 1, "source": 1, "relevance_score": 1,
     "job_url": 1, "date_scraped": 1, "date_posted": 1},
).sort([("relevance_score", -1), ("date_scraped", -1)]).limit(int(LIMIT)))

print(f"\nTop {LIMIT} matched jobs (score >= 7):\n")
for i, r in enumerate(rows, 1):
    posted = r['date_posted'][:10] if r.get('date_posted') else "unknown"
    print(f"{i:>2}. [{r['relevance_score']}/10] {r['title'][:45]}")
    print(f"      {r['company'][:35]} | {r['location'][:25]} | {r['source']}")
    print(f"      Posted: {posted} | {r['job_url'][:70]}")
    print()
```

Execute the snippet with the Bash tool and display the results. Make the job URLs clickable using markdown link format where possible.
