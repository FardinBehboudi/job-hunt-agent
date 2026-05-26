"""
interview_handler.py — classify interview stage, auto-confirm or pause.

Email is sent via SMTP (smtplib, built-in).
Credentials: hotmail_address from config.yaml, HOTMAIL_PASSWORD from .env.

Stage routing:
  recruiter_call + auto_confirm_recruiter_call=true → reply immediately
  technical / advanced / unknown → send pause notification to user

Watch-for-reply:
  Pending confirmations are stored in {agent_dir}/pending_confirmations.json.
  email_watcher calls send_confirmation_from_reply() when the user replies.
"""

import json
import logging
import os
import smtplib
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from core.config import load_config

load_dotenv()
log = logging.getLogger(__name__)

_MODEL      = "claude-sonnet-4-6"
SMTP_HOST   = "smtp-mail.outlook.com"
SMTP_PORT   = 587

# Subject prefix stamped on pause notifications — used to detect user replies
_PAUSE_SUBJECT_PREFIX = "⏸️ Action needed"


# ── Stage classification ──────────────────────────────────────────────────────

_CLASSIFY_SYSTEM = """\
You classify job-interview invitation emails.
Return ONLY one JSON object — no prose, no markdown fences.
"""

_CLASSIFY_PROMPT = """\
## Email Subject
{subject}

## Email Body
{body}

Classify this email and extract offered time slots.
Return:
{{
  "stage": "<recruiter_call|technical|advanced|unknown>",
  "offered_slots": ["<slot1>", "<slot2>"],
  "email_language": "<en|de>",
  "reasoning": "<one sentence>"
}}

Stage definitions:
- recruiter_call: intro, kennenlern, recruiter, first call, Erstgespräch, chat
- technical: technical, coding, case study, Fachgespräch, assignment, task, test
- advanced: final, panel, CEO, team interview, Probearbeiten, assessment
- unknown: anything that doesn't clearly fit the above
"""


def _classify(client: anthropic.Anthropic, subject: str, body: str) -> dict:
    resp = client.messages.create(
        model=_MODEL,
        max_tokens=256,
        system=_CLASSIFY_SYSTEM,
        messages=[{"role": "user", "content": _CLASSIFY_PROMPT.format(
            subject=subject, body=body[:3000]
        )}],
    )
    try:
        return json.loads(resp.content[0].text.strip())
    except Exception:
        return {"stage": "unknown", "offered_slots": [], "email_language": "en",
                "reasoning": "parse error"}


# ── Confirmation reply templates ──────────────────────────────────────────────

_CONFIRM_EN = """\
Dear {name_or_team},

Thank you for your invitation. I am happy to confirm the following slot:

{chosen_slot}

Please let me know if there is anything I should prepare in advance.

Looking forward to speaking with you.

Best regards,
{candidate_name}
"""

_CONFIRM_DE = """\
Guten Tag {name_or_team},

vielen Dank für Ihre Einladung. Ich freue mich, den folgenden Termin zu bestätigen:

{chosen_slot}

Falls es etwas gibt, das ich im Voraus vorbereiten soll, lassen Sie es mich bitte wissen.

Ich freue mich auf das Gespräch.

Mit freundlichen Grüßen,
{candidate_name}
"""

_PAUSE_NOTIFY = """\
Company:        {company}
Stage detected: {stage_label}
They offered:
  {slots}

Original email subject: {original_subject}

Reply to THIS email with your chosen slot and I will confirm for you.
"""


# ── SMTP helpers ──────────────────────────────────────────────────────────────

def _smtp_send(to: str, subject: str, body: str, cfg: dict,
               in_reply_to: str = "", original_subject: str = "") -> None:
    """
    Send an email via SMTP.
    If in_reply_to is set, sends as a reply (RE: prefix + threading headers).
    """
    from_addr = cfg.get("hotmail_address") or cfg["contact"]["email"]
    password  = os.getenv("HOTMAIL_PASSWORD")
    if not password:
        raise EnvironmentError("HOTMAIL_PASSWORD is not set in .env")

    final_subject = ("RE: " + original_subject) if (in_reply_to and original_subject) else subject

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = final_subject
    msg["From"]    = from_addr
    msg["To"]      = to
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"]  = in_reply_to

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(from_addr, password)
        smtp.send_message(msg)

    log.info("Email sent to %s: %s", to, final_subject)


# ── Pending confirmations log ─────────────────────────────────────────────────

def _pending_path(cfg: dict) -> Path:
    return cfg["paths"]["agent_dir"] / "pending_confirmations.json"


def _load_pending(cfg: dict) -> list[dict]:
    path = _pending_path(cfg)
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _save_pending(cfg: dict, entries: list[dict]) -> None:
    path = _pending_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)


def _add_pending(cfg: dict, entry: dict) -> None:
    entries = _load_pending(cfg)
    entries.append(entry)
    _save_pending(cfg, entries)


def _remove_pending(cfg: dict, notification_subject: str) -> None:
    entries = [e for e in _load_pending(cfg)
               if e.get("notification_subject") != notification_subject]
    _save_pending(cfg, entries)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_sender_name(msg: dict) -> str:
    display = msg.get("from", {}).get("emailAddress", {}).get("name", "")
    return display.split()[0] if display else "Team"


# ── Public: handle incoming interview invite ──────────────────────────────────

def handle(msg: dict, matched_job: dict | None, cfg: dict | None = None) -> None:
    if cfg is None:
        cfg = load_config()

    subject     = msg.get("subject", "")
    body        = msg.get("body", {}).get("content", "") or msg.get("bodyPreview", "")
    company     = matched_job.get("company", "Unknown Company") if matched_job else "Unknown Company"
    msg_id      = msg.get("id", "")          # email Message-ID header
    sender_addr = msg.get("from_addr", "") or \
                  msg.get("from", {}).get("emailAddress", {}).get("address", "")
    recruiter   = _extract_sender_name(msg)

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    info   = _classify(client, subject, body)

    stage       = info.get("stage", "unknown")
    slots       = info.get("offered_slots", [])
    lang        = info.get("email_language", "en")

    log.info("Interview stage=%s company=%s slots=%s", stage, company, slots)

    from excel_updater import update_status

    _STAGE_LABELS = {
        "recruiter_call": "Recruiter / intro call",
        "technical":      "Technical screening",
        "advanced":       "Advanced / final round",
        "unknown":        "Unknown stage",
    }
    stage_label = _STAGE_LABELS.get(stage, stage)

    if stage == "recruiter_call" and cfg.get("auto_confirm_recruiter_call", False):
        chosen    = slots[0] if slots else "(no slot detected — please clarify)"
        candidate = cfg.get("contact", {}).get("name", "Candidate")
        template  = _CONFIRM_DE if lang == "de" else _CONFIRM_EN
        reply_body = template.format(
            name_or_team=recruiter,
            chosen_slot=chosen,
            candidate_name=candidate,
        )
        try:
            _smtp_send(
                to=sender_addr,
                subject="",
                body=reply_body,
                cfg=cfg,
                in_reply_to=msg_id,
                original_subject=subject,
            )
            if matched_job:
                update_status(matched_job, "Call Scheduled ✓", cfg)
            log.info("Auto-confirmed recruiter call: %s", company)
        except Exception as exc:
            log.error("Failed to send confirmation reply: %s", exc)

    else:
        notify_email = cfg.get("notify_email") or cfg.get("hotmail_address", "")
        if not notify_email:
            log.warning("No notify_email configured — cannot send pause notification")
            return

        notification_subject = f"{_PAUSE_SUBJECT_PREFIX} — {company} wants a {stage_label}"
        notify_body = _PAUSE_NOTIFY.format(
            company=company,
            stage_label=stage_label,
            slots="\n  ".join(slots) if slots else "(no specific slots found in email)",
            original_subject=subject,
        )

        try:
            _smtp_send(to=notify_email, subject=notification_subject,
                       body=notify_body, cfg=cfg)
        except Exception as exc:
            log.error("Failed to send pause notification: %s", exc)
            return

        _add_pending(cfg, {
            "notification_subject": notification_subject,
            "original_msg_id":      msg_id,
            "original_sender_addr": sender_addr,
            "original_subject":     subject,
            "recruiter_name":       recruiter,
            "company":              company,
            "job_url":              matched_job.get("url", "") if matched_job else "",
            "stage":                stage,
            "email_language":       lang,
            "created_at":           datetime.now(timezone.utc).isoformat(),
        })

        status_map = {
            "recruiter_call": "⏸️ Technical — awaiting your confirmation",
            "technical":      "⏸️ Technical — awaiting your confirmation",
            "advanced":       "⏸️ Final — awaiting your confirmation",
            "unknown":        "⏸️ Technical — awaiting your confirmation",
        }
        if matched_job:
            update_status(matched_job, status_map[stage], cfg)

        log.info("Pause notification sent → %s for %s", notify_email, company)


# ── Public: process user's reply to a pause notification ─────────────────────

def _extract_chosen_slot(client: anthropic.Anthropic, reply_body: str) -> str:
    resp = client.messages.create(
        model=_MODEL,
        max_tokens=64,
        system="Extract the time slot the user chose from their email reply. Return ONLY the slot as plain text.",
        messages=[{"role": "user", "content": reply_body[:1000]}],
    )
    return resp.content[0].text.strip()


def send_confirmation_from_reply(reply_body: str, pending: dict,
                                 cfg: dict | None = None) -> None:
    """
    Called when the user replies to a pause notification with their chosen slot.
    Drafts a professional confirmation and sends it to the recruiter.
    """
    if cfg is None:
        cfg = load_config()

    client    = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    chosen    = _extract_chosen_slot(client, reply_body)
    lang      = pending.get("email_language", "en")
    candidate = cfg.get("contact", {}).get("name", "Candidate")
    template  = _CONFIRM_DE if lang == "de" else _CONFIRM_EN
    confirm_body = template.format(
        name_or_team=pending.get("recruiter_name", "Team"),
        chosen_slot=chosen,
        candidate_name=candidate,
    )

    try:
        _smtp_send(
            to=pending["original_sender_addr"],
            subject="",
            body=confirm_body,
            cfg=cfg,
            in_reply_to=pending.get("original_msg_id", ""),
            original_subject=pending.get("original_subject", ""),
        )
        log.info("Sent confirmation to recruiter for %s (slot: %s)",
                 pending.get("company"), chosen)
    except Exception as exc:
        log.error("Failed to send recruiter confirmation: %s", exc)
        return

    from excel_updater import update_status
    stage        = pending.get("stage", "technical")
    excel_status = ("Technical Scheduled ✓"
                    if stage in ("technical", "recruiter_call")
                    else "Final Scheduled ✓")
    job_stub = {"url": pending.get("job_url", ""),
                "title": "", "company": pending.get("company", "")}
    update_status(job_stub, excel_status, cfg)

    _remove_pending(cfg, pending["notification_subject"])
    log.info("Pending entry resolved for %s", pending.get("company"))


if __name__ == "__main__":
    from config import setup_logging
    cfg = load_config()
    setup_logging(cfg)
    sample_msg = {
        "id": "<test-msg-id@example.com>",
        "subject": "Einladung zum Erstgespräch — Data Engineer Position",
        "from_addr": "recruiter@example.de",
        "from": {"emailAddress": {"name": "Max Mustermann", "address": "recruiter@example.de"}},
        "body": {"content": (
            "Guten Tag,\nwir laden Sie herzlich zu einem ersten Kennenlern-Gespräch ein.\n"
            "Mögliche Termine: Montag 18. Mai 14:00 Uhr oder Dienstag 19. Mai 10:00 Uhr.\n"
            "Mit freundlichen Grüßen,\nMax Mustermann, Recruiter"
        )},
    }
    handle(sample_msg,
           {"company": "ExampleCorp", "title": "Data Engineer", "url": "https://example.com/job/1"},
           cfg)
