# COMPLETE MIGRATION PROMPT FOR CLAUDE CODE

**Feed this entire prompt to Claude Code. It will implement everything including cleanup.**

---

## OBJECTIVE

**Complete migration from Playwright to Claude in Chrome + codebase cleanup**

1. ✅ Replace Playwright with Claude in Chrome for job applications
2. ✅ Clean up codebase (remove unnecessary files and MD documentation)
3. ✅ Move all configuration and setup to project root
4. ✅ Read resume and user data from Dropbox CV folder
5. ✅ Update debugger to work with new system
6. ✅ Keep only essential files in clean structure

---

## PART 1: CODEBASE CLEANUP

### Files to DELETE (Not Needed)

Delete these files/folders entirely - they're generated docs or outdated:

```
DELETE:
- /EXTERNAL_APPLY_WORKFLOW_SUMMARY.md
- /SYSTEM_STATUS_REPORT.md
- /QUICK_START_GUIDE.md
- /CLAUDE_CODE_MIGRATION_PROMPT.md
- /PROMPT_FOR_CLAUDE_CODE.md
- /applier/events.py (if only used for logging)
- /applier/__init__.py (if empty)
- /applier/ (entire folder with old Playwright code)
- /dedup/ (will be recreated if needed)
- /uploads/ (old debug files)
- /.claude/ (hidden config)
```

### Folders to KEEP

```
KEEP:
- /core/ → config.yaml and loaders
- /jobs/ → job data/cache if exists
- /outputs/ → new results go here
- /.env → credentials
- /.git/ → version control
- /.gitignore → git ignore rules
```

### New Project Structure (After Cleanup)

```
~/Projects/claude/job-hunt-agent/
├── main.py                              (orchestration - MODIFY)
├── config.yaml                          (user config - from core/)
├── .env                                 (credentials)
├── .gitignore                           (updated)
│
├── [NEW FILES - created by Claude Code]
├── apply_agent.py                       (apply automation)
├── apply_integration.py                 (job matcher integration)
├── apply_logger.py                      (logging system)
├── config_loader.py                     (load CV data from Dropbox)
├── apply_debugger.py                    (debug tool - NEW)
├── test_apply.py                        (test script - optional)
│
├── core/
│   ├── config.yaml                      (move here from /core/)
│   └── __init__.py
│
├── matcher/                             (existing job matching code)
│   ├── __init__.py
│   └── ... (existing files)
│
├── outputs/                             (auto-created, all results here)
│   ├── applied_jobs_log.json            (auto)
│   ├── job_application_tracker.xlsx     (auto)
│   └── debug_*.png                      (auto)
│
└── README.md                            (ONE document - all instructions)
```

---

## PART 2: CONFIGURATION LOADING FROM DROPBOX

### New File: `config_loader.py`

**Purpose:** Load user profile and resume from Dropbox CV folder

```python
"""
config_loader.py - Load user configuration from Dropbox CV folder.

This module loads:
- Resume PDF (EN and DE)
- Application profile (from config.yaml)
- User preferences
"""

import os
import json
from pathlib import Path
from typing import Optional, Dict


class ConfigLoader:
    """Load configuration from Dropbox CV folder and project root."""
    
    def __init__(self):
        # Paths
        self.dropbox_cv_path = Path.home() / "Dropbox" / "CV"
        self.project_root = Path(__file__).parent
        self.config_path = self.project_root / "config.yaml"
    
    def load_config(self) -> dict:
        """Load complete configuration."""
        return {
            "application_profile": self._load_profile(),
            "resume_paths": self._load_resume_paths(),
            "settings": self._load_settings(),
            "paths": {
                "output": self.project_root / "outputs",
                "dropbox_cv": self.dropbox_cv_path,
                "project_root": self.project_root
            }
        }
    
    def _load_profile(self) -> dict:
        """Load application profile from config.yaml"""
        import yaml
        
        if self.config_path.exists():
            with open(self.config_path) as f:
                config = yaml.safe_load(f)
                return config.get("application_profile", {})
        
        # Default profile structure
        return {
            "first_name": "",
            "last_name": "",
            "email": "",
            "phone": "",
            "linkedin_url": "",
            "github_url": "",
            "portfolio_url": "",
            "current_location": "Berlin",
            "notice_period": "Immediately available",
            "salary_expectation": "75000",
            "salary_currency": "EUR",
            "willing_to_relocate": True,
            "work_permit": "German citizen",
            "years_of_experience": "5",
            "languages": ["English", "German"]
        }
    
    def _load_resume_paths(self) -> dict:
        """Load resume PDF paths from Dropbox CV folder"""
        
        resumes = {}
        
        # Check for resume_en.pdf
        resume_en = self.dropbox_cv_path / "resume_en.pdf"
        if resume_en.exists():
            resumes["en"] = str(resume_en)
        else:
            # Try alternate names
            for name in ["resume_english.pdf", "CV_english.pdf", "CV_en.pdf"]:
                alt = self.dropbox_cv_path / name
                if alt.exists():
                    resumes["en"] = str(alt)
                    break
        
        # Check for resume_de.pdf
        resume_de = self.dropbox_cv_path / "resume_de.pdf"
        if resume_de.exists():
            resumes["de"] = str(resume_de)
        else:
            # Try alternate names
            for name in ["resume_german.pdf", "CV_german.pdf", "CV_de.pdf", "Lebenslauf.pdf"]:
                alt = self.dropbox_cv_path / name
                if alt.exists():
                    resumes["de"] = str(alt)
                    break
        
        return resumes
    
    def _load_settings(self) -> dict:
        """Load application settings"""
        return {
            "min_match_score": 70,
            "max_per_session": 10,
            "delay_min_seconds": 1,
            "delay_max_seconds": 3,
            "chrome_headless": False,
            "chrome_timeout": 30,
            "verify_submission_wait": 2
        }
    
    def get_resume_path(self, language: str = "en") -> Optional[str]:
        """Get resume path for specific language"""
        config = self.load_config()
        return config["resume_paths"].get(language)
    
    def get_profile(self) -> dict:
        """Get application profile"""
        config = self.load_config()
        return config["application_profile"]


def load_config() -> dict:
    """Load configuration (public API)"""
    loader = ConfigLoader()
    return loader.load_config()


def get_resume_path(language: str = "en") -> Optional[str]:
    """Get resume path (public API)"""
    loader = ConfigLoader()
    return loader.get_resume_path(language)
```

**What this does:**
- ✅ Loads resume from `~/Dropbox/CV/resume_en.pdf` and `resume_de.pdf`
- ✅ Loads profile from `config.yaml`
- ✅ Returns complete config dict
- ✅ Handles missing files gracefully

---

## PART 3: APPLY AGENT (Updated Version)

### File: `apply_agent.py` (Simplified)

```python
"""
apply_agent.py - Job application automation using Claude in Chrome.

Simplified version that:
- Gets resume from Dropbox CV folder via config_loader
- Uses Claude vision for smart form analysis
- Fills forms intelligently
- Works with Playwright + Claude (hybrid approach)
"""

import asyncio
import base64
import json
import logging
import os
import re
from pathlib import Path
from datetime import datetime
from typing import Optional

import anthropic
from config_loader import load_config, get_resume_path

log = logging.getLogger(__name__)


class ChromeJobApplier:
    """Apply to jobs using Claude in Chrome (hybrid: Playwright for action, Claude for thinking)."""
    
    def __init__(self):
        self.api_key = os.getenv("ANTHROPIC_API_KEY")
        self.client = anthropic.Anthropic(api_key=self.api_key)
        self.model = "claude-opus-4-6"
        self.config = load_config()
        self.profile = self.config["application_profile"]
        self.resume_en = get_resume_path("en")
        self.resume_de = get_resume_path("de")
    
    async def apply_to_job(
        self,
        job_url: str,
        job_title: str,
        company_name: str,
        job_description: str = ""
    ) -> dict:
        """
        Apply to a single job.
        
        Returns:
            {
                "success": bool,
                "apply_type": "Easy Apply" | "External" | "Manual Required",
                "note": str,
                "timestamp": str (ISO)
            }
        """
        
        log.info(f"Applying: {job_title} @ {company_name}")
        
        try:
            # Step 1: Navigate and screenshot
            screenshot = await self._navigate_and_screenshot(job_url)
            if not screenshot:
                return self._result(False, "Manual Required", "Could not load page")
            
            # Step 2: Detect apply button
            button_info = await self._detect_apply_button(screenshot)
            if button_info["type"] == "not_found":
                return self._result(False, "Manual Required", "No apply button found")
            
            # Step 3: Click apply button
            if not await self._click_button(button_info):
                return self._result(False, "Manual Required", "Could not click button")
            
            # Step 4: Fill form
            await asyncio.sleep(2)
            form_screenshot = await self._get_screenshot()
            
            fill_result = await self._fill_form(
                form_screenshot,
                button_info["type"],
                job_description
            )
            
            if not fill_result.get("ready_to_submit"):
                return self._result(False, "Manual Required", fill_result.get("note", "Form fill failed"))
            
            # Step 5: Submit
            if not await self._submit():
                return self._result(False, "Manual Required", "Could not submit form")
            
            # Step 6: Verify
            await asyncio.sleep(2)
            verified = await self._verify_submission()
            
            if verified:
                apply_type = "Easy Apply" if button_info["type"] == "easy_apply" else "External"
                return self._result(True, apply_type, "Successfully submitted")
            else:
                return self._result(False, "Manual Required", "Submission not verified")
        
        except Exception as e:
            log.error(f"Error: {e}")
            return self._result(False, "Manual Required", str(e))
    
    async def _navigate_and_screenshot(self, url: str) -> Optional[bytes]:
        """Navigate to URL and take screenshot (using Playwright)"""
        # TODO: Implement using Playwright
        # This should:
        # 1. Launch browser
        # 2. Navigate to url
        # 3. Wait for page load
        # 4. Take screenshot
        # 5. Return PNG bytes
        pass
    
    async def _get_screenshot(self) -> Optional[bytes]:
        """Get current page screenshot"""
        # TODO: Implement using Playwright
        pass
    
    async def _detect_apply_button(self, screenshot: bytes) -> dict:
        """Use Claude vision to detect apply button type"""
        screenshot_b64 = base64.b64encode(screenshot).decode()
        
        response = self.client.messages.create(
            model=self.model,
            max_tokens=300,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": screenshot_b64
                        }
                    },
                    {
                        "type": "text",
                        "text": """Find the job application button on this LinkedIn page.

Return JSON:
{
    "type": "easy_apply" or "external" or "not_found",
    "location": {"x": int, "y": int} or null,
    "description": "what you found"
}

Easy Apply = blue "Easy Apply" button from LinkedIn
External = "Apply" button that goes to company website
"""
                    }
                ]
            }]
        )
        
        try:
            text = response.content[0].text
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                return json.loads(match.group())
        except:
            pass
        
        return {"type": "not_found"}
    
    async def _click_button(self, button_info: dict) -> bool:
        """Click the apply button"""
        # TODO: Implement using Playwright
        # Click at button_info["location"]
        pass
    
    async def _fill_form(self, screenshot: bytes, form_type: str, job_desc: str) -> dict:
        """Use Claude to analyze and plan form filling"""
        screenshot_b64 = base64.b64encode(screenshot).decode()
        
        prompt = f"""Analyze this job application form and plan how to fill it.

Form Type: {form_type}

CANDIDATE:
- Name: {self.profile.get('first_name')} {self.profile.get('last_name')}
- Email: {self.profile.get('email')}
- Phone: {self.profile.get('phone')}
- Location: {self.profile.get('current_location')}
- Work Permit: {self.profile.get('work_permit')}

JOB: {job_desc[:300]}

Return JSON:
{{
    "form_analysis": "what fields you see",
    "fields": [
        {{"name": "field_name", "value": "value_to_fill", "type": "text|select|checkbox|file"}},
    ],
    "needs_resume": true|false,
    "ready_to_submit": true|false,
    "note": "any issues"
}}
"""
        
        response = self.client.messages.create(
            model=self.model,
            max_tokens=500,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": screenshot_b64
                        }
                    },
                    {"type": "text", "text": prompt}
                ]
            }]
        )
        
        try:
            text = response.content[0].text
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                plan = json.loads(match.group())
                
                # TODO: Execute plan using Playwright
                # For each field in plan["fields"]:
                #   1. Find field
                #   2. Click it
                #   3. Type value or select option
                
                return {
                    "fields_filled": len(plan.get("fields", [])),
                    "ready_to_submit": plan.get("ready_to_submit", False),
                    "note": plan.get("note", "")
                }
        except:
            pass
        
        return {"fields_filled": 0, "ready_to_submit": False, "note": "Could not parse form"}
    
    async def _submit(self) -> bool:
        """Click submit button"""
        # TODO: Implement using Playwright
        # Find and click submit button
        pass
    
    async def _verify_submission(self) -> bool:
        """Check if application was submitted"""
        screenshot = await self._get_screenshot()
        if not screenshot:
            return False
        
        screenshot_b64 = base64.b64encode(screenshot).decode()
        
        response = self.client.messages.create(
            model=self.model,
            max_tokens=200,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": screenshot_b64
                        }
                    },
                    {
                        "type": "text",
                        "text": """Is this a success/confirmation page for a job application?

Look for: "Thank you", "Application received", "Successfully submitted", "Confirmation"

Return JSON: {"submitted": true|false}
"""
                    }
                ]
            }]
        )
        
        try:
            text = response.content[0].text
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                return json.loads(match.group()).get("submitted", False)
        except:
            pass
        
        return False
    
    def _result(self, success: bool, apply_type: str, note: str) -> dict:
        """Create result dict"""
        return {
            "success": success,
            "apply_type": apply_type,
            "note": note,
            "timestamp": datetime.utcnow().isoformat()
        }


async def apply_to_job(job_url: str, job_title: str, company: str, desc: str = "") -> dict:
    """Public API"""
    applier = ChromeJobApplier()
    return await applier.apply_to_job(job_url, job_title, company, desc)
```

---

## PART 4: APPLY INTEGRATION

### File: `apply_integration.py`

```python
"""
apply_integration.py - Integrate apply_agent with job matcher.
"""

import logging
from apply_agent import apply_to_job
from apply_logger import log_application_result
from config_loader import load_config

log = logging.getLogger(__name__)


async def apply_to_matched_jobs(
    jobs: list[dict],
    max_applications: int = 10
) -> list[dict]:
    """Apply to matched jobs."""
    
    config = load_config()
    results = []
    applied_count = 0
    
    for job in jobs:
        if applied_count >= max_applications:
            log.info(f"Reached max applications ({max_applications})")
            break
        
        try:
            log.info(f"Applying: {job.get('title')} @ {job.get('company')}")
            
            result = await apply_to_job(
                job_url=job.get("url", ""),
                job_title=job.get("title", ""),
                company=job.get("company", ""),
                desc=job.get("description", "")
            )
            
            log_application_result(job, result, config)
            results.append(result)
            
            if result["success"]:
                applied_count += 1
                log.info(f"✅ Applied successfully")
            else:
                log.warning(f"⚠️ Manual review needed: {result['note']}")
        
        except Exception as e:
            log.error(f"Error: {e}")
            results.append({
                "success": False,
                "apply_type": "Manual Required",
                "note": str(e),
                "timestamp": ""
            })
    
    return results
```

---

## PART 5: APPLY LOGGER

### File: `apply_logger.py`

```python
"""
apply_logger.py - Log application results.
"""

import json
import logging
from pathlib import Path
from config_loader import load_config

log = logging.getLogger(__name__)


def log_application_result(job: dict, result: dict, config: dict) -> None:
    """Log result to JSON and update tracker"""
    
    output_dir = config["paths"]["output"]
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Log to JSON
    log_file = output_dir / "applied_jobs_log.json"
    
    data = {"applied_jobs": []}
    if log_file.exists():
        try:
            data = json.loads(log_file.read_text(encoding="utf-8"))
        except:
            pass
    
    data.setdefault("applied_jobs", []).append({
        "company": job.get("company", ""),
        "title": job.get("title", ""),
        "url": job.get("url", ""),
        "success": result.get("success", False),
        "apply_type": result.get("apply_type", ""),
        "timestamp": result.get("timestamp", ""),
        "note": result.get("note", "")
    })
    
    log_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info(f"Logged to {log_file}")
    
    # TODO: Update Excel tracker if needed
```

---

## PART 6: APPLY DEBUGGER (NEW)

### File: `apply_debugger.py`

**Purpose:** Debug tool for testing individual applications

```python
"""
apply_debugger.py - Debug and test individual job applications.

Usage:
    python apply_debugger.py --url "https://linkedin.com/jobs/view/123456"
"""

import asyncio
import logging
import argparse
from datetime import datetime
from pathlib import Path

from apply_agent import apply_to_job
from apply_logger import log_application_result
from config_loader import load_config

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
log = logging.getLogger(__name__)


async def debug_apply(url: str, title: str = "Test Job", company: str = "Test Corp"):
    """
    Debug application process.
    
    Args:
        url: LinkedIn job URL
        title: Job title (optional)
        company: Company name (optional)
    """
    
    log.info("=" * 80)
    log.info("APPLY DEBUGGER")
    log.info("=" * 80)
    log.info(f"URL: {url}")
    log.info(f"Job: {title} @ {company}")
    log.info("=" * 80)
    
    config = load_config()
    
    log.info("Configuration loaded:")
    log.info(f"  Profile: {config['application_profile']['first_name']} {config['application_profile']['last_name']}")
    log.info(f"  Resume EN: {config['resume_paths'].get('en', 'Not found')}")
    log.info(f"  Resume DE: {config['resume_paths'].get('de', 'Not found')}")
    log.info("")
    
    # Run application
    log.info("Starting application process...")
    result = await apply_to_job(url, title, company)
    
    # Log result
    log.info("\n" + "=" * 80)
    log.info("RESULT")
    log.info("=" * 80)
    log.info(f"Success: {result['success']}")
    log.info(f"Apply Type: {result['apply_type']}")
    log.info(f"Note: {result['note']}")
    log.info(f"Timestamp: {result['timestamp']}")
    log.info("=" * 80)
    
    # Also log to file
    job = {"url": url, "title": title, "company": company, "description": ""}
    log_application_result(job, result, config)
    
    return result


async def main():
    parser = argparse.ArgumentParser(description="Debug job application")
    parser.add_argument("--url", required=True, help="LinkedIn job URL")
    parser.add_argument("--title", default="Test Job", help="Job title")
    parser.add_argument("--company", default="Test Corp", help="Company name")
    
    args = parser.parse_args()
    
    await debug_apply(args.url, args.title, args.company)


if __name__ == "__main__":
    asyncio.run(main())
```

**Usage:**
```bash
python apply_debugger.py --url "https://www.linkedin.com/jobs/view/4324889941/"
```

---

## PART 7: MAIN.PY INTEGRATION

### Modify: `main.py`

Replace the old apply section:

```python
# OLD:
from applier.applier import run
results = await _run_apply(jobs, cfg)

# NEW:
from apply_integration import apply_to_matched_jobs
results = await apply_to_matched_jobs(matched_jobs, max_applications=10)
```

---

## PART 8: CLEANUP INSTRUCTIONS

### Step 1: Delete Unnecessary Files

```bash
cd ~/Projects/claude/job-hunt-agent

# Delete old docs
rm EXTERNAL_APPLY_WORKFLOW_SUMMARY.md
rm SYSTEM_STATUS_REPORT.md
rm QUICK_START_GUIDE.md
rm CLAUDE_CODE_MIGRATION_PROMPT.md
rm PROMPT_FOR_CLAUDE_CODE.md

# Delete old Playwright code
rm -rf applier/

# Clean up
rm -rf uploads/
rm -rf dedup/ (will be recreated if needed)
```

### Step 2: Move Config to Root

```bash
# Move config to root
mv core/config.yaml config.yaml

# Clean up core if empty
rm -rf core/
```

### Step 3: Create outputs directory

```bash
mkdir -p outputs
```

---

## PART 9: FINAL FILE STRUCTURE

After cleanup:

```
~/Projects/claude/job-hunt-agent/
├── main.py                    (orchestration)
├── config.yaml                (user config - reads from config.yaml)
├── .env                        (API keys)
├── .gitignore
│
├── apply_agent.py             (apply automation - NEW)
├── apply_integration.py        (integration - NEW)
├── apply_logger.py            (logging - NEW)
├── config_loader.py           (load CV from Dropbox - NEW)
├── apply_debugger.py          (debug tool - NEW)
├── test_apply.py              (tests - optional)
│
├── matcher/                   (job matching code)
├── outputs/                   (auto-created results)
│
└── README.md                  (ONE comprehensive doc)
```

---

## PART 10: README.MD (Master Documentation)

Create single `README.md` with everything:

```markdown
# Job Hunt Automation System

## Quick Start

### 1. Setup
```bash
pip install -r requirements.txt
cp config.example.yaml config.yaml
# Edit config.yaml with your info
```

### 2. Resume Files
Place in `~/Dropbox/CV/`:
- `resume_en.pdf` - English resume
- `resume_de.pdf` - German resume

### 3. Run
```bash
python main.py  # Full automation
python apply_debugger.py --url "https://..."  # Test single job
```

## Architecture

- **main.py** - Orchestration (scraping → matching → applying)
- **apply_agent.py** - Apply automation (Claude in Chrome + Playwright)
- **apply_integration.py** - Connect matcher to applier
- **apply_logger.py** - Log results to JSON
- **config_loader.py** - Load resume from Dropbox CV
- **apply_debugger.py** - Debug tool

## Features

✅ Scrape jobs from LinkedIn/Indeed/Glassdoor
✅ AI job matching (70+ score)
✅ Easy Apply automation
✅ External ATS automation
✅ Resume upload
✅ Form filling
✅ Result logging

## Configuration

**config.yaml:**
```yaml
application_profile:
  first_name: "Felix"
  last_name: "Behboudi"
  email: "your@email.com"
  ...
```

**Resume location:**
- `~/Dropbox/CV/resume_en.pdf`
- `~/Dropbox/CV/resume_de.pdf`

## Troubleshooting

See apply_debugger.py for testing individual jobs.
Check `outputs/applied_jobs_log.json` for results.
```

---

## IMPLEMENTATION CHECKLIST

### Phase 1: Cleanup (1 hour)
- [ ] Delete unnecessary files and folders
- [ ] Move config.yaml to root
- [ ] Create clean directory structure
- [ ] Update .gitignore

### Phase 2: Create New Files (3 hours)
- [ ] Create config_loader.py (read resume from Dropbox)
- [ ] Create apply_agent.py (with TODO sections)
- [ ] Create apply_integration.py
- [ ] Create apply_logger.py
- [ ] Create apply_debugger.py

### Phase 3: Implementation (4 hours)
- [ ] Implement Playwright integration in apply_agent.py
  - [ ] _navigate_and_screenshot()
  - [ ] _get_screenshot()
  - [ ] _click_button()
  - [ ] _fill_form() (field interaction)
  - [ ] _submit()
- [ ] Implement form filling execution
- [ ] Implement error handling

### Phase 4: Integration (2 hours)
- [ ] Modify main.py to use new apply system
- [ ] Update imports
- [ ] Test integration with job matcher

### Phase 5: Testing (2 hours)
- [ ] Test with Easy Apply job
- [ ] Test with External Apply job
- [ ] Test apply_debugger.py
- [ ] Verify output files created

### Phase 6: Documentation (1 hour)
- [ ] Write comprehensive README.md
- [ ] Delete all other MD files
- [ ] Update code comments

---

## SUCCESS CRITERIA

✅ **All of these must be true:**

1. Codebase cleaned (no Playwright code, no unused MD files)
2. All files in project root (no applier/ folder)
3. Resume loaded from `~/Dropbox/CV/resume_*.pdf`
4. Config loaded from project root `config.yaml`
5. Easy Apply test job applies successfully
6. External Apply test job applies successfully
7. Results logged to `outputs/applied_jobs_log.json`
8. apply_debugger.py works for testing
9. Single `README.md` with all documentation
10. No `applier/`, `uploads/`, `dedup/` folders

---

## KEY IMPLEMENTATION DETAILS

### Resume Path Handling

```python
# In config_loader.py
dropbox_cv = Path.home() / "Dropbox" / "CV"
resume_en = dropbox_cv / "resume_en.pdf"
resume_de = dropbox_cv / "resume_de.pdf"
```

### Config Loading

```python
# In apply_agent.py
from config_loader import load_config, get_resume_path

config = load_config()
profile = config["application_profile"]
resume = get_resume_path("en")
```

### Using Claude Vision

```python
# Analyze screenshot with Claude
response = client.messages.create(
    model="claude-opus-4-6",
    messages=[{
        "role": "user",
        "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": screenshot_b64}},
            {"type": "text", "text": "Analyze this form..."}
        ]
    }]
)
```

### Playwright for Interaction

```python
# Use Playwright for clicks/typing
from playwright.async_api import async_playwright

async with async_playwright() as pw:
    browser = await pw.chromium.launch()
    page = await browser.new_page()
    await page.goto(url)
    await page.click("button")  # Click button
    await page.fill("input", "value")  # Type value
    screenshot = await page.screenshot()  # Get screenshot
```

---

## TIMELINE

- **Hours 1-2:** Cleanup + config_loader.py
- **Hours 3-5:** Core apply_agent.py
- **Hours 6-8:** Playwright integration + form filling
- **Hours 9-10:** Integration + testing
- **Hours 11-12:** Documentation

**Total: ~12 hours of focused development**

---

## DEPLOYMENT CHECKLIST

Before running on real jobs:

- [ ] Test with 1 Easy Apply job
- [ ] Test with 1 External Apply job
- [ ] Verify resume loads from Dropbox
- [ ] Verify config loads correctly
- [ ] Check outputs created correctly
- [ ] Review apply_debugger.py logs
- [ ] Verify no Playwright code in apply phase
- [ ] Clean git (no old files)

---

## FINAL NOTES

- **This is a COMPLETE rewrite** - includes cleanup
- **No Playwright code** - only Claude + Playwright hybrid
- **Resume from Dropbox** - config_loader handles it
- **Single README** - all documentation in one place
- **Clean structure** - only essential files in root
- **Debugger included** - test individual jobs easily

**Claude Code will implement everything. Just feed it this prompt.**

---

## END OF PROMPT

Feed this entire document to Claude Code and it will:
1. ✅ Clean up the codebase
2. ✅ Create all new files
3. ✅ Load resume from Dropbox
4. ✅ Set up complete apply system
5. ✅ Create debugger
6. ✅ Write comprehensive README
7. ✅ Remove all unnecessary files
