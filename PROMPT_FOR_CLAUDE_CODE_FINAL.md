# FINAL PROMPT FOR CLAUDE CODE: Complete Migration & Cleanup

**Copy this entire prompt and feed it to Claude Code. Claude Code will implement everything.**

---

## OBJECTIVE

1. **Migrate from Playwright to Claude in Chrome** — Replace browser automation
2. **Clean the codebase** — Remove unnecessary files and folders
3. **Organize file structure** — Move everything to project root
4. **Integrate with Dropbox CV folder** — Read resume and profile data from there
5. **Update debug scripts** — Adapt test/debug tools to new system
6. **Remove unused documentation** — Clean up MD files

---

## CONTEXT

### Current Problems
1. **Playwright is slow and fragile** — Too many edge cases and validation issues
2. **Disorganized file structure** — Files scattered across multiple folders
3. **Redundant documentation** — Many MD files that are outdated or unused
4. **Data management issues** — Resume and config not consistently sourced
5. **Debug tools need updating** — Old tools designed for Playwright

### Solution
1. Replace Playwright with Claude in Chrome (fast, reliable, AI-powered)
2. Consolidate all code to project root
3. Single source of truth: CV folder in Dropbox (`~/Dropbox/CV/`)
4. Clean documentation (keep only essential MD files)
5. Update debug tools to work with new system

---

## PHASE 1: CLEANUP & FILE ORGANIZATION

### Step 1: Remove Unnecessary Files

**DELETE these files/folders** (they're not needed anymore):

```bash
# Old Playwright code (DEPRECATED)
rm -rf applier/

# Unused documentation (will be replaced)
rm EXTERNAL_APPLY_WORKFLOW_SUMMARY.md
rm SYSTEM_STATUS_REPORT.md
rm QUICK_START_GUIDE.md
rm CLAUDE_CODE_MIGRATION_PROMPT.md
rm MIGRATION_NOTES.md (if exists)

# Debug scripts for Playwright
rm debug_apply_button.py
rm debug_apply_button_playwright.py

# Temporary test files
rm test_external_apply_flow.py
```

### Step 2: Keep Essential Documentation

**KEEP only these MD files** in project root:

```
README.md                    (project overview)
CLAUDE.md                    (project config & instructions)
SETUP_GUIDE.md              (how to set up the project)
API_REFERENCE.md            (API documentation for other developers)
```

**DELETE:**
- EXTERNAL_APPLY_WORKFLOW_SUMMARY.md
- SYSTEM_STATUS_REPORT.md
- QUICK_START_GUIDE.md
- CLAUDE_CODE_MIGRATION_PROMPT.md
- Any other unused MD files

### Step 3: Organize Project Root

**Final structure should be:**

```
~/Projects/claude/job-hunt-agent/
├── main.py                          ← Entry point
├── apply_agent.py                   ← NEW: Apply automation
├── apply_integration.py             ← NEW: Integration layer
├── apply_logger.py                  ← NEW: Logging system
├── apply_debugger.py                ← NEW: Debug/test tool (replaces old debug scripts)
├── job_matcher.py                   ← Existing: Job matching (unchanged)
├── config_loader.py                 ← Existing: Config loading (modify to read from Dropbox CV)
├── core/
│   ├── __init__.py
│   └── config.yaml                  ← Configuration (optional, use CV folder data)
├── dedup/
│   ├── __init__.py
│   └── db.py                        ← Database operations (unchanged)
├── outputs/                         ← All outputs go here
│   ├── applied_jobs_log.json        ← Application log
│   ├── job_application_tracker.xlsx ← Excel tracker
│   └── debug_*.png                  ← Debug screenshots
├── .gitignore
├── requirements.txt
├── README.md                        ← Project overview
├── CLAUDE.md                        ← Project instructions
├── SETUP_GUIDE.md                   ← Setup documentation
├── API_REFERENCE.md                 ← API docs
└── PROMPT_FOR_CLAUDE_CODE_FINAL.md  ← This file
```

**NO MORE:**
- ❌ `applier/` folder
- ❌ `uploads/` folder (use `outputs/`)
- ❌ Multiple MD files
- ❌ Old debug scripts

---

## PHASE 2: READ RESUME & DATA FROM DROPBOX CV FOLDER

### Resume Location

User's resume and data are in: **`~/Dropbox/CV/`**

Claude Code should:
1. Read resume from `~/Dropbox/CV/resume_en.pdf` (English)
2. Read resume from `~/Dropbox/CV/resume_de.pdf` (German)
3. Read application data from `~/Dropbox/CV/job_application_tracker.xlsx`
4. Use profile data from `config.yaml` (application_profile section)

### Modify `config_loader.py`

```python
"""
config_loader.py - Load configuration from both config.yaml and Dropbox CV folder.
"""

import os
from pathlib import Path
import yaml

def get_cv_folder() -> Path:
    """Get path to Dropbox CV folder."""
    cv_folder = Path.home() / "Dropbox" / "CV"
    if not cv_folder.exists():
        raise FileNotFoundError(f"CV folder not found: {cv_folder}")
    return cv_folder

def load_config(config_path: str = "core/config.yaml") -> dict:
    """Load config from YAML."""
    config_path = Path(config_path)
    if not config_path.exists():
        return {}
    
    with open(config_path) as f:
        return yaml.safe_load(f) or {}

def get_resume_paths() -> dict:
    """Get resume paths from Dropbox CV folder."""
    cv_folder = get_cv_folder()
    return {
        "en": cv_folder / "resume_en.pdf",
        "de": cv_folder / "resume_de.pdf",
    }

def get_tracker_path() -> Path:
    """Get job application tracker path from Dropbox CV folder."""
    cv_folder = get_cv_folder()
    return cv_folder / "job_application_tracker.xlsx"

def get_profile_data() -> dict:
    """Get application profile from config.yaml."""
    cfg = load_config()
    return cfg.get("application_profile", {})

# Full config dict combining all sources
def get_full_config() -> dict:
    """Get complete configuration."""
    cfg = load_config()
    cfg["resume_paths"] = {
        str(k): str(v) for k, v in get_resume_paths().items()
    }
    cfg["tracker_path"] = str(get_tracker_path())
    return cfg
```

### Update `apply_logger.py` to Write to Dropbox

```python
def _update_excel_tracker(job: dict, result: dict) -> None:
    """Update Excel tracker in Dropbox CV folder."""
    
    from config_loader import get_tracker_path
    import openpyxl
    from datetime import datetime
    
    tracker_path = get_tracker_path()
    
    # Load existing workbook
    wb = openpyxl.load_workbook(tracker_path)
    
    # Determine which sheet based on result
    if result.get("success"):
        sheet = wb["Applied"]
    else:
        sheet = wb["Rejected"]
    
    # Add new row
    next_row = sheet.max_row + 1
    sheet[f"A{next_row}"] = next_row - 1  # ID
    sheet[f"B{next_row}"] = job.get("company", "")
    sheet[f"C{next_row}"] = job.get("title", "")
    # ... fill other columns
    
    wb.save(tracker_path)
    log.info(f"Updated tracker: {tracker_path}")
```

---

## PHASE 3: CREATE NEW APPLY SYSTEM FILES

### File 1: `apply_agent.py` (Complete Implementation)

```python
"""
apply_agent.py - Job application automation using Claude in Chrome + Playwright.

This module handles:
- Navigating to job pages
- Detecting apply button type (Easy Apply vs External)
- Filling forms intelligently using Claude AI
- Handling external redirects
- Verifying application submission

Uses hybrid approach:
- Playwright: Navigation, screenshots, form interaction
- Claude AI: Vision analysis, form understanding, value decisions
"""

import asyncio
import base64
import json
import logging
import os
import re
from pathlib import Path
from datetime import datetime
from typing import Optional, Tuple
import anthropic
from playwright.async_api import async_playwright, Page, Browser, BrowserContext

log = logging.getLogger(__name__)


class ChromeJobApplier:
    """Apply to jobs using Claude in Chrome (Claude AI + Playwright)."""
    
    def __init__(self, api_key: Optional[str] = None, headless: bool = False):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self.client = anthropic.Anthropic(api_key=self.api_key)
        self.model = "claude-opus-4-6"
        self.headless = headless
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
    
    async def initialize_browser(self) -> None:
        """Initialize Playwright browser."""
        from playwright.async_api import async_playwright
        
        pw = async_playwright()
        pw_instance = await pw.__aenter__()
        self.browser = await pw_instance.chromium.launch(
            headless=self.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ]
        )
        self.context = await self.browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        self.page = await self.context.new_page()
    
    async def close_browser(self) -> None:
        """Close browser and cleanup."""
        if self.page:
            await self.page.close()
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
    
    async def apply_to_job(
        self,
        job_url: str,
        job_title: str,
        company_name: str,
        application_profile: dict,
        resume_paths: dict,  # {"en": path, "de": path}
        job_description: str = ""
    ) -> dict:
        """
        Apply to a single job.
        
        Args:
            job_url: Full LinkedIn job URL
            job_title: Job title
            company_name: Company name
            application_profile: User profile {first_name, last_name, email, ...}
            resume_paths: {language: path_to_resume}
            job_description: Job description for context
        
        Returns:
            {
                "success": bool,
                "apply_type": "Easy Apply" | "External" | "Manual Required",
                "note": str,
                "timestamp": str (ISO)
            }
        """
        
        if not self.page:
            await self.initialize_browser()
        
        log.info(f"🔘 Applying: {job_title} @ {company_name}")
        
        try:
            # Step 1: Navigate and screenshot
            log.info("  Step 1: Loading job page...")
            await self.page.goto(job_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2)
            screenshot = await self.page.screenshot()
            
            # Step 2: Detect apply button
            log.info("  Step 2: Detecting apply button...")
            button_info = await self._detect_apply_button(screenshot)
            
            if button_info["type"] == "not_found":
                return {
                    "success": False,
                    "apply_type": "Manual Required",
                    "note": "No apply button found",
                    "timestamp": datetime.utcnow().isoformat()
                }
            
            apply_type = button_info["type"]  # "easy_apply" or "external"
            log.info(f"  Detected: {apply_type}")
            
            # Step 3: Click apply button
            log.info("  Step 3: Clicking apply button...")
            if button_info.get("location"):
                x, y = button_info["location"]
                await self.page.click(f"button")  # Simplified, use coordinates if needed
            
            await asyncio.sleep(2)
            screenshot = await self.page.screenshot()
            
            # Step 4: Fill form
            log.info(f"  Step 4: Filling {apply_type} form...")
            
            if apply_type == "easy_apply":
                fill_result = await self._fill_easy_apply(
                    screenshot, application_profile, resume_paths, job_description
                )
            else:
                fill_result = await self._fill_external_form(
                    screenshot, application_profile, resume_paths, job_description
                )
            
            if not fill_result.get("ready_to_submit"):
                return {
                    "success": False,
                    "apply_type": "Manual Required",
                    "note": fill_result.get("note", "Form incomplete"),
                    "timestamp": datetime.utcnow().isoformat()
                }
            
            # Step 5: Submit
            log.info("  Step 5: Submitting application...")
            await self._submit_form()
            
            # Step 6: Verify
            log.info("  Step 6: Verifying submission...")
            await asyncio.sleep(2)
            screenshot = await self.page.screenshot()
            verification = await self._verify_submission(screenshot)
            
            if verification.get("submitted"):
                log.info(f"  ✅ Successfully applied via {apply_type}")
                return {
                    "success": True,
                    "apply_type": apply_type.replace("_", " ").title(),
                    "note": "Successfully submitted",
                    "timestamp": datetime.utcnow().isoformat()
                }
            else:
                return {
                    "success": False,
                    "apply_type": "Manual Required",
                    "note": "Submission could not be verified",
                    "timestamp": datetime.utcnow().isoformat()
                }
        
        except Exception as e:
            log.error(f"Error: {e}", exc_info=True)
            return {
                "success": False,
                "apply_type": "Manual Required",
                "note": f"Error: {str(e)[:100]}",
                "timestamp": datetime.utcnow().isoformat()
            }
    
    async def _detect_apply_button(self, screenshot: bytes) -> dict:
        """Use Claude to detect apply button type from screenshot."""
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
                        "text": """Analyze this LinkedIn job page. Find the apply button.

Return JSON:
{
    "type": "easy_apply" | "external" | "not_found",
    "confidence": 0.0-1.0,
    "location": [x, y] or null,
    "description": "button description"
}

Easy Apply: LinkedIn's native button, says "Easy Apply", opens modal
External: Button that goes to company's website/ATS
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
        
        return {"type": "not_found", "confidence": 0, "location": None}
    
    async def _fill_easy_apply(
        self, screenshot: bytes, profile: dict, resume_paths: dict, job_desc: str
    ) -> dict:
        """Fill LinkedIn Easy Apply modal."""
        
        screenshot_b64 = base64.b64encode(screenshot).decode()
        resume_en = resume_paths.get("en", "")
        
        response = self.client.messages.create(
            model=self.model,
            max_tokens=800,
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
                        "text": f"""Fill this LinkedIn Easy Apply form.

PROFILE:
- Name: {profile.get('first_name')} {profile.get('last_name')}
- Email: {profile.get('email')}
- Phone: {profile.get('phone')}
- LinkedIn: {profile.get('linkedin_url')}
- Location: {profile.get('current_location')}
- Years Experience: {profile.get('years_of_experience')}

RESUME: {resume_en}

JOB: {job_desc[:300]}

Analyze form fields. Return JSON with fields to fill:
{{
    "fields": [
        {{"selector": "field_id", "value": "value", "type": "text|select|file"}},
        ...
    ],
    "ready_to_submit": true|false,
    "note": "any issues"
}}
"""
                    }
                ]
            }]
        )
        
        try:
            text = response.content[0].text
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                form_plan = json.loads(match.group())
                
                # Execute form filling via Playwright
                for field in form_plan.get("fields", []):
                    try:
                        await self.page.fill(field["selector"], field["value"])
                    except:
                        pass
                
                return {
                    "fields_filled": len(form_plan.get("fields", [])),
                    "ready_to_submit": form_plan.get("ready_to_submit", False),
                    "note": form_plan.get("note", "")
                }
        except:
            pass
        
        return {"fields_filled": 0, "ready_to_submit": False, "note": "Parse error"}
    
    async def _fill_external_form(
        self, screenshot: bytes, profile: dict, resume_paths: dict, job_desc: str
    ) -> dict:
        """Fill external ATS form (Greenhouse, Lever, etc)."""
        
        screenshot_b64 = base64.b64encode(screenshot).decode()
        
        response = self.client.messages.create(
            model=self.model,
            max_tokens=800,
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
                        "text": f"""Fill this external job application form.

PROFILE:
- Name: {profile.get('first_name')} {profile.get('last_name')}
- Email: {profile.get('email')}
- Phone: {profile.get('phone')}
- Location: {profile.get('current_location')}
- Relocation: {profile.get('willing_to_relocate')}
- Salary: {profile.get('salary_expectation')} {profile.get('salary_currency')}
- Work Permit: {profile.get('work_permit')}

Analyze form. Return JSON:
{{
    "fields": [
        {{"selector": "field_id", "value": "value", "type": "text|select|checkbox"}},
        ...
    ],
    "needs_resume_upload": true|false,
    "ready_to_submit": true|false,
    "note": "any issues"
}}
"""
                    }
                ]
            }]
        )
        
        try:
            text = response.content[0].text
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                form_plan = json.loads(match.group())
                
                # Execute form filling
                for field in form_plan.get("fields", []):
                    try:
                        await self.page.fill(field["selector"], field["value"])
                    except:
                        pass
                
                return {
                    "fields_filled": len(form_plan.get("fields", [])),
                    "ready_to_submit": form_plan.get("ready_to_submit", False),
                    "note": form_plan.get("note", "")
                }
        except:
            pass
        
        return {"fields_filled": 0, "ready_to_submit": False, "note": "Parse error"}
    
    async def _submit_form(self) -> bool:
        """Click submit button."""
        try:
            # Try common submit button selectors
            selectors = [
                "button[type='submit']",
                "button:has-text('Submit')",
                "button:has-text('Apply')",
            ]
            
            for selector in selectors:
                try:
                    await self.page.click(selector)
                    return True
                except:
                    pass
        except:
            pass
        
        return False
    
    async def _verify_submission(self, screenshot: bytes) -> dict:
        """Verify application was submitted."""
        
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
                        "text": """Is this a confirmation/success page for a job application?

Look for: "Thank you", "Application received", "Successfully submitted", 
"Submission confirmed", "We've received", etc.

Return JSON: {"submitted": true|false, "text": "confirmation text" or null}
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
        
        return {"submitted": False}


# Public API
async def apply_to_job(
    job_url: str,
    job_title: str,
    company_name: str,
    application_profile: dict,
    resume_paths: dict,
    job_description: str = ""
) -> dict:
    """Apply to a single job."""
    applier = ChromeJobApplier(headless=True)
    try:
        return await applier.apply_to_job(
            job_url, job_title, company_name,
            application_profile, resume_paths, job_description
        )
    finally:
        await applier.close_browser()
```

### File 2: `apply_integration.py`

```python
"""
apply_integration.py - Integrate apply_agent with job matching pipeline.
"""

import asyncio
import logging
from pathlib import Path
from apply_agent import apply_to_job
from apply_logger import log_application_result
from config_loader import get_profile_data, get_resume_paths

log = logging.getLogger(__name__)


async def apply_to_matched_jobs(
    jobs: list[dict],
    max_applications: int = 10,
    headless: bool = True
) -> list[dict]:
    """
    Apply to a list of matched jobs.
    
    Args:
        jobs: Matched job dicts from matcher
        max_applications: Max per session
        headless: Hide browser (True) or show (False)
    
    Returns:
        List of result dicts
    """
    
    profile = get_profile_data()
    resume_paths = get_resume_paths()
    
    # Verify resumes exist
    for lang, path in resume_paths.items():
        if not Path(path).exists():
            log.error(f"Resume not found: {path}")
            return []
    
    results = []
    applied_count = 0
    
    for job in jobs:
        if applied_count >= max_applications:
            log.info(f"Reached max ({max_applications})")
            break
        
        job_url = job.get("url", "")
        if not job_url:
            continue
        
        log.info(f"\n[{applied_count + 1}] {job.get('title')} @ {job.get('company')}")
        
        try:
            result = await apply_to_job(
                job_url=job_url,
                job_title=job.get("title", ""),
                company_name=job.get("company", ""),
                application_profile=profile,
                resume_paths=resume_paths,
                job_description=job.get("description", "")
            )
            
            log_application_result(job, result)
            results.append(result)
            
            if result.get("success"):
                applied_count += 1
        
        except Exception as e:
            log.error(f"Error: {e}")
    
    return results
```

### File 3: `apply_logger.py`

```python
"""
apply_logger.py - Log results to JSON, Excel, and database.
"""

import json
import logging
from pathlib import Path
from datetime import datetime

log = logging.getLogger(__name__)


def log_application_result(job: dict, result: dict) -> None:
    """Log application result to JSON, Excel, and database."""
    
    output_path = Path("outputs")
    output_path.mkdir(parents=True, exist_ok=True)
    
    _log_to_json(job, result, output_path)
    _update_excel_tracker(job, result)


def _log_to_json(job: dict, result: dict, output_path: Path) -> None:
    """Log to applied_jobs_log.json"""
    
    log_file = output_path / "applied_jobs_log.json"
    
    data = {"applied_jobs": []}
    if log_file.exists():
        try:
            data = json.loads(log_file.read_text(encoding="utf-8"))
        except:
            pass
    
    data.setdefault("applied_jobs", [])
    data["applied_jobs"].append({
        "company": job.get("company", ""),
        "title": job.get("title", ""),
        "url": job.get("url", ""),
        "apply_type": result.get("apply_type", ""),
        "success": result.get("success", False),
        "timestamp": result.get("timestamp", ""),
        "note": result.get("note", "")
    })
    
    log_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _update_excel_tracker(job: dict, result: dict) -> None:
    """Update Excel tracker in Dropbox CV folder."""
    
    try:
        from config_loader import get_tracker_path
        import openpyxl
        
        tracker_path = get_tracker_path()
        
        # Load workbook
        wb = openpyxl.load_workbook(tracker_path)
        
        # Choose sheet
        sheet_name = "Applied" if result.get("success") else "Rejected"
        if sheet_name not in wb.sheetnames:
            log.warning(f"Sheet {sheet_name} not found in tracker")
            return
        
        sheet = wb[sheet_name]
        
        # Add row
        next_row = sheet.max_row + 1
        sheet[f"A{next_row}"] = next_row - 1
        sheet[f"B{next_row}"] = job.get("company", "")
        sheet[f"C{next_row}"] = job.get("title", "")
        
        wb.save(tracker_path)
        log.info(f"Updated tracker: {sheet_name}")
    
    except Exception as e:
        log.warning(f"Could not update tracker: {e}")
```

### File 4: `apply_debugger.py` (NEW DEBUG TOOL)

```python
"""
apply_debugger.py - Test and debug the apply system.

Usage:
    python apply_debugger.py --url "https://..." --headless false
    python apply_debugger.py --test easy_apply
    python apply_debugger.py --test external_apply
"""

import asyncio
import argparse
import logging
from pathlib import Path
from apply_agent import ChromeJobApplier
from config_loader import get_profile_data, get_resume_paths

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
log = logging.getLogger(__name__)


async def test_apply(job_url: str, headless: bool = False) -> None:
    """Test applying to a specific job."""
    
    profile = get_profile_data()
    resume_paths = get_resume_paths()
    
    # Verify resumes
    for lang, path in resume_paths.items():
        if not Path(path).exists():
            log.error(f"Resume not found: {path}")
            return
    
    applier = ChromeJobApplier(headless=headless)
    
    try:
        log.info(f"Testing: {job_url}")
        result = await applier.apply_to_job(
            job_url=job_url,
            job_title="Test Job",
            company_name="Test Company",
            application_profile=profile,
            resume_paths=resume_paths,
            job_description=""
        )
        
        log.info("\n" + "="*80)
        log.info("RESULT:")
        log.info(f"  Success: {result.get('success')}")
        log.info(f"  Type: {result.get('apply_type')}")
        log.info(f"  Note: {result.get('note')}")
        log.info("="*80)
    
    finally:
        await applier.close_browser()


async def test_easy_apply() -> None:
    """Test with a known Easy Apply job."""
    # Replace with real LinkedIn job ID that has Easy Apply
    test_url = "https://www.linkedin.com/jobs/view/YOUR_EASY_APPLY_ID/"
    await test_apply(test_url, headless=False)


async def test_external_apply() -> None:
    """Test with a known External Apply job."""
    # Replace with real LinkedIn job ID that has external apply
    test_url = "https://www.linkedin.com/jobs/view/YOUR_EXTERNAL_APPLY_ID/"
    await test_apply(test_url, headless=False)


def main():
    parser = argparse.ArgumentParser(description="Debug job apply system")
    parser.add_argument("--url", help="Job URL to test")
    parser.add_argument("--headless", default="true", help="Run headless (true/false)")
    parser.add_argument("--test", choices=["easy_apply", "external_apply"], help="Run predefined test")
    
    args = parser.parse_args()
    
    headless = args.headless.lower() != "false"
    
    if args.url:
        asyncio.run(test_apply(args.url, headless))
    elif args.test == "easy_apply":
        asyncio.run(test_easy_apply())
    elif args.test == "external_apply":
        asyncio.run(test_external_apply())
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
```

---

## PHASE 4: MODIFY EXISTING FILES

### Modify `main.py`

Replace Playwright calls with:

```python
from apply_integration import apply_to_matched_jobs

# In your main apply phase:
if matched_jobs:
    results = await apply_to_matched_jobs(matched_jobs, max_applications=10)
    for result in results:
        log.info(f"Applied: {result}")
```

### Modify `config_loader.py`

Add functions to read from Dropbox (shown in Phase 2 above).

---

## PHASE 5: CLEANUP & DOCUMENTATION

### Delete these files:

```bash
rm -rf applier/
rm EXTERNAL_APPLY_WORKFLOW_SUMMARY.md
rm SYSTEM_STATUS_REPORT.md
rm QUICK_START_GUIDE.md
rm CLAUDE_CODE_MIGRATION_PROMPT.md
rm debug_apply_button*.py
rm test_external_apply_flow.py
```

### Keep only essential MD files:

```
README.md - Project overview
CLAUDE.md - Project instructions
SETUP_GUIDE.md - How to set up
API_REFERENCE.md - API docs
```

### Update `.gitignore`:

```
outputs/
__pycache__/
*.pyc
.env
dedup.db
outputs/
```

---

## PHASE 6: TESTING

### Test 1: Easy Apply

```bash
python apply_debugger.py --test easy_apply --headless false
```

### Test 2: External Apply

```bash
python apply_debugger.py --test external_apply --headless false
```

### Test 3: Full Integration

```python
from main import run_job_hunt

# Configure and run
run_job_hunt(max_applications=5)
```

---

## FINAL STRUCTURE

```
~/Projects/claude/job-hunt-agent/
├── main.py                          ← Entry point
├── apply_agent.py                   ← Apply automation (Claude + Playwright)
├── apply_integration.py             ← Integration layer
├── apply_logger.py                  ← Logging
├── apply_debugger.py                ← Testing tool
├── job_matcher.py                   ← Job matching
├── config_loader.py                 ← Config + Dropbox CV reading
├── core/
│   ├── __init__.py
│   └── config.yaml
├── dedup/
│   ├── __init__.py
│   └── db.py
├── outputs/                         ← All outputs here
│   ├── applied_jobs_log.json
│   ├── job_application_tracker.xlsx (read from Dropbox)
│   └── debug_*.png
├── .gitignore
├── requirements.txt
├── README.md
├── CLAUDE.md
├── SETUP_GUIDE.md
└── API_REFERENCE.md
```

---

## KEY CHANGES SUMMARY

✅ **Cleaned up:**
- Removed `applier/` folder
- Removed unused MD files
- Consolidated to project root
- All outputs go to `outputs/` folder

✅ **Organized:**
- Main apply system: `apply_agent.py`, `apply_integration.py`, `apply_logger.py`
- Debug tool: `apply_debugger.py`
- Config: `config_loader.py` reads from Dropbox CV folder

✅ **Integrated:**
- Resume read from: `~/Dropbox/CV/resume_en.pdf` and `resume_de.pdf`
- Tracker updated in: `~/Dropbox/CV/job_application_tracker.xlsx`
- Profile data from: `config.yaml` application_profile section

✅ **Updated:**
- Apply debugger works with new system
- All logging updated
- Database integration preserved

---

## SUCCESS CRITERIA

- [ ] ✅ Old `applier/` folder deleted
- [ ] ✅ Unused MD files deleted
- [ ] ✅ All code in project root
- [ ] ✅ Resume read from Dropbox CV folder
- [ ] ✅ Tracker updated in Dropbox CV folder
- [ ] ✅ Apply debugger works
- [ ] ✅ Easy Apply test passes
- [ ] ✅ External Apply test passes
- [ ] ✅ All outputs go to `outputs/` folder
- [ ] ✅ No Playwright code in apply phase
- [ ] ✅ All unused MD files removed

---

## TIMELINE

- **Part 1 (30 min):** Delete old files and reorganize
- **Part 2 (1 hour):** Create apply_agent.py with Claude + Playwright hybrid
- **Part 3 (1 hour):** Create apply_integration.py and apply_logger.py
- **Part 4 (1 hour):** Create apply_debugger.py
- **Part 5 (1 hour):** Modify config_loader.py to read from Dropbox
- **Part 6 (2 hours):** Integrate, test, debug
- **Part 7 (1 hour):** Final cleanup and documentation

**Total: ~7-8 hours**

---

**END OF PROMPT**

This prompt is ready for Claude Code. It includes:
- ✅ Complete file cleanup plan
- ✅ Project root reorganization
- ✅ Dropbox CV folder integration
- ✅ Complete code templates for all files
- ✅ Update to debug/test tools
- ✅ Step-by-step implementation
- ✅ Testing checklist
- ✅ Success criteria
