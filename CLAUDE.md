# JobPilot AI — Claude Context

## Who is the user
Aditi Kumari Ray — Aspiring Equity Research Analyst based in Delhi, India.
2.8 yrs experience as a Financial Advisor at Religare Broking Limited (HNI equity/derivatives trading & advisory, ~Rs30cr AUM). CFA Level I cleared, Level II candidate (appeared Aug 2026), NISM Series VIII, B.Com + PGDIBO.
Target roles: broad finance/accounting scope (Equity Research, Investment/Financial Analyst, Accountant, Finance Executive, Credit Analyst, FP&A Analyst, etc.), 1–3 yrs experience, location priority Hyderabad > Bangalore > Visakhapatnam.
Email: aditiraycapital@gmail.com — notification emails are sent here (see `notify.recipient_email` in config.yaml).

## What this project does
Autonomous job scraping and matching pipeline for FINANCE roles (not tech/dev). Runs every 2 hours,
scrapes job boards, scores each job with Gemini AI against whatever resume is loaded at
`resume/resume.pdf` (the scoring prompt injects the actual resume text — it's not hardcoded to one
candidate's bio, so swapping the resume file re-targets the whole pipeline), stores matches in
MongoDB Atlas, exports to Excel, and sends a notification email with the best jobs — never
re-sending a posting that's already been emailed, even if a different source scrapes it again later.

---

## Architecture overview

```
scheduler.py          — APScheduler BlockingScheduler, runs run_pipeline() every 2 hours
  └─ scrapers/        — 4 sources, all run in parallel via ThreadPoolExecutor
  └─ agent/
       resume_matcher.py   — 3-pass scorer: pre-filter → cache → batch Gemini AI
       exp_filter.py       — shared regex filters (INTERNSHIP/SENIOR/IRRELEVANT_TITLE_RE)
       description_filler.py — fetches missing descriptions, batch re-scores
       email_drafter.py    — template-based cold email (no LLM)
       notify_email.py     — Gmail SMTP notification with Excel attachment
  └─ storage/
       database.py         — MongoDB Atlas CRUD + dedup + company analytics (see below)
       excel_export.py     — openpyxl export with 3 sheets
  └─ monitor.py        — health checker (run separately, every 30 min)
```

---

## Pipeline flow (4 steps)

```
[1/4] Load resume + reset LLM fallback flags
[2/4] Scrape 4 sources in parallel → dedup → drop stale (>14 days)
[3/4] Score — 3 passes:
        Pass 1: instant regex pre-filters (INTERNSHIP/SENIOR/IRRELEVANT/exp req/no-desc)
                → score=1 inserted, score=5 for no-desc
        Pass 2: DB cache (7-day lookback by title+company LOWER match, MongoDB)
        Pass 3: Gemini batch AI (5 jobs/call) → Groq fallback → Ollama fallback
                → score=0 means AI failed — NOT inserted into DB
[4/4] Description filler (fetch missing descs, batch re-score)
      → reload relevant[] from DB (filler may have downgraded some)
      → drop jobs already emailed before under the same title+company (cross-run/
        cross-source dedup — see storage/database.py was_recently_notified())
      → export Excel → send Gmail notification → mark sent jobs as "emailed"
```

---

## Active scrapers (4 total, all parallel)

| Source | Type | Notes |
|---|---|---|
| LinkedIn/Indeed/Google | jobspy library | 12 keywords × 3 locations × 40 results |
| Foundit | requests (JSON API) | Indian job board |
| TimesJobs | requests+BS4 | Indian job board |
| Naukri | requests+BS4 | India #1 board (may hit Cloudflare) |

All four read `search.keywords` / `search.locations` from config.yaml directly — no per-scraper
hardcoded search terms — so re-tuning config.yaml is enough to redirect the whole search.

**Deleted entirely** (were tech-only by internal design, not just config — could not be re-tuned
for finance without rewriting each one from scratch): `scrapers/hirist.py`, `cutshort.py`,
`wellfound.py` (tech/startup job boards), `remoteok.py`, `remotive.py`, `jobicy.py`,
`workingnomads.py` (remote-tech APIs with hardcoded React/Node/MERN tags), `internshala.py`
(internship-focused — excluded per "no internships"), `hackernews.py`, `shine.py`,
`freshersworld.py`, `arbeitnow.py` (already-dead from an earlier cleanup). If a future need for
tech-role scraping ever comes back, these would need to be rewritten, not restored as-is.

---

## AI scoring

**Primary:** Gemini 2.5 Flash (free tier)
**Secondary fallback:** Groq llama-3.1-8b-instant
**Tertiary fallback:** Ollama qwen3:8b (local, disabled by default)

Fallback chain resets at the start of each pipeline run.

Scoring rules are built dynamically by `resume_matcher.py` `_build_rules()` — NOT a hardcoded bio.
It injects the actual text of whichever resume is loaded (`resume/resume.pdf`) into the AI prompt,
so the LLM compares each job against the real resume rather than a fixed candidate description.
The experience "too senior" ceiling is also dynamic: `config.matching.max_experience_years + 2`
(currently 3+2=5yrs for Aditi's profile) — change `max_experience_years` in config.yaml, not code.

- 9–10: role's title/function closely matches the loaded resume's target role/level, full-time permanent
- 7–8: same finance function/domain as the resume, junior-mid level, exp requirement roughly matches
- 5–6: same broad domain (finance/accounting) but different function than the resume, exp unclear
- 1–3: auto if over the experience ceiling / senior-lead-director title / internship / role outside finance entirely (tech, manufacturing, etc.)
- CRITICAL: description requiring more years than the ceiling anywhere → score 1-3, no exceptions

Batch scoring: 5 jobs per Gemini API call (saves ~80% quota vs 1 job/call).

---

## Key files and what's in them

### agent/resume_matcher.py
- `_build_rules()` — builds the scoring prompt dynamically from the loaded resume text + config
  (`matching.max_experience_years`); called fresh by every prompt builder, not a static constant
- `_build_batch_prompt(jobs)` — builds prompt for up to 5 jobs, returns JSON array
- `_score_batch_via_gemini(jobs, model)` — POST to Gemini REST API (no SDK)
- `score_jobs_batch(jobs, batch_size=5)` — main pipeline API, prints "Batch X/Y: Gemini OK"
- `score_job(job)` — single-job API used by description_filler
- `check_score_cache(title, company, days=7)` — DB lookback
- `reset_llm_state()` — resets both `_gemini_failed` and `_groq_failed`
- Gemini API URL: `https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent`

### agent/exp_filter.py
Single source of truth for all regex filters. Import from here — never redefine in other files:
- `INTERNSHIP_TITLE_RE` — matches intern/trainee/apprentice in title
- `SENIOR_TITLE_RE` — matches senior/lead/principal/director etc in title
- `IRRELEVANT_TITLE_RE` — matches non-finance junk only (tech dev/devops/data-eng, manufacturing,
  guard, data entry, HR recruiter, generic telesales/BPO). Deliberately does NOT exclude
  finance/accounting sub-functions (RM, credit, collections, payroll, tax, insurance) — those are
  left to the AI scorer to judge against whatever resume is loaded, since a different finance
  profile in the future might legitimately target one of them.
- `_EXP_FLOOR` — dynamic "too senior" threshold = `config.matching.max_experience_years + 2`,
  read from config.yaml at import time
- `has_experience_requirement(desc)` — returns True if desc requires ≥ `_EXP_FLOOR` yrs min experience
- `find_experience_snippet(desc)` — returns snippet around exp mention (used by monitor)

### agent/email_drafter.py
Template-only, zero LLM calls. Only `draft_email_lite(job)` is used by the pipeline.
Removed: Groq email generation (dead code, never called).

### agent/notify_email.py
- `send_report(jobs, recipient=None, tag="[APPLY NOW]")` — sends Gmail with Excel attachment
- `_build_sheet(jobs, out_path)` — filters jobs through SENIOR/IRRELEVANT before writing sheet
- Headers: #, Platform, Job Title, Company, Location, Score, Apply

### agent/description_filler.py
- `run_filler(limit=50, rescore=True, dry_run=False)`
- Phase 1: fetch descriptions with 2–4s delays (rate limit)
- Phase 2: batch AI re-score via `score_jobs_batch()`
- Phase 3: write DB updates

### storage/database.py — MongoDB Atlas (pymongo), NOT SQLite
Two collections: `jobs` (one doc per posting) and `companies` (aggregated per-company stats,
built up automatically as jobs are inserted). Connection comes from `MONGODB_URI` in `.env`;
DB name from `config.yaml` `storage.mongo_db_name` (default "jobpilot").

Every job doc carries `title_key` / `company_key` (normalized, lowercased, whitespace-collapsed)
so duplicate detection works across different job_ids/URLs/sources for the same real posting —
not just exact job_id matches.

- `init_db()` — ensures indexes (unique on job_id, compound on title_key+company_key, etc.)
- `insert_job(job)` — inserts the job doc; returns False if job_id already exists. Also computes
  `is_duplicate` / `duplicate_of` by checking title_key+company_key against all prior jobs, and
  rolls the job into that company's `companies` doc (`_upsert_company`)
- `was_recently_notified(title, company, days=14)` — True if a job with the same title+company was
  already emailed recently (status="emailed") — used to skip re-sending the same posting
- `mark_notified(job_ids)` — sets status="emailed" on the jobs actually included in a sent email
- `check_score_cache(title, company, days=7)` — 7-day AI-score cache lookback (moved here from
  resume_matcher.py so all DB access lives in one module)
- `find_duplicate_groups(min_count=2)` — aggregation report: title+company groups seen 2+ times,
  with their sources and job_ids — a direct answer to "which postings are duplicates"
- `list_companies(min_jobs=1, limit=50)` — companies ranked by best score seen, then job count
- `delete_zero_score_jobs()` — deletes jobs where relevance_score = 0
- `get_relevant_jobs(min_score=7)`, `get_all_jobs()`, `list_jobs(...)`, `get_jobs_by_ids(...)` —
  ordered by days_old ASC, score DESC (same contract as the old SQLite versions)
- `get_jobs_missing_description`, `get_jobs_with_hr_email`, `get_relevant_jobs_since`,
  `get_last_scraped_time`, `get_recent_high_score_jobs`, `get_score_distribution`, `get_db_stats`
  — replace the raw SQL that used to live inline in description_filler.py/monitor.py/
  export_fresher_jobs.py/push_all_drafts.py/test_5_drafts.py; those files now call these instead
  of touching a database driver directly
- `update_contact`, `update_draft_email` — still in schema but not called by the automated pipeline

### storage/excel_export.py
- COLUMN_MAP maps DB field "source" → "Platform" header
- 3 sheets: All Matches / Last 24hrs / Internship Friendly
- Score colors: green 9-10 / yellow 7-8 / red 0-6

### scheduler.py
- Imports INTERNSHIP/SENIOR/IRRELEVANT_TITLE_RE from exp_filter (not redefined locally)
- Log rotation: trim pipeline_log.txt to last 2000 lines when it exceeds 5000
- Per-source scrape counts logged to pipeline_log.txt
- score=0 jobs skipped with "not saved" message, never inserted into DB

### monitor.py
- Reads `interval_hours` from config.yaml for stale threshold (not hardcoded)
- Uses SENIOR_TITLE_RE from exp_filter
- "Quality: OK" only shown when total_checked > 0

---

## Config (config.yaml)

```yaml
search:
  keywords: 12 keywords (broad finance/accounting: Equity Research, Investment/Financial
            Analyst, Accountant, Finance Executive, Credit Analyst, FP&A, etc.)
  locations: Hyderabad, Bangalore, Visakhapatnam (priority order)
  experience_range: "1-3"
  results_per_keyword: 40

matching:
  relevance_threshold: 7
  gemini_model: "gemini-2.5-flash"
  groq_model: "llama-3.1-8b-instant"
  ollama_model: "qwen3:8b"
  use_ollama: false
  max_experience_years: 3   # drives the dynamic "too senior" ceiling in exp_filter.py + resume_matcher.py

scraping:
  interval_hours: 2
  hours_old: 24
  delay_between_requests: 3

storage:
  mongo_db_name: "jobpilot"   # MongoDB Atlas database name (connection string in .env)
  excel_path: "jobs_output.xlsx"

resume:
  path: "resume/resume.pdf"   # swap this file to re-target the whole pipeline to a new candidate

notify:
  recipient_email: "aditiraycapital@gmail.com"   # where match notifications are sent
```

---

## Environment (.env) — NEVER print or commit these values

```
GEMINI_API_KEY=...        # from aistudio.google.com — must start with AIzaSy
GROQ_API_KEY=...          # from console.groq.com
GMAIL_ADDRESS=...         # Gmail account for sending
GMAIL_APP_PASSWORD=...    # Gmail App Password (not regular password)
OLLAMA_MODEL=qwen3:8b     # optional local model
MONGODB_URI=...           # MongoDB Atlas connection string (mongodb+srv://user:pass@cluster...)
```

---

## Security constraints (permanent, never override)

1. **Never auto-send job application emails** — only notification emails to Aditi herself
2. **Never commit or push to git unless Aditi explicitly asks**
3. **Never print GROQ_API_KEY, GMAIL_APP_PASSWORD, or MONGODB_URI in output** (the Mongo URI has the DB password embedded in it)
4. **Never push --force to main**

---

## How to run

```bash
# Start the pipeline scheduler (runs every 2 hours)
python scheduler.py

# Run monitor separately (checks every 30 min)
python monitor.py

# One-off pipeline run
python -c "from scheduler import run_pipeline; run_pipeline()"

# Export Excel manually
python -c "from storage.excel_export import export_to_excel; export_to_excel()"

# DB cleanup
python -c "from storage.database import delete_zero_score_jobs; print(delete_zero_score_jobs(), 'deleted')"
```

---

## Custom slash commands (.claude/commands/)

| Command | What it does |
|---|---|
| `/job-stats` | DB overview — totals, source breakdown, score distribution |
| `/top-jobs [N]` | Top N matched jobs by score (default 15) |
| `/run-pipeline` | Trigger one manual pipeline run |
| `/check-monitor` | Health check — pipeline alive, quality issues, score sanity |
| `/clean-db` | Delete score=0 jobs + old score=1 rejects |
| `/export` | Regenerate jobs_output.xlsx from current DB |
| `/test-scorer` | Fire a test job at Gemini to verify the API works |

---

## What was removed (do not re-add without asking)

- **All tech-only scrapers, deleted outright** (not just disabled): `hirist.py`, `cutshort.py`,
  `wellfound.py`, `remoteok.py`, `remotive.py`, `jobicy.py`, `workingnomads.py`, `internshala.py`,
  `hackernews.py`, `shine.py`, `freshersworld.py`, `arbeitnow.py` — structurally tech/startup/
  internship-only, couldn't be re-tuned for finance via config alone
- **`playwright` / `playwright-stealth`** — removed from requirements.txt, only used by the deleted
  browser-based scrapers (Wellfound/Cutshort/Freshersworld); no active scraper needs a browser
- **Kumar's old resume.tex + contact_finder.py + test_contact_finder.py** — leftover from the prior
  tech-recruiting profile, unused and deleted
- **Hardcoded `_RULES` scoring constant** — replaced with `_build_rules()`, which reads the actual
  resume text at runtime instead of a fixed candidate bio (see "AI scoring" above)
- **Gmail Drafts (IMAP APPEND)** — removed along with contact finder
- **AI email drafting via Groq** — dead code, `draft_email_lite()` (template) is sufficient
- **Matched_skills / missing_skills in scoring response** — removed to save output tokens

---

## Known issues / things to watch

- **Naukri**: may return 0 jobs if Cloudflare blocks the request — not a bug, just log it
- **Gemini free tier**: gemini-2.5-flash works; gemini-2.0-flash has limit=0 on free tier
- **Gemini quota**: free tier is ~1500 req/day. With batch scoring (5 jobs/call) and a 2hr interval, quota lasts all day comfortably
- **score=0**: means AI failed entirely (all 3 providers failed). These jobs are NOT in the DB.

---

## Database (SQLite → MongoDB Atlas migration)

The old `jobs.db` SQLite file is gone entirely — every file that used to touch it directly
(scheduler.py, monitor.py, description_filler.py, export_fresher_jobs.py, push_all_drafts.py,
test_5_drafts.py, and the `.claude/commands/*.md` snippets) now goes through `storage/database.py`,
which is 100% MongoDB. There is no SQLite fallback and no dual-write — do not reintroduce sqlite3
imports anywhere in this project.

**Duplicate detection** happens at two points:
1. **At insert time** (`insert_job`) — every job is checked against ALL prior jobs by normalized
   title+company and flagged `is_duplicate` / `duplicate_of`. This is a general "have we seen this
   posting before" signal, stored for visibility/analytics — it does NOT block the insert.
2. **Before sending the notification email** (`was_recently_notified`, checked in scheduler.py) —
   a job is dropped from the email if a job with the same title+company was already marked
   `status="emailed"` in the last 14 days, even if this run scraped it from a different source with
   a different job_id/URL. After a successful send, `mark_notified()` sets that status on the jobs
   that went out.

**Companies collection** (new) — every insert also rolls into a per-company aggregate doc:
first/last seen, best score ever seen, total postings seen, sources, locations, last 10 titles.
`list_companies()` surfaces which companies are worth watching. `find_duplicate_groups()` gives a
direct report of which (title, company) pairs have been scraped more than once and from where.

---

## System specs
- Windows 11 Pro
- Local LLM via Ollama is viable if needed (qwen3:8b configured)
