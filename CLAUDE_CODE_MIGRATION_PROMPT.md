# Migration Prompt for Claude Code: Playwright → Claude in Chrome

**Objective:** Replace Playwright browser automation with Claude in Chrome for the job application workflow. This prompt should be fed directly to Claude Code for implementation.

---

## Executive Summary

**Goal:** Migrate the job application automation from Playwright (slow, many validation issues) to Claude in Chrome (fast, reliable, better for development).

**What Changes:** Only the "Apply" phase. Job scraping and matching stay the same.

**What Stays:** Database structure, caching, configuration, job matching logic.

**Timeline:** Full rewrite of apply phase using Claude in Chrome API.

**Output Location:** All files go to project root (`~/Projects/claude/job-hunt-agent/`), not CV folder.

---

## Architecture Overview

### Current Flow (Playwright — TO BE DEPRECATED)
```
Job Matched
    ↓
[applier/applier.py] — uses Playwright to automate browser
    ├─ Navigate to LinkedIn job page
    ├─ Click Apply button (Easy Apply or External)
    ├─ Fill form (Easy Apply modal)
    └─ Follow external link and fill external form
    ↓
Result logged to JSON/Excel/DB
```

### New Flow (Claude in Chrome — REPLACE)
```
Job Matched
    ↓
[apply_agent.py] — uses Claude in Chrome to interact with browser
    ├─ Browser already open and logged in
    ├─ Claude in Chrome navigates to LinkedIn job page
    ├─ Claude in Chrome detects and clicks Apply button
    ├─ Claude in Chrome fills Easy Apply modal if needed
    ├─ Claude in Chrome follows external link if needed
    ├─ Claude in Chrome fills external form using AI judgment
    └─ Claude in Chrome verifies submission
    ↓
Result logged to JSON/Excel/DB
```

---

## Implementation Details

### Files to Create/Modify

#### 1. **New Main Apply Agent: `apply_agent.py`**

**Purpose:** Main entry point for applying to jobs using Claude in Chrome.

**Public API:**
```python
async def apply_to_job_via_chrome(
    job: dict,
    application_profile: dict,
    resume_path: str,
    match_score: int
) -> dict:
    """
    Apply to a single job using Claude in Chrome.
    
    Args:
        job: {title, company, url, description}
        application_profile: User's profile data (from config)
        resume_path: Path to resume PDF
        match_score: Job match score (0-100)
    
    Returns:
        {
            "success": bool,
            "apply_type": "Easy Apply" | "External" | "Manual Required",
            "note": "Success message or reason for manual queue",
            "timestamp": ISO timestamp
        }
    """
```

**Responsibilities:**
- Use Claude in Chrome SDK to open/control browser
- Navigate to job URL
- Detect Apply button (Easy Apply vs External)
- Fill forms intelligently using Claude API for decisions
- Handle redirects and external sites
- Verify submission (look for confirmation pages)
- Return result dict with success/failure info

**Key Tasks:**
1. Initialize Claude in Chrome browser connection
2. Navigate to `job.url`
3. Wait for page load
4. Detect apply button type:
   - Easy Apply? → Fill LinkedIn modal
   - External? → Click, follow redirect, fill external form
5. Use Claude AI to decide field values (reuse existing logic from linkedin_applier.py)
6. Submit and verify
7. Return result

#### 2. **Integration File: `apply_integration.py`**

**Purpose:** Bridge between job matcher and apply agent.

**Public API:**
```python
async def apply_to_matched_jobs(
    jobs: list[dict],
    config: dict,
) -> list[dict]:
    """
    Apply to a list of matched jobs using Claude in Chrome.
    
    Returns: [{success, apply_type, url, timestamp}, ...]
    """
```

**Responsibilities:**
- Load application profile from config
- Load resume(s) from config paths
- For each job, call `apply_to_job_via_chrome()`
- Log results to JSON
- Update Excel tracker
- Handle errors gracefully

#### 3. **Updated Config Integration: `config_loader.py`** (modify existing)

**Changes:**
- Ensure application profile is fully loaded
- Add paths to resume files
- Add Chrome browser configuration (headless, viewport, etc.)

**No database changes needed.**

#### 4. **Logging Helper: `apply_logger.py`** (new)

**Purpose:** Standardize logging for apply results.

**Responsibilities:**
- Log to JSON: `applied_jobs_log.json`
- Update Excel tracker: `job_application_tracker.xlsx`
- Create debug screenshots/info in `outputs/` folder
- Emit events for UI dashboard (if needed)

---

## Implementation Workflow

### Phase 1: Setup Claude in Chrome Connection

```python
# In apply_agent.py

from anthropic import Anthropic

class JobApplierAgent:
    def __init__(self, api_key: str):
        self.client = Anthropic(api_key=api_key)
        self.model = "claude-opus-4-6"  # Use best model for complex tasks
        self.browser_action_count = 0
    
    async def initialize_browser(self, headless: bool = False):
        """Start Claude in Chrome session."""
        # Initialize Claude in Chrome
        # Return: browser context/session handle
    
    async def navigate_to_job(self, url: str):
        """Navigate browser to job URL using Claude in Chrome."""
        # Claude takes screenshot
        # Claude navigates to URL
        # Wait for page load
        # Return screenshot for next step
```

### Phase 2: Detect Apply Button

```python
async def detect_apply_button(self, screenshot: bytes) -> dict:
    """
    Use Claude vision to detect apply button type.
    
    Returns: {
        "type": "easy_apply" | "external" | "not_found",
        "confidence": 0.95,
        "button_location": (x, y) or None,
        "reasoning": "description of what Claude sees"
    }
    """
    # Claude analyzes screenshot
    # Identifies button type and location
    # Returns detection info
```

### Phase 3: Click and Navigate

```python
async def click_apply_button(self, button_location: tuple):
    """Click detected apply button."""
    # Claude uses browser action to click at coordinates
    # Wait for page change
    # Take new screenshot
    # Return new page state
```

### Phase 4: Fill Forms Intelligently

```python
async def fill_form_fields(
    self,
    form_type: str,  # "easy_apply" or "external"
    job: dict,
    application_profile: dict,
    resume_path: str
) -> dict:
    """
    Intelligently fill form fields using Claude AI.
    
    Returns: {
        "fields_filled": int,
        "fields_skipped": int,
        "success": bool,
        "ready_to_submit": bool
    }
    """
    # Claude analyzes form fields (screenshot)
    # Claude decides what to fill (using application_profile)
    # Claude fills each field
    # Claude checks for validation errors
    # Return readiness status
```

### Phase 5: Submit and Verify

```python
async def submit_and_verify(self) -> dict:
    """
    Click submit button and verify application was submitted.
    
    Returns: {
        "submitted": bool,
        "verified": bool,
        "confirmation_text": str or None,
        "error_text": str or None
    }
    """
    # Claude finds and clicks submit button
    # Wait for page change (2-3 seconds)
    # Look for confirmation page
    # Check for success phrases: "thank you", "application received", etc.
    # Return verification result
```

---

## Integration with Existing System

### In `main.py` or orchestration:

```python
from apply_agent import apply_to_job_via_chrome
from apply_integration import apply_to_matched_jobs

# After job matching phase:
matched_jobs = [...]  # Jobs with match_score >= 70

# Instead of: result = await applier._apply_linkedin(page, job, cfg, ...)
# Do:
results = await apply_to_matched_jobs(matched_jobs, cfg)

# Results automatically logged
for result in results:
    if result['success']:
        print(f"✅ Applied to {result['url']}")
    else:
        print(f"⚠️ Manual review needed: {result['note']}")
```

---

## Data Flow

### Input
- `job`: LinkedIn job URL + metadata
- `application_profile`: {first_name, last_name, email, phone, linkedin_url, github_url, ...}
- `resume_path`: Path to PDF resume
- `config`: Browser settings, timeout values, etc.

### Output
```json
{
    "url": "https://www.linkedin.com/jobs/view/123456789/",
    "company": "TechCorp",
    "title": "Backend Engineer",
    "success": true,
    "apply_type": "Easy Apply",
    "timestamp": "2026-05-25T14:30:00Z",
    "note": "Successfully submitted via Easy Apply modal"
}
```

### Logging
- **JSON Log:** `outputs/applied_jobs_log.json`
- **Excel Tracker:** `outputs/job_application_tracker.xlsx` (auto-updated)
- **Database:** `dedup.db` (avoid duplicate applications)
- **Debug Files:** `outputs/apply_debug_*.png` (screenshots for troubleshooting)

---

## Key Design Decisions

### 1. Use Claude API for Intelligence
- Detect button types from screenshots
- Decide field values (name, email, etc.)
- Handle custom questions
- Verify submission success

### 2. Simple Coordinate-Based Clicking
- Claude analyzes screenshot, identifies button location
- Click at (x, y) coordinates
- Simple, reliable, no complex selectors

### 3. Graceful Degradation
- If form too complex → Manual queue
- If validation errors → Retry up to 2 times
- If still failing → Log for manual review

### 4. Reuse Existing Logic
- Use `answer_custom_question()` from `linkedin_applier.py`
- Use profile summary logic from existing code
- Use resume text extraction

### 5. No Database Schema Changes
- Keep `dedup.db` as is
- Keep same logging format
- Keep same Excel tracker structure

---

## Implementation Checklist

### Core Files to Create
- [ ] `apply_agent.py` — Main apply automation using Claude in Chrome
- [ ] `apply_integration.py` — Integration with job matcher
- [ ] `apply_logger.py` — Logging to JSON/Excel/DB
- [ ] `README_CHROME_MIGRATION.md` — Documentation

### Existing Files to Modify
- [ ] `main.py` — Replace Playwright applier with Claude in Chrome
- [ ] `core/config.yaml` — Add Chrome browser config if needed
- [ ] `.gitignore` — Add `applier/` to archived files (don't delete)

### Testing
- [ ] Create test with 1 LinkedIn Easy Apply job
- [ ] Create test with 1 external apply job (Greenhouse/Lever)
- [ ] Verify JSON logging works
- [ ] Verify Excel tracker updates

### Deprecation
- [ ] Archive `applier/linkedin_clicker.py` (keep for reference)
- [ ] Archive `applier/linkedin_applier.py` (keep for reference)
- [ ] Archive `applier/external_applier.py` (keep for reference)
- [ ] Create `MIGRATION_NOTES.md` documenting old approach

---

## Performance Targets

| Metric | Playwright | Claude in Chrome | Target |
|--------|-----------|------------------|--------|
| Time per application | 30-60 seconds | 15-30 seconds | ✅ 2x faster |
| Error rate | 10-15% | <5% | ✅ More reliable |
| Development time | Hours debugging | Minutes adjusting | ✅ Better ROI |
| Cost (API calls) | ~$0.01 per app | ~$0.05 per app | Acceptable |

---

## API Integration Points

### From Claude in Chrome:
```python
from anthropic import Anthropic

client = Anthropic()

# Take screenshot
screenshot = client.beta.messages.create(
    model="claude-opus-4-6",
    max_tokens=1024,
    messages=[{
        "role": "user",
        "content": [
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": screenshot_b64}
            },
            {
                "type": "text",
                "text": "What apply button do you see? Describe its location and type."
            }
        ]
    }]
)

# Browser action (if Claude in Chrome provides direct API)
response = client.beta.browser.click(
    coordinates=(500, 300),
    session_id=session_id
)
```

---

## Error Handling Strategy

### Common Failures & Fallbacks

| Error | Cause | Fallback |
|-------|-------|----------|
| Apply button not found | Button not visible/clickable | Manual queue |
| Modal never opens | JavaScript error or slow load | Retry once, then manual |
| Form fields not detected | Form structure unexpected | Manual queue |
| Submission not verified | No confirmation page found | Manual queue |
| External link not recognized | Unusual redirect behavior | Manual queue |

**All failures logged with:** screenshot, error message, timestamp.

---

## Reusable Components from Old Code

**Keep and reuse:**
1. `_profile_summary()` — Format profile for prompts
2. `_resume_path()` — Get resume path from config
3. `answer_custom_question()` — AI-powered field decisions
4. `is_numeric_question()` — Detect number vs text fields
5. Error detection patterns (CAPTCHA, login page checks)

**Don't reuse:**
1. Playwright-specific code (page.goto, page.locator, etc.)
2. Complex selector-based detection
3. javascript evaluation patterns

---

## File Organization

```
~/Projects/claude/job-hunt-agent/
├── main.py                          (orchestration - modify to use apply_agent)
├── apply_agent.py                   (NEW - main apply automation)
├── apply_integration.py             (NEW - integration with matcher)
├── apply_logger.py                  (NEW - logging to JSON/Excel)
├── config_loader.py                 (modify if needed)
├── core/
│   └── config.yaml                  (may add Chrome config)
├── applier/                         (DEPRECATED - archive these)
│   ├── __init__.py
│   ├── linkedin_clicker.py          (deprecated, keep for reference)
│   ├── linkedin_applier.py          (deprecated, keep for reference)
│   ├── external_applier.py          (deprecated, keep for reference)
│   └── events.py                    (may reuse for logging)
├── dedup/
│   └── db.py                        (keep as is)
├── outputs/
│   ├── applied_jobs_log.json        (auto-created)
│   ├── job_application_tracker.xlsx (auto-updated)
│   └── apply_debug_*.png            (auto-created)
└── MIGRATION_NOTES.md               (NEW - document old approach)
```

---

## Configuration Example

No major config changes needed. Just ensure:

```yaml
# config.yaml
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

paths:
  resume_en: "/path/to/resume_en.pdf"
  resume_de: "/path/to/resume_de.pdf"

# New: Chrome browser config (optional)
chrome:
  headless: false              # See browser while applying
  viewport_width: 1366
  viewport_height: 768
  timeout_seconds: 30
  max_retries: 2

# Application preferences
apply:
  min_match_score: 70          # Only apply to 70%+ matches
  max_per_session: 10
  delay_min_seconds: 1
  delay_max_seconds: 3
```

---

## Testing Strategy

### Test 1: Easy Apply
```python
test_job = {
    "url": "https://www.linkedin.com/jobs/view/KNOWN_EASY_APPLY_ID/",
    "title": "Backend Engineer",
    "company": "Test Company",
    "description": "Test job"
}
result = await apply_to_job_via_chrome(test_job, profile, resume_path)
assert result['success'] == True
assert result['apply_type'] == 'Easy Apply'
```

### Test 2: External Apply
```python
test_job = {
    "url": "https://www.linkedin.com/jobs/view/KNOWN_EXTERNAL_APPLY_ID/",
    "title": "Backend Engineer",
    "company": "Test Company",
    "description": "Test job"
}
result = await apply_to_job_via_chrome(test_job, profile, resume_path)
assert result['success'] == True
assert result['apply_type'] == 'External'
```

### Test 3: Logging
```python
# Verify JSON log created
assert Path('outputs/applied_jobs_log.json').exists()

# Verify Excel tracker updated
import openpyxl
wb = openpyxl.load_workbook('outputs/job_application_tracker.xlsx')
# Check latest row matches test job
```

---

## Timeline & Phases

1. **Phase 1 (Day 1):** Create `apply_agent.py` with basic Claude in Chrome integration
2. **Phase 2 (Day 1-2):** Implement Easy Apply detection and form filling
3. **Phase 3 (Day 2-3):** Implement external apply follow + external form filling
4. **Phase 4 (Day 3):** Integration, logging, and Excel tracker updates
5. **Phase 5 (Day 4):** Testing with real jobs, debugging
6. **Phase 6 (Day 4-5):** Archive old Playwright code, documentation

---

## Success Criteria

- [ ] ✅ Easy Apply jobs apply successfully (2+ real tests)
- [ ] ✅ External apply jobs apply successfully (2+ real tests)
- [ ] ✅ JSON log created correctly
- [ ] ✅ Excel tracker updates automatically
- [ ] ✅ All failures logged with debug info
- [ ] ✅ No Playwright code running in production
- [ ] ✅ Old code archived but preserved
- [ ] ✅ Documentation complete

---

## Questions to Address During Implementation

1. **How to provide user profile data to Claude in Chrome?**
   - Pass as context in system prompt

2. **How to handle file uploads (resume)?**
   - Claude in Chrome finds file input
   - Provides path to resume file
   - Browser handles upload

3. **How to verify submission for different platforms?**
   - Check for confirmation page (wait 2 seconds)
   - Look for success phrases: "thank you", "application received", "submitted", etc.
   - Check URL change

4. **How to handle external links across different platforms?**
   - Let Claude analyze page
   - Detect redirect URL
   - Navigate to it

5. **How to retry on failure?**
   - Up to 2 retries per job
   - Different error triggers different actions
   - After 2 failures → manual queue

---

## Final Notes

- **Goal:** Replace error-prone Playwright with robust Claude in Chrome
- **Scope:** Only apply phase changes
- **Benefit:** Faster development, better reliability, easier debugging
- **Timeline:** 4-5 days of focused development
- **Output:** Production-ready apply agent + full documentation

This is a strategic shift that trades API cost ($0.05/app) for developer time (hours saved) and reliability (fewer manual fixes needed).

---

**Ready for Claude Code to implement. Feed this entire prompt to Claude Code with the instruction:**

> Use this comprehensive prompt to rewrite the job application automation to use Claude in Chrome instead of Playwright. Create all new files mentioned, integrate with existing job matcher, and preserve/archive old Playwright code.
