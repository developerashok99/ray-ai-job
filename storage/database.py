"""
MongoDB Atlas-backed storage layer. Replaces the old SQLite jobs.db.

Two collections:
  jobs      — one document per scraped job posting
  companies — aggregated per-company stats, built up as jobs are inserted

Duplicate detection works on a normalized (title, company) key ("title_key" /
"company_key" on every job doc) rather than job_id alone, since the same real
posting can show up with a different job_id/URL from a different source.
"""
import os
from datetime import datetime, timedelta

import yaml
from dotenv import load_dotenv
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError

load_dotenv()

_client = None
_db = None


def _get_config():
    try:
        with open("config.yaml") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def get_db():
    global _client, _db
    if _db is None:
        uri = os.getenv("MONGODB_URI")
        if not uri:
            raise RuntimeError("MONGODB_URI not set — add it to .env")
        db_name = _get_config().get("storage", {}).get("mongo_db_name", "jobpilot")
        _client = MongoClient(uri, serverSelectionTimeoutMS=10000)
        _db = _client[db_name]
    return _db


def _norm(s) -> str:
    """Normalize a string for duplicate matching: lowercase, collapse whitespace."""
    return " ".join(str(s or "").strip().lower().split())


def init_db():
    db = get_db()
    db.jobs.create_index("job_id", unique=True)
    db.jobs.create_index([("title_key", ASCENDING), ("company_key", ASCENDING)])
    db.jobs.create_index("relevance_score")
    db.jobs.create_index("date_scraped")
    db.jobs.create_index("status")
    db.companies.create_index("company_key", unique=True)
    print("MongoDB initialized (indexes ensured).")


def _strip_id(doc):
    if doc is not None:
        doc.pop("_id", None)
    return doc


# ── Jobs ──────────────────────────────────────────────────────────────────────

def job_exists(job_id) -> bool:
    return get_db().jobs.find_one({"job_id": job_id}, {"_id": 1}) is not None


def find_existing_by_title_company(title, company, exclude_job_id=None):
    """Most recent prior job doc with the same normalized title+company, if any."""
    q = {"title_key": _norm(title), "company_key": _norm(company)}
    if exclude_job_id:
        q["job_id"] = {"$ne": exclude_job_id}
    return get_db().jobs.find_one(q, sort=[("date_scraped", DESCENDING)])


def insert_job(job: dict) -> bool:
    """
    Insert a scraped job. Returns False if job_id already exists (duplicate
    insert attempt), True otherwise. Every insert is checked against job
    history by normalized title+company and flagged with is_duplicate /
    duplicate_of — this is the general-purpose "have we seen this posting
    before" signal, independent of whether it's ever been emailed.
    """
    db = get_db()
    title_key   = _norm(job.get("title"))
    company_key = _norm(job.get("company"))

    dup = None
    if title_key and company_key:
        dup = find_existing_by_title_company(job.get("title"), job.get("company"))

    now_iso = datetime.now().isoformat()
    doc = {
        "job_id":              job.get("job_id"),
        "title":               job.get("title"),
        "company":             job.get("company"),
        "title_key":           title_key,
        "company_key":         company_key,
        "location":            job.get("location"),
        "source":              job.get("source"),
        "job_url":             job.get("job_url"),
        "description":         job.get("description"),
        "relevance_score":     job.get("relevance_score", 0),
        "match_reason":        job.get("match_reason"),
        "internship_friendly": bool(job.get("internship_friendly", 0)),
        "experience_required": job.get("experience_required"),
        "days_old":            job.get("days_old"),
        "hr_email":            job.get("hr_email"),
        "hr_email_2":          job.get("hr_email_2"),
        "hr_email_3":          job.get("hr_email_3"),
        "hr_name":             job.get("hr_name"),
        "phone":               job.get("phone"),
        "contact_form_url":    job.get("contact_form_url"),
        "company_website":     job.get("company_website"),
        "draft_email":         job.get("draft_email"),
        "date_posted":         job.get("date_posted"),
        "date_scraped":        now_iso,
        "status":              "new",
        "applied":             0,
        "applicants":          job.get("applicants", ""),
        "is_duplicate":        dup is not None,
        "duplicate_of":        dup["job_id"] if dup else None,
    }
    try:
        db.jobs.insert_one(doc)
    except DuplicateKeyError:
        return False

    _upsert_company(doc)
    return True


def was_recently_notified(title, company, days=14) -> bool:
    """
    True if a job with the same title+company was already emailed in a
    notification recently — catches the same real posting re-appearing
    under a different job_id/URL from another source.
    """
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    return get_db().jobs.find_one({
        "title_key":   _norm(title),
        "company_key": _norm(company),
        "status":      "emailed",
        "date_scraped": {"$gte": cutoff},
    }, {"_id": 1}) is not None


def mark_notified(job_ids: list):
    """Mark jobs as emailed so the same posting isn't sent again in a future run."""
    if not job_ids:
        return
    get_db().jobs.update_many(
        {"job_id": {"$in": job_ids}}, {"$set": {"status": "emailed"}}
    )


def check_score_cache(title, company, days=7):
    """Return a cached scoring result if the same title+company scored recently."""
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    doc = get_db().jobs.find_one({
        "title_key":   _norm(title),
        "company_key": _norm(company),
        "date_scraped": {"$gte": cutoff},
        "relevance_score": {"$gt": 0},
    }, sort=[("date_scraped", DESCENDING)])
    if doc:
        return {
            "score": doc["relevance_score"],
            "reason": f"[cached] {doc.get('match_reason', '')}",
            "internship_friendly": bool(doc.get("internship_friendly")),
            "experience_required": doc.get("experience_required") or "unknown",
        }
    return None


def update_contact(job_id, contacts: dict):
    get_db().jobs.update_one({"job_id": job_id}, {"$set": {
        "hr_email":         contacts.get("email_1"),
        "hr_email_2":       contacts.get("email_2"),
        "hr_email_3":       contacts.get("email_3"),
        "hr_name":          contacts.get("name"),
        "phone":            contacts.get("phone"),
        "contact_form_url": contacts.get("contact_form"),
    }})


def update_draft_email(job_id, draft):
    get_db().jobs.update_one({"job_id": job_id}, {"$set": {"draft_email": draft}})


def update_job_description_and_score(job_id, description, score, reason):
    get_db().jobs.update_one({"job_id": job_id}, {"$set": {
        "description": description,
        "relevance_score": score,
        "match_reason": reason,
    }})


def delete_zero_score_jobs() -> int:
    """Delete all jobs with relevance_score = 0 (failed scoring). Returns count deleted."""
    return get_db().jobs.delete_many({"relevance_score": 0}).deleted_count


def update_status(job_id, status):
    valid = {"new", "emailed", "response", "interview", "offer", "rejected"}
    if status.lower() not in valid:
        raise ValueError(f"Status must be one of: {valid}")
    get_db().jobs.update_one({"job_id": job_id}, {"$set": {"status": status.lower()}})


def get_relevant_jobs(min_score=7) -> list:
    docs = get_db().jobs.find({"relevance_score": {"$gte": min_score}}) \
        .sort([("days_old", ASCENDING), ("relevance_score", DESCENDING)])
    return [_strip_id(d) for d in docs]


def get_jobs_by_ids(job_ids: list, min_score=None) -> list:
    if not job_ids:
        return []
    q = {"job_id": {"$in": job_ids}}
    if min_score is not None:
        q["relevance_score"] = {"$gte": min_score}
    return [_strip_id(d) for d in get_db().jobs.find(q)]


def get_all_jobs() -> list:
    docs = get_db().jobs.find().sort([("days_old", ASCENDING), ("relevance_score", DESCENDING)])
    return [_strip_id(d) for d in docs]


def list_jobs(min_score=7, limit=20) -> list:
    docs = get_db().jobs.find(
        {"relevance_score": {"$gte": min_score}},
        {"job_id": 1, "title": 1, "company": 1, "location": 1, "relevance_score": 1,
         "internship_friendly": 1, "days_old": 1, "status": 1, "hr_email": 1},
    ).sort([("days_old", ASCENDING), ("relevance_score", DESCENDING)]).limit(limit)
    return [_strip_id(d) for d in docs]


def get_jobs_missing_description(min_score=7, limit=50) -> list:
    docs = get_db().jobs.find({
        "relevance_score": {"$gte": min_score},
        "description": {"$in": [None, "", "nan", "None"]},
        "job_url": {"$nin": [None, ""]},
        # Foundit's job pages 403 every fetch attempt (Akamai bot-protection) — confirmed
        # dead end, so don't waste a run's time and rate-limit budget retrying them.
        "source": {"$ne": "foundit"},
    }, {"job_id": 1, "title": 1, "company": 1, "job_url": 1, "source": 1, "relevance_score": 1}) \
        .sort([("relevance_score", DESCENDING), ("date_scraped", DESCENDING)]).limit(limit)
    return [_strip_id(d) for d in docs]


def get_jobs_with_hr_email(min_score=7, days=None) -> list:
    q = {"relevance_score": {"$gte": min_score}, "hr_email": {"$nin": [None, ""]}}
    if days is not None:
        cutoff = (datetime.today() - timedelta(days=days)).strftime("%Y-%m-%d")
        q["$or"] = [{"date_posted": None}, {"date_posted": "nan"}, {"date_posted": {"$gte": cutoff}}]
    docs = get_db().jobs.find(q).sort([("relevance_score", DESCENDING), ("date_posted", DESCENDING)])
    return [_strip_id(d) for d in docs]


def get_relevant_jobs_since(min_score=7, days=7) -> list:
    cutoff = (datetime.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    q = {
        "relevance_score": {"$gte": min_score},
        "$or": [{"date_posted": None}, {"date_posted": "nan"}, {"date_posted": {"$gte": cutoff}}],
    }
    docs = get_db().jobs.find(q).sort([("date_posted", DESCENDING), ("relevance_score", DESCENDING)])
    return [_strip_id(d) for d in docs]


# ── Companies (new) ───────────────────────────────────────────────────────────

def _upsert_company(job_doc: dict):
    """Roll a newly-inserted job into that company's aggregate stats."""
    company = job_doc.get("company")
    if not company or not str(company).strip():
        return

    set_fields = {
        "name": company,
        "last_seen": job_doc["date_scraped"],
    }
    # Only overwrite company_website when this job actually has one — otherwise
    # a later posting with no website field would null out a known-good value.
    if job_doc.get("company_website"):
        set_fields["company_website"] = job_doc["company_website"]

    get_db().companies.update_one(
        {"company_key": job_doc["company_key"]},
        {
            "$set": set_fields,
            "$setOnInsert": {"first_seen": job_doc["date_scraped"]},
            "$max": {"best_score": job_doc.get("relevance_score", 0)},
            "$inc": {"total_jobs_seen": 1},
            "$addToSet": {
                "sources": job_doc.get("source"),
                "locations": job_doc.get("location"),
            },
            "$push": {
                "recent_titles": {"$each": [job_doc.get("title")], "$slice": -10}
            },
        },
        upsert=True,
    )


def list_companies(min_jobs=1, limit=50) -> list:
    """Companies ranked by best score seen, then total postings — a quick read
    on which companies are worth watching closely."""
    docs = get_db().companies.find({"total_jobs_seen": {"$gte": min_jobs}}) \
        .sort([("best_score", DESCENDING), ("total_jobs_seen", DESCENDING)]).limit(limit)
    return [_strip_id(d) for d in docs]


def find_duplicate_groups(min_count=2) -> list:
    """
    Groups of jobs sharing the same normalized (title, company) — i.e. the
    same real posting scraped more than once, usually from different sources.
    """
    pipeline = [
        {"$group": {
            "_id": {"title_key": "$title_key", "company_key": "$company_key"},
            "count":       {"$sum": 1},
            "sources":     {"$addToSet": "$source"},
            "job_ids":     {"$push": "$job_id"},
            "title":       {"$first": "$title"},
            "company":     {"$first": "$company"},
            "best_score":  {"$max": "$relevance_score"},
        }},
        {"$match": {"count": {"$gte": min_count}}},
        {"$sort": {"count": DESCENDING}},
    ]
    return list(get_db().jobs.aggregate(pipeline))


# ── Monitor / stats helpers ───────────────────────────────────────────────────

def get_last_scraped_time():
    doc = get_db().jobs.find_one(sort=[("date_scraped", DESCENDING)], projection={"date_scraped": 1})
    if doc and doc.get("date_scraped"):
        try:
            return datetime.fromisoformat(doc["date_scraped"])
        except Exception:
            return None
    return None


def get_recent_high_score_jobs(min_score=7, since=None, limit=100) -> list:
    q = {"relevance_score": {"$gte": min_score}}
    if since is not None:
        q["date_scraped"] = {"$gte": since}
    docs = get_db().jobs.find(
        q, {"job_id": 1, "title": 1, "company": 1, "relevance_score": 1,
            "description": 1, "experience_required": 1},
    ).sort("date_scraped", DESCENDING).limit(limit)
    return [_strip_id(d) for d in docs]


def get_score_distribution(since_days=3) -> dict:
    cutoff = (datetime.today() - timedelta(days=since_days)).isoformat()
    pipeline = [
        {"$match": {"date_scraped": {"$gte": cutoff}}},
        {"$group": {"_id": "$relevance_score", "cnt": {"$sum": 1}}},
        {"$sort": {"_id": DESCENDING}},
    ]
    rows = list(get_db().jobs.aggregate(pipeline))
    dist  = {str(r["_id"]): r["cnt"] for r in rows}
    total = sum(dist.values())
    high  = sum(v for k, v in dist.items() if int(k or 0) >= 7)
    pct   = round(100 * high / total, 1) if total else 0
    return {"distribution": dist, "total": total, "high_score_pct": pct}


def get_db_stats() -> dict:
    db = get_db()
    today_cut = datetime.today().strftime("%Y-%m-%d")
    total      = db.jobs.count_documents({})
    new_today  = db.jobs.count_documents({"date_scraped": {"$gte": today_cut}})
    with_email = db.jobs.count_documents({"hr_email": {"$nin": [None, ""]}})
    no_desc    = db.jobs.count_documents({
        "description": {"$in": [None, "", "nan", "None"]},
        "relevance_score": {"$gte": 7},
    })
    return {"total": total, "new_today": new_today,
            "with_email": with_email, "no_description_matched": no_desc}
