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
import re
from datetime import datetime

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

_APPLICATION_SYSTEM = """\
You extract the company and job title from a job-application confirmation email.
Return ONLY one JSON object — no prose, no markdown fences.
"""

_APPLICATION_PROMPT = """\
## Email Subject
{subject}

## Email Body Preview
{body}

Extract:
{{
  "company": "<company name, or empty string if not confidently identifiable>",
  "title": "<job title applied for, or empty string if not confidently identifiable>"
}}
"""

# Minimum classification confidence before a new application row is created
# automatically from an "In Review" email that didn't match anything on file.
_NEW_APPLICATION_CONFIDENCE_FLOOR = 70

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
        raw = resp.content[0].text.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)
            raw = raw.strip()
        return json.loads(raw)
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
        raw = resp.content[0].text.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)
            raw = raw.strip()
        return json.loads(raw)
    except Exception as exc:
        log.warning("Event extraction error: %s", exc)
        return {"event_type": "interview", "title": subject,
                "description": "", "event_date": None,
                "event_time": None, "timezone": None, "priority": "medium"}


def _extract_application(client: anthropic.Anthropic, subject: str,
                         body_preview: str) -> dict:
    try:
        resp = client.messages.create(
            model=_MODEL,
            max_tokens=128,
            system=_APPLICATION_SYSTEM,
            messages=[{"role": "user", "content": _APPLICATION_PROMPT.format(
                subject=subject, body=body_preview,
            )}],
        )
        raw = resp.content[0].text.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)
            raw = raw.strip()
        return json.loads(raw)
    except Exception as exc:
        log.warning("Application extraction error: %s", exc)
        return {"company": "", "title": ""}


# ── Job linking ────────────────────────────────────────────────────────────────

def _link_to_application(sender: str, subject: str,
                          body_preview: str,
                          db_module=None) -> dict:
    """Match email to applications row. Returns {matched_app_id, match_confidence, match_type}."""
    import re as _re

    _db = db_module or db
    domain = ""
    if "@" in sender:
        domain = sender.split("@")[-1].lower().split(".")[0]

    companies = _db.get_application_companies()

    def _word_match(term: str, text: str) -> bool:
        """True if term appears as a whole word/token in text.
        Prevents 'sap' from matching inside 'osapiens', etc."""
        if not term:
            return False
        return bool(_re.search(r'(?<!\w)' + _re.escape(term) + r'(?!\w)', text))

    # Try domain match; also try stripping common prefixes (getpliant→pliant, mycompany→company)
    _DOMAIN_PREFIXES = ("get", "my", "use", "try", "hello", "join", "team",
                        "meet", "talk", "go", "the", "be")
    domain_variants = [domain]
    for pfx in _DOMAIN_PREFIXES:
        if domain.startswith(pfx) and len(domain) > len(pfx) + 2:
            domain_variants.append(domain[len(pfx):])
            break

    if domain and len(domain) > 2:
        for dv in domain_variants:
            exact = [c for c in companies
                     if _word_match(dv, (c.get("company") or "").lower())]
            if len(exact) == 1:
                return {"matched_app_id": exact[0]["id"],
                        "match_confidence": 95, "match_type": "exact"}
            if len(exact) > 1:
                return {"matched_app_id": None,
                        "match_confidence": 80, "match_type": "ambiguous"}

    subject_lower = subject.lower()
    body_lower    = body_preview.lower()
    # Strip URLs from body before matching — prevents "google" in
    # "calendar.google.com" from incorrectly matching the Google application.
    body_no_urls = _re.sub(r'https?://\S+|www\.\S+|\S+\.\w{2,4}/\S*', ' ', body_lower)

    # ── Pass 1: subject-only (most reliable signal) ──────────────────────────
    # Collect ALL companies that match in the subject so we can detect ambiguity.
    subject_hits = [c for c in companies
                    if len((c.get("company") or "").strip()) >= 3
                    and _word_match((c.get("company") or "").lower().strip(), subject_lower)]
    if len(subject_hits) == 1:
        return {"matched_app_id": subject_hits[0]["id"],
                "match_confidence": 78, "match_type": "fuzzy"}
    if len(subject_hits) > 1:
        return {"matched_app_id": None,
                "match_confidence": 78, "match_type": "ambiguous"}

    # ── Pass 2: body text (URL-stripped), first match wins ───────────────────
    for c in companies:
        name = (c.get("company") or "").lower().strip()
        if len(name) >= 3 and _word_match(name, body_no_urls):
            return {"matched_app_id": c["id"],
                    "match_confidence": 65, "match_type": "fuzzy"}

    return {"matched_app_id": None, "match_confidence": 0, "match_type": "unmatched"}


def _maybe_create_application(client: anthropic.Anthropic, msg: dict, subject: str,
                              body_preview: str, confidence: int) -> "int | None":
    """For an 'In Review' confirmation email that didn't match anything on file,
    extract the company/title and add it to the applications list. Returns the
    new application id, or None if extraction wasn't confident enough."""
    if confidence < _NEW_APPLICATION_CONFIDENCE_FLOOR:
        return None

    extracted = _extract_application(client, subject, body_preview)
    company = (extracted.get("company") or "").strip()
    if not company:
        return None

    app_id = db.log_application({
        "company": company,
        "title": (extracted.get("title") or "").strip(),
        "source": "email",
        "date_applied": (msg.get("received_date") or "")[:10],
    }, status="In Review")
    log.info("Created application from email confirmation: %s — %s",
             company, extracted.get("title") or "?")
    return app_id


# Signals that an email is even plausibly about a job application, used only as a
# fallback for emails _link_to_application couldn't tie to a specific application
# (e.g. a recruiter emailing from a personal address, or an ATS domain not yet in
# the applications table). Anything matched to an application already bypasses this.
_JOB_KEYWORDS = (
    "job", "career", "recruit", "talent", "hiring", "application", "applying",
    "candidacy", "candidate", "interview", "vacature",
    "bewerb", "beworben", "stelle", "vorstellungsgespräch", "eingangsbest",
)


def _is_job_related(sender: str, subject: str) -> bool:
    """True if the sender address (local part + domain) or subject contains
    a job/recruiting signal."""
    sender_lower = sender.lower()
    subject_lower = subject.lower()
    return any(kw in sender_lower for kw in _JOB_KEYWORDS) or \
           any(kw in subject_lower for kw in _JOB_KEYWORDS)


# Recurring "new jobs matching your profile" recommendation digests — these are
# never about the status of an existing application, so they're excluded even
# though their sender domain is job-related and would otherwise pass _is_job_related.
_DIGEST_SENDER_DOMAINS = ("jobagent.stepstone.de", "jobs2web.com")


def _is_recommendation_digest(sender: str) -> bool:
    domain = sender.split("@")[-1].lower() if "@" in sender else ""
    return any(d in domain for d in _DIGEST_SENDER_DOMAINS)


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
        email_executor.move(staging_id, move_source="auto")
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

    # Fetch all visible messages from monitored folders (up to 200 per folder).
    # We keep track of every message_id currently present in Outlook so that
    # pending records for emails the user already handled manually can be pruned.
    messages: list[dict] = []
    live_message_ids: set[str] = set()   # all IDs currently visible in Outlook

    for folder in MONITORED_FOLDERS:
        try:
            fetched = ms_graph.get_messages(folder, max_messages=200)
            live_message_ids.update(m["email_message_id"] for m in fetched)
            new = [m for m in fetched if m["email_message_id"] not in seen_ids]
            messages.extend(new)
            for m in new:
                seen_ids.add(m["email_message_id"])
        except Exception as exc:
            log.warning("Failed to fetch from folder '%s': %s", folder, exc)

    log.info("Fetched %d new emails (%d total visible in monitored folders)",
             len(messages), len(live_message_ids))

    # ── Prune stale pending records ───────────────────────────────────────────
    # If an email was manually moved or deleted in Outlook while it was sitting
    # in our pending queue, remove it so the user doesn't see ghost entries.
    if live_message_ids:   # only prune when we got a valid response from Outlook
        pruned = 0
        now_iso = datetime.utcnow().isoformat()
        for pending in db.get_pending_emails():
            mid = pending.get("email_message_id")
            if mid and mid not in live_message_ids:
                db.update_email_staging_status(
                    pending["id"], "skipped",
                    reviewed_at=now_iso,
                )
                pruned += 1
                log.info("Pruned stale pending id=%d — email no longer in monitored folder", pending["id"])
        if pruned:
            log.info("Pruned %d stale pending record(s)", pruned)

        # Re-stage emails marked 'executed' that are still in a monitored folder —
        # their Graph move never actually happened (common from the old 404 false-executed bug).
        restaged = db.restage_falsely_executed(live_message_ids)
        if restaged:
            log.info("Re-staged %d falsely-executed email(s) back to pending", restaged)

    skipped = 0
    for msg in messages:
        subject = msg["subject"]
        preview = msg["body_preview"]

        link = _link_to_application(msg["sender"], subject, preview)

        if not link["matched_app_id"] and (
            _is_recommendation_digest(msg["sender"]) or not _is_job_related(msg["sender"], subject)
        ):
            skipped += 1
            continue

        clf = _classify(client, subject, preview)
        category = clf.get("category", "Uncertain")
        confidence = int(clf.get("confidence", 0))
        reason = clf.get("reason", "")

        if category == "In Review" and link["match_type"] == "unmatched":
            new_app_id = _maybe_create_application(client, msg, subject, preview, confidence)
            if new_app_id:
                link = {"matched_app_id": new_app_id,
                        "match_confidence": confidence, "match_type": "email-confirmation"}

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

    if skipped:
        log.info("Skipped %d email(s) with no application match and no job/recruiting signal", skipped)


if __name__ == "__main__":
    from core.config import setup_logging
    _cfg = load_config()
    setup_logging(_cfg)
    run(_cfg)
