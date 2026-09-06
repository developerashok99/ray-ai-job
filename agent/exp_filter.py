"""
Shared experience-requirement detection + title filters for the whole pipeline.
Import regex patterns from here — do NOT redefine them in other files.
"""
import re

import yaml

# ── Title pre-filter regexes (single source of truth) ────────────────────────

INTERNSHIP_TITLE_RE = re.compile(
    r"\b(intern(ship)?|internships|trainee\s+program|apprentice(ship)?)\b",
    re.IGNORECASE,
)

SENIOR_TITLE_RE = re.compile(
    r"\b(senior|sr\.?\s|lead\s|principal|staff\s+eng|engineering\s+manager|"
    r"director|head\s+of|vice\s+pres|vp\s+of|architect(?!\s+as))\b",
    re.IGNORECASE,
)

# Only true non-finance junk — finance/accounting sub-functions (RM, credit,
# collections, payroll, tax, insurance, etc.) are left to the AI scorer to
# judge against whatever resume is loaded, since a future finance profile
# may legitimately target one of those.
IRRELEVANT_TITLE_RE = re.compile(
    r"\b("
    r"telecaller|telesales|bpo|call\s+center|"
    r"data\s+entry|hr\s+(recruiter|executive)|"
    r"sales\s+executive|business\s+development\s+exec\w*|"
    r"software\s+(developer|engineer)|full[- ]?stack\s+(developer|engineer)|"
    r"react\s*\.?\s*(developer|engineer)|node\s*\.?\s*js\s*(developer|engineer)|"
    r"frontend\s+(developer|engineer)|backend\s+(developer|engineer)|web\s+developer|"
    r"devops|data\s+scientist|data\s+engineer\w*|"
    r"manufacturing|quality\s+assurance|qa\s+engineer|"
    r"guard\b|transportation\s+rep"
    r")",
    re.IGNORECASE,
)


def _read_max_experience_years(default=3):
    try:
        with open("config.yaml") as f:
            cfg = yaml.safe_load(f)
        return int(cfg.get("matching", {}).get("max_experience_years", default))
    except Exception:
        return default


# "Too senior" floor = candidate's own max years (from config) + a 2yr cushion.
# Jobs requiring up to that many years are left alone — only genuinely
# over-qualified requirements get pre-filtered out before the AI even runs.
_EXP_FLOOR = _read_max_experience_years() + 2
_YRS = "|".join(str(n) for n in range(_EXP_FLOOR, 21))

# An explicit range like "3 to 6 years" or "3-6 yrs" — matched FIRST and separately,
# because its true floor is the LOWER number. Without this, "3 to 6 years of relevant
# experience" would wrongly get caught by the standalone "N years of ... experience"
# pattern below matching on the upper number (6), rejecting a posting whose real
# minimum (3) is perfectly in range.
_RANGE_RE = re.compile(
    r"\b(\d{1,2})\s*\\?(?:[-–]|to)\s*(\d{1,2})\s*(?:years?|yrs?)",
    re.IGNORECASE,
)

# Matches ONLY clear minimum/required experience at or above the floor above.
# Patterns deliberately narrow to avoid false positives:
#   - "up to X years" = ceiling, excluded
#   - "35 years of experience" (company history) = excluded (cap at 20)
#   - "candidates with N+ years welcome" = requirement, included (the + marks floor)
EXP_MIN_RE = re.compile(
    # "N+ yrs", "N+ years"  — the + explicitly marks this as a minimum
    rf"\b({_YRS})\s*\+\s*(?:years?|yrs?)"
    # "minimum N years", "min N yrs", "at least N years"
    rf"|\b(?:minimum|min\.?|at\s+least)\s+(?:of\s+)?({_YRS})\s+(?:years?|yrs?)"
    # "requires N years", "required: N years"
    rf"|\brequires?\s+({_YRS})\s*\+?\s*(?:years?|yrs?)"
    # "must have N years", "should have N+ years", "we need N years"
    rf"|\b(?:must\s+have|should\s+have|we\s+need|need\s+to\s+have)\s+({_YRS})\s*\+?\s*(?:years?|yrs?)"
    # "experience: N+", "experience required: N"
    rf"|\bexperience\s*(?:required)?\s*[:\-]\s*({_YRS})\s*\+?"
    # "N years of relevant/professional/work/hands-on experience" — only when N isn't
    # already accounted for as the upper bound of a range (checked separately, see below)
    rf"|\b({_YRS})\s+(?:years?|yrs?)\s+(?:of\s+)?(?:relevant|professional|work|industry|hands.on)\s+experience"
    # "proven experience of N years", "experience of N+ years"
    rf"|\bexperience\s+of\s+({_YRS})\s*\+?\s*(?:years?|yrs?)",
    re.IGNORECASE,
)

# Patterns that indicate a ceiling/welcome phrase — if found before a number, it's NOT a min req
_CEILING_MARKERS = ("up to", "upto", "maximum", "max ", "no more than", "less than",
                    "fewer than", "as many as", "welcome", "open to")


def _is_ceiling(description: str, start: int) -> bool:
    prefix = description[max(0, start - 25): start].lower()
    return any(marker in prefix for marker in _CEILING_MARKERS)


def _find_match(description: str):
    """
    Return the re.Match for the first genuine over-the-floor requirement, checking
    explicit ranges (by their LOWER bound) before standalone single-number mentions,
    and never letting a range's own upper-bound number double-count as a standalone hit.
    """
    consumed = []
    for m in _RANGE_RE.finditer(description):
        lo, hi = int(m.group(1)), int(m.group(2))
        if lo > hi:
            continue
        consumed.append(m.span())
        if _is_ceiling(description, m.start()):
            continue
        if lo >= _EXP_FLOOR:
            return m

    for m in EXP_MIN_RE.finditer(description):
        if any(s <= m.start() < e for s, e in consumed):
            continue  # this number is a range's upper bound, already resolved above
        if _is_ceiling(description, m.start()):
            continue
        return m

    return None


def has_experience_requirement(description: str) -> bool:
    """
    Return True if the job description has a MINIMUM experience requirement above the
    candidate's level (config max_experience_years + cushion, see _EXP_FLOOR above).
    Returns False for ceiling phrases like "up to 5 years welcome" or company history,
    and for ranges whose true (lower-bound) floor is within reach, e.g. "3 to 6 years".
    """
    if not description or str(description).strip() in ("", "nan", "None"):
        return False
    return _find_match(description) is not None


def find_experience_snippet(description: str) -> str | None:
    """Return a short snippet around the first minimum experience requirement, or None."""
    if not description or str(description).strip() in ("", "nan", "None"):
        return None

    m = _find_match(description)
    if not m:
        return None
    snippet = description[max(0, m.start() - 15): m.end() + 25].replace("\n", " ")
    return snippet.strip()
