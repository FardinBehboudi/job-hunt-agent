# Job Hunt Automation System

Fully automated job application pipeline with AI-powered scraping, applying, and email tracking.

## Quick Start

### 1. Prerequisites
- Python 3.11+
- API keys: `ANTHROPIC_API_KEY`, `APIFY_API_TOKEN`
- Azure app registration (for email OAuth2)
- Resume files in `~/Dropbox/CV/`

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Configure
Edit `data/config.yaml` with your profile:
```yaml
contact:
  email: "you@hotmail.com"
application_profile:
  first_name: "..."
  last_name: "..."
  ...
```

Copy `.env.example` to `.env` and fill in your keys:
```
ANTHROPIC_API_KEY=sk-ant-...
APIFY_API_TOKEN=apify_api_...
LINKEDIN_EMAIL=you@hotmail.com
LINKEDIN_PASSWORD=...
```

### 4. One-time email auth (Microsoft OAuth2)
```bash
python tracking/get_token.py
```
Opens a device-code browser flow — sign in with your Outlook/Hotmail account.
Saves `MS_REFRESH_TOKEN` to `.env` automatically.

### 5. Run
```bash
# Full pipeline (scrape → match → apply)
python main.py

# Email processor only
python tracking/email_processor.py

# Dashboard
python dashboard/dashboard.py
```

---

## How It Works

1. **Scraping** — Finds jobs from LinkedIn via Apify
2. **Matching** — Claude AI scores each job against your profile (min 70%)
3. **Applying** — Claude in Chrome fills and submits application forms
4. **Tracking** — SQLite DB logs every application with status
5. **Email monitoring** — Reads recruiter emails via Microsoft Graph API, classifies them (Rejected / In Review / Interview / Offer / etc.), and files them into the correct Outlook folders

---

## Email Handler

The email pipeline runs every 15 minutes (via Windows Task Scheduler) and:

- Fetches unread emails from: `Inbox`, `Focus`, `Other`, `Junk Email`
- Classifies each email with Claude AI
- Auto-files **Rejected** and **In Review** emails above 90% confidence
- Queues everything else for manual approval in the Email Admin dashboard tab
- Extracts calendar events from Interview / Code Challenge emails

### Outlook folder structure created automatically
```
Applications/
  Rejected/
  In Review/
  Next Step/
    Interview/Todo/
    Code Challange/ToDo/
  Offer/
```

### Email Admin dashboard
Open `http://localhost:5000` → **Email Admin** tab:
- Review pending emails with AI classification and confidence score
- Approve / skip individual emails or bulk-approve all
- Override the target folder per email
- View move history and logs
- Adjust auto-move threshold and on/off toggle in Settings

---

## Architecture

```
job-hunt-agent/
├── main.py                  # Orchestration entry point
├── data/config.yaml         # User profile & preferences
├── .env                     # API keys & secrets (never commit)
├── core/
│   └── config.py            # Config loader + logging setup
├── scraping/                # Apify job scraping
├── matching/                # Claude AI job scoring
├── applying/                # Claude-in-Chrome form filling
├── tracking/
│   ├── email_processor.py   # Fetch → classify → stage emails
│   ├── email_executor.py    # Move emails via Graph API
│   ├── ms_auth.py           # MSAL OAuth2 token management
│   ├── ms_graph.py          # Microsoft Graph API helpers
│   └── get_token.py         # One-time auth script
├── dedup/
│   └── db.py                # SQLite helpers (applications, email staging)
├── dashboard/
│   ├── dashboard.py         # Flask app + Job Hunt Agent UI
│   └── email_admin.py       # Email Admin Blueprint (/email-admin/)
└── tests/                   # pytest suite (23 tests)
```

---

## Environment Variables

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API key |
| `APIFY_API_TOKEN` | Apify scraping token |
| `LINKEDIN_EMAIL` | LinkedIn login |
| `LINKEDIN_PASSWORD` | LinkedIn password |
| `MS_REFRESH_TOKEN` | Auto-set by `get_token.py` — do not edit manually |

---

## Testing

```bash
pytest                          # run all tests
pytest tests/test_email_db.py   # DB schema tests
pytest tests/test_email_processor.py
pytest tests/test_email_executor.py
```

---

## Task Scheduler (Windows)

To run the email processor every 15 minutes automatically:

1. Open **Task Scheduler** → Create Basic Task
2. Trigger: Daily, repeat every 15 minutes
3. Action: Start a program
   - Program: `C:\path\to\python.exe`
   - Arguments: `tracking\email_processor.py`
   - Start in: `C:\Users\f_beh\Projects\claude\job-hunt-agent`

---

## License

Internal use only.
