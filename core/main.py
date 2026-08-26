r"""
main.py — daily job-hunt pipeline.

Run via Windows Task Scheduler at 08:00 daily:
    python core\main.py

Email inbox (replies, confirmations, interview invites) is handled separately
via the Microsoft Graph API, every 15 minutes:
    python tracking\email_processor.py   (Task Scheduler entry, see README)
"""

import json
import logging
import sys
from pathlib import Path

from core.config import load_config, setup_logging
from scraper import scraper
from matcher import matcher
from dedup import dedup
from tailor import tailor
from applier import applier
from tracking import excel_updater

log = logging.getLogger(__name__)


def _confirm_job(job: dict) -> bool:
    """Print job details and wait for y/n. Returns True to apply, False to skip."""
    sep = "─" * 58
    print(f"\n{sep}")
    print(f"  Company  : {job.get('company', '?')}")
    print(f"  Role     : {job.get('title', '?')}")
    print(f"  Location : {job.get('location', '?')}")
    print(f"  Source   : {job.get('source', '?')}")
    print(f"  Score    : {job.get('match_score', '?')}%  |  "
          f"Chance: {job.get('interview_chance', '?')}  |  "
          f"German: {job.get('german_level_required', job.get('german_level', 'none'))}")
    if job.get("match_summary"):
        print(f"  Summary  : {job['match_summary']}")
    print(f"  URL      : {job.get('url', '?')}")
    print(sep)
    while True:
        try:
            answer = input("  Apply? [y/n] → ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return False
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("  Please type y or n.")


def run_pipeline(cfg: dict) -> None:
    log.info("=" * 60)
    log.info("Job Hunt Agent — daily pipeline starting")
    log.info("=" * 60)

    # Mark any 'Applied ✓' rows older than 5 days as unconfirmed
    excel_updater.sweep_unconfirmed(cfg)

    # 1. Scrape
    log.info("Step 1/5 — Scraping jobs")
    jobs = scraper.run(cfg)
    if not jobs:
        log.info("No jobs scraped — exiting")
        return

    # 2. AI match + filter
    log.info("Step 2/5 — Matching with AI (%d candidates)", len(jobs))
    matched = matcher.run(jobs, cfg)
    if not matched:
        log.info("No jobs passed matching filters — exiting")
        return

    # 3. Dedup (skip already applied/pending)
    log.info("Step 3/5 — Deduplication (%d matched)", len(matched))
    new_jobs = dedup.filter(matched, cfg)
    if not new_jobs:
        log.info("All matched jobs already applied — exiting")
        return

    # 4. Tailor docs + apply (capped at max_applications_per_day)
    daily_cap: int = cfg.get("max_applications_per_day", 10)
    to_apply = new_jobs[:daily_cap]
    log.info("Step 4/5 — Tailoring and applying to %d jobs (cap=%d)",
             len(to_apply), daily_cap)

    for job in to_apply:
        log.info("Processing: %s @ %s", job["title"], job["company"])

        try:
            archive_path: Path = tailor.create_docs(job, cfg)
        except Exception as exc:
            log.error("Tailoring failed for %s @ %s: %s",
                      job["title"], job["company"], exc)
            continue

        if cfg.get("confirm_before_apply", False):
            if not _confirm_job(job):
                log.info("Skipped by user: %s @ %s", job["title"], job["company"])
                continue

        success = applier.apply(job, archive_path, cfg)

        if success:
            # Upgrade any pending entry → applied; add full entry if none exists
            dedup.log_application(job, archive_path, cfg)
            excel_updater.add_or_update(job, archive_path, cfg=cfg)
            log.info("Applied: %s @ %s", job["title"], job["company"])
        else:
            log.warning("Application failed: %s @ %s",
                        job["title"], job["company"])

    # Email inbox (replies, confirmations, interview invites) is handled by
    # tracking/email_processor.py via the Microsoft Graph API, scheduled
    # separately — see README. The legacy IMAP-based email_watcher.py has
    # been removed: Microsoft disabled basic auth for this account, so it
    # could no longer authenticate.
    log.info("Pipeline complete.")


def main() -> None:
    cfg = load_config()
    setup_logging(cfg)
    try:
        run_pipeline(cfg)
    except KeyboardInterrupt:
        log.info("Interrupted by user.")
        sys.exit(0)
    except Exception as exc:
        log.exception("Unhandled error: %s", exc)
        sys.exit(1)


_UPLOADS_DIR = Path(__file__).parent.parent / "uploads"  # User files: resumes, logs, JSONs


def _uploads_resume_path() -> "Path | None":
    for ext in (".pdf", ".docx"):
        p = _UPLOADS_DIR / f"resume_en{ext}"
        if p.exists():
            return p
    return None


def _override_resume(cfg: dict) -> dict:
    p = _uploads_resume_path()
    if p:
        cfg = dict(cfg)
        cfg["paths"] = dict(cfg["paths"])
        cfg["paths"]["resume_en"] = p
    return cfg


def run_scrape_only(cfg: dict | None = None) -> list[dict]:
    """Scrape jobs, insert into DB and save JSON snapshot. Returns job list."""
    if cfg is None:
        cfg = load_config()
    setup_logging(cfg)
    cfg = _override_resume(cfg)

    from dedup import db as _db
    _db.init_db()

    log.info("=== Scrape-only stage starting ===")
    jobs = scraper.run(cfg)
    _UPLOADS_DIR.mkdir(exist_ok=True)

    inserted = _db.insert_scraped_jobs(jobs)
    log.info("Inserted %d new scraped jobs into DB", inserted)

    out = _UPLOADS_DIR / "scraped_jobs.json"
    out.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Scrape-only complete: %d jobs", len(jobs))
    return jobs


def run_match_only(cfg: dict | None = None, job_ids: list | None = None) -> list[dict]:
    """Score unmatched scraped jobs, store results in DB, return matched list.

    job_ids: optional list of scraped_job IDs to restrict matching to.
    """
    if cfg is None:
        cfg = load_config()
    setup_logging(cfg)

    from dedup import db as _db
    _db.init_db()

    db_jobs = _db.get_unmatched_scraped_jobs()
    if job_ids:
        _id_set = set(job_ids)
        db_jobs = [j for j in db_jobs if j["id"] in _id_set]
        log.info("Job-limit filter applied: %d / %d jobs will be matched", len(db_jobs), len(_id_set))
    if db_jobs:
        jobs = [
            {
                "title":       j["title"],
                "company":     j["company"],
                "location":    j["location"],
                "url":         j["url"],
                "description": j["description"],
                "source":      j["source"],
                "_db_id":      j["id"],
            }
            for j in db_jobs
        ]
    else:
        src = _UPLOADS_DIR / "scraped_jobs.json"
        if not src.exists():
            raise FileNotFoundError("No scraped jobs found — run scrape first")
        jobs = json.loads(src.read_text(encoding="utf-8"))

    cfg = _override_resume(cfg)
    log.info("=== Match-only stage starting: %d jobs ===", len(jobs))
    matched = matcher.run(jobs, cfg)
    # DB inserts happen inside matcher.run() after each scored job for live progress

    out = _UPLOADS_DIR / "matched_jobs.json"
    out.write_text(json.dumps(matched, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Match-only complete: %d jobs matched", len(matched))
    return matched


def run_apply_only(cfg: dict | None = None,
                   job_urls: "list[str] | None" = None) -> list[dict]:
    """Apply using uploaded resume/cover letter; skip tailoring. Logs to DB."""
    if cfg is None:
        cfg = load_config()
    setup_logging(cfg)

    from dedup import db as _db
    _db.init_db()

    try:
        jobs = _db.get_matched_jobs_for_apply(cfg)
    except Exception:
        src = _UPLOADS_DIR / "matched_jobs.json"
        if not src.exists():
            raise FileNotFoundError("No matched jobs — run match first")
        jobs = json.loads(src.read_text(encoding="utf-8"))

    if job_urls is not None:
        url_set = set(job_urls)
        jobs = [j for j in jobs if j.get("url") in url_set]

    archive = _UPLOADS_DIR   # applier looks for resume_en.pdf/docx here

    log.info("=== Apply-only stage starting: %d jobs ===", len(jobs))
    results: list[dict] = []
    for job in jobs:
        log.info("Applying: %s @ %s", job.get("title"), job.get("company"))
        try:
            success = applier.apply(job, archive, cfg)
        except Exception as exc:
            log.error("Apply error %s @ %s: %s", job.get("title"), job.get("company"), exc)
            success = False
        if success:
            _db.log_application(job, str(archive), "Applied")
        results.append({
            "url":     job.get("url", ""),
            "title":   job.get("title", ""),
            "company": job.get("company", ""),
            "success": success,
        })
    log.info("Apply-only complete: %d/%d succeeded",
             sum(r["success"] for r in results), len(results))
    return results


if __name__ == "__main__":
    main()
