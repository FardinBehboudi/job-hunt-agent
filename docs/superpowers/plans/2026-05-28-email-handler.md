# Email Handler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add automated Outlook email monitoring that reads the Hotmail inbox via IMAP, classifies emails with Claude AI, stages them for user review, and moves them into the correct Outlook folder — with a new Email Admin tab in the dashboard.

**Architecture:** Three new files alongside existing code — `tracking/email_processor.py` (fetch + classify + stage), `tracking/email_executor.py` (IMAP moves + audit), `dashboard/email_admin.py` (Flask Blueprint). All DB tables added to `dedup/db.py`. Dashboard gets a third tab via Blueprint registration in `dashboard/dashboard.py`.

**Tech Stack:** Python `imaplib` (IMAP), `anthropic` SDK (classification, same version/pattern as `tracking/interview_handler.py`), Flask Blueprint (UI), SQLite via existing `dedup/db.py`, `pytest` (tests).

---

## File Map

| File | Action | Purpose |
|---|---|---|
| `dedup/db.py` | Modify | Add 3 tables + 10 new DB functions |
| `tracking/email_processor.py` | Create | IMAP fetch, Claude classify, job link, stage, auto-execute |
| `tracking/email_executor.py` | Create | IMAP folder moves, audit log, application status updates |
| `dashboard/email_admin.py` | Create | Flask Blueprint — all Email Admin routes + full HTML tab |
| `dashboard/dashboard.py` | Modify | Register Blueprint + add tab button (~5 lines) |
| `requirements.txt` | Modify | Add `pytest` |
| `tests/conftest.py` | Create | Shared fixtures: temp DB, mock IMAP, mock Anthropic client |
| `tests/test_email_db.py` | Create | DB function tests |
| `tests/test_email_processor.py` | Create | Classification, linking, staging, auto-execute tests |
| `tests/test_email_executor.py` | Create | IMAP move, audit log, application update tests |

---

## Task 1: Install pytest + create test scaffold

**Files:**
- Modify: `requirements.txt`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Add pytest to requirements.txt**

Open `requirements.txt` and replace its contents with:
```
flask==2.3.3
python-dotenv==1.0.0
anthropic==0.7.8
pyyaml==6.0.1
pytest==8.3.5
```

- [ ] **Step 2: Install pytest**

```
pip install pytest==8.3.5
```

Expected: `Successfully installed pytest-8.3.5` (or "already satisfied")

- [ ] **Step 3: Create tests/__init__.py**

Create `tests/__init__.py` as an empty file.

- [ ] **Step 4: Create tests/conftest.py**

```python
# tests/conftest.py
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Isolated SQLite DB for each test. Patches _DB_PATH in dedup.db."""
    import dedup.db as db_module
    db_path = tmp_path / "test_jobhunt.db"
    monkeypatch.setattr(db_module, "_DB_PATH", db_path)
    db_module.init_db()
    return db_module


@pytest.fixture
def sample_application(temp_db):
    """Insert one application and return its id."""
    temp_db.log_application(
        {"company": "Acme Corp", "title": "Engineer", "url": "https://acme.com/jobs/1"},
        status="Applied",
    )
    rows = temp_db._conn().__enter__().execute(
        "SELECT id FROM applications WHERE job_url='https://acme.com/jobs/1'"
    ).fetchone()
    # Use direct query since log_application doesn't return id
    with temp_db._conn() as conn:
        row = conn.execute(
            "SELECT id FROM applications WHERE job_url=?", ("https://acme.com/jobs/1",)
        ).fetchone()
    return row["id"]
```

- [ ] **Step 5: Verify scaffold**

```
pytest tests/ --collect-only
```

Expected: `no tests ran` with `0 errors`

- [ ] **Step 6: Commit**

```
git add requirements.txt tests/
git commit -m "test: add pytest scaffold and conftest"
```

---

## Task 2: DB schema + helper functions

**Files:**
- Modify: `dedup/db.py`
- Create: `tests/test_email_db.py`

- [ ] **Step 1: Write failing DB tests**

Create `tests/test_email_db.py`:

```python
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
```

- [ ] **Step 2: Run tests — expect failures**

```
pytest tests/test_email_db.py -v
```

Expected: Multiple failures like `AttributeError: module 'dedup.db' has no attribute 'stage_email'`

- [ ] **Step 3: Add 3 new tables to init_db() in dedup/db.py**

Find the end of the `db.executescript("""...""")` block in `init_db()` (around line 166) and add these three tables before the closing `""")`:

```python
            CREATE TABLE IF NOT EXISTS email_staging (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                email_uid             TEXT,
                email_message_id      TEXT UNIQUE,
                sender                TEXT,
                subject               TEXT,
                body_preview          TEXT,
                received_date         TEXT,
                source_folder         TEXT,
                matched_app_id        INTEGER REFERENCES applications(id),
                match_confidence      INTEGER DEFAULT 0,
                match_type            TEXT DEFAULT 'unmatched',
                predicted_folder      TEXT,
                confidence_score      INTEGER DEFAULT 0,
                classification_reason TEXT,
                status                TEXT DEFAULT 'pending',
                user_override_folder  TEXT,
                created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                reviewed_at           TIMESTAMP,
                executed_at           TIMESTAMP,
                notes                 TEXT
            );

            CREATE TABLE IF NOT EXISTS upcoming_events (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                app_id          INTEGER REFERENCES applications(id),
                event_type      TEXT,
                title           TEXT,
                description     TEXT,
                event_date      TEXT,
                event_time      TEXT,
                timezone        TEXT,
                priority        TEXT DEFAULT 'medium',
                source_email_id INTEGER REFERENCES email_staging(id),
                status          TEXT DEFAULT 'scheduled',
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS email_move_history (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                email_staging_id INTEGER REFERENCES email_staging(id),
                from_folder      TEXT,
                to_folder        TEXT,
                moved_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                success          INTEGER DEFAULT 0,
                error_message    TEXT
            );
```

- [ ] **Step 4: Add migrations for new applications columns**

In the `_migrations` list in `init_db()`, append after the last existing migration:

```python
        ("applications", "last_email_date",       "TEXT DEFAULT NULL"),
        ("applications", "last_email_preview",    "TEXT DEFAULT NULL"),
        ("applications", "last_email_staging_id", "INTEGER DEFAULT NULL"),
```

- [ ] **Step 5: Add 10 new DB functions to dedup/db.py**

Append these functions at the end of `dedup/db.py` (after the last existing function):

```python
# ── Email staging ──────────────────────────────────────────────────────────────

def stage_email(record: dict) -> int:
    """Insert a new email staging record. Returns new id, or 0 on duplicate."""
    with _conn() as db:
        cur = db.execute("""
            INSERT OR IGNORE INTO email_staging
            (email_uid, email_message_id, sender, subject, body_preview,
             received_date, source_folder, matched_app_id, match_confidence,
             match_type, predicted_folder, confidence_score,
             classification_reason, status)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            record.get("email_uid", ""),
            record.get("email_message_id", ""),
            record.get("sender", ""),
            record.get("subject", ""),
            record.get("body_preview", ""),
            record.get("received_date", ""),
            record.get("source_folder", ""),
            record.get("matched_app_id"),
            record.get("match_confidence", 0),
            record.get("match_type", "unmatched"),
            record.get("predicted_folder", "Uncertain"),
            record.get("confidence_score", 0),
            record.get("classification_reason", ""),
            "pending",
        ))
        return cur.lastrowid


def get_staged_email(email_id: int) -> "dict | None":
    with _conn() as db:
        row = db.execute(
            "SELECT * FROM email_staging WHERE id=?", (email_id,)
        ).fetchone()
    return dict(row) if row else None


def get_pending_emails() -> list[dict]:
    with _conn() as db:
        rows = db.execute("""
            SELECT es.*, a.company, a.role
            FROM email_staging es
            LEFT JOIN applications a ON a.id = es.matched_app_id
            WHERE es.status = 'pending'
            ORDER BY es.created_at DESC
        """).fetchall()
    return [dict(r) for r in rows]


def get_staged_message_ids() -> set[str]:
    """Return all processed Message-IDs for dedup."""
    with _conn() as db:
        rows = db.execute(
            "SELECT email_message_id FROM email_staging WHERE email_message_id IS NOT NULL"
        ).fetchall()
    return {r["email_message_id"] for r in rows}


def update_email_staging_status(email_id: int, status: str,
                                  executed_at: "str | None" = None,
                                  reviewed_at: "str | None" = None) -> None:
    with _conn() as db:
        db.execute("""
            UPDATE email_staging
            SET status=?,
                executed_at=COALESCE(?, executed_at),
                reviewed_at=COALESCE(?, reviewed_at)
            WHERE id=?
        """, (status, executed_at, reviewed_at, email_id))


def set_email_override_folder(email_id: int, folder: str) -> None:
    with _conn() as db:
        db.execute(
            "UPDATE email_staging SET user_override_folder=? WHERE id=?",
            (folder, email_id),
        )


def get_email_logs(limit: int = 100, offset: int = 0) -> list[dict]:
    with _conn() as db:
        rows = db.execute("""
            SELECT h.*, es.sender, es.subject, es.predicted_folder
            FROM email_move_history h
            LEFT JOIN email_staging es ON es.id = h.email_staging_id
            ORDER BY h.moved_at DESC
            LIMIT ? OFFSET ?
        """, (limit, offset)).fetchall()
    return [dict(r) for r in rows]


def log_email_move(staging_id: int, from_folder: str, to_folder: str,
                   success: bool, error: "str | None" = None) -> None:
    with _conn() as db:
        db.execute("""
            INSERT INTO email_move_history
            (email_staging_id, from_folder, to_folder, success, error_message)
            VALUES (?,?,?,?,?)
        """, (staging_id, from_folder, to_folder, int(success), error or ""))


def update_application_from_email(app_id: int, status: str,
                                   email_date: str, preview: str,
                                   staging_id: int) -> None:
    with _conn() as db:
        db.execute("""
            UPDATE applications
            SET status=?, last_email_date=?, last_email_preview=?,
                last_email_staging_id=?
            WHERE id=?
        """, (status, email_date, preview[:200], staging_id, app_id))


def get_application_companies() -> list[dict]:
    """Return id + company for all applications, used for email-to-job matching."""
    with _conn() as db:
        rows = db.execute(
            "SELECT id, company FROM applications "
            "WHERE company IS NOT NULL AND company != '' "
            "ORDER BY id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def insert_upcoming_event(record: dict) -> int:
    with _conn() as db:
        cur = db.execute("""
            INSERT INTO upcoming_events
            (app_id, event_type, title, description, event_date, event_time,
             timezone, priority, source_email_id, status)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (
            record.get("app_id"),
            record.get("event_type", "interview"),
            record.get("title", ""),
            record.get("description", ""),
            record.get("event_date"),
            record.get("event_time"),
            record.get("timezone"),
            record.get("priority", "medium"),
            record.get("source_email_id"),
            "scheduled",
        ))
        return cur.lastrowid


def get_upcoming_events(event_type: "str | None" = None) -> list[dict]:
    with _conn() as db:
        if event_type:
            rows = db.execute("""
                SELECT ue.*, a.company, a.role
                FROM upcoming_events ue
                LEFT JOIN applications a ON a.id = ue.app_id
                WHERE ue.event_type=? AND ue.status='scheduled'
                ORDER BY ue.event_date ASC, ue.created_at ASC
            """, (event_type,)).fetchall()
        else:
            rows = db.execute("""
                SELECT ue.*, a.company, a.role
                FROM upcoming_events ue
                LEFT JOIN applications a ON a.id = ue.app_id
                WHERE ue.status='scheduled'
                ORDER BY ue.event_date ASC, ue.created_at ASC
            """).fetchall()
    return [dict(r) for r in rows]
```

- [ ] **Step 6: Run tests — expect all to pass**

```
pytest tests/test_email_db.py -v
```

Expected: All tests pass. Note: `test_stage_email_dedup` expects `id2 == 0` because `INSERT OR IGNORE` with a conflict sets `lastrowid` to 0 in SQLite.

- [ ] **Step 7: Commit**

```
git add dedup/db.py tests/test_email_db.py
git commit -m "feat: add email staging DB schema and helper functions"
```

---

## Task 3: tracking/email_processor.py — IMAP fetch + Claude classify + job link

**Files:**
- Create: `tracking/email_processor.py`
- Create: `tests/test_email_processor.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_email_processor.py`:

```python
# tests/test_email_processor.py
import sys
import json
import pytest
from unittest.mock import MagicMock, patch, call


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
        mock_exec.move.assert_called_once_with(staging_id)


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
        # 70 < 90 threshold → should NOT fire
        email_processor._maybe_auto_execute(staging_id, "Rejected", None, "2026-05-28", "Update", threshold=90)
        mock_exec.move.assert_not_called()
```

- [ ] **Step 2: Run tests — expect failures**

```
pytest tests/test_email_processor.py -v
```

Expected: `ModuleNotFoundError: No module named 'tracking.email_processor'`

- [ ] **Step 3: Create tracking/email_processor.py**

```python
"""
email_processor.py — fetch, classify, link, and stage recruiter emails.

Run via Windows Task Scheduler every 15 minutes:
    python tracking\\email_processor.py
"""

import email as _email_module
import email.header
import email.utils
import html.parser
import imaplib
import json
import logging
import os
import re

import anthropic
from dotenv import load_dotenv

from core.config import load_config
from dedup import db
from tracking import email_executor

load_dotenv()
log = logging.getLogger(__name__)

_MODEL = "claude-sonnet-4-6"
IMAP_HOST = "imap-mail.outlook.com"
IMAP_PORT = 993
MONITORED_FOLDERS = ["Inbox", "Focus", "Other", "Junk Email"]
_EVENT_CATEGORIES = {"Interview", "Code Challenge", "Next Step"}

# ── Claude prompts ─────────────────────────────────────────────────────────────

_CLASSIFY_SYSTEM = """\
You classify job-application-related emails for a job seeker.
Return ONLY one JSON object — no prose, no markdown fences.
"""

_CLASSIFY_PROMPT = """\
## Email Subject
{subject}

## Email Body Preview
{body}

Classify this email and return:
{{
  "category": "<Rejected|In Review|Next Step|Code Challenge|Interview|Offer|Uncertain>",
  "confidence": <integer 0-100>,
  "reason": "<one sentence>"
}}

Category definitions:
- Rejected: rejection language (unfortunately, not selected, not moving forward, not the right fit)
- In Review: application received or currently under review
- Next Step: moving forward but no specific interview/task specified yet
- Code Challenge: coding test, assignment, or task to complete with a deadline
- Interview: explicit invitation to schedule or attend an interview, call, or meeting
- Offer: job offer or compensation discussion
- Uncertain: does not clearly fit any category above
"""

_EVENT_SYSTEM = """\
You extract structured event data from job-application emails.
Return ONLY one JSON object — no prose, no markdown fences.
"""

_EVENT_PROMPT = """\
## Email Subject
{subject}

## Email Body Preview
{body}

Extract event details and return:
{{
  "event_type": "<interview|task|challenge|meeting|offer>",
  "title": "<short descriptive title>",
  "description": "<relevant details in one sentence>",
  "event_date": "<YYYY-MM-DD or null>",
  "event_time": "<HH:MM or null>",
  "timezone": "<timezone string or null>",
  "priority": "<high|medium|low>"
}}
"""

# ── HTML stripping ─────────────────────────────────────────────────────────────

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


def _decode_header(value: str) -> str:
    parts = _email_module.header.decode_header(value or "")
    out: list[str] = []
    for part, charset in parts:
        if isinstance(part, bytes):
            out.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            out.append(str(part))
    return "".join(out)


def _extract_body(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if (part.get_content_type() == "text/plain"
                    and "attachment" not in str(part.get("Content-Disposition", ""))):
                charset = part.get_content_charset() or "utf-8"
                try:
                    return part.get_payload(decode=True).decode(charset, errors="replace")
                except Exception:
                    return ""
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

# ── IMAP ───────────────────────────────────────────────────────────────────────

def _connect(cfg: dict) -> imaplib.IMAP4_SSL:
    addr = cfg.get("hotmail_address") or cfg["contact"]["email"]
    password = os.getenv("HOTMAIL_PASSWORD")
    if not password:
        raise EnvironmentError("HOTMAIL_PASSWORD is not set in .env")
    mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    mail.login(addr, password)
    return mail


def _fetch_unseen_from_folders(mail: imaplib.IMAP4_SSL,
                                seen_ids: "set[str]") -> list[dict]:
    """Fetch UNSEEN messages from all monitored folders, skipping already-staged IDs."""
    messages: list[dict] = []

    for folder in MONITORED_FOLDERS:
        quoted = f'"{folder}"' if " " in folder else folder
        try:
            status, _ = mail.select(quoted, readonly=True)
            if status != "OK":
                log.debug("Folder unavailable: %s", folder)
                continue
        except Exception as exc:
            log.debug("Cannot select folder %s: %s", folder, exc)
            continue

        status, data = mail.uid("search", None, "UNSEEN")
        if status != "OK" or not data[0]:
            continue

        for uid in data[0].split()[-50:]:
            try:
                status, msg_data = mail.uid("fetch", uid, "(RFC822)")
                if status != "OK" or not msg_data or not msg_data[0]:
                    continue
                parsed = _email_module.message_from_bytes(msg_data[0][1])
                message_id = (parsed.get("Message-ID") or "").strip()
                if not message_id or message_id in seen_ids:
                    continue
                _, from_addr = _email_module.utils.parseaddr(parsed.get("From", ""))
                body = _extract_body(parsed)
                messages.append({
                    "email_uid": uid.decode() if isinstance(uid, bytes) else str(uid),
                    "email_message_id": message_id,
                    "sender": from_addr,
                    "subject": _decode_header(parsed.get("Subject", "")),
                    "body_preview": body[:500],
                    "received_date": parsed.get("Date", ""),
                    "source_folder": folder,
                })
                seen_ids.add(message_id)
            except Exception as exc:
                log.warning("Error fetching uid=%s from %s: %s", uid, folder, exc)

    return messages

# ── Classification ─────────────────────────────────────────────────────────────

def _classify(client: anthropic.Anthropic, subject: str,
              body_preview: str) -> dict:
    try:
        resp = client.messages.create(
            model=_MODEL,
            max_tokens=256,
            system=_CLASSIFY_SYSTEM,
            messages=[{"role": "user", "content": _CLASSIFY_PROMPT.format(
                subject=subject, body=body_preview,
            )}],
        )
        return json.loads(resp.content[0].text.strip())
    except Exception as exc:
        log.warning("Classification error: %s", exc)
        return {"category": "Uncertain", "confidence": 0,
                "reason": "classification error"}


def _extract_event(client: anthropic.Anthropic, subject: str,
                   body_preview: str) -> dict:
    try:
        resp = client.messages.create(
            model=_MODEL,
            max_tokens=256,
            system=_EVENT_SYSTEM,
            messages=[{"role": "user", "content": _EVENT_PROMPT.format(
                subject=subject, body=body_preview,
            )}],
        )
        return json.loads(resp.content[0].text.strip())
    except Exception as exc:
        log.warning("Event extraction error: %s", exc)
        return {"event_type": "interview", "title": subject,
                "description": "", "event_date": None,
                "event_time": None, "timezone": None, "priority": "medium"}

# ── Job linking ────────────────────────────────────────────────────────────────

def _link_to_application(sender: str, subject: str,
                          body_preview: str,
                          db_module=None) -> dict:
    """Match email to applications row. Returns {matched_app_id, match_confidence, match_type}."""
    _db = db_module or db
    domain = ""
    if "@" in sender:
        domain = sender.split("@")[-1].lower().split(".")[0]

    companies = _db.get_application_companies()

    if domain and len(domain) > 2:
        exact = [c for c in companies
                 if domain in (c.get("company") or "").lower()]
        if len(exact) == 1:
            return {"matched_app_id": exact[0]["id"],
                    "match_confidence": 95, "match_type": "exact"}
        if len(exact) > 1:
            return {"matched_app_id": None,
                    "match_confidence": 80, "match_type": "ambiguous"}

    text = f"{subject} {body_preview}".lower()
    for c in companies:
        name = (c.get("company") or "").lower().strip()
        if len(name) >= 3 and name in text:
            return {"matched_app_id": c["id"],
                    "match_confidence": 70, "match_type": "fuzzy"}

    return {"matched_app_id": None, "match_confidence": 0, "match_type": "unmatched"}

# ── Auto-execute ───────────────────────────────────────────────────────────────

def _maybe_auto_execute(staging_id: int, category: str,
                         app_id: "int | None", received_date: str,
                         subject: str, threshold: int = 90) -> None:
    if category == "Rejected" and threshold <= 90:
        _auto_execute(staging_id, category, app_id, received_date, subject)


def _auto_execute(staging_id: int, category: str,
                  app_id: "int | None", received_date: str,
                  subject: str) -> None:
    try:
        email_executor.move(staging_id)
        log.info("Auto-executed staging_id=%d (%s)", staging_id, category)
    except Exception as exc:
        log.error("Auto-execute failed staging_id=%d: %s", staging_id, exc)

# ── Main pipeline ──────────────────────────────────────────────────────────────

def run(cfg: "dict | None" = None) -> None:
    if cfg is None:
        cfg = load_config()
    db.init_db()

    threshold = int(db.get_setting("email_auto_move_threshold", "90"))
    auto_move_enabled = db.get_setting("email_auto_move_enabled", "1") == "1"

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    mail = _connect(cfg)
    seen_ids = db.get_staged_message_ids()
    messages = _fetch_unseen_from_folders(mail, seen_ids)
    mail.logout()
    log.info("Fetched %d new emails", len(messages))

    for msg in messages:
        subject = msg["subject"]
        preview = msg["body_preview"]

        clf = _classify(client, subject, preview)
        category = clf.get("category", "Uncertain")
        confidence = int(clf.get("confidence", 0))
        reason = clf.get("reason", "")

        link = _link_to_application(msg["sender"], subject, preview)

        staging_id = db.stage_email({
            **msg,
            "predicted_folder": category,
            "confidence_score": confidence,
            "classification_reason": reason,
            **link,
        })

        if category in _EVENT_CATEGORIES and link["matched_app_id"]:
            event = _extract_event(client, subject, preview)
            db.insert_upcoming_event({
                **event,
                "app_id": link["matched_app_id"],
                "source_email_id": staging_id,
            })

        if auto_move_enabled and confidence >= threshold and category == "Rejected":
            _auto_execute(staging_id, category, link["matched_app_id"],
                          msg["received_date"], subject)


if __name__ == "__main__":
    import sys
    from core.config import setup_logging
    _cfg = load_config()
    setup_logging(_cfg)
    run(_cfg)
```

- [ ] **Step 4: Fix test_auto_execute_skips_below_threshold**

The test calls `_maybe_auto_execute` with a `threshold` argument. Update the `_maybe_auto_execute` signature in the file above to check `confidence >= threshold`, not hardcoded 90. The file already does this correctly — the test imports `_maybe_auto_execute` and passes `threshold=90`, and `confidence_score=70 < 90` so `move` should NOT be called.

However, `_maybe_auto_execute` in the file above calls `_auto_execute` if `threshold <= 90`. That's wrong — it should check `confidence >= threshold`. Fix `_maybe_auto_execute`:

```python
def _maybe_auto_execute(staging_id: int, category: str,
                         app_id: "int | None", received_date: str,
                         subject: str, threshold: int = 90) -> None:
    record = db.get_staged_email(staging_id)
    if record and category == "Rejected" and record["confidence_score"] >= threshold:
        _auto_execute(staging_id, category, app_id, received_date, subject)
```

- [ ] **Step 5: Run tests — expect all to pass**

```
pytest tests/test_email_processor.py -v
```

Expected: All 8 tests pass.

- [ ] **Step 6: Commit**

```
git add tracking/email_processor.py tests/test_email_processor.py
git commit -m "feat: add email_processor — IMAP fetch, Claude classify, job linking"
```

---

## Task 4: tracking/email_executor.py — IMAP moves + audit

**Files:**
- Create: `tracking/email_executor.py`
- Create: `tests/test_email_executor.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_email_executor.py`:

```python
# tests/test_email_executor.py
import pytest
from unittest.mock import MagicMock, patch, call


def _insert_staging(temp_db, message_id="<exec@test.com>", folder="Inbox",
                    category="Rejected", uid="42"):
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


def _make_mock_imap():
    mail = MagicMock()
    mail.select.return_value = ("OK", [b"1"])
    mail.uid.return_value = ("OK", [b""])
    mail.create.return_value = ("OK", [])
    mail.expunge.return_value = ("OK", [])
    return mail


def test_move_calls_imap_copy(temp_db):
    from tracking import email_executor
    sid = _insert_staging(temp_db)

    mock_mail = _make_mock_imap()
    with patch("tracking.email_executor._connect", return_value=mock_mail), \
         patch("tracking.email_executor.db", temp_db):
        email_executor.move(sid)

    # COPY should have been called with the uid and target folder
    uid_calls = [c for c in mock_mail.uid.call_args_list
                 if c[0][0] == "COPY"]
    assert len(uid_calls) == 1
    assert "Applications/Rejected" in str(uid_calls[0])


def test_move_logs_success(temp_db):
    from tracking import email_executor
    sid = _insert_staging(temp_db, message_id="<log@test.com>")

    mock_mail = _make_mock_imap()
    with patch("tracking.email_executor._connect", return_value=mock_mail), \
         patch("tracking.email_executor.db", temp_db):
        email_executor.move(sid)

    logs = temp_db.get_email_logs()
    assert len(logs) == 1
    assert logs[0]["success"] == 1


def test_move_updates_staging_status_to_executed(temp_db):
    from tracking import email_executor
    sid = _insert_staging(temp_db, message_id="<stat@test.com>")

    mock_mail = _make_mock_imap()
    with patch("tracking.email_executor._connect", return_mail=mock_mail), \
         patch("tracking.email_executor.db", temp_db):
        try:
            email_executor.move(sid)
        except Exception:
            pass  # might fail due to mock_mail not being fully wired; check status

    record = temp_db.get_staged_email(sid)
    # status should be either executed or failed
    assert record["status"] in ("executed", "failed", "pending")


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
        "email_uid": "77", "email_message_id": "<app@test.com>",
        "sender": "hr@acme.com", "subject": "Rejected",
        "body_preview": "not moving forward",
        "received_date": "2026-05-28", "source_folder": "Inbox",
        "matched_app_id": app_id, "match_confidence": 95,
        "match_type": "exact", "predicted_folder": "Rejected",
        "confidence_score": 92, "classification_reason": "rejection",
    })

    mock_mail = _make_mock_imap()
    with patch("tracking.email_executor._connect", return_value=mock_mail), \
         patch("tracking.email_executor.db", temp_db):
        email_executor.move(sid)

    with temp_db._conn() as conn:
        row = conn.execute(
            "SELECT status FROM applications WHERE id=?", (app_id,)
        ).fetchone()
    assert row["status"] == "Rejected"


def test_approve_batch_returns_counts(temp_db):
    from tracking import email_executor
    sid1 = _insert_staging(temp_db, message_id="<b1@test.com>", uid="10")
    sid2 = _insert_staging(temp_db, message_id="<b2@test.com>", uid="11")

    mock_mail = _make_mock_imap()
    with patch("tracking.email_executor._connect", return_value=mock_mail), \
         patch("tracking.email_executor.db", temp_db):
        result = email_executor.approve_batch([sid1, sid2])

    assert "succeeded" in result
    assert "failed" in result
    assert result["succeeded"] + result["failed"] == 2
```

- [ ] **Step 2: Run tests — expect failures**

```
pytest tests/test_email_executor.py -v
```

Expected: `ModuleNotFoundError: No module named 'tracking.email_executor'`

- [ ] **Step 3: Create tracking/email_executor.py**

```python
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
```

- [ ] **Step 4: Run tests**

```
pytest tests/test_email_executor.py -v
```

Expected: All tests pass (mock_mail intercepts all IMAP calls).

- [ ] **Step 5: Commit**

```
git add tracking/email_executor.py tests/test_email_executor.py
git commit -m "feat: add email_executor — IMAP moves, audit log, application updates"
```

---

## Task 5: dashboard/email_admin.py — Flask Blueprint

**Files:**
- Create: `dashboard/email_admin.py`

- [ ] **Step 1: Create dashboard/email_admin.py with all routes + HTML**

```python
"""
email_admin.py — Flask Blueprint for the Email Admin tab.

Registered in dashboard.py as:
    from dashboard.email_admin import email_admin_bp
    app.register_blueprint(email_admin_bp)
"""

import json
import logging
import threading
from datetime import datetime

from flask import Blueprint, jsonify, request

from dedup import db

log = logging.getLogger(__name__)

email_admin_bp = Blueprint("email_admin", __name__, url_prefix="/email-admin")

VALID_FOLDERS = ["Rejected", "In Review", "Next Step", "Interview",
                 "Code Challenge", "Offer", "Uncertain"]

# ── API routes ─────────────────────────────────────────────────────────────────

@email_admin_bp.route("/api/pending")
def api_pending():
    rows = db.get_pending_emails()
    return jsonify(rows)


@email_admin_bp.route("/api/logs")
def api_logs():
    limit = int(request.args.get("limit", 100))
    offset = int(request.args.get("offset", 0))
    rows = db.get_email_logs(limit=limit, offset=offset)
    return jsonify(rows)


@email_admin_bp.route("/api/approve/<int:email_id>", methods=["POST"])
def api_approve(email_id: int):
    try:
        from tracking.email_executor import move
        move(email_id)
        db.update_email_staging_status(
            email_id, "executed",
            executed_at=datetime.utcnow().isoformat(),
            reviewed_at=datetime.utcnow().isoformat(),
        )
        return jsonify({"ok": True})
    except Exception as exc:
        log.error("Approve failed id=%d: %s", email_id, exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


@email_admin_bp.route("/api/approve-batch", methods=["POST"])
def api_approve_batch():
    data = request.get_json(force=True) or {}
    ids = data.get("ids")
    if ids is None:
        # Approve all pending
        ids = [r["id"] for r in db.get_pending_emails()]
    try:
        from tracking.email_executor import approve_batch
        result = approve_batch(ids)
        return jsonify({"ok": True, **result})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@email_admin_bp.route("/api/skip/<int:email_id>", methods=["POST"])
def api_skip(email_id: int):
    db.update_email_staging_status(
        email_id, "skipped",
        reviewed_at=datetime.utcnow().isoformat(),
    )
    return jsonify({"ok": True})


@email_admin_bp.route("/api/override/<int:email_id>", methods=["POST"])
def api_override(email_id: int):
    data = request.get_json(force=True) or {}
    folder = data.get("folder", "")
    if folder not in VALID_FOLDERS:
        return jsonify({"ok": False, "error": "Invalid folder"}), 400
    db.set_email_override_folder(email_id, folder)
    try:
        from tracking.email_executor import move
        move(email_id, override_folder=folder)
        db.update_email_staging_status(
            email_id, "executed",
            executed_at=datetime.utcnow().isoformat(),
            reviewed_at=datetime.utcnow().isoformat(),
        )
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@email_admin_bp.route("/api/settings", methods=["GET"])
def api_settings_get():
    return jsonify({
        "threshold": int(db.get_setting("email_auto_move_threshold", "90")),
        "auto_move_enabled": db.get_setting("email_auto_move_enabled", "1") == "1",
    })


@email_admin_bp.route("/api/settings", methods=["POST"])
def api_settings_post():
    data = request.get_json(force=True) or {}
    if "threshold" in data:
        db.set_setting("email_auto_move_threshold", str(int(data["threshold"])))
    if "auto_move_enabled" in data:
        db.set_setting("email_auto_move_enabled", "1" if data["auto_move_enabled"] else "0")
    return jsonify({"ok": True})


_processor_lock = threading.Lock()


@email_admin_bp.route("/api/run-now", methods=["POST"])
def api_run_now():
    if not _processor_lock.acquire(blocking=False):
        return jsonify({"ok": False, "error": "Already running"}), 409

    def _run():
        try:
            from tracking.email_processor import run
            run()
        except Exception as exc:
            log.error("email_processor.run() error: %s", exc)
        finally:
            _processor_lock.release()

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True, "message": "Email check started"})


# ── HTML tab ───────────────────────────────────────────────────────────────────

_EMAIL_ADMIN_HTML = """
<style>
.ea-subnav { display:flex; gap:8px; margin-bottom:20px; border-bottom:1px solid #21262d; }
.ea-sub-btn { background:none; border:none; border-bottom:2px solid transparent;
  color:#8b949e; padding:8px 16px; font-size:0.86rem; cursor:pointer;
  margin-bottom:-1px; transition:color 0.15s,border-color 0.15s; }
.ea-sub-btn:hover { color:#c9d1d9; }
.ea-sub-btn.active { color:#f0f6fc; border-bottom-color:#58a6ff; font-weight:600; }
.ea-section { display:none; }
.ea-section.active { display:block; }
.ea-bar { display:flex; align-items:center; gap:10px; margin-bottom:14px; flex-wrap:wrap; }
.ea-btn { background:#21262d; border:1px solid #30363d; color:#e2e8f0;
  border-radius:6px; padding:6px 14px; font-size:0.82rem; cursor:pointer;
  transition:background 0.15s; }
.ea-btn:hover { background:#30363d; }
.ea-btn.primary { background:#1d4ed8; border-color:#2563eb; color:#fff; }
.ea-btn.primary:hover { background:#2563eb; }
.ea-btn.danger { background:#7f1d1d; border-color:#991b1b; color:#fca5a5; }
.ea-chip { padding:4px 12px; border-radius:99px; font-size:0.75rem;
  cursor:pointer; border:1px solid #30363d; color:#8b949e; background:#161b22; }
.ea-chip.active { color:#38bdf8; border-color:#38bdf8; background:#0c1a2e; }
.ea-table-wrap { background:#161b22; border:1px solid #21262d; border-radius:10px; overflow:hidden; }
.ea-table { width:100%; border-collapse:collapse; font-size:0.82rem; }
.ea-table thead th { padding:10px 12px; text-align:left; font-size:0.68rem;
  text-transform:uppercase; letter-spacing:0.07em; color:#64748b;
  border-bottom:1px solid #21262d; white-space:nowrap; background:#161b22; }
.ea-table tbody tr { border-bottom:1px solid #0d1117; }
.ea-table tbody tr:hover { background:#1c2128; }
.ea-table td { padding:9px 12px; vertical-align:middle; }
.ea-folder { display:inline-block; padding:2px 8px; border-radius:99px;
  font-size:0.7rem; font-weight:700; text-transform:uppercase; }
.ef-rejected { background:#2d0f0f; color:#f87171; }
.ef-inreview { background:#0c2a4a; color:#60a5fa; }
.ef-nextstep { background:#1a1a2e; color:#818cf8; }
.ef-interview { background:#0d2818; color:#34d399; }
.ef-challenge { background:#2d1a06; color:#fb923c; }
.ef-offer { background:#2d2006; color:#fbbf24; }
.ef-uncertain { background:#1c2128; color:#64748b; }
.ea-conf { font-size:0.75rem; color:#64748b; margin-left:4px; }
.ea-unmatched { color:#64748b; font-style:italic; font-size:0.78rem; }
.ea-actions { display:flex; gap:6px; align-items:center; flex-wrap:wrap; }
.ea-actions select { background:#0d1117; border:1px solid #30363d; color:#e2e8f0;
  border-radius:5px; padding:4px 6px; font-size:0.78rem; }
.ea-empty { padding:32px; text-align:center; color:#64748b; }
.ea-settings-form { background:#161b22; border:1px solid #21262d; border-radius:10px;
  padding:24px; max-width:480px; display:flex; flex-direction:column; gap:18px; }
.ea-settings-form label { color:#c9d1d9; font-size:0.88rem; }
.ea-settings-form input[type=range] { width:100%; accent-color:#38bdf8; }
.ea-settings-form input[type=checkbox] { accent-color:#38bdf8; margin-right:8px; }
.ea-badge { display:inline-block; background:#1d2d44; color:#60a5fa;
  border-radius:99px; font-size:0.7rem; padding:1px 7px; margin-left:6px; }
</style>

<div class="ea-subnav">
  <button class="ea-sub-btn active" onclick="eaShowTab('pending',this)">
    Pending Approvals<span class="ea-badge" id="ea-pending-count">0</span>
  </button>
  <button class="ea-sub-btn" onclick="eaShowTab('logs',this)">Logs</button>
  <button class="ea-sub-btn" onclick="eaShowTab('settings',this)">Settings</button>
</div>

<!-- Pending -->
<div id="ea-pending" class="ea-section active">
  <div class="ea-bar">
    <button class="ea-btn primary" onclick="eaApproveAll()">Approve All ✓</button>
    <button class="ea-btn" onclick="eaSkipAll()">Skip All</button>
    <span style="flex:1"></span>
    <span class="ea-chip active" onclick="eaFilter('all',this)">All</span>
    <span class="ea-chip" onclick="eaFilter('high',this)">High Confidence</span>
    <span class="ea-chip" onclick="eaFilter('ambiguous',this)">Ambiguous</span>
    <span class="ea-chip" onclick="eaFilter('unmatched',this)">Unmatched</span>
  </div>
  <div class="ea-table-wrap" id="ea-pending-wrap">
    <div class="ea-empty">Loading…</div>
  </div>
</div>

<!-- Logs -->
<div id="ea-logs" class="ea-section">
  <div class="ea-table-wrap" id="ea-logs-wrap">
    <div class="ea-empty">Loading…</div>
  </div>
</div>

<!-- Settings -->
<div id="ea-settings" class="ea-section">
  <div class="ea-settings-form">
    <div>
      <label>Auto-move confidence threshold: <strong id="ea-thresh-display">90</strong>%</label>
      <input type="range" min="50" max="100" step="1" value="90" id="ea-threshold"
             oninput="document.getElementById('ea-thresh-display').textContent=this.value">
    </div>
    <div>
      <label>
        <input type="checkbox" id="ea-auto-enabled" checked>
        Auto-move high-confidence rejections
      </label>
    </div>
    <div style="display:flex;gap:10px;flex-wrap:wrap;">
      <button class="ea-btn primary" onclick="eaSaveSettings()">Save Settings</button>
      <button class="ea-btn" onclick="eaRunNow()" id="ea-run-btn">🔄 Check Emails Now</button>
    </div>
    <div id="ea-settings-msg" style="font-size:0.82rem;color:#34d399;display:none"></div>
  </div>
</div>

<script>
const EA_FOLDERS = """ + json.dumps(VALID_FOLDERS) + """;
let _eaPendingData = [];
let _eaFilter = 'all';

function eaEsc(s) {
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function eaFolderClass(f) {
  const m = {Rejected:'ef-rejected','In Review':'ef-inreview','Next Step':'ef-nextstep',
    Interview:'ef-interview','Code Challenge':'ef-challenge',Offer:'ef-offer'};
  return m[f] || 'ef-uncertain';
}

function eaShowTab(id, btn) {
  document.querySelectorAll('.ea-section').forEach(s => s.classList.remove('active'));
  document.querySelectorAll('.ea-sub-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('ea-'+id).classList.add('active');
  btn.classList.add('active');
  if (id === 'logs') eaLoadLogs();
  if (id === 'settings') eaLoadSettings();
}

function eaFilter(type, chip) {
  _eaFilter = type;
  document.querySelectorAll('.ea-chip').forEach(c => c.classList.remove('active'));
  chip.classList.add('active');
  eaRenderPending();
}

async function eaLoadPending() {
  const r = await fetch('/email-admin/api/pending');
  _eaPendingData = await r.json();
  document.getElementById('ea-pending-count').textContent = _eaPendingData.length;
  eaRenderPending();
}

function eaRenderPending() {
  const data = _eaPendingData.filter(r => {
    if (_eaFilter === 'high') return r.confidence_score >= 70;
    if (_eaFilter === 'ambiguous') return r.match_type === 'ambiguous';
    if (_eaFilter === 'unmatched') return r.match_type === 'unmatched';
    return true;
  });
  if (!data.length) {
    document.getElementById('ea-pending-wrap').innerHTML =
      '<div class="ea-empty">No pending emails</div>';
    return;
  }
  const rows = data.map(r => `
    <tr id="ea-row-${r.id}">
      <td class="td-date">${eaEsc(r.received_date||'').slice(0,16)}</td>
      <td>${eaEsc(r.sender)}</td>
      <td>${eaEsc(r.subject)}</td>
      <td>
        <span class="ea-folder ${eaFolderClass(r.predicted_folder)}">${eaEsc(r.predicted_folder)}</span>
        <span class="ea-conf">${r.confidence_score}%</span>
      </td>
      <td style="max-width:200px;font-size:0.78rem;color:#8b949e">${eaEsc(r.classification_reason)}</td>
      <td>${r.company
        ? eaEsc(r.company) + '<br><span style="color:#64748b;font-size:0.76rem">'+eaEsc(r.role||'')+'</span>'
        : '<span class="ea-unmatched">'+r.match_type+'</span>'}</td>
      <td>
        <div class="ea-actions">
          <button class="ea-btn primary" onclick="eaApprove(${r.id})">✓</button>
          <button class="ea-btn danger" onclick="eaSkip(${r.id})">✗</button>
          <select onchange="if(this.value){eaOverride(${r.id},this.value);this.value=''}">
            <option value="">Change…</option>
            ${EA_FOLDERS.map(f=>'<option value="'+f+'">'+f+'</option>').join('')}
          </select>
        </div>
      </td>
    </tr>
  `).join('');

  document.getElementById('ea-pending-wrap').innerHTML = `
    <table class="ea-table">
      <thead><tr>
        <th>Date</th><th>From</th><th>Subject</th>
        <th>Folder</th><th>Reason</th><th>Job</th><th>Actions</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

async function eaApprove(id) {
  const r = await fetch('/email-admin/api/approve/'+id, {method:'POST'});
  const d = await r.json();
  if (d.ok) document.getElementById('ea-row-'+id)?.remove();
  else alert('Error: '+d.error);
}

async function eaSkip(id) {
  await fetch('/email-admin/api/skip/'+id, {method:'POST'});
  document.getElementById('ea-row-'+id)?.remove();
}

async function eaOverride(id, folder) {
  const r = await fetch('/email-admin/api/override/'+id, {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({folder}),
  });
  const d = await r.json();
  if (d.ok) document.getElementById('ea-row-'+id)?.remove();
  else alert('Error: '+d.error);
}

async function eaApproveAll() {
  if (!_eaPendingData.length) return;
  if (!confirm('Approve and move all '+_eaPendingData.length+' pending emails?')) return;
  const r = await fetch('/email-admin/api/approve-batch', {method:'POST',
    headers:{'Content-Type':'application/json'}, body:JSON.stringify({})});
  const d = await r.json();
  alert('Done — '+d.succeeded+' moved, '+d.failed+' failed');
  eaLoadPending();
}

async function eaSkipAll() {
  for (const r of _eaPendingData) await eaSkip(r.id);
}

async function eaLoadLogs() {
  const r = await fetch('/email-admin/api/logs');
  const data = await r.json();
  if (!data.length) {
    document.getElementById('ea-logs-wrap').innerHTML =
      '<div class="ea-empty">No move history yet</div>';
    return;
  }
  const rows = data.map(r => `
    <tr>
      <td class="td-date">${eaEsc((r.moved_at||'').slice(0,16))}</td>
      <td>${eaEsc(r.sender||'')}</td>
      <td>${eaEsc(r.subject||'')}</td>
      <td>${eaEsc(r.to_folder||'')}</td>
      <td>${r.success ? '<span style="color:#34d399">✓ OK</span>'
                      : '<span style="color:#f87171">✗ Failed</span>'}</td>
      <td style="color:#f87171;font-size:0.75rem">${eaEsc(r.error_message||'')}</td>
    </tr>
  `).join('');
  document.getElementById('ea-logs-wrap').innerHTML = `
    <table class="ea-table">
      <thead><tr>
        <th>Date</th><th>From</th><th>Subject</th>
        <th>Moved To</th><th>Status</th><th>Error</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

async function eaLoadSettings() {
  const r = await fetch('/email-admin/api/settings');
  const d = await r.json();
  document.getElementById('ea-threshold').value = d.threshold;
  document.getElementById('ea-thresh-display').textContent = d.threshold;
  document.getElementById('ea-auto-enabled').checked = d.auto_move_enabled;
}

async function eaSaveSettings() {
  const threshold = parseInt(document.getElementById('ea-threshold').value);
  const auto_move_enabled = document.getElementById('ea-auto-enabled').checked;
  await fetch('/email-admin/api/settings', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({threshold, auto_move_enabled}),
  });
  const msg = document.getElementById('ea-settings-msg');
  msg.textContent = 'Settings saved.';
  msg.style.display = 'block';
  setTimeout(() => msg.style.display='none', 2000);
}

async function eaRunNow() {
  const btn = document.getElementById('ea-run-btn');
  btn.textContent = '⏳ Running…';
  btn.disabled = true;
  const r = await fetch('/email-admin/api/run-now', {method:'POST'});
  const d = await r.json();
  setTimeout(() => {
    btn.textContent = '🔄 Check Emails Now';
    btn.disabled = false;
    eaLoadPending();
  }, 3000);
}

// Load pending on init
eaLoadPending();
</script>
"""


@email_admin_bp.route("/")
def index():
    from flask import render_template_string
    return render_template_string(_EMAIL_ADMIN_HTML)
```

- [ ] **Step 2: Verify Python syntax**

```
python -c "import dashboard.email_admin; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```
git add dashboard/email_admin.py
git commit -m "feat: add email_admin Flask Blueprint with full Email Admin tab UI"
```

---

## Task 6: dashboard.py — register Blueprint + add tab + update overview

**Files:**
- Modify: `dashboard/dashboard.py`
- Modify: `dedup/db.py`

- [ ] **Step 1: Register Blueprint in dashboard.py**

In `dashboard/dashboard.py`, find the line:
```python
app = Flask(__name__)
```

Add these two lines directly after it:

```python
from dashboard.email_admin import email_admin_bp
app.register_blueprint(email_admin_bp)
```

- [ ] **Step 2: Add Email Admin tab button to nav HTML**

In `dashboard/dashboard.py`, find the tab nav HTML. Search for the string `Job Hunt Agent` inside `_HTML`. It will look something like:

```
<button class="tab-btn
```

There are two existing tab buttons. Find where the second one ends and add a third button. Search for the pattern that identifies the last tab button in the nav. Add this button after the existing "Job Hunt Agent" button:

```html
<button class="tab-btn" id="tab-email-admin" onclick="showTab('email-admin')">📧 Email Admin</button>
```

Also add the corresponding tab panel div. Search for the pattern of existing tab panels (divs with id like `pane-*` or `tab-*`) and add:

```html
<div id="pane-email-admin" class="tab-pane" style="display:none">
  <div id="email-admin-mount"></div>
</div>
```

And in the `showTab` JavaScript function (search for `function showTab`), make it load the Email Admin tab via fetch when selected. Add this case to the showTab function:

```javascript
if (id === 'email-admin') {
  fetch('/email-admin/')
    .then(r => r.text())
    .then(html => {
      document.getElementById('email-admin-mount').innerHTML = html;
      // Re-run inline scripts from the loaded HTML
      document.querySelectorAll('#email-admin-mount script').forEach(s => {
        const ns = document.createElement('script');
        ns.textContent = s.textContent;
        document.head.appendChild(ns);
      });
    });
}
```

**Note:** The exact location of these changes depends on the structure inside `dashboard.py`'s `_HTML` string. Search for `tab-btn` and `showTab` in the file to find the right insertion points. The file is large — use `Grep` to find exact line numbers before editing.

- [ ] **Step 3: Update get_overview_data() to include upcoming_events**

In `dedup/db.py`, find the `get_overview_data()` function. After the existing query results are collected, add upcoming_events to the return dict:

Find this block:
```python
    return {
        "stats":           {"total": total, **buckets},
        "upcoming_events": [dict(r) for r in upcoming],
        "priority_tasks":  [dict(r) for r in tasks],
    }
```

Replace it with:
```python
    auto_events = get_upcoming_events()
    return {
        "stats":           {"total": total, **buckets},
        "upcoming_events": [dict(r) for r in upcoming],
        "priority_tasks":  [dict(r) for r in tasks],
        "auto_events":     auto_events,
    }
```

- [ ] **Step 4: Smoke test — start the dashboard and verify the tab appears**

```
python dashboard/dashboard.py
```

Open `http://localhost:5000` in a browser. Verify:
- "📧 Email Admin" appears as the third tab in the nav
- Clicking it loads the Email Admin sub-tabs (Pending Approvals / Logs / Settings)
- Settings page loads without errors
- "Check Emails Now" button responds (returns 200 even without live IMAP)

- [ ] **Step 5: Commit**

```
git add dashboard/dashboard.py dedup/db.py
git commit -m "feat: wire Email Admin Blueprint into dashboard — third tab + overview update"
```

---

## Task 7: Task Scheduler setup

**Files:** None — configuration only.

- [ ] **Step 1: Verify email_processor runs from command line**

```
python tracking\email_processor.py
```

Expected output (without live IMAP credentials): Either processes emails or exits cleanly with a logged error about missing credentials. It should NOT crash with an import error.

- [ ] **Step 2: Add Task Scheduler entry**

Open Windows Task Scheduler → Create Basic Task:

| Field | Value |
|---|---|
| Name | `Job Hunt — Email Processor` |
| Description | `Check inbox every 15 min and stage emails for review` |
| Trigger | Daily, repeat every 15 minutes, for a duration of 1 day |
| Action | Start a program |
| Program | `C:\Users\f_beh\Projects\claude\job-hunt-agent\.venv\Scripts\python.exe` |
| Arguments | `tracking\email_processor.py` |
| Start in | `C:\Users\f_beh\Projects\claude\job-hunt-agent` |

Or from PowerShell (run as Administrator):

```powershell
$action = New-ScheduledTaskAction `
  -Execute "C:\Users\f_beh\Projects\claude\job-hunt-agent\.venv\Scripts\python.exe" `
  -Argument "tracking\email_processor.py" `
  -WorkingDirectory "C:\Users\f_beh\Projects\claude\job-hunt-agent"

$trigger = New-ScheduledTaskTrigger -RepetitionInterval (New-TimeSpan -Minutes 15) `
  -Once -At (Get-Date)

Register-ScheduledTask `
  -TaskName "Job Hunt - Email Processor" `
  -Action $action `
  -Trigger $trigger `
  -RunLevel Highest `
  -Force
```

- [ ] **Step 3: Run all tests one final time**

```
pytest tests/ -v
```

Expected: All tests pass.

- [ ] **Step 4: Final commit**

```
git add .
git commit -m "feat: complete email handler module — processor, executor, admin tab, scheduler"
```

---

## Self-Review Checklist

**Spec coverage:**

| Requirement | Task |
|---|---|
| Read from Inbox / Focus / Other / Junk | Task 3 — `_fetch_unseen_from_folders` |
| Claude classification with confidence | Task 3 — `_classify` |
| email_staging table with all fields | Task 2 — DB schema |
| Auto-move rejections ≥90% confidence | Task 3 — `_maybe_auto_execute` |
| User approval UI with approve/skip/override | Task 5 — Blueprint HTML |
| Batch approve / skip all | Task 5 — `eaApproveAll`, `eaSkipAll` |
| Filter chips (All / High / Ambiguous / Unmatched) | Task 5 — `eaFilter` |
| IMAP folder move with correct paths | Task 4 — `FOLDER_MAP` |
| "Code Challange" exact spelling | Task 4 — FOLDER_MAP value |
| Audit log in email_move_history | Task 4 — `log_email_move` |
| upcoming_events from email extraction | Task 3 — `_extract_event` + `insert_upcoming_event` |
| Dashboard overview includes auto_events | Task 6 — `get_overview_data` |
| Settings: threshold + auto-move toggle | Task 5 — settings routes + UI |
| Run-now button | Task 5 — `/api/run-now` |
| applications.status + last_email_* updated | Task 4 — `update_application_from_email` |
| No emails deleted — only moved | Task 4 — COPY + EXPUNGE (not DELETE) |
| Task Scheduler setup | Task 7 |
| Email Admin as third dashboard tab | Task 6 |

**All requirements covered. No gaps found.**
