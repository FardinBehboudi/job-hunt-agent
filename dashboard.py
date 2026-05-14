"""
dashboard.py — local web dashboard for job application tracking.

Reads job_application_tracker_v34.xlsx (path from config.yaml / cv_root) and
displays all applications in a filterable, sortable, auto-refreshing web page.

Run:  python dashboard.py
Open: http://localhost:5000
"""

import os
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template_string

load_dotenv()

import sys
sys.path.insert(0, str(Path(__file__).parent))
from config import load_config

app = Flask(__name__)

# Canonical field → possible Excel column header spellings
_COL_ALIASES: dict[str, list[str]] = {
    "date":             ["Date", "Applied Date", "Application Date"],
    "company":          ["Company", "Employer"],
    "role":             ["Role", "Job Title", "Title", "Position"],
    "location":         ["Location", "City"],
    "match_score":      ["Match Score", "Match %", "Score"],
    "interview_chance": ["Interview Chance", "Chance"],
    "german_level":     ["German Level", "German", "German Required"],
    "status":           ["Status", "Application Status"],
    "job_url":          ["Job URL", "URL", "Link"],
    "archive_folder":   ["Archive Folder", "Archive", "Folder"],
    "notes":            ["Notes", "Comment"],
}


def _find_col(df: pd.DataFrame, canonical: str) -> str | None:
    for alias in _COL_ALIASES.get(canonical, [canonical]):
        if alias in df.columns:
            return alias
    lower_map = {c.lower(): c for c in df.columns}
    for alias in _COL_ALIASES.get(canonical, [canonical]):
        if alias.lower() in lower_map:
            return lower_map[alias.lower()]
    return None


def _normalise_score(raw: str) -> str:
    """Convert Excel percentage format (0.85 → '85') or float ('85.0' → '85')."""
    try:
        val = float(raw)
        if 0 < val <= 1.0:
            return str(round(val * 100))
        if val > 1:
            return str(round(val))
    except (ValueError, TypeError):
        pass
    return raw


def _load_jobs() -> tuple[list[dict], str]:
    """Return (jobs_list, error_str). error_str is empty on success."""
    try:
        cfg = load_config()
    except Exception as exc:
        return [], f"Config error: {exc}"

    tracker_path: Path = cfg["paths"]["tracker_file"]
    if not tracker_path.exists():
        return [], f"Tracker file not found: {tracker_path}"

    try:
        df = pd.read_excel(tracker_path)
    except Exception as exc:
        return [], f"Failed to read Excel: {exc}"

    df = df.where(pd.notna(df), other="")
    col_map = {canon: _find_col(df, canon) for canon in _COL_ALIASES}

    def get(row, canon: str) -> str:
        col = col_map.get(canon)
        if not col or col not in row.index:
            return ""
        val = row[col]
        if val == "" or (isinstance(val, float) and pd.isna(val)):
            return ""
        if hasattr(val, "strftime"):
            return val.strftime("%Y-%m-%d")
        return str(val).strip()

    jobs = []
    for _, row in df.iterrows():
        score_raw = get(row, "match_score")
        job = {
            "date":             get(row, "date"),
            "company":          get(row, "company"),
            "role":             get(row, "role"),
            "location":         get(row, "location"),
            "match_score":      _normalise_score(score_raw) if score_raw else "",
            "interview_chance": get(row, "interview_chance"),
            "german_level":     get(row, "german_level"),
            "status":           get(row, "status"),
            "job_url":          get(row, "job_url"),
            "archive_folder":   get(row, "archive_folder"),
            "notes":            get(row, "notes"),
        }
        if not any(job.values()):
            continue
        jobs.append(job)

    return jobs, ""


# ── HTML / CSS / JS ───────────────────────────────────────────────────────────

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Job Hunt Dashboard</title>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  background: #0d1117; color: #e2e8f0; min-height: 100vh;
}
a { color: inherit; }

/* ── Layout ── */
.container { max-width: 1500px; margin: 0 auto; padding: 28px 20px; }

header {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 28px; flex-wrap: wrap; gap: 12px;
}
header h1 { font-size: 1.5rem; font-weight: 700; color: #f8fafc; letter-spacing: -0.02em; }
.refresh-pill {
  font-size: 0.76rem; color: #64748b; background: #161b22;
  border: 1px solid #21262d; border-radius: 20px; padding: 5px 12px;
}
.refresh-pill span { color: #38bdf8; font-weight: 600; }

/* ── Summary cards ── */
.cards {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 12px; margin-bottom: 20px;
}
@media (max-width: 960px) { .cards { grid-template-columns: repeat(3, 1fr); } }
@media (max-width: 480px) { .cards { grid-template-columns: repeat(2, 1fr); } }
.card {
  background: #161b22; border: 1px solid #21262d; border-radius: 10px;
  padding: 16px 18px; cursor: default;
}
.card .n    { font-size: 2.2rem; font-weight: 700; line-height: 1; margin-bottom: 4px; }
.card .lbl  { font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.07em; color: #64748b; }
.c-total    .n { color: #f1f5f9; }
.c-applied  .n { color: #60a5fa; }
.c-interview .n { color: #34d399; }
.c-pending  .n { color: #fb923c; }
.c-offer    .n { color: #fbbf24; }
.c-rejected .n { color: #f87171; }

/* ── Filters ── */
.filters {
  background: #161b22; border: 1px solid #21262d; border-radius: 10px;
  padding: 14px 18px; margin-bottom: 18px;
  display: flex; flex-wrap: wrap; gap: 12px; align-items: flex-end;
}
.fg { display: flex; flex-direction: column; gap: 5px; }
.fg label { font-size: 0.69rem; text-transform: uppercase; letter-spacing: 0.07em; color: #64748b; }
.fg input, .fg select {
  background: #0d1117; border: 1px solid #30363d; color: #e2e8f0;
  border-radius: 6px; padding: 7px 10px; font-size: 0.84rem;
  min-width: 130px; outline: none; transition: border-color 0.15s;
}
.fg input:focus, .fg select:focus { border-color: #38bdf8; }
.fg.wide input { min-width: 210px; }
.btn-reset {
  background: #21262d; border: 1px solid #30363d; color: #8b949e;
  border-radius: 6px; padding: 7px 14px; font-size: 0.84rem;
  cursor: pointer; align-self: flex-end; transition: background 0.15s;
}
.btn-reset:hover { background: #30363d; color: #e2e8f0; }

/* ── Table wrapper ── */
.table-wrap {
  background: #161b22; border: 1px solid #21262d; border-radius: 10px;
  overflow: hidden;
}
.table-bar {
  padding: 10px 18px; font-size: 0.78rem; color: #64748b;
  border-bottom: 1px solid #21262d; display: flex;
  justify-content: space-between; align-items: center;
}
.table-bar .error { color: #f87171; }
.scroll-x { overflow-x: auto; }

table { width: 100%; border-collapse: collapse; font-size: 0.84rem; }
thead th {
  padding: 11px 14px; text-align: left; font-size: 0.69rem;
  text-transform: uppercase; letter-spacing: 0.07em; color: #64748b;
  border-bottom: 1px solid #21262d; white-space: nowrap;
  cursor: pointer; user-select: none;
  background: #161b22; position: sticky; top: 0; z-index: 1;
}
thead th:hover { color: #94a3b8; }
thead th .arr { margin-left: 4px; font-size: 0.68rem; opacity: 0.35; }
thead th.sorted .arr { opacity: 1; color: #38bdf8; }

tbody tr { border-bottom: 1px solid #0d1117; transition: background 0.1s; }
tbody tr:last-child { border-bottom: none; }
tbody tr:hover { background: #1c2128; }
td { padding: 10px 14px; vertical-align: middle; }

.td-date    { white-space: nowrap; color: #8b949e; font-size: 0.79rem; }
.td-company { font-weight: 600; color: #f1f5f9; max-width: 180px; }
.td-role a  { color: #58a6ff; text-decoration: none; }
.td-role a:hover { text-decoration: underline; }
.td-role span { color: #c9d1d9; }
.td-score   { font-weight: 700; }
.s-hi  { color: #34d399; }
.s-mid { color: #fb923c; }
.s-lo  { color: #f87171; }
.s-na  { color: #64748b; }

/* Chance chip */
.chip {
  display: inline-block; padding: 2px 8px; border-radius: 99px;
  font-size: 0.68rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em;
}
.ch-high   { background: #0d2818; color: #34d399; }
.ch-medium { background: #2d1a06; color: #fb923c; }
.ch-low    { background: #2d0f0f; color: #f87171; }
.ch-na     { background: #1c2128; color: #64748b; }

/* Status badges */
.badge {
  display: inline-block; padding: 3px 9px; border-radius: 99px;
  font-size: 0.7rem; font-weight: 600; white-space: nowrap;
}
.bs-applied     { background: #0c2a4a; color: #60a5fa; }
.bs-unconfirmed { background: #1c2128; color: #8b949e; }
.bs-scheduled   { background: #0d2818; color: #34d399; }
.bs-pending     { background: #2d1a06; color: #fb923c; }
.bs-rejected    { background: #2d0f0f; color: #f87171; }
.bs-offer       { background: #2d2006; color: #fbbf24; }
.bs-default     { background: #1c2128; color: #8b949e; }

.td-german      { font-size: 0.79rem; color: #8b949e; }
.german-warn    { color: #fb923c; }

.td-archive a {
  color: #a78bfa; font-size: 0.76rem; text-decoration: none; white-space: nowrap;
}
.td-archive a:hover { text-decoration: underline; }

/* Empty / error states */
.empty-state {
  text-align: center; padding: 64px 20px; color: #484f58;
}
.empty-state .icon { font-size: 2.8rem; margin-bottom: 10px; }
.hidden { display: none !important; }

/* Spinner */
#spinner-overlay {
  position: fixed; inset: 0; background: rgba(13,17,23,0.75);
  display: flex; align-items: center; justify-content: center; z-index: 50;
}
.spinner {
  width: 38px; height: 38px; border: 3px solid #21262d;
  border-top-color: #38bdf8; border-radius: 50%;
  animation: spin 0.75s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>

<div id="spinner-overlay"><div class="spinner"></div></div>

<div class="container">

  <header>
    <h1>&#128188; Job Hunt Dashboard</h1>
    <div class="refresh-pill">
      Auto-refreshes every 60s &nbsp;&middot;&nbsp; Last update: <span id="last-update">—</span>
    </div>
  </header>

  <!-- Summary cards -->
  <div class="cards">
    <div class="card c-total">    <div class="n" id="s-total">—</div>    <div class="lbl">Total</div></div>
    <div class="card c-applied">  <div class="n" id="s-applied">—</div>  <div class="lbl">Applied</div></div>
    <div class="card c-interview"><div class="n" id="s-interviews">—</div><div class="lbl">Interviews</div></div>
    <div class="card c-pending">  <div class="n" id="s-pending">—</div>  <div class="lbl">Pending</div></div>
    <div class="card c-offer">    <div class="n" id="s-offers">—</div>   <div class="lbl">Offers</div></div>
    <div class="card c-rejected"> <div class="n" id="s-rejected">—</div> <div class="lbl">Rejected</div></div>
  </div>

  <!-- Filters -->
  <div class="filters">
    <div class="fg wide">
      <label>Search</label>
      <input id="f-search" type="text" placeholder="Company or role…">
    </div>
    <div class="fg">
      <label>Status</label>
      <select id="f-status"><option value="">All statuses</option></select>
    </div>
    <div class="fg">
      <label>Location</label>
      <select id="f-location"><option value="">All locations</option></select>
    </div>
    <div class="fg">
      <label>Min match %</label>
      <input id="f-score" type="number" placeholder="e.g. 70" min="0" max="100" style="min-width:90px">
    </div>
    <div class="fg">
      <label>Date from</label>
      <input id="f-date-from" type="date" style="min-width:130px">
    </div>
    <div class="fg">
      <label>Date to</label>
      <input id="f-date-to" type="date" style="min-width:130px">
    </div>
    <button class="btn-reset" onclick="resetFilters()">Reset</button>
  </div>

  <!-- Table -->
  <div class="table-wrap">
    <div class="table-bar">
      <span id="table-info">Loading…</span>
    </div>
    <div class="scroll-x">
      <table>
        <thead>
          <tr>
            <th onclick="sortBy('date')"             data-col="date">             Date           <span class="arr">↕</span></th>
            <th onclick="sortBy('company')"          data-col="company">          Company        <span class="arr">↕</span></th>
            <th onclick="sortBy('role')"             data-col="role">             Role           <span class="arr">↕</span></th>
            <th onclick="sortBy('location')"         data-col="location">         Location       <span class="arr">↕</span></th>
            <th onclick="sortBy('match_score')"      data-col="match_score">      Match %        <span class="arr">↕</span></th>
            <th onclick="sortBy('interview_chance')" data-col="interview_chance"> Chance         <span class="arr">↕</span></th>
            <th onclick="sortBy('german_level')"     data-col="german_level">     German         <span class="arr">↕</span></th>
            <th onclick="sortBy('status')"           data-col="status">           Status         <span class="arr">↕</span></th>
            <th>Archive</th>
          </tr>
        </thead>
        <tbody id="tbody"></tbody>
      </table>
      <div class="empty-state hidden" id="empty-state">
        <div class="icon">&#128239;</div>
        <div>No applications match the current filters.</div>
      </div>
    </div>
  </div>

</div><!-- /container -->

<script>
'use strict';

let allJobs  = [];
let sortCol  = 'date';
let sortAsc  = false;

const HIGH_GERMAN_TERMS = ['c1','c2','native','muttersprache','verhandlungssicher','fließend','fliessend'];

// ── Helpers ────────────────────────────────────────────────────────────────

function esc(s) {
  return String(s ?? '')
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function formatDate(s) {
  if (!s) return '';
  // ISO / datetime strings: "2025-01-15" or "2025-01-15 00:00:00"
  if (/^\\d{4}-\\d{2}-\\d{2}/.test(s)) return s.slice(0, 10);
  // Excel serial number
  const n = Number(s);
  if (!isNaN(n) && n > 1000 && n < 200000) {
    return new Date(Math.round((n - 25569) * 86400000)).toISOString().slice(0, 10);
  }
  const d = new Date(s);
  return isNaN(d) ? s : d.toISOString().slice(0, 10);
}

function scoreNum(s) {
  const n = parseInt(s, 10);
  return isNaN(n) ? -1 : n;
}

function scoreClass(s) {
  const n = scoreNum(s);
  if (n < 0)   return 's-na';
  if (n >= 80) return 's-hi';
  if (n >= 60) return 's-mid';
  return 's-lo';
}

function chanceChip(s) {
  if (!s) return '';
  const sl = s.toLowerCase();
  if (sl === 'high')   return '<span class="chip ch-high">High</span>';
  if (sl === 'medium') return '<span class="chip ch-medium">Medium</span>';
  if (sl === 'low')    return '<span class="chip ch-low">Low</span>';
  return `<span class="chip ch-na">${esc(s)}</span>`;
}

function statusBadge(s) {
  if (!s) return '';
  const sl = s.toLowerCase();
  let cls = 'bs-default';
  if      (sl.includes('offer'))                                     cls = 'bs-offer';
  else if (sl.includes('scheduled'))                                 cls = 'bs-scheduled';
  else if (sl.includes('awaiting') || sl.includes('⏸'))             cls = 'bs-pending';
  else if (sl.includes('rejected'))                                  cls = 'bs-rejected';
  else if (sl.includes('unconfirmed') || sl.includes('no email'))    cls = 'bs-unconfirmed';
  else if (sl.includes('applied'))                                   cls = 'bs-applied';
  return `<span class="badge ${cls}">${esc(s)}</span>`;
}

function germanCell(s) {
  if (!s || s.toLowerCase() === 'none') return '<span class="td-german">—</span>';
  const isWarn = HIGH_GERMAN_TERMS.some(t => s.toLowerCase().includes(t));
  return `<span class="td-german${isWarn ? ' german-warn' : ''}">${esc(s)}</span>`;
}

function archiveLink(path) {
  if (!path) return '';
  const url = 'file:///' + path.replace(/\\\\/g, '/').replace(/^\\//,'');
  return `<a href="${esc(url)}" title="${esc(path)}" target="_blank">&#128193; open</a>`;
}

// ── Filtering & sorting ───────────────────────────────────────────────────

function applyFilters() {
  const q    = document.getElementById('f-search').value.toLowerCase();
  const sta  = document.getElementById('f-status').value;
  const loc  = document.getElementById('f-location').value;
  const minS = parseInt(document.getElementById('f-score').value, 10) || 0;
  const dfr  = document.getElementById('f-date-from').value;
  const dto  = document.getElementById('f-date-to').value;

  return allJobs.filter(j => {
    if (q && !j.company.toLowerCase().includes(q) && !j.role.toLowerCase().includes(q)) return false;
    if (sta && j.status   !== sta)  return false;
    if (loc && j.location !== loc)  return false;
    if (minS && scoreNum(j.match_score) < minS) return false;
    const d = formatDate(j.date);
    if (dfr && d && d < dfr) return false;
    if (dto && d && d > dto) return false;
    return true;
  });
}

function applySorting(jobs) {
  return [...jobs].sort((a, b) => {
    let av = a[sortCol] ?? '', bv = b[sortCol] ?? '';
    if (sortCol === 'match_score') { av = scoreNum(av); bv = scoreNum(bv); }
    else if (sortCol === 'date')   { av = formatDate(av); bv = formatDate(bv); }
    const cmp = typeof av === 'number'
      ? av - bv
      : String(av).localeCompare(String(bv), undefined, {numeric: true});
    return sortAsc ? cmp : -cmp;
  });
}

// ── Render ────────────────────────────────────────────────────────────────

function render() {
  const jobs   = applySorting(applyFilters());
  const tbody  = document.getElementById('tbody');
  const empty  = document.getElementById('empty-state');
  const info   = document.getElementById('table-info');

  const showed = jobs.length;
  const total  = allJobs.length;
  info.textContent = showed === total
    ? `${total} application${total !== 1 ? 's' : ''}`
    : `Showing ${showed} of ${total} applications`;

  empty.classList.toggle('hidden', showed > 0);

  tbody.innerHTML = jobs.map(j => {
    const dateStr  = esc(formatDate(j.date));
    const n = scoreNum(j.match_score);
    const scoreTxt = n >= 0 ? n + '%' : (j.match_score || '—');
    const roleCell = j.job_url
      ? `<a href="${esc(j.job_url)}" target="_blank" rel="noopener">${esc(j.role || '(no title)')}</a>`
      : `<span>${esc(j.role || '—')}</span>`;

    return `<tr>
      <td class="td-date">${dateStr}</td>
      <td class="td-company" title="${esc(j.notes || '')}">${esc(j.company)}</td>
      <td class="td-role">${roleCell}</td>
      <td>${esc(j.location)}</td>
      <td class="td-score ${scoreClass(j.match_score)}">${esc(scoreTxt)}</td>
      <td>${chanceChip(j.interview_chance)}</td>
      <td>${germanCell(j.german_level)}</td>
      <td>${statusBadge(j.status)}</td>
      <td class="td-archive">${archiveLink(j.archive_folder)}</td>
    </tr>`;
  }).join('');

  // Update sort-arrow indicators
  document.querySelectorAll('thead th[data-col]').forEach(th => {
    const col = th.dataset.col;
    th.classList.toggle('sorted', col === sortCol);
    const arr = th.querySelector('.arr');
    if (arr) arr.textContent = col !== sortCol ? '↕' : (sortAsc ? '↑' : '↓');
  });
}

function sortBy(col) {
  sortAsc = sortCol === col ? !sortAsc : (col !== 'date');
  sortCol = col;
  render();
}

function resetFilters() {
  ['f-search','f-score','f-date-from','f-date-to'].forEach(id => {
    document.getElementById(id).value = '';
  });
  ['f-status','f-location'].forEach(id => {
    document.getElementById(id).value = '';
  });
  render();
}

// ── Summary ───────────────────────────────────────────────────────────────

function updateSummary(jobs) {
  const sl = s => s.toLowerCase();
  const count = pred => jobs.filter(j => pred(sl(j.status))).length;
  document.getElementById('s-total').textContent      = jobs.length;
  document.getElementById('s-applied').textContent    = count(s => s.includes('applied') && !s.includes('unconfirmed'));
  document.getElementById('s-interviews').textContent = count(s => s.includes('scheduled'));
  document.getElementById('s-pending').textContent    = count(s => s.includes('awaiting') || s.includes('⏸'));
  document.getElementById('s-offers').textContent     = count(s => s.includes('offer'));
  document.getElementById('s-rejected').textContent   = count(s => s.includes('rejected'));
}

// ── Dropdowns ─────────────────────────────────────────────────────────────

function populateDropdown(id, field, label) {
  const sel = document.getElementById(id);
  const cur = sel.value;
  const opts = [...new Set(allJobs.map(j => j[field]).filter(Boolean))].sort();
  sel.innerHTML = `<option value="">All ${label}</option>` +
    opts.map(o => `<option value="${esc(o)}"${o === cur ? ' selected' : ''}>${esc(o)}</option>`).join('');
}

// ── Data fetch ────────────────────────────────────────────────────────────

async function fetchJobs() {
  try {
    const resp = await fetch('/api/jobs');
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    if (data.error) throw new Error(data.error);
    allJobs = data.jobs || [];
    updateSummary(allJobs);
    populateDropdown('f-status',   'status',   'statuses');
    populateDropdown('f-location', 'location', 'locations');
    render();
    document.getElementById('last-update').textContent = new Date().toLocaleTimeString();
  } catch (err) {
    document.getElementById('table-info').innerHTML =
      `<span class="error">&#9888; ${esc(err.message)}</span>`;
  } finally {
    document.getElementById('spinner-overlay').classList.add('hidden');
  }
}

// ── Boot ──────────────────────────────────────────────────────────────────

['f-search','f-status','f-location','f-score','f-date-from','f-date-to'].forEach(id => {
  const el = document.getElementById(id);
  el.addEventListener('input',  render);
  el.addEventListener('change', render);
});

fetchJobs();
setInterval(fetchJobs, 60000);
</script>
</body>
</html>"""


# ── Flask routes ──────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template_string(_HTML)


@app.route("/api/jobs")
def api_jobs():
    jobs, error = _load_jobs()
    if error:
        return jsonify({"error": error, "jobs": []}), 200
    return jsonify({"jobs": jobs})


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Job Hunt Dashboard")
    print("  → http://localhost:5000")
    print("  Press Ctrl+C to stop.\n")
    app.run(host="127.0.0.1", port=5000, debug=False)
