import sqlite3
from datetime import datetime
import yaml


def get_db_path():
    with open("config.yaml") as f:
        return yaml.safe_load(f)["storage"]["db_path"]


def get_conn():
    return sqlite3.connect(get_db_path())


def init_db():
    conn = get_conn()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS jobs (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id              TEXT UNIQUE,
            title               TEXT,
            company             TEXT,
            location            TEXT,
            source              TEXT,
            job_url             TEXT,
            description         TEXT,
            relevance_score     INTEGER DEFAULT 0,
            match_reason        TEXT,
            internship_friendly INTEGER DEFAULT 0,
            experience_required TEXT,
            days_old            INTEGER,
            hr_email            TEXT,
            hr_email_2          TEXT,
            hr_email_3          TEXT,
            hr_name             TEXT,
            phone               TEXT,
            contact_form_url    TEXT,
            company_website     TEXT,
            draft_email         TEXT,
            date_posted         TEXT,
            date_scraped        TEXT,
            status              TEXT DEFAULT 'new',
            applied             INTEGER DEFAULT 0
        )
    ''')
    conn.commit()
    conn.close()
    migrate_db()
    print("Database initialized.")


def migrate_db():
    """Add any missing columns to support schema upgrades."""
    new_cols = [
        ("internship_friendly", "INTEGER DEFAULT 0"),
        ("experience_required", "TEXT"),
        ("days_old",            "INTEGER"),
        ("hr_email_2",          "TEXT"),
        ("hr_email_3",          "TEXT"),
        ("phone",               "TEXT"),
        ("contact_form_url",    "TEXT"),
        ("draft_email",         "TEXT"),
        ("status",              "TEXT DEFAULT 'new'"),
        ("match_reason",        "TEXT"),
        ("applicants",          "TEXT"),
    ]
    conn = get_conn()
    existing = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    for col_name, col_type in new_cols:
        if col_name not in existing:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {col_name} {col_type}")
            print(f"  DB migration: added column '{col_name}'")
    conn.commit()
    conn.close()


def job_exists(job_id):
    conn = get_conn()
    row = conn.execute("SELECT id FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
    conn.close()
    return row is not None


def insert_job(job):
    conn = get_conn()
    try:
        conn.execute('''
            INSERT INTO jobs
                (job_id, title, company, location, source, job_url, description,
                 relevance_score, match_reason, internship_friendly, experience_required,
                 days_old, hr_email, hr_name, company_website, date_posted, date_scraped,
                 applicants)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ''', (
            job.get("job_id"),        job.get("title"),       job.get("company"),
            job.get("location"),      job.get("source"),      job.get("job_url"),
            job.get("description"),   job.get("relevance_score", 0),
            job.get("match_reason"),  job.get("internship_friendly", 0),
            job.get("experience_required"), job.get("days_old"),
            job.get("hr_email"),      job.get("hr_name"),
            job.get("company_website"), job.get("date_posted"),
            datetime.now().isoformat(), job.get("applicants", "")
        ))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def update_contact(job_id, contacts: dict):
    conn = get_conn()
    conn.execute('''
        UPDATE jobs SET
            hr_email         = ?,
            hr_email_2       = ?,
            hr_email_3       = ?,
            hr_name          = ?,
            phone            = ?,
            contact_form_url = ?
        WHERE job_id = ?
    ''', (
        contacts.get("email_1"), contacts.get("email_2"), contacts.get("email_3"),
        contacts.get("name"),    contacts.get("phone"),   contacts.get("contact_form"),
        job_id
    ))
    conn.commit()
    conn.close()


def update_draft_email(job_id, draft):
    conn = get_conn()
    conn.execute("UPDATE jobs SET draft_email = ? WHERE job_id = ?", (draft, job_id))
    conn.commit()
    conn.close()


def delete_zero_score_jobs() -> int:
    """Delete all jobs with relevance_score = 0 (failed scoring). Returns count deleted."""
    conn = get_conn()
    cur  = conn.execute("DELETE FROM jobs WHERE relevance_score = 0")
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    return deleted


def was_recently_notified(title, company, days=14):
    """
    True if a job with the same title+company (case-insensitive) was already
    emailed in a notification recently — catches the same real posting
    re-appearing under a different job_id/URL from another source.
    """
    conn = get_conn()
    row = conn.execute("""
        SELECT id FROM jobs
        WHERE LOWER(TRIM(title))   = LOWER(TRIM(?))
          AND LOWER(TRIM(company)) = LOWER(TRIM(?))
          AND status = 'emailed'
          AND date_scraped >= datetime('now', ? || ' days')
        LIMIT 1
    """, (title, company, f"-{days}")).fetchone()
    conn.close()
    return row is not None


def mark_notified(job_ids: list):
    """Mark jobs as emailed so the same posting isn't sent again in a future run."""
    if not job_ids:
        return
    conn = get_conn()
    placeholders = ",".join("?" * len(job_ids))
    conn.execute(f"UPDATE jobs SET status = 'emailed' WHERE job_id IN ({placeholders})", job_ids)
    conn.commit()
    conn.close()


def update_status(job_id, status):
    valid = {"new", "emailed", "response", "interview", "offer", "rejected"}
    if status.lower() not in valid:
        raise ValueError(f"Status must be one of: {valid}")
    conn = get_conn()
    conn.execute("UPDATE jobs SET status = ? WHERE job_id = ?", (status.lower(), job_id))
    conn.commit()
    conn.close()


def get_relevant_jobs(min_score=7):
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        '''SELECT * FROM jobs WHERE relevance_score >= ?
           ORDER BY days_old ASC, relevance_score DESC''',
        (min_score,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_jobs():
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM jobs ORDER BY days_old ASC, relevance_score DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_jobs(min_score=7, limit=20):
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        '''SELECT job_id, title, company, location, relevance_score,
                  internship_friendly, days_old, status, hr_email
           FROM jobs WHERE relevance_score >= ?
           ORDER BY days_old ASC, relevance_score DESC LIMIT ?''',
        (min_score, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
