# Job Hunt Automation System

Fully automated job application pipeline — scrapes LinkedIn, scores matches with Claude AI, fills and submits applications (Easy Apply + external ATS), and tracks every response via email.

## Quick Start

### 1. Prerequisites
- Python 3.11+
- API keys: `ANTHROPIC_API_KEY`, `APIFY_API_TOKEN`
- Azure app registration (for email OAuth2)

### 2. Install
```bash
pip install -r requirements.txt
playwright install chromium
```

### 3. Configure
Edit `data/config.yaml`:
```yaml
full_name: "Jane Doe"
email: "jane@hotmail.com"
phone: "+49 151 12345678"
locations: ["Berlin", "Remote"]
min_match_score: 70
max_applications_per_day: 20
posted_limit: 7          # only jobs posted within N days
scrape_pool_size: 3
```

Copy `.env.example` → `.env` and fill in:
```
ANTHROPIC_API_KEY=sk-ant-...
APIFY_API_TOKEN=apify_api_...
LINKEDIN_EMAIL=you@hotmail.com
LINKEDIN_PASSWORD=...
```

### 4. Upload your resume
Open `http://localhost:5000` → **Upload** tab.  
Upload `resume_en.pdf` (and optionally `resume_de.pdf` for German jobs).  
These land in `uploads/` and are used by the applier automatically.

### 5. One-time email auth (Microsoft OAuth2)
```bash
python tracking/get_token.py
```
Opens a device-code browser flow — sign in with your Outlook/Hotmail account.  
Saves `MS_REFRESH_TOKEN` to `.env` automatically.

### 6. Run
```bash
# Full pipeline (scrape → match → apply)
python core/main.py

# Dashboard only
python dashboard/dashboard.py

# Email processor only (also runs on Task Scheduler)
python tracking/email_processor.py
```

---

## Pipeline Overview

```
LinkedIn (Apify)
      │
      ▼
  scraper/          — fetch job listings via Apify actor
      │
      ▼
  dedup/            — skip already-seen / already-applied jobs
      │
      ▼
  matcher/          — Claude Haiku scores each job (0-100) against your profile
      │  (< min_match_score → skip)
      ▼
  applier/          — Playwright fills & submits the application
      │  Easy Apply: linkedin_applier.py wizard driver
      │  External:   external_applier.py (Greenhouse, Lever, Ashby, generic)
      │  Vision:     smart_filler.py (Claude vision fallback for unknown ATSes)
      │
      ▼
  dedup/db.py       — writes "Pending Confirmation" to SQLite
      │
      ▼
  tracking/         — email_processor classifies recruiter reply
                      email_executor promotes status to "In Review" / "Interview" / etc.
```

---

## Applier Architecture

### LinkedIn Easy Apply (`applier/`)

| File | Role |
|------|------|
| `applier.py` | Orchestrator — loops jobs, calls clicker + applier, logs result |
| `linkedin_clicker.py` | Opens the Easy Apply modal, handles login checks |
| `linkedin_applier.py` | Drives the multi-step wizard; all field-filling logic |
| `external_applier.py` | Routes to platform-specific handlers (Greenhouse, Lever, Ashby) or generic CSS/vision fallback |
| `smart_filler.py` | Claude Sonnet vision fallback for unknown/SPA-based ATSes |
| `memory.py` | Persistent Q&A cache — avoids re-asking Claude the same question |
| `events.py` | SSE event emitter for live dashboard updates |

### Easy Apply wizard flow (`linkedin_applier.py`)

```
fill_easy_apply()
  ├─ _dismiss_unfinished_application_dialog()   # continue or discard prior draft
  ├─ _handle_email_dropdown()                   # select correct email address
  ├─ _fill_profile_fields()                     # name / phone / LinkedIn URL etc.
  └─ for step in wizard (up to 12 steps):
       ├─ _select_or_upload_resume()            # pick or upload PDF (once, cached)
       ├─ _fill_wizard_step()                   # ← unified field handler (see below)
       ├─ blur/change events → show validation
       ├─ _retry_invalid_fields()               # re-fill fields with errors
       └─ click Next / Submit
            └─ on Submit: _dismiss_post_submit_dialogs()
```

### `_fill_wizard_step` — unified field handler

Processes all field types in a fixed order so that no element is processed twice and context (`prior_answers`) accumulates across all types:

1. **Phone country-code `<select>`** — must precede the phone number field
2. **`input[type=file]`** — resume upload (language-aware: DE/EN)
3. **`<select>`** — fuzzy-match answer, dispatch `change` event
4. **Fieldset radio groups** — 4-strategy click (LinkedIn attribute → label → force → JS)
5. **`input[type=checkbox]`** — consent regex auto-check; others via Claude
6. **Text-like inputs** — `text`, `email`, `tel`, `url`, `number` (all previously missing types now included)
7. **`<textarea>`** — cover letters routed to Claude Sonnet; others to Haiku
8. **Combobox / typeahead** — type → wait for dropdown → click match → `ArrowDown+Enter` fallback

### `answer_custom_question` — AI answer routing

```
question_text
  ├─ accommodations → hardcoded safe answer (avoids LinkedIn validation errors)
  ├─ cover letter   → _generate_cover_letter() [Claude Sonnet]
  ├─ salary         → _get_salary_answer() [profile value, nearest option]
  ├─ location/country → _normalize_location() [alias map]
  ├─ fast-path patterns → profile fields (name, email, phone, work permit, etc.)
  ├─ answer cache hit → return cached answer
  └─ Claude Haiku API call → cache + return
```

### External ATS routing (`external_applier.py`)

```
URL
  ├─ greenhouse.io / boards.greenhouse.io → _apply_greenhouse()
  ├─ lever.co                             → _apply_lever()
  ├─ ashby.io                             → _apply_ashby()
  └─ unknown / custom portal
       ├─ Tier 1: _run_ats_form()         [CSS-based multi-step loop]
       └─ Tier 2: smart_apply_page()      [Claude Sonnet vision fallback]
```

`_run_ats_form` shared loop: fill → fix validation errors (2 passes) → click Next/Submit → verify → detect post-submit errors → check if still a form.

---

## Email Tracking

Status flow for each application:

```
Submit → "Pending Confirmation"  (written by applier)
       ↓
Recruiter email arrives
       ↓
email_processor.py  →  classifies with Claude AI
       ↓
email_executor.py   →  moves email to Outlook folder
                        calls db.update_application_from_email()
                        status → "In Review" / "Interview" / "Offer" / "Rejected"
```

### Outlook folder structure (auto-created)
```
Applications/
  Rejected/
  In Review/
  Next Step/
    Interview/Todo/
    Code Challenge/ToDo/
  Offer/
```

### Email Admin dashboard
Open `http://localhost:5000` → **Email Admin** tab:
- Review pending emails with AI classification and confidence score
- Approve / reject / override target folder
- Bulk-approve all above threshold
- Adjust auto-move confidence threshold and toggle

---

## Directory Structure

```
job-hunt-agent/
├── core/
│   ├── main.py              # Pipeline entry point (scrape → match → apply loop)
│   └── config.py            # YAML config loader, path resolution, logging setup
│
├── scraper/
│   └── scraper.py           # Apify LinkedIn scraper
│
├── matcher/
│   └── matcher.py           # Claude Haiku job-profile scorer (returns 0-100)
│
├── applier/
│   ├── applier.py           # Orchestrator: job loop, status logging, retry logic
│   ├── linkedin_clicker.py  # Opens Easy Apply modal, handles session/login
│   ├── linkedin_applier.py  # Wizard driver + all field-filling logic
│   ├── external_applier.py  # External ATS handlers + multi-step loop + vision fallback
│   ├── smart_filler.py      # Claude Sonnet vision filler for unknown ATSes
│   ├── memory.py            # Persistent Q&A answer cache (JSON)
│   └── events.py            # SSE event emitter (live dashboard feed)
│
├── tracking/
│   ├── email_processor.py   # Fetch unread emails → classify → stage in DB
│   ├── email_executor.py    # Move emails via Graph API, update application status
│   ├── ms_auth.py           # MSAL OAuth2 token management
│   ├── ms_graph.py          # Microsoft Graph API helpers (read/move/folder)
│   ├── get_token.py         # One-time device-code auth script
│   ├── interview_handler.py # Calendar event extraction from interview emails
│   └── excel_updater.py     # Optional Excel tracker sync
│
├── dedup/
│   └── db.py                # SQLite: applications table, email staging, status updates
│
├── dashboard/
│   ├── dashboard.py         # Flask app — job pipeline UI + live feed
│   └── email_admin.py       # Email Admin Blueprint (/email-admin/)
│
├── tailor/
│   └── tailor.py            # (Optional) CV tailoring per job description
│
├── tests/                   # pytest suite
│   ├── test_email_db.py
│   ├── test_email_processor.py
│   └── test_email_executor.py
│
├── uploads/                 # Resume PDFs + support docs (managed via dashboard)
│   ├── resume_en.pdf
│   ├── resume_de.pdf        # optional — falls back to EN for German jobs
│   └── support/             # additional attachments (references, certificates)
│
├── data/
│   └── config.yaml          # User profile, preferences, thresholds
│
└── .env                     # API keys & secrets (never commit)
```

---

## Environment Variables

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API key (Haiku for Q&A, Sonnet for cover letters & vision) |
| `APIFY_API_TOKEN` | Apify scraping token |
| `LINKEDIN_EMAIL` | LinkedIn login email |
| `LINKEDIN_PASSWORD` | LinkedIn password |
| `MS_REFRESH_TOKEN` | Auto-set by `get_token.py` — do not edit manually |
| `CONFIG_PATH` | (optional) override default `data/config.yaml` path |

---

## Testing

```bash
pytest                              # all tests
pytest tests/test_email_db.py
pytest tests/test_email_processor.py
pytest tests/test_email_executor.py
```

---

## Task Scheduler (Windows)

Run the email processor automatically every 15 minutes:

1. Open **Task Scheduler** → Create Basic Task
2. Trigger: Daily, repeat every **15 minutes**
3. Action: Start a program
   - Program: `C:\path\to\.venv\Scripts\python.exe`
   - Arguments: `tracking\email_processor.py`
   - Start in: `C:\Users\f_beh\Projects\claude\job-hunt-agent`

---

## License

Internal use only.
