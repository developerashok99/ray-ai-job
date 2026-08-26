Show the top matched jobs from the JobPilot AI database — best scores first, most recent first.

If the user passes an argument (e.g. `/top-jobs 20`), use that as the limit. Default limit is 15.

Run this Python snippet:

```python
import sqlite3, os
os.chdir(r"c:\Users\ashok\Desktop\JobPilot_AI")
LIMIT = $ARGUMENTS if "$ARGUMENTS".strip().isdigit() else 15
conn = sqlite3.connect("jobs.db")
conn.row_factory = sqlite3.Row
rows = conn.execute("""
    SELECT title, company, location, source, relevance_score, job_url, date_scraped, date_posted
    FROM jobs
    WHERE relevance_score >= 7
    ORDER BY relevance_score DESC, date_scraped DESC
    LIMIT ?
""", (int(LIMIT),)).fetchall()
conn.close()

print(f"\nTop {LIMIT} matched jobs (score >= 7):\n")
for i, r in enumerate(rows, 1):
    posted = r['date_posted'][:10] if r['date_posted'] else "unknown"
    print(f"{i:>2}. [{r['relevance_score']}/10] {r['title'][:45]}")
    print(f"      {r['company'][:35]} | {r['location'][:25]} | {r['source']}")
    print(f"      Posted: {posted} | {r['job_url'][:70]}")
    print()
```

Execute the snippet with the Bash tool and display the results. Make the job URLs clickable using markdown link format where possible.
