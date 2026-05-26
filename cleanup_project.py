"""
Clean up job-hunt-agent project:
- Removes Chrome extension migration artifacts
- Removes debug/test scripts
- Removes temporary fix scripts
- Removes docs for abandoned approach
- Updates .gitignore for secrets/cache/uploads

Run from project root:
    python cleanup_project.py
"""
import os, shutil
from pathlib import Path

ROOT = Path(".")

# ── Files to DELETE ────────────────────────────────────────────────────────────
DELETE_FILES = [
    # Our fix scripts
    "fix_all_apply.py", "fix_apply_start_and_manual.py",
    "fix_apply_start_query.py", "fix_apply_stream.py",
    "fix_dashboard_worker.py", "fix_db_tracker.py",
    "fix_job_ids.py", "fix_stream_route.py",
    "restore_pre_migration.py",

    # Chrome extension migration artifacts
    "chrome_extension_bridge.py",
    "apply_agent_chrome.py",
    "apply_agent_old.py",
    "config_loader.py",

    # Debug / test scripts
    "debug_apply_button.py",
    "debug_apply_button_playwright.py",
    "apply_debugger.py",
    "web_debugger.py",
    "test_external_apply_flow.py",
    "test_integration.py",
    "view_db.py",

    # Migration/Chrome extension docs
    "CLAUDE_CODE_COMPLETE_MIGRATION.md",
    "CLAUDE_CODE_FINAL_PROMPT.md",
    "CLAUDE_CODE_MIGRATION_PROMPT.md",
    "CLAUDE_IN_CHROME_SETUP.md",
    "DOCUMENTATION_INDEX.md",
    "EXTENSION_SERVER_INTEGRATION.md",
    "FORM_FILLING_FIX_PROMPT.md",
    "INTEGRATION_COMPLETE.md",
    "INTEGRATION_SUMMARY.md",
    "PROMPT_FOR_CLAUDE_CODE.md",
    "PROMPT_FOR_CLAUDE_CODE_FINAL.md",
    "QUICKSTART_CHROME_EXTENSION.md",
    "QUICK_START_CHECKLIST.md",
    "READY_TO_TEST.md",
    "REFACTORING_COMPLETE.md",
    "REFACTOR_ANALYSIS.md",
    "VERIFICATION_RESULTS.txt",
    "RUN_MIGRATION.bat",
    "RUN_MIGRATION.sh",

    # Accidental files
    "=",
    "\U0001f389_INTEGRATION_COMPLETE.txt",

    # Dashboard backup
    "dashboard/dashboard_restored.py",
]

# ── Directories to DELETE ──────────────────────────────────────────────────────
DELETE_DIRS = [
    "core_old",
    "Usersf_beh.claudepluginsmarketplacesECC",
    "__pycache__",
    "temp-karpathy",
]

# ── .gitignore entries to ADD ──────────────────────────────────────────────────
GITIGNORE_ADD = [
    ".env",
    "dedup.db",
    "uploads/",
    "__pycache__/",
    "*.pyc",
    "*.pyo",
    ".venv/",
    "venv/",
    "*.log",
    ".idea/",
    "data/linkedin_session.json",
]

print("=== Deleting files ===")
for f in DELETE_FILES:
    p = ROOT / f
    if p.exists():
        p.unlink()
        print(f"  ✅ Deleted {f}")
    else:
        print(f"  ⏭  {f} (not found)")

print("\n=== Deleting directories ===")
for d in DELETE_DIRS:
    p = ROOT / d
    if p.exists():
        shutil.rmtree(p)
        print(f"  ✅ Deleted {d}/")
    else:
        print(f"  ⏭  {d}/ (not found)")

print("\n=== Updating .gitignore ===")
gi = ROOT / ".gitignore"
existing = gi.read_text(encoding="utf-8") if gi.exists() else ""
added = []
for entry in GITIGNORE_ADD:
    if entry not in existing:
        existing += f"\n{entry}"
        added.append(entry)
gi.write_text(existing.rstrip() + "\n", encoding="utf-8")
if added:
    print(f"  ✅ Added: {', '.join(added)}")
else:
    print("  ✅ .gitignore already up to date")

print("\n=== Untracking sensitive/generated files from git ===")
os.system("git rm --cached .env 2>/dev/null && echo '  ✅ Untracked .env'")
os.system("git rm --cached dedup.db 2>/dev/null && echo '  ✅ Untracked dedup.db'")
os.system("git rm -r --cached __pycache__ 2>/dev/null && echo '  ✅ Untracked __pycache__'")
os.system("git rm -r --cached uploads/linkedin_session.json 2>/dev/null")

print("\nDone! Now run:")
print("  git add -A")
print("  git commit -m 'chore: clean up project — remove migration artifacts and fix scripts'")
print("  git push --force")
