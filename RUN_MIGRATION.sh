#!/bin/bash

# MIGRATION COMMAND FOR CLAUDE CODE
# Execute this to implement all Claude in Chrome migration changes

echo "=================================="
echo "Claude in Chrome Migration"
echo "=================================="
echo ""
echo "This will:"
echo "✅ Delete unnecessary MD files"
echo "✅ Create apply_agent.py"
echo "✅ Create apply_integration.py"
echo "✅ Create apply_logger.py"
echo "✅ Create config_loader.py"
echo "✅ Create apply_debugger.py"
echo "✅ Create comprehensive README.md"
echo "✅ Update main.py"
echo "✅ Setup resume loading from Dropbox"
echo "✅ Organize files to project root"
echo ""
echo "=================================="
echo ""

# Get the migration prompt content
MIGRATION_PROMPT=$(cat << 'EOF'
# MIGRATION PROMPT FOR CLAUDE CODE - CLAUDE IN CHROME MIGRATION

**IMPORTANT: This is the complete implementation prompt. Claude Code will execute all tasks below.**

---

## OBJECTIVE

Implement complete migration from Playwright to Claude in Chrome for job applications.

**What this will do:**
1. Delete unnecessary MD documentation files
2. Create all new Python files for Claude in Chrome automation
3. Load resume from Dropbox CV folder
4. Update main.py to use new apply system
5. Create comprehensive README.md
6. Organize files to project root
7. Create apply_debugger.py for testing
8. Setup complete apply automation pipeline

---

## PART 1: DELETE UNNECESSARY MD FILES

Delete these files:
- EXTERNAL_APPLY_WORKFLOW_SUMMARY.md
- SYSTEM_STATUS_REPORT.md
- QUICK_START_GUIDE.md
- CLAUDE_CODE_MIGRATION_PROMPT.md
- PROMPT_FOR_CLAUDE_CODE.md
- CLAUDE_CODE_COMPLETE_MIGRATION.md
- CLAUDE_CODE_FINAL_PROMPT.md
- RUN_MIGRATION.sh

Keep only:
- README.md (will create new one)
- CLAUDE.md (project instructions)

---

## PART 2: CREATE PYTHON FILES

### File 1: config_loader.py

Load user configuration from Dropbox CV folder and project config.

```python
"""
config_loader.py - Load user configuration from Dropbox CV folder.
"""

import os
from pathlib import Path
from typing import Optional, Dict
import yaml


class ConfigLoader:
    """Load configuration from Dropbox CV folder and project root."""

    def __init__(self):
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
        if self.config_path.exists():
            with open(self.config_path) as f:
                config = yaml.safe_load(f)
                return config.get("application_profile", {})
        return {}

    def _load_resume_paths(self) -> dict:
        """Load resume PDF paths from Dropbox CV folder"""
        resumes = {}

        # Check for resume_en.pdf
        resume_en = self.dropbox_cv_path / "resume_en.pdf"
        if resume_en.exists():
            resumes["en"] = str(resume_en)

        # Check for resume_de.pdf
        resume_de = self.dropbox_cv_path / "resume_de.pdf"
        if resume_de.exists():
            resumes["de"] = str(resume_de)

        return resumes

    def _load_settings(self) -> dict:
        """Load application settings"""
        return {
            "min_match_score": 70,
            "max_per_session": 10,
            "delay_min_seconds": 1,
            "delay_max_seconds": 3,
            "chrome_headless": False,
            "chrome_timeout": 30
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

### File 2: apply_agent.py

Main apply automation using Claude in Chrome. Use the complete template from PROMPT_FOR_CLAUDE_CODE.md exactly as provided.

[Copy the entire apply_agent.py from PROMPT_FOR_CLAUDE_CODE.md]

### File 3: apply_integration.py

Integration with job matcher. Use the complete template from PROMPT_FOR_CLAUDE_CODE.md exactly as provided.

[Copy the entire apply_integration.py from PROMPT_FOR_CLAUDE_CODE.md]

### File 4: apply_logger.py

Logging system. Use the complete template from PROMPT_FOR_CLAUDE_CODE.md exactly as provided.

[Copy the entire apply_logger.py from PROMPT_FOR_CLAUDE_CODE.md]

### File 5: apply_debugger.py

Debug tool for testing. Use the complete template from PROMPT_FOR_CLAUDE_CODE.md exactly as provided.

[Copy the entire apply_debugger.py from PROMPT_FOR_CLAUDE_CODE.md]

### File 6: test_apply.py (Optional)

Simple test script:

```python
"""
test_apply.py - Test the apply system with a real job.
"""

import asyncio
import logging
from apply_agent import apply_to_job

logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger(__name__)


async def test_easy_apply():
    """Test Easy Apply with a known LinkedIn job."""
    log.info("Testing Easy Apply...")

    result = await apply_to_job(
        job_url="https://www.linkedin.com/jobs/view/TEST_JOB_ID/",
        job_title="Backend Engineer",
        company_name="Test Company",
        job_description="Test job description"
    )

    log.info(f"Result: {result}")
    return result


if __name__ == "__main__":
    asyncio.run(test_easy_apply())
```

---

## PART 3: CREATE README.md

Create comprehensive single documentation file:

```markdown
# Job Hunt Automation System

## Quick Start

### 1. Prerequisites
- Python 3.9+
- API key: ANTHROPIC_API_KEY
- Resume files in ~/Dropbox/CV/

### 2. Setup
\`\`\`bash
pip install -r requirements.txt
\`\`\`

### 3. Configure
Edit \`config.yaml\`:
\`\`\`yaml
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
\`\`\`

### 4. Add Resumes
Place in \`~/Dropbox/CV/\`:
- \`resume_en.pdf\`
- \`resume_de.pdf\`

### 5. Run
\`\`\`bash
# Full automation
python main.py

# Test single job
python apply_debugger.py --url "https://www.linkedin.com/jobs/view/123456/"
\`\`\`

## How It Works

1. **Scraping** - Find jobs from LinkedIn/Indeed/Glassdoor
2. **Matching** - AI scores jobs (min 70% match)
3. **Applying** - Claude in Chrome + Playwright applies automatically
4. **Logging** - Results saved to JSON and Excel

## Features

✅ Easy Apply automation
✅ External ATS form filling
✅ Intelligent form filling via Claude AI
✅ Resume upload
✅ Application tracking

## System Architecture

- **main.py** - Orchestration (scraping → matching → applying)
- **apply_agent.py** - Apply automation core
- **apply_integration.py** - Connect matcher to applier
- **apply_logger.py** - Log results to JSON/Excel/DB
- **config_loader.py** - Load resume from Dropbox CV
- **apply_debugger.py** - Debug tool for testing individual jobs

## Configuration Files

- \`config.yaml\` - User profile and preferences
- \`.env\` - API keys (ANTHROPIC_API_KEY)
- \`core/config.yaml\` - Default configuration

## Output Files

All results saved to \`outputs/\`:
- \`applied_jobs_log.json\` - Application results
- \`job_application_tracker.xlsx\` - Excel tracker
- \`debug_*.png\` - Debug screenshots

## Troubleshooting

### Resume not found
Check that resumes are in \`~/Dropbox/CV/\`:
- resume_en.pdf
- resume_de.pdf

### Apply failed
Run debugger to test individual job:
\`\`\`bash
python apply_debugger.py --url "https://..."
\`\`\`

Check \`outputs/applied_jobs_log.json\` for results.

## Development

### Testing
\`\`\`bash
# Test with Easy Apply job
python apply_debugger.py --url "https://www.linkedin.com/jobs/view/EASY_APPLY_ID/"

# Test with External Apply job
python apply_debugger.py --url "https://www.linkedin.com/jobs/view/EXTERNAL_ID/"
\`\`\`

### Debugging
Add logging to see detailed execution:
\`\`\`python
import logging
logging.basicConfig(level=logging.DEBUG)
\`\`\`

## License

Internal use only.
```

---

## PART 4: MOVE config.yaml TO ROOT

If \`config.yaml\` is in \`core/\` folder, move it to project root:

```bash
mv core/config.yaml config.yaml
```

---

## PART 5: UPDATE main.py

Find the apply section and replace Playwright calls with new system:

OLD:
```python
from applier.applier import run
results = await _run_apply(jobs, cfg)
```

NEW:
```python
from apply_integration import apply_to_matched_jobs
results = await apply_to_matched_jobs(matched_jobs, max_applications=10)
```

Add to imports:
```python
from apply_integration import apply_to_matched_jobs
```

---

## PART 6: FILE ORGANIZATION

Final structure after implementation:

```
~/Projects/claude/job-hunt-agent/
├── main.py
├── config.yaml              (moved from core/)
├── .env
├── .gitignore
├── README.md               (NEW - single doc)
├── CLAUDE.md               (keep)
├── apply_agent.py          (NEW)
├── apply_integration.py     (NEW)
├── apply_logger.py         (NEW)
├── config_loader.py        (NEW)
├── apply_debugger.py       (NEW)
├── test_apply.py           (optional)
├── core/                   (keep if has other code)
├── matcher/                (keep)
└── outputs/                (auto-created)
```

---

## IMPLEMENTATION CHECKLIST

### Phase 1: Delete MD Files
- [ ] Delete EXTERNAL_APPLY_WORKFLOW_SUMMARY.md
- [ ] Delete SYSTEM_STATUS_REPORT.md
- [ ] Delete QUICK_START_GUIDE.md
- [ ] Delete CLAUDE_CODE_MIGRATION_PROMPT.md
- [ ] Delete PROMPT_FOR_CLAUDE_CODE.md
- [ ] Delete CLAUDE_CODE_COMPLETE_MIGRATION.md
- [ ] Delete CLAUDE_CODE_FINAL_PROMPT.md
- [ ] Delete RUN_MIGRATION.sh

### Phase 2: Create Python Files
- [ ] Create config_loader.py
- [ ] Create apply_agent.py (from PROMPT_FOR_CLAUDE_CODE.md)
- [ ] Create apply_integration.py (from PROMPT_FOR_CLAUDE_CODE.md)
- [ ] Create apply_logger.py (from PROMPT_FOR_CLAUDE_CODE.md)
- [ ] Create apply_debugger.py (from PROMPT_FOR_CLAUDE_CODE.md)
- [ ] Create test_apply.py (optional)

### Phase 3: Documentation
- [ ] Create README.md

### Phase 4: Configuration
- [ ] Move config.yaml to project root
- [ ] Update main.py apply section

### Phase 5: Testing
- [ ] Test with apply_debugger.py
- [ ] Verify resume loads from Dropbox
- [ ] Test Easy Apply job
- [ ] Test External Apply job

---

## SUCCESS CRITERIA

✅ All unnecessary MD files deleted
✅ All Python files created
✅ README.md is single documentation
✅ Resume loads from ~/Dropbox/CV/
✅ apply_debugger.py works
✅ Easy Apply test succeeds
✅ External Apply test succeeds
✅ Results logged to JSON
✅ Clean project structure
✅ No Playwright code in apply phase

---

## TIMELINE

- Phase 1 (30 min): Delete MD files
- Phase 2 (1 hour): Create Python files
- Phase 3 (30 min): Create README.md
- Phase 4 (30 min): Update configuration
- Phase 5 (1 hour): Testing

**Total: ~4 hours**

---

**This is the complete migration. Claude Code will implement everything.**
EOF
)

# Run Claude Code with the migration prompt
echo "Starting Claude Code migration..."
echo ""

# Check if claude-code command exists
if ! command -v claude-code &> /dev/null; then
    echo "ERROR: claude-code command not found"
    echo "Please install Claude Code: https://github.com/anthropics/claude-code"
    exit 1
fi

# Run the migration
# Create temporary file with the prompt
TEMP_PROMPT=$(mktemp)
echo "$MIGRATION_PROMPT" > "$TEMP_PROMPT"

# Execute Claude Code
claude-code < "$TEMP_PROMPT"

# Cleanup
rm "$TEMP_PROMPT"

echo ""
echo "=================================="
echo "Migration Complete!"
echo "=================================="
echo ""
echo "Next steps:"
echo "1. Review the changes"
echo "2. Test with: python apply_debugger.py --url '<linkedin_job_url>'"
echo "3. Run full automation: python main.py"
echo ""
