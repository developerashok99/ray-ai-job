"""
Naukri scraper — HTML scraping with BeautifulSoup (no browser needed).
Naukri's API requires CAPTCHA; we use their server-rendered search pages instead.
"""
import hashlib
import json
import re
import time
import random
import yaml
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-IN,en-US;q=0.7,en;q=0.3",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.naukri.com/",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# Per-keyword slug overrides — usually unnecessary since the fallback
# (keyword.lower().replace(" ", "-")) already produces a valid Naukri slug
# for plain alphanumeric keywords. Add an entry here only if a keyword needs
# a different slug than its default (e.g. contains punctuation).
KEYWORD_SLUGS = {}

LOC_SLUGS = {
    "Hyderabad":     "hyderabad",
    "Bangalore":     "bangalore",
    "Visakhapatnam": "visakhapatnam",
    "Remote":        None,
}


def load_config():
    with open("config.yaml") as f:
        return yaml.safe_load(f)


def _min_experience_years():
    try:
        exp_range = load_config()["search"].get("experience_range", "0-1")
        return str(int(str(exp_range).split("-")[0]))
    except Exception:
        return "0"


def _build_url(keyword, location):
    kw_slug  = KEYWORD_SLUGS.get(keyword, keyword.lower().replace(" ", "-"))
    loc_slug = LOC_SLUGS.get(location)
    kw_param = keyword.replace(" ", "+")
    min_exp  = _min_experience_years()

    if loc_slug:
        return (f"https://www.naukri.com/{kw_slug}-jobs-in-{loc_slug}"
                f"?k={kw_param}&l={location}&experience={min_exp}")
    else:
        return (f"https://www.naukri.com/{kw_slug}-jobs"
                f"?k={kw_param}&experience={min_exp}&wfhType=2")


def _parse_jobs(html, location, max_jobs=25):
    jobs = []

    # Strategy 1: Extract from embedded JSON (__REDUX_STATE__ or similar)
    json_match = re.search(r'window\.__INITIAL_REDUX_STATE__\s*=\s*({.+?});\s*(?:window|</script>)', html, re.DOTALL)
    if not json_match:
        json_match = re.search(r'"jobDetails"\s*:\s*(\[.+?\])\s*,\s*"', html, re.DOTALL)

    if json_match:
        try:
            raw = json_match.group(1)
            # Try to parse as full redux state
            data = json.loads(raw)
            job_list = (
                data.get("jobResults", {}).get("data", {}).get("jobDetails", [])
                or data.get("jobDetails", [])
            )
            for j in job_list[:max_jobs]:
                title    = j.get("title", "")
                company  = j.get("companyName", "")
                loc_text = j.get("placeholders", [{}])
                loc_text = loc_text[0].get("label", location) if loc_text else location
                job_url  = j.get("jdURL", "")
                if not job_url.startswith("http"):
                    job_url = "https://www.naukri.com" + job_url
                desc     = j.get("jobDescription", "") or ""
                date_raw = j.get("modifiedDate", "") or ""
                job_id   = hashlib.md5(job_url.encode()).hexdigest()
                if title:
                    jobs.append({
                        "job_id": job_id, "title": title, "company": company,
                        "location": loc_text, "source": "naukri", "job_url": job_url,
                        "description": desc[:2000] if desc else "",
                        "date_posted": date_raw[:10] if date_raw else "",
                        "company_website": "",
                    })
            if jobs:
                return jobs
        except Exception:
            pass

    # Strategy 2: Parse HTML cards with BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")

    # Try multiple card selectors
    cards = (
        soup.select("article.jobTuple")
        or soup.select("[class*='srp-jobtuple-wrapper']")
        or soup.select("[data-job-id]")
        or soup.select(".jobTupleHeader")
    )

    for card in cards[:max_jobs]:
        try:
            title_el   = card.select_one("a.title, a[class*='title'], h2 a, h3 a")
            company_el = card.select_one("a.subTitle, a[class*='comp'], [class*='companyName'] a")
            loc_el     = card.select_one(".locWdth, [class*='location'] span, [class*='loc'] span")

            title   = title_el.get_text(strip=True) if title_el else ""
            company = company_el.get_text(strip=True) if company_el else ""
            loc_txt = loc_el.get_text(strip=True) if loc_el else location
            href    = title_el.get("href", "") if title_el else ""

            if not title or not href:
                continue
            if not href.startswith("http"):
                href = "https://www.naukri.com" + href

            job_id = hashlib.md5(href.encode()).hexdigest()
            jobs.append({
                "job_id": job_id, "title": title, "company": company,
                "location": loc_txt, "source": "naukri", "job_url": href,
                "description": "", "date_posted": "", "company_website": "",
            })
        except Exception:
            continue

    return jobs


def _fetch_one(keyword, location):
    url = _build_url(keyword, location)
    session = requests.Session()
    # Prime cookies
    try:
        session.get("https://www.naukri.com/", headers=HEADERS, timeout=10)
    except Exception:
        pass
    try:
        resp = session.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        return _parse_jobs(resp.text, location)
    except Exception as e:
        print(f"    Naukri error [{keyword[:30]}/{location}]: {e}")
        return []


def scrape_naukri():
    config    = load_config()
    keywords  = config["search"]["keywords"]
    locations = config["search"]["locations"]
    delay     = config["scraping"].get("delay_between_requests", 3)

    results  = []
    seen_ids = set()
    seen_slugs = set()

    for keyword in keywords:
        kw_slug = KEYWORD_SLUGS.get(keyword, keyword.lower().replace(" ", "-"))
        for location in locations:
            loc_slug = LOC_SLUGS.get(location)
            key = (kw_slug, str(loc_slug))
            if key in seen_slugs:
                continue
            seen_slugs.add(key)

            jobs = _fetch_one(keyword, location)
            new  = [j for j in jobs if j["job_id"] not in seen_ids]
            for j in new:
                seen_ids.add(j["job_id"])
                results.append(j)
            if new:
                print(f"  [Naukri] {keyword[:30]} | {location}: {len(new)} jobs")
            time.sleep(delay + random.random() * 2)

    print(f"  Naukri total: {len(results)} unique jobs")
    return results
