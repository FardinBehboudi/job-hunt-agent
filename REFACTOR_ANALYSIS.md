# Job Hunt Agent — Codebase Analysis & Refactoring Plan

## Current State: 17 Python Files (Flat Structure)

### ✅ KEEP (Core Pipeline)
| File | Purpose | Status |
|------|---------|--------|
| **main.py** | Daily orchestrator | Essential |
| **config.py** | Config loading | Essential |
| **scraper.py** | Fetch jobs from Apify | Essential |
| **matcher.py** | AI scoring (Claude) | Essential |
| **dedup.py** | Prevent duplicates | Essential |
| **tailor.py** | Document tailoring | ⚠️ DISABLED (user choice) |
| **applier.py** | Apply dispatcher | Essential |
| **excel_updater.py** | Excel tracker updates | Essential |
| **email_watcher.py** | Inbox monitoring | Essential |
| **interview_handler.py** | Interview auto-confirm | Essential |

### ⚠️ REDUNDANCY ISSUES
| File | Problem | Recommendation |
|------|---------|-----------------|
| **apply_clicker.py** | Button clicker (v1) | ❌ DELETE - choose ONE clicker |
| **linkedin_clicker.py** | Button clicker (v2) | ✅ KEEP - simpler, works well |
| Both are nearly identical | Wastes maintenance effort | Pick linkedin_clicker.py |

### 📊 SUPPORTING FILES
| File | Purpose | Notes |
|------|---------|-------|
| **apply_events.py** | SSE event queue | Move to `applier/events.py` |
| **linkedin_applier.py** | Easy Apply form filling | Move to `applier/` |
| **external_applier.py** | External ATS handler | Move to `applier/` |
| **db.py** | SQLite layer | Move to `tracking/` |
| **dashboard.py** | Web UI dashboard | Keep in `dashboard/` (optional) |

---

## Proposed New Structure

```
job-hunt-agent/
│
├── 📁 core/                          # Configuration & orchestration
│   ├── main.py
│   ├── config.py
│   ├── requirements.txt
│   ├── .env.example
│   ├── setup_scheduler.ps1
│   ├── README.md
│   └── .gitignore
│
├── 📁 scraper/                       # Job scraping
│   └── scraper.py
│
├── 📁 matcher/                       # AI matching
│   └── matcher.py
│
├── 📁 dedup/                         # Deduplication
│   ├── dedup.py
│   └── db.py                         # MOVED FROM ROOT
│
├── 📁 tailor/                        # Document tailoring (disabled)
│   └── tailor.py
│
├── 📁 applier/                       # Application & clicking
│   ├── __init__.py
│   ├── applier.py                    # Dispatcher
│   ├── linkedin_clicker.py           # KEPT (apply_clicker.py DELETED)
│   ├── linkedin_applier.py           # MOVED FROM ROOT
│   ├── external_applier.py           # MOVED FROM ROOT
│   └── events.py                     # MOVED FROM apply_events.py
│
├── 📁 tracking/                      # Application tracking
│   ├── excel_updater.py
│   ├── email_watcher.py
│   └── interview_handler.py
│
├── 📁 dashboard/                     # Web UI (optional)
│   └── dashboard.py
│
├── 📁 data/                          # Data storage
│   ├── uploads/
│   ├── support/
│   └── config.yaml                   # Gitignored
│
├── .env                              # Gitignored
├── .gitignore
└── README.md
```

---

## Actions to Take

### 1️⃣ DELETE (Redundant)
- ❌ `apply_clicker.py` — Keep `linkedin_clicker.py` instead

### 2️⃣ MOVE (Reorganize)
- `apply_events.py` → `applier/events.py`
- `linkedin_applier.py` → `applier/linkedin_applier.py`
- `external_applier.py` → `applier/external_applier.py`
- `db.py` → `dedup/db.py`

### 3️⃣ CREATE Folders
- `core/` — Copy main.py, config.py, requirements.txt, etc.
- `scraper/` — scraper.py
- `matcher/` — matcher.py
- `dedup/` — dedup.py + db.py
- `tailor/` — tailor.py
- `applier/` — applier.py + clickers + form fillers + events
- `tracking/` — excel_updater, email_watcher, interview_handler
- `dashboard/` — dashboard.py (optional)
- `data/` — uploads/, support/, config.yaml

### 4️⃣ Update Imports
All files will need imports updated to reflect new paths:
- `from applier.linkedin_clicker import click_apply_button`
- `from applier.events import _emit`
- `from dedup.db import get_connection`
- etc.

---

## Benefits

✅ **Clarity** — Related files grouped by functionality
✅ **Maintainability** — Easy to find what you need
✅ **Redundancy removed** — One clicker module (linkedin_clicker.py)
✅ **Separation of concerns** — Each folder has clear purpose
✅ **Scalability** — Easy to add new features to specific modules

---

## Files by Category (For Review)

**Lines of Code (rough estimate)**
- applier.py: 300+ (dispatches, imports many)
- linkedin_applier.py: 500+ (form filling logic)
- matcher.py: 200+ (AI scoring)
- scraper.py: 200+ (Apify calls)
- tailor.py: 300+ (document tailoring — DISABLED)
- email_watcher.py: 250+ (IMAP + email handling)
- interview_handler.py: 200+ (email classification)
- linkedin_clicker.py: 200+ (button clicking strategies)
- apply_clicker.py: 200+ (SIMILAR — DELETE)

**Can be deleted without impact:**
- apply_clicker.py (linkedin_clicker.py is superior)

---

## Next Steps

Ready to implement? When you confirm, I will:
1. Create all new folders
2. Move files to correct locations
3. Update ALL imports globally
4. Delete redundant files
5. Verify nothing breaks (imports check)
6. Provide a summary of changes
