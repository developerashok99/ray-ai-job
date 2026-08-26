"""
Foundit.in scraper — middleware/jobsearch API (no browser needed).
Previously Monster India, general Indian job board.
API: GET https://www.foundit.in/middleware/jobsearch
"""
import hashlib
import time
import random
import yaml
import requests
from datetime import datetime

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-IN,en;q=0.9",
    "Origin": "https://www.foundit.in",
    "Referer": "https://www.foundit.in/srp/results",
}

LOC_CODES = {
    "Hyderabad":     "hyderabad",
    "Bangalore":     "bengaluru",
    "Visakhapatnam": "visakhapatnam",
    "Remote":        "",
}

# Per-keyword query overrides — usually unnecessary since the fallback
# (keyword.lower()) already produces a valid search query for plain keywords.
KEYWORD_MAP = {}

API_URL = "https://www.foundit.in/middleware/jobsearch"


def load_config():
    with open("config.yaml") as f:
        return yaml.safe_load(f)


def _experience_range_param():
    try:
        exp_range = str(load_config()["search"].get("experience_range", "0-1"))
        lo, hi = exp_range.split("-")
        return f"{int(lo)}~{int(hi)}"
    except Exception:
        return "0~1"


def _search(keyword, location):
    kw  = KEYWORD_MAP.get(keyword, keyword.lower())
    loc = LOC_CODES.get(location, location.lower())
    params = {
        "query":           kw,
        "location":        loc,
        "experienceRanges": _experience_range_param(),
        "sort":            "1",   # most recent
        "limit":           "25",
    }
    try:
        resp = requests.get(API_URL, headers=HEADERS, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return _parse(data, location)
    except Exception as e:
        print(f"    Foundit error [{kw}/{loc}]: {e}")
        return []


def _parse(data, fallback_location):
    jobs = []
    for j in (data.get("jobSearchResponse") or {}).get("data") or []:
        title    = j.get("title", "") or ""
        company  = j.get("companyName", "") or ""
        loc_text = j.get("locations", "") or fallback_location
        seo_url  = j.get("seoJdUrl", "") or j.get("jdUrl", "") or ""
        job_url  = ("https://www.foundit.in" + seo_url) if seo_url and not seo_url.startswith("http") else seo_url
        job_id_raw = str(j.get("jobId", "") or j.get("id", "") or "")
        job_id   = hashlib.md5((job_url or job_id_raw).encode()).hexdigest()

        ts = j.get("lastUpdated") or j.get("freshness") or 0
        try:
            date_posted = datetime.fromtimestamp(int(ts) / 1000).strftime("%Y-%m-%d") if ts else ""
        except Exception:
            date_posted = ""

        if title and job_url:
            jobs.append({
                "job_id": job_id, "title": title, "company": company,
                "location": loc_text, "source": "foundit", "job_url": job_url,
                "description": "", "date_posted": date_posted, "company_website": "",
            })
    return jobs


def scrape_foundit():
    config    = load_config()
    keywords  = config["search"]["keywords"]
    locations = config["search"]["locations"]
    delay     = config["scraping"].get("delay_between_requests", 3)

    results  = []
    seen_ids = set()
    seen_slugs = set()

    for keyword in keywords:
        kw_slug = KEYWORD_MAP.get(keyword, keyword.lower())
        for location in locations:
            loc_slug = LOC_CODES.get(location, location.lower())
            key = (kw_slug, loc_slug)
            if key in seen_slugs:
                continue
            seen_slugs.add(key)

            jobs = _search(keyword, location)
            new  = [j for j in jobs if j["job_id"] not in seen_ids]
            for j in new:
                seen_ids.add(j["job_id"])
                results.append(j)
            if new:
                print(f"  [Foundit] {keyword[:30]} | {location}: {len(new)} jobs")
            time.sleep(delay + random.random())

    print(f"  Foundit total: {len(results)} unique jobs")
    return results
