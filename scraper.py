"""
scraper.py — fetch jobs from LinkedIn via the harvestapi/linkedin-job-search Apify actor.

run() reads cfg["roles"] (set by the user in the dashboard UI and saved to
config.yaml) to determine what to search for.

extract_roles_from_resume() is a separate helper used only by the /api/roles
dashboard endpoint to suggest an initial role list from the resume PDF — it
is never called during the scraping pipeline.

Each call is filtered to jobs posted in the last 24 hours and the total is
capped at _MAX_TOTAL_JOBS across all role/location combinations.

Actor ID is read from env var APIFY_LINKEDIN_ACTOR so it can be swapped
without touching code.
"""

import json
import logging
import os
import sqlite3
from itertools import product
from pathlib import Path

import anthropic
import pdfplumber
from apify_client import ApifyClient
from dotenv import load_dotenv

from config import load_config

load_dotenv()
log = logging.getLogger(__name__)


def _safe_str(val) -> str:
    """Convert any API field value to a plain string, handling nested dicts."""
    if val is None:
        return ""
    if isinstance(val, dict):
        return str(val.get("name") or val.get("value") or val.get("text") or "")
    return str(val)


def _extract_company(raw) -> str:
    if isinstance(raw, dict):
        return raw.get("name") or raw.get("companyName") or ""
    return str(raw) if raw else ""

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


def _scrape_linkedin(client: ApifyClient, role: str, location: str,
                     cfg: dict | None = None) -> list[dict]:
    log.info("LinkedIn scrape: %s @ %s", role, location)
    posted_limit = (cfg or {}).get("posted_limit", "24h")
    run_input = {
        "jobTitles":   [role],
        "locations":   [location],
        "maxItems":    min(_RESULTS_PER_RUN, 25),   # hard cap — cost guard
        "postedLimit": posted_limit,
    }
    log.info("Apify run_input: %s", run_input)
    run = client.actor(_LINKEDIN_ACTOR).call(run_input=run_input)
    raw_items: list[dict] = list(client.dataset(run["defaultDatasetId"]).iterate_items())
    log.info("  → %d raw items from Apify dataset", len(raw_items))
    if raw_items:
        log.info("  Raw item keys: %s", list(raw_items[0].keys()))
        log.info("  Sample linkedinUrls (first 3): %s",
                 [i.get("linkedinUrl", "NO_linkedinUrl") for i in raw_items[:3]])

    items: list[dict] = []
    for item in raw_items:
        url         = (_safe_str(item.get("linkedinUrl")) or _safe_str(item.get("easyApplyUrl"))).strip()
        description = (_safe_str(item.get("descriptionText")) or _safe_str(item.get("descriptionHtml"))).strip()
        loc         = _safe_str(item.get("location")).strip() or location

        items.append({
            "title":       _safe_str(item.get("title")).strip(),
            "company":     _extract_company(item.get("company")).strip(),
            "location":    loc,
            "url":         url,
            "description": description,
            "posted_date": _safe_str(item.get("postedDate")),
            "source":      "LinkedIn",
        })

    log.info("  → %d mapped items; jobs with no URL: %d",
             len(items), sum(1 for j in items if not j.get("url")))
    log.info("  Sample mapped URLs (first 3): %s",
             [j.get("url", "NO_URL") for j in items[:3]])
    if raw_items:
        log.info("  LinkedIn query field from result: %s", raw_items[0].get("query"))
    return items


def _deduplicate(jobs: list[dict]) -> list[dict]:
    seen_keys: set[str] = set()
    unique: list[dict] = []
    for job in jobs:
        url     = (job.get("url") or "").strip()
        title   = (job.get("title") or "").strip().lower()
        company = _extract_company(job.get("company")).strip().lower()
        # Prefer URL as key; fall back to company|title for URL-less jobs
        key = url if url else (f"{company}|{title}" if (company or title) else "")
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)
        unique.append(job)
    log.info("Deduplication: %d → %d unique jobs", len(jobs), len(unique))
    return unique


def run(cfg: dict | None = None) -> list[dict]:
    if cfg is None:
        cfg = load_config()

    roles = [r for r in (cfg.get("roles") or []) if isinstance(r, str) and r.strip()]
    if not roles:
        raise ValueError("cfg['roles'] is empty — select at least one role in the dashboard before scraping")

    # Clear previous run's temp data before fetching fresh results
    _clear_run_tables()

    client = _client()
    all_jobs: list[dict] = []

    for role, location in product(roles, cfg["locations"]):
        try:
            batch = _scrape_linkedin(client, role, location, cfg)
            log.debug(
                "Batch %s @ %s — %d jobs, first 3 URLs: %s",
                role, location, len(batch),
                [j.get("url", "") for j in batch[:3]],
            )
            all_jobs.extend(batch)
        except Exception as exc:
            log.warning("LinkedIn failed for %s @ %s: %s", role, location, exc)

    log.info("Total before dedup: %d jobs across all role/location combos", len(all_jobs))
    jobs = _deduplicate(all_jobs)
    jobs = [j for j in jobs if j.get("description")]   # must have text; URL is optional
    jobs = jobs[:_MAX_TOTAL_JOBS]

    # Filter out jobs we already applied to (applications table is the source of truth).
    # Only skip a job when its URL is non-empty AND that URL is in the applied set.
    # An empty applied_urls set (all rows had NULL job_url) means nothing is skipped.
    applied_urls = _get_applied_urls()
    before = len(jobs)
    jobs = [j for j in jobs if not (j.get("url") and j["url"] in applied_urls)]
    skipped = before - len(jobs)
    log.info("Scraper done: %d jobs (%d skipped — already applied), capped at %d",
             len(jobs), skipped, _MAX_TOTAL_JOBS)

    _save_to_db(jobs)
    return jobs


_DB_PATH = Path(__file__).parent / "uploads" / "jobhunt.db"


def _db_connect() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(str(_DB_PATH), timeout=15)
    con.execute("PRAGMA journal_mode=WAL")
    return con


def _clear_run_tables() -> None:
    """Delete all rows from the two temp tables before a new scrape run."""
    try:
        con = _db_connect()
        try:
            con.execute("DELETE FROM matched_jobs")
            con.execute("DELETE FROM scraped_jobs")
            con.commit()
            log.info("Cleared scraped_jobs and matched_jobs for fresh run")
        finally:
            con.close()
    except Exception as exc:
        log.warning("Could not clear run tables: %s", exc)


def _get_applied_urls() -> set[str]:
    """Return non-empty job URLs from the applications table (already-applied guard)."""
    try:
        con = _db_connect()
        try:
            rows = con.execute(
                "SELECT job_url FROM applications"
                " WHERE job_url IS NOT NULL AND job_url != ''"
            ).fetchall()
            urls = {r[0].strip() for r in rows if r[0] and r[0].strip()}
            log.info("Applied-URL dedup set: %d entries", len(urls))
            return urls
        finally:
            con.close()
    except Exception as exc:
        log.warning("Could not fetch applied URLs from DB: %s", exc)
        return set()


def _save_to_db(jobs: list[dict]) -> None:
    """Insert fresh scraped jobs into SQLite (tables already cleared by _clear_run_tables)."""
    _DB_PATH.parent.mkdir(exist_ok=True)
    con = _db_connect()
    try:
        con.executemany(
            "INSERT OR IGNORE INTO scraped_jobs"
            " (title, company, location, url, description, source)"
            " VALUES (?,?,?,?,?,?)",
            [
                (
                    j.get("title", ""),
                    _extract_company(j.get("company")),
                    j.get("location", ""),
                    j.get("url", ""),
                    j.get("description", ""),
                    j.get("source", "LinkedIn"),
                )
                for j in jobs
            ],
        )
        con.commit()
        log.info("Saved %d scraped jobs to DB", len(jobs))
    except Exception as exc:
        log.error("Failed to save scraped jobs to DB: %s", exc)
        con.rollback()
    finally:
        con.close()


if __name__ == "__main__":
    import json as _json
    from config import setup_logging
    cfg = load_config()
    setup_logging(cfg)
    results = run(cfg)
    print(_json.dumps(results[:3], indent=2, ensure_ascii=False))
    print(f"\nTotal: {len(results)} jobs")
