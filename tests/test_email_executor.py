# tests/test_email_executor.py
import pytest
from unittest.mock import MagicMock, patch


def _insert_staging(temp_db, message_id="<exec@test.com>", folder="Inbox",
                    category="Rejected", uid="AAMkABC123"):
    return temp_db.stage_email({
        "email_uid": uid,
        "email_message_id": message_id,
        "sender": "hr@co.com",
        "subject": "Application Update",
        "body_preview": "Unfortunately...",
        "received_date": "2026-05-28",
        "source_folder": folder,
        "matched_app_id": None,
        "match_confidence": 0,
        "match_type": "unmatched",
        "predicted_folder": category,
        "confidence_score": 92,
        "classification_reason": "rejection",
    })


def test_move_calls_graph_move(temp_db):
    from tracking import email_executor
    sid = _insert_staging(temp_db)

    with patch("tracking.email_executor.ms_graph.move_message") as mock_move, \
         patch("tracking.email_executor.db", temp_db):
        email_executor.move(sid)

    mock_move.assert_called_once()
    _, target = mock_move.call_args[0]
    assert target == "Applications/Rejected"


def test_move_logs_success(temp_db):
    from tracking import email_executor
    sid = _insert_staging(temp_db, message_id="<log@test.com>")

    with patch("tracking.email_executor.ms_graph.move_message"), \
         patch("tracking.email_executor.db", temp_db):
        email_executor.move(sid)

    logs = temp_db.get_email_logs()
    assert len(logs) == 1
    assert logs[0]["success"] == 1


def test_move_updates_staging_status_to_executed(temp_db):
    from tracking import email_executor
    sid = _insert_staging(temp_db, message_id="<stat@test.com>")

    with patch("tracking.email_executor.ms_graph.move_message"), \
         patch("tracking.email_executor.db", temp_db):
        email_executor.move(sid)

    record = temp_db.get_staged_email(sid)
    assert record["status"] == "executed"


def test_move_updates_application_status(temp_db):
    from tracking import email_executor
    temp_db.log_application(
        {"company": "Acme", "title": "Dev", "url": "https://acme.com/j/1"},
        status="Applied",
    )
    with temp_db._conn() as conn:
        app_id = conn.execute(
            "SELECT id FROM applications WHERE job_url=?", ("https://acme.com/j/1",)
        ).fetchone()["id"]

    sid = temp_db.stage_email({
        "email_uid": "AAMkACME", "email_message_id": "<app@test.com>",
        "sender": "hr@acme.com", "subject": "Rejected",
        "body_preview": "not moving forward",
        "received_date": "2026-05-28", "source_folder": "Inbox",
        "matched_app_id": app_id, "match_confidence": 95,
        "match_type": "exact", "predicted_folder": "Rejected",
        "confidence_score": 92, "classification_reason": "rejection",
    })

    with patch("tracking.email_executor.ms_graph.move_message"), \
         patch("tracking.email_executor.db", temp_db):
        email_executor.move(sid)

    with temp_db._conn() as conn:
        row = conn.execute(
            "SELECT status FROM applications WHERE id=?", (app_id,)
        ).fetchone()
    assert row["status"] == "Rejected"


def test_approve_batch_returns_counts(temp_db):
    from tracking import email_executor
    sid1 = _insert_staging(temp_db, message_id="<b1@test.com>", uid="AAMkB1")
    sid2 = _insert_staging(temp_db, message_id="<b2@test.com>", uid="AAMkB2")

    with patch("tracking.email_executor.ms_graph.move_message"), \
         patch("tracking.email_executor.db", temp_db):
        result = email_executor.approve_batch([sid1, sid2])

    assert "succeeded" in result
    assert "failed" in result
    assert result["succeeded"] + result["failed"] == 2
