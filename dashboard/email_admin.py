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
                 "Code Challenge", "Offer", "Uncertain", "Delete"]

# ── API routes ─────────────────────────────────────────────────────────────────

@email_admin_bp.route("/api/pending")
def api_pending():
    rows = db.get_pending_emails()
    return jsonify(rows)


@email_admin_bp.route("/api/logs")
def api_logs():
    limit = int(request.args.get("limit", 1000))
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


@email_admin_bp.route("/api/skipped", methods=["GET"])
def api_skipped():
    return jsonify(db.get_skipped_emails())


@email_admin_bp.route("/api/requeue/<int:email_id>", methods=["POST"])
def api_requeue(email_id: int):
    db.requeue_email(email_id)
    return jsonify({"ok": True})


@email_admin_bp.route("/api/override/<int:email_id>", methods=["POST"])
def api_override(email_id: int):
    """Save the user's folder selection. Does NOT move — move happens on Approve."""
    data = request.get_json(force=True) or {}
    folder = data.get("folder", "")
    if folder not in VALID_FOLDERS:
        return jsonify({"ok": False, "error": "Invalid folder"}), 400
    db.set_email_override_folder(email_id, folder)
    return jsonify({"ok": True})


@email_admin_bp.route("/api/restore/<int:staging_id>", methods=["POST"])
def api_restore(staging_id: int):
    data = request.get_json(force=True) or {}
    folder = data.get("folder", "Inbox")
    record = db.get_staged_email(staging_id)
    if not record:
        return jsonify({"ok": False, "error": "Staging record not found"}), 404
    graph_id = record.get("email_uid")
    if not graph_id:
        return jsonify({"ok": False, "error": "No Graph message ID stored"}), 400
    try:
        from tracking import ms_graph
        import requests as _req
        try:
            new_id = ms_graph.move_message(graph_id, folder)
        except _req.HTTPError as first_exc:
            if first_exc.response is None or first_exc.response.status_code != 404:
                raise
            # Stored Graph ID is stale — find current ID via stable internetMessageId
            internet_id = record.get("email_message_id")
            if not internet_id:
                raise ValueError("Message not found and no internet message ID to search by") from first_exc
            current_id = ms_graph.find_message_id_by_internet_id(internet_id)
            if not current_id:
                raise ValueError("Could not locate message — it may have been permanently deleted") from first_exc
            new_id = ms_graph.move_message(current_id, folder)
        # Store the new Graph ID assigned after the move
        db.update_email_uid(staging_id, new_id)
        # Undo: remove the log entry and reset status to pending
        db.delete_email_move_log(staging_id)
        db.update_email_staging_status(staging_id, "pending")
        return jsonify({"ok": True})
    except Exception as exc:
        log.error("Restore failed id=%d: %s", staging_id, exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


@email_admin_bp.route("/api/backup", methods=["POST"])
def api_backup():
    try:
        from dedup.db import backup_db
        dest = backup_db()
        return jsonify({"ok": True, "file": dest.name})
    except Exception as exc:
        log.error("Manual backup failed: %s", exc)
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


@email_admin_bp.route("/api/events", methods=["GET"])
def api_events_list():
    return jsonify(db.get_upcoming_events())


@email_admin_bp.route("/api/events", methods=["POST"])
def api_events_create():
    data = request.get_json(force=True) or {}
    event_id = db.insert_upcoming_event(data)
    return jsonify({"ok": True, "id": event_id})


@email_admin_bp.route("/api/events/<int:event_id>", methods=["PUT"])
def api_events_update(event_id: int):
    data = request.get_json(force=True) or {}
    db.update_upcoming_event(event_id, data)
    return jsonify({"ok": True})


@email_admin_bp.route("/api/failed-count")
def api_failed_count():
    """Return count of emails stuck in 'failed' status."""
    return jsonify({"count": db.get_failed_email_count()})


@email_admin_bp.route("/api/run-now", methods=["POST"])
def api_run_now():
    if not _processor_lock.acquire(blocking=False):
        return jsonify({"ok": False, "error": "Already running"}), 409

    failed_count = db.get_failed_email_count()

    def _run():
        try:
            from tracking.email_processor import run
            run()
        except Exception as exc:
            log.error("email_processor.run() error: %s", exc)
        finally:
            _processor_lock.release()

    threading.Thread(target=_run, daemon=True).start()
    msg = "Email check started"
    if failed_count:
        msg += f" — {failed_count} previously failed email(s) will be retried"
    return jsonify({"ok": True, "message": msg, "failed_count": failed_count})


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
.ea-table-wrap { background:#161b22; border:1px solid #21262d; border-radius:10px; overflow-x:auto; }
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
/* Toast */
#ea-toast { position:fixed; bottom:24px; right:24px; z-index:9999;
  background:#1e293b; border:1px solid #334155; color:#e2e8f0;
  padding:10px 18px; border-radius:8px; font-size:0.84rem;
  opacity:0; transform:translateY(6px); transition:all 0.2s; pointer-events:none; }
#ea-toast.show { opacity:1; transform:translateY(0); }
#ea-toast.ea-toast-error { border-color:#f87171; color:#fca5a5; }
/* Confirm dialog */
#ea-confirm-overlay { display:none; position:fixed; inset:0; background:rgba(0,0,0,0.65);
  z-index:2000; align-items:center; justify-content:center; }
#ea-confirm-overlay.open { display:flex; }
#ea-confirm-box { background:#161b22; border:1px solid #30363d; border-radius:12px;
  padding:24px 28px; max-width:380px; width:90%; box-shadow:0 8px 32px rgba(0,0,0,0.5); }
#ea-confirm-box p { margin:0 0 20px; font-size:0.88rem; color:#c9d1d9; line-height:1.5; }
#ea-confirm-box .ea-confirm-btns { display:flex; gap:10px; justify-content:flex-end; }
</style>

<div id="ea-toast"></div>

<!-- Confirm dialog -->
<div id="ea-confirm-overlay">
  <div id="ea-confirm-box">
    <p id="ea-confirm-msg"></p>
    <div class="ea-confirm-btns">
      <button class="ea-btn" id="ea-confirm-no">Cancel</button>
      <button class="ea-btn primary" id="ea-confirm-yes">Confirm</button>
    </div>
  </div>
</div>

<div class="ea-subnav">
  <button class="ea-sub-btn active" onclick="eaShowTab('pending',this)">
    Pending Approvals<span class="ea-badge" id="ea-pending-count">0</span>
  </button>
  <button class="ea-sub-btn" onclick="eaShowTab('skipped',this)">
    Skipped<span class="ea-badge" id="ea-skipped-count">0</span>
  </button>
  <button class="ea-sub-btn" onclick="eaShowTab('logs',this)">Logs</button>
  <button class="ea-sub-btn" onclick="eaShowTab('settings',this)">Settings</button>
</div>

<!-- Pending -->
<div id="ea-pending" class="ea-section active">
  <div class="ea-bar">
    <button class="ea-btn primary" onclick="eaApproveAll()">Approve All ✓</button>
    <button class="ea-btn" onclick="eaSkipAll()">Skip All</button>
    <button class="ea-btn" id="ea-retry-btn" onclick="eaRetryFailed()" title="Re-scan Outlook, remove stale records, and retry any failed moves">🔄 Sync Outlook</button>
    <span id="ea-failed-badge" style="display:none;background:#7f1d1d;color:#fca5a5;border-radius:99px;font-size:0.7rem;padding:1px 7px;margin-left:4px"></span>
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

<!-- Skipped -->
<div id="ea-skipped" class="ea-section">
  <div class="ea-table-wrap" id="ea-skipped-wrap">
    <div class="ea-empty">Loading…</div>
  </div>
</div>

<!-- Logs -->
<div id="ea-logs" class="ea-section">
  <div class="ea-bar" style="margin-bottom:12px;gap:8px;flex-wrap:wrap;">
    <input id="ea-log-search" type="text" placeholder="Search sender or subject…"
      oninput="eaRenderLogs()"
      style="background:#0d1117;border:1px solid #30363d;color:#e2e8f0;border-radius:6px;
             padding:6px 10px;font-size:0.82rem;width:240px;outline:none;">
    <input id="ea-log-date-from" type="date" oninput="eaRenderLogs()"
      style="background:#0d1117;border:1px solid #30363d;color:#e2e8f0;border-radius:6px;
             padding:5px 8px;font-size:0.82rem;outline:none;">
    <span style="color:#64748b;font-size:0.8rem;align-self:center;">→</span>
    <input id="ea-log-date-to" type="date" oninput="eaRenderLogs()"
      style="background:#0d1117;border:1px solid #30363d;color:#e2e8f0;border-radius:6px;
             padding:5px 8px;font-size:0.82rem;outline:none;">
    <button class="ea-btn" onclick="eaClearLogFilters()" style="font-size:0.78rem;">✕ Clear</button>
    <span id="ea-log-count" style="color:#64748b;font-size:0.78rem;align-self:center;margin-left:4px;"></span>
  </div>
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
      <button class="ea-btn" onclick="eaBackupNow()" id="ea-backup-btn" title="Save a copy to uploads/backups/">💾 Backup DB Now</button>
    </div>
    <div id="ea-settings-msg" style="font-size:0.82rem;color:#34d399;display:none"></div>
  </div>
</div>

"""

_EMAIL_ADMIN_JS = """
const EA_FOLDERS = """ + json.dumps(VALID_FOLDERS) + """;
let _eaPendingData = [];
let _eaFilter = 'all';
let _eaLogsData = [];

function eaToast(msg, type) {
  const t = document.getElementById('ea-toast');
  t.textContent = msg;
  t.className = 'show' + (type === 'error' ? ' ea-toast-error' : '');
  clearTimeout(t._tid);
  t._tid = setTimeout(() => t.className = '', 3200);
}

function eaConfirm(msg) {
  return new Promise(resolve => {
    document.getElementById('ea-confirm-msg').textContent = msg;
    const ov  = document.getElementById('ea-confirm-overlay');
    const yes = document.getElementById('ea-confirm-yes');
    const no  = document.getElementById('ea-confirm-no');
    ov.classList.add('open');
    function cleanup(val) {
      ov.classList.remove('open');
      yes.removeEventListener('click', onYes);
      no.removeEventListener('click', onNo);
      resolve(val);
    }
    function onYes() { cleanup(true); }
    function onNo()  { cleanup(false); }
    yes.addEventListener('click', onYes);
    no.addEventListener('click', onNo);
  });
}

function eaFmtDate(s) {
  if (!s) return '—';
  try {
    const d = new Date(s);
    if (isNaN(d.getTime())) return s.slice(0,16);
    const mo = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][d.getMonth()];
    return mo+' '+d.getDate()+', '+String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0');
  } catch(e) { return s.slice(0,16); }
}
function eaModalFromData(el) {
  eaModalOpen(el.dataset.title||'', el.dataset.sub||'', el.dataset.body||'');
}
function eaDomainHint(sender) {
  if (!sender || !sender.includes('@')) return '';
  const d = sender.split('@')[1].split('.')[0];
  return d.charAt(0).toUpperCase() + d.slice(1);
}

function eaEsc(s) {
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function eaFolderClass(f) {
  const m = {Rejected:'ef-rejected','In Review':'ef-inreview','Next Step':'ef-nextstep',
    Interview:'ef-interview','Code Challenge':'ef-challenge',Offer:'ef-offer'};
  return m[f] || 'ef-uncertain';
}

function eaShortFolder(path) {
  if (!path) return '';
  if (path.toLowerCase() === 'deleteditems') return '\U0001f5d1 Deleted';
  return path.replace(/^Inbox\\/Applications\\//i, '').replace(/^Applications\\//i, '');
}

function eaFolderBadge(path) {
  if (!path) return '<span class="ea-folder ef-uncertain">—</span>';
  const label = eaShortFolder(path);
  const p = path.toLowerCase();
  let cls;
  if      (p === 'deleteditems')                            cls = 'ef-uncertain';
  else if (p.includes('rejected'))                          cls = 'ef-rejected';
  else if (p.includes('offer'))                             cls = 'ef-offer';
  else if (p.includes('interview'))                         cls = 'ef-interview';
  else if (p.includes('challang') || p.includes('challenge')) cls = 'ef-challenge';
  else if (p.includes('in review') || p.includes('in-review')) cls = 'ef-inreview';
  else if (p.includes('next step') || p.includes('nextstep'))   cls = 'ef-nextstep';
  else                                                      cls = 'ef-uncertain';
  return '<span class="ea-folder ' + cls + '">' + eaEsc(label) + '</span>';
}

function eaShowTab(id, btn) {
  document.querySelectorAll('.ea-section').forEach(s => s.classList.remove('active'));
  document.querySelectorAll('.ea-sub-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('ea-'+id).classList.add('active');
  btn.classList.add('active');
  if (id === 'pending')  eaLoadPending();
  if (id === 'logs')     eaLoadLogs();
  if (id === 'skipped')  eaLoadSkipped();
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
  eaCheckFailedCount();
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
      <td class="td-date">${eaFmtDate(r.received_date)}</td>
      <td class="td-trunc-sm">${eaEsc(r.sender)}</td>
      <td class="td-trunc ea-clickable"
          data-title="Email Subject" data-sub="${eaEsc(r.sender||'')}" data-body="${eaEsc(r.subject||'')}"
          onclick="eaModalFromData(this)">
        ${eaEsc(r.subject)}</td>
      <td class="td-nowrap">
        <span id="ea-folder-${r.id}" class="ea-folder ${eaFolderClass(r.predicted_folder)}">${eaEsc(r.predicted_folder)}</span>
        <span class="ea-conf">${r.confidence_score}%</span>
      </td>
      <td class="td-trunc ea-clickable" style="font-size:0.78rem;color:#8b949e"
          data-title="AI Classification" data-sub="${eaEsc((r.sender||'')+' — '+(r.subject||''))}" data-body="${eaEsc(r.classification_reason||'')}"
          onclick="eaModalFromData(this)">
        ${eaEsc(r.classification_reason)}</td>
      <td class="td-trunc-sm">${r.company
        ? eaEsc(r.company) + '<br><span style="color:#64748b;font-size:0.76rem">'+eaEsc(r.role||'')+'</span>'
        : r.match_type === 'ambiguous'
          ? '<span style="color:#c9d1d9;font-size:0.78rem">'+eaEsc(eaDomainHint(r.sender))+'</span><br><span class="ea-unmatched" style="font-size:0.7rem">multiple apps</span>'
          : '<span class="ea-unmatched">'+r.match_type+'</span>'}</td>
      <td id="ea-actions-${r.id}">
        <div class="ea-actions">
          <button class="ea-btn primary" onclick="eaApprove(${r.id})">✓</button>
          <button class="ea-btn danger" onclick="eaSkip(${r.id})">✗</button>
          <select onchange="if(this.value){eaOverride(${r.id},this.value,this)}">
            <option value="">Change…</option>
            ${EA_FOLDERS.filter(f=>f!=='Delete').map(f=>'<option value="'+f+'">'+f+'</option>').join('')}
            <option value="Delete" style="color:#f87171">🗑 Delete</option>
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
      eaToast('Error: ' + (d.error || 'unknown'), 'error');
      actionsCell.innerHTML = original;
      actionsCell.dataset.busy = '0';
    }
  } catch(e) {
    eaToast('Request failed: ' + e.message, 'error');
    actionsCell.innerHTML = original;
    actionsCell.dataset.busy = '0';
  }
}

async function eaSkip(id) {
  await fetch('/email-admin/api/skip/'+id, {method:'POST'});
  document.getElementById('ea-row-'+id)?.remove();
}

async function eaOverride(id, folder, selectEl) {
  // Save the selection — do NOT move yet, user must still click Approve
  try {
    const r = await fetch('/email-admin/api/override/'+id, {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({folder}),
    });
    const d = await r.json();
    if (d.ok) {
      // Update the folder badge to reflect the new choice
      const badge = document.getElementById('ea-folder-'+id);
      if (badge) {
        badge.className = 'ea-folder ' + eaFolderClass(folder);
        badge.textContent = folder;
      }
    } else {
      eaToast('Could not save selection: ' + (d.error || 'unknown error'), 'error');
      if (selectEl) selectEl.value = '';
    }
  } catch(e) {
    eaToast('Request failed: ' + e.message, 'error');
    if (selectEl) selectEl.value = '';
  }
}

async function eaApproveAll() {
  if (!_eaPendingData.length) {
    eaToast('No pending emails to approve', 'error');
    return;
  }
  if (!await eaConfirm('Approve and move all ' + _eaPendingData.length + ' pending emails?')) return;
  try {
    const r = await fetch('/email-admin/api/approve-batch', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({}),
    });
    if (!r.ok) { eaToast('Server error: ' + r.status, 'error'); return; }
    const d = await r.json();
    if (!d.ok) { eaToast('Failed: ' + (d.error || 'unknown'), 'error'); return; }
    eaToast('Done — ' + d.succeeded + ' moved, ' + d.failed + ' failed');
    eaLoadPending();
  } catch(e) {
    eaToast('Request failed: ' + e.message, 'error');
  }
}

async function eaSkipAll() {
  for (const r of _eaPendingData) await eaSkip(r.id);
}

async function eaLoadSkipped() {
  const r = await fetch('/email-admin/api/skipped');
  const data = await r.json();
  const badge = document.getElementById('ea-skipped-count');
  if (badge) badge.textContent = data.length;
  if (!data.length) {
    document.getElementById('ea-skipped-wrap').innerHTML =
      '<div class="ea-empty">No skipped emails</div>';
    return;
  }
  const rows = data.map(d => `
    <tr id="ea-skipped-row-${d.id}">
      <td class="td-date">${eaFmtDate(d.received_date)}</td>
      <td class="td-trunc-sm">${eaEsc(d.sender||'')}</td>
      <td class="td-trunc ea-clickable"
          data-title="Email Subject" data-sub="${eaEsc(d.sender||'')}" data-body="${eaEsc(d.subject||'')}"
          onclick="eaModalFromData(this)">
        ${eaEsc(d.subject||'')}</td>
      <td class="td-nowrap">
        <span class="ea-folder ${eaFolderClass(d.predicted_folder)}">${eaEsc(d.predicted_folder)}</span>
        <span class="ea-conf">${d.confidence_score}%</span>
      </td>
      <td class="td-trunc ea-clickable" style="font-size:0.78rem;color:#8b949e"
          data-title="AI Classification" data-sub="${eaEsc((d.sender||'')+' — '+(d.subject||''))}" data-body="${eaEsc(d.classification_reason||'')}"
          onclick="eaModalFromData(this)">
        ${eaEsc(d.classification_reason||'')}</td>
      <td id="ea-requeue-${d.id}">
        <button class="ea-btn" onclick="eaRequeue(${d.id})" style="font-size:0.78rem">↩ Re-queue</button>
      </td>
    </tr>`).join('');
  document.getElementById('ea-skipped-wrap').innerHTML = `
    <table class="ea-table">
      <thead><tr>
        <th>Date</th><th>From</th><th>Subject</th>
        <th>Folder</th><th>Reason</th><th>Action</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

async function eaRequeue(id) {
  const cell = document.getElementById('ea-requeue-'+id);
  if (!cell || cell.dataset.busy === '1') return;
  cell.dataset.busy = '1';
  cell.innerHTML = '<span style="color:#38bdf8;font-size:0.8rem">Re-queuing…</span>';
  try {
    const r = await fetch('/email-admin/api/requeue/'+id, {method:'POST'});
    const d = await r.json();
    if (d.ok) {
      cell.innerHTML = '<span style="color:#34d399;font-size:0.8rem">✓ Queued</span>';
      setTimeout(() => document.getElementById('ea-skipped-row-'+id)?.remove(), 700);
      const badge = document.getElementById('ea-skipped-count');
      if (badge) badge.textContent = Math.max(0, parseInt(badge.textContent||0) - 1);
    } else {
      eaToast('Re-queue failed: ' + (d.error||'unknown'), 'error');
      cell.innerHTML = '<button class="ea-btn" onclick="eaRequeue('+id+')" style="font-size:0.78rem">↩ Re-queue</button>';
      cell.dataset.busy = '0';
    }
  } catch(e) {
    eaToast('Request failed: ' + e.message, 'error');
    cell.innerHTML = '<button class="ea-btn" onclick="eaRequeue('+id+')" style="font-size:0.78rem">↩ Re-queue</button>';
    cell.dataset.busy = '0';
  }
}

async function eaRestoreFromData(btn) {
  if (btn.disabled) return;
  const sid = btn.dataset.sid;
  const folder = btn.dataset.folder || 'Inbox';
  btn.disabled = true;
  const orig = btn.textContent;
  btn.textContent = '↩…';
  try {
    const r = await fetch('/email-admin/api/restore/'+sid, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({folder}),
    });
    const d = await r.json();
    if (d.ok) {
      // Reload both logs (row removed) and pending count (email is back)
      await Promise.all([eaLoadLogs(), eaLoadPending()]);
    } else {
      eaToast('Restore failed: ' + (d.error||'unknown'), 'error');
      btn.disabled = false;
      btn.textContent = orig;
    }
  } catch(e) {
    eaToast('Request failed: ' + e.message, 'error');
    btn.disabled = false;
    btn.textContent = orig;
  }
}

async function eaLoadLogs() {
  const r = await fetch('/email-admin/api/logs?limit=1000');
  _eaLogsData = await r.json();
  eaRenderLogs();
}

function eaClearLogFilters() {
  document.getElementById('ea-log-search').value = '';
  document.getElementById('ea-log-date-from').value = '';
  document.getElementById('ea-log-date-to').value = '';
  eaRenderLogs();
}

function eaRenderLogs() {
  const q = (document.getElementById('ea-log-search')?.value || '').toLowerCase();
  const dateFrom = document.getElementById('ea-log-date-from')?.value || '';
  const dateTo   = document.getElementById('ea-log-date-to')?.value   || '';

  const filtered = _eaLogsData.filter(r => {
    if (q) {
      const hay = ((r.sender||'') + ' ' + (r.subject||'')).toLowerCase();
      if (!hay.includes(q)) return false;
    }
    if (dateFrom || dateTo) {
      const d = r.moved_at ? r.moved_at.slice(0,10) : '';
      if (dateFrom && d < dateFrom) return false;
      if (dateTo   && d > dateTo)   return false;
    }
    return true;
  });

  const countEl = document.getElementById('ea-log-count');
  if (countEl) countEl.textContent = filtered.length + ' of ' + _eaLogsData.length;

  if (!filtered.length) {
    document.getElementById('ea-logs-wrap').innerHTML =
      '<div class="ea-empty">' + (_eaLogsData.length ? 'No matching logs' : 'No move history yet') + '</div>';
    return;
  }
  const rows = filtered.map(r => {
    const isAuto = (r.move_source === 'auto');
    const sourceIcon = isAuto
      ? '<span title="Auto-filed by agent" style="margin-right:4px">🤖</span>'
      : '<span title="Manually approved" style="margin-right:4px">👤</span>';
    const restoreBtn = r.success && r.email_staging_id
      ? `<button class="ea-btn" style="font-size:0.72rem;padding:3px 8px"
           data-sid="${r.email_staging_id}" data-folder="${eaEsc(r.from_folder||'Inbox')}"
           onclick="eaRestoreFromData(this)" title="Restore to ${eaEsc(r.from_folder||'Inbox')}">↩ Restore</button>`
      : '';
    return `
    <tr>
      <td class="td-date">${eaFmtDate(r.moved_at)}</td>
      <td class="td-trunc-sm">${eaEsc(r.sender||'')}</td>
      <td class="td-trunc">${eaEsc(r.subject||'')}</td>
      <td class="td-nowrap">${eaFolderBadge(r.to_folder)}</td>
      <td class="td-nowrap">${r.success ? '<span style="color:#34d399">✓ OK</span>'
                      : '<span style="color:#f87171">✗ Failed</span>'}</td>
      <td class="td-nowrap">${sourceIcon}${isAuto ? 'Agent' : 'Manual'}</td>
      <td class="td-trunc" style="color:#f87171;font-size:0.75rem">${eaEsc(r.error_message||'')}</td>
      <td>${restoreBtn}</td>
    </tr>`; }).join('');
  document.getElementById('ea-logs-wrap').innerHTML = `
    <table class="ea-table">
      <thead><tr>
        <th>Date</th><th>From</th><th>Subject</th>
        <th>Moved To</th><th>Status</th><th>By</th><th>Error</th><th></th>
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
  try {
    const r = await fetch('/email-admin/api/run-now', {method:'POST'});
    const d = await r.json();
    if (!d.ok) { eaToast(d.error || 'Already running', 'error'); btn.textContent = '🔄 Check Emails Now'; btn.disabled = false; return; }
    eaToast(d.message || 'Email check started');
  } catch(e) { eaToast('Request failed: '+e.message, 'error'); }
  // Wait a moment then refresh pending list to show any re-queued failed emails
  setTimeout(() => {
    btn.textContent = '🔄 Check Emails Now';
    btn.disabled = false;
    eaLoadPending();
  }, 4000);
}

async function eaRetryFailed() {
  const btn = document.getElementById('ea-retry-btn');
  btn.disabled = true;
  btn.textContent = '↺ Scanning…';
  try {
    const r = await fetch('/email-admin/api/run-now', {method:'POST'});
    const d = await r.json();
    if (!d.ok) { eaToast(d.error || 'Could not start scan', 'error'); return; }
    eaToast(d.message || 'Scan started — failed emails will be re-queued');
    // Give the background thread a moment, then refresh
    setTimeout(async () => {
      await eaLoadPending();
      eaCheckFailedCount();
    }, 4000);
  } catch(e) { eaToast('Request failed: '+e.message, 'error'); }
  finally {
    setTimeout(() => { btn.disabled = false; btn.textContent = '🔄 Sync Outlook'; }, 4500);
  }
}

async function eaCheckFailedCount() {
  try {
    const r = await fetch('/email-admin/api/failed-count');
    const d = await r.json();
    const badge = document.getElementById('ea-failed-badge');
    const btn   = document.getElementById('ea-retry-btn');
    if (d.count > 0) {
      badge.textContent = d.count + ' failed';
      badge.style.display = 'inline';
      if (btn) btn.style.borderColor = '#991b1b';
    } else {
      badge.style.display = 'none';
      if (btn) btn.style.borderColor = '';
    }
  } catch(_) {}
}

async function eaBackupNow() {
  const btn = document.getElementById('ea-backup-btn');
  btn.disabled = true;
  btn.textContent = '⏳ Backing up…';
  try {
    const r = await fetch('/email-admin/api/backup', {method:'POST'});
    const d = await r.json();
    if (d.ok) eaToast('Backup saved: ' + d.file);
    else eaToast('Backup failed: ' + (d.error||'unknown'), 'error');
  } catch(e) { eaToast('Request failed: '+e.message, 'error'); }
  btn.disabled = false;
  btn.textContent = '💾 Backup DB Now';
}

// Load pending and skipped counts on init
eaLoadPending();
eaCheckFailedCount();
fetch('/email-admin/api/skipped').then(r=>r.json()).then(data => {
  const badge = document.getElementById('ea-skipped-count');
  if (badge) badge.textContent = data.length;
});
"""


@email_admin_bp.route("/")
def index():
    from flask import Response
    return Response(_EMAIL_ADMIN_HTML, content_type='text/html; charset=utf-8')


@email_admin_bp.route("/ea.js")
def ea_js():
    from flask import Response
    return Response(_EMAIL_ADMIN_JS, content_type='application/javascript; charset=utf-8')
