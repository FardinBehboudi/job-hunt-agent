"""
email_admin.py — Flask Blueprint for the Email Admin tab.

Registered in dashboard.py as:
    from dashboard.email_admin import email_admin_bp
    app.register_blueprint(email_admin_bp)
"""

import json
import logging
import threading
from datetime import datetime

from flask import Blueprint, jsonify, request

from dedup import db

log = logging.getLogger(__name__)

email_admin_bp = Blueprint("email_admin", __name__, url_prefix="/email-admin")

VALID_FOLDERS = ["Rejected", "In Review", "Next Step", "Interview",
                 "Code Challenge", "Offer", "Uncertain"]

# ── API routes ─────────────────────────────────────────────────────────────────

@email_admin_bp.route("/api/pending")
def api_pending():
    rows = db.get_pending_emails()
    return jsonify(rows)


@email_admin_bp.route("/api/logs")
def api_logs():
    limit = int(request.args.get("limit", 100))
    offset = int(request.args.get("offset", 0))
    rows = db.get_email_logs(limit=limit, offset=offset)
    return jsonify(rows)


@email_admin_bp.route("/api/approve/<int:email_id>", methods=["POST"])
def api_approve(email_id: int):
    # Idempotency: if already executed, return success without moving again
    record = db.get_staged_email(email_id)
    if record and record.get("status") == "executed":
        return jsonify({"ok": True})
    try:
        from tracking.email_executor import move
        move(email_id)
        db.update_email_staging_status(
            email_id, "executed",
            executed_at=datetime.utcnow().isoformat(),
            reviewed_at=datetime.utcnow().isoformat(),
        )
        return jsonify({"ok": True})
    except Exception as exc:
        log.error("Approve failed id=%d: %s", email_id, exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


@email_admin_bp.route("/api/approve-batch", methods=["POST"])
def api_approve_batch():
    data = request.get_json(force=True) or {}
    ids = data.get("ids")
    if ids is None:
        # Approve all pending
        ids = [r["id"] for r in db.get_pending_emails()]
    try:
        from tracking.email_executor import approve_batch
        result = approve_batch(ids)
        return jsonify({"ok": True, **result})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@email_admin_bp.route("/api/skip/<int:email_id>", methods=["POST"])
def api_skip(email_id: int):
    db.update_email_staging_status(
        email_id, "skipped",
        reviewed_at=datetime.utcnow().isoformat(),
    )
    return jsonify({"ok": True})


@email_admin_bp.route("/api/override/<int:email_id>", methods=["POST"])
def api_override(email_id: int):
    data = request.get_json(force=True) or {}
    folder = data.get("folder", "")
    if folder not in VALID_FOLDERS:
        return jsonify({"ok": False, "error": "Invalid folder"}), 400
    db.set_email_override_folder(email_id, folder)
    try:
        from tracking.email_executor import move
        move(email_id, override_folder=folder)
        db.update_email_staging_status(
            email_id, "executed",
            executed_at=datetime.utcnow().isoformat(),
            reviewed_at=datetime.utcnow().isoformat(),
        )
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@email_admin_bp.route("/api/settings", methods=["GET"])
def api_settings_get():
    return jsonify({
        "threshold": int(db.get_setting("email_auto_move_threshold", "90")),
        "auto_move_enabled": db.get_setting("email_auto_move_enabled", "1") == "1",
    })


@email_admin_bp.route("/api/settings", methods=["POST"])
def api_settings_post():
    data = request.get_json(force=True) or {}
    if "threshold" in data:
        db.set_setting("email_auto_move_threshold", str(int(data["threshold"])))
    if "auto_move_enabled" in data:
        db.set_setting("email_auto_move_enabled", "1" if data["auto_move_enabled"] else "0")
    return jsonify({"ok": True})


_processor_lock = threading.Lock()


@email_admin_bp.route("/api/run-now", methods=["POST"])
def api_run_now():
    if not _processor_lock.acquire(blocking=False):
        return jsonify({"ok": False, "error": "Already running"}), 409

    def _run():
        try:
            from tracking.email_processor import run
            run()
        except Exception as exc:
            log.error("email_processor.run() error: %s", exc)
        finally:
            _processor_lock.release()

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True, "message": "Email check started"})


# ── HTML tab ───────────────────────────────────────────────────────────────────

_EMAIL_ADMIN_HTML = """
<style>
.ea-subnav { display:flex; gap:8px; margin-bottom:20px; border-bottom:1px solid #21262d; }
.ea-sub-btn { background:none; border:none; border-bottom:2px solid transparent;
  color:#8b949e; padding:8px 16px; font-size:0.86rem; cursor:pointer;
  margin-bottom:-1px; transition:color 0.15s,border-color 0.15s; }
.ea-sub-btn:hover { color:#c9d1d9; }
.ea-sub-btn.active { color:#f0f6fc; border-bottom-color:#58a6ff; font-weight:600; }
.ea-section { display:none; }
.ea-section.active { display:block; }
.ea-bar { display:flex; align-items:center; gap:10px; margin-bottom:14px; flex-wrap:wrap; }
.ea-btn { background:#21262d; border:1px solid #30363d; color:#e2e8f0;
  border-radius:6px; padding:6px 14px; font-size:0.82rem; cursor:pointer;
  transition:background 0.15s; }
.ea-btn:hover { background:#30363d; }
.ea-btn.primary { background:#1d4ed8; border-color:#2563eb; color:#fff; }
.ea-btn.primary:hover { background:#2563eb; }
.ea-btn.danger { background:#7f1d1d; border-color:#991b1b; color:#fca5a5; }
.ea-chip { padding:4px 12px; border-radius:99px; font-size:0.75rem;
  cursor:pointer; border:1px solid #30363d; color:#8b949e; background:#161b22; }
.ea-chip.active { color:#38bdf8; border-color:#38bdf8; background:#0c1a2e; }
.ea-table-wrap { background:#161b22; border:1px solid #21262d; border-radius:10px; overflow:hidden; }
.ea-table { width:100%; border-collapse:collapse; font-size:0.82rem; }
.ea-table thead th { padding:10px 12px; text-align:left; font-size:0.68rem;
  text-transform:uppercase; letter-spacing:0.07em; color:#64748b;
  border-bottom:1px solid #21262d; white-space:nowrap; background:#161b22; }
.ea-table tbody tr { border-bottom:1px solid #0d1117; }
.ea-table tbody tr:hover { background:#1c2128; }
.ea-table td { padding:7px 10px; vertical-align:middle; }
.td-date { white-space:nowrap; color:#8b949e; font-size:0.78rem; }
.td-nowrap { white-space:nowrap; }
.td-trunc { max-width:220px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.td-trunc-sm { max-width:140px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.ea-folder { display:inline-block; padding:2px 8px; border-radius:99px;
  font-size:0.7rem; font-weight:700; text-transform:uppercase; }
.ef-rejected { background:#2d0f0f; color:#f87171; }
.ef-inreview { background:#0c2a4a; color:#60a5fa; }
.ef-nextstep { background:#1a1a2e; color:#818cf8; }
.ef-interview { background:#0d2818; color:#34d399; }
.ef-challenge { background:#2d1a06; color:#fb923c; }
.ef-offer { background:#2d2006; color:#fbbf24; }
.ef-uncertain { background:#1c2128; color:#64748b; }
.ea-conf { font-size:0.75rem; color:#64748b; margin-left:4px; }
.ea-unmatched { color:#64748b; font-style:italic; font-size:0.78rem; }
.ea-actions { display:flex; gap:6px; align-items:center; flex-wrap:wrap; }
.ea-actions select { background:#0d1117; border:1px solid #30363d; color:#e2e8f0;
  border-radius:5px; padding:4px 6px; font-size:0.78rem; }
.ea-empty { padding:32px; text-align:center; color:#64748b; }
.ea-settings-form { background:#161b22; border:1px solid #21262d; border-radius:10px;
  padding:24px; max-width:480px; display:flex; flex-direction:column; gap:18px; }
.ea-settings-form label { color:#c9d1d9; font-size:0.88rem; }
.ea-settings-form input[type=range] { width:100%; accent-color:#38bdf8; }
.ea-settings-form input[type=checkbox] { accent-color:#38bdf8; margin-right:8px; }
.ea-badge { display:inline-block; background:#1d2d44; color:#60a5fa;
  border-radius:99px; font-size:0.7rem; padding:1px 7px; margin-left:6px; }
.ea-clickable { cursor:pointer; border-bottom:1px dotted #444; }
.ea-clickable:hover { color:#e2e8f0; border-bottom-color:#8b949e; }
/* Detail modal */
#ea-modal-overlay { display:none; position:fixed; inset:0; background:rgba(0,0,0,0.65);
  z-index:1000; align-items:center; justify-content:center; }
#ea-modal-overlay.open { display:flex; }
#ea-modal-box { background:#161b22; border:1px solid #30363d; border-radius:12px;
  padding:24px 28px; max-width:520px; width:90%; box-shadow:0 8px 32px rgba(0,0,0,0.5); }
#ea-modal-box h3 { margin:0 0 6px; font-size:0.9rem; color:#38bdf8; }
#ea-modal-box .ea-modal-subject { font-size:0.85rem; color:#e2e8f0; margin-bottom:14px;
  padding-bottom:12px; border-bottom:1px solid #21262d; }
#ea-modal-box .ea-modal-body { font-size:0.85rem; color:#94a3b8; line-height:1.6; }
#ea-modal-close { float:right; background:none; border:none; color:#8b949e;
  font-size:1.2rem; cursor:pointer; padding:0; margin:-4px -4px 0 0; }
#ea-modal-close:hover { color:#e2e8f0; }
</style>

<div class="ea-subnav">
  <button class="ea-sub-btn active" onclick="eaShowTab('pending',this)">
    Pending Approvals<span class="ea-badge" id="ea-pending-count">0</span>
  </button>
  <button class="ea-sub-btn" onclick="eaShowTab('logs',this)">Logs</button>
  <button class="ea-sub-btn" onclick="eaShowTab('settings',this)">Settings</button>
</div>

<!-- Pending -->
<div id="ea-pending" class="ea-section active">
  <div class="ea-bar">
    <button class="ea-btn primary" onclick="eaApproveAll()">Approve All ✓</button>
    <button class="ea-btn" onclick="eaSkipAll()">Skip All</button>
    <button class="ea-btn" onclick="eaLoadPending()" title="Refresh pending list" style="margin-left:4px">🔄</button>
    <span style="flex:1"></span>
    <span class="ea-chip active" onclick="eaFilter('all',this)">All</span>
    <span class="ea-chip" onclick="eaFilter('high',this)">High Confidence</span>
    <span class="ea-chip" onclick="eaFilter('ambiguous',this)">Ambiguous</span>
    <span class="ea-chip" onclick="eaFilter('unmatched',this)">Unmatched</span>
  </div>
  <div class="ea-table-wrap" id="ea-pending-wrap">
    <div class="ea-empty">Loading…</div>
  </div>
</div>

<!-- Detail modal -->
<div id="ea-modal-overlay" onclick="if(event.target===this)eaModalClose()">
  <div id="ea-modal-box">
    <button id="ea-modal-close" onclick="eaModalClose()">✕</button>
    <h3 id="ea-modal-title"></h3>
    <div class="ea-modal-subject" id="ea-modal-subject"></div>
    <div class="ea-modal-body" id="ea-modal-body"></div>
  </div>
</div>

<!-- Logs -->
<div id="ea-logs" class="ea-section">
  <div class="ea-table-wrap" id="ea-logs-wrap">
    <div class="ea-empty">Loading…</div>
  </div>
</div>

<!-- Settings -->
<div id="ea-settings" class="ea-section">
  <div class="ea-settings-form">
    <div>
      <label>Auto-move confidence threshold: <strong id="ea-thresh-display">90</strong>%</label>
      <input type="range" min="50" max="100" step="1" value="90" id="ea-threshold"
             oninput="document.getElementById('ea-thresh-display').textContent=this.value">
    </div>
    <div>
      <label>
        <input type="checkbox" id="ea-auto-enabled" checked>
        Auto-move high-confidence rejections
      </label>
    </div>
    <div style="display:flex;gap:10px;flex-wrap:wrap;">
      <button class="ea-btn primary" onclick="eaSaveSettings()">Save Settings</button>
      <button class="ea-btn" onclick="eaRunNow()" id="ea-run-btn">🔄 Check Emails Now</button>
    </div>
    <div id="ea-settings-msg" style="font-size:0.82rem;color:#34d399;display:none"></div>
  </div>
</div>

<script>
const EA_FOLDERS = """ + json.dumps(VALID_FOLDERS) + """;
let _eaPendingData = [];
let _eaFilter = 'all';

function eaEsc(s) {
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function eaFolderClass(f) {
  const m = {Rejected:'ef-rejected','In Review':'ef-inreview','Next Step':'ef-nextstep',
    Interview:'ef-interview','Code Challenge':'ef-challenge',Offer:'ef-offer'};
  return m[f] || 'ef-uncertain';
}

function eaShowTab(id, btn) {
  document.querySelectorAll('.ea-section').forEach(s => s.classList.remove('active'));
  document.querySelectorAll('.ea-sub-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('ea-'+id).classList.add('active');
  btn.classList.add('active');
  if (id === 'logs') eaLoadLogs();
  if (id === 'settings') eaLoadSettings();
}

function eaFilter(type, chip) {
  _eaFilter = type;
  document.querySelectorAll('.ea-chip').forEach(c => c.classList.remove('active'));
  chip.classList.add('active');
  eaRenderPending();
}

async function eaLoadPending() {
  const r = await fetch('/email-admin/api/pending');
  _eaPendingData = await r.json();
  document.getElementById('ea-pending-count').textContent = _eaPendingData.length;
  eaRenderPending();
}

function eaRenderPending() {
  const data = _eaPendingData.filter(r => {
    if (_eaFilter === 'high') return r.confidence_score >= 70;
    if (_eaFilter === 'ambiguous') return r.match_type === 'ambiguous';
    if (_eaFilter === 'unmatched') return r.match_type === 'unmatched';
    return true;
  });
  if (!data.length) {
    document.getElementById('ea-pending-wrap').innerHTML =
      '<div class="ea-empty">No pending emails</div>';
    return;
  }
  const rows = data.map(r => `
    <tr id="ea-row-${r.id}">
      <td class="td-date">${eaEsc(r.received_date||'').slice(0,16)}</td>
      <td class="td-trunc-sm">${eaEsc(r.sender)}</td>
      <td class="td-trunc ea-clickable"
          onclick="eaModalOpen('Email Subject', ${JSON.stringify('')}, ${JSON.stringify(r.subject||'')})">
        ${eaEsc(r.subject)}</td>
      <td class="td-nowrap">
        <span class="ea-folder ${eaFolderClass(r.predicted_folder)}">${eaEsc(r.predicted_folder)}</span>
        <span class="ea-conf">${r.confidence_score}%</span>
      </td>
      <td class="td-trunc ea-clickable" style="font-size:0.78rem;color:#8b949e"
          onclick="eaModalOpen('AI Classification', ${JSON.stringify('From: '+(r.sender||'')+'\n'+r.subject)}, ${JSON.stringify(r.classification_reason||'')})">
        ${eaEsc(r.classification_reason)}</td>
      <td class="td-trunc-sm">${r.company
        ? eaEsc(r.company) + '<br><span style="color:#64748b;font-size:0.76rem">'+eaEsc(r.role||'')+'</span>'
        : '<span class="ea-unmatched">'+r.match_type+'</span>'}</td>
      <td id="ea-actions-${r.id}">
        <div class="ea-actions">
          <button class="ea-btn primary" onclick="eaApprove(${r.id})">✓</button>
          <button class="ea-btn danger" onclick="eaSkip(${r.id})">✗</button>
          <select onchange="if(this.value){eaOverride(${r.id},this.value,this)}">
            <option value="">Change…</option>
            ${EA_FOLDERS.map(f=>'<option value="'+f+'">'+f+'</option>').join('')}
          </select>
        </div>
      </td>
    </tr>
  `).join('');

  document.getElementById('ea-pending-wrap').innerHTML = `
    <table class="ea-table">
      <thead><tr>
        <th>Date</th><th>From</th><th>Subject</th>
        <th>Folder</th><th>Reason</th><th>Job</th><th>Actions</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

function eaModalOpen(title, subject, body) {
  document.getElementById('ea-modal-title').textContent = title;
  document.getElementById('ea-modal-subject').textContent = subject;
  document.getElementById('ea-modal-body').textContent = body;
  document.getElementById('ea-modal-overlay').classList.add('open');
}
function eaModalClose() {
  document.getElementById('ea-modal-overlay').classList.remove('open');
}
document.addEventListener('keydown', e => { if(e.key==='Escape') eaModalClose(); });

async function eaApprove(id) {
  const actionsCell = document.getElementById('ea-actions-'+id);
  if (!actionsCell || actionsCell.dataset.busy === '1') return;
  actionsCell.dataset.busy = '1';
  const original = actionsCell.innerHTML;
  actionsCell.innerHTML = '<span style="color:#38bdf8;font-size:0.8rem">Moving…</span>';
  try {
    const r = await fetch('/email-admin/api/approve/'+id, {method:'POST'});
    const d = await r.json();
    if (d.ok) {
      actionsCell.innerHTML = '<span style="color:#34d399;font-size:0.8rem">✓ Moved</span>';
      setTimeout(() => document.getElementById('ea-row-'+id)?.remove(), 600);
    } else {
      alert('Error: ' + (d.error || 'unknown'));
      actionsCell.innerHTML = original;
      actionsCell.dataset.busy = '0';
    }
  } catch(e) {
    alert('Request failed: ' + e.message);
    actionsCell.innerHTML = original;
    actionsCell.dataset.busy = '0';
  }
}

async function eaSkip(id) {
  await fetch('/email-admin/api/skip/'+id, {method:'POST'});
  document.getElementById('ea-row-'+id)?.remove();
}

async function eaOverride(id, folder, selectEl) {
  if (selectEl) { selectEl.disabled = true; }
  try {
    const r = await fetch('/email-admin/api/override/'+id, {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({folder}),
    });
    const d = await r.json();
    if (d.ok) {
      document.getElementById('ea-row-'+id)?.remove();
    } else {
      alert('Move failed: ' + (d.error || 'unknown error'));
      if (selectEl) { selectEl.value = ''; selectEl.disabled = false; }
    }
  } catch(e) {
    alert('Request failed: ' + e.message);
    if (selectEl) { selectEl.value = ''; selectEl.disabled = false; }
  }
}

async function eaApproveAll() {
  if (!_eaPendingData.length) return;
  if (!confirm('Approve and move all '+_eaPendingData.length+' pending emails?')) return;
  const r = await fetch('/email-admin/api/approve-batch', {method:'POST',
    headers:{'Content-Type':'application/json'}, body:JSON.stringify({})});
  const d = await r.json();
  alert('Done — '+d.succeeded+' moved, '+d.failed+' failed');
  eaLoadPending();
}

async function eaSkipAll() {
  for (const r of _eaPendingData) await eaSkip(r.id);
}

async function eaLoadLogs() {
  const r = await fetch('/email-admin/api/logs');
  const data = await r.json();
  if (!data.length) {
    document.getElementById('ea-logs-wrap').innerHTML =
      '<div class="ea-empty">No move history yet</div>';
    return;
  }
  function eaShortFolder(path) {
    // Strip "Inbox/Applications/" prefix for display
    return (path||'').replace(/^Inbox\/Applications\//i, '').replace(/^Applications\//i, '');
  }
  const rows = data.map(r => {
    const isAuto = (r.move_source === 'auto');
    const sourceIcon = isAuto
      ? '<span title="Auto-filed by agent" style="margin-right:4px">🤖</span>'
      : '<span title="Manually approved" style="margin-right:4px">👤</span>';
    return `
    <tr>
      <td class="td-date">${eaEsc((r.moved_at||'').slice(0,16))}</td>
      <td class="td-trunc-sm">${eaEsc(r.sender||'')}</td>
      <td class="td-trunc">${eaEsc(r.subject||'')}</td>
      <td class="td-nowrap">${eaEsc(eaShortFolder(r.to_folder))}</td>
      <td class="td-nowrap">${r.success ? '<span style="color:#34d399">✓ OK</span>'
                      : '<span style="color:#f87171">✗ Failed</span>'}</td>
      <td class="td-nowrap">${sourceIcon}${isAuto ? 'Agent' : 'Manual'}</td>
      <td class="td-trunc" style="color:#f87171;font-size:0.75rem">${eaEsc(r.error_message||'')}</td>
    </tr>`; }).join('');
  document.getElementById('ea-logs-wrap').innerHTML = `
    <table class="ea-table">
      <thead><tr>
        <th>Date</th><th>From</th><th>Subject</th>
        <th>Moved To</th><th>Status</th><th>By</th><th>Error</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

async function eaLoadSettings() {
  const r = await fetch('/email-admin/api/settings');
  const d = await r.json();
  document.getElementById('ea-threshold').value = d.threshold;
  document.getElementById('ea-thresh-display').textContent = d.threshold;
  document.getElementById('ea-auto-enabled').checked = d.auto_move_enabled;
}

async function eaSaveSettings() {
  const threshold = parseInt(document.getElementById('ea-threshold').value);
  const auto_move_enabled = document.getElementById('ea-auto-enabled').checked;
  await fetch('/email-admin/api/settings', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({threshold, auto_move_enabled}),
  });
  const msg = document.getElementById('ea-settings-msg');
  msg.textContent = 'Settings saved.';
  msg.style.display = 'block';
  setTimeout(() => msg.style.display='none', 2000);
}

async function eaRunNow() {
  const btn = document.getElementById('ea-run-btn');
  btn.textContent = '⏳ Running…';
  btn.disabled = true;
  const r = await fetch('/email-admin/api/run-now', {method:'POST'});
  const d = await r.json();
  setTimeout(() => {
    btn.textContent = '🔄 Check Emails Now';
    btn.disabled = false;
    eaLoadPending();
  }, 3000);
}

// Load pending on init
eaLoadPending();
</script>
"""


@email_admin_bp.route("/")
def index():
    from flask import render_template_string
    return render_template_string(_EMAIL_ADMIN_HTML)
