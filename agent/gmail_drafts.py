import imaplib
import os
import re
import time
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import yaml
from dotenv import load_dotenv

load_dotenv()

GMAIL_ADDRESS  = os.getenv("GMAIL_ADDRESS", "")
GMAIL_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")
IMAP_HOST      = "imap.gmail.com"
DRAFTS_FOLDER  = "[Gmail]/Drafts"

# ── Email sanity filters (mirrors contact_finder.py) ──────────────────────────
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif", ".ico",
              ".bmp", ".avif", ".tiff", ".tif", ".woff", ".woff2", ".css",
              ".js", ".json", ".xml", ".pdf", ".zip"}

PLACEHOLDER_EMAILS = {
    "yourname@email.com", "user@domain.com", "joe.smith@company.com",
    "name@company.com", "example@example.com", "test@test.com",
    "tony.stark@stark.com", "email@email.com",
}

SKIP_PREFIXES = {
    "noreply", "no-reply", "support", "info", "admin", "test", "example",
    "webmaster", "postmaster", "sales", "billing", "upgrade", "renewal",
    "cancellation", "invoice", "newsletter", "unsubscribe", "bounce",
    "mailer", "daemon", "press", "media", "pr", "fraud", "abuse", "legal",
    "privacy", "security", "help", "feedback", "contact", "hello", "hi",
    "hey", "team", "general", "office", "customer", "service", "premium",
    "referral", "partner", "affiliate", "pay", "payment", "slice",
    "suspicious", "report",
}

SKIP_DOMAINS = {"example.com", "sentry.io", "wixpress.com", "googleapis.com",
                "w3.org", "schema.org", "placeholder.com", "vs-errors.eightfold.ai"}

# TLDs that are not real IANA-registered domains (catches ROT13/encoded addresses)
INVALID_TLDS = {"pu", "onion", "local", "internal", "localhost", "invalid", "test"}

_HTML_ENTITY_RE = re.compile(r"u00[0-9a-f]{2}", re.IGNORECASE)


def _is_valid_email(email: str) -> bool:
    """Return False for image filenames, placeholders, encoded, and wrong-purpose addresses."""
    if not email or "@" not in email:
        return False
    e = email.lower().strip()
    if e in PLACEHOLDER_EMAILS:
        return False
    parts = e.split("@")
    if len(parts) != 2:
        return False
    prefix, domain = parts
    # Block HTML-entity encoded prefixes (e.g. u003ehelp, u0022name)
    if _HTML_ENTITY_RE.search(prefix):
        return False
    if any(domain.endswith(ext) for ext in IMAGE_EXTS):
        return False
    tld_parts = domain.split(".")
    if len(tld_parts) < 2 or len(tld_parts[-1]) < 2:
        return False
    # Block known-invalid / non-IANA TLDs (catches ROT13-encoded addresses like .pu)
    if tld_parts[-1].lower() in INVALID_TLDS:
        return False
    if domain in SKIP_DOMAINS:
        return False
    if any(prefix.startswith(p) for p in SKIP_PREFIXES):
        return False
    return True


def _collect_valid_emails(job: dict) -> list:
    """Return all valid, deduped HR emails for a job (hr_email, hr_email_2, hr_email_3)."""
    candidates = [job.get("hr_email"), job.get("hr_email_2"), job.get("hr_email_3")]
    seen, out = set(), []
    for e in candidates:
        if not e:
            continue
        e = e.strip().lower()
        if e in seen or not _is_valid_email(e):
            continue
        seen.add(e)
        out.append(e)
    return out


def _load_resume_path() -> str:
    try:
        with open("config.yaml") as f:
            cfg = yaml.safe_load(f)
        return cfg.get("resume", {}).get("path", "resume/resume.pdf")
    except Exception:
        return "resume/resume.pdf"


def _attach_resume(msg: MIMEMultipart, resume_path: str) -> bool:
    """Attach PDF resume to the email. Returns True if attached."""
    if not resume_path or not os.path.exists(resume_path):
        return False
    try:
        with open(resume_path, "rb") as f:
            attach = MIMEBase("application", "octet-stream")
            attach.set_payload(f.read())
        encoders.encode_base64(attach)
        filename = os.path.basename(resume_path)
        attach.add_header("Content-Disposition", "attachment", filename=filename)
        msg.attach(attach)
        return True
    except Exception as e:
        print(f"    Resume attach error: {e}")
        return False


def delete_all_drafts() -> int:
    """Delete ALL drafts in [Gmail]/Drafts. Returns count deleted."""
    if not GMAIL_ADDRESS or not GMAIL_PASSWORD:
        print("  Gmail credentials not set.")
        return 0
    deleted = 0
    try:
        imap = imaplib.IMAP4_SSL(IMAP_HOST)
        imap.login(GMAIL_ADDRESS, GMAIL_PASSWORD)
        imap.select(DRAFTS_FOLDER)
        _, data = imap.search(None, "ALL")
        msg_ids = data[0].split()
        print(f"  Found {len(msg_ids)} drafts to delete...")
        for mid in msg_ids:
            imap.store(mid, "+FLAGS", "\\Deleted")
            deleted += 1
        imap.expunge()
        imap.logout()
        print(f"  Deleted {deleted} drafts.")
    except Exception as e:
        print(f"  Delete drafts error: {e}")
    return deleted


def _parse_draft(draft_text: str):
    """Parse 'SUBJECT: ...\n\nbody' format into (subject, body)."""
    lines = draft_text.strip().splitlines()
    subject = ""
    body_lines = []
    in_body = False

    for line in lines:
        if not in_body and line.lower().startswith("subject:"):
            subject = line[8:].strip()
        elif not in_body and line.strip() == "":
            in_body = True
        elif in_body:
            body_lines.append(line)

    if not subject and lines:
        subject = lines[0][:80]
        body_lines = lines[1:]

    return subject, "\n".join(body_lines).strip()


def save_draft(to_email: str, draft_text: str, job_title: str = "", company: str = "") -> bool:
    """Save a single cold email as a Gmail Draft with resume attached. Returns True on success."""
    if not GMAIL_ADDRESS or not GMAIL_PASSWORD:
        print("    Gmail credentials not set — skipping draft save")
        return False
    if not to_email or not draft_text:
        return False
    if not _is_valid_email(to_email):
        print(f"    Skipping invalid/bad email: {to_email}")
        return False

    subject, body = _parse_draft(draft_text)
    if not subject:
        subject = f"Application – {job_title} at {company}"

    msg = MIMEMultipart("mixed")
    msg["From"]    = GMAIL_ADDRESS
    msg["To"]      = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))
    _attach_resume(msg, _load_resume_path())

    try:
        imap = imaplib.IMAP4_SSL(IMAP_HOST)
        imap.login(GMAIL_ADDRESS, GMAIL_PASSWORD)
        imap.append(
            DRAFTS_FOLDER,
            "\\Draft",
            imaplib.Time2Internaldate(time.time()),
            msg.as_bytes(),
        )
        imap.logout()
        return True
    except Exception as e:
        print(f"    Gmail draft error: {e}")
        return False


def save_all_drafts(jobs: list) -> int:
    """
    Push jobs that have both a valid hr_email and draft_email to Gmail Drafts.
    Attaches resume PDF to every draft. Skips bad/invalid emails.
    Returns count of successfully saved drafts.
    """
    if not GMAIL_ADDRESS or not GMAIL_PASSWORD:
        print("  Gmail credentials not configured — skipping.")
        return 0

    resume_path = _load_resume_path()
    resume_exists = os.path.exists(resume_path)
    if not resume_exists:
        print(f"  WARNING: Resume not found at '{resume_path}' — drafts will have no attachment")

    eligible = []
    for j in jobs:
        emails = _collect_valid_emails(j)
        if emails and j.get("draft_email"):
            eligible.append((j, emails))
    skipped = len(jobs) - len(eligible)

    print(f"\n  Saving {len(eligible)} drafts to Gmail ({GMAIL_ADDRESS})...")
    if skipped:
        print(f"  Skipped {skipped} jobs with no valid email addresses")

    saved = 0
    try:
        imap = imaplib.IMAP4_SSL(IMAP_HOST)
        imap.login(GMAIL_ADDRESS, GMAIL_PASSWORD)

        for job, emails in eligible:
            subject, body = _parse_draft(job["draft_email"])
            if not subject:
                subject = f"Application – {job.get('title','')} at {job.get('company','')}"

            msg = MIMEMultipart("mixed")
            msg["From"]    = GMAIL_ADDRESS
            msg["To"]      = ", ".join(emails)
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "plain", "utf-8"))
            _attach_resume(msg, resume_path)

            try:
                imap.append(
                    DRAFTS_FOLDER,
                    "\\Draft",
                    imaplib.Time2Internaldate(time.time()),
                    msg.as_bytes(),
                )
                saved += 1
                attach_tag = " [+resume]" if resume_exists else ""
                print(f"    Saved{attach_tag}: {subject[:50]} → {', '.join(emails)}")
            except Exception as e:
                print(f"    Failed [{job.get('company','')}]: {e}")

        imap.logout()
    except Exception as e:
        print(f"  Gmail login error: {e}")

    print(f"  Gmail Drafts saved: {saved}/{len(eligible)}")
    return saved
