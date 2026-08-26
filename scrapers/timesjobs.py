"""
TimesJobs scraper — POST API (no browser needed).
API: POST https://tjapi.timesjobs.com/search/api/v1/search/jobs/list
Discovered by intercepting XHR from the TimesJobs search page.
"""
import hashlib
import time
import random
import yaml
import requests

API_URL = "https://tjapi.timesjobs.com/search/api/v1/search/jobs/list"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Content-Type": "application/json",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.timesjobs.com/",
    "Origin": "https://www.timesjobs.com",
}

# Per-keyword query overrides — usually unnecessary since the fallback
# (keyword.lower()) already produces a valid search query for plain keywords.
KEYWORD_MAP = {}

INDIA_TERMS = {
    "india", "hyderabad", "bangalore", "bengaluru", "mumbai", "pune",
    "chennai", "kolkata", "delhi", "noida", "gurugram", "gurgaon",
    "vizag", "visakhapatnam", "remote", "work from home", "wfh",
    "pan india", "anywhere", "ahmedabad", "kochi", "coimbatore",
}


def _is_india_or_remote(location: str) -> bool:
    loc = (location or "").lower()
    return any(term in loc for term in INDIA_TERMS)


def load_config():
    with open("config.yaml") as f:
        return yaml.safe_load(f)


def _min_experience_years():
    try:
        exp_range = str(load_config()["search"].get("experience_range", "0-1"))
        return str(int(exp_range.split("-")[0]))
    except Exception:
        return "0"


def _search(keyword: str, page: int = 1, size: int = 50) -> list:
    body = {
        "keyword":        keyword,
        "location":       "",
        "experience":     _min_experience_years(),
        "page":           str(page),
        "size":           str(size),
        "jobFunctions":   ["IT Software : Software Products & Services"],
        "company":        "",
        "industry":       "",
        "functionAreaId": "",
        "jobFunction":    "IT Software : Software Products & Services",
    }
    try:
        resp = requests.post(API_URL, json=body, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        return resp.json().get("jobs", [])
    except Exception as e:
        print(f"    TimesJobs error [{keyword}]: {e}")
        return []


def _parse(raw_jobs: list) -> list:
    jobs = []
    for j in raw_jobs:
        loc = j.get("location", "") or ""
        if not _is_india_or_remote(loc):
            continue
        exp_to = j.get("experienceTo")
        if exp_to is not None and int(exp_to) > 3:
            continue

        title   = (j.get("title") or "").strip()
        company = (j.get("company") or "").strip()
        job_url = (j.get("jobDetailUrl") or "").strip()
        if not title or not job_url:
            continue

        job_id    = hashlib.md5(job_url.encode()).hexdigest()
        desc      = (j.get("description") or "").strip()
        date_post = (j.get("postDate") or "")[:10]

        jobs.append({
            "job_id": job_id, "title": title, "company": company,
            "location": loc, "source": "timesjobs", "job_url": job_url,
            "description": desc, "date_posted": date_post, "company_website": "",
        })
    return jobs


def scrape_timesjobs():
    config = load_config()
    delay  = config["scraping"].get("delay_between_requests", 3)

    results  = []
    seen_ids = set()
    seen_kws = set()

    for keyword in config["search"]["keywords"]:
        kw = KEYWORD_MAP.get(keyword, keyword.lower())
        if kw in seen_kws:
            continue
        seen_kws.add(kw)

        raw  = _search(kw, size=50)
        jobs = _parse(raw)
        new  = [j for j in jobs if j["job_id"] not in seen_ids]
        for j in new:
            seen_ids.add(j["job_id"])
            results.append(j)
        if new:
            print(f"  [TimesJobs] {keyword[:35]}: {len(new)} jobs")
        time.sleep(delay + random.random())

    print(f"  TimesJobs total: {len(results)} unique jobs")
    return results
