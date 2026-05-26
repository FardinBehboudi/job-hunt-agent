"""
Fixes:
1. Failed jobs go to "Jobs to Apply" section (not "Successfully Applied")
2. Adds Remove button to Manual Queue items

Run from project root:
    python fix_sections.py
"""
from pathlib import Path

dash = Path("dashboard/dashboard.py")
content = dash.read_text(encoding="utf-8")

# Fix 1: Failed stays in pending section, only 'done' goes to applied
OLD1 = ("  const applied = _applyJobs.filter((j,i) =>\n"
        "    j._applyStatus === 'done' || j._applyStatus === 'failed');")
NEW1 = ("  const applied = _applyJobs.filter((j,i) => j._applyStatus === 'done');")
if OLD1 in content:
    content = content.replace(OLD1, NEW1, 1)
    print("✅ Fix 1: Failed jobs removed from applied section")
else:
    print("⚠️  Fix 1: pattern not found")

# Fix 2: Also update pending filter to include failed
OLD2 = ("  const pending = _applyJobs.filter((j,i) =>\n"
        "    !j._applyStatus || j._applyStatus === 'pending' || j._applyStatus === 'running');")
NEW2 = ("  const pending = _applyJobs.filter((j,i) =>\n"
        "    !j._applyStatus || j._applyStatus === 'pending' || j._applyStatus === 'running' || j._applyStatus === 'failed');")
if OLD2 in content:
    content = content.replace(OLD2, NEW2, 1)
    print("✅ Fix 2: Failed jobs now show in 'Jobs to Apply' section")
else:
    print("⚠️  Fix 2: pattern not found")

# Fix 3: Add Remove button to manual queue cards + fix section label
OLD3 = ("  const actionBtns = section === 'manual'\n"
        "    ? `<div class=\"ajc-actions\">\n"
        "         <button class=\"ajc-btn ajc-btn-applied\" onclick=\"markJobApplied(${idx})\">&#10003; Applied</button>\n"
        "         <button class=\"ajc-btn ajc-btn-retry\"   onclick=\"retryJob(${idx})\">&#8617; Retry</button>\n"
        "         ${openBtn}\n"
        "       </div>`")
NEW3 = ("  const actionBtns = section === 'manual'\n"
        "    ? `<div class=\"ajc-actions\">\n"
        "         <button class=\"ajc-btn ajc-btn-applied\" onclick=\"markJobApplied(${idx})\">&#10003; Applied</button>\n"
        "         <button class=\"ajc-btn ajc-btn-retry\"   onclick=\"retryJob(${idx})\">&#8617; Retry</button>\n"
        "         <button class=\"ajc-btn\" style=\"background:#2d1515;color:#f87171;\" onclick=\"removeFromManual(${idx})\">&#10005; Remove</button>\n"
        "         ${openBtn}\n"
        "       </div>`")
if OLD3 in content:
    content = content.replace(OLD3, NEW3, 1)
    print("✅ Fix 3: Added Remove button to manual queue cards")
else:
    print("⚠️  Fix 3: pattern not found")

# Fix 4: Add removeFromManual() function after retryJob()
OLD4 = "async function retryJob(idx) {\n  const j = _applyJobs[idx];\n  if (!j) return;\n  j._applyStatus = 'pending';\n  j._applyNote   = '';\n  renderApplyCards();\n  showToast('Job returned to queue — run Apply again to retry', 'info');\n}"
NEW4 = ("async function retryJob(idx) {\n"
        "  const j = _applyJobs[idx];\n"
        "  if (!j) return;\n"
        "  j._applyStatus = 'pending';\n"
        "  j._applyNote   = '';\n"
        "  renderApplyCards();\n"
        "  showToast('Job returned to queue \\u2014 run Apply again to retry', 'info');\n"
        "}\n"
        "\n"
        "function removeFromManual(idx) {\n"
        "  const j = _applyJobs[idx];\n"
        "  if (!j) return;\n"
        "  _applyJobs.splice(idx, 1);\n"
        "  _applyStats.manual = Math.max(0, (_applyStats.manual||0) - 1);\n"
        "  document.getElementById('asb-manual').textContent = _applyStats.manual;\n"
        "  renderApplyCards();\n"
        "  showToast('Removed from manual queue', 'info');\n"
        "}")
if "async function retryJob(idx)" in content:
    content = content.replace(OLD4, NEW4, 1)
    print("✅ Fix 4: Added removeFromManual() function")
else:
    # Just append after retryJob closing brace
    content = content.replace(
        "  showToast('Job returned to queue — run Apply again to retry', 'info');\n}",
        "  showToast('Job returned to queue — run Apply again to retry', 'info');\n}\n\nfunction removeFromManual(idx) {\n  const j = _applyJobs[idx];\n  if (!j) return;\n  _applyJobs.splice(idx, 1);\n  _applyStats.manual = Math.max(0, (_applyStats.manual||0) - 1);\n  document.getElementById('asb-manual').textContent = _applyStats.manual;\n  renderApplyCards();\n  showToast('Removed from manual queue', 'info');\n}",
        1
    )
    print("✅ Fix 4: Added removeFromManual() (fallback)")

dash.write_text(content, encoding="utf-8")

import subprocess
r = subprocess.run(["python", "-m", "py_compile", "dashboard/dashboard.py"], capture_output=True, text=True)
print("✅ Syntax OK" if r.returncode == 0 else f"❌ {r.stderr}")
