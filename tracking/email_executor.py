"""
email_executor.py — IMAP folder moves and application status updates.
"""

import imaplib
import logging
import os
from datetime import datetime

from dotenv import load_dotenv

from core.config import load_config
from dedup import db

load_dotenv()
log = logging.getLogger(__name__)

IMAP_HOST = "imap-mail.outlook.com"
IMAP_PORT = 993

FOLDER_MAP = {
    "Rejected":       "Applications/Rejected",
    "In Review":      "Applications/In Review",
    "Next Step":      "Applications/Next Step",
    "Interview":      "Applications/Next Step/Interview/Todo",
    "Code Challenge": "Applications/Next Step/Code Challange/ToDo",
    "Offer":          "Applications/Offer",
}

APP_STATUS_MAP = {
    "Rejected":       "Rejected",
    "In Review":      "In Review",
    "Next Step":      "Next Step",
    "Interview":      "Interview Scheduled",
    "Code Challenge": "Code Challenge",
    "Offer":          "Offer",
}


def _connect(cfg: "dict | None" = None) -> imaplib.IMAP4_SSL:
    if cfg is None:
        cfg = load_config()
    addr = cfg.get("hotmail_address") or cfg["contact"]["email"]
    password = os.getenv("HOTMAIL_PASSWORD")
    if not password:
        raise EnvironmentError("HOTMAIL_PASSWORD is not set in .env")
    mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    mail.login(addr, password)
    return mail


def _imap_quoted(path: str) -> str:
    return f'"{path}"' if " " in path else path


def _ensure_folder(mail: imaplib.IMAP4_SSL, path: str) -> None:
    status, _ = mail.select(_imap_quoted(path))
    if status == "OK":
        return
    result, _ = mail.create(_imap_quoted(path))
    if result != "OK":
        raise OSError(f"Could not create IMAP folder: {path}")
    log.info("Created IMAP folder: %s", path)


def move(staging_id: int, override_folder: "str | None" = None,
         cfg: "dict | None" = None) -> None:
    """Move the email for staging_id to its target Outlook folder via IMAP."""
    record = db.get_staged_email(staging_id)
    if not record:
        raise ValueError(f"No staging record found: id={staging_id}")

    category = (override_folder
                or record.get("user_override_folder")
                or record["predicted_folder"])
    target_folder = FOLDER_MAP.get(category, FOLDER_MAP["Next Step"])
    source_folder = record["source_folder"]
    uid = str(record["email_uid"]).encode()
    now = datetime.utcnow().isoformat()

    mail = _connect(cfg)
    try:
        status, _ = mail.select(_imap_quoted(source_folder))
        if status != "OK":
            raise OSError(f"Cannot select source folder: {source_folder}")

        _ensure_folder(mail, target_folder)

        result, _ = mail.uid("COPY", uid, _imap_quoted(target_folder))
        if result != "OK":
            raise OSError(f"IMAP COPY failed → {target_folder}")

        mail.uid("STORE", uid, "+FLAGS", "\\Deleted")
        mail.expunge()

        db.log_email_move(staging_id, source_folder, target_folder, success=True)
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
        log.info("Moved staging_id=%d → %s", staging_id, target_folder)

    except Exception as exc:
        db.log_email_move(staging_id, source_folder, target_folder,
                          success=False, error=str(exc))
        db.update_email_staging_status(staging_id, "failed")
        log.error("Move failed staging_id=%d: %s", staging_id, exc)
        raise
    finally:
        try:
            mail.logout()
        except Exception:
            pass


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
