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

_MAX_TOTAL_JOBS = 100  # hard cap on total jobs returned per daily run

_scrape_progress: dict = {
    "total_combinations": 0,
    "completed_combinations": 0,
    "current_search": "",
    "jobs_found_so_far": 0,
}


def get_scrape_progress() -> dict:
    return dict(_scrape_progress)

# Titles containing these compounds are kept regardless of other keywords.
# Rationale: "Java/Python Backend Engineer" lists Python as secondary — still relevant.
_SAFE_TITLE_COMPOUNDS = [
    "backend engineer", "backend developer",
    "software engineer", "software developer",
    "full stack", "fullstack", "full-stack",
    "platform engineer",
]

_SKIP_TITLE_KEYWORDS = [
    # Wrong primary-stack roles (Java candidate is unlikely to match these)
    "python developer", "python engineer",
    "go developer", "golang",
    "ruby developer", "ruby on rails",
    "php developer", "php engineer",
    "frontend developer", "frontend engineer",
    "react developer", "angular developer", "vue developer",
    "ios developer", "android developer",
    "mobile developer", "mobile engineer",
    # Non-tech roles
    "marketing", "sales", "accountant", "mechanic", "nurse", "driver",
    "designer", "hr ", " hr", "recruiter", "lawyer", "finance", "intern",
    "co-founder", "assistant", "coordinator", "consultant", "analyst",
    "manager", "director", "product owner", "scrum master",
]

_TECH_TITLE_KEYWORDS = [
    "engineer", "developer", "architect", "backend", "software", "java",
    "python", "devops", "cloud", "data", "fullstack", "full-stack", "api",
    "platform", "infrastructure", "site reliability", "sre", "ml ",
    "machine learning", "kotlin", "scala", "typescript",
]


def is_title_relevant(job_title: str, search_role: str) -> bool:
    """Fast keyword check — no API calls. Returns False for clearly off-topic titles."""
    title = job_title.lower()
    # Safe compounds bypass the skip list (e.g. "Python Backend Engineer" → keep)
    if any(kw in title for kw in _SAFE_TITLE_COMPOUNDS):
        return True
    if any(kw in title for kw in _SKIP_TITLE_KEYWORDS):
        return False
    if any(kw in title for kw in _TECH_TITLE_KEYWORDS):
        return True
    # Fall back to checking overlap with the role's own words
    role_words = set(search_role.lower().split())
    return bool(role_words & set(title.split()))

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
    _cfg = cfg or {}
    posted_limit = _cfg.get("posted_limit", "24h")
    count = int(_cfg.get("scrape_pool_size", 25))
    run_input = {
        "jobTitles":       [role],
        "locations":       [location],
        "maxItems":        count,
        "postedLimit":     posted_limit,
        "employmentType":  ["full-time"],
        "experienceLevel": ["entry", "associate", "mid-senior", "director", "executive"],
        "sortBy":          "relevance",
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
            "posted_date": _safe_str(item.get("postedDate") or item.get("posted_date")),
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

    smart_scrape = cfg.get("smart_scrape", True)

    # Load the persistent cache before clearing the run tables
    import db as _db_cache
    _db_cache.init_db()
    cached_urls = _db_cache.get_cached_job_urls()
    applied_urls, applied_combos = _get_applied_data()
    log.info("Cache: %d known URLs, %d already applied", len(cached_urls), len(applied_urls))

    # Clear previous run's temp data before fetching fresh results
    _clear_run_tables()

    client = _client()
    all_jobs: list[dict] = []

    # ── Stage 1: Broad scrape ────────────────────────────────────────────────
    combos = list(product(roles, cfg["locations"]))
    _scrape_progress.update({
        "total_combinations":     len(combos),
        "completed_combinations": 0,
        "current_search":         "",
        "jobs_found_so_far":      0,
    })

    for role, location in combos:
        _scrape_progress["current_search"] = f"{role} @ {location}"
        try:
            batch = _scrape_linkedin(client, role, location, cfg)
            log.debug("Batch %s @ %s — %d jobs", role, location, len(batch))
            all_jobs.extend(batch)
        except Exception as exc:
            log.warning("LinkedIn failed for %s @ %s: %s", role, location, exc)
        finally:
            _scrape_progress["completed_combinations"] += 1
            _scrape_progress["jobs_found_so_far"] = len(all_jobs)

    log.info("Stage 1 — scraped: %d jobs across all role/location combos", len(all_jobs))
    jobs = _deduplicate(all_jobs)
    jobs = [j for j in jobs if j.get("description")]

    # Upsert all fresh unique jobs into the persistent seen_jobs cache
    _db_cache.upsert_seen_jobs(jobs)

    # Backfill resume_hash for any seen_jobs rows missing it so the matcher
    # cache lookup never misses a previously-scored job due to a NULL hash
    try:
        current_resume_hash = _db_cache.get_resume_hash(cfg)
        if current_resume_hash:
            import sqlite3 as _sq
            _dbp = Path(__file__).parent / "uploads" / "jobhunt.db"
            _dbc = _sq.connect(str(_dbp), timeout=10)
            _dbc.execute("PRAGMA journal_mode=WAL")
            _dbc.execute(
                "UPDATE seen_jobs SET resume_hash = ? WHERE resume_hash IS NULL AND dismissed = 0",
                (current_resume_hash,),
            )
            _dbc.commit()
            _dbc.close()
    except Exception as _rhe:
        log.warning("resume_hash backfill failed: %s", _rhe)

    # ── Stage 2: Title relevance filter (free, no API) ───────────────────────
    scraped_total = len(jobs)
    if smart_scrape:
        role_set = roles
        relevant = []
        for job in jobs:
            title = job.get("title", "")
            if any(is_title_relevant(title, r) for r in role_set):
                relevant.append(job)
            else:
                log.debug("Title filter drop: %s @ %s", title, job.get("company", ""))
        saved_calls = scraped_total - len(relevant)
        log.info(
            "Smart scrape: %d scraped → %d passed title filter → "
            "ready for Claude matching (saved %d API calls)",
            scraped_total, len(relevant), saved_calls,
        )
        jobs = relevant
    else:
        jobs = jobs[:_MAX_TOTAL_JOBS]

    # ── Applied dedup (URL + company+title) ─────────────────────────────────
    before = len(jobs)
    filtered: list[dict] = []
    for j in jobs:
        url     = j.get("url") or ""
        company = (j.get("company") or "").lower().strip()
        title   = (j.get("title") or "").lower().strip()
        combo   = (company, title)
        if url and url in applied_urls:
            log.info("Skip (already in dashboard): %s @ %s", j.get("title"), j.get("company"))
            continue
        if company and title and combo in applied_combos:
            log.info("Skip (already in dashboard): %s @ %s", j.get("title"), j.get("company"))
            continue
        filtered.append(j)
    skipped = before - len(filtered)
    if skipped:
        log.info("Skipped %d jobs already in applications table", skipped)
    jobs = filtered

    # ── Dismissed dedup (URL + company+title) ───────────────────────────────────
    try:
        with _db_cache._conn() as _dc:
            _drows = _dc.execute(
                "SELECT url, company, title FROM seen_jobs WHERE dismissed=1"
            ).fetchall()
        _dismissed_urls   = {r[0].strip() for r in _drows if r[0] and r[0].strip()}
        _dismissed_combos = {
            (r[1].lower().strip(), r[2].lower().strip())
            for r in _drows if r[1] and r[2]
        }
    except Exception:
        _dismissed_urls, _dismissed_combos = set(), set()
    if _dismissed_urls or _dismissed_combos:
        _before_d = len(jobs)
        _filtered_d = []
        for j in jobs:
            _url = j.get("url") or ""
            _co  = (j.get("company") or "").lower().strip()
            _ti  = (j.get("title")   or "").lower().strip()
            if _url and _url in _dismissed_urls:
                log.info("Skip (dismissed): %s @ %s", j.get("title"), j.get("company"))
                continue
            if _co and _ti and (_co, _ti) in _dismissed_combos:
                log.info("Skip (dismissed): %s @ %s", j.get("title"), j.get("company"))
                continue
            _filtered_d.append(j)
        _skipped_d = _before_d - len(_filtered_d)
        if _skipped_d:
            log.info("Skipped %d dismissed jobs", _skipped_d)
        jobs = _filtered_d

    # Tag fresh jobs: "new" if not seen before, "cached" if already in the cache
    for j in jobs:
        j["cache_status"] = "cached" if j.get("url") in cached_urls else "new"

    # ── Stage 3: Add cached jobs within the configured time window ─────────────
    fresh_urls    = {j.get("url") for j in jobs if j.get("url")}
    all_cached    = _db_cache.get_relevant_cached_jobs(cfg)
    cached_extra = []
    for j in all_cached:
        url     = j.get("url") or ""
        company = (j.get("company") or "").lower().strip()
        title   = (j.get("title") or "").lower().strip()
        if url and url in fresh_urls:
            continue
        if url and url in applied_urls:
            continue
        if company and title and (company, title) in applied_combos:
            continue
        cached_extra.append(j)
    for j in cached_extra:
        j["cache_status"] = "cached"

    # Count jobs sitting in the cache but outside the time window (for logging)
    win_stats = _db_cache.get_cache_window_stats(cfg)
    outside_window = win_stats.get("outside_window", 0)

    jobs = jobs + cached_extra
    jobs = jobs[:_MAX_TOTAL_JOBS]

    new_count    = sum(1 for j in jobs if j.get("cache_status") == "new")
    cached_count = sum(1 for j in jobs if j.get("cache_status") == "cached")
    limit_label  = cfg.get("posted_limit", "24h")
    log.info(
        "Cache: %d jobs loaded from cache (posted within last %s) | "
        "%d new jobs scraped from LinkedIn | "
        "%d jobs in cache but outside time window (ignored) | "
        "%d total to process",
        cached_count, limit_label, new_count, outside_window, len(jobs),
    )

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


def _get_applied_data() -> tuple[set[str], set[tuple[str, str]]]:
    """Return (applied_urls, applied_combos) from the applications table."""
    try:
        con = _db_connect()
        try:
            rows = con.execute(
                "SELECT job_url, company, role FROM applications"
                " WHERE job_url IS NOT NULL OR company IS NOT NULL"
            ).fetchall()
            applied_urls   = {r[0].strip() for r in rows if r[0] and r[0].strip()}
            applied_combos = {
                (r[1].lower().strip(), r[2].lower().strip())
                for r in rows if r[1] and r[2]
            }
            log.info(
                "Applied dedup: %d URLs, %d company+title combos",
                len(applied_urls), len(applied_combos),
            )
            return applied_urls, applied_combos
        finally:
            con.close()
    except Exception as exc:
        log.warning("Could not fetch applied data from DB: %s", exc)
        return set(), set()


def _save_to_db(jobs: list[dict]) -> None:
    """Insert fresh scraped jobs into SQLite (tables already cleared by _clear_run_tables)."""
    _DB_PATH.parent.mkdir(exist_ok=True)
    con = _db_connect()
    try:
        con.executemany(
            "INSERT OR IGNORE INTO scraped_jobs"
            " (title, company, location, url, description, source, posted_date, cache_status)"
            " VALUES (?,?,?,?,?,?,?,?)",
            [
                (
                    j.get("title", ""),
                    _extract_company(j.get("company")),
                    j.get("location", ""),
                    j.get("url", ""),
                    j.get("description", ""),
                    j.get("source", "LinkedIn"),
                    j.get("posted_date", ""),
                    j.get("cache_status", "new"),
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
