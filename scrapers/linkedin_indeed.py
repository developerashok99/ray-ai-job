import hashlib
import time
import yaml
from jobspy import scrape_jobs


def load_config():
    with open("config.yaml") as f:
        return yaml.safe_load(f)


def scrape_linkedin_indeed():
    config = load_config()
    keywords  = config["search"]["keywords"]
    locations = config["search"]["locations"]
    results_per = config["scraping"].get("results_per_keyword", 25)
    delay       = config["scraping"].get("delay_between_requests", 3)
    hours_old   = config["scraping"].get("hours_old", 168)

    all_jobs  = []
    seen_keys = set()

    for keyword in keywords:
        for location in locations:
            # Indeed needs a real country; for Remote pass India
            indeed_loc = "India" if location.lower() == "remote" else location
            print(f"  [Multi-Board] {keyword} | {location}")
            try:
                df = scrape_jobs(
                    site_name=["linkedin", "indeed", "google"],
                    search_term=keyword,
                    location=indeed_loc,
                    results_wanted=results_per,
                    country_indeed="India",
                    hours_old=hours_old,
                    linkedin_fetch_description=True,
                )
                if df is None or df.empty:
                    print("    No results.")
                    continue

                added = 0
                for _, row in df.iterrows():
                    url     = str(row.get("job_url", "")).strip()
                    title   = str(row.get("title",   "")).strip()
                    company = str(row.get("company", "")).strip()

                    if not url:
                        continue

                    dedup_key = f"{company.lower()}|{title.lower()}"
                    if url in seen_keys or dedup_key in seen_keys:
                        continue
                    seen_keys.add(url)
                    seen_keys.add(dedup_key)

                    job_id = hashlib.md5(url.encode()).hexdigest()
                    all_jobs.append({
                        "job_id":          job_id,
                        "title":           title,
                        "company":         company,
                        "location":        str(row.get("location", "")).strip() or location,
                        "source":          str(row.get("site", "multi")),
                        "job_url":         url,
                        "description":     str(row.get("description", "")).strip(),
                        "date_posted":     str(row.get("date_posted", "")).strip(),
                        "company_website": str(row.get("company_url", "")).strip(),
                    })
                    added += 1

                print(f"    +{added} unique jobs.")
            except Exception as e:
                print(f"    Error: {e}")

            time.sleep(delay)

    print(f"  Multi-board total unique jobs: {len(all_jobs)}")
    return all_jobs
