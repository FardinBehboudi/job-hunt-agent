"""
dedup.py — prevent applying to the same job twice.

Log lives at config.paths.log_file (applied_jobs_log.json).
Each entry: {url, company, title, source, location, match_score, timestamp, archive_path, status}

status values:
  "pending" — URL saved immediately before the submit click (crash-safety)
  "applied" — confirmed submitted
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from config import load_config

log = logging.getLogger(__name__)


def _load_log(log_path: Path) -> list[dict]:
    if not log_path.exists():
        return []
    with open(log_path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            log.warning("applied_jobs_log.json is corrupt — starting fresh")
            return []


def _save_log(log_path: Path, entries: list[dict]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)


def _key(company: str, title: str) -> str:
    return f"{company.lower().strip()}|{title.lower().strip()}"


def filter(jobs: list[dict], cfg: dict | None = None) -> list[dict]:
    """Return only jobs not already in the applied log (pending or applied)."""
    if cfg is None:
        cfg = load_config()

    log_path: Path = cfg["paths"]["log_file"]
    entries = _load_log(log_path)

    applied_urls: set[str] = {e["url"] for e in entries if e.get("url")}
    applied_keys: set[str] = {_key(e.get("company", ""), e.get("title", "")) for e in entries}

    new_jobs: list[dict] = []
    for job in jobs:
        url = job.get("url", "").strip()
        key = _key(job.get("company", ""), job.get("title", ""))

        if url in applied_urls:
            log.debug("Duplicate URL: %s", url)
            continue
        if key in applied_keys:
            log.debug("Duplicate company+title: %s", key)
            continue

        new_jobs.append(job)

    log.info("Dedup: %d new (of %d total, %d already logged)",
             len(new_jobs), len(jobs), len(entries))
    return new_jobs


def pre_log(job: dict, archive_path: str | Path, cfg: dict | None = None) -> None:
    """
    Write a 'pending' entry immediately before the submit click.
    Guards against losing track of a job if the process crashes mid-submission.
    If an entry for this URL already exists, this is a no-op.
    """
    if cfg is None:
        cfg = load_config()

    log_path: Path = cfg["paths"]["log_file"]
    entries = _load_log(log_path)

    url = job.get("url", "")
    if any(e.get("url") == url for e in entries):
        return  # already logged

    entries.append({
        "url":          url,
        "company":      job.get("company", ""),
        "title":        job.get("title", ""),
        "source":       job.get("source", ""),
        "location":     job.get("location", ""),
        "match_score":  job.get("match_score"),
        "timestamp":    datetime.now(timezone.utc).isoformat(),
        "archive_path": str(archive_path),
        "status":       "pending",
    })
    _save_log(log_path, entries)


def log_application(job: dict, archive_path: str | Path, cfg: dict | None = None) -> None:
    """
    Mark a job as fully applied. If a 'pending' entry exists for this URL,
    upgrade it in-place rather than adding a duplicate.
    """
    if cfg is None:
        cfg = load_config()

    log_path: Path = cfg["paths"]["log_file"]
    entries = _load_log(log_path)

    url = job.get("url", "")
    full_entry = {
        "url":          url,
        "company":      job.get("company", ""),
        "title":        job.get("title", ""),
        "source":       job.get("source", ""),
        "location":     job.get("location", ""),
        "match_score":  job.get("match_score"),
        "timestamp":    datetime.now(timezone.utc).isoformat(),
        "archive_path": str(archive_path),
        "status":       "applied",
    }

    for entry in entries:
        if entry.get("url") == url:
            entry.update(full_entry)
            _save_log(log_path, entries)
            log.info("Updated log entry to 'applied': %s @ %s", job.get("title"), job.get("company"))
            return

    entries.append(full_entry)
    _save_log(log_path, entries)
    log.info("Logged application: %s @ %s", job.get("title"), job.get("company"))


if __name__ == "__main__":
    from config import setup_logging
    cfg = load_config()
    setup_logging(cfg)
    sample = [
        {"url": "https://example.com/job/1", "company": "TestCorp", "title": "Data Engineer",
         "source": "LinkedIn", "location": "Berlin"},
    ]
    new = filter(sample, cfg)
    print(f"New jobs: {len(new)}")
    if new:
        pre_log(new[0], "/tmp/archive/TestCorp_DataEngineer_2026-05-14", cfg)
        print(f"After pre_log, new: {len(filter(sample, cfg))}")  # 0 — URL now blocked
        log_application(new[0], "/tmp/archive/TestCorp_DataEngineer_2026-05-14", cfg)
        print(f"After log_application, new: {len(filter(sample, cfg))}")  # still 0
