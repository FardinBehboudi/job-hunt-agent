"""
Implements Smart Job Application State Management:
- 3-section Step 5 UI: Jobs to Apply / Manual Queue / Successfully Applied
- Jobs move between sections as apply progresses
- Action buttons: ✓ Applied, ↩ Retry, 🔗 Open
- Color-coded status with smooth transitions

Run from project root:
    python implement_state_management.py
"""
from pathlib import Path
import re

dash = Path("dashboard/dashboard.py")
content = dash.read_text(encoding="utf-8")

# ── 1. CSS: Add section styles ─────────────────────────────────────────────────
CSS_ANCHOR = ".apply-cards-wrap { max-height:640px; overflow-y:auto; }"
NEW_CSS = """.apply-cards-wrap { overflow-y:auto; }
.apply-section-block { margin-bottom:18px; }
.apply-section-header {
  display:flex; align-items:center; gap:8px;
  font-size:0.78rem; font-weight:700; text-transform:uppercase;
  letter-spacing:0.06em; color:#94a3b8; padding:6px 0 8px 0;
  border-bottom:1px solid #1e293b; margin-bottom:8px;
}
.apply-section-header .section-count { font-weight:400; color:#64748b; }
.section-dot { font-size:0.7rem; }
.section-dot.dot-pending  { color:#f59e0b; }
.section-dot.dot-manual   { color:#f97316; }
.section-dot.dot-applied  { color:#22c55e; }
.ajc-actions { display:flex; gap:6px; margin-top:6px; flex-wrap:wrap; }
.ajc-btn {
  font-size:0.72rem; padding:3px 10px; border-radius:5px; border:none;
  cursor:pointer; font-weight:600; transition:opacity .15s;
}
.ajc-btn:hover { opacity:0.8; }
.ajc-btn-applied  { background:#14532d; color:#4ade80; }
.ajc-btn-retry    { background:#1e3a5f; color:#60a5fa; }
.ajc-btn-open     { background:#1e293b; color:#94a3b8; text-decoration:none; display:inline-block; }
.apply-job-card { transition: opacity .3s, transform .3s; }
.apply-job-card.status-running { border-left:3px solid #3b82f6; }
.apply-job-card.status-done    { border-left:3px solid #22c55e; opacity:.85; }
.apply-job-card.status-manual  { border-left:3px solid #f97316; }
.apply-job-card.status-failed  { border-left:3px solid #ef4444; opacity:.75; }"""

if CSS_ANCHOR in content:
    content = content.replace(CSS_ANCHOR, NEW_CSS, 1)
    print("✅ CSS: Added section styles")
else:
    print("⚠️  CSS anchor not found — styles may already be updated")

# ── 2. HTML: Replace apply-cards-wrap inner content ───────────────────────────
OLD_HTML = '''<div id="apply-cards-wrap" class="apply-cards-wrap">
              <div class="apply-empty" id="apply-cards-empty">No jobs queued. Go to <strong>Step 4 — Select</strong> and click Apply / Apply All.</div>'''

NEW_HTML = '''<div id="apply-cards-wrap" class="apply-cards-wrap">
              <!-- Section: Jobs to Apply -->
              <div class="apply-section-block" id="section-pending">
                <div class="apply-section-header">
                  <span class="section-dot dot-pending">&#9679;</span>
                  Jobs to Apply <span id="count-pending" class="section-count"></span>
                </div>
                <div id="cards-pending">
                  <div class="apply-empty" id="apply-cards-empty">No jobs queued. Go to <strong>Step 4 — Select</strong> and click Apply / Apply All.</div>
                </div>
              </div>
              <!-- Section: Manual Queue -->
              <div class="apply-section-block" id="section-manual" style="display:none">
                <div class="apply-section-header">
                  <span class="section-dot dot-manual">&#9679;</span>
                  Manual Apply Queue <span id="count-manual" class="section-count"></span>
                </div>
                <div id="cards-manual"></div>
              </div>
              <!-- Section: Successfully Applied -->
              <div class="apply-section-block" id="section-applied" style="display:none">
                <div class="apply-section-header">
                  <span class="section-dot dot-applied">&#9679;</span>
                  Successfully Applied <span id="count-applied" class="section-count"></span>
                </div>
                <div id="cards-applied"></div>
              </div>'''

if OLD_HTML in content:
    content = content.replace(OLD_HTML, NEW_HTML, 1)
    print("✅ HTML: Added 3-section structure to Step 5")
else:
    print("⚠️  HTML anchor not found — structure may already be updated")

# ── 3. JS: Replace renderApplyCards ───────────────────────────────────────────
OLD_RENDER = '''function renderApplyCards() {
  const wrap  = document.getElementById('apply-cards-wrap');
  const empty = document.getElementById('apply-cards-empty');
  const count = document.getElementById('apply-cards-count');
  if (!_applyJobs.length) {
    empty.style.display = '';
    count.textContent = '';
    return;
  }
  empty.style.display = 'none';
  count.textContent = _applyJobs.length + ' job' + (_applyJobs.length !== 1 ? 's' : '');
  wrap.innerHTML = _applyJobs.map((j, idx) => {
    const platform = _detectPlatformJS(j.url || '');
    const statusCls = j._applyStatus ? 'status-' + j._applyStatus : '';
    const easyApplyBadge = j.has_easy_apply
      ? `<span class="badge-easy-apply" style="background:#10b981;color:#fff;padding:2px 6px;border-radius:4px;font-size:0.7rem;font-weight:600;margin-left:6px;display:inline-block;">⚡ Easy Apply</span>`
      : '';
    const icon = j._applyStatus === 'done'   ? '&#10003;' :
                 j._applyStatus === 'failed' ? '&#10007;' :
                 j._applyStatus === 'manual' ? '&#9888;'  :
                 j._applyStatus === 'running'? '&#9654;'  : '&#8226;';
    return `<div class="apply-job-card ${statusCls}" id="ajc-${idx}">
      <span class="status-icon">${icon}</span>
      <div class="ajc-info">
        <div class="ajc-title">${esc(j.title||'')}${easyApplyBadge}</div>
        <div class="ajc-company">${esc(j.company||'')} ${j.location ? '· '+esc(j.location) : ''}</div>
        ${j._applyNote ? `<div class="ajc-note">${esc(j._applyNote)}</div>` : ''}
      </div>
      <div class="ajc-badges">
        <span class="platform-badge ${_platformBadgeClass(platform)}">${platform}</span>
        ${j.url ? `<a href="${esc(j.url)}" target="_blank" class="btn-sm" style="text-decoration:none;font-size:0.72rem;">Link</a>` : ''}
      </div>
    </div>`;
  }).join('');
}'''

NEW_RENDER = r'''function _buildJobCard(j, idx, section) {
  const platform = _detectPlatformJS(j.url || '');
  const statusCls = j._applyStatus ? 'status-' + j._applyStatus : '';
  const easyApplyBadge = j.has_easy_apply
    ? `<span class="badge-easy-apply" style="background:#10b981;color:#fff;padding:2px 6px;border-radius:4px;font-size:0.7rem;font-weight:600;margin-left:6px;display:inline-block;">&#9889; Easy Apply</span>`
    : '';
  const icon = j._applyStatus === 'done'    ? '&#10003;' :
               j._applyStatus === 'failed'  ? '&#10007;' :
               j._applyStatus === 'manual'  ? '&#9888;'  :
               j._applyStatus === 'running' ? '&#9654;'  : '&#8226;';
  const openBtn = j.url
    ? `<a href="${esc(j.url)}" target="_blank" class="ajc-btn ajc-btn-open">&#128279; Open</a>`
    : '';
  const actionBtns = section === 'manual'
    ? `<div class="ajc-actions">
         <button class="ajc-btn ajc-btn-applied" onclick="markJobApplied(${idx})">&#10003; Applied</button>
         <button class="ajc-btn ajc-btn-retry"   onclick="retryJob(${idx})">&#8617; Retry</button>
         ${openBtn}
       </div>`
    : section === 'pending'
    ? `<div class="ajc-actions">${openBtn}</div>`
    : `<div class="ajc-actions">${openBtn}</div>`;
  return `<div class="apply-job-card ${statusCls}" id="ajc-${idx}">
    <span class="status-icon">${icon}</span>
    <div class="ajc-info">
      <div class="ajc-title">${esc(j.title||'')}${easyApplyBadge}</div>
      <div class="ajc-company">${esc(j.company||'')}${j.location ? ' &middot; '+esc(j.location) : ''}</div>
      ${j._applyNote ? `<div class="ajc-note" style="color:#94a3b8;font-size:0.75rem;margin-top:2px;">${esc(j._applyNote)}</div>` : ''}
      ${actionBtns}
    </div>
    <div class="ajc-badges">
      <span class="platform-badge ${_platformBadgeClass(platform)}">${platform}</span>
    </div>
  </div>`;
}

function renderApplyCards() {
  const empty  = document.getElementById('apply-cards-empty');
  const countEl = document.getElementById('apply-cards-count');

  if (!_applyJobs.length) {
    if (empty) empty.style.display = '';
    if (countEl) countEl.textContent = '';
    document.getElementById('section-manual').style.display  = 'none';
    document.getElementById('section-applied').style.display = 'none';
    return;
  }
  if (empty) empty.style.display = 'none';

  const pending = _applyJobs.filter((j,i) =>
    !j._applyStatus || j._applyStatus === 'pending' || j._applyStatus === 'running');
  const manual  = _applyJobs.filter((j,i) => j._applyStatus === 'manual');
  const applied = _applyJobs.filter((j,i) =>
    j._applyStatus === 'done' || j._applyStatus === 'failed');

  // Pending section
  const pendingContainer = document.getElementById('cards-pending');
  const pendingCount = document.getElementById('count-pending');
  pendingContainer.innerHTML = pending.length
    ? pending.map(j => _buildJobCard(j, _applyJobs.indexOf(j), 'pending')).join('')
    : '<div style="color:#475569;font-size:0.8rem;padding:8px 0;">All jobs processed &#10003;</div>';
  if (pendingCount) pendingCount.textContent = pending.length ? `(${pending.length})` : '';

  // Manual section
  const manualSection = document.getElementById('section-manual');
  const manualContainer = document.getElementById('cards-manual');
  const manualCount = document.getElementById('count-manual');
  if (manual.length) {
    manualSection.style.display = '';
    manualContainer.innerHTML = manual.map(j => _buildJobCard(j, _applyJobs.indexOf(j), 'manual')).join('');
    if (manualCount) manualCount.textContent = `(${manual.length})`;
  } else {
    manualSection.style.display = 'none';
  }

  // Applied section
  const appliedSection = document.getElementById('section-applied');
  const appliedContainer = document.getElementById('cards-applied');
  const appliedCount = document.getElementById('count-applied');
  if (applied.length) {
    appliedSection.style.display = '';
    appliedContainer.innerHTML = applied.map(j => _buildJobCard(j, _applyJobs.indexOf(j), 'applied')).join('');
    if (appliedCount) appliedCount.textContent = `(${applied.length})`;
  } else {
    appliedSection.style.display = 'none';
  }

  if (countEl) {
    const total = _applyJobs.length;
    countEl.textContent = `${total} job${total !== 1 ? 's' : ''}`;
  }
}

async function markJobApplied(idx) {
  const j = _applyJobs[idx];
  if (!j) return;
  try {
    await fetch('/api/applications/manual', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        job_url: j.url, title: j.title,
        company: j.company, platform: _detectPlatformJS(j.url||''),
        action: 'applied',
      }),
    });
  } catch(_) {}
  j._applyStatus = 'done';
  j._applyNote = 'Marked as applied manually';
  _applyStats.manual = Math.max(0, (_applyStats.manual||0) - 1);
  _applyStats.success = (_applyStats.success||0) + 1;
  document.getElementById('asb-success').textContent = _applyStats.success;
  document.getElementById('asb-manual').textContent  = _applyStats.manual;
  renderApplyCards();
  showToast('&#10003; Marked as Applied', 'success');
}

async function retryJob(idx) {
  const j = _applyJobs[idx];
  if (!j) return;
  j._applyStatus = 'pending';
  j._applyNote   = '';
  renderApplyCards();
  showToast('Job returned to queue — run Apply again to retry', 'info');
}'''

if OLD_RENDER in content:
    content = content.replace(OLD_RENDER, NEW_RENDER, 1)
    print("✅ JS: Replaced renderApplyCards with 3-section version")
    print("✅ JS: Added markJobApplied() and retryJob() actions")
else:
    print("⚠️  renderApplyCards function not found exactly — check manually")

dash.write_text(content, encoding="utf-8")

# Syntax check
import subprocess
r = subprocess.run(["python", "-m", "py_compile", "dashboard/dashboard.py"],
                   capture_output=True, text=True)
if r.returncode == 0:
    print("✅ Syntax OK")
else:
    print("❌ Syntax error:", r.stderr)
