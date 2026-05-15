"""
matcher.py — score all scraped jobs against the candidate's resume using Claude.

Receives up to 100 jobs from scraper.run() (the last 24 hours across all
role/location combos). Scores every job and returns only those that pass
the min_match_score threshold (default 70) and the German-level filter.
"""

import json
import logging
import os
import re

import anthropic
import pdfplumber
from dotenv import load_dotenv

from config import load_config

load_dotenv()
log = logging.getLogger(__name__)

_MODEL = "claude-sonnet-4-6"

_SYSTEM_PROMPT = """\
You are an expert recruiter and career coach.
Given a job description and a candidate's resume, evaluate the fit.
Return ONLY valid JSON — no prose, no markdown fences.
"""

_USER_TEMPLATE = """\
## Job Description
{description}

## Candidate Resume
{resume}

Respond with exactly this JSON structure:
{{
  "match_score": <integer 0-100>,
  "interview_chance": "<high|medium|low>",
  "german_level_required": "<A1|A2|B1|B2|C1|C2|native|none>",
  "skip_reason": <null or short string>,
  "match_summary": "<2-3 sentence explanation>"
}}

Rules:
- match_score: how well the candidate's skills/experience match the role requirements
- german_level_required: the minimum German level stated or implied in the JD; use "none" if not mentioned
- skip_reason: non-null only if there is a clear hard blocker (e.g. requires citizenship, 10+ years mandatory)
"""


def _extract_resume_text(resume_path) -> str:
    text_parts: list[str] = []
    with pdfplumber.open(resume_path) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                text_parts.append(t)
    return "\n".join(text_parts)


def _score_job(client: anthropic.Anthropic, job: dict, resume_text: str) -> dict | None:
    prompt = _USER_TEMPLATE.format(
        description=job["description"][:6000],  # cap to stay inside context
        resume=resume_text[:4000],
    )
    try:
        response = client.messages.create(
            model=_MODEL,
            max_tokens=512,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text
        log.info("Raw Claude response for %s: %s", job.get("title"), raw)

        raw = raw.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)
            raw = raw.strip()

        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            log.warning("JSON parse failed for %s @ %s — full response: %s",
                        job.get("title"), job.get("company"), raw)
            return None
    except (IndexError, anthropic.APIError) as exc:
        log.warning("Scoring failed for %s @ %s: %s", job.get("title"), job.get("company"), exc)
        return None


_match_stats: dict = {"fresh": 0, "cache_hits": 0, "resume_changed": False, "invalidated": 0}


def get_match_stats() -> dict:
    return dict(_match_stats)


def run(jobs: list[dict], cfg: dict | None = None) -> list[dict]:
    global _match_stats
    if cfg is None:
        cfg = load_config()

    resume_path = cfg["paths"]["resume_en"]
    if not resume_path.exists():
        raise FileNotFoundError(f"Resume not found: {resume_path}")

    resume_text = _extract_resume_text(resume_path)
    log.info("Loaded resume: %d chars", len(resume_text))

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    skip_levels: set[str] = {lvl.lower() for lvl in cfg.get("skip_german_levels", [])}

    import db as _db
    _db.init_db()

    # ── Scoring cache ─────────────────────────────────────────────────────────
    current_hash = _db.get_resume_hash(cfg)
    _match_stats = {"fresh": 0, "cache_hits": 0, "resume_changed": False, "invalidated": 0}

    if current_hash and _db.has_scores_with_different_hash(current_hash, cfg):
        invalidated = _db.invalidate_scores_for_changed_resume(cfg)
        log.info("Resume changed — invalidated %d cached scores within window; re-scoring", invalidated)
        _match_stats["resume_changed"] = True
        _match_stats["invalidated"] = invalidated
        scored_urls: set[str] = set()
    else:
        scored_urls = _db.get_scored_urls_for_hash(current_hash) if current_hash else set()

    # Pre-fetch cached scores for all jobs whose URLs are already in the cache
    batch_urls = {j.get("url") for j in jobs if j.get("url")}
    cached_scores = _db.get_cached_scores_by_url(scored_urls & batch_urls) if scored_urls else {}
    if cached_scores:
        log.info("Score cache: %d jobs can skip Claude scoring", len(cached_scores))

    matched: list[dict] = []
    for job in jobs:
        url       = job.get("url", "")
        db_id     = job.get("_db_id")
        from_cache = False

        if url and url in cached_scores:
            # ── Cache hit ──────────────────────────────────────────────────
            c = cached_scores[url]
            job["match_score"]           = c.get("match_score") or 0
            job["interview_chance"]      = c.get("interview_chance") or "low"
            job["german_level_required"] = c.get("german_level_required") or "none"
            job["skip_reason"]           = c.get("skip_reason")
            job["match_summary"]         = job.get("match_summary") or ""
            _match_stats["cache_hits"] += 1
            from_cache = True
            log.info("Cache hit %d%% (%s) — %s @ %s",
                     job["match_score"], job["interview_chance"],
                     job.get("title"), job.get("company"))
        else:
            # ── Fresh Claude scoring ───────────────────────────────────────
            scores = _score_job(client, job, resume_text)
            if scores is None:
                continue

            job["match_score"]           = scores.get("match_score", 0)
            job["interview_chance"]      = scores.get("interview_chance", "low")
            job["german_level_required"] = scores.get("german_level_required", "none")
            job["skip_reason"]           = scores.get("skip_reason")
            job["match_summary"]         = scores.get("match_summary", "")
            _match_stats["fresh"] += 1

        # ── German level check (applied to both fresh and cached scores) ───
        german = job["german_level_required"].lower()
        if not job.get("skip_reason") and any(s in german for s in skip_levels):
            job["skip_reason"] = f"German level {job['german_level_required']} required"
            job["match_score"] = 0
            log.info("German filter: %s required — %s @ %s",
                     job["german_level_required"], job.get("title"), job.get("company"))

        # Persist score to seen_jobs (fresh only; cache hits are already stored)
        if not from_cache and url:
            try:
                _db.update_seen_job_score(url, {
                    "match_score":           job["match_score"],
                    "interview_chance":      job["interview_chance"],
                    "skip_reason":           job["skip_reason"],
                    "german_level_required": job["german_level_required"],
                }, resume_hash=current_hash)
            except Exception as exc:
                log.warning("Cache score update failed for %s: %s", url, exc)

        # Insert into matched_jobs for live progress counter
        if db_id:
            try:
                _db.insert_matched_job(db_id, {
                    "match_score":           job["match_score"],
                    "interview_chance":      job["interview_chance"],
                    "german_level_required": job["german_level_required"],
                    "skip_reason":           job["skip_reason"],
                    "match_summary":         job["match_summary"],
                })
            except Exception as exc:
                log.warning("DB insert failed for %s: %s", job.get("title"), exc)

        # ── Skip filter ────────────────────────────────────────────────────
        if job.get("skip_reason"):
            log.info("Skip (%s): %s @ %s",
                     job["skip_reason"], job.get("title"), job.get("company"))
            continue

        log.info("Match %d%% (%s) — %s @ %s",
                 job["match_score"], job["interview_chance"], job.get("title"), job.get("company"))
        matched.append(job)

    log.info(
        "Matcher done: %d matched / %d jobs (%d fresh, %d from cache)",
        len(matched), len(jobs), _match_stats["fresh"], _match_stats["cache_hits"],
    )
    return matched


if __name__ == "__main__":
    import json as _json
    from config import setup_logging
    cfg = load_config()
    setup_logging(cfg)
    # Quick test with a synthetic job
    sample_jobs = [
        {
            "title": "Data Engineer",
            "company": "TestCorp",
            "location": "Berlin",
            "url": "https://example.com/job/1",
            "description": "We need a Data Engineer with Python, SQL, and Airflow experience. German not required.",
            "source": "LinkedIn",
        }
    ]
    results = run(sample_jobs, cfg)
    print(_json.dumps(results, indent=2, ensure_ascii=False))
