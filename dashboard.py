"""
dashboard.py — local web dashboard for job application tracking and agent control.

Tab 1 — Dashboard: reads job_application_tracker_v34.xlsx, filterable/sortable table.
Tab 2 — Job Hunt Agent: role extractor, config editor, run/stop agent, live log.

Run:  python dashboard.py
Open: http://localhost:5000
"""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pandas as pd
import yaml
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template_string, request

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent))
from config import load_config
import config as _config_module

app = Flask(__name__)

_CONFIG_PATH = Path(__file__).parent / "config.yaml"
_MAIN_PY     = Path(__file__).parent / "main.py"
_SSE_SEP     = chr(10) + chr(10)   # SSE message separator: two LF bytes

_agent_proc: "subprocess.Popen | None" = None
_agent_lock  = threading.Lock()

# ── Excel helpers ─────────────────────────────────────────────────────────────

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


def _find_col(df: pd.DataFrame, canonical: str) -> "str | None":
    for alias in _COL_ALIASES.get(canonical, [canonical]):
        if alias in df.columns:
            return alias
    lower_map = {c.lower(): c for c in df.columns}
    for alias in _COL_ALIASES.get(canonical, [canonical]):
        if alias.lower() in lower_map:
            return lower_map[alias.lower()]
    return None


def _normalise_score(raw: str) -> str:
    try:
        val = float(raw)
        if 0 < val <= 1.0:
            return str(round(val * 100))
        if val > 1:
            return str(round(val))
    except (ValueError, TypeError):
        pass
    return raw


def _load_jobs() -> "tuple[list[dict], str]":
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
<title>Job Hunt</title>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  background: #0d1117; color: #e2e8f0; min-height: 100vh;
}
a { color: inherit; }

/* ── Layout ── */
.container { max-width: 1500px; margin: 0 auto; padding: 24px 20px; }

header { margin-bottom: 0; }
.header-row {
  display: flex; align-items: center; justify-content: space-between;
  flex-wrap: wrap; gap: 12px; margin-bottom: 16px;
}
header h1 { font-size: 1.45rem; font-weight: 700; color: #f8fafc; letter-spacing: -0.02em; }
.refresh-pill {
  font-size: 0.75rem; color: #64748b; background: #161b22;
  border: 1px solid #21262d; border-radius: 20px; padding: 5px 12px;
}
.refresh-pill span { color: #38bdf8; font-weight: 600; }

/* ── Tab nav ── */
.tab-nav {
  display: flex; gap: 2px; border-bottom: 1px solid #21262d; margin-bottom: 24px;
}
.tab-btn {
  background: none; border: none; border-bottom: 2px solid transparent;
  color: #8b949e; padding: 9px 18px; font-size: 0.88rem; cursor: pointer;
  margin-bottom: -1px; transition: color 0.15s, border-color 0.15s;
}
.tab-btn:hover { color: #c9d1d9; }
.tab-btn.active { color: #f0f6fc; border-bottom-color: #58a6ff; font-weight: 600; }

/* ── Summary cards ── */
.cards {
  display: grid; grid-template-columns: repeat(6, 1fr);
  gap: 12px; margin-bottom: 20px;
}
@media (max-width: 960px) { .cards { grid-template-columns: repeat(3, 1fr); } }
@media (max-width: 480px) { .cards { grid-template-columns: repeat(2, 1fr); } }
.card {
  background: #161b22; border: 1px solid #21262d; border-radius: 10px;
  padding: 16px 18px;
}
.card .n   { font-size: 2.1rem; font-weight: 700; line-height: 1; margin-bottom: 4px; }
.card .lbl { font-size: 0.69rem; text-transform: uppercase; letter-spacing: 0.07em; color: #64748b; }
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

/* ── Table ── */
.table-wrap {
  background: #161b22; border: 1px solid #21262d; border-radius: 10px; overflow: hidden;
}
.table-bar {
  padding: 10px 18px; font-size: 0.78rem; color: #64748b;
  border-bottom: 1px solid #21262d;
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
.td-score { font-weight: 700; }
.s-hi  { color: #34d399; } .s-mid { color: #fb923c; }
.s-lo  { color: #f87171; } .s-na  { color: #64748b; }
.chip {
  display: inline-block; padding: 2px 8px; border-radius: 99px;
  font-size: 0.68rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em;
}
.ch-high   { background: #0d2818; color: #34d399; }
.ch-medium { background: #2d1a06; color: #fb923c; }
.ch-low    { background: #2d0f0f; color: #f87171; }
.ch-na     { background: #1c2128; color: #64748b; }
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
.td-german   { font-size: 0.79rem; color: #8b949e; }
.german-warn { color: #fb923c; }
.td-archive a {
  color: #a78bfa; font-size: 0.76rem; text-decoration: none; white-space: nowrap;
}
.td-archive a:hover { text-decoration: underline; }
.empty-state { text-align: center; padding: 64px 20px; color: #484f58; }
.empty-state .icon { font-size: 2.8rem; margin-bottom: 10px; }

/* ── Agent tab layout ── */
.agent-grid {
  display: grid; grid-template-columns: 1fr 2fr; gap: 16px; align-items: start;
}
@media (max-width: 960px) { .agent-grid { grid-template-columns: 1fr; } }

/* ── Panels ── */
.panel {
  background: #161b22; border: 1px solid #21262d; border-radius: 10px; overflow: hidden;
}
.mt16 { margin-top: 16px; }
.panel-hd {
  padding: 11px 18px; border-bottom: 1px solid #21262d;
  display: flex; align-items: center; justify-content: space-between; gap: 10px;
}
.panel-title { font-weight: 600; font-size: 0.88rem; color: #f0f6fc; }
.panel-actions { display: flex; gap: 6px; align-items: center; }

/* ── Buttons ── */
.btn-sm {
  background: #21262d; border: 1px solid #30363d; color: #8b949e;
  border-radius: 6px; padding: 4px 10px; font-size: 0.77rem; cursor: pointer;
  transition: background 0.15s; white-space: nowrap;
}
.btn-sm:hover { background: #30363d; color: #e2e8f0; }
.btn-primary {
  background: #1f6feb; border: 1px solid #388bfd; color: #fff;
  border-radius: 6px; padding: 7px 16px; font-size: 0.84rem; cursor: pointer;
  transition: background 0.15s; white-space: nowrap;
}
.btn-primary:hover:not(:disabled) { background: #388bfd; }
.btn-primary:disabled { background: #1c2128; border-color: #30363d; color: #64748b; cursor: not-allowed; }
.btn-danger {
  background: #2d0f0f; border: 1px solid #7f1d1d; color: #f87171;
  border-radius: 6px; padding: 7px 16px; font-size: 0.84rem; cursor: pointer;
  transition: background 0.15s; white-space: nowrap;
}
.btn-danger:hover:not(:disabled) { background: #450a0a; }
.btn-danger:disabled { background: #1c2128; border-color: #30363d; color: #64748b; cursor: not-allowed; }
.btn-full { width: 100%; padding: 9px; font-size: 0.88rem; margin-top: 6px; }

/* ── Role chips ── */
.roles-area {
  padding: 16px 18px; display: flex; flex-wrap: wrap; gap: 8px; min-height: 72px;
}
.role-chip {
  display: inline-block; padding: 5px 14px; border-radius: 99px;
  background: #1c2128; border: 1px solid #30363d; color: #8b949e;
  font-size: 0.83rem; cursor: pointer; user-select: none; transition: all 0.15s;
}
.role-chip:hover { border-color: #58a6ff; color: #c9d1d9; }
.role-chip.selected { background: #0c2a4a; border-color: #58a6ff; color: #58a6ff; font-weight: 600; }
.loading-text { color: #64748b; font-size: 0.84rem; font-style: italic; align-self: center; }
.error-text   { color: #f87171; font-size: 0.84rem; align-self: center; }

/* ── Config form ── */
.config-form {
  padding: 14px 18px; display: flex; flex-direction: column; gap: 9px;
  max-height: 640px; overflow-y: auto;
}
.cfg-section {
  font-size: 0.67rem; text-transform: uppercase; letter-spacing: 0.08em; color: #4b5563;
  border-bottom: 1px solid #21262d; padding-bottom: 3px; margin-top: 4px;
}
.cfg-row { display: flex; flex-direction: column; gap: 4px; }
.cfg-row label, .cfg-col label {
  font-size: 0.69rem; text-transform: uppercase; letter-spacing: 0.07em; color: #64748b;
}
.cfg-row input[type='text'],
.cfg-row input[type='number'],
.cfg-col input[type='text'],
.cfg-col input[type='number'] {
  background: #0d1117; border: 1px solid #30363d; color: #e2e8f0;
  border-radius: 6px; padding: 6px 10px; font-size: 0.83rem; outline: none;
  transition: border-color 0.15s; width: 100%;
}
.cfg-row input:focus, .cfg-col input:focus { border-color: #58a6ff; }
.cfg-2col { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.cfg-col  { display: flex; flex-direction: column; gap: 4px; }

/* ── Tag lists ── */
.tag-list { display: flex; flex-wrap: wrap; gap: 5px; margin-bottom: 5px; min-height: 24px; }
.tag-chip {
  display: inline-flex; align-items: center; gap: 3px;
  background: #1c2128; border: 1px solid #30363d; color: #c9d1d9;
  padding: 2px 8px; border-radius: 6px; font-size: 0.78rem;
}
.tag-rm {
  background: none; border: none; color: #64748b; cursor: pointer;
  padding: 0 1px; font-size: 0.9rem; line-height: 1; transition: color 0.1s;
}
.tag-rm:hover { color: #f87171; }
.tag-input-row { display: flex; gap: 5px; }
.tag-input-row input {
  flex: 1; background: #0d1117; border: 1px solid #30363d; color: #e2e8f0;
  border-radius: 6px; padding: 5px 8px; font-size: 0.81rem; outline: none;
  transition: border-color 0.15s;
}
.tag-input-row input:focus { border-color: #58a6ff; }

/* ── Toggle switches ── */
.toggle-rows { display: flex; flex-direction: column; gap: 7px; }
.toggle-row  { display: flex; align-items: center; justify-content: space-between; padding: 1px 0; }
.t-label     { font-size: 0.84rem; color: #c9d1d9; }
.t-switch    { position: relative; width: 36px; height: 20px; flex-shrink: 0; display: inline-block; }
.t-switch input  { opacity: 0; width: 0; height: 0; position: absolute; }
.t-track {
  position: absolute; inset: 0; background: #374151; border-radius: 10px;
  cursor: pointer; transition: background 0.2s;
}
.t-switch input:checked + .t-track { background: #2563eb; }
.t-thumb {
  position: absolute; top: 2px; left: 2px; width: 16px; height: 16px;
  background: #fff; border-radius: 50%; transition: left 0.2s; pointer-events: none;
}
.t-switch input:checked + .t-track .t-thumb { left: 18px; }

/* ── Agent control ── */
.agent-ctrl-row {
  padding: 14px 18px; display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
}
.agent-status { display: flex; align-items: center; gap: 7px; font-size: 0.84rem; color: #8b949e; }
.status-dot { width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0; }
.status-dot.stopped { background: #4b5563; }
.status-dot.running { background: #34d399; animation: blink 1.4s ease-in-out infinite; }
@keyframes blink { 0%, 100% { opacity: 1; } 50% { opacity: 0.25; } }

/* ── Log viewer ── */
.log-viewer {
  background: #010409; margin: 0 16px 16px; border-radius: 6px;
  padding: 10px 14px; height: 320px; overflow-y: auto;
  font-family: 'Consolas', 'Courier New', monospace; font-size: 0.76rem; line-height: 1.6;
}
.log-line { white-space: pre-wrap; word-break: break-all; padding: 1px 0; }
.log-info    { color: #58a6ff; }
.log-warning { color: #fb923c; }
.log-error   { color: #f87171; font-weight: 600; }
.log-default { color: #8b949e; }

/* ── Toast ── */
.toast {
  position: fixed; bottom: 24px; right: 24px; z-index: 999;
  background: #1e293b; border: 1px solid #334155; color: #e2e8f0;
  padding: 9px 16px; border-radius: 8px; font-size: 0.84rem; max-width: 320px;
  opacity: 0; transform: translateY(6px); transition: all 0.2s; pointer-events: none;
}
.toast.show { opacity: 1; transform: translateY(0); }
.toast-error { border-color: #f87171 !important; color: #fca5a5 !important; }

/* ── Shared ── */
.hidden { display: none !important; }
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
<div id="toast" class="toast"></div>

<div class="container">

  <header>
    <div class="header-row">
      <h1>&#128188; Job Hunt</h1>
      <div id="refresh-pill" class="refresh-pill">
        Auto-refreshes every 60s &nbsp;&middot;&nbsp; Last update: <span id="last-update">—</span>
      </div>
    </div>
    <nav class="tab-nav">
      <button class="tab-btn active" data-tab="dashboard" onclick="showTab('dashboard')">&#128202; Dashboard</button>
      <button class="tab-btn"        data-tab="agent"     onclick="showTab('agent')">&#129302; Job Hunt Agent</button>
    </nav>
  </header>

  <!-- ═══ TAB: DASHBOARD ═══════════════════════════════════════════════════ -->
  <div id="tab-dashboard" class="tab-content">

    <div class="cards">
      <div class="card c-total">    <div class="n" id="s-total">—</div>    <div class="lbl">Total</div></div>
      <div class="card c-applied">  <div class="n" id="s-applied">—</div>  <div class="lbl">Applied</div></div>
      <div class="card c-interview"><div class="n" id="s-interviews">—</div><div class="lbl">Interviews</div></div>
      <div class="card c-pending">  <div class="n" id="s-pending">—</div>  <div class="lbl">Pending</div></div>
      <div class="card c-offer">    <div class="n" id="s-offers">—</div>   <div class="lbl">Offers</div></div>
      <div class="card c-rejected"> <div class="n" id="s-rejected">—</div> <div class="lbl">Rejected</div></div>
    </div>

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

    <div class="table-wrap">
      <div class="table-bar"><span id="table-info">Loading…</span></div>
      <div class="scroll-x">
        <table>
          <thead><tr>
            <th onclick="sortBy('date')"             data-col="date">             Date    <span class="arr">↕</span></th>
            <th onclick="sortBy('company')"          data-col="company">          Company <span class="arr">↕</span></th>
            <th onclick="sortBy('role')"             data-col="role">             Role    <span class="arr">↕</span></th>
            <th onclick="sortBy('location')"         data-col="location">         Location<span class="arr">↕</span></th>
            <th onclick="sortBy('match_score')"      data-col="match_score">      Match % <span class="arr">↕</span></th>
            <th onclick="sortBy('interview_chance')" data-col="interview_chance"> Chance  <span class="arr">↕</span></th>
            <th onclick="sortBy('german_level')"     data-col="german_level">     German  <span class="arr">↕</span></th>
            <th onclick="sortBy('status')"           data-col="status">           Status  <span class="arr">↕</span></th>
            <th>Archive</th>
          </tr></thead>
          <tbody id="tbody"></tbody>
        </table>
        <div class="empty-state hidden" id="empty-state">
          <div class="icon">&#128239;</div>
          <div>No applications match the current filters.</div>
        </div>
      </div>
    </div>

  </div><!-- /tab-dashboard -->

  <!-- ═══ TAB: AGENT ═══════════════════════════════════════════════════════ -->
  <div id="tab-agent" class="tab-content hidden">

    <div class="agent-grid">

      <!-- Job Titles panel -->
      <div class="panel">
        <div class="panel-hd">
          <span class="panel-title">Job Titles</span>
          <div class="panel-actions">
            <button class="btn-sm" onclick="selectAllRoles()">Select All</button>
            <button class="btn-sm" onclick="clearAllRoles()">Clear</button>
            <button class="btn-sm btn-primary" onclick="saveRoles()">Save</button>
          </div>
        </div>
        <div id="roles-container" class="roles-area">
          <span class="loading-text">Click the Agent tab to load…</span>
        </div>
      </div>

      <!-- Config Editor panel -->
      <div class="panel">
        <div class="panel-hd">
          <span class="panel-title">Config Editor</span>
          <button id="btn-save-config" class="btn-sm btn-primary" onclick="saveConfig()">Save Config</button>
        </div>
        <div class="config-form">

          <div class="cfg-section">Identity</div>
          <div class="cfg-row"><label>Full Name</label><input id="cfg-full_name" type="text"></div>
          <div class="cfg-row"><label>Email Address</label><input id="cfg-hotmail_address" type="text"></div>
          <div class="cfg-row"><label>Notify Email</label><input id="cfg-notify_email" type="text"></div>
          <div class="cfg-row"><label>Phone</label><input id="cfg-phone" type="text"></div>

          <div class="cfg-section">Paths</div>
          <div class="cfg-row"><label>CV Root Path</label><input id="cfg-cv_root" type="text"></div>
          <div class="cfg-row"><label>Resume EN (relative)</label><input id="cfg-resume_en" type="text"></div>
          <div class="cfg-row"><label>Resume DE (relative)</label><input id="cfg-resume_de" type="text"></div>
          <div class="cfg-row"><label>Tracker File</label><input id="cfg-tracker_file" type="text"></div>

          <div class="cfg-section">Scoring</div>
          <div class="cfg-2col">
            <div class="cfg-col"><label>Min Match Score</label><input id="cfg-min_match_score" type="number" min="0" max="100"></div>
            <div class="cfg-col"><label>Max Apps / Day</label><input id="cfg-max_applications_per_day" type="number" min="1"></div>
          </div>

          <div class="cfg-section">Search</div>
          <div class="cfg-row">
            <label>Locations</label>
            <div id="tl-locations" class="tag-list"></div>
            <div class="tag-input-row">
              <input id="tl-input-locations" type="text" placeholder="Add location…" onkeydown="addTagOnEnter(event,'locations')">
              <button class="btn-sm" onclick="addTag('locations')">+ Add</button>
            </div>
          </div>
          <div class="cfg-row">
            <label>Skip German Levels</label>
            <div id="tl-skip_german_levels" class="tag-list"></div>
            <div class="tag-input-row">
              <input id="tl-input-skip_german_levels" type="text" placeholder="Add level…" onkeydown="addTagOnEnter(event,'skip_german_levels')">
              <button class="btn-sm" onclick="addTag('skip_german_levels')">+ Add</button>
            </div>
          </div>

          <div class="cfg-section">Behaviour</div>
          <div class="toggle-rows">
            <div class="toggle-row">
              <span class="t-label">Auto-confirm recruiter call</span>
              <label class="t-switch"><input id="cfg-auto_confirm_recruiter_call" type="checkbox"><div class="t-track"><div class="t-thumb"></div></div></label>
            </div>
            <div class="toggle-row">
              <span class="t-label">Auto-confirm technical round</span>
              <label class="t-switch"><input id="cfg-auto_confirm_technical" type="checkbox"><div class="t-track"><div class="t-thumb"></div></div></label>
            </div>
            <div class="toggle-row">
              <span class="t-label">Headless browser</span>
              <label class="t-switch"><input id="cfg-headless" type="checkbox"><div class="t-track"><div class="t-thumb"></div></div></label>
            </div>
            <div class="toggle-row">
              <span class="t-label">Confirm before apply</span>
              <label class="t-switch"><input id="cfg-confirm_before_apply" type="checkbox"><div class="t-track"><div class="t-thumb"></div></div></label>
            </div>
            <div class="toggle-row">
              <span class="t-label">Retry CAPTCHA as manual</span>
              <label class="t-switch"><input id="cfg-retry_captcha_as_manual" type="checkbox"><div class="t-track"><div class="t-thumb"></div></div></label>
            </div>
          </div>

        </div><!-- /config-form -->
      </div><!-- /Config panel -->

    </div><!-- /agent-grid -->

    <!-- Agent Control panel -->
    <div class="panel mt16">
      <div class="panel-hd">
        <span class="panel-title">Agent Control</span>
        <div class="agent-status">
          <span id="status-dot" class="status-dot stopped"></span>
          <span id="status-text">Stopped</span>
        </div>
      </div>
      <div class="agent-ctrl-row">
        <button id="btn-start" class="btn-primary" onclick="startAgent()">&#9654; Run Agent</button>
        <button id="btn-stop"  class="btn-danger"  onclick="stopAgent()" disabled>&#9632; Stop</button>
      </div>
    </div>

    <!-- Live Log panel -->
    <div class="panel mt16">
      <div class="panel-hd">
        <span class="panel-title">Live Log</span>
        <button class="btn-sm" onclick="clearLog()">Clear</button>
      </div>
      <div id="log-viewer" class="log-viewer"></div>
    </div>

  </div><!-- /tab-agent -->

</div><!-- /container -->

<script>
'use strict';

// ═══════════════════════════════════════════════════════════
//  TAB SWITCHING
// ═══════════════════════════════════════════════════════════

let _agentInited = false;

function showTab(name) {
  document.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));
  document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
  document.getElementById('tab-' + name).classList.remove('hidden');
  document.querySelector('.tab-btn[data-tab="' + name + '"]').classList.add('active');
  document.getElementById('refresh-pill').classList.toggle('hidden', name !== 'dashboard');
  if (name === 'agent' && !_agentInited) { _agentInited = true; initAgentTab(); }
}

function initAgentTab() {
  loadConfig();
  loadRoles();
  startLogStream();
  pollAgentStatus();
  setInterval(pollAgentStatus, 5000);
}

// ═══════════════════════════════════════════════════════════
//  SHARED HELPERS
// ═══════════════════════════════════════════════════════════

function esc(s) {
  return String(s ?? '')
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function showToast(msg, type) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast show' + (type === 'error' ? ' toast-error' : '');
  clearTimeout(t._tid);
  t._tid = setTimeout(() => t.classList.remove('show'), 3200);
}

// ═══════════════════════════════════════════════════════════
//  DASHBOARD TAB
// ═══════════════════════════════════════════════════════════

let allJobs = [];
let sortCol = 'date';
let sortAsc = false;

const HIGH_GERMAN = ['c1','c2','native','muttersprache','verhandlungssicher','fließend','fliessend'];

function formatDate(s) {
  if (!s) return '';
  if (/^\\d{4}-\\d{2}-\\d{2}/.test(s)) return s.slice(0, 10);
  const n = Number(s);
  if (!isNaN(n) && n > 1000 && n < 200000)
    return new Date(Math.round((n - 25569) * 86400000)).toISOString().slice(0, 10);
  const d = new Date(s);
  return isNaN(d) ? s : d.toISOString().slice(0, 10);
}

function scoreNum(s) { const n = parseInt(s, 10); return isNaN(n) ? -1 : n; }

function scoreClass(s) {
  const n = scoreNum(s);
  if (n < 0) return 's-na'; if (n >= 80) return 's-hi';
  if (n >= 60) return 's-mid'; return 's-lo';
}

function chanceChip(s) {
  if (!s) return '';
  const sl = s.toLowerCase();
  if (sl === 'high')   return '<span class="chip ch-high">High</span>';
  if (sl === 'medium') return '<span class="chip ch-medium">Medium</span>';
  if (sl === 'low')    return '<span class="chip ch-low">Low</span>';
  return '<span class="chip ch-na">' + esc(s) + '</span>';
}

function statusBadge(s) {
  if (!s) return '';
  const sl = s.toLowerCase();
  let c = 'bs-default';
  if      (sl.includes('offer'))                                  c = 'bs-offer';
  else if (sl.includes('scheduled'))                              c = 'bs-scheduled';
  else if (sl.includes('awaiting') || sl.includes('⏸'))      c = 'bs-pending';
  else if (sl.includes('rejected'))                               c = 'bs-rejected';
  else if (sl.includes('unconfirmed') || sl.includes('no email')) c = 'bs-unconfirmed';
  else if (sl.includes('applied'))                                c = 'bs-applied';
  return '<span class="badge ' + c + '">' + esc(s) + '</span>';
}

function germanCell(s) {
  if (!s || s.toLowerCase() === 'none') return '<span class="td-german">—</span>';
  const w = HIGH_GERMAN.some(t => s.toLowerCase().includes(t));
  return '<span class="td-german' + (w ? ' german-warn' : '') + '">' + esc(s) + '</span>';
}

function archiveLink(path) {
  if (!path) return '';
  const url = 'file:///' + path.replace(/\\\\/g, '/').replace(/^\\//,'');
  return '<a href="' + esc(url) + '" title="' + esc(path) + '" target="_blank">&#128193; open</a>';
}

function applyFilters() {
  const q   = document.getElementById('f-search').value.toLowerCase();
  const sta = document.getElementById('f-status').value;
  const loc = document.getElementById('f-location').value;
  const min = parseInt(document.getElementById('f-score').value, 10) || 0;
  const dfr = document.getElementById('f-date-from').value;
  const dto = document.getElementById('f-date-to').value;
  return allJobs.filter(j => {
    if (q && !j.company.toLowerCase().includes(q) && !j.role.toLowerCase().includes(q)) return false;
    if (sta && j.status   !== sta) return false;
    if (loc && j.location !== loc) return false;
    if (min && scoreNum(j.match_score) < min) return false;
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
      ? av - bv : String(av).localeCompare(String(bv), undefined, {numeric: true});
    return sortAsc ? cmp : -cmp;
  });
}

function render() {
  const jobs  = applySorting(applyFilters());
  const tbody = document.getElementById('tbody');
  const info  = document.getElementById('table-info');
  info.textContent = jobs.length === allJobs.length
    ? allJobs.length + ' application' + (allJobs.length !== 1 ? 's' : '')
    : 'Showing ' + jobs.length + ' of ' + allJobs.length + ' applications';
  document.getElementById('empty-state').classList.toggle('hidden', jobs.length > 0);
  tbody.innerHTML = jobs.map(j => {
    const n = scoreNum(j.match_score);
    const scoreTxt = n >= 0 ? n + '%' : (j.match_score || '—');
    const roleCell = j.job_url
      ? '<a href="' + esc(j.job_url) + '" target="_blank" rel="noopener">' + esc(j.role || '(no title)') + '</a>'
      : '<span>' + esc(j.role || '—') + '</span>';
    return '<tr>'
      + '<td class="td-date">' + esc(formatDate(j.date)) + '</td>'
      + '<td class="td-company" title="' + esc(j.notes || '') + '">' + esc(j.company) + '</td>'
      + '<td class="td-role">' + roleCell + '</td>'
      + '<td>' + esc(j.location) + '</td>'
      + '<td class="td-score ' + scoreClass(j.match_score) + '">' + esc(scoreTxt) + '</td>'
      + '<td>' + chanceChip(j.interview_chance) + '</td>'
      + '<td>' + germanCell(j.german_level) + '</td>'
      + '<td>' + statusBadge(j.status) + '</td>'
      + '<td class="td-archive">' + archiveLink(j.archive_folder) + '</td>'
      + '</tr>';
  }).join('');
  document.querySelectorAll('thead th[data-col]').forEach(th => {
    const col = th.dataset.col;
    th.classList.toggle('sorted', col === sortCol);
    const arr = th.querySelector('.arr');
    if (arr) arr.textContent = col !== sortCol ? '↕' : (sortAsc ? '↑' : '↓');
  });
}

function sortBy(col) {
  sortAsc = sortCol === col ? !sortAsc : (col !== 'date');
  sortCol = col; render();
}

function resetFilters() {
  ['f-search','f-score','f-date-from','f-date-to'].forEach(id => document.getElementById(id).value = '');
  ['f-status','f-location'].forEach(id => document.getElementById(id).value = '');
  render();
}

function updateSummary(jobs) {
  const sl = s => s.toLowerCase();
  const n  = pred => jobs.filter(j => pred(sl(j.status))).length;
  document.getElementById('s-total').textContent      = jobs.length;
  document.getElementById('s-applied').textContent    = n(s => s.includes('applied') && !s.includes('unconfirmed'));
  document.getElementById('s-interviews').textContent = n(s => s.includes('scheduled'));
  document.getElementById('s-pending').textContent    = n(s => s.includes('awaiting') || s.includes('⏸'));
  document.getElementById('s-offers').textContent     = n(s => s.includes('offer'));
  document.getElementById('s-rejected').textContent   = n(s => s.includes('rejected'));
}

function populateDropdown(id, field, label) {
  const sel = document.getElementById(id);
  const cur = sel.value;
  const opts = [...new Set(allJobs.map(j => j[field]).filter(Boolean))].sort();
  sel.innerHTML = '<option value="">All ' + label + '</option>'
    + opts.map(o => '<option value="' + esc(o) + '"' + (o === cur ? ' selected' : '') + '>' + esc(o) + '</option>').join('');
}

async function fetchJobs() {
  try {
    const r = await fetch('/api/jobs');
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const d = await r.json();
    if (d.error) throw new Error(d.error);
    allJobs = d.jobs || [];
    updateSummary(allJobs);
    populateDropdown('f-status',   'status',   'statuses');
    populateDropdown('f-location', 'location', 'locations');
    render();
    document.getElementById('last-update').textContent = new Date().toLocaleTimeString();
  } catch (err) {
    document.getElementById('table-info').innerHTML = '<span class="error">&#9888; ' + esc(err.message) + '</span>';
  } finally {
    document.getElementById('spinner-overlay').classList.add('hidden');
  }
}

['f-search','f-status','f-location','f-score','f-date-from','f-date-to'].forEach(id => {
  const el = document.getElementById(id);
  el.addEventListener('input',  render);
  el.addEventListener('change', render);
});

// ═══════════════════════════════════════════════════════════
//  ROLES PANEL
// ═══════════════════════════════════════════════════════════

let _rolesData = [];
let _selected  = new Set();

async function loadRoles() {
  const box = document.getElementById('roles-container');
  box.innerHTML = '<span class="loading-text">Extracting roles from resume via Claude…</span>';
  try {
    const r = await fetch('/api/roles');
    const d = await r.json();
    if (d.error) throw new Error(d.error);
    _rolesData = d.roles || [];
    _selected  = new Set(_rolesData);
    renderRoles();
  } catch (err) {
    box.innerHTML = '<span class="error-text">&#9888; ' + esc(err.message) + '</span>';
  }
}

function renderRoles() {
  const box = document.getElementById('roles-container');
  if (!_rolesData.length) {
    box.innerHTML = '<span class="loading-text">No roles extracted.</span>';
    return;
  }
  box.innerHTML = _rolesData.map(r =>
    `<span class="role-chip${_selected.has(r) ? ' selected' : ''}" data-role="${esc(r)}">${esc(r)}</span>`
  ).join('');
  box.querySelectorAll('.role-chip').forEach(chip =>
    chip.addEventListener('click', () => toggleRole(chip.dataset.role))
  );
}

function toggleRole(r) {
  _selected.has(r) ? _selected.delete(r) : _selected.add(r);
  const chip = document.querySelector(`#roles-container [data-role="${CSS.escape(r)}"]`);
  if (chip) chip.classList.toggle('selected', _selected.has(r));
}
function selectAllRoles() { _selected = new Set(_rolesData); renderRoles(); }
function clearAllRoles()  { _selected = new Set(); renderRoles(); }

async function saveRoles() {
  try {
    const cfgR = await fetch('/api/config');
    const cfg  = await cfgR.json();
    cfg.roles  = [..._selected];
    const r    = await fetch('/api/config', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(cfg)});
    const d    = await r.json();
    d.ok ? showToast('Saved ' + cfg.roles.length + ' role(s) to config.yaml') : showToast(d.error, 'error');
  } catch (err) { showToast(err.message, 'error'); }
}

// ═══════════════════════════════════════════════════════════
//  CONFIG EDITOR
// ═══════════════════════════════════════════════════════════

let _cfgData = {};
const _tagLists = {};

const _TEXT_KEYS = ['full_name','hotmail_address','notify_email','phone','cv_root','resume_en','resume_de','tracker_file'];
const _NUM_KEYS  = ['min_match_score','max_applications_per_day'];
const _TOG_KEYS  = ['auto_confirm_recruiter_call','auto_confirm_technical','headless','confirm_before_apply','retry_captcha_as_manual'];
const _TAG_KEYS  = ['locations','skip_german_levels'];

async function loadConfig() {
  try {
    const r = await fetch('/api/config');
    const d = await r.json();
    if (d.error) throw new Error(d.error);
    _cfgData = d;
    _TEXT_KEYS.forEach(k => { const el = document.getElementById('cfg-' + k); if (el) el.value = d[k] || ''; });
    _NUM_KEYS.forEach(k  => { const el = document.getElementById('cfg-' + k); if (el) el.value = d[k] ?? ''; });
    _TOG_KEYS.forEach(k  => { const el = document.getElementById('cfg-' + k); if (el) el.checked = !!d[k]; });
    _TAG_KEYS.forEach(k  => { _tagLists[k] = [...(d[k] || [])]; renderTagList(k); });
  } catch (err) { showToast('Config load error: ' + err.message, 'error'); }
}

function renderTagList(key) {
  const box = document.getElementById('tl-' + key);
  if (!box) return;
  box.innerHTML = (_tagLists[key] || []).map(v =>
    `<span class="tag-chip">${esc(v)}<button class="tag-rm" onclick="removeTag('${key}',${JSON.stringify(v)})">&#215;</button></span>`
  ).join('');
}

function addTag(key) {
  const inp = document.getElementById('tl-input-' + key);
  const val = inp.value.trim();
  if (!val || (_tagLists[key] || []).includes(val)) { inp.value = ''; return; }
  (_tagLists[key] = _tagLists[key] || []).push(val);
  inp.value = ''; renderTagList(key);
}

function removeTag(key, val) { _tagLists[key] = (_tagLists[key] || []).filter(v => v !== val); renderTagList(key); }
function addTagOnEnter(e, key) { if (e.key === 'Enter') { e.preventDefault(); addTag(key); } }

async function saveConfig() {
  const payload = {..._cfgData};
  _TEXT_KEYS.forEach(k => { const el = document.getElementById('cfg-' + k); if (el) payload[k] = el.value; });
  _NUM_KEYS.forEach(k  => { const el = document.getElementById('cfg-' + k); if (el) payload[k] = el.value !== '' ? Number(el.value) : null; });
  _TOG_KEYS.forEach(k  => { const el = document.getElementById('cfg-' + k); if (el) payload[k] = el.checked; });
  _TAG_KEYS.forEach(k  => { payload[k] = _tagLists[k] || []; });
  delete payload.paths; delete payload.contact;

  const btn = document.getElementById('btn-save-config');
  btn.disabled = true; btn.textContent = 'Saving…';
  try {
    const r = await fetch('/api/config', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)});
    const d = await r.json();
    d.ok ? showToast('Config saved ✓') : showToast('Save failed: ' + d.error, 'error');
  } catch (err) {
    showToast('Error: ' + err.message, 'error');
  } finally {
    btn.disabled = false; btn.textContent = 'Save Config';
  }
}

// ═══════════════════════════════════════════════════════════
//  AGENT CONTROL
// ═══════════════════════════════════════════════════════════

async function startAgent() {
  const btn = document.getElementById('btn-start');
  btn.disabled = true;
  try {
    const r = await fetch('/api/agent/start', {method:'POST'});
    const d = await r.json();
    if (d.error) { showToast(d.error, 'error'); return; }
    showToast('Agent started (PID ' + d.pid + ')');
    setAgentUI(true, d.pid);
  } catch (err) { showToast(err.message, 'error'); }
  finally { btn.disabled = false; }
}

async function stopAgent() {
  const btn = document.getElementById('btn-stop');
  btn.disabled = true;
  try {
    const r = await fetch('/api/agent/stop', {method:'POST'});
    const d = await r.json();
    if (d.ok) showToast('Agent stopped');
    setAgentUI(false, null);
  } catch (err) { showToast(err.message, 'error'); }
  finally { btn.disabled = false; }
}

async function pollAgentStatus() {
  try {
    const r = await fetch('/api/agent/status');
    const d = await r.json();
    setAgentUI(d.running, d.pid);
  } catch (_) {}
}

function setAgentUI(running, pid) {
  document.getElementById('status-dot').className  = 'status-dot ' + (running ? 'running' : 'stopped');
  document.getElementById('status-text').textContent = running ? 'Running (PID ' + pid + ')' : 'Stopped';
  document.getElementById('btn-start').disabled = running;
  document.getElementById('btn-stop').disabled  = !running;
}

// ═══════════════════════════════════════════════════════════
//  LIVE LOG
// ═══════════════════════════════════════════════════════════

let _logES = null;

function startLogStream() {
  if (_logES) { _logES.close(); _logES = null; }
  const box = document.getElementById('log-viewer');
  _logES = new EventSource('/api/log/stream');
  _logES.onmessage = function(e) {
    const line = JSON.parse(e.data);
    const div  = document.createElement('div');
    div.className = 'log-line ' + logClass(line);
    div.textContent = line;
    box.appendChild(div);
    while (box.children.length > 500) box.children[0].remove();
    if (box.scrollHeight - box.scrollTop - box.clientHeight < 80) box.scrollTop = box.scrollHeight;
  };
}

function logClass(line) {
  const u = line.toUpperCase();
  if (u.includes('ERROR'))   return 'log-error';
  if (u.includes('WARNING')) return 'log-warning';
  if (u.includes('INFO'))    return 'log-info';
  return 'log-default';
}

function clearLog() { document.getElementById('log-viewer').innerHTML = ''; }

// ═══════════════════════════════════════════════════════════
//  BOOT
// ═══════════════════════════════════════════════════════════

fetchJobs();
setInterval(fetchJobs, 60000);
</script>
</body>
</html>"""


# ── Flask routes — existing ───────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template_string(_HTML)


@app.route("/api/jobs")
def api_jobs():
    jobs, error = _load_jobs()
    if error:
        return jsonify({"error": error, "jobs": []})
    return jsonify({"jobs": jobs})


# ── Flask routes — config ─────────────────────────────────────────────────────

@app.route("/api/config", methods=["GET"])
def api_config_get():
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        return jsonify(raw)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/config", methods=["POST"])
def api_config_post():
    try:
        data = request.get_json(force=True)
        if not isinstance(data, dict):
            return jsonify({"error": "Expected JSON object"}), 400
        # Merge with existing so keys not managed by the UI are preserved
        try:
            with open(_CONFIG_PATH, encoding="utf-8") as f:
                existing = yaml.safe_load(f) or {}
        except Exception:
            existing = {}
        existing.update(data)
        for k in ("paths", "contact"):
            existing.pop(k, None)
        with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.dump(existing, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        _config_module._cfg_cache = None
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── Flask routes — roles ──────────────────────────────────────────────────────

@app.route("/api/roles")
def api_roles():
    try:
        cfg = load_config()
        import scraper
        roles = scraper.extract_roles_from_resume(cfg)
        return jsonify({"roles": roles})
    except Exception as exc:
        return jsonify({"error": str(exc), "roles": []}), 200


# ── Flask routes — agent subprocess ──────────────────────────────────────────

@app.route("/api/agent/start", methods=["POST"])
def api_agent_start():
    global _agent_proc
    with _agent_lock:
        if _agent_proc and _agent_proc.poll() is None:
            return jsonify({"error": "already running", "pid": _agent_proc.pid})
        _agent_proc = subprocess.Popen(
            [sys.executable, str(_MAIN_PY)],
            cwd=str(_MAIN_PY.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return jsonify({"pid": _agent_proc.pid})


@app.route("/api/agent/stop", methods=["POST"])
def api_agent_stop():
    global _agent_proc
    with _agent_lock:
        if not _agent_proc or _agent_proc.poll() is not None:
            return jsonify({"ok": True, "message": "not running"})
        _agent_proc.terminate()
        try:
            _agent_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _agent_proc.kill()
        return jsonify({"ok": True})


@app.route("/api/agent/status")
def api_agent_status():
    if _agent_proc is None:
        return jsonify({"running": False, "pid": None})
    running = _agent_proc.poll() is None
    return jsonify({"running": running, "pid": _agent_proc.pid if running else None})


# ── Flask routes — log stream (SSE) ──────────────────────────────────────────

@app.route("/api/log/stream")
def api_log_stream():
    def generate():
        try:
            cfg      = load_config()
            log_path = cfg["paths"]["agent_dir"] / "agent.log"
        except Exception as exc:
            yield "data: " + json.dumps(f"[error] Could not load config: {exc}") + _SSE_SEP
            return

        # Send last 100 lines immediately on connect
        if log_path.exists():
            try:
                lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
                for line in lines[-100:]:
                    yield "data: " + json.dumps(line) + _SSE_SEP
            except Exception as exc:
                yield "data: " + json.dumps(f"[error reading log] {exc}") + _SSE_SEP
        else:
            yield "data: " + json.dumps("[waiting for agent.log — will appear once the agent runs]") + _SSE_SEP

        # Tail new content
        offset   = log_path.stat().st_size if log_path.exists() else 0
        last_ka  = time.time()

        while True:
            if log_path.exists():
                try:
                    size = log_path.stat().st_size
                    if size > offset:
                        with open(log_path, encoding="utf-8", errors="replace") as f:
                            f.seek(offset)
                            chunk = f.read()
                        offset = size
                        for line in chunk.splitlines():
                            if line.strip():
                                yield "data: " + json.dumps(line) + _SSE_SEP
                except Exception:
                    pass
            else:
                time.sleep(1)
            time.sleep(0.4)
            if time.time() - last_ka > 15:
                yield ": keepalive" + _SSE_SEP
                last_ka = time.time()

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Job Hunt Dashboard")
    print("  → http://localhost:5000")
    print("  Press Ctrl+C to stop.\n")
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
