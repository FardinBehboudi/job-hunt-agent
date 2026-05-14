# Job Hunt Agent

Automated job hunting pipeline: scrapes LinkedIn + Stepstone → AI scoring → tailored CV/cover letter → auto-applies → reads confirmation emails → handles interview scheduling.

---

## Project layout

```
job-hunt-agent/          ← this repo (code only, no personal data)
├── main.py              ← daily pipeline orchestrator
├── scraper.py           ← Apify LinkedIn + Stepstone
├── matcher.py           ← Claude AI scoring
├── dedup.py             ← applied_jobs_log.json read/write
├── tailor.py            ← tailored resume + cover letter
├── applier.py           ← Playwright browser automation
├── email_watcher.py     ← IMAP inbox polling (imaplib)
├── interview_handler.py ← auto-confirm or pause for user
├── excel_updater.py     ← tracker spreadsheet updates
├── config.py            ← loads and validates config.yaml
├── config.yaml          ← gitignored — your settings go here
├── .env                 ← gitignored — your API keys go here
├── .env.example         ← copy this to .env
└── requirements.txt

C:\Users\...\Dropbox\CV\   ← personal documents (not in this repo)
├── agent/
│   ├── resume_en.docx           ← editable base resume (REQUIRED)
│   ├── resume_en.pdf            ← PDF copy
│   ├── resume_de.pdf            ← German resume
│   ├── cover_letter_template.docx  ← or .pdf
│   └── applied_jobs_log.json    ← auto-created
├── support/                     ← reference letters, certificates
├── application_history/         ← auto-created per application
└── job_application_tracker_v34.xlsx
```

---

## One-time setup

### 1. Install dependencies

```powershell
pip install -r requirements.txt
playwright install chromium
```

### 2. Copy `.env.example` → `.env` and fill in keys

```
ANTHROPIC_API_KEY=...
APIFY_API_TOKEN=...
HOTMAIL_PASSWORD=...
```

**Anthropic API key** — console.anthropic.com

**Apify API token** — apify.com → Settings → Integrations

**Hotmail password** — your normal account password.
If two-factor authentication is enabled, generate an App Password instead:
account.microsoft.com → Security → Advanced security options → App passwords

### 3. Edit `config.yaml`

The email address (`f_behboud@hotmail.com`) is already set. Fill in:
- `phone` — your phone number
- `cv_root` — confirm the path to your Dropbox CV folder

### 4. Add `resume_en.docx` to Dropbox

The tailor needs an editable Word version of your resume at:
`<cv_root>/agent/resume_en.docx`

### 5. Test email connection

```powershell
python email_watcher.py --test-auth
```

Prints the last 10 email subject lines. If you see them, IMAP is working.

### 6. Test run

```powershell
python main.py
```

---

## Windows Task Scheduler setup

Two scheduled tasks are needed:

### Task 1 — Daily pipeline (08:00)

```powershell
schtasks /create /tn "JobHuntAgent-Daily" /tr "python C:\path\to\main.py" /sc daily /st 08:00 /ru SYSTEM
```

### Task 2 — Inbox polling (every 2 hours)

```powershell
schtasks /create /tn "JobHuntAgent-Email" /tr "python C:\path\to\email_watcher.py" /sc hourly /mo 2 /ru SYSTEM
```

Replace `C:\path\to\` with the actual project path.

---

## PDF export

The tailor uses **mammoth** (docx → HTML) + **weasyprint** (HTML → PDF).

On Windows, weasyprint requires GTK+ runtime libraries. If PDF export fails, install:
- GTK3 runtime for Windows: https://github.com/tschoonj/GTK-for-Windows-Runtime-Environment-Installer

Alternatively, install LibreOffice — it will be used as a fallback automatically.

---

## Status values

| Status | Meaning |
|---|---|
| Applied ✓ | Submitted and confirmed by email |
| Applied — unconfirmed (no email after 5 days) | No reply received |
| Call Scheduled ✓ | Recruiter call auto-confirmed |
| ⏸️ Technical — awaiting your confirmation | Waiting for your slot choice |
| Technical Scheduled ✓ | Technical interview confirmed |
| ⏸️ Final — awaiting your confirmation | Waiting for your slot choice |
| Final Scheduled ✓ | Final interview confirmed |
| Rejected | |
| Offer Received 🎉 | |

---

## Logs

All activity is written to `<cv_root>/agent/agent.log`.
