"""
scraper.py — fetch jobs from LinkedIn via the harvestapi/linkedin-job-search Apify actor.

At startup, extract_roles_from_resume() reads the candidate's PDF resume and
asks Claude to derive up to 6 job-search terms. Those terms drive every Apify
call, replacing the manual roles list in config.yaml.

Each call is filtered to jobs posted in the last 24 hours and the total is
capped at _MAX_TOTAL_JOBS across all role/location combinations.

Actor ID is read from env var APIFY_LINKEDIN_ACTOR so it can be swapped
without touching code.
"""

import json
import logging
import os
from itertools import product

import anthropic
import pdfplumber
from apify_client import ApifyClient
from dotenv import load_dotenv

from config import load_config

load_dotenv()
log = logging.getLogger(__name__)

_LINKEDIN_ACTOR  = os.getenv("APIFY_LINKEDIN_ACTOR", "harvestapi/linkedin-job-search")
_MODEL           = "claude-sonnet-4-6"

_RESULTS_PER_RUN = 25   # per actor call
_MAX_TOTAL_JOBS  = 100  # hard cap on total jobs returned per daily run

_extracted_roles_cache: list[str] | None = None

_ROLE_SYSTEM = """\
You analyse a candidate's resume and extract job-search terms.
Return ONLY a JSON array of strings — no prose, no markdown fences.
"""

_ROLE_PROMPT = """\
## Resume
{resume}

Extract up to 6 job titles or core skill areas that best represent this candidate for job searching.
Rules:
- Only include titles/skills that are clearly evidenced in the resume.
- Do NOT invent roles absent from the resume.
- Use standard industry terms a recruiter would type into a job board.
- Prefer specific titles over generic ones (e.g. "Data Engineer" over "Engineer").

Return a JSON array, e.g.: ["Data Engineer", "Python Developer", "Backend Engineer"]
"""


def extract_roles_from_resume(cfg: dict) -> list[str]:
    """Read the resume PDF and ask Claude for up to 6 job-search terms."""
    global _extracted_roles_cache
    if _extracted_roles_cache is not None:
        return _extracted_roles_cache

    resume_path = cfg["paths"]["resume_en"]
    if not resume_path.exists():
        raise FileNotFoundError(f"Resume not found: {resume_path}")

    text_parts: list[str] = []
    with pdfplumber.open(resume_path) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                text_parts.append(t)
    resume_text = "\n".join(text_parts)

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY is not set in .env")

    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=_MODEL,
        max_tokens=256,
        system=_ROLE_SYSTEM,
        messages=[{"role": "user", "content": _ROLE_PROMPT.format(resume=resume_text[:4000])}],
    )
    raw = resp.content[0].text.strip()
    try:
        roles = json.loads(raw)
        if not isinstance(roles, list):
            raise ValueError("Expected JSON array")
    except Exception:
        log.warning("Role extraction parse error — raw: %s", raw[:200])
        roles = ["Data Engineer", "Python Developer"]

    roles = [r.strip() for r in roles if isinstance(r, str) and r.strip()][:6]
    log.info("Extracted roles from resume: %s", roles)
    _extracted_roles_cache = roles
    return roles


def _client() -> ApifyClient:
    token = os.getenv("APIFY_API_TOKEN")
    if not token:
        raise EnvironmentError("APIFY_API_TOKEN is not set in .env")
    return ApifyClient(token)


def _scrape_linkedin(client: ApifyClient, role: str, location: str) -> list[dict]:
    log.info("LinkedIn scrape: %s @ %s", role, location)
    run = client.actor(_LINKEDIN_ACTOR).call(run_input={
        "searchQueries": [role],
        "locations":     [location],
        "postedLimit":   "24h",
        "maxItems":      _RESULTS_PER_RUN,
    })
    items: list[dict] = []
    for item in client.dataset(run["defaultDatasetId"]).iterate_items():
        items.append({
            "title":       item.get("title")       or item.get("jobTitle", ""),
            "company":     item.get("company")     or item.get("companyName", ""),
            "location":    item.get("location",    location),
            "url":         item.get("url")         or item.get("jobUrl", ""),
            "description": item.get("description") or item.get("jobDescription", ""),
            "source":      "LinkedIn",
        })
    log.info("  → %d LinkedIn results", len(items))
    return items


def _deduplicate(jobs: list[dict]) -> list[dict]:
    seen_urls: set[str] = set()
    unique: list[dict] = []
    for job in jobs:
        url = job.get("url", "").strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        unique.append(job)
    log.info("Deduplication: %d → %d unique jobs", len(jobs), len(unique))
    return unique


def run(cfg: dict | None = None) -> list[dict]:
    if cfg is None:
        cfg = load_config()

    client = _client()
    roles  = extract_roles_from_resume(cfg)
    all_jobs: list[dict] = []

    for role, location in product(roles, cfg["locations"]):
        try:
            all_jobs.extend(_scrape_linkedin(client, role, location))
        except Exception as exc:
            log.warning("LinkedIn failed for %s @ %s: %s", role, location, exc)

    jobs = _deduplicate(all_jobs)
    jobs = [j for j in jobs if j.get("url") and j.get("description")]
    jobs = jobs[:_MAX_TOTAL_JOBS]
    log.info("Scraper done: %d jobs (capped at %d) ready for matching", len(jobs), _MAX_TOTAL_JOBS)
    return jobs


if __name__ == "__main__":
    import json as _json
    from config import setup_logging
    cfg = load_config()
    setup_logging(cfg)
    results = run(cfg)
    print(_json.dumps(results[:3], indent=2, ensure_ascii=False))
    print(f"\nTotal: {len(results)} jobs")
