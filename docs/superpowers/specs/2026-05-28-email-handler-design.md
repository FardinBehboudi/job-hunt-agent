# Email Handler Module — Design Spec
**Date:** 2026-05-28
**Branch:** feature/email-handler
**Status:** Approved

---

## Overview

Automatically monitor the Hotmail inbox (`f_behboud@hotmail.com`) for recruiter/company emails, classify them with Claude AI, stage them for user review, and move them into the correct Outlook folder via IMAP. Extracts interview dates, deadlines, and tasks into the dashboard.

---

## Decisions Made

| Question | Decision |
|---|---|
| Email access method | IMAP (pure, no COM/pywin32) — same as existing `email_watcher.py` |
| Folder moves | IMAP COPY + EXPUNGE (no Outlook desktop required) |
| Classification | Claude AI — subject + 500-char body preview, returns category + confidence + reason |
| DB layer | Add to existing `dedup/db.py`, new tables link to `applications.id` |
| Dashboard integration | Flask Blueprint (`dashboard/email_admin.py`) — third tab in existing nav |

---

## File Layout

```
tracking/
  email_watcher.py          ← existing, unchanged
  email_processor.py        ← NEW: read → classify → link → extract → stage
  email_executor.py         ← NEW: IMAP moves + applications update + audit log

dashboard/
  email_admin.py            ← NEW: Flask Blueprint (routes + HTML for Email Admin tab)
  dashboard.py              ← existing: +register Blueprint, +tab button (~5 lines)

dedup/
  db.py                     ← existing: +3 new tables, +email DB functions
```

---

## Database Schema

All new tables added to `dedup/db.py` via `init_db()` and migration guards.

### `email_staging`
One row per processed email. Written by `email_processor`, read by Blueprint.

```sql
CREATE TABLE IF NOT EXISTS email_staging (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    email_uid            TEXT,                          -- IMAP UID
    email_message_id     TEXT UNIQUE,                  -- RFC Message-ID (dedup key)
    sender               TEXT,
    subject              TEXT,
    body_preview         TEXT,                         -- first 500 chars
    received_date        TEXT,
    source_folder        TEXT,                         -- Inbox / Junk Email / etc.
    matched_app_id       INTEGER REFERENCES applications(id),
    match_confidence     INTEGER,                      -- 0-100, how sure we are it's the right job
    match_type           TEXT,                         -- exact / fuzzy / ambiguous / unmatched
    predicted_folder     TEXT,                         -- Rejected / In Review / Next Step / etc.
    confidence_score     INTEGER,                      -- 0-100, Claude classification confidence
    classification_reason TEXT,
    status               TEXT DEFAULT 'pending',       -- pending/approved/auto_executed/executed/failed/skipped
    user_override_folder TEXT,
    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reviewed_at          TIMESTAMP,
    executed_at          TIMESTAMP,
    notes                TEXT
);
```

### `upcoming_events`
Extracted interview/task/deadline data. Populated when category is Interview, Code Challenge, or Next Step.

```sql
CREATE TABLE IF NOT EXISTS upcoming_events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id           INTEGER REFERENCES applications(id),
    event_type       TEXT,    -- interview / task / challenge / offer / meeting
    title            TEXT,
    description      TEXT,
    event_date       TEXT,    -- ISO date
    event_time       TEXT,
    timezone         TEXT,
    priority         TEXT,    -- high / medium / low
    source_email_id  INTEGER REFERENCES email_staging(id),
    status           TEXT DEFAULT 'scheduled',  -- scheduled / completed / cancelled
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### `email_move_history`
Audit log of every IMAP move attempted. Never deleted.

```sql
CREATE TABLE IF NOT EXISTS email_move_history (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    email_staging_id INTEGER REFERENCES email_staging(id),
    from_folder      TEXT,
    to_folder        TEXT,
    moved_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    success          INTEGER DEFAULT 0,    -- 0/1
    error_message    TEXT
);
```

### New columns on `applications`
Added via migration guards (ALTER TABLE IF NOT EXISTS pattern):
- `last_email_date TEXT`
- `last_email_preview TEXT`
- `last_email_staging_id INTEGER`

---

## `tracking/email_processor.py`

Entry point: `run()` — called by Windows Task Scheduler every 15 min.

### Pipeline

```
run()
  └── _connect()                     IMAP login (hotmail_address + HOTMAIL_PASSWORD)
  └── for each monitored folder:
        _fetch_unseen()              UNSEEN messages only; skip UIDs already in email_staging
  └── for each new message:
        _classify(subject, preview)  Claude API call → {category, confidence, reason}
        _link_to_application()       domain match → applications table
        _extract_events()            second Claude call if Interview/Challenge/Next Step
        db.stage_email()             INSERT into email_staging (status=pending)
        _maybe_auto_execute()        if confidence≥90 AND category=Rejected → execute now
```

### Monitored Folders
```python
MONITORED_FOLDERS = ["Inbox", "Focus", "Other", "Junk Email"]
```

### Claude Classification Call
Single call per email. System prompt instructs Claude to return only JSON:
```json
{"category": "Rejected", "confidence": 94, "reason": "Contains 'not moving forward'"}
```
Valid categories: `Rejected` / `In Review` / `Next Step` / `Code Challenge` / `Interview` / `Offer` / `Uncertain`

### Claude Event Extraction Call
Only triggered when category ∈ {`Interview`, `Code Challenge`, `Next Step`}.
Returns structured JSON with `event_type`, `event_date` (ISO), `event_time`, `timezone`, `priority`, `title`, `description`.

### Job Linking Logic
1. Extract domain from sender (`recruiter@google.com` → `google`)
2. `SELECT * FROM applications WHERE LOWER(company) LIKE '%google%'`
3. Exactly 1 result → `match_type=exact`
4. Multiple results → `match_type=ambiguous`, `matched_app_id=NULL` (user resolves in UI)
5. No result → fuzzy fallback: difflib against company names in email body
6. Still nothing → `match_type=unmatched`, `matched_app_id=NULL`

### Auto-Execute Rule
```python
if record.confidence_score >= AUTO_MOVE_THRESHOLD and record.predicted_folder == "Rejected":
    email_executor.move(staging_id)
```
`AUTO_MOVE_THRESHOLD` defaults to 90, configurable via Settings in the UI (stored in `settings` table).

---

## `tracking/email_executor.py`

### `move(staging_id, override_folder=None)`
1. Load staging record; determine target folder (override takes precedence)
2. Open IMAP connection
3. `COPY` message UID → target folder (call `_ensure_folder()` first to create if missing)
4. `STORE \Deleted` + `EXPUNGE` on source folder
5. Write `email_move_history` row (success or failure)
6. Update `email_staging.status` → `executed` / `failed`, set `executed_at`
7. If `matched_app_id` is set: update `applications.status`, `last_email_date`, `last_email_preview`, `last_email_staging_id`

### `approve_batch(staging_ids)`
Loop over IDs, call `move()` for each. Returns `{succeeded: N, failed: M}`.

### Target Folder Mapping
```python
FOLDER_MAP = {
    "Rejected":       "Applications/Rejected",
    "In Review":      "Applications/In Review",
    "Next Step":      "Applications/Next Step",
    "Interview":      "Applications/Next Step/Interview/Todo",
    "Code Challenge": "Applications/Next Step/Code Challange/ToDo",  # exact Outlook spelling
    "Offer":          "Applications/Offer",    # created on first use
}
```

### Application Status Mapping
```python
STATUS_MAP = {
    "Rejected":       "Rejected",
    "In Review":      "In Review",
    "Next Step":      "Next Step",
    "Interview":      "Interview Scheduled",
    "Code Challenge": "Code Challenge",
    "Offer":          "Offer",
}
```

---

## `dashboard/email_admin.py` (Flask Blueprint)

Blueprint name: `email_admin`, url_prefix: `/email-admin`

### Routes

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Render Email Admin tab HTML |
| GET | `/api/pending` | List `status=pending` staging records |
| GET | `/api/logs` | `email_move_history` records (filterable) |
| POST | `/api/approve/<id>` | Approve single record → execute move |
| POST | `/api/approve-batch` | Approve all pending (or list of IDs) |
| POST | `/api/skip/<id>` | Mark as skipped |
| POST | `/api/override/<id>` | Set `user_override_folder`, then approve |
| GET | `/api/settings` | Read auto-move threshold + toggle state |
| POST | `/api/settings` | Update threshold/toggle (stored in `settings` table) |
| POST | `/api/run-now` | Trigger `email_processor.run()` in background thread |

### UI Sub-sections

**Pending Approvals**
- Table: From | Subject | Predicted Folder (confidence %) | Reason | Matched Job | Actions
- Actions per row: `Approve ✓` | `Skip` | `Change Folder ▼` (dropdown of valid folders)
- Batch: `Approve All` | `Skip All`
- Filter chips: All | High Confidence (≥70%) | Ambiguous | Unmatched

**Logs**
- Table: Date | From | Subject | Moved To | Status | Error
- Filter by date range and status

**Settings**
- Auto-move threshold slider (default: 90%)
- Auto-move category toggle (Rejections only, default on)
- "Run Now" button to trigger immediate email check

### `dashboard.py` changes
1. `from dashboard.email_admin import email_admin_bp` + `app.register_blueprint(email_admin_bp)`
2. Add `<button class="tab-btn" ...>📧 Email Admin</button>` to tab nav HTML

---

## Scheduling

Windows Task Scheduler entry (separate from existing `email_watcher.py`):
```
Program:   python
Arguments: tracking\email_processor.py
Schedule:  Every 15 minutes
Working dir: C:\Users\f_beh\Projects\claude\job-hunt-agent
```

Run via: `python tracking/email_processor.py` (has `if __name__ == "__main__"` guard).

---

## Credentials

No new credentials needed. Reuses existing:
- `HOTMAIL_PASSWORD` from `.env`
- `hotmail_address` from `config.yaml` (maps to `f_behboud@hotmail.com`)
- `ANTHROPIC_API_KEY` from `.env` (already present for Claude calls)

---

## Edge Cases

| Case | Handling |
|---|---|
| Duplicate email | `email_message_id` UNIQUE constraint; silently skipped |
| Ambiguous job match (multiple at same company) | `match_type=ambiguous`, shown in UI for manual assignment |
| Unmatched email (unknown company) | `match_type=unmatched`, user can manually set `matched_app_id` in UI |
| No date extracted from interview email | `event_date=NULL`; event still created, shown without date |
| IMAP move fails (folder missing) | `_ensure_folder()` creates it; if still fails, logged in `email_move_history` with `success=0` |
| Claude returns unexpected JSON | `try/except` → falls back to `Uncertain` with `confidence=0` |
| Focus / Other folders don't exist on account | IMAP SELECT returns error → skip gracefully, log warning |
| Low confidence (< 45%) | Category set to `Uncertain`, always requires user approval |

---

## Dashboard Widget Integration

`upcoming_events` is a new auto-populated table, separate from the existing manually-managed `upcoming_interviews` and `priority_tasks` tables. The dashboard Overview tab will be updated to show data from **both** sources:

- **Upcoming Interviews widget**: union of `upcoming_interviews` (manual) + `upcoming_events WHERE event_type='interview'` (auto)
- **Priority To-Do widget**: union of `priority_tasks` (manual) + `upcoming_events WHERE event_type IN ('task','challenge')` (auto)

Manual entries remain editable as before. Auto-populated entries show a `📧` badge to indicate they came from email.

---

## Success Criteria

- Emails from Inbox, Focus, Other, Junk Email are read and staged
- All emails appear in `email_staging` before any folder move
- High-confidence rejections (≥90%) auto-move; everything else requires approval
- User can review, approve, skip, or reassign folder for each pending email
- Extracted events (interviews, deadlines) populate `upcoming_events`
- `applications.status` updates when a move is executed
- Full audit trail in `email_move_history`
- No emails deleted — only moved
- Module runs on 15-min schedule without manual intervention
