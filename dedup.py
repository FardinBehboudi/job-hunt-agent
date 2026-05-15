"""
dedup.py — prevent applying to the same job twice.

Primary store: SQLite via db.py (applications table).
Fallback: applied_jobs_log.json for environments without a DB.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from config import load_config

log = logging.getLogger(__name__)


# ── DB-backed implementation ──────────────────────────────────────────────────

def filter(jobs: list[dict], cfg: dict | None = None) -> list[dict]:
    """Return only jobs not already in the applied log (DB-first, JSON fallback)."""
    try:
        import db as _db
        _db.init_db()
        applied_urls = _db.get_applied_urls()
        new_jobs = [j for j in jobs
                    if (j.get("url") or "").strip() not in applied_urls]
        log.info("Dedup (DB): %d new of %d (%d already applied)",
                 len(new_jobs), len(jobs), len(jobs) - len(new_jobs))
        return new_jobs
    except Exception as exc:
        log.warning("DB dedup failed (%s) — falling back to JSON", exc)
        return _filter_json(jobs, cfg)


def pre_log(job: dict, archive_path: "str | Path", cfg: dict | None = None) -> None:
    """Write a 'pending' entry immediately before the submit click (crash guard)."""
    try:
        import db as _db
        _db.init_db()
        _db.log_application(job, str(archive_path), status="pending")
        return
    except Exception as exc:
        log.warning("DB pre_log failed (%s) — falling back to JSON", exc)
    _pre_log_json(job, archive_path, cfg)


def log_application(job: dict, archive_path: "str | Path", cfg: dict | None = None) -> None:
    """Mark a job as fully applied."""
    try:
        import db as _db
        _db.init_db()
        _db.log_application(job, str(archive_path), status="Applied")
        log.info("Logged application (DB): %s @ %s", job.get("title"), job.get("company"))
        return
    except Exception as exc:
        log.warning("DB log_application failed (%s) — falling back to JSON", exc)
    _log_application_json(job, archive_path, cfg)


# ── JSON fallback (legacy) ────────────────────────────────────────────────────

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


def _filter_json(jobs: list[dict], cfg: dict | None = None) -> list[dict]:
    if cfg is None:
        cfg = load_config()
    log_path: Path = cfg["paths"]["log_file"]
    entries = _load_log(log_path)
    applied_urls = {e["url"] for e in entries if e.get("url")}
    applied_keys = {_key(e.get("company", ""), e.get("title", "")) for e in entries}
    new_jobs = []
    for job in jobs:
        url = job.get("url", "").strip()
        key = _key(job.get("company", ""), job.get("title", ""))
        if url in applied_urls or key in applied_keys:
            continue
        new_jobs.append(job)
    log.info("Dedup (JSON): %d new of %d", len(new_jobs), len(jobs))
    return new_jobs


def _pre_log_json(job: dict, archive_path: "str | Path",
                  cfg: dict | None = None) -> None:
    if cfg is None:
        cfg = load_config()
    log_path: Path = cfg["paths"]["log_file"]
    entries = _load_log(log_path)
    url = job.get("url", "")
    if any(e.get("url") == url for e in entries):
        return
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


def _log_application_json(job: dict, archive_path: "str | Path",
                          cfg: dict | None = None) -> None:
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
            return
    entries.append(full_entry)
    _save_log(log_path, entries)
    log.info("Logged application (JSON): %s @ %s", job.get("title"), job.get("company"))
