"""
email_processor.py — fetch, classify, link, and stage recruiter emails.

Run via Windows Task Scheduler every 15 minutes:
    python tracking\\email_processor.py
"""

import sys
from pathlib import Path
# Ensure project root is on sys.path when run as a standalone script
sys.path.insert(0, str(Path(__file__).parent.parent))

import json
import logging
import os

import anthropic
from dotenv import load_dotenv

from core.config import load_config
from dedup import db
from tracking import email_executor
from tracking import ms_graph

load_dotenv()
log = logging.getLogger(__name__)

_MODEL = "claude-sonnet-4-6"
MONITORED_FOLDERS = ["Inbox", "Focus", "Other", "Junk Email"]
_EVENT_CATEGORIES = {"Interview", "Code Challenge", "Next Step"}
_AUTO_MOVE_CATEGORIES = {"Rejected", "In Review"}

# ── Claude prompts ─────────────────────────────────────────────────────────────

_CLASSIFY_SYSTEM = """\
You classify job-application-related emails for a job seeker.
Return ONLY one JSON object — no prose, no markdown fences.
"""

_CLASSIFY_PROMPT = """\
## Email Subject
{subject}

## Email Body Preview
{body}

Classify this email and return:
{{
  "category": "<Rejected|In Review|Next Step|Code Challenge|Interview|Offer|Uncertain>",
  "confidence": <integer 0-100>,
  "reason": "<one sentence>"
}}

Category definitions:
- Rejected: rejection language (unfortunately, not selected, not moving forward, not the right fit)
- In Review: application received or currently under review
- Next Step: moving forward but no specific interview/task specified yet
- Code Challenge: coding test, assignment, or task to complete with a deadline
- Interview: explicit invitation to schedule or attend an interview, call, or meeting
- Offer: job offer or compensation discussion
- Uncertain: does not clearly fit any category above
"""

_EVENT_SYSTEM = """\
You extract structured event data from job-application emails.
Return ONLY one JSON object — no prose, no markdown fences.
"""

_EVENT_PROMPT = """\
## Email Subject
{subject}

## Email Body Preview
{body}

Extract event details and return:
{{
  "event_type": "<interview|task|challenge|meeting|offer>",
  "title": "<short descriptive title>",
  "description": "<relevant details in one sentence>",
  "event_date": "<YYYY-MM-DD or null>",
  "event_time": "<HH:MM or null>",
  "timezone": "<timezone string or null>",
  "priority": "<high|medium|low>"
}}
"""

# ── Classification ─────────────────────────────────────────────────────────────

def _classify(client: anthropic.Anthropic, subject: str,
              body_preview: str) -> dict:
    try:
        resp = client.messages.create(
            model=_MODEL,
            max_tokens=256,
            system=_CLASSIFY_SYSTEM,
            messages=[{"role": "user", "content": _CLASSIFY_PROMPT.format(
                subject=subject, body=body_preview,
            )}],
        )
        return json.loads(resp.content[0].text.strip())
    except Exception as exc:
        log.warning("Classification error: %s", exc)
        return {"category": "Uncertain", "confidence": 0,
                "reason": "classification error"}


def _extract_event(client: anthropic.Anthropic, subject: str,
                   body_preview: str) -> dict:
    try:
        resp = client.messages.create(
            model=_MODEL,
            max_tokens=256,
            system=_EVENT_SYSTEM,
            messages=[{"role": "user", "content": _EVENT_PROMPT.format(
                subject=subject, body=body_preview,
            )}],
        )
        return json.loads(resp.content[0].text.strip())
    except Exception as exc:
        log.warning("Event extraction error: %s", exc)
        return {"event_type": "interview", "title": subject,
                "description": "", "event_date": None,
                "event_time": None, "timezone": None, "priority": "medium"}


# ── Job linking ────────────────────────────────────────────────────────────────

def _link_to_application(sender: str, subject: str,
                          body_preview: str,
                          db_module=None) -> dict:
    """Match email to applications row. Returns {matched_app_id, match_confidence, match_type}."""
    _db = db_module or db
    domain = ""
    if "@" in sender:
        domain = sender.split("@")[-1].lower().split(".")[0]

    companies = _db.get_application_companies()

    if domain and len(domain) > 2:
        exact = [c for c in companies
                 if domain in (c.get("company") or "").lower()]
        if len(exact) == 1:
            return {"matched_app_id": exact[0]["id"],
                    "match_confidence": 95, "match_type": "exact"}
        if len(exact) > 1:
            return {"matched_app_id": None,
                    "match_confidence": 80, "match_type": "ambiguous"}

    text = f"{subject} {body_preview}".lower()
    for c in companies:
        name = (c.get("company") or "").lower().strip()
        if len(name) >= 3 and name in text:
            return {"matched_app_id": c["id"],
                    "match_confidence": 70, "match_type": "fuzzy"}

    return {"matched_app_id": None, "match_confidence": 0, "match_type": "unmatched"}


# ── Auto-execute ───────────────────────────────────────────────────────────────

def _maybe_auto_execute(staging_id: int, category: str,
                         app_id: "int | None", received_date: str,
                         subject: str, threshold: int = 90) -> None:
    record = db.get_staged_email(staging_id)
    if record and category in _AUTO_MOVE_CATEGORIES and record["confidence_score"] >= threshold:
        _auto_execute(staging_id, category, app_id, received_date, subject)


def _auto_execute(staging_id: int, category: str,
                  app_id: "int | None", received_date: str,
                  subject: str) -> None:
    try:
        email_executor.move(staging_id)
        log.info("Auto-executed staging_id=%d (%s)", staging_id, category)
    except Exception as exc:
        log.error("Auto-execute failed staging_id=%d: %s", staging_id, exc)


# ── Main pipeline ──────────────────────────────────────────────────────────────

def run(cfg: "dict | None" = None) -> None:
    if cfg is None:
        cfg = load_config()
    db.init_db()

    threshold = int(db.get_setting("email_auto_move_threshold", "90"))
    auto_move_enabled = db.get_setting("email_auto_move_enabled", "1") == "1"

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    seen_ids = db.get_staged_message_ids()

    messages: list[dict] = []
    for folder in MONITORED_FOLDERS:
        try:
            fetched = ms_graph.get_messages(folder)
            new = [m for m in fetched if m["email_message_id"] not in seen_ids]
            messages.extend(new)
            for m in new:
                seen_ids.add(m["email_message_id"])
        except Exception as exc:
            log.warning("Failed to fetch from folder '%s': %s", folder, exc)

    log.info("Fetched %d new emails", len(messages))

    for msg in messages:
        subject = msg["subject"]
        preview = msg["body_preview"]

        clf = _classify(client, subject, preview)
        category = clf.get("category", "Uncertain")
        confidence = int(clf.get("confidence", 0))
        reason = clf.get("reason", "")

        link = _link_to_application(msg["sender"], subject, preview)

        staging_id = db.stage_email({
            **msg,
            "predicted_folder": category,
            "confidence_score": confidence,
            "classification_reason": reason,
            **link,
        })

        if category in _EVENT_CATEGORIES and link["matched_app_id"]:
            event = _extract_event(client, subject, preview)
            db.insert_upcoming_event({
                **event,
                "app_id": link["matched_app_id"],
                "source_email_id": staging_id,
            })

        if auto_move_enabled and category in _AUTO_MOVE_CATEGORIES:
            _maybe_auto_execute(staging_id, category, link["matched_app_id"],
                                msg["received_date"], subject, threshold)


if __name__ == "__main__":
    from core.config import setup_logging
    _cfg = load_config()
    setup_logging(_cfg)
    run(_cfg)
