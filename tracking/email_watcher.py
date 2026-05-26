"""
email_watcher.py — read Hotmail inbox via IMAP (imaplib, built-in).

Credentials:
  Email address : config.yaml → hotmail_address
  Password      : .env        → HOTMAIL_PASSWORD

Design:
  run() polls once and returns. Call it every 2 hours via Task Scheduler
  (separate entry from the daily main.py — see README).

  On each poll:
    1. Check for replies to pending pause-notifications (watch-for-reply).
    2. Process other unread emails from known company domains.
"""

import email
import email.header
import email.utils
import html.parser
import imaplib
import json
import logging
import os
import re
from pathlib import Path

from dotenv import load_dotenv

from core.config import load_config

load_dotenv()
log = logging.getLogger(__name__)

IMAP_HOST = "imap-mail.outlook.com"
IMAP_PORT = 993

_CONFIRM_KEYWORDS = [
    "bewerbung eingegangen", "application received",
    "we received your application", "bestätigung",
    "ihre bewerbung", "thank you for applying",
    "danke für ihre bewerbung",
]
_INTERVIEW_KEYWORDS = [
    "einladung", "invitation", "interview", "gespräch",
    "call", "meeting", "vorstellungsgespräch",
]


# ── HTML stripping ────────────────────────────────────────────────────────────

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


# ── IMAP helpers ──────────────────────────────────────────────────────────────

def _connect(cfg: dict) -> imaplib.IMAP4_SSL:
    addr     = cfg.get("hotmail_address") or cfg["contact"]["email"]
    password = os.getenv("HOTMAIL_PASSWORD")
    if not password:
        raise EnvironmentError("HOTMAIL_PASSWORD is not set in .env")
    mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    mail.login(addr, password)
    return mail


def _decode_header(value: str) -> str:
    parts = email.header.decode_header(value or "")
    out: list[str] = []
    for part, charset in parts:
        if isinstance(part, bytes):
            out.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            out.append(str(part))
    return "".join(out)


def _extract_body(msg: email.message.Message) -> str:
    """Return plain-text body, stripping HTML if necessary."""
    if msg.is_multipart():
        # Prefer text/plain
        for part in msg.walk():
            if (part.get_content_type() == "text/plain"
                    and "attachment" not in str(part.get("Content-Disposition", ""))):
                charset = part.get_content_charset() or "utf-8"
                try:
                    return part.get_payload(decode=True).decode(charset, errors="replace")
                except Exception:
                    return ""
        # Fall back to text/html
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


def _fetch_unseen(mail: imaplib.IMAP4_SSL) -> list[dict]:
    """Fetch all unseen messages from INBOX. Returns list of message dicts."""
    mail.select("INBOX")
    status, data = mail.uid("search", None, "UNSEEN")
    if status != "OK" or not data[0]:
        return []

    messages: list[dict] = []
    uids = data[0].split()
    for uid in uids[-50:]:  # cap at 50 most recent
        status, msg_data = mail.uid("fetch", uid, "(RFC822)")
        if status != "OK" or not msg_data or not msg_data[0]:
            continue

        raw = msg_data[0][1]
        parsed = email.message_from_bytes(raw)

        subject    = _decode_header(parsed.get("Subject", ""))
        from_full  = parsed.get("From", "")
        from_name, from_addr = email.utils.parseaddr(from_full)
        body       = _extract_body(parsed)
        message_id = parsed.get("Message-ID", "")

        messages.append({
            "uid":      uid,
            "id":       message_id,      # used as In-Reply-To target
            "subject":  subject,
            "from_addr": from_addr,
            "from": {
                "emailAddress": {
                    "address": from_addr,
                    "name":    from_name or from_addr,
                }
            },
            "body":        {"content": body},
            "bodyPreview": body[:255],
        })

    return messages


def _mark_read(mail: imaplib.IMAP4_SSL, uid: bytes) -> None:
    try:
        mail.uid("store", uid, "+FLAGS", "\\Seen")
    except Exception as exc:
        log.warning("Could not mark message as read: %s", exc)


# ── Email classification ──────────────────────────────────────────────────────

def _classify_email(subject: str, body: str) -> str:
    text = (subject + " " + body).lower()
    if any(kw in text for kw in _INTERVIEW_KEYWORDS):
        return "interview"
    if any(kw in text for kw in _CONFIRM_KEYWORDS):
        return "confirmation"
    return "other"


# ── Applied log ───────────────────────────────────────────────────────────────

def _load_applied_log(cfg: dict) -> list[dict]:
    log_path: Path = cfg["paths"]["log_file"]
    if not log_path.exists():
        return []
    try:
        with open(log_path, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _match_to_application(sender: str, subject: str,
                           applied_log: list[dict]) -> dict | None:
    """Return the log entry whose company name appears in sender domain or subject."""
    sender_domain = sender.split("@")[-1].lower() if "@" in sender else ""
    subject_lower = subject.lower()
    for entry in applied_log:
        company = entry.get("company", "").lower().strip()
        if company and (company in sender_domain or company in subject_lower):
            return entry
    return None


# ── Pending-reply detection ───────────────────────────────────────────────────

def _check_pending_replies(mail: imaplib.IMAP4_SSL, messages: list[dict],
                           cfg: dict) -> None:
    """
    Look for replies to our pause notifications among the unread messages.
    Reply signature: sent FROM the user's own address, subject begins with
    "RE:" and contains the pause prefix.
    """
    from interview_handler import (
        _load_pending, _PAUSE_SUBJECT_PREFIX, send_confirmation_from_reply,
    )

    pending = _load_pending(cfg)
    if not pending:
        return

    own_email     = (cfg.get("hotmail_address") or cfg["contact"]["email"]).lower()
    reply_prefixes = ("re:", "aw:", "re :", "aw :")

    for msg in messages:
        subject      = msg.get("subject", "")
        sender       = msg.get("from_addr", "").lower()
        subject_lower = subject.lower()

        is_reply = any(subject_lower.startswith(p) for p in reply_prefixes)
        is_self  = sender == own_email
        is_pause = _PAUSE_SUBJECT_PREFIX.lower() in subject_lower

        if not (is_reply and is_self and is_pause):
            continue

        for entry in pending:
            notif_subj = entry.get("notification_subject", "")
            if notif_subj.lower() in subject_lower:
                body = msg.get("body", {}).get("content", "")
                log.info("User replied to pause notification for %s",
                         entry.get("company"))
                try:
                    send_confirmation_from_reply(body, entry, cfg)
                except Exception as exc:
                    log.error("send_confirmation_from_reply failed: %s", exc)
                _mark_read(mail, msg["uid"])
                break


# ── Main poll ─────────────────────────────────────────────────────────────────

def run(cfg: dict | None = None) -> None:
    """
    Poll inbox once. Task Scheduler calls this every 2 hours via:
        python email_watcher.py
    """
    if cfg is None:
        cfg = load_config()

    from interview_handler import handle as handle_interview
    from excel_updater import update_status

    mail        = _connect(cfg)
    applied_log = _load_applied_log(cfg)
    messages    = _fetch_unseen(mail)
    log.info("Inbox: %d unread messages", len(messages))

    # Pass 1: check for user replies to pause notifications
    _check_pending_replies(mail, messages, cfg)

    # Pass 2: process emails from known company domains
    for msg in messages:
        subject = msg.get("subject", "")
        sender  = msg.get("from_addr", "")

        matched_job = _match_to_application(sender, subject, applied_log)
        if not matched_job:
            log.debug("No application match: '%s' (from %s)", subject, sender)
            continue

        body     = msg.get("body", {}).get("content", "")
        category = _classify_email(subject, body)

        if category == "confirmation":
            log.info("Confirmation from %s for %s @ %s",
                     sender, matched_job.get("title"), matched_job.get("company"))
            update_status(matched_job, "Applied ✓", cfg)
            _mark_read(mail, msg["uid"])

        elif category == "interview":
            log.info("Interview invite: '%s' from %s", subject, sender)
            handle_interview(msg, matched_job, cfg)
            _mark_read(mail, msg["uid"])

        else:
            log.debug("Skipping (category=%s): %s", category, subject)

    mail.logout()


if __name__ == "__main__":
    import sys
    from config import setup_logging

    cfg = load_config()
    setup_logging(cfg)

    if "--test-auth" in sys.argv:
        # Verify credentials — prints last 10 subject lines then exits
        mail = _connect(cfg)
        mail.select("INBOX")
        _, data = mail.uid("search", None, "ALL")
        uids = data[0].split()
        for uid in uids[-10:]:
            _, msg_data = mail.uid("fetch", uid, "(BODY[HEADER.FIELDS (SUBJECT FROM DATE)])")
            parsed = email.message_from_bytes(msg_data[0][1])
            print(f"{parsed.get('Date', '')}  {parsed.get('From', '')}  {_decode_header(parsed.get('Subject', ''))}")
        mail.logout()
    else:
        run(cfg)
