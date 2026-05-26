"""
Restore script — reverts all changes made by the Playwright → Claude in Chrome migration
(CLAUDE_CODE_MIGRATION_PROMPT.md, run at 2026-05-25 19:15)

Run from your project root:
    cd C:/Users/f_beh/Projects/claude/job-hunt-agent
    python restore_pre_migration.py
"""

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
DRY_RUN = "--dry-run" in sys.argv

def log(msg): print(msg)
def warn(msg): print(f"  ⚠️  {msg}")
def ok(msg): print(f"  ✅ {msg}")
def skip(msg): print(f"  ⏭️  {msg}")

print("=" * 60)
print("Pre-migration restore script")
print("Target: state before 2026-05-25 19:15")
if DRY_RUN:
    print("MODE: DRY RUN — no changes will be made")
print("=" * 60)
print()

# ── STEP 1: Delete the 3 new files ──────────────────────────────
log("STEP 1: Removing new files created by migration")

new_files = [
    "apply_agent.py",
    "apply_logger.py",
    "apply_integration.py",
]

for fname in new_files:
    path = PROJECT_ROOT / fname
    if path.exists():
        if not DRY_RUN:
            path.unlink()
        ok(f"Deleted {fname}")
    else:
        skip(f"{fname} not found (already deleted?)")

print()

# ── STEP 2: Revert dashboard/dashboard.py ───────────────────────
log("STEP 2: Reverting dashboard/dashboard.py")

dashboard_path = PROJECT_ROOT / "dashboard" / "dashboard.py"
if dashboard_path.exists():
    content = dashboard_path.read_text(encoding="utf-8")
    old = "import apply_integration"
    new = "import applier"
    if old in content:
        if not DRY_RUN:
            dashboard_path.write_text(content.replace(old, new, 1), encoding="utf-8")
        ok(f"Reverted: '{old}' → '{new}'")
    elif new in content:
        skip("dashboard.py already has 'import applier' — no change needed")
    else:
        warn("Could not find expected string in dashboard.py — manual fix needed:")
        warn(f"  Find:    {old}")
        warn(f"  Replace: {new}")
else:
    warn("dashboard/dashboard.py not found!")

print()

# ── STEP 3: Revert data/config.yaml ─────────────────────────────
log("STEP 3: Reverting data/config.yaml")

config_path = PROJECT_ROOT / "data" / "config.yaml"
if config_path.exists():
    content = config_path.read_text(encoding="utf-8")
    line_to_remove = "\nuse_claude_agent: false"
    if line_to_remove in content:
        if not DRY_RUN:
            config_path.write_text(content.replace(line_to_remove, "", 1), encoding="utf-8")
        ok("Removed 'use_claude_agent: false' from config.yaml")
    else:
        skip("'use_claude_agent: false' not found in config.yaml — no change needed")
else:
    warn("data/config.yaml not found!")

print()
print("=" * 60)
if DRY_RUN:
    print("DRY RUN complete — run without --dry-run to apply changes")
else:
    print("✅ Restore complete! Your codebase is back to its pre-migration state.")
    print()
    print("Summary of what was done:")
    print("  • Deleted:  apply_agent.py")
    print("  • Deleted:  apply_logger.py")
    print("  • Deleted:  apply_integration.py")
    print("  • Reverted: dashboard/dashboard.py (import line)")
    print("  • Reverted: data/config.yaml (removed use_claude_agent flag)")
print("=" * 60)
