# Claude in Chrome Extension API Setup Guide

This guide explains how to use Claude in Chrome Extension API for job application automation.

## 🎯 Overview

The system now uses **Claude in Chrome Extension API** to:
- Navigate to job pages
- Take screenshots for analysis
- Click buttons and fill forms intelligently
- Verify application submission

## 🔧 Prerequisites

### 1. Install Claude in Chrome Extension
- Install the Claude in Chrome extension in your browser
- [Get it from: https://chrome.google.com/webstore](https://chrome.google.com/webstore)
- Pin it to your toolbar for easy access

### 2. Set ANTHROPIC_API_KEY
```bash
export ANTHROPIC_API_KEY="your-api-key-here"
```

### 3. Start Your Dashboard
```bash
python dashboard/dashboard.py
```

## 📱 How It Works

### Flow:
```
Dashboard UI (localhost:5000)
    ↓
Click "Apply" or use DEBUG section
    ↓
apply_integration.run()
    ↓
apply_agent.apply_to_job()
    ↓
[Claude in Chrome Extension]
    ├─ Navigate to job URL
    ├─ Take screenshot
    ├─ Claude analyzes with vision
    ├─ Decides what to do
    ├─ Extension executes actions
    └─ Repeat until done
    ↓
Results logged & streamed to UI
```

## 🚀 Using the System

### Method 1: Apply Tab in Dashboard
1. Go to `localhost:5000`
2. "Job Hunt Agent" → Step 4 (Apply)
3. Select matched jobs
4. Click "Start Apply Session"
5. Watch real-time progress

### Method 2: DEBUG Section
1. Go to "Job Hunt Agent" tab
2. Scroll to "🛠️ DEBUG — Test Applier Directly"
3. Paste a LinkedIn job URL
4. Click "Run Apply"
5. Watch events stream in real-time

## 📊 Event Types

As the system runs, you'll see these events:

| Event | Meaning |
|-------|---------|
| `[session_start]` | Apply session started |
| `[job_started]` | Started processing job |
| `[result] ✓ Applied` | Successfully applied ✅ |
| `[result] ⚠ Manual queue` | Needs manual review ⚠️ |
| `[result] ✗ Failed` | Apply failed ✗ |
| `[done]` | Session complete |

## 🎯 What Claude in Chrome Does

### Taking Screenshots
- Navigates to job URL
- Captures full page screenshot
- Sends to Claude for analysis

### Analyzing Form Fields
- Claude vision reads the form
- Identifies all input fields
- Determines field types (text, dropdown, checkbox, file)

### Filling Forms
- Claude decides what to enter based on your profile
- Extension clicks fields
- Claude tells it what to type/select
- Handles resume uploads

### Making Decisions
- "Should I relocate?" → Uses your profile
- "What salary?" → Uses your expectation
- "Years of experience?" → Uses your background

### Verifying Submission
- Takes final screenshot
- Claude checks for "Thank you" / "Application received"
- Confirms success or failure

## ⚙️ Configuration

Edit `config.yaml` to set your profile:

```yaml
application_profile:
  first_name: "Felix"
  last_name: "Behboudi"
  email: "your@email.com"
  phone: "+49 123 456789"
  linkedin_url: "https://linkedin.com/in/..."
  github_url: "https://github.com/..."
  current_location: "Berlin"
  years_of_experience: 5
  salary_expectation: 75000
  work_permit: "German citizen"
  willing_to_relocate: false
  salary_currency: "EUR"
```

## 🐛 Troubleshooting

### "Session complete" but no jobs processed
**Issue**: The apply session runs but shows no results

**Solutions**:
1. Check `ANTHROPIC_API_KEY` is set
2. Verify Claude in Chrome extension is installed
3. Check browser is not blocked by OS firewall
4. Try the DEBUG section with a real LinkedIn URL

### Extension not responding
**Solutions**:
1. Reload the extension (Developer Mode → Reload)
2. Clear extension cache
3. Restart browser

### Form not filling correctly
**Solutions**:
1. Check your `config.yaml` profile is complete
2. Try manual apply first to understand the form
3. Check logs in dashboard console

## 📝 Logs

All activity is logged to:
- **Console**: Real-time in dashboard
- **File**: `outputs/applied_jobs_log.json`
- **Excel**: `outputs/job_application_tracker.xlsx`

## 🔗 Integration Points

- `apply_agent.py` - Claude in Chrome interaction
- `apply_integration.py` - Dashboard integration
- `apply_logger.py` - Result logging
- `config_loader.py` - Profile loading
- `dashboard/dashboard.py` - Web UI

## ✨ Features

✅ Easy Apply automation
✅ External ATS form filling
✅ Intelligent form decisions
✅ Resume upload handling
✅ Real-time progress streaming
✅ Error recovery
✅ Manual queue for complex forms

## 🎓 Advanced Usage

### Testing Individual Jobs
Use the DEBUG section to test with specific URLs:
```
https://www.linkedin.com/jobs/view/[JOB_ID]/
```

### Monitoring Progress
Watch the events stream in real-time:
- Success rate
- Error types
- Form filling accuracy

### Adjusting Behavior
Edit `apply_agent.py` to:
- Change model (e.g., claude-sonnet-4-6)
- Adjust timeouts
- Add logging
- Customize form logic

## 📞 Support

If something isn't working:
1. Check logs in `outputs/`
2. Test with DEBUG section
3. Verify config.yaml
4. Check Claude in Chrome extension is updated

---

**Ready to apply!** Use your dashboard at `localhost:5000` 🚀
