Show live statistics from the JobPilot AI database.

Run this Python snippet and display the results clearly:

```python
import sqlite3, os
os.chdir(r"c:\Users\ashok\Desktop\JobPilot_AI")
conn = sqlite3.connect("jobs.db")

total      = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
today      = conn.execute("SELECT COUNT(*) FROM jobs WHERE date_scraped >= date('now')").fetchone()[0]
matched    = conn.execute("SELECT COUNT(*) FROM jobs WHERE relevance_score >= 7").fetchone()[0]
top        = conn.execute("SELECT COUNT(*) FROM jobs WHERE relevance_score >= 9").fetchone()[0]
zero       = conn.execute("SELECT COUNT(*) FROM jobs WHERE relevance_score = 0").fetchone()[0]
by_source  = conn.execute("SELECT source, COUNT(*) FROM jobs GROUP BY source ORDER BY COUNT(*) DESC").fetchall()
score_dist = conn.execute("SELECT relevance_score, COUNT(*) FROM jobs WHERE relevance_score > 0 GROUP BY relevance_score ORDER BY relevance_score DESC").fetchall()
recent10   = conn.execute("SELECT title, company, relevance_score, source FROM jobs WHERE relevance_score >= 7 ORDER BY date_scraped DESC LIMIT 10").fetchall()
conn.close()

print(f"\n=== JobPilot DB Stats ===")
print(f"Total jobs      : {total}")
print(f"Scraped today   : {today}")
print(f"Matched (>=7)   : {matched}")
print(f"Top matches (9+): {top}")
print(f"Score=0 (bad)   : {zero}")
print(f"\n--- By Source ---")
for src, cnt in by_source:
    print(f"  {src:<20} {cnt}")
print(f"\n--- Score Distribution ---")
for score, cnt in score_dist:
    print(f"  Score {score}: {cnt} jobs")
print(f"\n--- 10 Most Recent Matches ---")
for title, company, score, src in recent10:
    print(f"  [{score}/10] {title[:40]} @ {company[:25]} ({src})")
```

Execute the snippet using the Bash tool, then present the output in a clean readable format with sections for Overview, Sources, and Recent Matches.
