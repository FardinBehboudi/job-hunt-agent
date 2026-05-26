"""
Implements expired/unavailable job detection:

1. applier/applier.py — detects "no longer accepting applications" after page load
   and emits a job_expired SSE event instead of proceeding

2. dashboard/dashboard.py — adds:
   - 4th section: Expired / Unavailable jobs (gray)
   - "Not Interested" button that excludes job permanently
   - Compact card styling
   - SSE handler for job_expired event

Run from project root:
    python fix_expired_jobs.py
"""
from pathlib import Path
import subprocess

# ══════════════════════════════════════════════════════════════════════════════
# PART 1: applier/applier.py — detect expired jobs after page.goto()
# ══════════════════════════════════════════════════════════════════════════════
applier_path = Path("applier/applier.py")
applier = applier_path.read_text(encoding="utf-8")

# Find the goto line and inject expired check after it
OLD_APPLIER = '        await page.goto(job["url"], wait_until="domcontentloaded", timeout=30_000)'
NEW_APPLIER = '''        await page.goto(job["url"], wait_until="domcontentloaded", timeout=30_000)

        # ── Check if job is expired / no longer available ──────────────────
        try:
            page_text = (await page.inner_text("body")).lower()
            _expired_phrases = [
                "no longer accepting applications",
                "this job is no longer available",
                "job is closed",
                "position has been filled",
                "this posting has expired",
                "no longer available",
                "job has expired",
                "application period has ended",
            ]
            if any(phrase in page_text for phrase in _expired_phrases):
                log.info("Job expired/unavailable: %s", job.get("url"))
                _emit("job_expired", {
                    "url":    job.get("url", ""),
                    "title":  job.get("title", ""),
                    "reason": "No longer accepting applications",
                })
                return {"success": False, "expired": True,
                        "note": "No longer accepting applications"}
        except Exception:
            pass  # best-effort check, continue normally'''

if OLD_APPLIER in applier:
    applier = applier.replace(OLD_APPLIER, NEW_APPLIER, 1)
    applier_path.write_text(applier, encoding="utf-8")
    print("✅ applier.py: Added expired job detection after page.goto()")
else:
    print("⚠️  applier.py: Could not find goto() line — add expired check manually after page.goto()")

# ══════════════════════════════════════════════════════════════════════════════
# PART 2: dashboard.py — expired section + compact cards
# ══════════════════════════════════════════════════════════════════════════════
dash = Path("dashboard/dashboard.py")
content = dash.read_text(encoding="utf-8")
changed = False

# 2a. Add compact card CSS + expired section CSS
OLD_CSS = ".ajc-btn-open     { background:#1e293b; color:#94a3b8; text-decoration:none; display:inline-block; }"
NEW_CSS = """.ajc-btn-open     { background:#1e293b; color:#94a3b8; text-decoration:none; display:inline-block; }
.ajc-btn-exclude  { background:#1a1a2e; color:#6b7280; }
.apply-job-card.status-expired { border-left:3px solid #374151; opacity:.65; }
.section-dot.dot-expired  { color:#4b5563; }
/* Compact cards */
.apply-job-card { padding:6px 10px !important; gap:8px !important; }
.ajc-title { font-size:0.82rem !important; }
.ajc-company { font-size:0.73rem !important; }
.ajc-note { font-size:0.71rem !important; }
.ajc-btn { padding:2px 8px !important; font-size:0.7rem !important; }
.apply-section-header { padding:4px 0 6px 0 !important; margin-bottom:6px !important; font-size:0.72rem !important; }"""
if OLD_CSS in content:
    content = content.replace(OLD_CSS, NEW_CSS, 1)
    changed = True
    print("✅ CSS: Added expired styles + compact card overrides")
else:
    print("⚠️  CSS anchor not found for expired styles")

# 2b. Add expired section HTML after the applied section
OLD_HTML_ANCHOR = '''              <!-- Section: Successfully Applied -->
              <div class="apply-section-block" id="section-applied" style="display:none">
                <div class="apply-section-header">
                  <span class="section-dot dot-applied">&#9679;</span>
                  Successfully Applied <span id="count-applied" class="section-count"></span>
                </div>
                <div id="cards-applied"></div>
              </div>'''
NEW_HTML_ANCHOR = '''              <!-- Section: Successfully Applied -->
              <div class="apply-section-block" id="section-applied" style="display:none">
                <div class="apply-section-header">
                  <span class="section-dot dot-applied">&#9679;</span>
                  Successfully Applied <span id="count-applied" class="section-count"></span>
                </div>
                <div id="cards-applied"></div>
              </div>
              <!-- Section: Expired / Unavailable -->
              <div class="apply-section-block" id="section-expired" style="display:none">
                <div class="apply-section-header">
                  <span class="section-dot dot-expired">&#9679;</span>
                  Expired / Unavailable <span id="count-expired" class="section-count"></span>
                </div>
                <div id="cards-expired"></div>
              </div>'''
if OLD_HTML_ANCHOR in content:
    content = content.replace(OLD_HTML_ANCHOR, NEW_HTML_ANCHOR, 1)
    changed = True
    print("✅ HTML: Added Expired/Unavailable section")
else:
    print("⚠️  HTML anchor not found for expired section")

# 2c. Add expired status to the pending filter (so it doesn't clog "Jobs to Apply")
OLD_PENDING = ("  const pending = _applyJobs.filter((j,i) =>\n"
               "    !j._applyStatus || j._applyStatus === 'pending' || j._applyStatus === 'running' || j._applyStatus === 'failed');")
NEW_PENDING = ("  const pending = _applyJobs.filter((j,i) =>\n"
               "    !j._applyStatus || j._applyStatus === 'pending' || j._applyStatus === 'running' || j._applyStatus === 'failed');\n"
               "  const expired = _applyJobs.filter((j,i) => j._applyStatus === 'expired');")
if OLD_PENDING in content:
    content = content.replace(OLD_PENDING, NEW_PENDING, 1)
    changed = True
    print("✅ JS: Added expired filter")
else:
    print("⚠️  Pending filter not found")

# 2d. Add expired section renderer after applied section renderer
OLD_APPLIED_RENDER = (
    "  // Applied section\n"
    "  const appliedSection = document.getElementById('section-applied');\n"
    "  const appliedContainer = document.getElementById('cards-applied');\n"
    "  const appliedCount = document.getElementById('count-applied');\n"
    "  if (applied.length) {\n"
    "    appliedSection.style.display = '';\n"
    "    appliedContainer.innerHTML = applied.map(j => _buildJobCard(j, _applyJobs.indexOf(j), 'applied')).join('');\n"
    "    if (appliedCount) appliedCount.textContent = `(${applied.length})`;\n"
    "  } else {\n"
    "    appliedSection.style.display = 'none';\n"
    "  }"
)
NEW_APPLIED_RENDER = (
    "  // Applied section\n"
    "  const appliedSection = document.getElementById('section-applied');\n"
    "  const appliedContainer = document.getElementById('cards-applied');\n"
    "  const appliedCount = document.getElementById('count-applied');\n"
    "  if (applied.length) {\n"
    "    appliedSection.style.display = '';\n"
    "    appliedContainer.innerHTML = applied.map(j => _buildJobCard(j, _applyJobs.indexOf(j), 'applied')).join('');\n"
    "    if (appliedCount) appliedCount.textContent = `(${applied.length})`;\n"
    "  } else {\n"
    "    appliedSection.style.display = 'none';\n"
    "  }\n"
    "\n"
    "  // Expired section\n"
    "  const expiredSection = document.getElementById('section-expired');\n"
    "  const expiredContainer = document.getElementById('cards-expired');\n"
    "  const expiredCount = document.getElementById('count-expired');\n"
    "  if (expired && expired.length) {\n"
    "    expiredSection.style.display = '';\n"
    "    expiredContainer.innerHTML = expired.map(j => _buildJobCard(j, _applyJobs.indexOf(j), 'expired')).join('');\n"
    "    if (expiredCount) expiredCount.textContent = `(${expired.length})`;\n"
    "  } else if (expiredSection) {\n"
    "    expiredSection.style.display = 'none';\n"
    "  }"
)
if OLD_APPLIED_RENDER in content:
    content = content.replace(OLD_APPLIED_RENDER, NEW_APPLIED_RENDER, 1)
    changed = True
    print("✅ JS: Added expired section rendering")
else:
    print("⚠️  Applied render block not found")

# 2e. Add expired to _buildJobCard action buttons
OLD_ACTIONS = ("  const actionBtns = section === 'manual'\n"
               "    ? `<div class=\"ajc-actions\">\n"
               "         <button class=\"ajc-btn ajc-btn-applied\" onclick=\"markJobApplied(${idx})\">&#10003; Applied</button>\n"
               "         <button class=\"ajc-btn ajc-btn-retry\"   onclick=\"retryJob(${idx})\">&#8617; Retry</button>\n"
               "         <button class=\"ajc-btn\" style=\"background:#2d1515;color:#f87171;\" onclick=\"removeFromManual(${idx})\">&#10005; Remove</button>\n"
               "         ${openBtn}\n"
               "       </div>`\n"
               "    : section === 'pending'\n"
               "    ? `<div class=\"ajc-actions\">${openBtn}</div>`\n"
               "    : `<div class=\"ajc-actions\">${openBtn}</div>`;")
NEW_ACTIONS = ("  const actionBtns = section === 'manual'\n"
               "    ? `<div class=\"ajc-actions\">\n"
               "         <button class=\"ajc-btn ajc-btn-applied\" onclick=\"markJobApplied(${idx})\">&#10003; Applied</button>\n"
               "         <button class=\"ajc-btn ajc-btn-retry\"   onclick=\"retryJob(${idx})\">&#8617; Retry</button>\n"
               "         <button class=\"ajc-btn\" style=\"background:#2d1515;color:#f87171;\" onclick=\"removeFromManual(${idx})\">&#10005; Remove</button>\n"
               "         ${openBtn}\n"
               "       </div>`\n"
               "    : section === 'expired'\n"
               "    ? `<div class=\"ajc-actions\">\n"
               "         <button class=\"ajc-btn ajc-btn-exclude\" onclick=\"excludeExpiredJob(${idx})\">&#128683; Not Interested</button>\n"
               "         ${openBtn}\n"
               "       </div>`\n"
               "    : section === 'pending'\n"
               "    ? `<div class=\"ajc-actions\">${openBtn}</div>`\n"
               "    : `<div class=\"ajc-actions\">${openBtn}</div>`;")
if OLD_ACTIONS in content:
    content = content.replace(OLD_ACTIONS, NEW_ACTIONS, 1)
    changed = True
    print("✅ JS: Added Not Interested button for expired cards")
else:
    print("⚠️  Action buttons block not found")

# 2f. Add excludeExpiredJob() function + SSE handler for job_expired
# Find removeFromManual and add after it
OLD_REMOVE = ("function removeFromManual(idx) {\n"
              "  const j = _applyJobs[idx];\n"
              "  if (!j) return;\n"
              "  _applyJobs.splice(idx, 1);\n"
              "  _applyStats.manual = Math.max(0, (_applyStats.manual||0) - 1);\n"
              "  document.getElementById('asb-manual').textContent = _applyStats.manual;\n"
              "  renderApplyCards();\n"
              "  showToast('Removed from manual queue', 'info');\n"
              "}")
NEW_REMOVE = ("function removeFromManual(idx) {\n"
              "  const j = _applyJobs[idx];\n"
              "  if (!j) return;\n"
              "  _applyJobs.splice(idx, 1);\n"
              "  _applyStats.manual = Math.max(0, (_applyStats.manual||0) - 1);\n"
              "  document.getElementById('asb-manual').textContent = _applyStats.manual;\n"
              "  renderApplyCards();\n"
              "  showToast('Removed from manual queue', 'info');\n"
              "}\n"
              "\n"
              "async function excludeExpiredJob(idx) {\n"
              "  const j = _applyJobs[idx];\n"
              "  if (!j) return;\n"
              "  try {\n"
              "    await fetch('/api/applications/manual', {\n"
              "      method: 'POST',\n"
              "      headers: {'Content-Type':'application/json'},\n"
              "      body: JSON.stringify({\n"
              "        job_url: j.url, title: j.title, company: j.company,\n"
              "        platform: _detectPlatformJS(j.url||''), action: 'exclude',\n"
              "      }),\n"
              "    });\n"
              "  } catch(_) {}\n"
              "  _applyJobs.splice(idx, 1);\n"
              "  renderApplyCards();\n"
              "  showToast('Job excluded — will never appear again', 'info');\n"
              "}")
if "function removeFromManual(idx)" in content:
    content = content.replace(OLD_REMOVE, NEW_REMOVE, 1)
    changed = True
    print("✅ JS: Added excludeExpiredJob() function")
else:
    print("⚠️  removeFromManual not found — add excludeExpiredJob() manually")

# 2g. Handle job_expired SSE event in handleApplyEvent
OLD_SESSION_DONE = "      } else if (evt.type === 'session_done') {"
NEW_SESSION_DONE = ("      } else if (evt.type === 'job_expired') {\n"
                    "        const i = _applyJobs.findIndex(j => j.url === evt.url);\n"
                    "        if (i >= 0) {\n"
                    "          _applyJobs[i]._applyStatus = 'expired';\n"
                    "          _applyJobs[i]._applyNote   = evt.reason || 'No longer accepting applications';\n"
                    "          renderApplyCards();\n"
                    "        }\n"
                    "      } else if (evt.type === 'session_done') {")
if OLD_SESSION_DONE in content:
    content = content.replace(OLD_SESSION_DONE, NEW_SESSION_DONE, 1)
    changed = True
    print("✅ JS: Added job_expired SSE event handler")
else:
    print("⚠️  session_done handler not found for expired event")

if changed:
    dash.write_text(content, encoding="utf-8")

# Syntax checks
r1 = subprocess.run(["python", "-m", "py_compile", "applier/applier.py"], capture_output=True, text=True)
r2 = subprocess.run(["python", "-m", "py_compile", "dashboard/dashboard.py"], capture_output=True, text=True)
print("✅ applier.py syntax OK" if r1.returncode == 0 else f"❌ applier.py: {r1.stderr}")
print("✅ dashboard.py syntax OK" if r2.returncode == 0 else f"❌ dashboard.py: {r2.stderr}")
