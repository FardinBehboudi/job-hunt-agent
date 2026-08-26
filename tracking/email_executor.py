"""
email_executor.py — Graph API folder moves and application status updates.
"""

import json
import logging
import os
import re
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

from dedup import db
from tracking import ms_graph

# ── Interview data extraction ─────────────────────────────────────────────────

_MONTHS = {
    'jan':1,'feb':2,'mar':3,'apr':4,'may':5,'jun':6,
    'jul':7,'aug':8,'sep':9,'oct':10,'nov':11,'dec':12,
}

_EXTRACT_SYSTEM = "You extract structured event data from job-application emails. Return ONLY valid JSON — no prose, no markdown."

_EXTRACT_PROMPT = """\
## Email Subject
{subject}

## Email Body Preview
{body}

Extract and return exactly this JSON:
{{
  "event_type": "<interview|code_challenge|call|meeting>",
  "title": "<concise title, e.g. Technical Interview at Pliant>",
  "event_date": "<YYYY-MM-DD or null>",
  "event_time": "<HH:MM 24-hour or null>",
  "timezone": "<e.g. Europe/Berlin or CEST or null>",
  "description": "<2-3 sentences: meeting link if present, interviewers if named, format video/phone/onsite, key topics>"
}}
"""

def _extract_meeting_link(text: str) -> str:
    """Return first meeting URL found (Meet, Zoom, Teams, Calendly), or ''."""
    for pat in (
        r'https?://meet\.google\.com/\S+',
        r'https?://\S+\.zoom\.us/\S+',
        r'https?://teams\.microsoft\.com/\S+',
        r'https?://calendly\.com/\S+',
        r'https?://whereby\.com/\S+',
    ):
        m = re.search(pat, text, re.I)
        if m:
            return m.group(0).rstrip('.,;)')
    return ""

def _extract_event_claude(subject: str, body_preview: str) -> dict:
    """
    Use Claude to extract rich interview details.
    Falls back to regex-only if the API call fails.
    """
    fallback = _extract_event_regex(subject, body_preview)
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=300,
            system=_EXTRACT_SYSTEM,
            messages=[{"role": "user", "content": _EXTRACT_PROMPT.format(
                subject=subject, body=body_preview[:600],
            )}],
        )
        raw = resp.content[0].text.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)
            raw = raw.strip()
        data = json.loads(raw)
        # Merge: Claude result takes precedence over regex, but keep regex date
        # if Claude missed it
        if not data.get("event_date"):
            data["event_date"] = fallback.get("event_date")
        if not data.get("event_time"):
            data["event_time"] = fallback.get("event_time")
        if not data.get("timezone"):
            data["timezone"] = fallback.get("timezone")
        # Append meeting link to description if found
        link = fallback.get("meeting_link", "")
        if link and link not in (data.get("description") or ""):
            data["description"] = (data.get("description") or "") + f"\n{link}"
        return data
    except Exception as exc:
        log.warning("Claude extraction failed, using regex fallback: %s", exc)
        return fallback

def _extract_event_regex(subject: str, body_preview: str) -> dict:
    """Regex-based fallback: extract date, time, timezone, meeting link."""
    text = subject + " " + body_preview
    result: dict = {}

    # Google Calendar: "@ Thu Jun 11, 2026 3pm" / "@ Thu Jun 11, 2026 3:00pm"
    m = re.search(
        r'@\s+(?:\w+\s+)?(\w{3,9})\s+(\d{1,2}),?\s+(\d{4})\s+'
        r'(\d{1,2})(?::(\d{2}))?\s*(am|pm)',
        text, re.I,
    )
    if m:
        mon, day, yr, hr, mn, ap = m.groups()
        month = _MONTHS.get(mon.lower()[:3])
        if month:
            result['event_date'] = f"{yr}-{month:02d}-{int(day):02d}"
            h = int(hr)
            if ap.lower() == 'pm' and h < 12: h += 12
            elif ap.lower() == 'am' and h == 12: h = 0
            result['event_time'] = f"{h:02d}:{mn or '00'}"

    if 'event_date' not in result:
        m2 = re.search(r'(\d{4})-(\d{2})-(\d{2})', text)
        if m2: result['event_date'] = m2.group(0)
        else:
            m3 = re.search(r'(\d{1,2})\.(\d{1,2})\.(\d{4})', text)
            if m3:
                d, mo, y = m3.groups()
                result['event_date'] = f"{y}-{int(mo):02d}-{int(d):02d}"

    tz = re.search(r'\(([A-Z][A-Za-z/_+-]{1,20})\)', text)
    if tz: result['timezone'] = tz.group(1)

    link = _extract_meeting_link(text)
    if link: result['meeting_link'] = link

    return result

load_dotenv()
log = logging.getLogger(__name__)

FOLDER_MAP = {
    "Rejected":       "Inbox/Applications/Rejected",
    "In Review":      "Inbox/Applications/In Review",
    "Next Step":      "Inbox/Applications/Next Step",
    "Interview":      "Inbox/Applications/Next Step/Interview/Todo",
    "Code Challenge": "Inbox/Applications/Next Step/Code Challange/ToDo",
    "Offer":          "Inbox/Applications/Offer",
    "Uncertain":      "Inbox/Applications/Uncertain",
    "Delete":         "deleteditems",
}

APP_STATUS_MAP = {
    "Rejected":       "Rejected",
    "In Review":      "In Review",
    "Next Step":      "Next Step",
    "Interview":      "Interview Scheduled",
    "Code Challenge": "Code Challenge",
    "Offer":          "Offer",
    "Delete":         "Deleted",
    "Uncertain":      "Uncertain",
}


def move(staging_id: int, override_folder: "str | None" = None,
         cfg: "dict | None" = None, move_source: str = "manual") -> None:
    """Move the email for staging_id to its target Outlook folder via Graph API."""
    record = db.get_staged_email(staging_id)
    if not record:
        raise ValueError(f"No staging record found: id={staging_id}")

    # Skip already-executed records to prevent duplicate log entries
    if record.get("status") == "executed":
        log.info("staging_id=%d already executed — skipping", staging_id)
        return

    category = (override_folder
                or record.get("user_override_folder")
                or record["predicted_folder"])
    if category not in FOLDER_MAP:
        raise ValueError(f"Unknown category '{category}' — no folder mapping defined")
    target_folder = FOLDER_MAP[category]   # may be None for "Uncertain"
    source_folder = record["source_folder"]
    graph_id = record.get("email_uid")   # Graph message ID stored in email_uid column
    now = datetime.now(timezone.utc).isoformat()

    # ── Attempt the move, with one automatic ID refresh on 404 ──────────────────
    # Graph message IDs change after every folder move. If the stored ID is stale
    # we look up the current ID via the stable RFC-2822 internetMessageId and retry.
    _graph_id = graph_id
    for _attempt in range(2):
        try:
            new_graph_id = ms_graph.move_message(_graph_id, target_folder)
            if new_graph_id != _graph_id:
                db.update_email_uid(staging_id, new_graph_id)

            db.log_email_move(staging_id, source_folder, target_folder or source_folder,
                              success=True, move_source=move_source)
            db.update_email_staging_status(staging_id, "executed", executed_at=now)

            app_id = record.get("matched_app_id")
            if app_id:
                db.update_application_from_email(
                    app_id,
                    APP_STATUS_MAP.get(category, category),
                    record.get("received_date", ""),
                    record.get("subject", ""),
                    staging_id,
                )

            # For any Next Step sub-folder (Interview, Code Challenge, Next Step):
            # extract rich event data and upsert with date priority.
            # This block is fully isolated — failures here NEVER break the move.
            if category in ("Interview", "Code Challenge", "Next Step") and app_id:
                try:
                    subject       = record.get("subject", "")
                    body_preview  = record.get("body_preview", "")
                    received_date = record.get("received_date", "")

                    extracted = _extract_event_claude(subject, body_preview)

                    if not extracted.get("event_type"):
                        extracted["event_type"] = (
                            "interview"      if category == "Interview" else
                            "code_challenge" if category == "Code Challenge" else
                            "meeting"
                        )

                    # Build an authoritative title from the matched application's
                    # company + role — these are exact strings from the DB and more
                    # reliable than whatever Claude extracted from the email text.
                    app = db.get_application_by_id(app_id)
                    if app and app.get("company"):
                        _type_label = {
                            "Interview":      "Interview",
                            "Code Challenge": "Code Challenge",
                            "Next Step":      "Next Step",
                        }.get(category, "Interview")
                        _role = (app.get("role") or "").strip()
                        _co   = app["company"].strip()
                        extracted["title"] = (
                            f"{_type_label} at {_co} — {_role}" if _role
                            else f"{_type_label} at {_co}"
                        )

                    db.upsert_event_from_executor(staging_id, app_id, {
                        "priority": "high",
                        "title":    subject[:120],
                        **extracted,
                    }, received_date=received_date)
                    log.info("Upserted upcoming_event staging_id=%d app_id=%d date=%s",
                             staging_id, app_id, extracted.get("event_date"))
                except Exception as ex_ev:
                    log.warning("Event extraction failed for staging_id=%d (move still OK): %s",
                                staging_id, ex_ev)

            log.info("Moved staging_id=%d → %s (attempt %d)", staging_id, target_folder, _attempt + 1)
            return  # ── success ──

        except requests.HTTPError as exc:
            is_404 = exc.response is not None and exc.response.status_code == 404
            if is_404 and _attempt == 0:
                # Stored Graph ID is stale — look up the current ID via stable internetMessageId
                internet_id = record.get("email_message_id")
                fresh_id = (ms_graph.find_message_id_by_internet_id(internet_id)
                            if internet_id else None)
                if fresh_id and fresh_id != _graph_id:
                    db.update_email_uid(staging_id, fresh_id)
                    _graph_id = fresh_id
                    log.info("staging_id=%d: refreshed stale Graph ID, retrying move", staging_id)
                    continue   # ── retry with fresh ID ──
                # Not found by any method — mark failed so it gets re-queued next scan
                log.warning("staging_id=%d: message not found (404) and not locatable by internetMessageId — marking failed",
                            staging_id)
                db.update_email_staging_status(staging_id, "failed")
                raise
            # Non-404 or second attempt — log and propagate
            db.log_email_move(staging_id, source_folder, target_folder,
                              success=False, error=str(exc))
            db.update_email_staging_status(staging_id, "failed")
            log.error("Move failed staging_id=%d: %s", staging_id, exc)
            raise

        except Exception as exc:
            db.log_email_move(staging_id, source_folder, target_folder,
                              success=False, error=str(exc))
            db.update_email_staging_status(staging_id, "failed")
            log.error("Move failed staging_id=%d: %s", staging_id, exc)
            raise


def approve_batch(staging_ids: list[int],
                  cfg: "dict | None" = None) -> dict:
    succeeded = 0
    failed = 0
    for sid in staging_ids:
        try:
            move(sid, cfg=cfg)
            succeeded += 1
        except Exception as exc:
            log.error("Batch move failed staging_id=%d: %s", sid, exc)
            failed += 1
    return {"succeeded": succeeded, "failed": failed}
