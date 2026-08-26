"""
Email drafter — template-based only, zero LLM calls.
draft_email_lite() is the only function used by the pipeline.
"""

LINKEDIN = "https://www.linkedin.com/in/aditi-kumari-ray-970b66220"

FINANCE_KEYWORDS = [
    "Equity Research", "Financial Modelling", "Financial Modeling", "Valuation",
    "DCF", "Fundamental Analysis", "Portfolio Management", "Equity Analysis",
    "Investment Research", "Derivatives", "Cash Equities", "CFA",
]

GENERIC_BODY = """\
Hi,

I hope you're doing well.

I'm a finance professional with 2.8 years of experience at Religare Broking Limited, advising HNI clients and executing trades across cash equities, derivatives, and structured investment products for an active book of ~Rs30 crore AUM.{tech_line} Alongside this, I've been building CFA-level rigor in fundamental analysis, valuation, and portfolio management — CFA Level I cleared, Level II appeared (August 2026).

I'd love to explore if there's a fit for the {title} role at {company}.

LinkedIn: {linkedin}

Please find my resume attached. Thank you for your time.

Regards,
Aditi Kumari Ray
aditiraycapital@gmail.com | +91-9289645684"""


def _extract_tech_mentions(description: str, max_n: int = 2) -> list:
    if not description or str(description).strip() in ("", "nan", "None"):
        return []
    desc_lower = str(description).lower()
    found, found_lower = [], []
    for kw in FINANCE_KEYWORDS:
        kw_lower = kw.lower()
        if kw_lower not in desc_lower:
            continue
        if any(kw_lower in f or f in kw_lower for f in found_lower):
            continue
        found.append(kw)
        found_lower.append(kw_lower)
        if len(found) >= max_n:
            break
    return found


def draft_email_lite(job) -> str:
    """Template-based cold email — no LLM, no API calls."""
    title   = str(job.get("title", "")).split("|")[0].strip()
    company = str(job.get("company", "")).strip()
    desc    = job.get("description", "")

    tech_found = _extract_tech_mentions(desc)
    if tech_found:
        tech_str  = " and ".join(tech_found) if len(tech_found) <= 2 else ", ".join(tech_found)
        tech_line = f" I noticed the role focuses on {tech_str} — that lines up well with my experience."
    else:
        tech_line = ""

    subject = f"Application – {title} at {company} | Aditi Kumari Ray"
    body    = GENERIC_BODY.format(
        tech_line=tech_line, title=title, company=company,
        linkedin=LINKEDIN,
    )
    return f"SUBJECT: {subject}\n\n{body}"
