"""
email_processor.py — fetch, classify, link, and stage recruiter emails.

Run via Windows Task Scheduler every 15 minutes:
    python tracking\\email_processor.py
"""

import email as _email_module
import email.header
import email.utils
import html.parser
import imaplib
import json
import logging
import os
import re

import anthropic
from dotenv import load_dotenv

from core.config import load_config
from dedup import db
from tracking import email_executor

load_dotenv()
log = logging.getLogger(__name__)

_MODEL = "claude-sonnet-4-6"
IMAP_HOST = "imap-mail.outlook.com"
IMAP_PORT = 993
MONITORED_FOLDERS = ["Inbox", "Focus", "Other", "Junk Email"]
_EVENT_CATEGORIES = {"Interview", "Code Challenge", "Next Step"}

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

# ── HTML stripping ─────────────────────────────────────────────────────────────

class _Stripper(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        return " ".join(self._parts)


def _strip_html(raw: str) -> str:
    s = _Stripper()
    try:
        s.feed(raw)
        return s.get_text()
    except Exception:
        return re.sub(r"<[^>]+>", " ", raw)


def _decode_header(value: str) -> str:
    parts = _email_module.header.decode_header(value or "")
    out: list[str] = []
    for part, charset in parts:
        if isinstance(part, bytes):
            out.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            out.append(str(part))
    return "".join(out)


def _extract_body(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if (part.get_content_type() == "text/plain"
                    and "attachment" not in str(part.get("Content-Disposition", ""))):
                charset = part.get_content_charset() or "utf-8"
                try:
                    return part.get_payload(decode=True).decode(charset, errors="replace")
                except Exception:
                    return ""
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                charset = part.get_content_charset() or "utf-8"
                try:
                    return _strip_html(
                        part.get_payload(decode=True).decode(charset, errors="replace")
                    )
                except Exception:
                    return ""
    else:
        charset = msg.get_content_charset() or "utf-8"
        try:
            raw = msg.get_payload(decode=True)
            if raw:
                text = raw.decode(charset, errors="replace")
                return _strip_html(text) if msg.get_content_type() == "text/html" else text
        except Exception:
            pass
    return ""


# ── IMAP ───────────────────────────────────────────────────────────────────────

def _connect(cfg: dict) -> imaplib.IMAP4_SSL:
    addr = cfg.get("hotmail_address") or cfg["contact"]["email"]
    password = os.getenv("HOTMAIL_PASSWORD")
    if not password:
        raise EnvironmentError("HOTMAIL_PASSWORD is not set in .env")
    mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    mail.login(addr, password)
    return mail


def _fetch_unseen_from_folders(mail: imaplib.IMAP4_SSL,
                                seen_ids: "set[str]") -> list[dict]:
    """Fetch UNSEEN messages from all monitored folders, skipping already-staged IDs."""
    messages: list[dict] = []

    for folder in MONITORED_FOLDERS:
        quoted = f'"{folder}"' if " " in folder else folder
        try:
            status, _ = mail.select(quoted, readonly=True)
            if status != "OK":
                log.debug("Folder unavailable: %s", folder)
                continue
        except Exception as exc:
            log.debug("Cannot select folder %s: %s", folder, exc)
            continue

        status, data = mail.uid("search", None, "UNSEEN")
        if status != "OK" or not data[0]:
            continue

        for uid in data[0].split()[-50:]:
            try:
                status, msg_data = mail.uid("fetch", uid, "(RFC822)")
                if status != "OK" or not msg_data or not msg_data[0]:
                    continue
                parsed = _email_module.message_from_bytes(msg_data[0][1])
                message_id = (parsed.get("Message-ID") or "").strip()
                if not message_id or message_id in seen_ids:
                    continue
                _, from_addr = _email_module.utils.parseaddr(parsed.get("From", ""))
                body = _extract_body(parsed)
                messages.append({
                    "email_uid": uid.decode() if isinstance(uid, bytes) else str(uid),
                    "email_message_id": message_id,
                    "sender": from_addr,
                    "subject": _decode_header(parsed.get("Subject", "")),
                    "body_preview": body[:500],
                    "received_date": parsed.get("Date", ""),
                    "source_folder": folder,
                })
                seen_ids.add(message_id)
            except Exception as exc:
                log.warning("Error fetching uid=%s from %s: %s", uid, folder, exc)

    return messages


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
    if record and category == "Rejected" and record["confidence_score"] >= threshold:
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
    mail = _connect(cfg)
    seen_ids = db.get_staged_message_ids()
    messages = _fetch_unseen_from_folders(mail, seen_ids)
    mail.logout()
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

        if auto_move_enabled and confidence >= threshold and category == "Rejected":
            _auto_execute(staging_id, category, link["matched_app_id"],
                          msg["received_date"], subject)


if __name__ == "__main__":
    from core.config import setup_logging
    _cfg = load_config()
    setup_logging(_cfg)
    run(_cfg)
