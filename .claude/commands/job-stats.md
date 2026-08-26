Show live statistics from the JobPilot AI MongoDB database.

Run this Python snippet and display the results clearly:

```python
import os
os.chdir(r"c:\Users\ashok\Desktop\JobPilot_AI")
from storage.database import get_db
from datetime import datetime

db = get_db()
today_cut = datetime.today().strftime("%Y-%m-%d")

total      = db.jobs.count_documents({})
today      = db.jobs.count_documents({"date_scraped": {"$gte": today_cut}})
matched    = db.jobs.count_documents({"relevance_score": {"$gte": 7}})
top        = db.jobs.count_documents({"relevance_score": {"$gte": 9}})
zero       = db.jobs.count_documents({"relevance_score": 0})
duplicates = db.jobs.count_documents({"is_duplicate": True})
by_source  = list(db.jobs.aggregate([{"$group": {"_id": "$source", "cnt": {"$sum": 1}}}, {"$sort": {"cnt": -1}}]))
score_dist = list(db.jobs.aggregate([{"$match": {"relevance_score": {"$gt": 0}}}, {"$group": {"_id": "$relevance_score", "cnt": {"$sum": 1}}}, {"$sort": {"_id": -1}}]))
recent10   = list(db.jobs.find({"relevance_score": {"$gte": 7}}, {"title": 1, "company": 1, "relevance_score": 1, "source": 1}).sort("date_scraped", -1).limit(10))
top_companies = list(db.companies.find().sort([("best_score", -1), ("total_jobs_seen", -1)]).limit(5))

print(f"\n=== JobPilot DB Stats (MongoDB) ===")
print(f"Total jobs      : {total}")
print(f"Scraped today   : {today}")
print(f"Matched (>=7)   : {matched}")
print(f"Top matches (9+): {top}")
print(f"Score=0 (bad)   : {zero}")
print(f"Duplicates      : {duplicates}")
print(f"\n--- By Source ---")
for row in by_source:
    print(f"  {row['_id']:<20} {row['cnt']}")
print(f"\n--- Score Distribution ---")
for row in score_dist:
    print(f"  Score {row['_id']}: {row['cnt']} jobs")
print(f"\n--- 10 Most Recent Matches ---")
for j in recent10:
    print(f"  [{j['relevance_score']}/10] {j['title'][:40]} @ {j['company'][:25]} ({j['source']})")
print(f"\n--- Top Companies ---")
for c in top_companies:
    print(f"  {c['name'][:30]:<32} best={c.get('best_score')} seen={c.get('total_jobs_seen')}")
```

Execute the snippet using the Bash tool, then present the output in a clean readable format with sections for Overview, Sources, Recent Matches, and Top Companies.
