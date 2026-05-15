"""
dashboard.py — local web dashboard for job application tracking and agent control.

Tab 1 — Dashboard : four sub-tabs (Overview / Applied / Interviews / Rejected),
                    Excel import banner, download button.
Tab 2 — Job Hunt Agent : file uploads (resume + cover letter), 4-step wizard,
                          config editor, role extractor, live log.

Run:  python dashboard.py
Open: http://localhost:5000
"""

import io
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import yaml
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template_string, request, send_file

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent))
from config import load_config
import config as _config_module

app = Flask(__name__)

_CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"
_MAIN_PY     = Path(__file__).resolve().parent / "main.py"
_UPLOADS_DIR = Path(__file__).resolve().parent / "uploads"
_SSE_SEP     = chr(10) + chr(10)

_agent_proc: "subprocess.Popen | None" = None
_agent_lock  = threading.Lock()

# ── Pipeline state ─────────────────────────────────────────────────────────────
# step: 0=idle 1=scraping 2=scraped 3=matching 4=matched 5=applying 6=done -1=error
_pipeline: dict = {
    "step": 0, "jobs_count": 0, "matched_count": 0,
    "error": None, "results": [],
}
_pipeline_lock   = threading.Lock()
_pipeline_thread: "threading.Thread | None" = None


def _pipeline_set(**kw):
    with _pipeline_lock:
        _pipeline.update(kw)


# ── HTML ──────────────────────────────────────────────────────────────────────

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Job Hunt</title>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
       background: #0d1117; color: #e2e8f0; min-height: 100vh; }
a { color: inherit; }
.container { max-width: 1500px; margin: 0 auto; padding: 24px 20px; }
header { margin-bottom: 0; }
.header-row { display: flex; align-items: center; justify-content: space-between;
  flex-wrap: wrap; gap: 12px; margin-bottom: 16px; }
header h1 { font-size: 1.45rem; font-weight: 700; color: #f8fafc; letter-spacing: -0.02em; }
.refresh-pill { font-size: 0.75rem; color: #64748b; background: #161b22;
  border: 1px solid #21262d; border-radius: 20px; padding: 5px 12px; }
.refresh-pill span { color: #38bdf8; font-weight: 600; }

/* ── Main tab nav ── */
.tab-nav { display: flex; gap: 2px; border-bottom: 1px solid #21262d; margin-bottom: 24px; }
.tab-btn { background: none; border: none; border-bottom: 2px solid transparent;
  color: #8b949e; padding: 9px 18px; font-size: 0.88rem; cursor: pointer;
  margin-bottom: -1px; transition: color 0.15s, border-color 0.15s; }
.tab-btn:hover { color: #c9d1d9; }
.tab-btn.active { color: #f0f6fc; border-bottom-color: #58a6ff; font-weight: 600; }

/* ── Sub-tab nav (dashboard) ── */
.sub-nav { display: flex; gap: 4px; margin-bottom: 18px; flex-wrap: wrap; align-items: center; }
.sub-btn { background: #161b22; border: 1px solid #21262d; color: #8b949e;
  padding: 6px 16px; border-radius: 6px; font-size: 0.83rem; cursor: pointer;
  transition: background 0.15s; white-space: nowrap; }
.sub-btn:hover { background: #1c2128; color: #c9d1d9; }
.sub-btn.active { background: #1f6feb; border-color: #388bfd; color: #fff; font-weight: 600; }
.sub-btn .cnt { font-size: 0.73rem; opacity: 0.8; margin-left: 4px; }
.sub-spacer { flex: 1; }

/* ── Import banner ── */
.import-banner { background: #1c2128; border: 1px dashed #30363d; border-radius: 10px;
  padding: 20px 24px; margin-bottom: 20px; display: flex; align-items: center;
  gap: 16px; flex-wrap: wrap; }
.import-banner p { color: #8b949e; font-size: 0.88rem; flex: 1; min-width: 200px; }

/* ── Summary cards ── */
.cards { display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; margin-bottom: 20px; }
@media (max-width: 900px) { .cards { grid-template-columns: repeat(3, 1fr); } }
.card { background: #161b22; border: 1px solid #21262d; border-radius: 10px; padding: 16px 18px; }
.card .n   { font-size: 2.1rem; font-weight: 700; line-height: 1; margin-bottom: 4px; }
.card .lbl { font-size: 0.69rem; text-transform: uppercase; letter-spacing: 0.07em; color: #64748b; }
.c-total     .n { color: #f1f5f9; }
.c-applied   .n { color: #60a5fa; }
.c-interview .n { color: #34d399; }
.c-rejected  .n { color: #f87171; }
.c-offer     .n { color: #fbbf24; }

/* ── Filters ── */
.filters { background: #161b22; border: 1px solid #21262d; border-radius: 10px;
  padding: 14px 18px; margin-bottom: 18px;
  display: flex; flex-wrap: wrap; gap: 12px; align-items: flex-end; }
.fg { display: flex; flex-direction: column; gap: 5px; }
.fg label { font-size: 0.69rem; text-transform: uppercase; letter-spacing: 0.07em; color: #64748b; }
.fg input, .fg select { background: #0d1117; border: 1px solid #30363d; color: #e2e8f0;
  border-radius: 6px; padding: 7px 10px; font-size: 0.84rem;
  min-width: 130px; outline: none; transition: border-color 0.15s; }
.fg input:focus, .fg select:focus { border-color: #38bdf8; }
.fg.wide input { min-width: 210px; }
.btn-reset { background: #21262d; border: 1px solid #30363d; color: #8b949e;
  border-radius: 6px; padding: 7px 14px; font-size: 0.84rem;
  cursor: pointer; align-self: flex-end; transition: background 0.15s; }
.btn-reset:hover { background: #30363d; color: #e2e8f0; }

/* ── Table ── */
.table-wrap { background: #161b22; border: 1px solid #21262d; border-radius: 10px; overflow: hidden; }
.table-bar { padding: 10px 18px; font-size: 0.78rem; color: #64748b; border-bottom: 1px solid #21262d; }
.table-bar .error { color: #f87171; }
.scroll-x { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 0.84rem; }
thead th { padding: 11px 14px; text-align: left; font-size: 0.69rem;
  text-transform: uppercase; letter-spacing: 0.07em; color: #64748b;
  border-bottom: 1px solid #21262d; white-space: nowrap;
  cursor: pointer; user-select: none;
  background: #161b22; position: sticky; top: 0; z-index: 1; }
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
.s-hi { color: #34d399; } .s-mid { color: #fb923c; } .s-lo { color: #f87171; } .s-na { color: #64748b; }
.chip { display: inline-block; padding: 2px 8px; border-radius: 99px;
  font-size: 0.68rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; }
.ch-high { background: #0d2818; color: #34d399; }
.ch-medium { background: #2d1a06; color: #fb923c; }
.ch-low { background: #2d0f0f; color: #f87171; }
.ch-na { background: #1c2128; color: #64748b; }
.badge { display: inline-block; padding: 3px 9px; border-radius: 99px;
  font-size: 0.7rem; font-weight: 600; white-space: nowrap; }
.bs-applied     { background: #0c2a4a; color: #60a5fa; }
.bs-unconfirmed { background: #1c2128; color: #8b949e; }
.bs-scheduled   { background: #0d2818; color: #34d399; }
.bs-pending     { background: #2d1a06; color: #fb923c; }
.bs-rejected    { background: #2d0f0f; color: #f87171; }
.bs-offer       { background: #2d2006; color: #fbbf24; }
.bs-default     { background: #1c2128; color: #8b949e; }
.td-german { font-size: 0.79rem; color: #8b949e; }
.german-warn { color: #fb923c; }
.td-archive a { color: #a78bfa; font-size: 0.76rem; text-decoration: none; white-space: nowrap; }
.td-archive a:hover { text-decoration: underline; }
.empty-state { text-align: center; padding: 64px 20px; color: #484f58; }
.empty-state .icon { font-size: 2.8rem; margin-bottom: 10px; }

/* ── Panels ── */
.panel { background: #161b22; border: 1px solid #21262d; border-radius: 10px; overflow: hidden; }
.mt16 { margin-top: 16px; } .mb16 { margin-bottom: 16px; }
.panel-hd { padding: 11px 18px; border-bottom: 1px solid #21262d;
  display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.panel-title { font-weight: 600; font-size: 0.88rem; color: #f0f6fc; }
.panel-actions { display: flex; gap: 6px; align-items: center; }

/* ── Buttons ── */
.btn-sm { background: #21262d; border: 1px solid #30363d; color: #8b949e;
  border-radius: 6px; padding: 4px 10px; font-size: 0.77rem; cursor: pointer;
  transition: background 0.15s; white-space: nowrap; }
.btn-sm:hover { background: #30363d; color: #e2e8f0; }
.btn-primary { background: #1f6feb; border: 1px solid #388bfd; color: #fff;
  border-radius: 6px; padding: 7px 16px; font-size: 0.84rem; cursor: pointer;
  transition: background 0.15s; white-space: nowrap; }
.btn-primary:hover:not(:disabled) { background: #388bfd; }
.btn-primary:disabled { background: #1c2128; border-color: #30363d; color: #64748b; cursor: not-allowed; }
.btn-danger { background: #2d0f0f; border: 1px solid #7f1d1d; color: #f87171;
  border-radius: 6px; padding: 7px 16px; font-size: 0.84rem; cursor: pointer;
  transition: background 0.15s; white-space: nowrap; }
.btn-danger:hover:not(:disabled) { background: #450a0a; }
.btn-danger:disabled { background: #1c2128; border-color: #30363d; color: #64748b; cursor: not-allowed; }
.btn-link { display: inline-flex; align-items: center; gap: 5px; background: #1f6feb;
  border: 1px solid #388bfd; color: #fff; border-radius: 6px; padding: 7px 14px;
  font-size: 0.84rem; text-decoration: none; cursor: pointer; transition: background 0.15s; }
.btn-link:hover { background: #388bfd; }

/* ── Wizard step indicator ── */
.wz-indicator { display: flex; align-items: center; margin-bottom: 20px; padding: 14px 18px;
  background: #161b22; border: 1px solid #21262d; border-radius: 10px; }
.wz-ind-step { display: flex; align-items: center; gap: 8px;
  font-size: 0.82rem; color: #4b5563; flex-shrink: 0; }
.wz-ind-step.active { color: #f0f6fc; }
.wz-ind-step.done   { color: #34d399; }
.wz-num { width: 22px; height: 22px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-size: 0.72rem; font-weight: 700;
  background: #21262d; border: 1px solid #30363d; flex-shrink: 0; }
.wz-ind-step.active .wz-num { background: #1f6feb; border-color: #388bfd; color: #fff; }
.wz-ind-step.done   .wz-num { background: #0d2818; border-color: #34d399; color: #34d399; }
.wz-sep { flex: 1; height: 1px; background: #21262d; min-width: 16px; max-width: 48px; margin: 0 6px; }

/* ── Upload panel ── */
.upload-row { display: flex; gap: 16px; padding: 16px 18px; flex-wrap: wrap; }
.upload-slot { flex: 1; min-width: 160px; border: 1px dashed #30363d; border-radius: 8px;
  padding: 16px 14px; display: flex; flex-direction: column; align-items: center;
  gap: 5px; cursor: pointer; transition: border-color 0.15s, background 0.15s;
  position: relative; text-align: center; }
.upload-slot:hover { border-color: #58a6ff; background: #0c1a2e; }
.upload-slot.uploaded { border-color: #34d399; background: #0a1f14; }
.upload-icon  { font-size: 1.6rem; line-height: 1; }
.upload-label { font-size: 0.83rem; font-weight: 600; color: #c9d1d9; }
.upload-sub   { font-size: 0.72rem; color: #64748b; }
.upload-status { font-size: 0.74rem; min-height: 16px; margin-top: 2px; word-break: break-all; }
.upload-slot input[type="file"] { position: absolute; inset: 0; opacity: 0; cursor: pointer; }

/* ── Progress bar ── */
.progress-bar { height: 4px; background: #21262d; border-radius: 2px; overflow: hidden; }
.progress-fill { height: 100%; background: #1f6feb; border-radius: 2px; width: 0;
  transition: width 0.4s ease; }
.progress-fill.indeterminate { width: 40%; animation: indeterminate 1.4s ease-in-out infinite; }
@keyframes indeterminate { 0% { transform: translateX(-150%); } 100% { transform: translateX(350%); } }

/* ── Job cards ── */
.job-cards { display: flex; flex-direction: column; gap: 8px; }
.job-card { background: #1c2128; border: 1px solid #21262d; border-radius: 8px; padding: 12px 14px; }
.job-card.matched { border-color: #1e3a5f; }
.jc-title { font-weight: 600; font-size: 0.88rem; color: #f1f5f9; margin-bottom: 3px; }
.jc-meta  { font-size: 0.79rem; color: #8b949e; margin-bottom: 4px; }

/* ── Agent grid ── */
.agent-grid { display: grid; grid-template-columns: 1fr 2fr; gap: 16px; align-items: start; }
@media (max-width: 960px) { .agent-grid { grid-template-columns: 1fr; } }

/* ── Role chips ── */
.roles-area { padding: 16px 18px; display: flex; flex-wrap: wrap; gap: 8px; min-height: 72px; }
.role-chip { display: inline-flex; align-items: center; gap: 5px; padding: 5px 14px; border-radius: 99px;
  background: #1c2128; border: 1px solid #30363d; color: #8b949e;
  font-size: 0.83rem; cursor: pointer; user-select: none; transition: all 0.15s; }
.role-chip:hover { border-color: #58a6ff; color: #c9d1d9; }
.role-chip.selected { background: #0c2a4a; border-color: #58a6ff; color: #58a6ff; font-weight: 600; }
.role-chip.custom { border-color: #2d6a4f; color: #7ecba1; }
.role-chip.custom:hover { border-color: #4caf82; color: #a3d9b8; }
.role-chip.custom.selected { background: #0d2e1e; border-color: #4caf82; color: #4caf82; font-weight: 600; }
.role-chip-rm { background: none; border: none; color: inherit; font-size: 0.78rem; line-height: 1;
  padding: 0 0 0 2px; cursor: pointer; opacity: 0.6; }
.role-chip-rm:hover { opacity: 1; }
.roles-add-row { display: flex; align-items: center; gap: 6px; padding: 6px 18px 14px; }
.roles-add-row input { flex: 1; background: #1c2128; border: 1px solid #30363d; border-radius: 6px;
  color: #c9d1d9; padding: 5px 10px; font-size: 0.83rem; outline: none; }
.roles-add-row input:focus { border-color: #4caf82; }
.roles-add-row .btn-add-role { background: #0d2e1e; border: 1px solid #2d6a4f; color: #4caf82;
  border-radius: 6px; padding: 5px 14px; font-size: 0.84rem; cursor: pointer; transition: all 0.15s; }
.roles-add-row .btn-add-role:hover { background: #174834; border-color: #4caf82; }
.loading-text { color: #64748b; font-size: 0.84rem; font-style: italic; align-self: center; }
.error-text   { color: #f87171; font-size: 0.84rem; align-self: center; }

/* ── Config form ── */
.config-form { padding: 14px 18px; display: flex; flex-direction: column; gap: 9px;
  max-height: 640px; overflow-y: auto; }
.cfg-section { font-size: 0.67rem; text-transform: uppercase; letter-spacing: 0.08em; color: #4b5563;
  border-bottom: 1px solid #21262d; padding-bottom: 3px; margin-top: 4px; }
.cfg-row { display: flex; flex-direction: column; gap: 4px; }
.cfg-row label, .cfg-col label { font-size: 0.69rem; text-transform: uppercase; letter-spacing: 0.07em; color: #64748b; }
.cfg-row input[type='text'], .cfg-row input[type='number'],
.cfg-col input[type='text'], .cfg-col input[type='number'] {
  background: #0d1117; border: 1px solid #30363d; color: #e2e8f0;
  border-radius: 6px; padding: 6px 10px; font-size: 0.83rem; outline: none;
  transition: border-color 0.15s; width: 100%; }
.cfg-row input:focus, .cfg-col input:focus { border-color: #58a6ff; }
.cfg-2col { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.cfg-col  { display: flex; flex-direction: column; gap: 4px; }
.tag-list { display: flex; flex-wrap: wrap; gap: 5px; margin-bottom: 5px; min-height: 24px; }
.tag-chip { display: inline-flex; align-items: center; gap: 3px;
  background: #1c2128; border: 1px solid #30363d; color: #c9d1d9;
  padding: 2px 8px; border-radius: 6px; font-size: 0.78rem; }
.tag-rm { background: none; border: none; color: #64748b; cursor: pointer;
  padding: 0 1px; font-size: 0.9rem; line-height: 1; transition: color 0.1s; }
.tag-rm:hover { color: #f87171; }
.tag-input-row { display: flex; gap: 5px; }
.tag-input-row input { flex: 1; background: #0d1117; border: 1px solid #30363d; color: #e2e8f0;
  border-radius: 6px; padding: 5px 8px; font-size: 0.81rem; outline: none; transition: border-color 0.15s; }
.tag-input-row input:focus { border-color: #58a6ff; }
.toggle-rows { display: flex; flex-direction: column; gap: 7px; }
.toggle-row  { display: flex; align-items: center; justify-content: space-between; padding: 1px 0; }
.t-label     { font-size: 0.84rem; color: #c9d1d9; }
.t-switch    { position: relative; width: 36px; height: 20px; flex-shrink: 0; display: inline-block; }
.t-switch input  { opacity: 0; width: 0; height: 0; position: absolute; }
.t-track { position: absolute; inset: 0; background: #374151; border-radius: 10px;
  cursor: pointer; transition: background 0.2s; }
.t-switch input:checked + .t-track { background: #2563eb; }
.t-thumb { position: absolute; top: 2px; left: 2px; width: 16px; height: 16px;
  background: #fff; border-radius: 50%; transition: left 0.2s; pointer-events: none; }
.t-switch input:checked + .t-track .t-thumb { left: 18px; }

/* ── Agent control ── */
.agent-ctrl-row { padding: 14px 18px; display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
.agent-status { display: flex; align-items: center; gap: 7px; font-size: 0.84rem; color: #8b949e; }
.status-dot { width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0; }
.status-dot.stopped { background: #4b5563; }
.status-dot.running { background: #34d399; animation: blink 1.4s ease-in-out infinite; }
@keyframes blink { 0%, 100% { opacity: 1; } 50% { opacity: 0.25; } }

/* ── Log viewer ── */
.log-viewer { background: #010409; margin: 0 16px 16px; border-radius: 6px;
  padding: 10px 14px; height: 320px; overflow-y: auto;
  font-family: 'Consolas', 'Courier New', monospace; font-size: 0.76rem; line-height: 1.6; }
.log-line { white-space: pre-wrap; word-break: break-all; padding: 1px 0; }
.log-info    { color: #58a6ff; }
.log-warning { color: #fb923c; }
.log-error   { color: #f87171; font-weight: 600; }
.log-default { color: #8b949e; }

/* ── Toast ── */
.toast { position: fixed; bottom: 24px; right: 24px; z-index: 999;
  background: #1e293b; border: 1px solid #334155; color: #e2e8f0;
  padding: 9px 16px; border-radius: 8px; font-size: 0.84rem; max-width: 320px;
  opacity: 0; transform: translateY(6px); transition: all 0.2s; pointer-events: none; }
.toast.show { opacity: 1; transform: translateY(0); }
.toast-error { border-color: #f87171 !important; color: #fca5a5 !important; }

/* ── Pipeline step tables (Step 2 / Step 3) ── */
.pip-toolbar { display: flex; align-items: center; gap: 8px; padding: 10px 18px;
  border-bottom: 1px solid #21262d; flex-wrap: wrap; }
.pip-count { font-size: 0.79rem; color: #64748b; white-space: nowrap; }
.pip-search { flex: 1; min-width: 160px; background: #0d1117; border: 1px solid #30363d;
  border-radius: 6px; color: #c9d1d9; padding: 5px 10px; font-size: 0.82rem; outline: none; }
.pip-search:focus { border-color: #58a6ff; }
.pip-table-wrap { overflow-x: auto; max-height: 420px; overflow-y: auto; }
.pip-table { width: 100%; border-collapse: collapse; font-size: 0.81rem; }
.pip-table thead th { padding: 7px 10px; text-align: left; font-size: 0.66rem;
  text-transform: uppercase; letter-spacing: 0.06em; color: #64748b;
  border-bottom: 1px solid #21262d; white-space: nowrap; background: #161b22;
  position: sticky; top: 0; z-index: 1; }
.pip-table th.cb-col { width: 28px; }
.pip-table tbody tr { border-bottom: 1px solid #0d1117; transition: background 0.1s; }
.pip-table tbody tr:last-child { border-bottom: none; }
.pip-table tbody tr:hover { background: #1c2128; }
.pip-table td { padding: 8px 10px; vertical-align: middle; }
.pip-table td.td-title { font-weight: 600; color: #f1f5f9; max-width: 200px; }
.pip-table td.td-summary { font-size: 0.76rem; color: #8b949e; max-width: 220px; }
.pip-table .td-actions { white-space: nowrap; }
.pip-footer { padding: 10px 18px; display: flex; gap: 8px; align-items: center;
  border-top: 1px solid #21262d; flex-wrap: wrap; }
/* Match score badges */
.badge-score { display: inline-block; padding: 2px 8px; border-radius: 99px;
  font-size: 0.78rem; font-weight: 700; white-space: nowrap; }
.badge-score-hi  { background: #0d2e1e; color: #34d399; border: 1px solid #2d6a4f; }
.badge-score-mid { background: #0c2a4a; color: #58a6ff; border: 1px solid #1e3a5f; }
.badge-score-lo  { background: #2d1a06; color: #fb923c; border: 1px solid #7c2d12; }
/* Interview chance badges */
.badge-chance { display: inline-block; padding: 2px 8px; border-radius: 99px;
  font-size: 0.76rem; font-weight: 600; white-space: nowrap; }
.badge-chance-high   { background: #0d2e1e; color: #34d399; border: 1px solid #2d6a4f; }
.badge-chance-medium { background: #2d1a06; color: #fb923c; border: 1px solid #7c2d12; }
.badge-chance-low    { background: #450a0a; color: #f87171; border: 1px solid #7f1d1d; }
/* German level badge */
.badge-de { display: inline-block; padding: 2px 7px; border-radius: 99px;
  font-size: 0.73rem; white-space: nowrap;
  background: #161b22; color: #8b949e; border: 1px solid #30363d; }

/* ── Manual interview / task tables ── */
.man-section { background: #161b22; border: 1px solid #21262d; border-radius: 10px;
  overflow: hidden; margin-bottom: 20px; }
.man-hd { padding: 10px 18px; display: flex; align-items: center;
  justify-content: space-between; }
.man-hd-purple { background: #2d1b69; border-bottom: 1px solid #4c1d95; }
.man-hd-blue   { background: #0c2a4a; border-bottom: 1px solid #1e40af; }
.man-hd-title  { font-weight: 700; font-size: 0.88rem; color: #fff;
  letter-spacing: 0.05em; text-transform: uppercase; }
.man-table-wrap { overflow-x: auto; }
.man-table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
.man-table thead th { padding: 8px 12px; text-align: left; font-size: 0.67rem;
  text-transform: uppercase; letter-spacing: 0.06em; color: #64748b;
  border-bottom: 1px solid #21262d; white-space: nowrap; background: #161b22; }
.man-table tbody tr { border-bottom: 1px solid #0d1117; transition: background 0.1s; }
.man-table tbody tr:last-child { border-bottom: none; }
.man-table tbody tr:hover { background: #1c2128; }
.man-table td { padding: 9px 12px; vertical-align: middle; }
.man-table tr.past-row td { color: #484f58; }
.man-table tr.past-row a  { color: #484f58 !important; }
.man-table tr.done-row td { text-decoration: line-through; color: #484f58; }
.man-table tr.done-row .badge { opacity: 0.45; }
.man-row-btns { display: flex; gap: 4px; align-items: center; }
.btn-icon { background: none; border: none; cursor: pointer;
  padding: 3px 6px; border-radius: 4px; font-size: 0.9rem;
  transition: background 0.15s; line-height: 1; color: #64748b; }
.btn-icon:hover { background: #30363d; color: #c9d1d9; }
.btn-icon.del:hover { background: #450a0a; color: #f87171; }
.pri-done   { background: #1c2128; color: #64748b; }
.pri-high   { background: #450a0a; color: #f87171; }
.pri-medium { background: #2d1a06; color: #fb923c; }
.pri-normal { background: #0d2818; color: #34d399; }

/* ── Modal ── */
.modal-backdrop { position: fixed; inset: 0; background: rgba(0,0,0,0.65);
  z-index: 200; display: flex; align-items: center; justify-content: center; }
.modal-box { background: #161b22; border: 1px solid #30363d; border-radius: 12px;
  width: 500px; max-width: calc(100vw - 32px);
  max-height: calc(100vh - 60px); overflow-y: auto; box-shadow: 0 20px 60px rgba(0,0,0,0.5); }
.modal-hd { padding: 14px 18px; border-bottom: 1px solid #21262d;
  display: flex; align-items: center; justify-content: space-between; position: sticky; top: 0;
  background: #161b22; z-index: 1; }
.modal-title { font-weight: 700; font-size: 0.92rem; color: #f0f6fc; }
.modal-close { background: none; border: none; color: #64748b; cursor: pointer;
  font-size: 1.3rem; line-height: 1; padding: 2px 6px; border-radius: 4px; }
.modal-close:hover { color: #e2e8f0; background: #21262d; }
.modal-body { padding: 16px 18px; display: flex; flex-direction: column; gap: 10px; }
.modal-footer { padding: 12px 18px; border-top: 1px solid #21262d;
  display: flex; justify-content: flex-end; gap: 8px;
  position: sticky; bottom: 0; background: #161b22; }
.mf { display: flex; flex-direction: column; gap: 4px; }
.mf label { font-size: 0.69rem; text-transform: uppercase; letter-spacing: 0.07em; color: #64748b; }
.mf input, .mf select, .mf textarea {
  background: #0d1117; border: 1px solid #30363d; color: #e2e8f0;
  border-radius: 6px; padding: 7px 10px; font-size: 0.83rem; outline: none;
  transition: border-color 0.15s; width: 100%; font-family: inherit; }
.mf input:focus, .mf select:focus, .mf textarea:focus { border-color: #58a6ff; }
.mf textarea { resize: vertical; min-height: 64px; }
.mf-2col { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }

/* ── Overview event / task columns ── */
.ov-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 20px; }
@media (max-width: 900px) { .ov-grid { grid-template-columns: 1fr; } }
.ov-section { background: #161b22; border: 1px solid #21262d; border-radius: 10px; overflow: hidden; }
.ov-section-hd { padding: 10px 16px; border-bottom: 1px solid #21262d;
  font-weight: 600; font-size: 0.83rem; color: #f0f6fc; }
.ov-list { display: flex; flex-direction: column; }
.ov-item { padding: 10px 16px; border-bottom: 1px solid #0d1117;
  display: flex; align-items: flex-start; gap: 10px; }
.ov-item:last-child { border-bottom: none; }
.ov-item-main { flex: 1; min-width: 0; }
.ov-company { font-weight: 600; font-size: 0.84rem; color: #f1f5f9;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ov-role { font-size: 0.78rem; color: #8b949e;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ov-item-right { text-align: right; flex-shrink: 0;
  display: flex; flex-direction: column; align-items: flex-end; gap: 4px; }
.ov-date { font-size: 0.75rem; color: #64748b; }
.ov-days { font-size: 0.75rem; font-weight: 600; color: #fb923c; }
.ov-empty { padding: 28px 16px; text-align: center; color: #484f58; font-size: 0.82rem; }
.bs-call  { background: #0d2818; color: #34d399; }
.bs-tech  { background: #0c2a4a; color: #60a5fa; }
.bs-final { background: #1e0d40; color: #a78bfa; }
.btn-advance { background: #21262d; border: 1px solid #30363d; color: #8b949e;
  border-radius: 6px; padding: 3px 10px; font-size: 0.75rem; cursor: pointer;
  transition: background 0.15s; white-space: nowrap; }
.btn-advance:hover { background: #30363d; color: #e2e8f0; }
.btn-advance:disabled { opacity: 0.4; cursor: not-allowed; }

/* ── Shared ── */
.hidden { display: none !important; }
#spinner-overlay { position: fixed; inset: 0; background: rgba(13,17,23,0.75);
  display: flex; align-items: center; justify-content: center; z-index: 50; }
.spinner { width: 38px; height: 38px; border: 3px solid #21262d;
  border-top-color: #38bdf8; border-radius: 50%; animation: spin 0.75s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
@keyframes pulse-text { 0%, 100% { opacity: 1; } 50% { opacity: 0.45; } }
.scraping-pulse { animation: pulse-text 1.3s ease-in-out infinite; }
.btn-stop { background: #450a0a; border: 1px solid #f87171; color: #f87171;
  border-radius: 6px; padding: 7px 16px; font-size: 0.84rem; cursor: pointer;
  animation: pulse-text 1.8s ease-in-out infinite; white-space: nowrap; }
.btn-stop:hover { background: #7f1d1d; border-color: #fca5a5; color: #fca5a5; }
.btn-stop:disabled { opacity: 0.5; cursor: not-allowed; animation: none; }
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

  <!-- ═══ DASHBOARD TAB ═══ -->
  <div id="tab-dashboard" class="tab-content">

    <!-- Import banner — hidden once data exists -->
    <div id="import-banner" class="import-banner hidden">
      <p>No data yet — import your existing tracker to get started.</p>
      <button class="btn-primary" onclick="triggerImportExcel()">&#128194; Import Excel Tracker</button>
      <input type="file" id="import-excel-input" accept=".xlsx" class="hidden" onchange="importExcel(this)">
    </div>

    <!-- Sub-tab navigation -->
    <div class="sub-nav">
      <button class="sub-btn active" data-sub="overview"    onclick="showSubTab('overview')">Overview</button>
      <button class="sub-btn"        data-sub="applied"     onclick="showSubTab('applied')">Applied<span class="cnt" id="cnt-Applied"></span></button>
      <button class="sub-btn"        data-sub="interviews"  onclick="showSubTab('interviews')">Interviews<span class="cnt" id="cnt-Interviews"></span></button>
      <button class="sub-btn"        data-sub="rejected"    onclick="showSubTab('rejected')">Rejected<span class="cnt" id="cnt-Rejected"></span></button>
      <div class="sub-spacer"></div>
      <a class="btn-link" href="/api/download/tracker" id="dl-btn">&#11015; Download Tracker</a>
    </div>

    <!-- Sub-tab: Overview -->
    <div id="sub-overview" class="sub-content">
      <div class="cards">
        <div class="card c-total">    <div class="n" id="s-total">—</div>    <div class="lbl">Total</div></div>
        <div class="card c-applied">  <div class="n" id="s-applied">—</div>  <div class="lbl">Applied</div></div>
        <div class="card c-interview"><div class="n" id="s-interviews">—</div><div class="lbl">Interviews</div></div>
        <div class="card c-rejected"> <div class="n" id="s-rejected">—</div> <div class="lbl">Rejected</div></div>
        <div class="card c-offer">    <div class="n" id="s-offers">—</div>   <div class="lbl">Offers</div></div>
      </div>
      <div class="ov-grid">
        <div class="ov-section">
          <div class="ov-section-hd">&#128197; Upcoming Events</div>
          <div id="ov-events" class="ov-list"><div class="ov-empty">Loading…</div></div>
        </div>
        <div class="ov-section">
          <div class="ov-section-hd">&#9889; Priority Tasks</div>
          <div id="ov-tasks" class="ov-list"><div class="ov-empty">Loading…</div></div>
        </div>
      </div>

      <!-- Upcoming Interviews (manually managed) -->
      <div class="man-section">
        <div class="man-hd man-hd-purple">
          <span class="man-hd-title">&#128197; Upcoming Interviews</span>
          <button class="btn-sm" onclick="openInterviewModal()">+ Add Interview</button>
        </div>
        <div class="man-table-wrap">
          <table class="man-table" id="interviews-table">
            <thead><tr>
              <th>Date</th><th>Company</th><th>Role</th>
              <th>Interview Type</th><th>Time (Berlin)</th><th>Format</th>
              <th style="width:72px"></th>
            </tr></thead>
            <tbody id="interviews-tbody"></tbody>
          </table>
          <div id="interviews-empty" class="ov-empty hidden">No upcoming interviews.</div>
        </div>
      </div>

      <!-- Priority To-Do (manually managed) -->
      <div class="man-section">
        <div class="man-hd man-hd-blue">
          <span class="man-hd-title">&#9989; Priority To-Do</span>
          <button class="btn-sm" onclick="openTaskModal()">+ Add Task</button>
        </div>
        <div class="man-table-wrap">
          <table class="man-table" id="tasks-table">
            <thead><tr>
              <th style="width:32px">#</th><th>Priority</th><th>Company</th>
              <th>Action</th><th>Deadline</th><th>Status</th>
              <th style="width:72px"></th>
            </tr></thead>
            <tbody id="tasks-tbody"></tbody>
          </table>
          <div id="tasks-empty" class="ov-empty hidden">No tasks yet.</div>
        </div>
      </div>

    </div>

    <!-- Sub-tabs: Applied / Interviews / Rejected share same filter+table structure -->
    <div id="sub-applied"    class="sub-content hidden"></div>
    <div id="sub-interviews" class="sub-content hidden"></div>
    <div id="sub-rejected"   class="sub-content hidden"></div>

  </div><!-- /tab-dashboard -->

  <!-- ═══ AGENT TAB ═══ -->
  <div id="tab-agent" class="tab-content hidden">

    <!-- Upload Files (resume + cover letter only) -->
    <div class="panel mb16">
      <div class="panel-hd"><span class="panel-title">&#128194; Upload Files</span></div>
      <div class="upload-row">
        <div class="upload-slot" id="us-resume">
          <div class="upload-icon">&#128196;</div>
          <div class="upload-label">Resume</div>
          <div class="upload-sub">PDF or DOCX</div>
          <input type="file" accept=".pdf,.docx" onchange="uploadFile('resume', this)">
          <div class="upload-status" id="ust-resume"></div>
        </div>
        <div class="upload-slot" id="us-cover_letter">
          <div class="upload-icon">&#128221;</div>
          <div class="upload-label">Cover Letter</div>
          <div class="upload-sub">PDF or DOCX</div>
          <input type="file" accept=".pdf,.docx" onchange="uploadFile('cover_letter', this)">
          <div class="upload-status" id="ust-cover_letter"></div>
        </div>
      </div>
    </div>

    <!-- Wizard step indicator -->
    <div class="wz-indicator mb16">
      <div class="wz-ind-step active" id="step-ind-1"><div class="wz-num">1</div><span>Scrape</span></div>
      <div class="wz-sep"></div>
      <div class="wz-ind-step" id="step-ind-2"><div class="wz-num">2</div><span>Review</span></div>
      <div class="wz-sep"></div>
      <div class="wz-ind-step" id="step-ind-3"><div class="wz-num">3</div><span>Match</span></div>
      <div class="wz-sep"></div>
      <div class="wz-ind-step" id="step-ind-4"><div class="wz-num">4</div><span>Apply</span></div>
    </div>

    <!-- Step 1: Scrape -->
    <div id="wz-1" class="panel mb16">
      <div class="panel-hd"><span class="panel-title">Step 1 — Scrape Jobs</span></div>
      <div style="padding:20px 18px;">
        <p style="color:#8b949e;font-size:0.84rem;margin-bottom:16px;">
          Searches LinkedIn for jobs posted in the last 24 h matching your resume.
        </p>
        <button id="btn-scrape" class="btn-primary" onclick="startScrape()">&#128270; Start Scraping</button>
        <div id="scrape-progress" class="hidden" style="margin-top:14px;">
          <div class="progress-bar"><div class="progress-fill indeterminate"></div></div>
          <div id="scrape-msg" style="margin-top:8px;font-size:0.8rem;color:#64748b;"></div>
        </div>
      </div>
    </div>

    <!-- Step 2: Review -->
    <div id="wz-2" class="panel mb16 hidden">
      <div class="panel-hd">
        <span class="panel-title">Step 2 — Review Scraped Jobs</span>
      </div>
      <div class="pip-toolbar">
        <span id="scraped-count" class="pip-count"></span>
        <input id="scraped-search" class="pip-search" type="text" placeholder="Filter by title, company, location…"
               oninput="filterScrapedJobs()">
      </div>
      <div class="pip-table-wrap">
        <table class="pip-table" id="scraped-table">
          <thead>
            <tr>
              <th>Title</th><th>Company</th><th>Location</th><th>Source</th><th>Posted</th><th>Actions</th>
            </tr>
          </thead>
          <tbody id="scraped-tbody"></tbody>
        </table>
      </div>
      <div class="pip-footer">
        <button class="btn-primary" onclick="goToMatch()">Match &amp; Filter &#8594;</button>
      </div>
    </div>

    <!-- Step 3: Match -->
    <div id="wz-3" class="panel mb16 hidden">
      <div class="panel-hd">
        <span class="panel-title">Step 3 — Match &amp; Filter</span>
        <button id="btn-match" class="btn-primary btn-sm" onclick="startMatch()">&#129302; Run Matching</button>
      </div>
      <div id="match-progress" class="hidden" style="padding:14px 18px 0;">
        <div class="progress-bar"><div class="progress-fill indeterminate"></div></div>
        <div id="match-msg" style="margin-top:8px;font-size:0.8rem;color:#64748b;"></div>
      </div>
      <div id="match-empty" style="padding:18px;color:#64748b;font-size:0.84rem;">
        Click <strong>Run Matching</strong> to score scraped jobs with Claude AI.
      </div>
      <div id="matched-table-wrap" class="hidden">
        <div class="pip-toolbar">
          <span id="matched-count" class="pip-count"></span>
          <input id="matched-search" class="pip-search" type="text" placeholder="Filter by title, company, location…"
                 oninput="filterMatchedJobs()">
          <button class="btn-sm btn-primary" onclick="applySelected()">&#9654; Apply Selected</button>
          <button class="btn-sm btn-primary" onclick="applyAll()">&#9654; Apply All</button>
        </div>
        <div class="pip-table-wrap">
          <table class="pip-table" id="matched-table">
            <thead>
              <tr>
                <th class="cb-col"><input type="checkbox" id="matched-select-all" onchange="toggleSelectAll(this)"></th>
                <th>Title</th><th>Company</th><th>Location</th>
                <th>Match %</th><th>Chance</th><th>German</th><th>Summary</th><th>Actions</th>
              </tr>
            </thead>
            <tbody id="matched-tbody"></tbody>
          </table>
        </div>
        <div class="pip-footer">
          <button class="btn-sm btn-primary" onclick="applySelected()">&#9654; Apply Selected</button>
          <button class="btn-sm btn-primary" onclick="applyAll()">&#9654; Apply All</button>
          <button class="btn-sm" onclick="goToApply()">Continue to Apply &#8594;</button>
        </div>
      </div>
    </div>

    <!-- Step 4: Apply -->
    <div id="wz-4" class="panel mb16 hidden">
      <div class="panel-hd">
        <span class="panel-title">Step 4 — Apply</span>
        <button id="btn-apply-all" class="btn-primary" onclick="applyAll()">&#9654; Apply All</button>
      </div>
      <div style="padding:14px 18px;">
        <div id="apply-jobs-list" class="job-cards"></div>
        <div id="apply-progress" class="hidden" style="margin-top:14px;">
          <div class="progress-bar"><div class="progress-fill indeterminate"></div></div>
          <div id="apply-msg" style="margin-top:8px;font-size:0.8rem;color:#64748b;"></div>
        </div>
        <div id="apply-results" class="hidden" style="margin-top:16px;">
          <a href="/api/download/tracker" class="btn-link">&#11015; Download Updated Tracker</a>
        </div>
      </div>
    </div>

    <!-- Roles + Config -->
    <div class="agent-grid">
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
        <div class="roles-add-row">
          <input id="custom-role-input" type="text" placeholder="Add custom title…"
                 onkeydown="if(event.key==='Enter'){event.preventDefault();addCustomRole();}">
          <button class="btn-add-role" onclick="addCustomRole()">+ Add</button>
        </div>
      </div>
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
            <label>How far back to search</label>
            <select id="cfg-posted_limit" style="background:#0d1117;border:1px solid #30363d;color:#e2e8f0;border-radius:6px;padding:6px 10px;font-size:0.83rem;outline:none;width:100%;">
              <option value="1h">Last hour</option>
              <option value="24h" selected>Last 24 hours</option>
              <option value="week">Last week</option>
              <option value="month">Last month</option>
            </select>
          </div>
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
            <div class="toggle-row"><span class="t-label">Auto-confirm recruiter call</span>
              <label class="t-switch"><input id="cfg-auto_confirm_recruiter_call" type="checkbox"><div class="t-track"><div class="t-thumb"></div></div></label></div>
            <div class="toggle-row"><span class="t-label">Auto-confirm technical round</span>
              <label class="t-switch"><input id="cfg-auto_confirm_technical" type="checkbox"><div class="t-track"><div class="t-thumb"></div></div></label></div>
            <div class="toggle-row"><span class="t-label">Headless browser</span>
              <label class="t-switch"><input id="cfg-headless" type="checkbox"><div class="t-track"><div class="t-thumb"></div></div></label></div>
            <div class="toggle-row"><span class="t-label">Confirm before apply</span>
              <label class="t-switch"><input id="cfg-confirm_before_apply" type="checkbox"><div class="t-track"><div class="t-thumb"></div></div></label></div>
            <div class="toggle-row"><span class="t-label">Retry CAPTCHA as manual</span>
              <label class="t-switch"><input id="cfg-retry_captcha_as_manual" type="checkbox"><div class="t-track"><div class="t-thumb"></div></div></label></div>
          </div>
        </div>
      </div>
    </div>

    <!-- Live Log -->
    <div class="panel mt16">
      <div class="panel-hd">
        <span class="panel-title">Live Log</span>
        <button class="btn-sm" onclick="clearLog()">Clear</button>
      </div>
      <div id="log-viewer" class="log-viewer"></div>
    </div>

  </div><!-- /tab-agent -->
</div><!-- /container -->

<!-- ═══ INTERVIEW MODAL ═══ -->
<div id="modal-interview" class="modal-backdrop hidden">
  <div class="modal-box">
    <div class="modal-hd">
      <span class="modal-title" id="modal-interview-title">Add Interview</span>
      <button class="modal-close" onclick="closeModal('modal-interview')">&#215;</button>
    </div>
    <div class="modal-body">
      <input type="hidden" id="mi-id">
      <div class="mf-2col">
        <div class="mf"><label>Date</label><input id="mi-date" type="date"></div>
        <div class="mf"><label>Time (Berlin)</label><input id="mi-time" type="text" placeholder="e.g. 09:30 – 11:00"></div>
      </div>
      <div class="mf-2col">
        <div class="mf"><label>Company</label><input id="mi-company" type="text"></div>
        <div class="mf"><label>Role</label><input id="mi-role" type="text"></div>
      </div>
      <div class="mf"><label>Interview Type</label><input id="mi-type" type="text" placeholder="e.g. System Design Interview, Round 1"></div>
      <div class="mf"><label>Format</label><input id="mi-format" type="text" placeholder="e.g. Microsoft Teams, Google Meet, On-site"></div>
      <div class="mf"><label>Job URL</label><input id="mi-url" type="url" placeholder="https://"></div>
      <div class="mf"><label>Notes</label><textarea id="mi-notes" placeholder="Prep notes, contact, etc."></textarea></div>
    </div>
    <div class="modal-footer">
      <button class="btn-sm" onclick="closeModal('modal-interview')">Cancel</button>
      <button class="btn-primary" onclick="saveInterview()">Save</button>
    </div>
  </div>
</div>

<!-- ═══ TASK MODAL ═══ -->
<div id="modal-task" class="modal-backdrop hidden">
  <div class="modal-box">
    <div class="modal-hd">
      <span class="modal-title" id="modal-task-title">Add Task</span>
      <button class="modal-close" onclick="closeModal('modal-task')">&#215;</button>
    </div>
    <div class="modal-body">
      <input type="hidden" id="mt-id">
      <div class="mf-2col">
        <div class="mf"><label>Priority</label>
          <select id="mt-priority">
            <option value="HIGH">HIGH</option>
            <option value="MEDIUM">MEDIUM</option>
            <option value="NORMAL" selected>NORMAL</option>
            <option value="DONE">DONE</option>
          </select>
        </div>
        <div class="mf"><label>Company</label><input id="mt-company" type="text"></div>
      </div>
      <div class="mf"><label>Action</label><input id="mt-action" type="text" placeholder="What needs to be done"></div>
      <div class="mf-2col">
        <div class="mf"><label>Deadline</label><input id="mt-deadline" type="text" placeholder="ASAP / 27 May / This week"></div>
        <div class="mf"><label>Status</label><input id="mt-status" type="text" placeholder="e.g. Awaiting feedback"></div>
      </div>
      <div class="mf"><label>Notes</label><textarea id="mt-notes"></textarea></div>
    </div>
    <div class="modal-footer">
      <button class="btn-sm" onclick="closeModal('modal-task')">Cancel</button>
      <button class="btn-primary" onclick="saveTask()">Save</button>
    </div>
  </div>
</div>

<script>
'use strict';

// ── Tab switching ─────────────────────────────────────────────────────────────
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
  checkUploadedFiles();
}

// ── Shared helpers ────────────────────────────────────────────────────────────
function esc(s) {
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function showToast(msg, type) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast show' + (type === 'error' ? ' toast-error' : '');
  clearTimeout(t._tid);
  t._tid = setTimeout(() => t.classList.remove('show'), 3200);
}

// ── Dashboard sub-tabs ────────────────────────────────────────────────────────
let _dashData   = null;
let _activeSubTab = 'overview';

function showSubTab(name) {
  _activeSubTab = name;
  document.querySelectorAll('.sub-content').forEach(el => el.classList.add('hidden'));
  document.querySelectorAll('.sub-btn').forEach(el => el.classList.remove('active'));
  document.getElementById('sub-' + name).classList.remove('hidden');
  document.querySelector('.sub-btn[data-sub="' + name + '"]').classList.add('active');
  if (name === 'overview') fetchOverview();
}

// ── Table helpers ─────────────────────────────────────────────────────────────
let _tableData  = {};  // keyed by tab name
let _sortColMap = {};  // per-tab sort state
let _sortAscMap = {};

const HIGH_GERMAN = ['c1','c2','native','muttersprache','verhandlungssicher','fließend','fliessend'];

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
  if      (sl.includes('offer'))                                   c = 'bs-offer';
  else if (sl.includes('scheduled'))                               c = 'bs-scheduled';
  else if (sl.includes('awaiting') || sl.includes('⏸'))   c = 'bs-pending';
  else if (sl.includes('rejected'))                                c = 'bs-rejected';
  else if (sl.includes('unconfirmed') || sl.includes('no email'))  c = 'bs-unconfirmed';
  else if (sl.includes('applied'))                                 c = 'bs-applied';
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
  return '<a href="' + esc(url) + '" target="_blank">&#128193; open</a>';
}

// ── Build filterable table HTML ───────────────────────────────────────────────
function buildTableSection(tabKey, rows) {
  const filterHtml = `
    <div class="filters">
      <div class="fg wide"><label>Search</label><input id="f-${tabKey}-q" type="text" placeholder="Company or role…" oninput="renderTable('${tabKey}')"></div>
      <div class="fg"><label>Min Match %</label><input id="f-${tabKey}-score" type="number" min="0" max="100" style="min-width:90px" oninput="renderTable('${tabKey}')"></div>
      <div class="fg"><label>Date from</label><input id="f-${tabKey}-dfr" type="date" oninput="renderTable('${tabKey}')"></div>
      <div class="fg"><label>Date to</label><input id="f-${tabKey}-dto" type="date" oninput="renderTable('${tabKey}')"></div>
      <button class="btn-reset" onclick="resetTableFilters('${tabKey}')">Reset</button>
    </div>`;
  const tableHtml = `
    <div class="table-wrap">
      <div class="table-bar"><span id="tbar-${tabKey}"></span></div>
      <div class="scroll-x">
        <table>
          <thead><tr>
            <th onclick="sortTable('${tabKey}','date_applied')"     data-col="date_applied">     Date   <span class="arr">↕</span></th>
            <th onclick="sortTable('${tabKey}','company')"          data-col="company">           Company<span class="arr">↕</span></th>
            <th onclick="sortTable('${tabKey}','role')"             data-col="role">              Role   <span class="arr">↕</span></th>
            <th onclick="sortTable('${tabKey}','location')"         data-col="location">          Location<span class="arr">↕</span></th>
            <th onclick="sortTable('${tabKey}','match_pct')"        data-col="match_pct">         Match %<span class="arr">↕</span></th>
            <th onclick="sortTable('${tabKey}','interview_chance')" data-col="interview_chance">  Chance <span class="arr">↕</span></th>
            <th onclick="sortTable('${tabKey}','language')"         data-col="language">          German <span class="arr">↕</span></th>
            <th onclick="sortTable('${tabKey}','status')"           data-col="status">            Status <span class="arr">↕</span></th>
            <th>Archive</th>
            ${tabKey === 'interviews' ? '<th>Interview Date</th><th>Interview Type</th><th>Time (Berlin)</th><th>Format</th><th>Notes</th>' : ''}
          </tr></thead>
          <tbody id="tbody-${tabKey}"></tbody>
        </table>
        <div class="empty-state hidden" id="empty-${tabKey}">
          <div class="icon">&#128239;</div><div>No applications match the current filters.</div>
        </div>
      </div>
    </div>`;
  return filterHtml + tableHtml;
}

function renderTable(tabKey) {
  const rows = _tableData[tabKey] || [];
  const q    = (document.getElementById('f-' + tabKey + '-q')   ?.value || '').toLowerCase();
  const min  = parseInt(document.getElementById('f-' + tabKey + '-score')?.value || '0', 10) || 0;
  const dfr  = document.getElementById('f-' + tabKey + '-dfr')?.value || '';
  const dto  = document.getElementById('f-' + tabKey + '-dto')?.value || '';

  let filtered = rows.filter(r => {
    if (q && !(r.company||'').toLowerCase().includes(q) && !(r.role||'').toLowerCase().includes(q)) return false;
    if (min && (r.match_pct || 0) < min) return false;
    const d = r.date_applied || '';
    if (dfr && d && d < dfr) return false;
    if (dto && d && d > dto) return false;
    return true;
  });

  const sc = _sortColMap[tabKey] || 'date_applied';
  const sa = _sortAscMap[tabKey] ?? false;
  filtered = [...filtered].sort((a, b) => {
    let av = a[sc] ?? '', bv = b[sc] ?? '';
    if (sc === 'match_pct') { av = a.match_pct || 0; bv = b.match_pct || 0; }
    const cmp = typeof av === 'number' ? av - bv
      : String(av).localeCompare(String(bv), undefined, {numeric: true});
    return sa ? cmp : -cmp;
  });

  const tbody = document.getElementById('tbody-' + tabKey);
  document.getElementById('tbar-' + tabKey).textContent =
    filtered.length === rows.length ? rows.length + ' application' + (rows.length !== 1 ? 's' : '')
    : 'Showing ' + filtered.length + ' of ' + rows.length;
  document.getElementById('empty-' + tabKey).classList.toggle('hidden', filtered.length > 0);

  tbody.innerHTML = filtered.map(j => {
    const sc2 = j.match_pct;
    const scoreTxt = sc2 != null ? sc2 + '%' : '—';
    const roleCell = j.job_url
      ? '<a href="' + esc(j.job_url) + '" target="_blank" rel="noopener">' + esc(j.role || '(no title)') + '</a>'
      : '<span>' + esc(j.role || '—') + '</span>';
    return '<tr>'
      + '<td class="td-date">' + esc(j.date_applied || '') + '</td>'
      + '<td class="td-company">' + esc(j.company || '') + '</td>'
      + '<td class="td-role">' + roleCell + '</td>'
      + '<td>' + esc(j.location || '') + '</td>'
      + '<td class="td-score ' + (sc2 != null ? scoreClass(sc2) : 's-na') + '">' + esc(scoreTxt) + '</td>'
      + '<td>' + chanceChip(j.interview_chance) + '</td>'
      + '<td>' + germanCell(j.language) + '</td>'
      + '<td>' + statusBadge(j.status) + '</td>'
      + '<td class="td-archive">' + archiveLink(j.archive_path) + '</td>'
      + (tabKey === 'interviews'
          ? '<td style="white-space:nowrap;color:#8b949e;font-size:0.79rem;">' + esc(j._iv_date||'') + '</td>'
          + '<td style="font-size:0.79rem;">' + esc(j._iv_type||'') + '</td>'
          + '<td style="white-space:nowrap;font-size:0.79rem;color:#8b949e;">' + esc(j._iv_time||'') + '</td>'
          + '<td style="font-size:0.79rem;color:#8b949e;">' + esc(j._iv_format||'') + '</td>'
          + '<td style="font-size:0.77rem;color:#64748b;max-width:180px;white-space:normal;">' + esc(j._iv_notes||'') + '</td>'
          : '')
      + '</tr>';
  }).join('');

  // Update sort arrows
  document.querySelectorAll(`#sub-${tabKey} thead th[data-col]`).forEach(th => {
    th.classList.toggle('sorted', th.dataset.col === sc);
    const arr = th.querySelector('.arr');
    if (arr) arr.textContent = th.dataset.col !== sc ? '↕' : (sa ? '↑' : '↓');
  });
}

function sortTable(tabKey, col) {
  const cur = _sortColMap[tabKey];
  _sortAscMap[tabKey] = cur === col ? !(_sortAscMap[tabKey] ?? false) : col !== 'date_applied';
  _sortColMap[tabKey] = col;
  renderTable(tabKey);
}

function resetTableFilters(tabKey) {
  ['q','score','dfr','dto'].forEach(s => {
    const el = document.getElementById('f-' + tabKey + '-' + s);
    if (el) el.value = '';
  });
  renderTable(tabKey);
}

// ── Fetch dashboard data ──────────────────────────────────────────────────────
async function fetchDashboard() {
  try {
    const r = await fetch('/api/dashboard');
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const d = await r.json();
    if (d.error) throw new Error(d.error);
    _dashData = d;
    applyDashboardData(d);
    document.getElementById('last-update').textContent = new Date().toLocaleTimeString();
    fetchOverview();
    fetchManualInterviews();
    fetchManualTasks();
  } catch (err) {
    console.error('Dashboard fetch error:', err.message);
  } finally {
    document.getElementById('spinner-overlay').classList.add('hidden');
  }
}

function applyDashboardData(d) {
  const ov = d.overview || {};
  document.getElementById('s-total').textContent      = ov.total      ?? '—';
  document.getElementById('s-applied').textContent    = ov.Applied    ?? '—';
  document.getElementById('s-interviews').textContent = ov.Interviews ?? '—';
  document.getElementById('s-rejected').textContent   = ov.Rejected   ?? '—';
  document.getElementById('s-offers').textContent     = ov.Offer      ?? '—';

  const tabs = [{key:'applied',name:'Applied'},{key:'interviews',name:'Interviews'},{key:'rejected',name:'Rejected'}];
  tabs.forEach(({key, name}) => {
    const cnt = (ov[name] ?? 0);
    const el = document.getElementById('cnt-' + name);
    if (el) el.textContent = cnt ? ' ' + cnt : '';
    const container = document.getElementById('sub-' + key);
    const rows = d[name] || [];
    _tableData[key] = rows;
    container.innerHTML = buildTableSection(key, rows);
    _sortColMap[key] = 'date_applied';
    _sortAscMap[key] = false;
    if (key === 'interviews') enrichInterviewsTab();
    else renderTable(key);
  });

  // Show/hide import banner
  const showBanner = !d.import_done && (ov.total || 0) === 0;
  document.getElementById('import-banner').classList.toggle('hidden', !showBanner);
}

// ── Excel import ──────────────────────────────────────────────────────────────
function triggerImportExcel() {
  document.getElementById('import-excel-input').click();
}

async function importExcel(input) {
  if (!input.files[0]) return;
  const fd = new FormData();
  fd.append('file', input.files[0]);
  input.value = '';
  showToast('Importing…');
  try {
    const r = await fetch('/api/import_excel', {method: 'POST', body: fd});
    const d = await r.json();
    if (d.error) throw new Error(d.error);
    showToast('Imported ' + d.imported + ' row(s) ✓');
    await fetchDashboard();
  } catch (err) { showToast(err.message, 'error'); }
}

// ── File uploads (resume + cover letter) ─────────────────────────────────────
async function uploadFile(type, input) {
  if (!input.files[0]) return;
  const fd = new FormData();
  fd.append('file', input.files[0]);
  const statusEl = document.getElementById('ust-' + type);
  const slotEl   = document.getElementById('us-' + type);
  statusEl.textContent = 'Uploading…';
  statusEl.style.color = '#64748b';
  try {
    const r = await fetch('/api/upload/' + type, {method: 'POST', body: fd});
    const d = await r.json();
    if (d.ok) {
      statusEl.textContent = '✓ ' + d.filename;
      statusEl.style.color = '#34d399';
      slotEl.classList.add('uploaded');
      showToast(d.filename + ' uploaded');
    } else {
      statusEl.textContent = '✗ ' + (d.error || 'Upload failed');
      statusEl.style.color = '#f87171';
    }
  } catch (err) {
    statusEl.textContent = '✗ ' + err.message;
    statusEl.style.color = '#f87171';
  }
}

async function checkUploadedFiles() {
  try {
    const r = await fetch('/api/upload/status');
    const d = await r.json();
    for (const [type, info] of Object.entries(d)) {
      if (info.exists) {
        const statusEl = document.getElementById('ust-' + type);
        const slotEl   = document.getElementById('us-' + type);
        if (statusEl) { statusEl.textContent = '✓ ' + info.filename; statusEl.style.color = '#34d399'; }
        if (slotEl)   slotEl.classList.add('uploaded');
      }
    }
  } catch (_) {}
}

// ── Wizard ───────────────────────────────────────────────────────────────────
let _wizStep         = 1;
let _pollTimer       = null;
let _scrapedJobs     = [];
let _scrapedFiltered = [];
let _matchedJobs     = [];
let _matchedFiltered = [];

function setWizardStep(n) {
  _wizStep = n;
  [1,2,3,4].forEach(i => {
    document.getElementById('wz-' + i).classList.toggle('hidden', i !== n);
    const ind = document.getElementById('step-ind-' + i);
    ind.classList.remove('active','done');
    if (i === n) ind.classList.add('active');
    else if (i < n) { ind.classList.add('done'); ind.querySelector('.wz-num').textContent = '✓'; }
    else ind.querySelector('.wz-num').textContent = i;
  });
}

function _startPoll() { if (_pollTimer) clearInterval(_pollTimer); _pollTimer = setInterval(pollPipeline, 3000); }
function _stopPoll()  { if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; } }

// ── Scrape button state helpers ───────────────────────────────────────────────
let _statusPollTimer = null;

function _setScrapeRunning() {
  const btn = document.getElementById('btn-scrape');
  btn.className = 'btn-stop';
  btn.innerHTML = '&#9209; Stop Scraping';
  btn.onclick   = stopScraping;
  btn.disabled  = false;
  const msg = document.getElementById('scrape-msg');
  msg.className   = 'scraping-pulse';
  msg.style.color = '#64748b';
  msg.textContent = '⏳ Scraping LinkedIn…';
  document.getElementById('scrape-progress').classList.remove('hidden');
}

function _resetScrapeButton() {
  const btn = document.getElementById('btn-scrape');
  btn.className = 'btn-primary';
  btn.innerHTML = '&#128270; Start Scraping';
  btn.onclick   = startScrape;
  btn.disabled  = false;
  const msg = document.getElementById('scrape-msg');
  msg.className = '';
  _stopStatusPoll();
}

function _startStatusPoll() {
  if (_statusPollTimer) clearInterval(_statusPollTimer);
  _statusPollTimer = setInterval(async () => {
    try {
      const r = await fetch('/api/agent/status');
      const d = await r.json();
      if (!d.running) { _stopStatusPoll(); _resetScrapeButton(); }
    } catch (_) {}
  }, 2000);
}

function _stopStatusPoll() {
  if (_statusPollTimer) { clearInterval(_statusPollTimer); _statusPollTimer = null; }
}

async function pollPipeline() {
  try {
    const r = await fetch('/api/pipeline/status');
    const d = await r.json();
    if (d.step === 1) {
      // still running — _setScrapeRunning already set the message
    } else if (d.step === 2) {
      _stopPoll(); _stopStatusPoll(); _resetScrapeButton();
      document.getElementById('scrape-progress').classList.add('hidden');
      showToast('✓ Scraping complete — ' + d.jobs_count + ' jobs found');
      setWizardStep(2); await loadScrapedJobs();
    } else if (d.step === 3) {
      document.getElementById('match-msg').textContent = 'Scoring with Claude AI (' + d.jobs_count + ' jobs)…';
    } else if (d.step === 4) {
      _stopPoll(); showToast('✓ Matching complete — ' + d.matched_count + ' jobs matched');
      await renderMatchedJobs();
    } else if (d.step === 5) {
      document.getElementById('apply-msg').textContent = 'Submitting applications…';
    } else if (d.step === 6) {
      _stopPoll();
      document.getElementById('apply-progress').classList.add('hidden');
      document.getElementById('apply-results').classList.remove('hidden');
      showToast('✓ Done! Download the updated tracker below.');
      fetchDashboard();
    } else if (d.step < 0) {
      _stopPoll(); _stopStatusPoll(); _resetScrapeButton();
      document.getElementById('scrape-progress').classList.add('hidden');
      showToast(d.error || 'Pipeline error', 'error');
      document.getElementById('btn-match').disabled = false;
    }
  } catch (_) {}
}

async function startScrape() {
  _setScrapeRunning();
  try {
    const r = await fetch('/api/pipeline/scrape', {method: 'POST'});
    const d = await r.json();
    if (d.error) throw new Error(d.error);
    _startPoll();
    _startStatusPoll();
  } catch (err) {
    showToast(err.message, 'error');
    _resetScrapeButton();
    document.getElementById('scrape-progress').classList.add('hidden');
  }
}

async function stopScraping() {
  const btn = document.getElementById('btn-scrape');
  btn.disabled = true;
  btn.innerHTML = 'Stopping…';
  _stopPoll();
  _stopStatusPoll();
  try { await fetch('/api/agent/stop', {method: 'POST'}); } catch (_) {}
  document.getElementById('scrape-progress').classList.add('hidden');
  _resetScrapeButton();
  showToast('Scraping stopped.');
}

// ── Step 2: Scraped Jobs table ────────────────────────────────────────────────

async function loadScrapedJobs() {
  try {
    const r = await fetch('/api/pipeline/scraped_jobs');
    const d = await r.json();
    _scrapedJobs = d.jobs || [];
    _scrapedFiltered = [..._scrapedJobs];
    if (!_scrapedJobs.length) {
      showToast('Scraping finished but no jobs were saved to the database — check the agent log for errors.', 'error');
    }
    renderScrapedTable();
  } catch (err) { showToast(err.message, 'error'); }
}

function filterScrapedJobs() {
  const q = (document.getElementById('scraped-search').value || '').toLowerCase();
  _scrapedFiltered = q
    ? _scrapedJobs.filter(j =>
        (j.title||'').toLowerCase().includes(q) ||
        (j.company||'').toLowerCase().includes(q) ||
        (j.location||'').toLowerCase().includes(q))
    : [..._scrapedJobs];
  renderScrapedTable();
}

function _fmtDate(ts) {
  if (!ts) return '';
  const d = new Date(ts.replace(' ', 'T') + 'Z');
  if (isNaN(d)) return ts;
  return d.toLocaleDateString(undefined, {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'});
}

function renderScrapedTable() {
  const total = _scrapedJobs.length;
  const shown = _scrapedFiltered.length;
  document.getElementById('scraped-count').textContent =
    shown === total ? total + ' jobs scraped' : shown + ' of ' + total + ' jobs';

  const tbody = document.getElementById('scraped-tbody');
  if (!_scrapedFiltered.length) {
    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:#64748b;padding:20px;">No jobs match the filter.</td></tr>';
    return;
  }
  tbody.innerHTML = _scrapedFiltered.map(j => `
    <tr data-id="${j.id||''}">
      <td class="td-title">${esc(j.title||'?')}</td>
      <td>${esc(j.company||'')}</td>
      <td>${esc(j.location||'')}</td>
      <td>${esc(j.source||'')}</td>
      <td style="white-space:nowrap;font-size:0.76rem;color:#64748b;">${_fmtDate(j.scraped_at)}</td>
      <td class="td-actions">
        <div class="man-row-btns">
          ${j.url ? `<a href="${esc(j.url)}" target="_blank" class="btn-sm" style="text-decoration:none;">View Job</a>` : ''}
          <button class="btn-icon del" title="Remove" onclick="removeScrapedJob(${j.id||0},this)">&#128465;</button>
        </div>
      </td>
    </tr>`).join('');
}

async function removeScrapedJob(id, btn) {
  btn.disabled = true;
  try {
    await fetch('/api/pipeline/scraped_jobs/' + id, {method: 'DELETE'});
    _scrapedJobs = _scrapedJobs.filter(j => j.id !== id);
    filterScrapedJobs();
  } catch (err) { showToast(err.message, 'error'); btn.disabled = false; }
}

function goToMatch() { setWizardStep(3); }

// ── Step 3: Matched Jobs table ────────────────────────────────────────────────

function _scoreBadge(sc) {
  const cls = sc >= 85 ? 'badge-score-hi' : sc >= 70 ? 'badge-score-mid' : 'badge-score-lo';
  return `<span class="badge-score ${cls}">${sc}%</span>`;
}
function _chanceBadge(ch) {
  const v = (ch||'').toLowerCase();
  const cls = v === 'high' ? 'badge-chance-high' : v === 'medium' ? 'badge-chance-medium' : 'badge-chance-low';
  return `<span class="badge-chance ${cls}">${esc(ch||'?')}</span>`;
}

async function startMatch() {
  document.getElementById('btn-match').disabled = true;
  document.getElementById('match-progress').classList.remove('hidden');
  document.getElementById('match-empty').classList.add('hidden');
  document.getElementById('match-msg').textContent = 'Starting AI scoring…';
  try {
    const r = await fetch('/api/pipeline/match', {method:'POST'});
    const d = await r.json();
    if (d.error) throw new Error(d.error);
    _startPoll();
  } catch (err) {
    showToast(err.message, 'error');
    document.getElementById('btn-match').disabled = false;
    document.getElementById('match-progress').classList.add('hidden');
    document.getElementById('match-empty').classList.remove('hidden');
  }
}

function filterMatchedJobs() {
  const q = (document.getElementById('matched-search').value || '').toLowerCase();
  _matchedFiltered = q
    ? _matchedJobs.filter(j =>
        (j.title||'').toLowerCase().includes(q) ||
        (j.company||'').toLowerCase().includes(q) ||
        (j.location||'').toLowerCase().includes(q))
    : [..._matchedJobs];
  renderMatchedTable();
}

function toggleSelectAll(cb) {
  document.querySelectorAll('#matched-tbody .row-cb').forEach(c => c.checked = cb.checked);
}

function renderMatchedTable() {
  const scraped = _scrapedJobs.length || '?';
  document.getElementById('matched-count').textContent =
    _matchedFiltered.length + ' of ' + scraped + ' jobs matched';

  const tbody = document.getElementById('matched-tbody');
  if (!_matchedFiltered.length) {
    tbody.innerHTML = '<tr><td colspan="9" style="text-align:center;color:#64748b;padding:20px;">No jobs match the filter.</td></tr>';
    return;
  }
  tbody.innerHTML = _matchedFiltered.map((j, idx) => {
    const sc = parseInt(j.match_score) || 0;
    return `<tr>
      <td><input type="checkbox" class="row-cb" value="${esc(j.url||'')}"></td>
      <td class="td-title">${esc(j.title||'?')}</td>
      <td>${esc(j.company||'')}</td>
      <td>${esc(j.location||'')}</td>
      <td>${_scoreBadge(sc)}</td>
      <td>${_chanceBadge(j.interview_chance)}</td>
      <td><span class="badge-de">${esc(j.german_level_required||j.german_level||'—')}</span></td>
      <td class="td-summary">${esc(j.match_summary||'')}</td>
      <td class="td-actions">
        <div class="man-row-btns">
          ${j.url ? `<a href="${esc(j.url)}" target="_blank" class="btn-sm" style="text-decoration:none;">View Job</a>` : ''}
          <button class="btn-sm btn-primary" onclick="applySingle(${JSON.stringify(j.url)})">Apply</button>
        </div>
      </td>
    </tr>`;
  }).join('');
}

async function renderMatchedJobs() {
  try {
    const r = await fetch('/api/pipeline/matched_jobs');
    const d = await r.json();
    _matchedJobs = d.jobs || [];
    _matchedFiltered = [..._matchedJobs];

    document.getElementById('match-progress').classList.add('hidden');
    document.getElementById('match-empty').classList.add('hidden');
    document.getElementById('btn-match').disabled = false;

    if (!_matchedJobs.length) {
      document.getElementById('match-empty').textContent = 'No jobs passed the matching filters.';
      document.getElementById('match-empty').classList.remove('hidden');
      return;
    }
    document.getElementById('matched-table-wrap').classList.remove('hidden');
    renderMatchedTable();
  } catch (err) { showToast(err.message, 'error'); }
}

function goToApply() {
  setWizardStep(4);
  document.getElementById('apply-jobs-list').innerHTML = _matchedJobs.map(j => `
    <div class="job-card" style="display:flex;justify-content:space-between;align-items:center;gap:12px;">
      <div>
        <div class="jc-title">${esc(j.title||'?')}</div>
        <div class="jc-meta">${esc(j.company||'?')} &bull; ${esc(j.location||'?')}</div>
      </div>
      <button class="btn-sm btn-primary" onclick="applySingle(${JSON.stringify(j.url)})">Apply</button>
    </div>`).join('');
}

async function _doApply(body) {
  document.getElementById('btn-apply-all').disabled = true;
  document.getElementById('apply-progress').classList.remove('hidden');
  document.getElementById('apply-msg').textContent = 'Submitting applications…';
  try {
    const r = await fetch('/api/pipeline/apply', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify(body),
    });
    const d = await r.json();
    if (d.error) throw new Error(d.error);
    _startPoll();
  } catch (err) {
    showToast(err.message, 'error');
    document.getElementById('btn-apply-all').disabled = false;
    document.getElementById('apply-progress').classList.add('hidden');
  }
}
function applyAll() { _doApply({all: true}); }
function applySingle(url) { _doApply({urls: [url]}); }
function applySelected() {
  const urls = [...document.querySelectorAll('#matched-tbody .row-cb:checked')].map(c => c.value).filter(Boolean);
  if (!urls.length) { showToast('No jobs selected.', 'error'); return; }
  _doApply({urls});
}

// ── Roles panel ───────────────────────────────────────────────────────────────
let _rolesData   = [];   // CV-extracted roles from /api/roles
let _customRoles = new Set(); // manually added titles
let _selected    = new Set();

async function loadRoles() {
  const box = document.getElementById('roles-container');
  box.innerHTML = '<span class="loading-text">Extracting roles from resume via Claude…</span>';
  try {
    // Load CV roles and saved config in parallel
    const [rolesResp, cfgResp] = await Promise.all([fetch('/api/roles'), fetch('/api/config')]);
    const rolesData = await rolesResp.json();
    const cfgData   = await cfgResp.json();
    if (rolesData.error) throw new Error(rolesData.error);

    _rolesData = rolesData.roles || [];
    const cvSet = new Set(_rolesData);
    const savedRoles = cfgData.roles || [];

    // Any saved role not in the CV set is a custom role
    _customRoles = new Set(savedRoles.filter(r => !cvSet.has(r)));

    // Selection = whatever was saved in config; if nothing saved, default to all CV roles
    _selected = savedRoles.length > 0 ? new Set(savedRoles) : new Set(_rolesData);

    renderRoles();
  } catch (err) { box.innerHTML = '<span class="error-text">&#9888; ' + esc(err.message) + '</span>'; }
}

function renderRoles() {
  const box = document.getElementById('roles-container');
  const allRoles = [..._rolesData, ...[..._customRoles].filter(r => !_rolesData.includes(r))];
  if (!allRoles.length) { box.innerHTML = '<span class="loading-text">No roles loaded.</span>'; return; }

  box.innerHTML = '';

  // CV-extracted chips (no remove button)
  _rolesData.forEach(r => {
    const chip = document.createElement('span');
    chip.className = 'role-chip' + (_selected.has(r) ? ' selected' : '');
    chip.dataset.role = r;
    chip.textContent = r;
    chip.addEventListener('click', () => toggleRole(r));
    box.appendChild(chip);
  });

  // Custom chips (with remove button, green border)
  [..._customRoles].forEach(r => {
    if (_rolesData.includes(r)) return; // skip if CV already has it
    const chip = document.createElement('span');
    chip.className = 'role-chip custom' + (_selected.has(r) ? ' selected' : '');
    chip.dataset.role = r;

    const label = document.createElement('span');
    label.textContent = r;
    label.style.pointerEvents = 'none';
    chip.appendChild(label);

    const rm = document.createElement('button');
    rm.className = 'role-chip-rm';
    rm.title = 'Remove';
    rm.textContent = '×';
    rm.addEventListener('click', (e) => { e.stopPropagation(); removeCustomRole(r); });
    chip.appendChild(rm);

    chip.addEventListener('click', () => toggleRole(r));
    box.appendChild(chip);
  });
}

function toggleRole(r) {
  _selected.has(r) ? _selected.delete(r) : _selected.add(r);
  const chip = document.querySelector(`#roles-container [data-role="${CSS.escape(r)}"]`);
  if (chip) chip.classList.toggle('selected', _selected.has(r));
}

function addCustomRole() {
  const input = document.getElementById('custom-role-input');
  const val = (input.value || '').trim();
  if (!val) return;
  if (_rolesData.includes(val) || _customRoles.has(val)) {
    // Just select it if it already exists
    _selected.add(val);
    renderRoles();
    input.value = '';
    return;
  }
  _customRoles.add(val);
  _selected.add(val);
  input.value = '';
  renderRoles();
}

function removeCustomRole(r) {
  _customRoles.delete(r);
  _selected.delete(r);
  renderRoles();
}

function selectAllRoles() {
  _selected = new Set([..._rolesData, ..._customRoles]);
  renderRoles();
}
function clearAllRoles()  { _selected = new Set(); renderRoles(); }
async function saveRoles() {
  try {
    const cfgR = await fetch('/api/config');
    const cfg  = await cfgR.json();
    cfg.roles  = [..._selected];
    const r = await fetch('/api/config', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(cfg)});
    const d = await r.json();
    d.ok ? showToast('Saved ' + cfg.roles.length + ' role(s)') : showToast(d.error, 'error');
  } catch (err) { showToast(err.message, 'error'); }
}

// ── Config editor ─────────────────────────────────────────────────────────────
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
    const plEl = document.getElementById('cfg-posted_limit');
    if (plEl && d.posted_limit) plEl.value = d.posted_limit;
  } catch (err) { showToast('Config load error: ' + err.message, 'error'); }
}

function renderTagList(key) {
  const box = document.getElementById('tl-' + key);
  if (!box) return;
  box.innerHTML = (_tagLists[key] || []).map((v, i) =>
    `<span class="tag-chip">${esc(v)}<button class="tag-rm" data-key="${esc(key)}" data-idx="${i}">&#215;</button></span>`
  ).join('');
  box.querySelectorAll('.tag-rm').forEach(btn =>
    btn.addEventListener('click', () => removeTag(btn.dataset.key, Number(btn.dataset.idx)))
  );
}
function removeTag(key, idx) {
  (_tagLists[key] || []).splice(idx, 1);
  renderTagList(key);
}
function addTag(key) {
  const inp = document.getElementById('tl-input-' + key);
  const val = inp.value.trim();
  if (!val || (_tagLists[key] || []).includes(val)) { inp.value = ''; return; }
  (_tagLists[key] = _tagLists[key] || []).push(val);
  inp.value = ''; renderTagList(key);
}
function addTagOnEnter(e, key) { if (e.key === 'Enter') { e.preventDefault(); addTag(key); } }
async function saveConfig() {
  const payload = {..._cfgData};
  _TEXT_KEYS.forEach(k => { const el = document.getElementById('cfg-' + k); if (el) payload[k] = el.value; });
  _NUM_KEYS.forEach(k  => { const el = document.getElementById('cfg-' + k); if (el) payload[k] = el.value !== '' ? Number(el.value) : null; });
  _TOG_KEYS.forEach(k  => { const el = document.getElementById('cfg-' + k); if (el) payload[k] = el.checked; });
  _TAG_KEYS.forEach(k  => { payload[k] = _tagLists[k] || []; });
  const plEl = document.getElementById('cfg-posted_limit');
  if (plEl) payload.posted_limit = plEl.value;
  delete payload.paths; delete payload.contact;
  const btn = document.getElementById('btn-save-config');
  btn.disabled = true; btn.textContent = 'Saving…';
  try {
    const r = await fetch('/api/config', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)});
    const d = await r.json();
    d.ok ? showToast('Config saved ✓') : showToast('Save failed: ' + d.error, 'error');
  } catch (err) { showToast('Error: ' + err.message, 'error'); }
  finally { btn.disabled = false; btn.textContent = 'Save Config'; }
}

// ── Live log ──────────────────────────────────────────────────────────────────
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

// ── Overview sections ────────────────────────────────────────────────────────
function daysSince(dateStr) {
  if (!dateStr) return null;
  const d = new Date(dateStr + 'T00:00:00');
  const today = new Date(); today.setHours(0, 0, 0, 0);
  return Math.round((today - d) / 86400000);
}

async function fetchOverview() {
  try {
    const r = await fetch('/api/overview');
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const d = await r.json();
    if (d.error) { console.error('Overview error:', d.error); return; }
    renderEvents(d.upcoming_events || []);
    renderTasks(d.priority_tasks || []);
  } catch (err) { console.error('Overview fetch error:', err.message); }
}

function _eventBadge(status) {
  const sl = (status || '').toLowerCase();
  if (sl.includes('call'))      return '<span class="badge bs-call">'  + esc(status) + '</span>';
  if (sl.includes('technical')) return '<span class="badge bs-tech">'  + esc(status) + '</span>';
  if (sl.includes('final'))     return '<span class="badge bs-final">' + esc(status) + '</span>';
  return '<span class="badge bs-scheduled">' + esc(status) + '</span>';
}

function renderEvents(items) {
  const box = document.getElementById('ov-events');
  if (!box) return;
  if (!items.length) { box.innerHTML = '<div class="ov-empty">No upcoming events.</div>'; return; }
  box.innerHTML = items.map(e => `
    <div class="ov-item">
      <div class="ov-item-main">
        <div class="ov-company">${esc(e.company || '—')}</div>
        <div class="ov-role">${esc(e.role || '—')}</div>
        <div style="margin-top:5px;">${_eventBadge(e.status || '')}</div>
      </div>
      <div class="ov-item-right">
        <div class="ov-date">${esc(e.date_applied || '')}</div>
        ${e.job_url ? `<a href="${esc(e.job_url)}" target="_blank" rel="noopener" style="font-size:0.75rem;color:#58a6ff;">View ↗</a>` : ''}
      </div>
    </div>`).join('');
}

function _taskBadge(status) {
  const sl = (status || '').toLowerCase();
  if (sl.includes('unconfirmed')) return '<span class="badge bs-unconfirmed">' + esc(status) + '</span>';
  return '<span class="badge bs-pending">' + esc(status) + '</span>';
}

function renderTasks(items) {
  const box = document.getElementById('ov-tasks');
  if (!box) return;
  if (!items.length) { box.innerHTML = '<div class="ov-empty">No pending actions.</div>'; return; }
  box.innerHTML = items.map(t => {
    const days = daysSince(t.date_applied);
    return `
    <div class="ov-item" id="ov-task-${t.id}">
      <div class="ov-item-main">
        <div class="ov-company">${esc(t.company || '—')}</div>
        <div class="ov-role">${esc(t.role || '—')}</div>
        <div style="margin-top:5px;">${_taskBadge(t.status || '')}</div>
      </div>
      <div class="ov-item-right">
        ${days != null ? `<div class="ov-days">${days}d ago</div>` : ''}
        <button class="btn-advance" onclick="markAsDone(${t.id}, this)">Mark Done</button>
      </div>
    </div>`;
  }).join('');
}

async function markAsDone(appId, btn) {
  if (btn) btn.disabled = true;
  try {
    const r = await fetch('/api/applications/' + appId + '/advance', {method: 'POST'});
    const d = await r.json();
    if (d.error) { showToast(d.error, 'error'); if (btn) btn.disabled = false; return; }
    showToast('Updated: ' + d.status);
    const row = document.getElementById('ov-task-' + appId);
    if (row) { row.style.opacity = '0'; setTimeout(() => { row.remove();
      const box = document.getElementById('ov-tasks');
      if (box && !box.querySelector('.ov-item')) box.innerHTML = '<div class="ov-empty">No pending actions.</div>';
    }, 250); }
  } catch (err) { showToast(err.message, 'error'); if (btn) btn.disabled = false; }
}

// ── Modal helpers ────────────────────────────────────────────────────────────
function closeModal(id) {
  document.getElementById(id).classList.add('hidden');
}
document.addEventListener('click', e => {
  if (e.target.classList.contains('modal-backdrop')) closeModal(e.target.id);
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    ['modal-interview','modal-task'].forEach(id => {
      const el = document.getElementById(id);
      if (el && !el.classList.contains('hidden')) closeModal(id);
    });
  }
});

// ── Manual Interviews ─────────────────────────────────────────────────────────
let _interviews = [];

async function fetchManualInterviews() {
  try {
    const r = await fetch('/api/interviews/upcoming');
    const d = await r.json();
    if (d.error) return;
    _interviews = d.interviews || [];
    renderManualInterviews();
    enrichInterviewsTab();
  } catch (_) {}
}

function _isPast(dateStr) {
  if (!dateStr) return false;
  const today = new Date(); today.setHours(0,0,0,0);
  return new Date(dateStr + 'T00:00:00') < today;
}

function renderManualInterviews() {
  const tbody = document.getElementById('interviews-tbody');
  const empty = document.getElementById('interviews-empty');
  if (!tbody) return;
  if (!_interviews.length) {
    tbody.innerHTML = '';
    if (empty) empty.classList.remove('hidden');
    return;
  }
  if (empty) empty.classList.add('hidden');
  tbody.innerHTML = _interviews.map(iv => `
    <tr class="${_isPast(iv.date) ? 'past-row' : ''}" id="iv-row-${iv.id}">
      <td style="white-space:nowrap;">${esc(iv.date||'')}</td>
      <td style="font-weight:600;">${iv.job_url
        ? `<a href="${esc(iv.job_url)}" target="_blank" rel="noopener" style="color:#58a6ff;">${esc(iv.company||'')}</a>`
        : esc(iv.company||'')}</td>
      <td>${esc(iv.role||'')}</td>
      <td>${esc(iv.interview_type||'')}</td>
      <td style="white-space:nowrap;color:#8b949e;">${esc(iv.time_berlin||'')}</td>
      <td style="color:#8b949e;">${esc(iv.format||'')}</td>
      <td><div class="man-row-btns">
        <button class="btn-icon" title="Edit" onclick="openInterviewModal(${iv.id})">&#9998;</button>
        <button class="btn-icon del" title="Delete" onclick="deleteInterview(${iv.id})">&#215;</button>
      </div></td>
    </tr>`).join('');
}

function openInterviewModal(id) {
  const iv = id != null ? (_interviews.find(x => x.id === id) || {}) : {};
  document.getElementById('modal-interview-title').textContent = id != null ? 'Edit Interview' : 'Add Interview';
  document.getElementById('mi-id').value      = iv.id      || '';
  document.getElementById('mi-date').value    = iv.date    || '';
  document.getElementById('mi-time').value    = iv.time_berlin || '';
  document.getElementById('mi-company').value = iv.company || '';
  document.getElementById('mi-role').value    = iv.role    || '';
  document.getElementById('mi-type').value    = iv.interview_type || '';
  document.getElementById('mi-format').value  = iv.format  || '';
  document.getElementById('mi-url').value     = iv.job_url || '';
  document.getElementById('mi-notes').value   = iv.notes   || '';
  document.getElementById('modal-interview').classList.remove('hidden');
}

async function saveInterview() {
  const id     = document.getElementById('mi-id').value;
  const data   = {
    date:           document.getElementById('mi-date').value,
    time_berlin:    document.getElementById('mi-time').value,
    company:        document.getElementById('mi-company').value,
    role:           document.getElementById('mi-role').value,
    interview_type: document.getElementById('mi-type').value,
    format:         document.getElementById('mi-format').value,
    job_url:        document.getElementById('mi-url').value,
    notes:          document.getElementById('mi-notes').value,
  };
  const url    = id ? '/api/interviews/upcoming/' + id : '/api/interviews/upcoming';
  const method = id ? 'PUT' : 'POST';
  try {
    const r = await fetch(url, {method, headers:{'Content-Type':'application/json'}, body: JSON.stringify(data)});
    const d = await r.json();
    if (d.error) { showToast(d.error, 'error'); return; }
    closeModal('modal-interview');
    showToast(id ? 'Interview updated' : 'Interview added');
    await fetchManualInterviews();
  } catch (err) { showToast(err.message, 'error'); }
}

async function deleteInterview(id) {
  if (!confirm('Delete this interview?')) return;
  try {
    const r = await fetch('/api/interviews/upcoming/' + id, {method:'DELETE'});
    const d = await r.json();
    if (d.error) { showToast(d.error, 'error'); return; }
    showToast('Interview deleted');
    await fetchManualInterviews();
  } catch (err) { showToast(err.message, 'error'); }
}

// ── Interviews sub-tab enrichment ─────────────────────────────────────────────
function enrichInterviewsTab() {
  const rows = _tableData['interviews'];
  if (!rows) return;
  const lookup = {};
  _interviews.forEach(iv => {
    const key = (iv.company || '').toLowerCase().trim();
    if (!lookup[key]) lookup[key] = iv;
  });
  _tableData['interviews'] = rows.map(row => {
    const iv = lookup[(row.company || '').toLowerCase().trim()] || {};
    return Object.assign({}, row, {
      _iv_date:   iv.date           || '',
      _iv_type:   iv.interview_type || '',
      _iv_time:   iv.time_berlin    || '',
      _iv_format: iv.format         || '',
      _iv_notes:  iv.notes          || '',
    });
  });
  renderTable('interviews');
}

// ── Manual Tasks ──────────────────────────────────────────────────────────────
let _manualTasks = [];

async function fetchManualTasks() {
  try {
    const r = await fetch('/api/tasks');
    const d = await r.json();
    if (d.error) return;
    _manualTasks = d.tasks || [];
    renderManualTasks();
  } catch (_) {}
}

function renderManualTasks() {
  const tbody = document.getElementById('tasks-tbody');
  const empty = document.getElementById('tasks-empty');
  if (!tbody) return;
  if (!_manualTasks.length) {
    tbody.innerHTML = '';
    if (empty) empty.classList.remove('hidden');
    return;
  }
  if (empty) empty.classList.add('hidden');
  const priClass = {DONE:'pri-done', HIGH:'pri-high', MEDIUM:'pri-medium', NORMAL:'pri-normal'};
  tbody.innerHTML = _manualTasks.map((t, i) => {
    const pri    = (t.priority || 'NORMAL').toUpperCase();
    const isDone = pri === 'DONE';
    const cls    = priClass[pri] || 'pri-normal';
    return `
    <tr class="${isDone ? 'done-row' : ''}" id="tk-row-${t.id}">
      <td style="color:#64748b;font-size:0.79rem;">${i + 1}</td>
      <td><span class="badge ${cls}">${esc(t.priority||'NORMAL')}</span></td>
      <td style="font-weight:600;">${esc(t.company||'')}</td>
      <td>${esc(t.action||'')}</td>
      <td style="font-size:0.79rem;color:#8b949e;white-space:nowrap;">${esc(t.deadline||'')}</td>
      <td style="font-size:0.79rem;color:#8b949e;">${esc(t.status||'')}</td>
      <td><div class="man-row-btns">
        <button class="btn-icon" title="Edit" onclick="openTaskModal(${t.id})">&#9998;</button>
        <button class="btn-icon del" title="Delete" onclick="deleteManualTask(${t.id})">&#215;</button>
      </div></td>
    </tr>`;
  }).join('');
}

function openTaskModal(id) {
  const t = id != null ? (_manualTasks.find(x => x.id === id) || {}) : {};
  document.getElementById('modal-task-title').textContent = id != null ? 'Edit Task' : 'Add Task';
  document.getElementById('mt-id').value       = t.id       || '';
  document.getElementById('mt-priority').value = t.priority || 'NORMAL';
  document.getElementById('mt-company').value  = t.company  || '';
  document.getElementById('mt-action').value   = t.action   || '';
  document.getElementById('mt-deadline').value = t.deadline || '';
  document.getElementById('mt-status').value   = t.status   || '';
  document.getElementById('mt-notes').value    = t.notes    || '';
  document.getElementById('modal-task').classList.remove('hidden');
}

async function saveTask() {
  const id   = document.getElementById('mt-id').value;
  const data = {
    priority: document.getElementById('mt-priority').value,
    company:  document.getElementById('mt-company').value,
    action:   document.getElementById('mt-action').value,
    deadline: document.getElementById('mt-deadline').value,
    status:   document.getElementById('mt-status').value,
    notes:    document.getElementById('mt-notes').value,
  };
  const url    = id ? '/api/tasks/' + id : '/api/tasks';
  const method = id ? 'PUT' : 'POST';
  try {
    const r = await fetch(url, {method, headers:{'Content-Type':'application/json'}, body: JSON.stringify(data)});
    const d = await r.json();
    if (d.error) { showToast(d.error, 'error'); return; }
    closeModal('modal-task');
    showToast(id ? 'Task updated' : 'Task added');
    await fetchManualTasks();
  } catch (err) { showToast(err.message, 'error'); }
}

async function deleteManualTask(id) {
  if (!confirm('Delete this task?')) return;
  try {
    const r = await fetch('/api/tasks/' + id, {method:'DELETE'});
    const d = await r.json();
    if (d.error) { showToast(d.error, 'error'); return; }
    showToast('Task deleted');
    await fetchManualTasks();
  } catch (err) { showToast(err.message, 'error'); }
}

// ── Boot ──────────────────────────────────────────────────────────────────────
fetchDashboard();
setInterval(fetchDashboard, 60000);
</script>
</body>
</html>"""


# ── Flask routes ──────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template_string(_HTML)


# ── Dashboard data ────────────────────────────────────────────────────────────

@app.route("/api/dashboard")
def api_dashboard():
    try:
        import db as _db
        _db.init_db()
        return jsonify(_db.get_dashboard_data())
    except Exception as exc:
        return jsonify({"error": str(exc), "overview": {}, "Applied": [],
                        "Interviews": [], "Rejected": [], "import_done": False})


# ── Upcoming interviews (manual CRUD) ────────────────────────────────────────

@app.route("/api/interviews/upcoming", methods=["GET"])
def api_interviews_get():
    try:
        import db as _db
        _db.init_db()
        return jsonify({"interviews": _db.get_upcoming_interviews()})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/interviews/upcoming", methods=["POST"])
def api_interviews_post():
    try:
        data = request.get_json(force=True) or {}
        import db as _db
        _db.init_db()
        new_id = _db.add_interview(data)
        return jsonify({"ok": True, "id": new_id})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/interviews/upcoming/<int:interview_id>", methods=["PUT"])
def api_interviews_put(interview_id: int):
    try:
        data = request.get_json(force=True) or {}
        import db as _db
        _db.init_db()
        _db.update_interview(interview_id, data)
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/interviews/upcoming/<int:interview_id>", methods=["DELETE"])
def api_interviews_delete(interview_id: int):
    try:
        import db as _db
        _db.init_db()
        _db.delete_interview(interview_id)
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── Priority tasks (manual CRUD) ──────────────────────────────────────────────

@app.route("/api/tasks", methods=["GET"])
def api_tasks_get():
    try:
        import db as _db
        _db.init_db()
        return jsonify({"tasks": _db.get_priority_tasks_manual()})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/tasks", methods=["POST"])
def api_tasks_post():
    try:
        data = request.get_json(force=True) or {}
        import db as _db
        _db.init_db()
        new_id = _db.add_task_manual(data)
        return jsonify({"ok": True, "id": new_id})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/tasks/<int:task_id>", methods=["PUT"])
def api_tasks_put(task_id: int):
    try:
        data = request.get_json(force=True) or {}
        import db as _db
        _db.init_db()
        _db.update_task_manual(task_id, data)
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/tasks/<int:task_id>", methods=["DELETE"])
def api_tasks_delete(task_id: int):
    try:
        import db as _db
        _db.init_db()
        _db.delete_task_manual(task_id)
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── Overview enriched ─────────────────────────────────────────────────────────

@app.route("/api/overview")
def api_overview():
    try:
        import db as _db
        _db.init_db()
        return jsonify(_db.get_overview_data())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


_NEXT_STATUS: dict[str, str] = {
    "Applied — unconfirmed":                       "Applied ✓",
    "⏸️ Technical — awaiting your confirmation":   "Technical Scheduled ✓",
    "⏸️ Final — awaiting your confirmation":       "Final Scheduled ✓",
}


@app.route("/api/applications/<int:app_id>/advance", methods=["POST"])
def api_advance_status(app_id: int):
    try:
        import db as _db
        _db.init_db()
        with _db._conn() as db:
            row = db.execute(
                "SELECT status FROM applications WHERE id=?", (app_id,)
            ).fetchone()
            if not row:
                return jsonify({"error": "Application not found"}), 404
            current = row["status"] or ""
            next_st = _NEXT_STATUS.get(current)
            if not next_st:
                return jsonify({"error": f"No next step defined for: {current!r}"}), 400
            db.execute(
                "UPDATE applications SET status=? WHERE id=?", (next_st, app_id)
            )
        return jsonify({"ok": True, "status": next_st})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/import_excel", methods=["POST"])
def api_import_excel():
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "No file"}), 400
    tmp = _UPLOADS_DIR / "_import_tmp.xlsx"
    result, status = {"error": "Unknown error"}, 500
    try:
        _UPLOADS_DIR.mkdir(exist_ok=True)
        f.save(str(tmp))
        import db as _db
        _db.init_db()
        imported = _db.import_from_excel(tmp)
        _db.set_setting("import_done", "1")
        result, status = {"imported": imported}, 200
    except Exception as exc:
        result, status = {"error": str(exc)}, 500
    try:
        tmp.unlink(missing_ok=True)
    except OSError:
        pass
    return jsonify(result), status


@app.route("/api/download/tracker")
def api_download_tracker():
    try:
        import db as _db
        _db.init_db()
        data = _db.generate_excel_report()
        return send_file(
            io.BytesIO(data),
            as_attachment=True,
            download_name="job_applications.xlsx",
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── Config ────────────────────────────────────────────────────────────────────

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


# ── Roles ─────────────────────────────────────────────────────────────────────

@app.route("/api/roles")
def api_roles():
    try:
        cfg = load_config()
        import scraper
        roles = scraper.extract_roles_from_resume(cfg)
        return jsonify({"roles": roles})
    except Exception as exc:
        return jsonify({"error": str(exc), "roles": []}), 200


# ── File uploads (resume + cover letter only) ─────────────────────────────────

_ALLOWED_DOC = {".pdf", ".docx"}


def _save_upload(file_storage, stem: str, allowed: set[str]) -> "tuple[str, str]":
    ext = Path(file_storage.filename).suffix.lower()
    if ext not in allowed:
        raise ValueError(f"File type {ext} not allowed (expected {allowed})")
    _UPLOADS_DIR.mkdir(exist_ok=True)
    for old in _UPLOADS_DIR.glob(f"{stem}.*"):
        old.unlink(missing_ok=True)
    filename = f"{stem}{ext}"
    dest = _UPLOADS_DIR / filename
    file_storage.save(str(dest))
    return filename, str(dest)


@app.route("/api/upload/resume", methods=["POST"])
def api_upload_resume():
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "No file"}), 400
    try:
        filename, _ = _save_upload(f, "resume_en", _ALLOWED_DOC)
        import scraper as _scraper
        _scraper._extracted_roles_cache = None
        return jsonify({"ok": True, "filename": filename})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/upload/cover_letter", methods=["POST"])
def api_upload_cover_letter():
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "No file"}), 400
    try:
        filename, _ = _save_upload(f, "cover_letter", _ALLOWED_DOC)
        return jsonify({"ok": True, "filename": filename})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/upload/status")
def api_upload_status():
    result = {}
    for stem, key in [("resume_en", "resume"), ("cover_letter", "cover_letter")]:
        found = next(_UPLOADS_DIR.glob(f"{stem}.*"), None) if _UPLOADS_DIR.exists() else None
        result[key] = {"exists": found is not None, "filename": found.name if found else None}
    return jsonify(result)


# ── Pipeline workers + routes ─────────────────────────────────────────────────

def _run_in_thread(fn, *args) -> bool:
    global _pipeline_thread
    with _pipeline_lock:
        if _pipeline_thread and _pipeline_thread.is_alive():
            return False
        _pipeline_thread = threading.Thread(target=fn, args=args, daemon=True)
        _pipeline_thread.start()
    return True


def _scrape_worker():
    _pipeline_set(step=1, error=None)
    try:
        import main as _main
        jobs = _main.run_scrape_only()
        _pipeline_set(step=2, jobs_count=len(jobs))
    except Exception as exc:
        _pipeline_set(step=-1, error=str(exc))


def _match_worker():
    _pipeline_set(step=3, error=None)
    try:
        import main as _main
        jobs = _main.run_match_only()
        _pipeline_set(step=4, matched_count=len(jobs))
    except Exception as exc:
        _pipeline_set(step=-1, error=str(exc))


def _apply_worker(job_urls):
    _pipeline_set(step=5, error=None)
    try:
        import main as _main
        results = _main.run_apply_only(job_urls=job_urls)
        _pipeline_set(step=6, results=results)
    except Exception as exc:
        _pipeline_set(step=-1, error=str(exc))


@app.route("/api/pipeline/scrape", methods=["POST"])
def api_pipeline_scrape():
    if not _run_in_thread(_scrape_worker):
        return jsonify({"error": "Pipeline already running"}), 409
    return jsonify({"ok": True})


@app.route("/api/pipeline/match", methods=["POST"])
def api_pipeline_match():
    if not _run_in_thread(_match_worker):
        return jsonify({"error": "Pipeline already running"}), 409
    return jsonify({"ok": True})


@app.route("/api/pipeline/apply", methods=["POST"])
def api_pipeline_apply():
    body = request.get_json(force=True) or {}
    job_urls = None if body.get("all") else body.get("urls")
    if not _run_in_thread(_apply_worker, job_urls):
        return jsonify({"error": "Pipeline already running"}), 409
    return jsonify({"ok": True})


@app.route("/api/pipeline/status")
def api_pipeline_status():
    with _pipeline_lock:
        return jsonify(dict(_pipeline))


@app.route("/api/pipeline/scraped_jobs")
def api_pipeline_scraped_jobs():
    try:
        import db as _db
        _db.init_db()
        with _db._conn() as db:
            rows = db.execute(
                "SELECT id, title, company, location, url, source, scraped_at"
                " FROM scraped_jobs"
                " ORDER BY scraped_at DESC"
            ).fetchall()
        return jsonify({"jobs": [dict(r) for r in rows]})
    except Exception:
        p = _UPLOADS_DIR / "scraped_jobs.json"
        if not p.exists():
            return jsonify({"jobs": []})
        return jsonify({"jobs": json.loads(p.read_text(encoding="utf-8"))})


@app.route("/api/pipeline/scraped_jobs/<int:job_id>", methods=["DELETE"])
def api_pipeline_scraped_job_delete(job_id):
    try:
        import db as _db
        _db.init_db()
        with _db._conn() as db:
            db.execute("DELETE FROM scraped_jobs WHERE id = ?", (job_id,))
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/pipeline/matched_jobs")
def api_pipeline_matched_jobs():
    try:
        import db as _db
        _db.init_db()
        with _db._conn() as db:
            rows = db.execute("""
                SELECT s.id AS scraped_id, s.title, s.company, s.location, s.url, s.source,
                       m.match_score, m.interview_chance, m.german_level AS german_level_required,
                       m.match_summary
                FROM matched_jobs m
                JOIN scraped_jobs s ON s.id = m.scraped_job_id
                ORDER BY m.match_score DESC
            """).fetchall()
        return jsonify({"jobs": [dict(r) for r in rows]})
    except Exception:
        p = _UPLOADS_DIR / "matched_jobs.json"
        if not p.exists():
            return jsonify({"jobs": []})
        return jsonify({"jobs": json.loads(p.read_text(encoding="utf-8"))})


# ── Agent subprocess ──────────────────────────────────────────────────────────

@app.route("/api/agent/start", methods=["POST"])
def api_agent_start():
    global _agent_proc
    with _agent_lock:
        if _agent_proc and _agent_proc.poll() is None:
            return jsonify({"error": "already running", "pid": _agent_proc.pid})
        _agent_proc = subprocess.Popen(
            [sys.executable, str(_MAIN_PY)],
            cwd=str(_MAIN_PY.parent),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return jsonify({"pid": _agent_proc.pid})


@app.route("/api/agent/stop", methods=["POST"])
def api_agent_stop():
    global _agent_proc
    # Terminate agent subprocess if one is running
    with _agent_lock:
        if _agent_proc and _agent_proc.poll() is None:
            _agent_proc.terminate()
            try:
                _agent_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _agent_proc.kill()
    # Also reset the pipeline-thread state so a fresh run can start immediately.
    # The background thread may finish on its own, but resetting step=0 means
    # the next poll won't auto-advance the wizard.
    _pipeline_set(step=0, error=None)
    return jsonify({"ok": True})


@app.route("/api/agent/status")
def api_agent_status():
    # Report running=True for either: agent subprocess OR active pipeline thread.
    if _agent_proc is not None and _agent_proc.poll() is None:
        return jsonify({"running": True, "pid": _agent_proc.pid})
    with _pipeline_lock:
        thread_running = (
            _pipeline_thread is not None
            and _pipeline_thread.is_alive()
            and _pipeline.get("step", 0) in (1, 3, 5)
        )
    return jsonify({"running": thread_running, "pid": None})


# ── SSE log stream ────────────────────────────────────────────────────────────

@app.route("/api/log/stream")
def api_log_stream():
    def generate():
        try:
            cfg      = load_config()
            log_path = cfg["paths"]["agent_dir"] / "agent.log"
        except Exception as exc:
            yield "data: " + json.dumps(f"[error] Could not load config: {exc}") + _SSE_SEP
            return
        if log_path.exists():
            try:
                lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
                for line in lines[-100:]:
                    if line and "werkzeug" not in line:
                        yield "data: " + json.dumps(line) + _SSE_SEP
            except Exception as exc:
                yield "data: " + json.dumps(f"[error reading log] {exc}") + _SSE_SEP
        else:
            yield "data: " + json.dumps("[waiting for agent.log…]") + _SSE_SEP

        offset  = log_path.stat().st_size if log_path.exists() else 0
        last_ka = time.time()
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
                            if line.strip() and "werkzeug" not in line:
                                yield "data: " + json.dumps(line) + _SSE_SEP
                except Exception:
                    pass
            else:
                time.sleep(1)
            time.sleep(0.4)
            if time.time() - last_ka > 15:
                yield ": keepalive" + _SSE_SEP
                last_ka = time.time()

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    _UPLOADS_DIR.mkdir(exist_ok=True)
    # Initialise DB on startup (no-op if already exists)
    try:
        import db as _db
        _db.init_db()
    except Exception:
        pass
    print("Job Hunt Dashboard  →  http://localhost:5000")
    print("Press Ctrl+C to stop.\n")
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
