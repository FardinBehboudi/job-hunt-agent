# Job Hunt Agent

Automated job hunting pipeline. Scrapes LinkedIn via Apify, scores each job against your resume with Claude AI, filters by match quality and German level requirements, and submits LinkedIn Easy Apply applications automatically. All state tracked in SQLite and a Flask web dashboard.

---

## Overview

Each run of the agent:

1. **Scrapes** LinkedIn job listings via the Apify `harvestapi/linkedin-job-search` actor for every configured role/location combination.
2. **Deduplicates** against a persistent `seen_jobs` cache and against jobs already applied to.
3. **Scores** each job with Claude AI — match score (0–100), interview chance, German level required, and skip reason. Scores are cached by resume MD5 hash so re-runs don't call the API again.
4. **Filters** out jobs below the minimum match score and jobs requiring German levels in `skip_german_levels`.
5. **Applies** via Playwright's LinkedIn Easy Apply automation.
6. **Tracks** every application in SQLite and updates an Excel tracker spreadsheet.
7. **Monitors** the inbox for confirmation emails and interview invitations.

The web dashboard exposes all of these steps as a wizard UI so you can review and approve each stage before proceeding.

---

## Architecture

### Code files

| File | Description |
|------|-------------|
| `main.py` | Pipeline orchestrator — calls scraper → matcher → dedup → tailor → applier → email_watcher in sequence. Also exposes `run_scrape_only()`, `run_match_only()`, `run_apply_only()` for the dashboard wizard. |
| `scraper.py` | Fetches jobs from LinkedIn via the Apify actor. Deduplicates within a run, upserts into `seen_jobs`, loads cached jobs within the `posted_limit` window, applies title-relevance pre-filter to save Claude API calls. |
| `matcher.py` | Reads the uploaded resume PDF, sends each job + resume to Claude for scoring. Caches scores by resume MD5 hash. Filters by German level using substring matching against `skip_german_levels`. |
| `dedup.py` | Filters matched jobs against the `applications` table to skip already-applied positions. |
| `tailor.py` | Generates a tailored resume and cover letter per job (currently skipped in the wizard flow — uploaded documents are used directly). |
| `applier.py` | Playwright automation for LinkedIn Easy Apply. Handles multi-step forms, file uploads, and confirmation. |
| `email_watcher.py` | IMAP polling for confirmation emails and interview invitations. Updates application status in the DB. |
| `interview_handler.py` | Classifies inbound emails as recruiter call / technical / final / rejection. Auto-confirms or pauses for user input. |
| `excel_updater.py` | Appends or updates rows in the Excel application tracker. Never deletes rows. |
| `db.py` | All SQLite interactions — schema creation, migrations, upserts, cache helpers, resume hash functions. |
| `dashboard.py` | Flask web server (~3000 lines). Serves the single-page HTML dashboard, all `/api/*` endpoints, SSE log stream, and the pipeline wizard. |
| `config.py` | Loads and validates `config.yaml`, resolves file paths, sets up logging. |
| `config.yaml` | User configuration — gitignored. |
| `.env` | API keys — gitignored. |
| `.env.example` | Template for `.env`. |
| `requirements.txt` | Python dependencies. |

### Uploads folder

All runtime data lives under `uploads/` (gitignored, auto-created on first run):

```
uploads/
├── resume_en.pdf          ← uploaded via web UI
├── cover_letter.*         ← uploaded via web UI
├── jobhunt.db             ← SQLite database (auto-created)
└── linkedin_session.json  ← saved LinkedIn session (auto-created)
```

No local file paths to configure. Everything is managed through the browser.

---

## Setup (one-time)

### 1. Install dependencies

```powershell
pip install -r requirements.txt
playwright install chromium
```

### 2. Create `.env`

```powershell
copy .env.example .env
```

Fill in the values:

```
ANTHROPIC_API_KEY=sk-ant-...        # console.anthropic.com
APIFY_API_TOKEN=apify_api_...       # apify.com → Settings → Integrations
LINKEDIN_EMAIL=you@example.com
LINKEDIN_PASSWORD=...
HOTMAIL_PASSWORD=...                # or App Password if 2FA is on
```

The LinkedIn actor is pre-configured as `harvestapi/linkedin-job-search`. Override with `APIFY_LINKEDIN_ACTOR=other/actor` in `.env` if needed.

### 3. Edit `config.yaml`

Minimum required edits:

```yaml
full_name: Your Name
hotmail_address: you@hotmail.com
notify_email: you@hotmail.com
phone: '+49...'
locations:
  - Berlin
roles:
  - Data Engineer
  - Python Developer
```

Everything else has working defaults — see [Configuration Reference](#configuration-reference-configyaml) below.

### 4. Start the web UI

```powershell
python dashboard.py
```

Open **http://localhost:5000** and go to the **Job Hunt Agent** tab.

---

## Web UI Guide

### Job Hunt Agent tab

The main tab. Everything runs as a wizard with four steps.

**Before your first run**, upload your resume and cover letter using the Upload Files panel in the Job Hunt Agent tab. No local file paths needed — everything is managed through the web interface.

- Upload your resume PDF under *Upload Resume*.
- Upload your cover letter PDF/DOCX under *Upload Cover Letter* (optional).
- Select job titles — click *Suggest from CV* to extract roles from your resume, or add custom titles.
- Edit config settings (locations, score threshold, pool size, time window, German filter).
- Connect LinkedIn — click *Connect LinkedIn*, complete the browser login with 2FA, then click *Done*. The session cookie is saved and reused on future runs.

**Step 1 — Scrape**

Fetches jobs from LinkedIn for every role × location combination in config. Shows a progress bar per combination. When done, displays a table of all scraped jobs with:
- Green *New* badge — fetched fresh from Apify this run
- Blue *From cache* badge — loaded from `seen_jobs` based on posted date

**Step 2 — Match**

Sends each job to Claude AI for scoring. Shows a live progress counter. When done, shows the matched jobs table with match score, interview chance, and German level badges. Jobs requiring German levels in your `skip_german_levels` list are excluded automatically even if the score filter is set to 0.

Scoring stats appear below the progress bar: *X scored fresh · Y from score cache*.

Click any row to open the detail panel — full description, match summary, skip reason, and View/Apply buttons.

**Step 3 → Step 4 — Apply**

Select jobs individually or click *Apply All*. Playwright submits LinkedIn Easy Apply. Results are logged to the DB and the Excel tracker is updated.

---

### Job Hunt Dashboard tab

Overview of your job hunt state:

- **Stats bar** — total applied, active interviews, pending responses.
- **Upcoming Interviews** — manually managed interview schedule with company, role, date/time, format.
- **Priority Tasks** — manually managed to-do list with priority levels and deadlines.
- **Applied** — full table of submitted applications with status, verdict, match score.
- **Interviews** — active interview pipeline.
- **Rejected** — rejected applications.
- **Import Excel** — one-time migration from an existing Excel tracker.
- **Download Tracker** — exports current DB state back to Excel.

---

## Cache System

### Scraper cache (`seen_jobs`)

Every scraped job is stored permanently in `seen_jobs`. On the next run:

1. Fresh jobs are fetched from Apify for the configured `posted_limit` window.
2. All jobs in `seen_jobs` that were posted within the same window are also loaded.
3. The two sets are merged and capped at 100 total.

This means Apify credits are only spent on genuinely new postings. Jobs with no stored `posted_date` are always included (never filtered out by the time window).

### Scoring cache (resume hash)

Match scores in `seen_jobs` are tagged with the MD5 hash of your uploaded resume PDF.

- **Same resume** → cached score reused, no Claude API call.
- **Resume changed** → all active jobs (within the time window) are re-scored automatically. Old scores outside the window are left untouched.

The match stats bar shows how many jobs were scored fresh vs. loaded from cache each run.

---

## Database Schema

File: `uploads/jobhunt.db` (SQLite, WAL mode)

| Table | Description |
|-------|-------------|
| `applications` | Every submitted application — company, role, location, date, status, verdict, match score, URLs, archive path. |
| `seen_jobs` | Persistent scraper + scorer cache. Stores URL, title, company, location, description, `posted_date`, `first_scraped_at`, match scores, `resume_hash`, `applied`, `dismissed` flags. |
| `scraped_jobs` | Current run's scraped jobs. Cleared at the start of each new scrape. |
| `matched_jobs` | Current run's scored jobs. Cleared at the start of each new scrape. Used for the live progress counter in the UI. |
| `upcoming_interviews` | Manually tracked interview schedule — date, company, role, type, time, format, notes. |
| `priority_tasks` | Manually tracked to-do list — priority, company, action, deadline. |
| `settings` | Key/value store for app state (e.g. LinkedIn session cookie path). |

---

## Configuration Reference (`config.yaml`)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `full_name` | string | — | Your full name, used in application documents. |
| `hotmail_address` | string | — | Hotmail/Outlook address for sending and receiving email. |
| `notify_email` | string | — | Address for pipeline notifications. |
| `phone` | string | — | Phone number included in application forms. |
| `locations` | list | `[Berlin]` | Cities to search. Each role is searched in each location. |
| `roles` | list | — | Job titles to search on LinkedIn. Set via the dashboard UI. |
| `min_match_score` | int (0–100) | `70` | Jobs below this score are filtered out after matching. |
| `max_applications_per_day` | int | `10` | Hard cap on applications submitted per pipeline run. |
| `scrape_pool_size` | int | `25` | Max jobs fetched from Apify per role/location combination. |
| `posted_limit` | string | `24h` | Time window for job freshness. Options: `1h`, `24h`, `week`, `month`. Controls both Apify filtering and the seen_jobs cache window. |
| `smart_scrape` | bool | `true` | Pre-filters scraped jobs by title keywords before sending to Claude. Saves API calls. |
| `skip_german_levels` | list | `[C1, C2, native, Muttersprache, verhandlungssicher, fließend]` | Jobs requiring any of these German levels are skipped. Matching is case-insensitive substring. |
| `auto_confirm_recruiter_call` | bool | `true` | Automatically confirm recruiter call scheduling emails. |
| `auto_confirm_technical` | bool | `false` | Automatically confirm technical interview scheduling emails. Set to `false` to review slot options manually. |
| `headless` | bool | `true` | Run Playwright browser in headless mode. Set to `false` to watch the browser during debugging. |
| `confirm_before_apply` | bool | `true` | Prompt for y/n confirmation in the terminal before each application (CLI pipeline only). |
| `cv_root` | string | — | **Legacy — not needed when using the web UI.** Only required if running `main.py` directly from the command line. Absolute path to a local CV folder. |
| `resume_en` | string | `agent/resume_en.pdf` | **Legacy — not needed when using the web UI.** Path to English resume relative to `cv_root`, for CLI use only. |
| `resume_de` | string | `agent/resume_de.pdf` | **Legacy — not needed when using the web UI.** Path to German resume relative to `cv_root`, for CLI use only. |
| `cover_letter_template` | string | `agent/cover_letter_template.docx` | **Legacy — not needed when using the web UI.** Cover letter template path relative to `cv_root`, for CLI use only. |
| `tracker_file` | string | — | Filename of the Excel tracker (used by `excel_updater.py`). |

---

## Application Status Values

| Status | Meaning |
|--------|---------|
| `Applied ✓` | Submitted; confirmed by a reply email. |
| `Applied — unconfirmed` | No confirmation email received within 5 days. |
| `Call Scheduled ✓` | Recruiter call auto-confirmed. |
| `⏸ Technical — awaiting confirmation` | Technical interview invite received; waiting for your slot choice. |
| `Technical Scheduled ✓` | Technical interview confirmed. |
| `⏸ Final — awaiting confirmation` | Final round invite received; waiting for your slot choice. |
| `Final Scheduled ✓` | Final interview confirmed. |
| `Rejected` | Rejection received. |
| `Offer Received 🎉` | Offer stage. |

---

## Logs

Activity is logged to the console and to the live log stream in the web UI. For persistent log files, configure a `log_file` path in `config.yaml`.
