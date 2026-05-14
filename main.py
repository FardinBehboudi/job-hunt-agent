"""
main.py — daily job-hunt pipeline.

Run via Windows Task Scheduler at 08:00 daily:
    python main.py

Email inbox is polled separately every 2 hours:
    python email_watcher.py   (separate Task Scheduler entry)
"""

import logging
import sys
from pathlib import Path

from config import load_config, setup_logging
import scraper
import matcher
import dedup
import tailor
import applier
import excel_updater
import email_watcher

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

    # 5. Email inbox check (confirmation emails + interview invites)
    log.info("Step 5/5 — Checking inbox")
    try:
        email_watcher.run(cfg)
    except Exception as exc:
        log.error("Email watcher error: %s", exc)

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


if __name__ == "__main__":
    main()
