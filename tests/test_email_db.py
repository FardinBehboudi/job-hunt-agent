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


def test_log_application_returns_new_id_without_url(temp_db):
    app_id = temp_db.log_application({"company": "Acme", "title": "SWE"}, status="In Review")
    assert app_id is not None
    row = temp_db.get_application_by_id(app_id)
    assert row["company"] == "Acme"
    assert row["status"] == "In Review"


def test_log_application_returns_id_with_url_on_insert_and_update(temp_db):
    id1 = temp_db.log_application(
        {"company": "Acme", "title": "SWE", "url": "https://acme.com/j/1"}, status="Applied",
    )
    id2 = temp_db.log_application(
        {"company": "Acme", "title": "SWE", "url": "https://acme.com/j/1"}, status="Rejected",
    )
    assert id1 == id2  # same row, upserted


def test_log_application_uses_provided_date_applied(temp_db):
    app_id = temp_db.log_application(
        {"company": "Acme", "title": "SWE", "date_applied": "2026-08-01"}, status="In Review",
    )
    row = temp_db.get_application_by_id(app_id)
    assert row["date_applied"] == "2026-08-01"


def test_delete_application_removes_row(temp_db):
    app_id = temp_db.log_application({"company": "Acme", "title": "SWE"}, status="In Review")
    assert temp_db.delete_application(app_id) is True
    assert temp_db.get_application_by_id(app_id) is None


def test_delete_application_returns_false_when_missing(temp_db):
    assert temp_db.delete_application(999999) is False


def test_delete_application_unlinks_referencing_email_and_events(temp_db):
    # Regression: foreign_keys=ON blocks the delete unless referencing rows in
    # email_staging/upcoming_events are cleared first.
    app_id = temp_db.log_application({"company": "Acme", "title": "SWE"}, status="In Review")
    staging_id = temp_db.stage_email({
        "email_uid": "1", "email_message_id": "<a@test.com>",
        "sender": "hr@acme.com", "subject": "Confirmation", "body_preview": "",
        "received_date": "2026-08-29", "source_folder": "Inbox",
        "matched_app_id": app_id, "match_confidence": 90,
        "match_type": "exact", "predicted_folder": "In Review",
        "confidence_score": 90, "classification_reason": "",
    })
    temp_db.insert_upcoming_event({
        "event_type": "interview", "title": "Call", "description": "",
        "event_date": None, "event_time": None, "timezone": None,
        "priority": "medium", "app_id": app_id, "source_email_id": staging_id,
    })

    assert temp_db.delete_application(app_id) is True

    staged = temp_db.get_staged_email(staging_id)
    assert staged["matched_app_id"] is None
    assert temp_db.get_upcoming_events() == []


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
