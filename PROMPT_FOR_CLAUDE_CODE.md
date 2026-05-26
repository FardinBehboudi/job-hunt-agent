# PROMPT FOR CLAUDE CODE: Migrate from Playwright to Claude in Chrome

**Copy this entire prompt and feed it to Claude Code. Claude Code will implement everything.**

---

## OBJECTIVE

Completely replace Playwright browser automation with Claude in Chrome for the job application workflow. This is a major refactor that affects only the "apply" phase of the job hunt automation system.

**What stays the same:** Job scraping, job matching, database, configuration structure, logging format.

**What changes:** Browser automation for applying to jobs (Easy Apply modal + external forms).

---

## CONTEXT

### Current Problem (Playwright)
- Too many validation issues with form filling
- Complex error handling for different platforms
- Slow (30-60 seconds per application)
- High maintenance burden (debugging selectors, timeouts, etc.)
- Not cost-effective for the job hunt use case

### Solution (Claude in Chrome)
- Claude AI understands form context naturally
- No complex selectors or JavaScript evaluation
- Faster (15-30 seconds per application)
- More reliable (AI can handle variations)
- Lower maintenance (Claude adapts to changes)

### Trade-off
- API cost: ~$0.05 per application (acceptable)
- Development time: Saved hours of debugging

---

## YOUR TASK

Implement the complete migration from Playwright to Claude in Chrome by creating these new files and modifying existing ones:

### FILES TO CREATE (NEW)

#### 1. `apply_agent.py` — Main Apply Automation Engine

```python
"""
apply_agent.py - Job application automation using Claude in Chrome.

This module handles:
- Navigating to job pages
- Detecting apply button type (Easy Apply vs External)
- Filling forms intelligently using Claude AI
- Handling external redirects
- Verifying application submission
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

log = logging.getLogger(__name__)


class ChromeJobApplier:
    """Apply to jobs using Claude in Chrome."""
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self.client = anthropic.Anthropic(api_key=self.api_key)
        self.model = "claude-opus-4-6"
        
    async def apply_to_job(
        self,
        job_url: str,
        job_title: str,
        company_name: str,
        application_profile: dict,
        resume_path: str,
        job_description: str = ""
    ) -> dict:
        """
        Apply to a single job using Claude in Chrome.
        
        Args:
            job_url: Full LinkedIn job URL
            job_title: Job title
            company_name: Company name
            application_profile: User's profile {first_name, last_name, email, phone, ...}
            resume_path: Path to resume PDF
            job_description: Job description (for context)
        
        Returns:
            {
                "success": bool,
                "apply_type": "Easy Apply" | "External" | "Manual Required",
                "note": str,
                "timestamp": str (ISO format)
            }
        """
        
        log.info(f"Starting application: {job_title} @ {company_name}")
        start_time = datetime.utcnow()
        
        try:
            # Step 1: Take screenshot of job page
            log.info("Step 1: Navigating to job page and taking screenshot...")
            initial_screenshot = await self._get_page_screenshot(job_url)
            
            if not initial_screenshot:
                return {
                    "success": False,
                    "apply_type": "Manual Required",
                    "note": "Could not load job page",
                    "timestamp": datetime.utcnow().isoformat()
                }
            
            # Step 2: Detect apply button type
            log.info("Step 2: Detecting apply button type...")
            button_info = await self._detect_apply_button(initial_screenshot)
            
            if button_info["type"] == "not_found":
                return {
                    "success": False,
                    "apply_type": "Manual Required",
                    "note": "No apply button found on page",
                    "timestamp": datetime.utcnow().isoformat()
                }
            
            apply_type = button_info["type"]  # "easy_apply" or "external"
            
            # Step 3: Click apply button
            log.info(f"Step 3: Clicking {apply_type} button...")
            if not await self._click_apply_button(button_info):
                return {
                    "success": False,
                    "apply_type": "Manual Required",
                    "note": "Could not click apply button",
                    "timestamp": datetime.utcnow().isoformat()
                }
            
            # Step 4: Wait for form to appear and fill it
            log.info("Step 4: Waiting for form and taking screenshot...")
            await asyncio.sleep(2)  # Wait for modal/page change
            form_screenshot = await self._get_page_screenshot()
            
            if apply_type == "easy_apply":
                # Handle LinkedIn Easy Apply modal
                log.info("Step 5: Filling LinkedIn Easy Apply form...")
                fill_result = await self._fill_easy_apply_form(
                    form_screenshot,
                    application_profile,
                    resume_path,
                    job_description
                )
            else:
                # Handle External Apply (follow redirect and fill external form)
                log.info("Step 5: Following external redirect and filling form...")
                fill_result = await self._fill_external_form(
                    form_screenshot,
                    application_profile,
                    resume_path,
                    job_description
                )
            
            if not fill_result.get("ready_to_submit"):
                return {
                    "success": False,
                    "apply_type": "Manual Required",
                    "note": f"Form filling incomplete: {fill_result.get('note', 'Unknown error')}",
                    "timestamp": datetime.utcnow().isoformat()
                }
            
            # Step 6: Submit form
            log.info("Step 6: Submitting application...")
            submit_result = await self._submit_form()
            
            # Step 7: Verify submission
            log.info("Step 7: Verifying submission...")
            await asyncio.sleep(2)
            verification = await self._verify_submission()
            
            if verification.get("submitted"):
                return {
                    "success": True,
                    "apply_type": "Easy Apply" if apply_type == "easy_apply" else "External",
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
            log.error(f"Error during application: {e}", exc_info=True)
            return {
                "success": False,
                "apply_type": "Manual Required",
                "note": f"Error: {str(e)}",
                "timestamp": datetime.utcnow().isoformat()
            }
    
    async def _get_page_screenshot(self, url: Optional[str] = None) -> Optional[bytes]:
        """
        Get screenshot of current page or navigate to URL and screenshot.
        
        IMPLEMENTATION NOTES:
        - This should use Claude in Chrome's screenshot capability
        - If url provided, navigate first
        - Return PNG bytes or None if fails
        - Handle timeouts gracefully
        """
        # TODO: Implement using Claude in Chrome API
        # Example: Use browser_use library or Playwright in parallel with Claude in Chrome
        pass
    
    async def _detect_apply_button(self, screenshot: bytes) -> dict:
        """
        Use Claude vision to detect apply button type from screenshot.
        
        Returns:
            {
                "type": "easy_apply" | "external" | "not_found",
                "confidence": 0.0-1.0,
                "location": (x, y) or None,
                "description": str
            }
        """
        screenshot_b64 = base64.b64encode(screenshot).decode()
        
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
                    {
                        "type": "text",
                        "text": """Analyze this LinkedIn job posting screenshot. Identify the apply button.

Return JSON with:
{
    "type": "easy_apply" or "external" or "not_found",
    "confidence": 0.0-1.0,
    "location": {"x": int, "y": int} or null,
    "description": "what button you found",
    "reasoning": "why you think this is easy_apply vs external"
}

Easy Apply indicators:
- "Easy Apply" text
- LinkedIn modal button
- Usually blue

External Apply indicators:
- "Apply" button leading to external site
- Usually has company branding
- May say "Apply on company website"
"""
                    }
                ]
            }]
        )
        
        # Parse response
        try:
            text = response.content[0].text
            # Extract JSON from response
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                return json.loads(match.group())
        except:
            pass
        
        return {"type": "not_found", "confidence": 0, "location": None}
    
    async def _click_apply_button(self, button_info: dict) -> bool:
        """
        Click the detected apply button at given coordinates.
        
        IMPLEMENTATION NOTES:
        - Use Claude in Chrome to click at button_info["location"]
        - Wait for page change
        - Return True if successful, False otherwise
        """
        # TODO: Implement using Claude in Chrome click API
        pass
    
    async def _fill_easy_apply_form(
        self,
        screenshot: bytes,
        profile: dict,
        resume_path: str,
        job_desc: str
    ) -> dict:
        """
        Fill LinkedIn Easy Apply modal form.
        
        Returns:
            {
                "fields_filled": int,
                "ready_to_submit": bool,
                "note": str
            }
        """
        screenshot_b64 = base64.b64encode(screenshot).decode()
        
        response = self.client.messages.create(
            model=self.model,
            max_tokens=1000,
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
                        "text": f"""You are filling out a LinkedIn Easy Apply form on behalf of the candidate.

CANDIDATE PROFILE:
- Name: {profile.get('first_name')} {profile.get('last_name')}
- Email: {profile.get('email')}
- Phone: {profile.get('phone')}
- LinkedIn: {profile.get('linkedin_url')}
- GitHub: {profile.get('github_url')}
- Location: {profile.get('current_location')}
- Years of Experience: {profile.get('years_of_experience')}
- Work Permit: {profile.get('work_permit')}

JOB DESCRIPTION: {job_desc[:500]}

RESUME PATH: {resume_path}

INSTRUCTIONS:
1. Identify all visible form fields
2. For each field, decide what value to fill (use profile data)
3. For questions, provide thoughtful answers based on the job
4. For file uploads, use the resume path provided
5. Do NOT submit yet - just fill the form

Return JSON:
{{
    "form_analysis": "description of form structure",
    "fields_to_fill": [
        {{"name": "field_name", "value": "value_to_fill", "type": "text|select|checkbox|file"}},
        ...
    ],
    "needs_resume_upload": true|false,
    "custom_questions": ["any non-standard questions found"],
    "ready_to_submit": true|false,
    "note": "any issues or notes"
}}
"""
                    }
                ]
            }]
        )
        
        # Parse and execute form filling
        try:
            text = response.content[0].text
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                form_plan = json.loads(match.group())
                
                # TODO: Execute form filling actions using Claude in Chrome
                # - Click fields
                # - Type values
                # - Select options
                # - Upload resume
                
                return {
                    "fields_filled": len(form_plan.get("fields_to_fill", [])),
                    "ready_to_submit": form_plan.get("ready_to_submit", False),
                    "note": form_plan.get("note", "")
                }
        except:
            pass
        
        return {
            "fields_filled": 0,
            "ready_to_submit": False,
            "note": "Could not parse form"
        }
    
    async def _fill_external_form(
        self,
        screenshot: bytes,
        profile: dict,
        resume_path: str,
        job_desc: str
    ) -> dict:
        """
        Fill external ATS form (Greenhouse, Lever, Ashby, etc).
        
        Returns same as _fill_easy_apply_form
        """
        screenshot_b64 = base64.b64encode(screenshot).decode()
        
        response = self.client.messages.create(
            model=self.model,
            max_tokens=1000,
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
                        "text": f"""You are filling out an external job application form (ATS system).

CANDIDATE PROFILE:
- Name: {profile.get('first_name')} {profile.get('last_name')}
- Email: {profile.get('email')}
- Phone: {profile.get('phone')}
- Location: {profile.get('current_location')}
- Work Permit: {profile.get('work_permit')}
- Willing to Relocate: {profile.get('willing_to_relocate')}
- Salary Expectation: {profile.get('salary_expectation')} {profile.get('salary_currency')}

RESUME PATH: {resume_path}

INSTRUCTIONS:
1. Analyze the form structure
2. Detect which ATS this is (Greenhouse, Lever, Ashby, etc.)
3. For each field, provide intelligent value based on profile
4. For yes/no questions, answer based on the job requirements
5. For salary questions, provide the salary expectation
6. For relocation, indicate if willing

Return JSON:
{{
    "detected_ats": "greenhouse|lever|ashby|workday|other",
    "fields_to_fill": [...],
    "needs_resume_upload": true|false,
    "ready_to_submit": true|false,
    "note": "any issues"
}}
"""
                    }
                ]
            }]
        )
        
        # Similar parsing and execution as _fill_easy_apply_form
        try:
            text = response.content[0].text
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                form_plan = json.loads(match.group())
                return {
                    "fields_filled": len(form_plan.get("fields_to_fill", [])),
                    "ready_to_submit": form_plan.get("ready_to_submit", False),
                    "note": form_plan.get("note", "")
                }
        except:
            pass
        
        return {
            "fields_filled": 0,
            "ready_to_submit": False,
            "note": "Could not parse form"
        }
    
    async def _submit_form(self) -> bool:
        """
        Click submit button.
        
        IMPLEMENTATION NOTES:
        - Use Claude in Chrome to find and click submit button
        - Return True if clicked successfully
        """
        # TODO: Implement
        pass
    
    async def _verify_submission(self) -> dict:
        """
        Verify that application was successfully submitted.
        
        Returns:
            {
                "submitted": bool,
                "confirmation_text": str or None,
                "error": str or None
            }
        """
        # Take screenshot
        screenshot = await self._get_page_screenshot()
        
        if not screenshot:
            return {"submitted": False, "error": "Could not get screenshot"}
        
        # Use Claude to check for confirmation
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
                        "text": """Check if this is a confirmation/success page for a job application.

Look for phrases like:
- "Thank you"
- "Application received"
- "Successfully submitted"
- "Submission confirmed"
- "We've received your application"
- "Bewerbung eingegangen" (German)

Return JSON:
{"submitted": true|false, "confirmation_text": "text found" or null}
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


async def apply_to_job(
    job_url: str,
    job_title: str,
    company_name: str,
    application_profile: dict,
    resume_path: str,
    job_description: str = ""
) -> dict:
    """Public API for applying to a job."""
    applier = ChromeJobApplier()
    return await applier.apply_to_job(
        job_url, job_title, company_name,
        application_profile, resume_path, job_description
    )
```

**What this file should do:**
1. ✅ Navigate to job URL using Claude in Chrome
2. ✅ Take screenshots and analyze with Claude vision
3. ✅ Detect apply button type (Easy Apply vs External)
4. ✅ Click apply button
5. ✅ Fill form fields intelligently
6. ✅ Submit form
7. ✅ Verify submission
8. ✅ Return result dict

**IMPORTANT IMPLEMENTATION NOTES:**
- Methods marked with `# TODO:` need to be implemented using Claude in Chrome
- Use Claude's vision capability (base64 images) to analyze screenshots
- Use Claude's reasoning to decide form values
- Handle all async/await properly
- Log all steps for debugging

---

#### 2. `apply_integration.py` — Integration with Job Matcher

```python
"""
apply_integration.py - Integrate apply_agent with job matching pipeline.
"""

import logging
from pathlib import Path
from typing import Optional
from apply_agent import apply_to_job
from apply_logger import log_application_result

log = logging.getLogger(__name__)


async def apply_to_matched_jobs(
    jobs: list[dict],
    config: dict,
    max_applications: int = 10
) -> list[dict]:
    """
    Apply to a list of matched jobs.
    
    Args:
        jobs: List of matched job dicts from matcher
        config: Configuration dict with application_profile and paths
        max_applications: Max apps per session
    
    Returns:
        List of result dicts: [{success, apply_type, url, timestamp}, ...]
    """
    
    # Load application profile
    profile = config.get("application_profile", {})
    resume_en = config.get("paths", {}).get("resume_en", "")
    resume_de = config.get("paths", {}).get("resume_de", "")
    
    if not resume_en:
        log.error("No resume path configured")
        return []
    
    results = []
    applied_count = 0
    
    for job in jobs:
        if applied_count >= max_applications:
            log.info(f"Reached max applications per session ({max_applications})")
            break
        
        job_url = job.get("url", "")
        job_title = job.get("title", "")
        company = job.get("company", "")
        description = job.get("description", "")
        
        if not job_url:
            log.warning("Job missing URL, skipping")
            continue
        
        log.info(f"Applying: {job_title} @ {company}")
        
        try:
            # Apply to job
            result = await apply_to_job(
                job_url=job_url,
                job_title=job_title,
                company_name=company,
                application_profile=profile,
                resume_path=resume_en,  # TODO: Detect language and select resume
                job_description=description
            )
            
            # Log result
            log_application_result(job, result, resume_en)
            
            results.append({
                "url": job_url,
                "title": job_title,
                "company": company,
                "success": result.get("success", False),
                "apply_type": result.get("apply_type", "Unknown"),
                "note": result.get("note", ""),
                "timestamp": result.get("timestamp", "")
            })
            
            if result.get("success"):
                applied_count += 1
                log.info(f"✅ Applied successfully")
            else:
                log.warning(f"⚠️ Manual review needed: {result.get('note')}")
        
        except Exception as e:
            log.error(f"Error applying to {job_title}: {e}")
            results.append({
                "url": job_url,
                "title": job_title,
                "company": company,
                "success": False,
                "apply_type": "Manual Required",
                "note": str(e),
                "timestamp": ""
            })
    
    return results
```

**What this file should do:**
1. ✅ Take list of matched jobs from matcher
2. ✅ Load user's profile and resume path
3. ✅ Call apply_to_job() for each
4. ✅ Log results using apply_logger
5. ✅ Return list of results

---

#### 3. `apply_logger.py` — Logging to JSON/Excel/Database

```python
"""
apply_logger.py - Log application results to JSON, Excel, and database.
"""

import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

log = logging.getLogger(__name__)


def log_application_result(
    job: dict,
    result: dict,
    resume_used: str,
    output_dir: str = "outputs"
) -> None:
    """
    Log application result to JSON, Excel tracker, and database.
    
    Args:
        job: Job dict {url, title, company, description}
        result: Result from apply_agent {success, apply_type, note, timestamp}
        resume_used: Path to resume file used
        output_dir: Output directory
    """
    
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    # 1. Log to JSON
    _log_to_json(job, result, resume_used, output_path)
    
    # 2. Update Excel tracker
    _update_excel_tracker(job, result, output_path)
    
    # 3. Log to database
    _log_to_database(job, result, output_path)


def _log_to_json(
    job: dict,
    result: dict,
    resume_used: str,
    output_path: Path
) -> None:
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
        "resume_used": Path(resume_used).name if resume_used else "",
        "note": result.get("note", "")
    })
    
    log_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info(f"Logged to {log_file}")


def _update_excel_tracker(
    job: dict,
    result: dict,
    output_path: Path
) -> None:
    """Update Excel tracker spreadsheet"""
    
    # TODO: Update job_application_tracker.xlsx
    # - Find appropriate sheet (Applied, Rejected, etc.)
    # - Add new row with job info
    # - Save file
    
    log.info("Excel tracker would be updated here")


def _log_to_database(
    job: dict,
    result: dict,
    output_path: Path
) -> None:
    """Log to database for deduplication"""
    
    # TODO: Update dedup.db
    # - Record URL and company/title combo
    # - Prevent duplicate applications
    
    log.info("Database would be updated here")
```

**What this file should do:**
1. ✅ Log successful/failed applications to JSON
2. ✅ Update Excel tracker with new row
3. ✅ Record in database for dedup
4. ✅ Handle errors gracefully

---

### FILES TO MODIFY (EXISTING)

#### 4. Modify `main.py` — Replace Playwright with Claude in Chrome

**In the apply phase, replace:**
```python
# OLD (Playwright):
from applier.applier import run
results = await _run_apply(jobs, cfg, stop_flag)

# NEW (Claude in Chrome):
from apply_integration import apply_to_matched_jobs
results = await apply_to_matched_jobs(matched_jobs, cfg)
```

**Find the section where jobs are applied to and replace the entire applier call with the new apply_integration.**

---

#### 5. Modify `core/config.yaml` — Add Chrome Configuration (Optional)

```yaml
# Add these sections if needed:

chrome:
  headless: false              # Show browser (for testing/debugging)
  viewport_width: 1366
  viewport_height: 768
  timeout_seconds: 30
  request_timeout_seconds: 10
  max_retries_per_job: 2

apply:
  min_match_score: 70
  max_per_session: 10
  delay_min_seconds: 1
  delay_max_seconds: 3
  verify_submission_wait_seconds: 2
```

---

### FILES TO ARCHIVE (DEPRECATED)

1. Move `applier/linkedin_clicker.py` → Archive or delete
2. Move `applier/linkedin_applier.py` → Archive or delete
3. Move `applier/external_applier.py` → Archive or delete
4. Keep `.gitignore` updated

---

## IMPLEMENTATION INSTRUCTIONS

### Step 1: Create `apply_agent.py`
- Copy the template above
- Implement the `# TODO:` sections using Claude in Chrome
- Key areas to implement:
  - `_get_page_screenshot()` — Navigate and screenshot
  - `_click_apply_button()` — Click at coordinates
  - `_fill_easy_apply_form()` — Fill LinkedIn modal
  - `_fill_external_form()` — Fill external ATS form
  - Form field interaction (click, type, select)

### Step 2: Create `apply_integration.py`
- Copy the template above
- No major TODOs needed
- Integrates apply_agent with job matcher

### Step 3: Create `apply_logger.py`
- Copy the template above
- Implement `_update_excel_tracker()` using openpyxl
- Implement `_log_to_database()` using sqlite3
- JSON logging already implemented

### Step 4: Modify `main.py`
- Find the apply phase
- Replace Playwright calls with Claude in Chrome calls
- Update imports from `applier.applier` to `apply_integration`

### Step 5: Test with Real Jobs
```python
# Test 1: Easy Apply
test_job_easy = {
    "url": "https://www.linkedin.com/jobs/view/EASY_APPLY_JOB_ID/",
    "title": "Backend Engineer",
    "company": "TestCorp",
    "description": "Test job"
}

# Test 2: External Apply
test_job_external = {
    "url": "https://www.linkedin.com/jobs/view/EXTERNAL_APPLY_JOB_ID/",
    "title": "Backend Engineer",
    "company": "TestCorp",
    "description": "Test job"
}

# Run tests
from apply_agent import apply_to_job
result1 = await apply_to_job(...)
result2 = await apply_to_job(...)
```

---

## CRITICAL IMPLEMENTATION DETAILS

### Claude in Chrome Integration

You'll need to use one of these approaches:

**Option 1: Use existing Playwright in parallel with Claude in Chrome**
```python
# Keep Playwright for screenshots/navigation
# Use Claude in Chrome for decision-making
from playwright.async_api import async_playwright

async with async_playwright() as pw:
    browser = await pw.chromium.launch(headless=False)
    page = await browser.new_page()
    
    # Navigation and screenshots via Playwright
    await page.goto(url)
    screenshot = await page.screenshot()
    
    # Decision-making via Claude
    button_info = await _detect_apply_button(screenshot)
    
    # Interaction via Playwright
    await page.click_at(button_info["location"])
```

**Option 2: Use Claude in Chrome SDK directly (if available)**
```python
from anthropic_browser import BrowserSession

session = BrowserSession()
await session.navigate(url)
screenshot = await session.screenshot()
await session.click_at(x, y)
```

**Recommendation:** Use Option 1 (Playwright for automation + Claude for decisions) for reliability.

---

## TESTING CHECKLIST

- [ ] Easy Apply: Test with LinkedIn job that has Easy Apply
- [ ] External Apply: Test with LinkedIn job that has external apply
- [ ] Form filling: Verify all fields are filled correctly
- [ ] Submission: Verify application submitted successfully
- [ ] JSON logging: Check applied_jobs_log.json created
- [ ] Excel tracker: Check job_application_tracker.xlsx updated
- [ ] Error handling: Test with invalid form, missing fields, etc.
- [ ] No Playwright code running in apply phase

---

## SUCCESS CRITERIA

✅ **All of these must be true:**
1. Easy Apply jobs apply successfully (verified with real LinkedIn job)
2. External Apply jobs apply successfully (verified with Greenhouse/Lever/etc.)
3. Application results logged to JSON
4. Excel tracker auto-updates with new applications
5. Database dedup prevents duplicate applications
6. All form fields filled correctly
7. Submission verified before returning success
8. Error handling sends jobs to manual queue
9. No Playwright code in apply phase
10. All code is clean, documented, and tested

---

## TIMELINE

- **Part 1 (2-3 hours):** Create apply_agent.py with basic structure
- **Part 2 (2-3 hours):** Implement form filling logic
- **Part 3 (1-2 hours):** Create apply_integration and apply_logger
- **Part 4 (1-2 hours):** Integrate with main.py and test
- **Part 5 (1 hour):** Archive old code and cleanup

**Total: ~8-10 hours of focused development**

---

## QUESTIONS TO HANDLE DURING IMPLEMENTATION

1. **How to get Claude in Chrome screenshots?**
   - Use Playwright for navigation/screenshot
   - Feed to Claude for analysis
   - Use Claude decisions to control Playwright

2. **How to handle form fields?**
   - Claude analyzes screenshot and says "fill field at (x, y) with value"
   - Playwright clicks and types the value
   - Repeat for all fields

3. **How to detect form type?**
   - Take screenshot after clicking Apply
   - Claude analyzes and says "Easy Apply modal" or "Greenhouse form" or "Lever form"
   - Route to appropriate handler

4. **How to verify submission?**
   - Click submit button
   - Wait 2 seconds
   - Take screenshot
   - Claude checks for "Thank you" / "Application received" / confirmation text
   - Return success/failure

5. **How to handle external redirects?**
   - After clicking apply, monitor URL changes
   - If URL changes from linkedin.com to other domain, it's external
   - Wait for form to load
   - Fill the external form using same process

---

## FINAL NOTES

- **This is a complete rewrite of the apply phase**
- **Keep everything else the same** (scraping, matching, database)
- **Trade API cost for developer time** (worth it)
- **Use Claude's reasoning for intelligence** (don't try to hardcode logic)
- **Log everything for debugging** (important for reliability)
- **Test thoroughly before full deployment** (real jobs)

**You're replacing complex, fragile browser automation with intelligent Claude-powered decisions. This should be much more reliable and faster to develop.**

---

## END OF PROMPT

**To use this prompt:**
1. Save it as a file or copy-paste it
2. Give it to Claude Code
3. Claude Code will create all files and integrate everything
4. You test with real jobs
5. Done!
