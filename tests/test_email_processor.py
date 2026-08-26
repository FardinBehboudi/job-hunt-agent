# tests/test_email_processor.py
import sys
import json
import pytest
from unittest.mock import MagicMock, patch


def _make_mock_client(category="Rejected", confidence=92, reason="rejection language"):
    client = MagicMock()
    resp = MagicMock()
    resp.content = [MagicMock()]
    resp.content[0].text = json.dumps({
        "category": category,
        "confidence": confidence,
        "reason": reason,
    })
    client.messages.create.return_value = resp
    return client


def test_classify_returns_category(temp_db):
    from tracking.email_processor import _classify
    client = _make_mock_client("Rejected", 92, "not moving forward")
    result = _classify(client, "Application Update", "Unfortunately we...")
    assert result["category"] == "Rejected"
    assert result["confidence"] == 92
    assert "not moving forward" in result["reason"]


def test_classify_fallback_on_bad_json(temp_db):
    from tracking.email_processor import _classify
    client = MagicMock()
    resp = MagicMock()
    resp.content = [MagicMock()]
    resp.content[0].text = "This is not JSON"
    client.messages.create.return_value = resp
    result = _classify(client, "Subject", "Body")
    assert result["category"] == "Uncertain"
    assert result["confidence"] == 0


def test_link_exact_match(temp_db):
    from tracking.email_processor import _link_to_application
    temp_db.log_application(
        {"company": "Acme Corp", "title": "SWE", "url": "https://acme.com/j/1"},
        status="Applied",
    )
    result = _link_to_application("hr@acme.com", "Your application", "", temp_db)
    assert result["match_type"] == "exact"
    assert result["matched_app_id"] is not None


def test_link_ambiguous_match(temp_db):
    from tracking.email_processor import _link_to_application
    temp_db.log_application(
        {"company": "Google", "title": "SWE", "url": "https://google.com/j/1"},
        status="Applied",
    )
    temp_db.log_application(
        {"company": "Google", "title": "PM", "url": "https://google.com/j/2"},
        status="Applied",
    )
    result = _link_to_application("recruiter@google.com", "Update", "", temp_db)
    assert result["match_type"] == "ambiguous"
    assert result["matched_app_id"] is None


def test_link_fuzzy_match_from_body(temp_db):
    from tracking.email_processor import _link_to_application
    temp_db.log_application(
        {"company": "Stripe", "title": "Engineer", "url": "https://stripe.com/j/1"},
        status="Applied",
    )
    result = _link_to_application(
        "noreply@greenhouse.io",
        "Application update",
        "Your application at Stripe has been reviewed",
        temp_db,
    )
    assert result["match_type"] == "fuzzy"
    assert result["matched_app_id"] is not None


def test_link_unmatched(temp_db):
    from tracking.email_processor import _link_to_application
    result = _link_to_application("recruiter@unknownco.com", "Hi", "No company here", temp_db)
    assert result["match_type"] == "unmatched"
    assert result["matched_app_id"] is None


def test_auto_execute_fires_at_threshold(temp_db):
    from tracking import email_processor
    staging_id = temp_db.stage_email({
        "email_uid": "99", "email_message_id": "<auto@test.com>",
        "sender": "hr@co.com", "subject": "Rejected", "body_preview": "not moving forward",
        "received_date": "2026-05-28", "source_folder": "Inbox",
        "matched_app_id": None, "match_confidence": 0,
        "match_type": "unmatched", "predicted_folder": "Rejected",
        "confidence_score": 92, "classification_reason": "rejection",
    })
    with patch("tracking.email_processor.email_executor") as mock_exec:
        mock_exec.move = MagicMock()
        email_processor._auto_execute(staging_id, "Rejected", None, "2026-05-28", "Rejected")
        mock_exec.move.assert_called_once_with(staging_id, move_source="auto")


def test_auto_execute_skips_below_threshold(temp_db):
    from tracking import email_processor
    staging_id = temp_db.stage_email({
        "email_uid": "100", "email_message_id": "<low@test.com>",
        "sender": "hr@co.com", "subject": "Update", "body_preview": "not sure",
        "received_date": "2026-05-28", "source_folder": "Inbox",
        "matched_app_id": None, "match_confidence": 0,
        "match_type": "unmatched", "predicted_folder": "Rejected",
        "confidence_score": 70, "classification_reason": "maybe rejected",
    })
    with patch("tracking.email_processor.email_executor") as mock_exec:
        mock_exec.move = MagicMock()
        email_processor._maybe_auto_execute(staging_id, "Rejected", None, "2026-05-28", "Update", threshold=90)
        mock_exec.move.assert_not_called()
