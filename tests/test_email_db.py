# tests/test_email_db.py
import pytest
from datetime import datetime


def test_email_staging_table_created(temp_db):
    with temp_db._conn() as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='email_staging'"
        ).fetchone()
    assert row is not None


def test_upcoming_events_table_created(temp_db):
    with temp_db._conn() as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='upcoming_events'"
        ).fetchone()
    assert row is not None


def test_email_move_history_table_created(temp_db):
    with temp_db._conn() as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='email_move_history'"
        ).fetchone()
    assert row is not None


def test_stage_email_returns_id(temp_db):
    record = {
        "email_uid": "123",
        "email_message_id": "<abc@mail.com>",
        "sender": "recruiter@acme.com",
        "subject": "Your application",
        "body_preview": "We regret to inform you...",
        "received_date": "2026-05-28",
        "source_folder": "Inbox",
        "matched_app_id": None,
        "match_confidence": 0,
        "match_type": "unmatched",
        "predicted_folder": "Rejected",
        "confidence_score": 92,
        "classification_reason": "Contains rejection language",
    }
    result_id = temp_db.stage_email(record)
    assert isinstance(result_id, int)
    assert result_id > 0


def test_stage_email_dedup(temp_db):
    record = {
        "email_uid": "123", "email_message_id": "<same@mail.com>",
        "sender": "a@b.com", "subject": "X", "body_preview": "",
        "received_date": "", "source_folder": "Inbox",
        "matched_app_id": None, "match_confidence": 0,
        "match_type": "unmatched", "predicted_folder": "Uncertain",
        "confidence_score": 0, "classification_reason": "",
    }
    id1 = temp_db.stage_email(record)
    id2 = temp_db.stage_email(record)  # duplicate message_id — should be ignored
    assert id1 > 0
    assert id2 == 0  # INSERT OR IGNORE returns 0 lastrowid on conflict


def test_get_pending_emails_returns_pending(temp_db):
    temp_db.stage_email({
        "email_uid": "1", "email_message_id": "<p1@x.com>",
        "sender": "x@y.com", "subject": "Test", "body_preview": "",
        "received_date": "", "source_folder": "Inbox",
        "matched_app_id": None, "match_confidence": 0,
        "match_type": "unmatched", "predicted_folder": "Uncertain",
        "confidence_score": 30, "classification_reason": "",
    })
    rows = temp_db.get_pending_emails()
    assert len(rows) == 1
    assert rows[0]["status"] == "pending"


def test_update_email_staging_status(temp_db):
    eid = temp_db.stage_email({
        "email_uid": "2", "email_message_id": "<s1@x.com>",
        "sender": "a@b.com", "subject": "Hi", "body_preview": "",
        "received_date": "2026-05-28", "source_folder": "Inbox",
        "matched_app_id": None, "match_confidence": 0,
        "match_type": "unmatched", "predicted_folder": "In Review",
        "confidence_score": 75, "classification_reason": "confirmed",
    })
    temp_db.update_email_staging_status(eid, "executed",
                                         executed_at="2026-05-28T10:00:00")
    record = temp_db.get_staged_email(eid)
    assert record["status"] == "executed"
    assert record["executed_at"] == "2026-05-28T10:00:00"


def test_log_email_move(temp_db):
    eid = temp_db.stage_email({
        "email_uid": "3", "email_message_id": "<m1@x.com>",
        "sender": "a@b.com", "subject": "Hi", "body_preview": "",
        "received_date": "", "source_folder": "Inbox",
        "matched_app_id": None, "match_confidence": 0,
        "match_type": "unmatched", "predicted_folder": "Rejected",
        "confidence_score": 91, "classification_reason": "rejected",
    })
    temp_db.log_email_move(eid, "Inbox", "Applications/Rejected", success=True)
    logs = temp_db.get_email_logs()
    assert len(logs) == 1
    assert logs[0]["success"] == 1
    assert logs[0]["to_folder"] == "Applications/Rejected"


def test_get_application_companies(temp_db):
    temp_db.log_application(
        {"company": "Google", "title": "SWE", "url": "https://google.com/j/1"},
        status="Applied",
    )
    companies = temp_db.get_application_companies()
    names = [c["company"] for c in companies]
    assert "Google" in names


def test_get_staged_message_ids(temp_db):
    temp_db.stage_email({
        "email_uid": "4", "email_message_id": "<id123@mail.com>",
        "sender": "x@y.com", "subject": "S", "body_preview": "",
        "received_date": "", "source_folder": "Inbox",
        "matched_app_id": None, "match_confidence": 0,
        "match_type": "unmatched", "predicted_folder": "Uncertain",
        "confidence_score": 0, "classification_reason": "",
    })
    ids = temp_db.get_staged_message_ids()
    assert "<id123@mail.com>" in ids
