# Job Hunt Automation System

Complete job application automation with Claude in Chrome integration.

## Quick Start

### 1. Prerequisites
- Python 3.9+
- API key: ANTHROPIC_API_KEY
- Resume files in ~/Dropbox/CV/

### 2. Setup
```bash
pip install -r requirements.txt
```

### 3. Configuration
Edit `config.yaml`:
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

### 4. Add Resumes
Place in `~/Dropbox/CV/`:
- `resume_en.pdf` - English resume
- `resume_de.pdf` - German resume

### 5. Run
```bash
# Full automation
python main.py

# Test single job
python apply_debugger.py --url "https://www.linkedin.com/jobs/view/123456/"
```

## How It Works

1. **Scraping** - Find jobs from LinkedIn/Indeed/Glassdoor
2. **Matching** - AI scores jobs (min 70% match)
3. **Applying** - Claude in Chrome + intelligent form filling applies automatically
4. **Logging** - Results saved to JSON and Excel

## Features

✅ Easy Apply automation
✅ External ATS form filling (Greenhouse, Lever, Ashby, Workday)
✅ Resume upload
✅ Intelligent form decisions via Claude AI
✅ Application tracking

## System Architecture

- **main.py** - Orchestration (scraping → matching → applying)
- **apply_agent.py** - Apply automation core
- **apply_integration.py** - Connect matcher to applier
- **apply_logger.py** - Log results to JSON/Excel/DB
- **config_loader.py** - Load resume from Dropbox CV
- **apply_debugger.py** - Debug tool for testing individual jobs

## Configuration Files

- `config.yaml` - User profile and preferences
- `.env` - API keys (ANTHROPIC_API_KEY)
- `core/config.yaml` - Default configuration

## Output Files

All results saved to `outputs/`:
- `applied_jobs_log.json` - Application results
- `job_application_tracker.xlsx` - Excel tracker
- `debug_*.png` - Debug screenshots

## Testing

### Test Easy Apply
```bash
python apply_debugger.py --url "https://www.linkedin.com/jobs/view/EASY_APPLY_ID/"
```

### Test External Apply
```bash
python apply_debugger.py --url "https://www.linkedin.com/jobs/view/EXTERNAL_APPLY_ID/"
```

## Troubleshooting

### Resume not found
Check that resumes are in `~/Dropbox/CV/`:
- resume_en.pdf
- resume_de.pdf

### Apply failed
Run debugger to test individual job:
```bash
python apply_debugger.py --url "https://..."
```

Check `outputs/applied_jobs_log.json` for results.

### API key issues
Set ANTHROPIC_API_KEY in environment:
```bash
export ANTHROPIC_API_KEY="your_api_key_here"
```

## Development

### Testing with debug output
Add logging to see detailed execution:
```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

### Debugging individual jobs
Use apply_debugger.py with verbose logging to identify issues with specific job forms.

## License

Internal use only.
