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
        log.info("Raw Claude response for %s: %s", job.get("title"), raw[:200])

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


def run(jobs: list[dict], cfg: dict | None = None) -> list[dict]:
    if cfg is None:
        cfg = load_config()

    resume_path = cfg["paths"]["resume_en"]
    if not resume_path.exists():
        raise FileNotFoundError(f"Resume not found: {resume_path}")

    resume_text = _extract_resume_text(resume_path)
    log.info("Loaded resume: %d chars", len(resume_text))

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    min_score: int = cfg.get("min_match_score", 70)
    skip_levels: set[str] = {lvl.lower() for lvl in cfg.get("skip_german_levels", [])}

    matched: list[dict] = []
    for job in jobs:
        scores = _score_job(client, job, resume_text)
        if scores is None:
            continue

        job["match_score"]          = scores.get("match_score", 0)
        job["interview_chance"]     = scores.get("interview_chance", "low")
        job["german_level_required"] = scores.get("german_level_required", "none")
        job["skip_reason"]          = scores.get("skip_reason")
        job["match_summary"]        = scores.get("match_summary", "")

        if job["match_score"] < min_score:
            log.debug("Skip (score %d < %d): %s @ %s",
                      job["match_score"], min_score, job["title"], job["company"])
            continue

        german = job["german_level_required"].lower()
        if german in skip_levels:
            log.info("Skip (German %s required): %s @ %s",
                     job["german_level_required"], job["title"], job["company"])
            continue

        if job.get("skip_reason"):
            log.info("Skip (%s): %s @ %s",
                     job["skip_reason"], job["title"], job["company"])
            continue

        log.info("Match %d%% (%s) — %s @ %s",
                 job["match_score"], job["interview_chance"], job["title"], job["company"])
        matched.append(job)

    log.info("Matcher done: %d / %d jobs passed filters", len(matched), len(jobs))
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
