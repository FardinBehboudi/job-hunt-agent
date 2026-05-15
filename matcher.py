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

_MODEL = "claude-haiku-4-5-20251001"

_SYSTEM_PROMPT = """\
You are a senior technical recruiter specialising in software engineering roles.
Your job is to fairly evaluate how well a candidate fits a job description.

GOLDEN RULE: When in doubt, favour the candidate. A missed good match is worse
than sending one extra application. Only hard blockers should kill a match.

Return ONLY valid JSON — no prose, no markdown, no code fences.
"""

_USER_TEMPLATE = """\
## Job Description
{description}

## Candidate Resume
{resume}

## Scoring Instructions

Respond with exactly this JSON:
{{
  "match_score": <integer 0-100>,
  "interview_chance": "<high|medium|low>",
  "german_level_required": "<A1|A2|B1|B2|C1|C2|native|none>",
  "skip_reason": null,
  "match_summary": "<2-3 sentence explanation of the score>",
  "detailed_reasoning": [
    {{"requirement": "<key requirement>", "met": true/false, "note": "<brief note>"}}
  ]
}}

── SCORING SCALE ──
90-100: Near-perfect match. Almost all requirements met, strong domain fit.
75-89:  Strong match. Core skills align well, minor gaps only.
60-74:  Good match. Most requirements met, some gaps but nothing blocking.
50-59:  Partial match. Core skills present but notable gaps exist.
30-49:  Weak match. Significant skill or domain gaps.
0-29:   Poor match. Fundamental mismatch in stack or requirements.

── TECHNOLOGY STACK RULES ──
Candidate's primary stack: Java / Spring Boot / Microservices / Backend / JVM.

Rule 1 — Language category clauses (MOST IMPORTANT):
When a job lists languages followed by "or other [category] languages" or
"or similar languages", judge by CATEGORY not by the listed examples:
  "C++, Python, or other object-oriented languages" → Java QUALIFIES (Java is OOP)
  "Python, Ruby, or other scripting languages" → Java does NOT qualify
  "JavaScript, TypeScript, or similar languages" → Java does NOT qualify
  "Any modern programming language" → Java QUALIFIES
  "Python or equivalent" → Java QUALIFIES if backend/data role, NOT if ML/scripting

Rule 2 — Primary vs secondary language:
Only penalise stack mismatch if the diverging language is the PRIMARY requirement,
not just mentioned as a bonus or secondary skill:
  "Java required, Python is a plus" → score normally, no penalty
  "Python required, Java is a plus" → apply penalty
  "We use Python and Java" → score normally if Java is sufficient for the role

Rule 3 — Stack mismatch caps (only when diverging language is confirmed PRIMARY):
  Job PRIMARY is Python (confirmed in title AND description): cap at 45
  Job PRIMARY is Go, Rust, Ruby, PHP: cap at 40
  Job PRIMARY is React/Angular/Vue (pure frontend): cap at 35
  Job PRIMARY is C++ (systems/embedded): cap at 40
  Mixed stack where Java is sufficient: NO cap, score normally

Rule 4 — Backend/fullstack roles:
  "Backend Engineer", "Software Engineer", "Platform Engineer",
  "Fullstack Engineer" → score normally even if Python/Go mentioned,
  unless the job description CONFIRMS Java is not acceptable.

── EXPERIENCE LEVEL RULES ──
  "5+ years required", candidate has 5 years → MEETS requirement (do not penalise)
  "7+ years required", candidate has 5 years → minor penalty only (-10 max)
  "10+ years required", candidate has 5 years → notable gap (-20 max)
  Graduate/entry roles when candidate is senior → skip_reason = "Overqualified"
  Never hard-block on experience unless difference is 5+ years

── GERMAN LANGUAGE RULES ──
  Only set german_level_required if the job EXPLICITLY states a German level.
  "German is a plus" or "nice to have" → set to "none" (not a hard requirement)
  "Fluent German required" or "C1 required" → set correctly
  Job is entirely in German language → set to "B2" minimum as implied requirement

── DOMAIN AND SOFT SKILLS ──
  Domain mismatch (e.g. fintech vs healthtech) → at most -10 penalty
  Missing soft skills → at most -5 penalty
  Missing "nice to have" items → at most -5 penalty total
  Only hard technical requirements should cause significant score drops

── SKIP REASON ──
  Set skip_reason only for genuine hard blockers:
  - Requires specific citizenship or security clearance candidate cannot meet
  - Role is completely outside candidate's field (sales, design, medical)
  - Requires 10+ years when candidate has 5 or fewer
  - Primary stack is completely different AND job title confirms it

  Do NOT set skip_reason for:
  - Missing "nice to have" skills
  - Minor experience gaps (less than 3 years)
  - Domain differences
  - Language mentioned as secondary/bonus
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
            max_tokens=1024,
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
    log.info("Scoring model: %s", _MODEL)
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

    # ── Applied company+title dedup ──────────────────────────────────────────
    applied_combos: set[tuple[str, str]] = set()
    try:
        with _db._conn() as conn:
            rows = conn.execute("SELECT company, role FROM applications").fetchall()
            applied_combos = {
                (r[0].lower().strip(), r[1].lower().strip())
                for r in rows if r[0] and r[1]
            }
        if applied_combos:
            log.info("Applied company+title dedup: %d combos loaded", len(applied_combos))
    except Exception as exc:
        log.warning("Could not load applied combos for dedup: %s", exc)

    matched: list[dict] = []
    for job in jobs:
        # ── Skip if already applied (company+title match) ────────────────────
        job_combo = (
            (job.get("company") or "").lower().strip(),
            (job.get("title") or "").lower().strip(),
        )
        if job_combo[0] and job_combo[1] and job_combo in applied_combos:
            log.info("Skip (already applied): %s @ %s",
                     job.get("title"), job.get("company"))
            continue

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
