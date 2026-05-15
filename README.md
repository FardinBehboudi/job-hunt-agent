# Job Hunt Agent

Automated job hunting pipeline. Scrapes LinkedIn via Apify, scores each job against your resume with Claude AI, filters by match quality and German level requirements, and submits LinkedIn Easy Apply applications automatically. All state tracked in SQLite and a Flask web dashboard.

---

## Overview

Each run of the agent:

1. **Scrapes** LinkedIn job listings via the Apify `harvestapi/linkedin-job-search` actor for every configured role/location combination.
2. **Deduplicates** against a persistent `seen_jobs` cache and against jobs already applied to — matched by URL and by company+title combination.
3. **Scores** each job with Claude Haiku — match score (0–100), interview chance, German level required, skip reason, and per-requirement reasoning. Scores are cached by resume MD5 hash so re-runs don't call the API again.
4. **Filters** out jobs below the minimum match score and jobs requiring German levels in `skip_german_levels`.
5. **Applies** via Playwright's LinkedIn Easy Apply automation, auto-filling profile fields (salary, notice period, work permit, etc.) from config.
6. **Tracks** every application in SQLite and updates an Excel tracker spreadsheet.
7. **Monitors** the inbox for confirmation emails and interview invitations.

The web dashboard exposes all of these steps as a wizard UI so you can review and approve each stage before proceeding.

---

## Architecture

### Code files

| File | Description |
|------|-------------|
| `main.py` | Pipeline orchestrator — calls scraper → matcher → dedup → tailor → applier → email_watcher in sequence. Also exposes `run_scrape_only()`, `run_match_only()`, `run_apply_only()` for the dashboard wizard. |
| `scraper.py` | Fetches jobs from LinkedIn via the Apify actor. Deduplicates within a run, upserts into `seen_jobs`, loads cached jobs within the `posted_limit` window. Applies a three-tier title pre-filter (safe compounds → skip list → tech keywords) to drop clearly irrelevant titles before Claude is called. Excludes already-applied jobs by URL and by company+title combo. |
| `matcher.py` | Reads the uploaded resume PDF, sends each job + resume to Claude Haiku for scoring. Enforces stack-mismatch score caps and language-category rules. Returns `detailed_reasoning` per requirement. Caches scores by resume MD5 hash. Filters by German level. Skips jobs already in the applications table by company+title match. After each run, syncs scores back into `seen_jobs` so audit and cache stats stay consistent. |
| `dedup.py` | Filters matched jobs against the `applications` table to skip already-applied positions. |
| `tailor.py` | Generates a tailored resume and cover letter per job (currently skipped in the wizard flow — uploaded documents are used directly). |
| `applier.py` | Playwright automation for LinkedIn Easy Apply. Handles multi-step forms, file uploads, profile field auto-fill (salary, notice period, work permit, relocate preference), and support document attachment. |
| `email_watcher.py` | IMAP polling for confirmation emails and interview invitations. Updates application status in the DB. |
| `interview_handler.py` | Classifies inbound emails as recruiter call / technical / final / rejection. Auto-confirms or pauses for user input. |
| `excel_updater.py` | Appends or updates rows in the Excel application tracker. Never deletes rows. |
| `db.py` | All SQLite interactions — schema creation, migrations, upserts, cache helpers, resume hash functions. |
| `dashboard.py` | Flask web server. Serves the single-page HTML dashboard, all `/api/*` endpoints, SSE log stream, and the pipeline wizard. |
| `config.py` | Loads and validates `config.yaml`, resolves file paths, sets up logging. |
| `config.yaml` | User configuration — gitignored. |
| `.env` | API keys — gitignored. |
| `.env.example` | Template for `.env`. |
| `requirements.txt` | Python dependencies. |

### Uploads folder

All runtime data lives under `uploads/` (gitignored, auto-created on first run):

```
uploads/
├── resume_en.pdf              ← uploaded via web UI
├── jobhunt.db                 ← SQLite database (auto-created)
├── linkedin_session.json      ← saved LinkedIn session (auto-created)
└── support/                   ← support documents (slot-based)
    ├── cover_letter.pdf
    ├── reference_1.pdf
    ├── reference_2.pdf
    ├── reference_3.pdf
    ├── photo.pdf
    ├── german_cert.pdf
    ├── university_doc.pdf
    └── other.pdf
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
  - Backend Engineer
  - Java Developer
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

The main tab. Layout:

- **Agent Control** (full width, top) — the four-step wizard
- **Left column** — Upload Resume · Job Titles · Application Profile (with Support Documents)
- **Right column** — Config Editor
- **Live Log** (full width, bottom)

**Before your first run:**

- Upload your resume PDF in the *Upload Files* panel.
- Fill in the *Application Profile* panel — notice period, salary, languages, etc. These are auto-filled into LinkedIn Easy Apply forms.
- Upload any support documents (reference letters, certificates) in the *Support Documents* section — each slot has a fixed name so the file is reused across runs.
- Select job titles in the *Job Titles* panel, or add custom ones.
- Connect LinkedIn — click *Connect LinkedIn* in the Config Editor, complete browser login with 2FA, then click *Done*. The session cookie is saved and reused on future runs.

**Step 1 — Scrape**

Fetches jobs from LinkedIn for every role × location combination in config. When done, displays a table of all scraped jobs with:
- Green *New* badge — fetched fresh from Apify this run
- Blue *From cache* badge — loaded from `seen_jobs` based on posted date

Jobs already in the Applied/Interviews/Rejected tabs are excluded at this stage by URL and by company+title combination.

**Step 2 — Review**

Review the scraped job list, filter by title or company, then proceed to matching.

**Step 3 — Match**

Sends each job to Claude Haiku for scoring. The stats bar shows:

```
92 scored by Claude · 6 above 70% threshold · 86 below threshold · 14 fresh · 78 from cache
```

Each row has **View Job**, **Apply**, and **Dismiss** buttons. Click anywhere on a row to open the detail panel — full description, match summary, per-requirement reasoning, and skip reason.

Click **Audit** to open the scoring breakdown: passed threshold / score too low / German too high / wrong tech stack, with collapsible job lists for each category.

Jobs requiring German levels in your `skip_german_levels` list are excluded automatically regardless of the score slider.

**Step 4 — Apply**

Select jobs individually or click *Apply All*. Playwright submits LinkedIn Easy Apply, auto-filling contact fields and profile fields from config. Results are logged to the DB and the Excel tracker is updated.

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

After each match run, scores are synced from `matched_jobs` back into `seen_jobs` so the audit modal and the stats bar always read from the same source.

---

## Filtering Logic

Jobs pass through four independent filter layers before appearing in the matched table.

### 1. Applied deduplication (scraper + matcher, free)

At scrape time, jobs are excluded if:
- Their URL appears in the `applications` table.
- Their company+title combination matches a row in the `applications` table.

The same company+title check runs again in the matcher before any API call is made, catching jobs that slipped through via the cache path.

### 2. Title pre-filter (scraper, free — no API calls)

`is_title_relevant()` in `scraper.py` uses a three-tier check:

1. **Safe compound bypass** — titles containing `backend engineer`, `backend developer`, `software engineer`, `software developer`, `full stack`, or `platform engineer` pass immediately.
2. **Skip list** — titles matching wrong-stack or non-tech roles are dropped.
3. **Tech keyword pass-through** — any remaining title containing `engineer`, `developer`, `architect`, `java`, `devops`, `cloud`, `data`, `kotlin`, `scala`, etc. passes to Claude.

### 3. Claude Haiku scoring (matcher)

Claude scores each job 0–100 using a detailed prompt with explicit rules:

**Scoring scale:**

| Score | Meaning |
|-------|---------|
| 90–100 | Near-perfect match |
| 75–89 | Strong match |
| 60–74 | Good match |
| 50–59 | Partial match |
| 30–49 | Weak match |
| 0–29 | Poor match |

**Stack-mismatch caps** (only when the diverging language is confirmed PRIMARY):

| Primary required stack | Cap |
|------------------------|-----|
| Java / Spring Boot / Backend / JVM | No cap |
| Python (confirmed in title + description) | 45 |
| Go, Rust, Ruby, PHP | 40 |
| C++ (systems/embedded) | 40 |
| React / Angular / Vue (pure frontend) | 35 |
| Mixed stack where Java is sufficient | No cap |

**Language category clause rule** — when a job lists languages with "or other [category] languages", Claude judges by category, not by example:
- `"C++, Python, or other object-oriented languages"` → Java qualifies
- `"Python, Ruby, or scripting languages"` → Java does not qualify

**Experience level rules** — minor gaps (< 3 years) cause at most -10 penalty; gaps of 5+ years are required to hard-block a match.

**Golden rule** — when in doubt, favour the candidate. A missed good match is worse than one extra application.

### 4. Post-scoring filters

Applied after every job is scored (both fresh and cached):

- **German level** — substring match against `skip_german_levels`. Matched jobs get `skip_reason` set and `match_score` forced to 0.
- **min_match_score** — jobs below the configured threshold are shown dimmed in the table (adjustable per-run via the slider).
- **German level hard exclusion** — the UI always hides jobs whose `skip_reason` contains "German level", even if the score slider is set to 0.

---

## Database Schema

File: `uploads/jobhunt.db` (SQLite, WAL mode)

| Table | Description |
|-------|-------------|
| `applications` | Every submitted application — company, role, location, date, status, verdict, match score, URLs, archive path. |
| `seen_jobs` | Persistent scraper + scorer cache. Stores URL, title, company, location, description, `posted_date`, `first_scraped_at`, match scores, `resume_hash`, `applied`, `dismissed` flags. |
| `scraped_jobs` | Current run's scraped jobs. Cleared at the start of each new scrape. |
| `matched_jobs` | Current run's scored jobs. Cleared at the start of each new scrape. Single source of truth for the stats bar and audit modal. |
| `upcoming_interviews` | Manually tracked interview schedule — date, company, role, type, time, format, notes. |
| `priority_tasks` | Manually tracked to-do list — priority, company, action, deadline. |
| `settings` | Key/value store for app state (e.g. LinkedIn session cookie path). |

---

## Configuration Reference (`config.yaml`)

### Search & pipeline

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `locations` | list | `[Berlin]` | Cities to search. Each role is searched in each location. |
| `roles` | list | — | Job titles to search on LinkedIn. Set via the dashboard UI. |
| `min_match_score` | int (0–100) | `70` | Jobs below this score are filtered out after matching. |
| `max_applications_per_day` | int | `10` | Hard cap on applications submitted per pipeline run. |
| `scrape_pool_size` | int | `25` | Max jobs fetched from Apify per role/location combination. |
| `posted_limit` | string | `24h` | Time window for job freshness. Options: `1h`, `24h`, `week`, `month`. |
| `smart_scrape` | bool | `true` | Pre-filters scraped jobs by title keywords before sending to Claude. Saves API calls. |
| `skip_german_levels` | list | `[C1, C2, native, Muttersprache, verhandlungssicher, fließend]` | Jobs requiring any of these German levels are skipped. |

### Identity & contact

| Field | Type | Description |
|-------|------|-------------|
| `full_name` | string | Your full name, used in application documents. |
| `hotmail_address` | string | Hotmail/Outlook address for sending and receiving email. |
| `notify_email` | string | Address for pipeline notifications. |
| `phone` | string | Phone number included in application forms. |

### Application Profile (auto-filled into Easy Apply forms)

| Field | Type | Description |
|-------|------|-------------|
| `notice_period` | string | e.g. `3 months`. Filled into notice period fields. |
| `earliest_start` | string | e.g. `3 months`. Filled into start date fields. |
| `salary_expectation` | int | Gross annual salary in `salary_currency`. |
| `salary_currency` | string | `EUR`, `USD`, `GBP`, or `CHF`. |
| `years_of_experience` | int | Filled into years of experience fields. |
| `work_permit` | string | e.g. `EU citizen`. Filled into work authorisation fields. |
| `current_location` | string | e.g. `Berlin, Germany`. |
| `willing_to_relocate` | bool | If true, selects "Yes" on relocate radio buttons. |
| `willing_to_travel` | string | `no`, `occasionally`, `25%`, `50%`, or `100%`. |
| `languages` | list | List of `{language, level}` objects. |

### Behaviour

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `auto_confirm_recruiter_call` | bool | `true` | Automatically confirm recruiter call scheduling emails. |
| `auto_confirm_technical` | bool | `false` | Automatically confirm technical interview scheduling emails. |
| `headless` | bool | `true` | Run Playwright browser in headless mode. Set to `false` to watch the browser during debugging. |
| `confirm_before_apply` | bool | `true` | Prompt for confirmation before each application (CLI pipeline only). |
| `retry_captcha_as_manual` | bool | `false` | Log CAPTCHA hits as "manual apply needed" instead of skipping. |

### Legacy (CLI only)

| Field | Description |
|-------|-------------|
| `cv_root` | Absolute path to a local CV folder. Not needed when using the web UI. |
| `tracker_file` | Filename of the Excel tracker (used by `excel_updater.py`). |
| `log_file` | Path to the persistent log file relative to `cv_root`. |

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
