"""
email_executor.py — Graph API folder moves and application status updates.
"""

import logging
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

from dedup import db
from tracking import ms_graph

load_dotenv()
log = logging.getLogger(__name__)

FOLDER_MAP = {
    "Rejected":       "Inbox/Applications/Rejected",
    "In Review":      "Inbox/Applications/In Review",
    "Next Step":      "Inbox/Applications/Next Step",
    "Interview":      "Inbox/Applications/Next Step/Interview/Todo",
    "Code Challenge": "Inbox/Applications/Next Step/Code Challange/ToDo",
    "Offer":          "Inbox/Applications/Offer",
}

APP_STATUS_MAP = {
    "Rejected":       "Rejected",
    "In Review":      "In Review",
    "Next Step":      "Next Step",
    "Interview":      "Interview Scheduled",
    "Code Challenge": "Code Challenge",
    "Offer":          "Offer",
}


def move(staging_id: int, override_folder: "str | None" = None,
         cfg: "dict | None" = None, move_source: str = "manual") -> None:
    """Move the email for staging_id to its target Outlook folder via Graph API."""
    record = db.get_staged_email(staging_id)
    if not record:
        raise ValueError(f"No staging record found: id={staging_id}")

    category = (override_folder
                or record.get("user_override_folder")
                or record["predicted_folder"])
    target_folder = FOLDER_MAP.get(category, FOLDER_MAP["Next Step"])
    source_folder = record["source_folder"]
    graph_id = record.get("email_uid")   # Graph message ID stored in email_uid column
    now = datetime.now(timezone.utc).isoformat()

    try:
        ms_graph.move_message(graph_id, target_folder)

        db.log_email_move(staging_id, source_folder, target_folder, success=True,
                          move_source=move_source)
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

    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            # Message no longer exists at this ID — already moved or deleted.
            # Treat as success so it doesn't stay in the approval queue.
            log.warning("staging_id=%d: message not found (404), marking executed", staging_id)
            db.log_email_move(staging_id, source_folder, target_folder, success=True,
                              move_source=move_source)
            db.update_email_staging_status(staging_id, "executed", executed_at=now)
        else:
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
