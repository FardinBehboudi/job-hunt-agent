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


def test_is_job_related_true_for_recruiting_domain(temp_db):
    from tracking.email_processor import _is_job_related
    assert _is_job_related("notifications@smartrecruiters.com", "Application Update")


def test_is_job_related_true_for_subject_keyword(temp_db):
    from tracking.email_processor import _is_job_related
    assert _is_job_related("f.behboudi7@gmail.com",
                            "Fwd: [Please Confirm] Google Meet Interview")


def test_is_job_related_false_for_unrelated_email(temp_db):
    from tracking.email_processor import _is_job_related
    assert not _is_job_related("no-reply@azure.com", "Welcome to your Azure pay-as-you-go account")
    assert not _is_job_related("play@draftfantasy.com", "New players have been added to the game!")


def test_is_job_related_true_for_keyword_in_sender_local_part(temp_db):
    # Regression: "bewerben" (German "to apply") sits in the local part of the
    # address, not the domain (lu.ch) — must not be dropped by a domain-only check.
    from tracking.email_processor import _is_job_related
    assert _is_job_related("noreply.bewerben@lu.ch",
                            "Eingangsbestätigung Senior Full Stack Developer (a)")


def test_is_job_related_true_for_german_verb_form(temp_db):
    # "bewerben"/"beworben" (verb forms) must match, not just "bewerbung"/"bewerber" (nouns).
    from tracking.email_processor import _is_job_related
    assert _is_job_related("hr@example.com", "Vielen Dank, dass Sie sich bei uns beworben haben")


def _make_routing_mock_client(routes: dict):
    """routes: {substring-of-system-prompt: response-dict}. Picks a response
    based on which system prompt the call used, so classify vs extraction
    calls in the same test get different canned answers."""
    client = MagicMock()

    def _create(**kwargs):
        system = kwargs.get("system", "")
        for marker, payload in routes.items():
            if marker in system:
                resp = MagicMock()
                resp.content = [MagicMock()]
                resp.content[0].text = json.dumps(payload)
                return resp
        raise AssertionError(f"No route matched system prompt: {system!r}")

    client.messages.create.side_effect = _create
    return client


def test_maybe_create_application_creates_new_app(temp_db, monkeypatch):
    from tracking import email_processor
    monkeypatch.setattr(email_processor, "db", temp_db)

    client = _make_routing_mock_client({
        "extract the company and job title": {"company": "Acme", "title": "Backend Engineer"},
    })
    msg = {"received_date": "2026-08-20T10:00:00Z"}

    app_id = email_processor._maybe_create_application(
        client, msg, "Your application to Acme", "We received your application", confidence=85,
    )

    assert app_id is not None
    row = temp_db.get_application_by_id(app_id)
    assert row["company"] == "Acme"
    assert row["role"] == "Backend Engineer"
    assert row["status"] == "In Review"
    assert row["date_applied"] == "2026-08-20"


def test_maybe_create_application_skips_below_confidence_floor(temp_db, monkeypatch):
    from tracking import email_processor
    monkeypatch.setattr(email_processor, "db", temp_db)
    client = MagicMock()

    app_id = email_processor._maybe_create_application(
        client, {"received_date": "2026-08-20"}, "subj", "body", confidence=50,
    )

    assert app_id is None
    client.messages.create.assert_not_called()  # never even tried extraction


def test_maybe_create_application_skips_when_company_unclear(temp_db, monkeypatch):
    from tracking import email_processor
    monkeypatch.setattr(email_processor, "db", temp_db)

    client = _make_routing_mock_client({
        "extract the company and job title": {"company": "", "title": ""},
    })

    app_id = email_processor._maybe_create_application(
        client, {"received_date": "2026-08-20"}, "subj", "body", confidence=95,
    )

    assert app_id is None


def test_run_creates_application_from_unmatched_in_review_email(temp_db, monkeypatch):
    from tracking import email_processor

    monkeypatch.setattr(email_processor, "db", temp_db)

    messages = [
        {"email_uid": "1", "email_message_id": "<confirm@test.com>",
         "sender": "noreply@newco.com", "subject": "Your application to NewCo received",
         "body_preview": "Thanks for applying to NewCo for the Backend Engineer role.",
         "received_date": "2026-08-27", "source_folder": "Inbox"},
    ]
    monkeypatch.setattr(
        email_processor.ms_graph, "get_messages",
        lambda folder, max_messages=200: messages if folder == "Inbox" else [],
    )
    monkeypatch.setattr(email_processor.email_executor, "move", MagicMock())

    client = _make_routing_mock_client({
        "You classify job-application-related emails": {
            "category": "In Review", "confidence": 92, "reason": "application received",
        },
        "extract the company and job title": {"company": "NewCo", "title": "Backend Engineer"},
    })
    monkeypatch.setattr(email_processor.anthropic, "Anthropic", lambda api_key=None: client)

    email_processor.run({})

    companies = [c["company"] for c in temp_db.get_application_companies()]
    assert "NewCo" in companies

    pending = temp_db.get_pending_emails()
    assert len(pending) == 1
    assert pending[0]["matched_app_id"] is not None
    assert pending[0]["company"] == "NewCo"


def test_run_does_not_duplicate_application_when_match_is_ambiguous(temp_db, monkeypatch):
    # Regression: two existing "Vesterling AG" rows (e.g. an old rejected one plus
    # a fresh one from the applier) make _link_to_application report "ambiguous"
    # (matched_app_id=None, same as a true non-match) — a confirmation email for
    # that company must NOT spawn a third row just because matched_app_id is None.
    from tracking import email_processor

    monkeypatch.setattr(email_processor, "db", temp_db)

    temp_db.log_application(
        {"company": "Vesterling AG", "title": "Java Dev"}, status="Rejected",
    )
    temp_db.log_application(
        {"company": "Vesterling AG", "title": "Requirements Engineer",
         "url": "https://linkedin.com/jobs/view/1"},
        status="Applied",
    )

    messages = [
        {"email_uid": "1", "email_message_id": "<confirm@test.com>",
         "sender": "Welcome@Vesterling.com",
         "subject": "Ihre Onlinebewerbung bei Vesterling - Eingangsbestätigung",
         "body_preview": "Vielen Dank fuer Ihre Bewerbung bei Vesterling.",
         "received_date": "2026-08-29", "source_folder": "Inbox"},
    ]
    monkeypatch.setattr(
        email_processor.ms_graph, "get_messages",
        lambda folder, max_messages=200: messages if folder == "Inbox" else [],
    )
    monkeypatch.setattr(email_processor.email_executor, "move", MagicMock())

    client = _make_routing_mock_client({
        "You classify job-application-related emails": {
            "category": "In Review", "confidence": 97, "reason": "application received",
        },
        "extract the company and job title": {"company": "Vesterling", "title": "Requirements Engineer"},
    })
    monkeypatch.setattr(email_processor.anthropic, "Anthropic", lambda api_key=None: client)

    email_processor.run({})

    companies = [c["company"] for c in temp_db.get_application_companies()]
    assert companies.count("Vesterling AG") == 2  # still just the original two, no third row


def test_is_recommendation_digest(temp_db):
    from tracking.email_processor import _is_recommendation_digest
    assert _is_recommendation_digest("info@jobagent.stepstone.de")
    assert _is_recommendation_digest("dlrdeutsch-jobnotification@noreply12.jobs2web.com")
    assert not _is_recommendation_digest("notifications@smartrecruiters.com")


def test_run_skips_unmatched_non_job_email(temp_db, monkeypatch):
    from tracking import email_processor

    monkeypatch.setattr(email_processor, "db", temp_db)

    temp_db.log_application(
        {"company": "Acme Corp", "title": "SWE", "url": "https://acme.com/j/1"},
        status="Applied",
    )

    messages = [
        {"email_uid": "1", "email_message_id": "<noise@test.com>",
         "sender": "no-reply@azure.com", "subject": "Welcome to your Azure account",
         "body_preview": "", "received_date": "2026-08-27", "source_folder": "Inbox"},
        {"email_uid": "2", "email_message_id": "<signal@test.com>",
         "sender": "hr@acme.com", "subject": "Your application at Acme Corp",
         "body_preview": "Unfortunately we will not move forward.",
         "received_date": "2026-08-27", "source_folder": "Inbox"},
    ]
    monkeypatch.setattr(
        email_processor.ms_graph, "get_messages",
        lambda folder, max_messages=200: messages if folder == "Inbox" else [],
    )
    monkeypatch.setattr(email_processor.email_executor, "move", MagicMock())

    mock_client = _make_mock_client("Rejected", 92, "rejection language")
    monkeypatch.setattr(email_processor.anthropic, "Anthropic", lambda api_key=None: mock_client)

    email_processor.run({})

    staged_subjects = {r["subject"] for r in temp_db.get_pending_emails()}
    assert "Your application at Acme Corp" in staged_subjects       # matched -> classified & staged
    assert "Welcome to your Azure account" not in staged_subjects   # unmatched, no job signal -> skipped
    assert mock_client.messages.create.call_count == 1  # only the matched email hit Claude


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
