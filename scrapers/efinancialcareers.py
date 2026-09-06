"""
efinancialcareers.com scraper — server-rendered HTML (Angular Universal SSR), no
browser needed. Finance-industry-specific board; confirmed no login wall on listings
and no Cloudflare/Akamai signals (unlike Foundit).

URL pattern: /jobs/{keyword-slug}/{location-slug}?page={n}
  - keyword slug: lowercased, non-alnum -> hyphens (e.g. "Equity Research Analyst" ->
    "equity-research-analyst"). Confirmed live that made-up slugs like this return
    genuinely filtered, topically relevant results — not just a generic fallback page.
  - location slug: "in-<city>" (e.g. "in-bangalore", "in-delhi"). Visakhapatnam has very
    low volume on this site (it's a global/enterprise-finance board) — expected, harmless.
"""
import hashlib
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import requests
import yaml
from bs4 import BeautifulSoup

BASE_URL = "https://www.efinancialcareers.com/jobs"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}
MAX_WORKERS = 6  # matches scrapers/linkedin_indeed.py's concurrency level

LOCATION_SLUGS = {
    "bangalore":      "in-bangalore",
    "hyderabad":      "in-hyderabad",
    "visakhapatnam":  "in-visakhapatnam",
    "delhi":          "in-delhi",
    "remote":         "in-india",
}


def load_config():
    with open("config.yaml") as f:
        return yaml.safe_load(f)


def _slugify(keyword: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", keyword.lower()).strip("-")


def _location_slug(location: str) -> str:
    return LOCATION_SLUGS.get(location.lower(), f"in-{location.lower().replace(' ', '-')}")


def _parse_relative_date(text: str) -> str:
    """
    "2 days ago" / "1 day ago" / "today" / "yesterday" -> "YYYY-MM-DD", matching
    the format TimesJobs/Foundit already store. Unparseable text -> "" (treated as
    unknown downstream, same as the other sources' nan/None cases — never crashes).
    """
    text = (text or "").strip().lower()
    now = datetime.now()
    if not text:
        return ""
    if "today" in text or "just posted" in text or "hour" in text or "minute" in text:
        return now.strftime("%Y-%m-%d")
    if "yesterday" in text:
        return (now - timedelta(days=1)).strftime("%Y-%m-%d")
    m = re.search(r"(\d+)\s*day", text)
    if m:
        return (now - timedelta(days=int(m.group(1)))).strftime("%Y-%m-%d")
    m = re.search(r"(\d+)\s*(week|month)", text)
    if m:
        days = int(m.group(1)) * (7 if m.group(2) == "week" else 30)
        return (now - timedelta(days=days)).strftime("%Y-%m-%d")
    return ""


def _parse_cards(html: str) -> list:
    soup = BeautifulSoup(html, "html.parser")
    jobs = []
    for card in soup.select("[class*='job-card']"):
        title_el = card.select_one("a.job-title")
        if not title_el:
            continue
        title   = title_el.get_text(strip=True)
        job_url = title_el.get("href", "") or ""
        if job_url and not job_url.startswith("http"):
            job_url = "https://www.efinancialcareers.com" + job_url
        if not title or not job_url:
            continue

        company_el  = card.select_one(".company")
        location_el = card.select_one(".location .dot-divider")
        date_el     = card.select_one(".search-result-meta-component")
        date_text   = date_el.get_text(strip=True) if date_el else ""

        jobs.append({
            "job_id":      hashlib.md5(job_url.encode()).hexdigest(),
            "title":       title,
            "company":     company_el.get_text(strip=True) if company_el else "",
            "location":    location_el.get_text(strip=True) if location_el else "",
            "source":      "efinancialcareers",
            "job_url":     job_url,
            "description": "",  # not on the listing card — description_filler.py backfills it
            "date_posted": _parse_relative_date(date_text),
        })
    return jobs


def scrape_efinancialcareers() -> list:
    config    = load_config()
    keywords  = config["search"]["keywords"]
    locations = config["search"]["locations"]
    delay     = config["scraping"].get("delay_between_requests", 3)

    all_jobs = []
    seen_ids = set()
    lock     = threading.Lock()
    combos   = [(kw, loc) for kw in keywords for loc in locations]

    def _scrape_one(combo):
        keyword, location = combo
        url = f"{BASE_URL}/{_slugify(keyword)}/{_location_slug(location)}"
        found = []
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            jobs = _parse_cards(resp.text)
            with lock:
                new = [j for j in jobs if j["job_id"] not in seen_ids]
                for j in new:
                    seen_ids.add(j["job_id"])
            found = new
            print(f"  [efinancialcareers] {keyword} | {location}: {len(new)} jobs")
        except Exception as e:
            print(f"  [efinancialcareers] {keyword} | {location}: ERROR {e}")
        time.sleep(delay)
        return found

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        for jobs in executor.map(_scrape_one, combos):
            all_jobs.extend(jobs)

    print(f"  efinancialcareers total: {len(all_jobs)} unique jobs")
    return all_jobs
