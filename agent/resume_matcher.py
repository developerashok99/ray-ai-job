import json
import os
import re

import pdfplumber
import requests
import yaml
from dotenv import load_dotenv

load_dotenv()

_resume_text   = None
_groq_failed   = False
_gemini_failed = False

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def reset_llm_state():
    global _groq_failed, _gemini_failed
    _groq_failed   = False
    _gemini_failed = False


reset_groq_state = reset_llm_state  # backwards-compat alias


def _get_config():
    with open("config.yaml") as f:
        return yaml.safe_load(f)


def load_resume(path=None):
    global _resume_text
    if path is None:
        path = _get_config()["resume"]["path"]
    with pdfplumber.open(path) as pdf:
        _resume_text = "\n".join(
            page.extract_text() for page in pdf.pages if page.extract_text()
        )
    print(f"Resume loaded: {len(_resume_text)} characters")
    return _resume_text


# ── Prompts ───────────────────────────────────────────────────────────────────

def _experience_ceiling():
    """Years above which a job is 'too senior' — anything more than max_experience_years."""
    try:
        max_years = int(_get_config().get("matching", {}).get("max_experience_years", 3))
    except Exception:
        max_years = 3
    return max_years + 1


def _build_rules():
    """
    Scoring rules built from the actually-loaded resume, not hardcoded to one candidate.
    This is a FINANCE-domain job matcher — score against the candidate's resume below,
    never against a software/tech stack.
    """
    ceiling = _experience_ceiling()
    resume_excerpt = (_resume_text or "")[:3000]
    return f"""SCORING — this is a FINANCE-domain job search. Compare the job posting below against the candidate's resume.

CANDIDATE RESUME:
{resume_excerpt}

BANDING:
9-10: Role's title/function closely matches the candidate's target role and experience level from the resume above, full-time permanent
7-8:  Role is in the same finance function/domain as the resume, junior-to-mid level, experience requirement roughly matches (+/-1-2yrs)
5-6:  Same broad domain (finance/accounting) but a different function than the resume, or experience requirement unclear
1-3:  AUTO if ANY → requires more than {ceiling} years experience | senior/lead/manager/director/VP title | internship | role clearly outside finance (e.g. software/tech development, manufacturing, unrelated non-finance function)
CRITICAL: if description requires more than {ceiling} years of experience ANYWHERE → score 1-3, no exceptions."""


def _trim_desc(desc, max_chars=800):
    desc = str(desc or "").strip()
    if len(desc) <= max_chars:
        return desc
    return desc[:600] + "...[cut]..." + desc[-200:]


def _build_single_prompt(job):
    return f"""{_build_rules()}

Title: {job.get('title','')}
Company: {job.get('company','')}
Location: {job.get('location','')}
Description:
{_trim_desc(job.get('description',''))}

Reply with JSON only:
{{"score":<1-10>,"reason":"<one sentence>","internship_friendly":<bool>,"experience_required":"<exact phrase or not mentioned>"}}"""


def _build_batch_prompt(jobs):
    """Batch prompt for Gemini — expects a JSON array response."""
    blocks = []
    for i, job in enumerate(jobs, 1):
        blocks.append(
            f"[JOB {i}] {job.get('title','')} @ {job.get('company','')}\n"
            f"Location: {job.get('location','')}\n"
            f"{_trim_desc(job.get('description',''))}"
        )
    jobs_text = "\n\n".join(blocks)
    return f"""{_build_rules()}

{jobs_text}

Return a JSON array with exactly {len(jobs)} objects in order:
[{{"job":1,"score":<1-10>,"reason":"<one sentence>","internship_friendly":<bool>,"experience_required":"<phrase or not mentioned>"}},...]"""


def _build_batch_prompt_groq(jobs):
    """Batch prompt for Groq — expects {\"results\":[...]} to comply with json_object mode."""
    blocks = []
    for i, job in enumerate(jobs, 1):
        blocks.append(
            f"[JOB {i}] {job.get('title','')} @ {job.get('company','')}\n"
            f"Location: {job.get('location','')}\n"
            f"{_trim_desc(job.get('description',''))}"
        )
    jobs_text = "\n\n".join(blocks)
    return f"""{_build_rules()}

{jobs_text}

Return a JSON object with key "results" containing exactly {len(jobs)} objects in order:
{{"results":[{{"job":1,"score":<1-10>,"reason":"<one sentence>","internship_friendly":<bool>,"experience_required":"<phrase or not mentioned>"}},...]}}"""


# ── Providers ─────────────────────────────────────────────────────────────────

def _score_via_groq(job, model):
    from groq import Groq
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise ValueError("GROQ_API_KEY not set")
    client = Groq(api_key=api_key)
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": _build_single_prompt(job)}],
        temperature=0.1,
        response_format={"type": "json_object"},
    )
    return json.loads(response.choices[0].message.content)


def _score_batch_via_groq(jobs, model):
    """Batch-score up to 5 jobs in one Groq call. Returns list of result dicts."""
    from groq import Groq
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise ValueError("GROQ_API_KEY not set")
    client = Groq(api_key=api_key)
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": _build_batch_prompt_groq(jobs)}],
        temperature=0.1,
        response_format={"type": "json_object"},
    )
    data = json.loads(response.choices[0].message.content)
    # Groq returns {"results": [...]} — extract the array
    results = data.get("results", data) if isinstance(data, dict) else data
    if not isinstance(results, list) or len(results) != len(jobs):
        raise ValueError(f"Expected {len(jobs)} results, got {len(results) if isinstance(results, list) else type(results)}")
    return results


def _score_via_gemini(job, model):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY not set")
    url = GEMINI_API_URL.format(model=model) + f"?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": _build_single_prompt(job)}]}],
        "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
    }
    resp = requests.post(url, json=payload, timeout=30)
    resp.raise_for_status()
    return json.loads(resp.json()["candidates"][0]["content"]["parts"][0]["text"])


def _score_batch_via_gemini(jobs, model):
    """Batch-score up to 5 jobs in one Gemini call. Returns list of result dicts."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY not set")
    url = GEMINI_API_URL.format(model=model) + f"?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": _build_batch_prompt(jobs)}]}],
        "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
    }
    resp = requests.post(url, json=payload, timeout=60)
    resp.raise_for_status()
    results = json.loads(resp.json()["candidates"][0]["content"]["parts"][0]["text"])
    if not isinstance(results, list) or len(results) != len(jobs):
        raise ValueError(f"Expected {len(jobs)} results, got {len(results) if isinstance(results, list) else type(results)}")
    return results


def _score_via_ollama(job, model):
    resp = requests.post(
        "http://localhost:11434/api/generate",
        json={
            "model": model,
            "prompt": "/no_think\n" + _build_single_prompt(job),
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.1, "think": False},
        },
        timeout=120,
    )
    resp.raise_for_status()
    raw = re.sub(r"<think>.*?</think>", "", resp.json()["response"], flags=re.DOTALL).strip()
    return json.loads(raw)


def _score_via_ollama_safe(job, model):
    try:
        return _score_via_ollama(job, model)
    except requests.exceptions.ReadTimeout:
        short = dict(job)
        short["description"] = str(job.get("description", "") or "")[:400]
        try:
            return _score_via_ollama(short, model)
        except Exception:
            pass
    except Exception:
        pass
    return {"score": 0, "reason": "Scoring failed", "internship_friendly": False,
            "experience_required": "unknown"}


def _is_daily_quota(e):
    """True when the provider's DAILY limit is exhausted — permanent for this run."""
    err = str(e).lower()
    return any(kw in err for kw in [
        "resource_exhausted", "quota exceeded", "daily limit",
        "per day", "exceeded your current quota",
        # Groq daily exhaustion message contains the word "daily"
        "daily",
    ]) or ("429" in err and "resource_exhausted" in err)


def _is_transient_rate_limit(e):
    """True for per-minute / per-second rate limits — safe to retry after a short wait."""
    err = str(e).lower()
    return "429" in err and not _is_daily_quota(e)


def _is_provider_down(e):
    """True for server errors / network failures — retry once then switch."""
    err = str(e).lower()
    return any(kw in err for kw in ["502", "503", "504", "connection", "timeout"])


# ── Public API ────────────────────────────────────────────────────────────────

def score_job(job):
    """
    Score a single job. Used by description_filler.
    Fallback chain: Groq (14,400 req/day) → Gemini (1,500 req/day) → Ollama (local).
    """
    global _groq_failed, _gemini_failed

    if _resume_text is None:
        load_resume()

    config       = _get_config()
    use_ollama   = config["matching"].get("use_ollama", False)
    groq_model   = config["matching"].get("groq_model",   "llama-3.1-8b-instant")
    gemini_model = config["matching"].get("gemini_model", "gemini-2.5-flash")
    ollama_model = config["matching"].get("ollama_model", "qwen3:8b")

    if use_ollama:
        return _score_via_ollama_safe(job, ollama_model)

    if not _groq_failed:
        for attempt in range(3):
            try:
                return _score_via_groq(job, groq_model)
            except Exception as e:
                if _is_transient_rate_limit(e) and attempt < 2:
                    import time; time.sleep(15 * (attempt + 1))
                elif _is_daily_quota(e):
                    _groq_failed = True
                    break
                else:
                    _groq_failed = True
                    break

    if not _gemini_failed:
        for attempt in range(3):
            try:
                return _score_via_gemini(job, gemini_model)
            except Exception as e:
                if _is_transient_rate_limit(e) and attempt < 2:
                    import time; time.sleep(15 * (attempt + 1))
                elif _is_daily_quota(e):
                    _gemini_failed = True
                    break
                else:
                    _gemini_failed = True
                    break

    return _score_via_ollama_safe(job, ollama_model)


def score_jobs_batch(jobs, batch_size=5):
    """
    Score a list of jobs using batch API calls (5 jobs per request).
    Returns a list of result dicts in the same order as input.

    Fallback chain per batch:
      1. Groq  — 14,400 req/day free, resets daily  (primary)
      2. Gemini — 1,500 req/day free, resets daily  (secondary)
      3. Ollama — local, unlimited                  (last resort)
    """
    global _groq_failed, _gemini_failed

    if _resume_text is None:
        load_resume()

    config       = _get_config()
    use_ollama   = config["matching"].get("use_ollama", False)
    groq_model   = config["matching"].get("groq_model",   "llama-3.1-8b-instant")
    gemini_model = config["matching"].get("gemini_model", "gemini-2.5-flash")
    ollama_model = config["matching"].get("ollama_model", "qwen3:8b")

    all_results = []

    for i in range(0, len(jobs), batch_size):
        batch         = jobs[i:i + batch_size]
        batch_num     = i // batch_size + 1
        total_batches = (len(jobs) + batch_size - 1) // batch_size
        print(f"    Batch {batch_num}/{total_batches} ({len(batch)} jobs)...", end=" ", flush=True)

        if use_ollama:
            for job in batch:
                all_results.append(_score_via_ollama_safe(job, ollama_model))
            print("Ollama")
            continue

        scored = False

        # ── Primary: Groq (14,400 req/day) ──────────────────────────────────
        if not _groq_failed:
            for attempt in range(3):  # up to 2 retries on transient limits
                try:
                    results = _score_batch_via_groq(batch, groq_model)
                    all_results.extend(results)
                    print(f"Groq OK" + (f" (retry {attempt})" if attempt else ""))
                    scored = True
                    break
                except Exception as e:
                    if _is_transient_rate_limit(e) and attempt < 2:
                        wait = 15 * (attempt + 1)   # 15s then 30s
                        print(f"\n      Groq rate limit — waiting {wait}s...", end=" ", flush=True)
                        import time; time.sleep(wait)
                    elif _is_daily_quota(e):
                        print(f"Groq daily quota hit — switching to Gemini...")
                        _groq_failed = True
                        break
                    elif _is_provider_down(e) and attempt < 1:
                        print(f"\n      Groq down — retrying...", end=" ", flush=True)
                        import time; time.sleep(5)
                    else:
                        print(f"Groq error ({str(e)[:50]}) — switching to Gemini...")
                        _groq_failed = True
                        break

        # ── Secondary: Gemini (1,500 req/day) ───────────────────────────────
        if not scored and not _gemini_failed:
            for attempt in range(3):
                try:
                    results = _score_batch_via_gemini(batch, gemini_model)
                    all_results.extend(results)
                    print(f"Gemini OK" + (f" (retry {attempt})" if attempt else ""))
                    scored = True
                    break
                except Exception as e:
                    if _is_transient_rate_limit(e) and attempt < 2:
                        wait = 15 * (attempt + 1)
                        print(f"\n      Gemini rate limit — waiting {wait}s...", end=" ", flush=True)
                        import time; time.sleep(wait)
                    elif _is_daily_quota(e):
                        print(f"Gemini daily quota hit — falling back to Ollama...")
                        _gemini_failed = True
                        break
                    elif _is_provider_down(e) and attempt < 1:
                        print(f"\n      Gemini down — retrying...", end=" ", flush=True)
                        import time; time.sleep(5)
                    else:
                        print(f"Gemini error ({str(e)[:50]}) — falling back to Ollama...")
                        _gemini_failed = True
                        break

        # ── Last resort: Ollama (local, unlimited) ───────────────────────────
        if not scored:
            for job in batch:
                all_results.append(_score_via_ollama_safe(job, ollama_model))
            print("Ollama")

    return all_results
