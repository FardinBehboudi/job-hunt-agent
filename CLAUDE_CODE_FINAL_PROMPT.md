# FINAL MIGRATION PROMPT FOR CLAUDE CODE

**Feed this entire prompt to Claude Code to implement the complete migration.**

---

## OBJECTIVE

1. ✅ Replace Playwright with Claude in Chrome for job applications
2. ✅ Clean up unnecessary MD documentation files (keep only README.md)
3. ✅ Keep all Python files as designed in PROMPT_FOR_CLAUDE_CODE.md
4. ✅ Move all file setup to project root
5. ✅ Read resume and required files from Dropbox CV folder
6. ✅ Update apply debugger to work with new system

---

## PART 1: MD FILES CLEANUP

### Files to DELETE (Documentation - Not Needed)

Delete these documentation files - they're outdated or redundant:

```
DELETE THESE MD FILES:
- EXTERNAL_APPLY_WORKFLOW_SUMMARY.md
- SYSTEM_STATUS_REPORT.md
- QUICK_START_GUIDE.md
- CLAUDE_CODE_MIGRATION_PROMPT.md
- PROMPT_FOR_CLAUDE_CODE.md
- CLAUDE_CODE_COMPLETE_MIGRATION.md
```

**Keep only:**
- README.md (if exists, update it)
- CLAUDE.md (project instructions)

### New Master Documentation: README.md

Create a single, comprehensive README.md:

```markdown
# Job Hunt Automation System

## Quick Start

### Setup
```bash
pip install -r requirements.txt
```

### Configuration
Edit `config.yaml` with your profile:
```yaml
application_profile:
  first_name: "Felix"
  last_name: "Behboudi"
  email: "your.email@example.com"
  phone: "+49 123 456789"
  linkedin_url: "https://linkedin.com/in/..."
  github_url: "https://github.com/..."
  current_location: "Berlin"
  years_of_experience: 5
  salary_expectation: 75000
  work_permit: "German citizen"
```

### Resume Files
Place in `~/Dropbox/CV/`:
- `resume_en.pdf` - English resume
- `resume_de.pdf` - German resume

### Run Application
```bash
# Full automation
python main.py

# Test single job
python apply_debugger.py --url "https://www.linkedin.com/jobs/view/123456/"
```

## How It Works

1. **Job Scraping** - Finds jobs from LinkedIn/Indeed/Glassdoor
2. **Job Matching** - AI scores jobs (70+ match score)
3. **Application** - Claude in Chrome + Playwright applies automatically
4. **Logging** - Results saved to JSON and Excel

## Features

✅ Easy Apply automation  
✅ External ATS form filling  
✅ Resume upload  
✅ Intelligent form decisions via Claude AI  
✅ Result tracking and logging  

## Architecture

- **main.py** - Orchestration
- **apply_agent.py** - Apply automation using Claude in Chrome
- **apply_integration.py** - Connect job matcher to applier
- **apply_logger.py** - Log to JSON/Excel/DB
- **apply_debugger.py** - Debug tool for testing

## Troubleshooting

Check `outputs/applied_jobs_log.json` for application results.

Use `apply_debugger.py` to test individual jobs.

## Configuration Files

- `config.yaml` - User profile and settings
- `.env` - API keys (ANTHROPIC_API_KEY, etc)
- `core/config.yaml` - Default configuration

## Output Files

All results saved to `outputs/`:
- `applied_jobs_log.json` - Application log
- `job_application_tracker.xlsx` - Excel tracker
- `debug_*.png` - Debug screenshots

```

---

## PART 2: PYTHON FILES (Keep as Designed)

Use the Python files from `PROMPT_FOR_CLAUDE_CODE.md` exactly as provided:

1. ✅ **apply_agent.py** — Complete apply automation
2. ✅ **apply_integration.py** — Integration with job matcher
3. ✅ **apply_logger.py** — Logging system
4. ✅ **config_loader.py** — Load resume from Dropbox CV

**No changes to Python files. Use them exactly as provided in PROMPT_FOR_CLAUDE_CODE.md**

---

## PART 3: DROPBOX RESUME LOADING

In `apply_agent.py` and `config_loader.py`, ensure:

```python
# Load from Dropbox CV folder
dropbox_cv_path = Path.home() / "Dropbox" / "CV"
resume_en = dropbox_cv_path / "resume_en.pdf"
resume_de = dropbox_cv_path / "resume_de.pdf"
```

**The prompt already handles this in the Python templates.**

---

## PART 4: PROJECT ROOT FILE ORGANIZATION

### Files to Move to Root

Move from `core/` to project root if needed:
```bash
# Move to root
mv core/config.yaml config.yaml
```

### Final Project Structure

```
~/Projects/claude/job-hunt-agent/
├── main.py                          (orchestration)
├── config.yaml                      (moved from core/)
├── .env                             (credentials)
├── .gitignore                       (git ignore)
├── README.md                        (MASTER documentation - only MD file)
├── CLAUDE.md                        (project instructions - keep)
│
├── apply_agent.py                   (NEW - from PROMPT_FOR_CLAUDE_CODE.md)
├── apply_integration.py             (NEW - from PROMPT_FOR_CLAUDE_CODE.md)
├── apply_logger.py                  (NEW - from PROMPT_FOR_CLAUDE_CODE.md)
├── config_loader.py                 (NEW - load CV from Dropbox)
├── apply_debugger.py                (NEW - debug tool)
│
├── core/                            (keep if has other code)
│   ├── __init__.py
│   └── (other core modules)
│
├── matcher/                         (keep - job matching code)
│   ├── __init__.py
│   └── (matching code)
│
├── outputs/                         (auto-created - results here)
│   ├── applied_jobs_log.json        (auto)
│   └── job_application_tracker.xlsx (auto)
│
└── [ALL OLD MD FILES DELETED]
```

---

## PART 5: APPLY DEBUGGER

Create `apply_debugger.py` to test individual applications:

```bash
# Test Easy Apply
python apply_debugger.py --url "https://www.linkedin.com/jobs/view/EASY_APPLY_ID/"

# Test External Apply  
python apply_debugger.py --url "https://www.linkedin.com/jobs/view/EXTERNAL_APPLY_ID/"
```

**This is already included in the PROMPT_FOR_CLAUDE_CODE.md template.**

---

## IMPLEMENTATION STEPS

### Step 1: Delete MD Files
```bash
cd ~/Projects/claude/job-hunt-agent/

# Delete old documentation
rm EXTERNAL_APPLY_WORKFLOW_SUMMARY.md
rm SYSTEM_STATUS_REPORT.md
rm QUICK_START_GUIDE.md
rm CLAUDE_CODE_MIGRATION_PROMPT.md
rm PROMPT_FOR_CLAUDE_CODE.md
rm CLAUDE_CODE_COMPLETE_MIGRATION.md
```

### Step 2: Create Python Files
Use the complete templates from `PROMPT_FOR_CLAUDE_CODE.md`:
- ✅ apply_agent.py
- ✅ apply_integration.py
- ✅ apply_logger.py
- ✅ config_loader.py
- ✅ apply_debugger.py

**Copy the code exactly as provided in PROMPT_FOR_CLAUDE_CODE.md**

### Step 3: Create README.md
Create single master documentation file with content from "PART 1" above.

### Step 4: Modify main.py
Replace Playwright calls with Claude in Chrome calls (as per PROMPT_FOR_CLAUDE_CODE.md).

### Step 5: Move config.yaml
Move from `core/` to project root if needed.

### Step 6: Test
```bash
python apply_debugger.py --url "https://www.linkedin.com/jobs/view/TEST_JOB_ID/"
```

---

## SUCCESS CRITERIA

✅ **All of these must be true:**

1. All old MD files deleted (except README.md and CLAUDE.md)
2. Only README.md as documentation
3. All Python files created as per PROMPT_FOR_CLAUDE_CODE.md
4. Resume loads from `~/Dropbox/CV/resume_*.pdf`
5. Config.yaml in project root
6. Easy Apply test job applies successfully
7. External Apply test job applies successfully
8. apply_debugger.py works for testing
9. Results logged to `outputs/applied_jobs_log.json`
10. Clean, organized project structure

---

## FINAL NOTES

- **Keep Python files** from PROMPT_FOR_CLAUDE_CODE.md exactly as designed
- **Only delete MD documentation files** (not Python files)
- **Use README.md as single documentation source**
- **Resume from Dropbox** - already implemented in Python code
- **All files at project root** - no nested folders except core/, matcher/, outputs/

---

## REFERENCE: USE PROMPT_FOR_CLAUDE_CODE.md

For all Python file implementations, refer to:
`PROMPT_FOR_CLAUDE_CODE.md`

This prompt only handles:
1. Deleting unnecessary MD files
2. Creating single README.md
3. File organization to root
4. Debugging setup

---

**END OF PROMPT**

Feed this to Claude Code. It will:
1. Delete old MD files
2. Create all Python files from PROMPT_FOR_CLAUDE_CODE.md
3. Write README.md
4. Organize files to root
5. Keep Python code exactly as designed
