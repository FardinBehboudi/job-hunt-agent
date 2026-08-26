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

import logging

import os

import subprocess

import sys

import threading

import time

from datetime import datetime

from pathlib import Path



import yaml

from dotenv import load_dotenv, set_key as dotenv_set_key

from flask import Flask, Response, jsonify, render_template_string, request, send_file



load_dotenv()



sys.path.insert(0, str(Path(__file__).parent.parent))

from core.config import load_config

import core.config as _config_module



# Setup logging (dummy function - logging is already configured)

def _setup_logging(cfg):

    """Setup logging from config (no-op for now)."""

    pass



app = Flask(__name__)

from dashboard.email_admin import email_admin_bp  # noqa: E402
app.register_blueprint(email_admin_bp)

log = logging.getLogger(__name__)



_PROJECT_ROOT = Path(__file__).resolve().parent.parent

_CONFIG_PATH = _PROJECT_ROOT / "data" / "config.yaml"

_MAIN_PY     = _PROJECT_ROOT / "core" / "main.py"

_UPLOADS_DIR = _PROJECT_ROOT / "uploads"

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

.card[data-sub] { transition: border-color 0.15s, box-shadow 0.15s; }

.card[data-sub]:hover { border-color: #388bfd; box-shadow: 0 0 0 1px #1d4ed8; }

.card.card-active { border: 2px solid #60b4ff !important; box-shadow: 0 0 0 2px rgba(96,180,255,0.15); }



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

  font-size: 0.82rem; color: #4b5563; flex-shrink: 0;

  background: none; border: none; padding: 0; font-family: inherit; }

.wz-ind-step.active { color: #f0f6fc; }

.wz-ind-step.done          { color: #34d399; }

.wz-ind-step.done:hover    { color: #6ee7b7; }

.wz-ind-step.done:hover .wz-num { border-color: #6ee7b7; }

.wz-ind-step:not([disabled]) { cursor: pointer; }

.wz-ind-step[disabled]     { cursor: default; opacity: 0.45; }

.wz-num { width: 22px; height: 22px; border-radius: 50%;

  display: flex; align-items: center; justify-content: center;

  font-size: 0.72rem; font-weight: 700;

  background: #21262d; border: 1px solid #30363d; flex-shrink: 0; color: #8b949e; }

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

.config-form { padding: 14px 18px; display: flex; flex-direction: column; gap: 9px; }

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

.tag-list { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 8px; margin-bottom: 10px; min-height: 24px; }

.tag-chip { display: inline-flex; align-items: center; gap: 3px;

  background: #1c2128; border: 1px solid #30363d; color: #c9d1d9;

  padding: 2px 8px; border-radius: 6px; font-size: 0.78rem; }

.tag-rm { background: none; border: none; color: #64748b; cursor: pointer;

  padding: 0 1px; font-size: 0.9rem; line-height: 1; transition: color 0.1s; }

.tag-rm:hover { color: #f87171; }

.tag-input-row { display: flex; gap: 5px; position: relative; }

.tag-input-row input { flex: 1; background: #0d1117; border: 1px solid #30363d; color: #e2e8f0;

  border-radius: 6px; padding: 5px 8px; font-size: 0.81rem; outline: none; transition: border-color 0.15s; }

.tag-input-row input:focus { border-color: #58a6ff; }

.tag-autocomplete { position: absolute; top: 100%; left: 0; right: 60px; background: #0d1117; border: 1px solid #30363d;

  border-top: none; border-radius: 0 0 6px 6px; max-height: 200px; overflow-y: auto; z-index: 100; display: none; }

.tag-autocomplete.show { display: block; }

.tag-autocomplete-item { padding: 6px 8px; cursor: pointer; font-size: 0.81rem; color: #e2e8f0; }

.tag-autocomplete-item:hover { background: #161b22; color: #58a6ff; }

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

.cfg-select { background:#0d1117; border:1px solid #30363d; color:#e2e8f0;

  border-radius:6px; padding:6px 10px; font-size:0.83rem; outline:none;

  transition:border-color 0.15s; width:100%; }

.cfg-select:focus { border-color:#58a6ff; }

.lang-list { display:flex; flex-direction:column; gap:5px; margin-bottom:4px; min-height:10px; }

.lang-row { display:flex; align-items:center; gap:6px; }

.lang-row input { flex:1; background:#0d1117; border:1px solid #30363d; color:#e2e8f0;

  border-radius:6px; padding:5px 8px; font-size:0.82rem; outline:none; transition:border-color 0.15s; }

.lang-row input:focus { border-color:#58a6ff; }

.lang-rm { background:none; border:none; color:#64748b; cursor:pointer; font-size:1rem; padding:0 4px; flex-shrink:0; }

.lang-rm:hover { color:#f87171; }

.agent-2col { display:grid; grid-template-columns:2fr 3fr; gap:16px; align-items:start; margin-bottom:16px; }

@media (max-width:1024px) { .agent-2col { grid-template-columns:1fr; } }

.agent-col-left { display:flex; flex-direction:column; gap:16px; }

.wz-step { border-top:1px solid #21262d; padding:0; }

.wz-step > .panel-hd { cursor:default; }

.sdoc-list { display:flex; flex-direction:column; gap:0; }

.sdoc-row { display:flex; align-items:center; gap:8px; padding:6px 0; border-bottom:1px solid #21262d; }

.sdoc-row:last-child { border-bottom:none; }

.sdoc-label { flex:1; font-size:0.82rem; color:#8b949e; }

.sdoc-status { font-size:0.76rem; color:#64748b; white-space:nowrap; }

.sdoc-status.uploaded { color:#34d399; }



/* ── Apply tab ── */

.apply-2col { display:grid; grid-template-columns:1fr 1fr; gap:16px; align-items:start; }

@media (max-width:960px) { .apply-2col { grid-template-columns:1fr; } }

.apply-session-bar { display:flex; align-items:center; gap:18px; flex-wrap:wrap;

  background:#161b22; border:1px solid #21262d; border-radius:8px; padding:12px 18px;

  margin-bottom:16px; font-size:0.84rem; }

.asb-stat { display:flex; flex-direction:column; gap:2px; }

.asb-label { font-size:0.72rem; color:#64748b; text-transform:uppercase; letter-spacing:0.04em; }

.asb-value { font-size:1.2rem; font-weight:700; color:#f0f6fc; }

.asb-value.success { color:#34d399; }

.asb-value.manual  { color:#f59e0b; }

.asb-value.failed  { color:#f87171; }

.asb-spacer { flex:1; }

.apply-job-card { background:#161b22; border:1px solid #21262d; border-radius:8px;

  padding:12px 14px; margin-bottom:8px; display:flex; align-items:flex-start; gap:10px; }

.apply-job-card.status-running  { border-color:#58a6ff; background:#0f1929; }

.apply-job-card.status-done     { border-color:#34d399; background:#0a1f14; }

.apply-job-card.status-failed   { border-color:#f87171; background:#1f0a0a; }

.apply-job-card.status-manual   { border-color:#f59e0b; background:#1f1500; }

.ajc-info { flex:1; min-width:0; }

.ajc-title { font-size:0.88rem; font-weight:600; color:#f0f6fc; white-space:nowrap;

  overflow:hidden; text-overflow:ellipsis; }

.ajc-company { font-size:0.78rem; color:#8b949e; margin-top:2px; }

.ajc-note { font-size:0.75rem; color:#64748b; margin-top:4px; font-style:italic; }

.ajc-badges { display:flex; gap:6px; align-items:center; flex-shrink:0; margin-left:auto; }

.platform-badge { font-size:0.68rem; padding:2px 7px; border-radius:4px; font-weight:600;

  text-transform:uppercase; letter-spacing:0.05em; }

.pb-linkedin      { background:#0a66c2; color:#fff; }

.pb-greenhouse    { background:#24a960; color:#fff; }

.pb-lever         { background:#3333cc; color:#fff; }

.pb-smartrecruiters { background:#ff6a40; color:#fff; }

.pb-ashby         { background:#6366f1; color:#fff; }

.pb-workday       { background:#d73b4a; color:#fff; }

.pb-personio      { background:#1eb980; color:#fff; }

.pb-workable      { background:#2b5ce6; color:#fff; }

.pb-recruitee     { background:#5b4cf5; color:#fff; }

.pb-teamtailor    { background:#40c0cb; color:#000; }

.pb-unknown,.pb-other { background:#30363d; color:#8b949e; }

.status-icon { font-size:1.1rem; flex-shrink:0; }

.apply-log { background:#0d1117; border:1px solid #21262d; border-radius:8px;

  padding:0; font-family:monospace; font-size:0.78rem; display:flex; flex-direction:column;

  max-height:640px; overflow:hidden; position:sticky; top:16px; }

.apply-log-hd { padding:10px 14px; border-bottom:1px solid #21262d; font-size:0.82rem;

  font-weight:600; color:#8b949e; display:flex; align-items:center; gap:8px; flex-shrink:0; }

.apply-log-body { flex:1; overflow-y:auto; padding:10px 14px; font-family:'Courier New',monospace; font-size:0.76rem; line-height:1.6; }

.alog-line { padding:2px 0; line-height:1.4; }

.alog-start   { color:#58a6ff; }

.alog-step    { color:#8b949e; }

.alog-answer  { color:#a8dadc; }

.alog-success { color:#34d399; }

.alog-manual  { color:#f59e0b; }

.alog-failed  { color:#f87171; }

.alog-delay   { color:#374151; }

.alog-done    { color:#f0f6fc; font-weight:600; }

.apply-cards-wrap { overflow-y:auto; }

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

.ajc-actions { display:flex; gap:5px; margin-top:6px; flex-wrap:wrap; align-items:center; }

.ajc-btn {
  background:#21262d; border:1px solid #30363d; color:#8b949e;
  border-radius:6px; padding:3px 10px; font-size:0.74rem; cursor:pointer;
  font-weight:500; transition:background .15s; white-space:nowrap; text-decoration:none;
  display:inline-block; line-height:1.5;
}

.ajc-btn:hover { background:#30363d; color:#e2e8f0; }

.ajc-btn-applied  { border-color:#16a34a; color:#4ade80; background:#0d2b1a; }
.ajc-btn-applied:hover { background:#14532d; }

.ajc-btn-retry    { border-color:#1d4ed8; color:#60a5fa; background:#0d1f3a; }
.ajc-btn-retry:hover { background:#1e3a5f; }

.ajc-btn-remove   { border-color:#7f1d1d; color:#f87171; background:#1a0a0a; }
.ajc-btn-remove:hover { background:#2d1515; }

.ajc-btn-open     { border-color:#30363d; color:#8b949e; background:#21262d; }
.ajc-btn-exclude  { border-color:#374151; color:#6b7280; background:#1a1a1a; }

.ajc-situation {
  font-size:0.74rem; font-weight:600; padding:3px 9px; border-radius:6px;
  display:inline-block; cursor:default; white-space:nowrap;
}
.ajc-situation.sit-done    { background:#0d2b1a; color:#4ade80; border:1px solid #16a34a; }
.ajc-situation.sit-manual  { background:#2a1500; color:#fb923c; border:1px solid #92400e; }
.ajc-situation.sit-failed  { background:#1a0a0a; color:#f87171; border:1px solid #7f1d1d; }
.ajc-situation.sit-expired { background:#111;    color:#6b7280; border:1px solid #374151; }
.ajc-situation.sit-pending { background:#0d1117; color:#64748b; border:1px solid #1e293b; }

.apply-job-card.status-expired { border-left:3px solid #374151; opacity:.65; }

.section-dot.dot-expired  { color:#4b5563; }

.section-dot.dot-failed   { color:#ef4444; }

/* Compact cards */

.apply-job-card { padding:6px 10px !important; gap:8px !important; }

.ajc-title { font-size:0.82rem !important; }

.ajc-company { font-size:0.73rem !important; }

.ajc-note { font-size:0.71rem !important; }

.ajc-btn { padding:3px 10px !important; font-size:0.74rem !important; }

.apply-section-header { padding:4px 0 6px 0 !important; margin-bottom:6px !important; font-size:0.72rem !important; }

.apply-job-card { transition: opacity .3s, transform .3s; }

.apply-job-card.status-running { border-left:3px solid #3b82f6; }

.apply-job-card.status-done    { border-left:3px solid #22c55e; opacity:.85; }

.apply-job-card.status-manual  { border-left:3px solid #f97316; }

.apply-job-card.status-failed  { border-left:3px solid #ef4444; opacity:.75; }

.apply-empty { text-align:center; color:#64748b; padding:40px 20px; font-size:0.85rem; }



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

/* Cache source badges */

.badge-new    { display:inline-block; padding:2px 7px; border-radius:99px; font-size:0.72rem;

  background:#0d2e1e; color:#34d399; border:1px solid #2d6a4f; white-space:nowrap; }

.badge-cached { display:inline-block; padding:2px 7px; border-radius:99px; font-size:0.72rem;

  background:#0c2a4a; color:#58a6ff; border:1px solid #1e3a5f; white-space:nowrap; }

.badge-source { display:inline-block; padding:2px 7px; border-radius:99px; font-size:0.72rem;

  background:#161b22; color:#8b949e; border:1px solid #30363d; white-space:nowrap; }

/* Cache stats bar */

.cache-stats-bar { display:flex; gap:18px; padding:8px 18px; background:#0d1117;

  border-bottom:1px solid #21262d; font-size:0.78rem; color:#64748b; align-items:center; }

.cache-stats-bar strong { color:#c9d1d9; }



/* ── Job detail side panel ── */

.job-detail-overlay { position:fixed; inset:0; background:rgba(0,0,0,0.45); z-index:300; }

.job-detail-panel { position:fixed; top:0; right:0; bottom:0; width:480px; max-width:100vw;

  background:#161b22; border-left:1px solid #30363d; z-index:301; display:flex;

  flex-direction:column; overflow:hidden;

  transform:translateX(100%); transition:transform 0.22s ease; }

.job-detail-panel.open { transform:translateX(0); }

.job-detail-header { display:flex; align-items:flex-start; justify-content:space-between;

  padding:16px 18px 14px; border-bottom:1px solid #21262d; flex-shrink:0; }

.job-detail-title { font-size:1rem; font-weight:700; color:#f0f6fc; line-height:1.3; flex:1; }

.job-detail-close { background:none; border:none; color:#64748b; cursor:pointer;

  font-size:1.4rem; line-height:1; padding:0 0 0 10px; flex-shrink:0; }

.job-detail-close:hover { color:#e2e8f0; }

.job-detail-body { flex:1; overflow-y:auto; padding:16px 18px; display:flex;

  flex-direction:column; gap:14px; }

.job-detail-meta { font-size:0.83rem; color:#94a3b8; }

.job-detail-badges { display:flex; gap:6px; flex-wrap:wrap; align-items:center; }

.job-detail-section-label { font-size:0.68rem; text-transform:uppercase;

  letter-spacing:0.07em; color:#475569; margin-bottom:4px; }

.job-detail-summary { font-size:0.83rem; color:#c9d1d9; line-height:1.5; }

.job-detail-skip { font-size:0.8rem; color:#f87171; background:#450a0a;

  border:1px solid #7f1d1d; border-radius:6px; padding:6px 10px; }

.job-detail-desc { font-size:0.8rem; color:#8b949e; line-height:1.55;

  white-space:pre-wrap; word-break:break-word;

  max-height:400px; overflow-y:auto; }

.job-detail-actions { display:flex; gap:8px; flex-shrink:0; padding:14px 18px;

  border-top:1px solid #21262d; }

.pip-table tbody tr.clickable { cursor:pointer; }

.pip-table tbody tr.row-dimmed { opacity:0.42; }

.pip-table tbody tr.row-dimmed:hover { opacity:0.72; background:#1c2128; }

.badge-below { display:inline-block; padding:1px 6px; border-radius:99px; font-size:0.68rem;

  background:#1c2128; color:#475569; border:1px solid #30363d; white-space:nowrap; }



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



/* ── Audit modal ── */

.audit-summary { display:grid; grid-template-columns:1fr auto auto; gap:0;

  border:1px solid #21262d; border-radius:8px; overflow:hidden; }

.audit-row { display:contents; }

.audit-row > * { padding:9px 14px; font-size:0.83rem; border-bottom:1px solid #21262d; }

.audit-row:last-child > * { border-bottom:none; }

.audit-label { color:#c9d1d9; }

.audit-n { text-align:right; font-weight:700; color:#f0f6fc; white-space:nowrap; }

.audit-pct { text-align:right; color:#64748b; white-space:nowrap; min-width:48px; }

.audit-row.passed > * { background:#0d2e1e; }

.audit-row.passed .audit-label { color:#34d399; }

.audit-row.failed > * { background:#0d1117; }

.audit-section { margin-top:14px; border:1px solid #21262d; border-radius:8px; overflow:hidden; }

.audit-section summary { padding:9px 14px; font-size:0.8rem; font-weight:600;

  color:#94a3b8; cursor:pointer; list-style:none; display:flex;

  align-items:center; justify-content:space-between; background:#161b22;

  user-select:none; }

.audit-section summary::-webkit-details-marker { display:none; }

.audit-section[open] summary { border-bottom:1px solid #21262d; color:#c9d1d9; }

.audit-section summary::after { content:'▸'; font-size:0.75rem; }

.audit-section[open] summary::after { content:'▾'; }

.audit-jobs { max-height:260px; overflow-y:auto; }

.audit-job-row { display:grid; grid-template-columns:1fr auto auto;

  gap:8px; padding:7px 14px; border-bottom:1px solid #0d1117;

  font-size:0.78rem; align-items:start; }

.audit-job-row:last-child { border-bottom:none; }

.audit-job-title { color:#e2e8f0; font-weight:600; white-space:nowrap;

  overflow:hidden; text-overflow:ellipsis; }

.audit-job-co { color:#64748b; font-size:0.73rem; }

.audit-job-score { white-space:nowrap; text-align:right; }

.audit-job-reason { grid-column:1/-1; color:#94a3b8; font-size:0.73rem;

  padding-top:2px; }



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

      <div style="display:flex;align-items:center;gap:12px;">

        <div id="refresh-pill" class="refresh-pill">

          Auto-refreshes every 60s &nbsp;&middot;&nbsp; Last update: <span id="last-update">—</span>

        </div>

        <button class="btn-danger btn-sm" onclick="restartPipeline()"
                title="Force-clear a stuck scrape/match/apply run and start fresh">
          &#128260; Restart Pipeline
        </button>

      </div>

    </div>

    <nav class="tab-nav">

      <button class="tab-btn active" data-tab="dashboard" onclick="showTab('dashboard')">&#128202; Dashboard</button>

      <button class="tab-btn"        data-tab="agent"     onclick="showTab('agent')">&#129302; Job Hunt Agent</button>

      <button class="tab-btn"        data-tab="email-admin" onclick="showTab('email-admin')">&#128231; Email Admin</button>

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



    <!-- Stat cards — always visible, click to filter content below -->

    <div class="cards" style="margin-bottom:8px;">

      <div class="card c-total card-active" data-sub="overview"   onclick="showSubTab('overview')"   title="Overview" style="cursor:pointer"><div class="n" id="s-total">—</div><div class="lbl">Total</div></div>

      <div class="card c-applied"           data-sub="applied"    onclick="showSubTab('applied')"    title="Applied jobs" style="cursor:pointer"><div class="n" id="s-applied">—</div><div class="lbl">In Review</div></div>

      <div class="card c-interview"         data-sub="interviews" onclick="showSubTab('interviews')" title="Interviews" style="cursor:pointer"><div class="n" id="s-interviews">—</div><div class="lbl">Interviews</div></div>

      <div class="card c-rejected"          data-sub="rejected"   onclick="showSubTab('rejected')"   title="Rejected" style="cursor:pointer"><div class="n" id="s-rejected">—</div><div class="lbl">Rejected</div></div>

      <div class="card c-offer"             data-sub="offers"     onclick="showSubTab('offers')"     title="Offers" style="cursor:pointer"><div class="n" id="s-offers">—</div><div class="lbl">Offers</div></div>

    </div>

    <div style="display:flex;justify-content:flex-end;margin-bottom:14px;">

      <a class="btn-link" href="/api/download/tracker" id="dl-btn">&#11015; Download Tracker</a>

    </div>



    <!-- Sub-tab: Overview -->

    <div id="sub-overview" class="sub-content">

      <!-- ov-events and ov-tasks kept as hidden sinks so JS calls are no-ops -->
      <div id="ov-events" style="display:none"></div>
      <div id="ov-tasks"  style="display:none"></div>



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

              <th>Type</th><th>Time</th><th>Notes / Interviewers / Link</th>

              <th style="width:48px"></th>

            </tr></thead>

            <tbody id="interviews-tbody"></tbody>

          </table>

          <div id="interviews-empty" class="ov-empty hidden">No upcoming interviews.</div>

        </div>

      </div>

      <!-- Notes detail modal -->
      <div id="iv-notes-overlay" onclick="if(event.target===this)document.getElementById('iv-notes-overlay').style.display='none'"
        style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.65);z-index:1000;align-items:center;justify-content:center;">
        <div style="background:#161b22;border:1px solid #30363d;border-radius:12px;padding:24px 28px;max-width:560px;width:92%;box-shadow:0 8px 32px rgba(0,0,0,0.5);position:relative;">
          <button onclick="document.getElementById('iv-notes-overlay').style.display='none'"
            style="position:absolute;top:12px;right:14px;background:none;border:none;color:#8b949e;font-size:1.2rem;cursor:pointer;">✕</button>
          <div id="iv-notes-header" style="font-size:0.8rem;color:#38bdf8;font-weight:600;margin-bottom:10px;padding-right:24px;"></div>
          <div id="iv-notes-body" style="font-size:0.85rem;color:#c9d1d9;line-height:1.7;white-space:pre-wrap;word-break:break-word;"></div>
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



    <!-- Sub-tabs: Applied / Interviews / Rejected / Offers share same filter+table structure -->

    <div id="sub-applied"    class="sub-content hidden"></div>

    <div id="sub-interviews" class="sub-content hidden"></div>

    <div id="sub-rejected"   class="sub-content hidden"></div>

    <div id="sub-offers"     class="sub-content hidden"></div>



  </div><!-- /tab-dashboard -->



  <!-- ═══ AGENT TAB ═══ -->

  <div id="tab-agent" class="tab-content hidden">



    <!-- Agent Control (wizard) -->

    <div class="panel mb16">

      <div class="panel-hd">

        <span class="panel-title">&#129302; Agent Control</span>

        <div id="agent-cache-stats" class="hidden" style="display:flex;gap:16px;font-size:0.78rem;color:#64748b;flex-wrap:wrap;align-items:center;">

          <span>&#128190; <strong id="acs-total">—</strong> seen</span>

          <span>&#128203; <strong id="acs-unseen">—</strong> unscored</span>

          <span>&#9989; <strong id="acs-applied">—</strong> applied</span>

          <span>&#128683; <strong id="acs-dismissed">—</strong> dismissed</span>

        </div>

      </div>



      <!-- Wizard step indicator -->

      <div class="wz-indicator" style="padding:12px 18px 0;">

        <button class="wz-ind-step active" id="step-ind-1" onclick="navigateToStep(1)"><div class="wz-num">1</div><span>Scrape</span></button>

        <div class="wz-sep"></div>

        <button class="wz-ind-step" id="step-ind-2" onclick="navigateToStep(2)" disabled><div class="wz-num">2</div><span>Review</span></button>

        <div class="wz-sep"></div>

        <button class="wz-ind-step" id="step-ind-3" onclick="navigateToStep(3)" disabled><div class="wz-num">3</div><span>Match</span></button>

        <div class="wz-sep"></div>

        <button class="wz-ind-step" id="step-ind-4" onclick="navigateToStep(4)" disabled><div class="wz-num">4</div><span>Select</span></button>

        <div class="wz-sep"></div>

        <button class="wz-ind-step" id="step-ind-5" onclick="navigateToStep(5)" disabled><div class="wz-num">5</div><span>Apply</span></button>

      </div>



      <!-- Step 1: Scrape -->

      <div id="wz-1" class="wz-step">

        <div class="panel-hd" style="border-top:none;">

          <span class="panel-title" style="font-size:0.85rem;color:#8b949e;">Step 1 — Scrape</span>

          <button id="btn-scrape" class="btn-primary btn-sm" onclick="startScrape()">&#128270; Start Scraping</button>

        </div>

        <div style="padding:12px 18px 18px;">

          <p style="color:#8b949e;font-size:0.84rem;margin-bottom:0;">

            Searches LinkedIn for jobs posted in the last 24 h matching your resume.

          </p>

          <div id="scrape-progress" class="hidden" style="margin-top:14px;">

            <div class="progress-bar"><div id="scrape-progress-fill" class="progress-fill" style="width:0%;transition:width 0.4s ease;"></div></div>

            <div id="scrape-msg"     style="margin-top:8px;font-size:0.8rem;color:#64748b;"></div>

            <div id="scrape-current" style="margin-top:3px;font-size:0.76rem;color:#94a3b8;"></div>

          </div>

        </div>

      </div>



      <!-- Step 2: Review -->

      <div id="wz-2" class="wz-step hidden">

        <div class="panel-hd" style="border-top:none;">

          <span class="panel-title" style="font-size:0.85rem;color:#8b949e;">Step 2 — Review Scraped Jobs</span>

          <button class="btn-primary btn-sm" onclick="goToMatch()">Next: Match &amp; Filter &#8594;</button>

        </div>

        <div id="scrape-cache-stats" class="cache-stats-bar hidden">

          <span>&#128994; <strong id="cs-new">0</strong> new</span>

          <span>&#128290; <strong id="cs-cached">0</strong> from cache</span>

          <span id="cs-outside-wrap" class="hidden" style="color:#475569;">

            &#128683; <strong id="cs-outside">0</strong> outside time window (hidden)

          </span>

        </div>

        <!-- job-limit selector — mirrors Step 3 threshold control -->
        <div id="step2-limit-banner" class="hidden" style="padding:9px 18px;background:#0d1f14;border-bottom:1px solid #21262d;font-size:0.82rem;color:#22c55e;font-weight:600;">
          &#10003; <span id="step2-above-cnt">0</span> jobs selected for matching &middot; <span id="step2-below-cnt">0</span> excluded
        </div>

        <div id="step2-limit-bar" class="hidden" style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:10px 18px;border-bottom:1px solid #21262d;background:#161b22;">
          <label style="font-size:0.82rem;font-weight:600;color:#8b949e;white-space:nowrap;">Jobs to Match:</label>
          <input type="range" id="step2-limit-slider" min="1" max="100" value="100"
                 style="width:120px;accent-color:#3b82f6;cursor:pointer;" oninput="onLimitSlider()">
          <input type="number" id="step2-limit-num" min="1" max="100" value="100"
                 style="width:58px;padding:4px 6px;border:1px solid #30363d;border-radius:6px;font-size:0.84rem;background:#0d1117;color:#e2e8f0;"
                 oninput="onLimitNum()">
          <span style="font-size:0.77rem;color:#64748b;">
            Showing top <span id="step2-limit-hint-val">100</span> jobs for matching &nbsp;(drag to adjust)
          </span>
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

                <th>Title</th><th>Company</th><th>Location</th><th>Posted</th><th>Source</th><th>Actions</th>

              </tr>

            </thead>

            <tbody id="scraped-tbody"></tbody>

          </table>

        </div>

      </div>



      <!-- Step 3: Match -->

      <div id="wz-3" class="wz-step hidden">

        <div class="panel-hd" style="border-top:none;">

          <span class="panel-title" style="font-size:0.85rem;color:#8b949e;">Step 3 — Match &amp; Score</span>

          <div style="display:flex;align-items:center;gap:8px;">

            <button id="btn-match" class="btn-primary btn-sm" onclick="startMatch()">&#129302; Run Matching</button>

            <button class="btn-primary btn-sm" id="btn-goto-select-top" onclick="goToSelect()" disabled

                    style="display:none;">Next: Select Jobs (0) &#8594;</button>

          </div>

        </div>

        <div id="match-progress" class="hidden" style="padding:14px 18px 8px;">

          <div class="progress-bar"><div id="match-progress-fill" class="progress-fill" style="width:0%;transition:width 0.4s ease;"></div></div>

          <div id="match-msg"     style="margin-top:8px;font-size:0.8rem;color:#64748b;"></div>

          <div id="match-current" style="margin-top:3px;font-size:0.76rem;color:#94a3b8;"></div>

        </div>

        <!-- results panel — shown after scoring completes -->

        <div id="match-results-panel" class="hidden">

          <!-- completion banner -->

          <div style="padding:9px 18px;background:#0d1f14;border-bottom:1px solid #21262d;font-size:0.82rem;color:#22c55e;font-weight:600;">

            &#10003; Matching complete &mdash;

            <span id="match-above-cnt">0</span> above threshold &middot;

            <span id="match-below-cnt">0</span> below

          </div>

          <!-- threshold editor row -->

          <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:10px 18px;border-bottom:1px solid #21262d;background:#161b22;">

            <label style="font-size:0.82rem;font-weight:600;color:#8b949e;white-space:nowrap;">Min Score Threshold:</label>

            <input type="range" id="match-threshold-slider" min="0" max="100" value="70"

                   style="width:120px;accent-color:#3b82f6;cursor:pointer;" oninput="onThresholdSlider()">

            <input type="number" id="match-threshold-num" min="0" max="100" value="70"

                   style="width:58px;padding:4px 6px;border:1px solid #30363d;border-radius:6px;font-size:0.84rem;background:#0d1117;color:#e2e8f0;"

                   oninput="onThresholdNum()">

            <button class="btn-sm btn-primary" onclick="saveThreshold()">Apply</button>

            <span id="match-threshold-saved" style="font-size:0.78rem;color:#22c55e;opacity:0;transition:opacity 0.4s;">&#10003; Saved</span>

          </div>

          <div style="display:flex;align-items:center;gap:12px;padding:6px 18px 8px;">

            <span id="match-threshold-hint" style="font-size:0.77rem;color:#64748b;">

              Showing jobs scored &ge; <span id="match-threshold-hint-val">70</span>

              &nbsp;(drag to adjust, then Apply)

            </span>

            <button class="btn-sm" onclick="openAuditModal()" style="margin-left:4px;">&#128202; Audit</button>

          </div>

          <!-- inline results table -->

          <div style="overflow-x:auto;max-height:420px;overflow-y:auto;">

            <table class="pip-table" id="match-results-table" style="width:100%;table-layout:fixed;">

              <thead>

                <tr>

                  <th style="width:32%;">Title</th>

                  <th style="width:18%;">Company</th>

                  <th style="width:13%;">Location</th>

                  <th style="width:68px;">Score</th>

                  <th style="width:78px;">Chance</th>

                  <th style="width:100px;">Type</th>

                  <th style="width:90px;">Actions</th>

                </tr>

              </thead>

              <tbody id="match-results-tbody"></tbody>

            </table>

          </div>

        </div>

        <!-- legacy stats bar kept so nothing else breaks -->

        <div id="match-score-stats" class="hidden"

             style="padding:5px 18px;font-size:0.77rem;color:#64748b;border-bottom:1px solid #21262d;background:#161b22;"></div>

      </div>



      <!-- Step 4: Select -->

      <div id="wz-4" class="wz-step hidden">

        <div class="panel-hd" style="border-top:none;">

          <span class="panel-title" style="font-size:0.85rem;color:#8b949e;">Step 4 — Select Jobs</span>

          <div style="display:flex;align-items:center;gap:8px;">

            <button id="btn-apply-selected" class="btn-sm btn-primary" onclick="applySelectedJobs()">&#9654; Apply Selected <span id="apply-selected-count">(0)</span></button>

            <button id="btn-apply-all-jobs" class="btn-sm btn-primary" onclick="applyAllJobs()">&#9654; Apply All</button>

          </div>

        </div>

        <div id="match-filter-bar" class="hidden" style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:10px 18px;border-bottom:1px solid #21262d;background:#161b22;">

          <label style="font-size:0.82rem;font-weight:600;color:#8b949e;">Min Score</label>

          <input id="match-min-score" type="number" min="0" max="100" value="70"

                 style="width:60px;padding:4px 6px;border:1px solid #30363d;border-radius:6px;font-size:0.84rem;background:#0d1117;color:#e2e8f0;">

          <label style="font-size:0.82rem;font-weight:600;color:#8b949e;">Interview Chance</label>

          <select id="match-chance-filter" style="padding:4px 8px;border:1px solid #30363d;border-radius:6px;font-size:0.84rem;background:#0d1117;color:#e2e8f0;">

            <option value="">Any</option>

            <option value="high">High</option>

            <option value="medium">Medium</option>

            <option value="low">Low</option>

          </select>

          <button class="btn-sm btn-primary" onclick="refilterMatchedJobs()">Re-filter</button>

          <button class="btn-sm" onclick="openAuditModal()" style="margin-left:4px;">&#128202; Audit</button>

          <button id="btn-show-dismissed" class="btn-sm" onclick="toggleShowDismissed()" style="margin-left:8px;">&#128065; Show dismissed (0)</button>

          <span id="match-filter-count" style="font-size:0.8rem;color:#64748b;margin-left:6px;"></span>

        </div>

        <div id="match-empty" style="padding:18px;color:#64748b;font-size:0.84rem;">

          Complete Step 3 — Match first to see jobs here.

        </div>

        <div id="matched-table-wrap" class="hidden">

          <div class="pip-toolbar">

            <span id="matched-count" class="pip-count"></span>

            <input id="matched-search" class="pip-search" type="text" placeholder="Filter by title, company, location…"

                   oninput="filterMatchedJobs()">

          </div>

          <div class="pip-table-wrap">

            <table class="pip-table" id="matched-table">

              <thead>

                <tr>

                  <th class="cb-col"><input type="checkbox" id="matched-select-all" onchange="toggleSelectAll(this)"></th>

                  <th>Title</th><th>Company</th><th>Location</th>

                  <th>Match %</th><th>Chance</th>
                  <th title="Generate an AI-tailored resume for this job before applying">Tailor Resume</th>
                  <th>Actions</th>

                </tr>

              </thead>

              <tbody id="matched-tbody"></tbody>

            </table>

          </div>

        </div>

      </div>



      <!-- Step 5: Apply -->

      <div id="wz-5" class="wz-step hidden">

        <div class="panel-hd" style="border-top:none;">

          <span class="panel-title" style="font-size:0.85rem;color:#8b949e;">Step 5 — Apply</span>

          <button id="btn-apply-start" class="btn-primary btn-sm" onclick="startApplySession()" style="display:none;">&#9654; Start Applying</button>

          <button id="btn-apply-stop"  class="btn-sm"             onclick="stopApplySession()"  style="display:none;">&#9632; Stop</button>

        </div>

        <div id="apply-session-bar" class="apply-session-bar" style="margin:12px 18px 0;">

          <div class="asb-stat"><span class="asb-label">Total</span><span id="asb-total" class="asb-value">0</span></div>

          <div class="asb-stat"><span class="asb-label">Applied</span><span id="asb-success" class="asb-value success">0</span></div>

          <div class="asb-stat"><span class="asb-label">Manual</span><span id="asb-manual" class="asb-value manual">0</span></div>

          <div class="asb-stat"><span class="asb-label">Failed</span><span id="asb-failed" class="asb-value failed">0</span></div>

        </div>

        <div class="apply-2col" style="padding:14px 18px;">

          <div>

            <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px;">

              <span style="font-size:0.83rem;font-weight:600;color:#8b949e;">Jobs to Apply</span>

              <span id="apply-cards-count" style="font-size:0.78rem;color:#64748b;"></span>

            </div>

            <div id="apply-session-summary" style="display:none;"></div>

            <div id="apply-cards-wrap" class="apply-cards-wrap">

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

              </div>

              <!-- Section: Expired / Unavailable -->

              <div class="apply-section-block" id="section-expired" style="display:none">

                <div class="apply-section-header">

                  <span class="section-dot dot-expired">&#9679;</span>

                  Expired / Unavailable <span id="count-expired" class="section-count"></span>

                </div>

                <div id="cards-expired"></div>

              </div>

              <!-- Section: Failed -->

              <div class="apply-section-block" id="section-failed" style="display:none">

                <div class="apply-section-header">

                  <span class="section-dot dot-failed">&#9679;</span>

                  Failed <span id="count-failed" class="section-count"></span>

                </div>

                <div id="cards-failed"></div>

              </div>

            </div>

            <div style="margin-top:16px;">

              <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px;">

                <span style="font-size:0.83rem;font-weight:600;color:#f59e0b;">&#9888; Manual Apply Queue</span>

                <div style="display:flex;align-items:center;gap:6px;">

                  <button class="btn-sm" id="btn-mq-all-sessions" onclick="toggleMqAllSessions()"

                          style="font-size:0.75rem;">&#128203; Show all sessions</button>

                  <button class="btn-sm" onclick="loadManualQueue()">Refresh</button>

                  <button class="btn-sm" style="color:#f87171;" onclick="clearManualQueue()">&#10005; Clear All</button>

                </div>

              </div>

              <p style="font-size:0.78rem;color:#94a3b8;margin-bottom:8px;">

                Agent could not complete these applications automatically.

                Review each one, apply manually then click <strong>&#10003; I Applied</strong>,

                or click <strong>&#10005; Not Interested</strong> to exclude the job permanently.

              </p>

              <div id="manual-queue-wrap">

                <div class="apply-empty" id="manual-queue-empty">No items in the manual queue.</div>

                <table class="pip-table" id="manual-queue-table" style="display:none;width:100%;">

                  <thead><tr><th>Company</th><th>Role</th><th>Platform</th><th>Reason</th><th>Link</th><th>Actions</th></tr></thead>

                  <tbody id="manual-queue-tbody"></tbody>

                </table>

              </div>

            </div>

          </div>

          <div class="apply-log" id="apply-log-panel">

            <div class="apply-log-hd">

              <span>&#128195; Apply Log</span>

              <button class="btn-sm" onclick="document.getElementById('apply-log-body').innerHTML=''" style="margin-left:auto;">Clear</button>

            </div>

            <div class="apply-log-body" id="apply-log-body"></div>

          </div>

        </div>

      </div>

    </div><!-- /Agent Control panel -->



    <!-- Job detail side panel (position:fixed — outside columns) -->

    <div id="job-detail-overlay" class="job-detail-overlay hidden" onclick="closeJobDetail()"></div>

    <div id="job-detail-panel" class="job-detail-panel">

      <div class="job-detail-header">

        <span id="jd-title" class="job-detail-title"></span>

        <button class="job-detail-close" onclick="closeJobDetail()">&#215;</button>

      </div>

      <div class="job-detail-body">

        <div id="jd-meta" class="job-detail-meta"></div>

        <div id="jd-badges" class="job-detail-badges"></div>

        <div id="jd-summary-wrap">

          <div class="job-detail-section-label">Match Summary</div>

          <div id="jd-summary" class="job-detail-summary"></div>

        </div>

        <div id="jd-skip-wrap" class="hidden">

          <div class="job-detail-section-label">Skip Reason</div>

          <div id="jd-skip" class="job-detail-skip"></div>

        </div>

        <div id="jd-desc-wrap">

          <div class="job-detail-section-label">Job Description</div>

          <div id="jd-desc" class="job-detail-desc"></div>

          <button id="jd-desc-toggle" class="btn-sm hidden" style="margin-top:6px;" onclick="toggleJobDesc()">Show more</button>

        </div>

      </div>

      <div class="job-detail-actions">

        <a id="jd-view-link" href="#" target="_blank" class="btn-sm" style="text-decoration:none;">View on LinkedIn</a>

        <button id="jd-apply-btn" class="btn-sm btn-primary" onclick="_jdApply()">Apply</button>

      </div>

    </div>



    <!-- Scoring Audit Modal -->

    <div id="audit-modal" class="modal-backdrop hidden" onclick="if(event.target===this)closeAuditModal()">

      <div class="modal-box" style="width:660px;">

        <div class="modal-hd">

          <span class="modal-title">&#128202; Scoring Audit</span>

          <button class="modal-close" onclick="closeAuditModal()">&#215;</button>

        </div>

        <div class="modal-body" id="audit-modal-body" style="gap:0;padding:16px 18px;">

          <div style="color:#64748b;font-size:0.84rem;text-align:center;padding:20px;">Loading…</div>

        </div>

      </div>

    </div>



    <!-- Two-column layout -->

    <div class="agent-2col">



      <!-- LEFT COLUMN -->

      <div class="agent-col-left">



        <!-- Upload Files (resume + cover letter) -->

        <div class="panel">

          <div class="panel-hd"><span class="panel-title">&#128194; Upload Files</span></div>

          <div class="upload-row" style="justify-content:flex-start;">

            <div class="upload-slot" id="us-resume">

              <div class="upload-icon">&#128196;</div>

              <div class="upload-label">Resume</div>

              <div class="upload-sub">DOCX only — needed to generate a tailored resume per job</div>

              <input type="file" accept=".docx" onchange="uploadFile('resume', this)">

              <div class="upload-status" id="ust-resume"></div>

            </div>

            <div class="upload-slot" id="us-cover_letter">

              <div class="upload-icon">&#128196;</div>

              <div class="upload-label">Cover Letter</div>

              <div class="upload-sub">PDF or DOCX — used as the tailoring template</div>

              <input type="file" accept=".pdf,.docx" onchange="uploadFile('cover_letter', this)">

              <div class="upload-status" id="ust-cover_letter"></div>

            </div>

          </div>

        </div>



        <!-- Job Titles -->

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



        <!-- Application Profile -->

        <div class="panel">

          <div class="panel-hd">

            <span class="panel-title">Application Profile</span>

            <button class="btn-sm btn-primary" onclick="saveConfig()">Save</button>

          </div>

          <div class="config-form">



            <div class="cfg-section">Personal Info</div>

            <div class="cfg-2col">

              <div class="cfg-col"><label>First Name</label>

                <input id="cfg-first_name" type="text" placeholder="Fardin"></div>

              <div class="cfg-col"><label>Last Name</label>

                <input id="cfg-last_name" type="text" placeholder="Behboudi"></div>

            </div>

            <div class="cfg-2col">

              <div class="cfg-col"><label>Email</label>

                <input id="cfg-email" type="email" placeholder="you@email.com"></div>

              <div class="cfg-col"><label>Phone</label>

                <input id="cfg-phone" type="text" placeholder="+49..."></div>

            </div>



            <div class="cfg-section">Online Presence</div>

            <div class="cfg-row"><label>LinkedIn URL</label>

              <input id="cfg-linkedin_url" type="url" placeholder="https://linkedin.com/in/yourprofile"></div>

            <div class="cfg-2col">

              <div class="cfg-col"><label>GitHub URL</label>

                <input id="cfg-github_url" type="url" placeholder="https://github.com/yourhandle"></div>

              <div class="cfg-col"><label>Portfolio / Website</label>

                <input id="cfg-portfolio_url" type="url" placeholder="https://yoursite.com"></div>

            </div>



            <div class="cfg-section">Work Preferences</div>

            <div class="cfg-2col">

              <div class="cfg-col">

                <label>Notice Period</label>

                <select id="cfg-notice_period" class="cfg-select">

                  <option value="immediately">Immediately</option>

                  <option value="2 weeks">2 weeks</option>

                  <option value="1 month">1 month</option>

                  <option value="2 months">2 months</option>

                  <option value="3 months">3 months</option>

                  <option value="6 months">6 months</option>

                </select>

              </div>

              <div class="cfg-col">

                <label>Salary Expectation (gross/year)</label>

                <input id="cfg-salary_expectation" type="number" min="0" step="1000">

              </div>

            </div>

            <div class="cfg-2col">

              <div class="cfg-col"><label>Years of Experience</label>

                <input id="cfg-years_of_experience" type="number" min="0" max="50"></div>

              <div class="cfg-col"><label>Work Permit / Visa</label>

                <input id="cfg-work_permit" type="text" placeholder="e.g. EU citizen"></div>

            </div>

            <div class="cfg-row">

              <label>Current Location</label>

              <input id="cfg-current_location" type="text" placeholder="e.g. Berlin, Germany">

            </div>

            <div class="cfg-2col">

              <div class="cfg-col">

                <label>Willing to Travel</label>

                <select id="cfg-willing_to_travel" class="cfg-select">

                  <option value="no">No travel</option>

                  <option value="occasionally">Occasionally</option>

                  <option value="25%">Up to 25%</option>

                  <option value="50%">Up to 50%</option>

                  <option value="100%">Fully mobile</option>

                </select>

              </div>

              <div class="cfg-col" style="display:flex;align-items:center;gap:8px;padding-top:18px;">

                <label class="t-switch" style="margin:0;"><input id="cfg-willing_to_relocate" type="checkbox"><div class="t-track"><div class="t-thumb"></div></div></label>

                <span style="font-size:0.82rem;color:#8b949e;">Willing to relocate</span>

              </div>

            </div>

            <div class="cfg-row">

              <label>Languages</label>

              <div id="lang-list" class="lang-list"></div>

              <button class="btn-sm" onclick="addLanguage()" style="margin-top:4px;align-self:flex-start;">+ Add Language</button>

            </div>



            <div class="cfg-section">Support Documents</div>

            <div style="font-size:0.78rem;color:#64748b;margin-bottom:8px;line-height:1.4;">

              Automatically attached to application forms when matching upload fields appear.

            </div>

            <div id="sdoc-list" class="sdoc-list"></div>

          </div>

        </div>



      </div><!-- /agent-col-left -->



      <!-- RIGHT COLUMN — Config Editor -->

      <div class="panel">

        <div class="panel-hd">

          <span class="panel-title">Config Editor</span>

          <button id="btn-save-config" class="btn-sm btn-primary" onclick="saveConfig()">Save Config</button>

        </div>

        <div class="config-form">

          <div class="cfg-section">LinkedIn Connection</div>

          <div id="li-session-status" style="margin-bottom:10px;padding:8px 10px;border-radius:6px;

            font-size:0.81rem;background:#1c2128;border:1px solid #30363d;color:#8b949e;">

            Checking session…

          </div>

          <div class="cfg-2col">

            <div class="cfg-col"><label>LinkedIn Email</label>

              <input id="li-email" type="email" placeholder="your@email.com"

                style="background:#0d1117;border:1px solid #30363d;color:#e2e8f0;

                  border-radius:6px;padding:6px 10px;font-size:0.83rem;outline:none;width:100%;">

            </div>

            <div class="cfg-col"><label>LinkedIn Password</label>

              <input id="li-password" type="password" placeholder="••••••••"

                style="background:#0d1117;border:1px solid #30363d;color:#e2e8f0;

                  border-radius:6px;padding:6px 10px;font-size:0.83rem;outline:none;width:100%;">

            </div>

          </div>

          <div style="margin-top:8px;margin-bottom:4px;display:flex;align-items:center;gap:8px;flex-wrap:wrap;">

            <button id="btn-li-connect" class="btn-primary btn-sm" onclick="startLinkedInLogin()">

              &#128279; Connect LinkedIn (one-time setup)

            </button>

            <button id="btn-li-cancel" class="btn-sm" onclick="cancelLinkedInLogin()"

                    style="display:none;background:#450a0a;border:1px solid #7f1d1d;color:#fca5a5;">

              &#x2715; Cancel

            </button>

            <span id="li-login-msg" style="font-size:0.79rem;color:#8b949e;"></span>

          </div>

          <div class="cfg-row"><label>Notification Email</label>

            <input id="cfg-notify_email" type="email" placeholder="email for pipeline alerts">

            <div style="font-size:0.75rem;color:#64748b;margin-top:3px;">Used for interview alerts and pipeline notifications.</div>

          </div>

          <div class="cfg-section">Scoring</div>

          <div class="cfg-2col">

            <div class="cfg-col"><label>Min Match Score</label><input id="cfg-min_match_score" type="number" min="0" max="100"></div>

            <div class="cfg-col"><label>Max Apps / Day</label><input id="cfg-max_applications_per_day" type="number" min="1"></div>

          </div>

          <div class="cfg-2col">

            <div class="cfg-col">

              <label>Jobs to fetch per search</label>

              <input id="cfg-scrape_pool_size" type="number" min="10" max="200" step="5"

                     oninput="updatePoolCostEstimate()">

              <div id="pool-cost-hint" style="font-size:0.76rem;color:#64748b;margin-top:4px;line-height:1.4;">

                Higher = more candidates but more Apify cost (~$0.001 per job).

              </div>

            </div>

            <div class="cfg-col" style="display:flex;align-items:center;gap:8px;padding-top:18px;">

              <label class="t-switch" style="margin:0;"><input id="cfg-smart_scrape" type="checkbox"><div class="t-track"><div class="t-thumb"></div></div></label>

              <span style="font-size:0.82rem;color:#8b949e;">Smart scrape (title filter before Claude)</span>

            </div>

            <div class="cfg-col" style="display:flex;align-items:center;gap:8px;padding-top:12px;">

              <label class="t-switch" style="margin:0;"><input id="cfg-only_easy_apply" type="checkbox"><div class="t-track"><div class="t-thumb"></div></div></label>

              <span style="font-size:0.82rem;color:#8b949e;">Only Easy Apply (skip jobs without LinkedIn Easy Apply)</span>

            </div>

            <div class="cfg-col" style="display:flex;align-items:center;gap:8px;padding-top:12px;">

              <label class="t-switch" style="margin:0;"><input id="cfg-skip_cached_jobs" type="checkbox"><div class="t-track"><div class="t-thumb"></div></div></label>

              <span style="font-size:0.82rem;color:#8b949e;">Skip cached jobs (fetch fresh jobs only, ignore cache)</span>

            </div>

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

            <div class="tag-input-row">

              <input id="tl-input-locations" type="text" placeholder="Add location…" onkeydown="addTagOnEnter(event,'locations')" oninput="showLocationAutocomplete(this.value)">

              <button class="btn-sm" onclick="addTag('locations')">+ Add</button>

              <div id="ac-locations" class="tag-autocomplete"></div>

            </div>

            <div id="tl-locations" class="tag-list"></div>

          </div>

          <div class="cfg-row">

            <label>Skip German Levels</label>

            <div class="tag-input-row">

              <input id="tl-input-skip_german_levels" type="text" placeholder="Add level…" onkeydown="addTagOnEnter(event,'skip_german_levels')">

              <button class="btn-sm" onclick="addTag('skip_german_levels')">+ Add</button>

            </div>

            <div id="tl-skip_german_levels" class="tag-list"></div>

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

      </div><!-- /Config Editor -->



    </div><!-- /agent-2col -->



    <!-- Live Log -->

    <div class="panel">

      <div class="panel-hd">

        <span class="panel-title">Live Log</span>

        <span id="log-status" style="font-size:0.75rem;color:#64748b;margin-right:auto;padding-left:8px;">● connecting…</span>

        <button class="btn-sm" onclick="clearLog()">Clear</button>

      </div>

      <div id="log-viewer" class="log-viewer"></div>

    </div>



    <!-- Debug Apply Panel -->

    <div id="debug-apply-panel" style="border:2px dashed #f90;border-radius:8px;padding:12px;margin-top:24px;">

      <div style="color:#f90;font-size:12px;font-weight:bold;margin-bottom:8px;">&#128736; DEBUG — Test Applier Directly</div>

      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">

        <input id="debug-job-url" type="text"

               placeholder="Paste job URL (LinkedIn, Greenhouse, Lever, etc.)"

               style="flex:1;min-width:260px;padding:5px 8px;border:1px solid #30363d;border-radius:6px;font-size:0.83rem;background:#0d1117;color:#e2e8f0;">

        <input id="debug-job-title" type="text"

               placeholder="Title (optional)"

               style="width:180px;padding:5px 8px;border:1px solid #30363d;border-radius:6px;font-size:0.83rem;background:#0d1117;color:#e2e8f0;">

        <input id="debug-job-company" type="text"

               placeholder="Company (optional)"

               style="width:160px;padding:5px 8px;border:1px solid #30363d;border-radius:6px;font-size:0.83rem;background:#0d1117;color:#e2e8f0;">

        <button class="btn-sm" onclick="debugApply()"

                style="background:#b45309;color:#fff;border-color:#92400e;">&#9654; Run Apply</button>

      </div>

      <div id="debug-apply-log"

           style="margin-top:10px;font-family:monospace;font-size:12px;background:#111;color:#eee;

                  padding:10px;border-radius:6px;min-height:60px;max-height:300px;

                  overflow-y:auto;white-space:pre-wrap;"></div>

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



<!-- ═══ EVENT EDIT MODAL ═══ -->

<div id="modal-event-edit" class="modal-backdrop hidden">

  <div class="modal-box">

    <div class="modal-hd">

      <span class="modal-title">&#9998; Edit Interview Details</span>

      <button class="modal-close" onclick="closeModal('modal-event-edit')">&#215;</button>

    </div>

    <div class="modal-body">

      <input type="hidden" id="ev-app-id">

      <input type="hidden" id="ev-event-id">

      <div class="mf"><label>Title</label><input id="ev-title" type="text" placeholder="Interview title / description"></div>

      <div class="mf-2col">

        <div class="mf"><label>Date</label><input id="ev-date" type="date"></div>

        <div class="mf"><label>Time (HH:MM)</label><input id="ev-time" type="time"></div>

      </div>

      <div class="mf-2col">

        <div class="mf"><label>Timezone</label><input id="ev-tz" type="text" placeholder="Europe/Berlin"></div>

        <div class="mf"><label>Priority</label>

          <select id="ev-priority">

            <option value="high">High</option>

            <option value="medium">Medium</option>

            <option value="low">Low</option>

          </select>

        </div>

      </div>

      <div class="mf"><label>Description / Meeting Link / Interviewers</label><textarea id="ev-desc" placeholder="Google Meet link, interviewers, topics to prepare..." rows="4"></textarea></div>

    </div>

    <div class="modal-footer">

      <button class="btn-sm" onclick="closeModal('modal-event-edit')">Cancel</button>

      <button class="btn-primary" onclick="saveEventEdit()">Save</button>

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



<!-- ═══ CONFIRM MODAL ═══ -->

<div id="modal-confirm" class="modal-backdrop hidden">

  <div class="modal-box" style="max-width:400px;">

    <div class="modal-hd">

      <span class="modal-title">Confirm</span>

    </div>

    <div style="padding:18px 24px;color:#c9d1d9;font-size:0.88rem;line-height:1.5;" id="confirm-msg"></div>

    <div class="modal-footer">

      <button class="btn-sm" id="confirm-no">Cancel</button>

      <button class="btn-primary" id="confirm-yes">Confirm</button>

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

  if (name === 'email-admin') {
    const mount = document.getElementById('email-admin-mount');
    if (mount.dataset.eaLoaded) {
      if (typeof eaLoadPending === 'function') eaLoadPending();
      return;
    }
    fetch('/email-admin/')
      .then(r => r.text())
      .then(html => {
        mount.innerHTML = html;
        mount.dataset.eaLoaded = '1';
        const ns = document.createElement('script');
        ns.src = '/email-admin/ea.js';
        document.head.appendChild(ns);
      })
      .catch(err => console.error('Email admin load failed:', err));
  }

}



function initAgentTab() {

  loadConfig();

  loadRoles();

  startLogStream();

  checkUploadedFiles();

  loadCacheStats();

  loadSupportDocs();

  prefillLinkedInCredentials();

  _initWizardFromHash();

  _initAutocompleteListeners();

}



function _initAutocompleteListeners() {

  // Close autocomplete when clicking outside or pressing Escape

  document.addEventListener('click', (e) => {

    const ac = document.getElementById('ac-locations');

    const inp = document.getElementById('tl-input-locations');

    if (ac && !ac.contains(e.target) && e.target !== inp) {

      ac.classList.remove('show');

    }

  });

  document.addEventListener('keydown', (e) => {

    if (e.key === 'Escape') {

      const ac = document.getElementById('ac-locations');

      if (ac) ac.classList.remove('show');

    }

  });

}



async function prefillLinkedInCredentials() {

  try {

    const r = await fetch('/api/linkedin/credentials');

    const d = await r.json();

    if (d.email) document.getElementById('li-email').value = d.email;

  } catch (_) {}

  checkLinkedInSession();

}



async function _initWizardFromHash() {

  await _refreshStepAvail();

  const m = location.hash.match(/^#step-([1-5])$/);

  const requested = m ? parseInt(m[1]) : 1;

  const key = 'step' + requested;

  const target = (requested === 1 || _stepAvail[key]) ? requested : 1;

  setWizardStep(1);

  if (target > 1) await navigateToStep(target);

}



// Browser back/forward support

window.addEventListener('hashchange', async () => {

  if (!_agentInited) return;

  const m = location.hash.match(/^#step-([1-5])$/);

  if (!m) return;

  const n = parseInt(m[1]);

  if (n === _wizStep) return;

  // Guard: back from step 5 while applying

  if (_wizStep === 5 && _applyRunning) {

    if (!await showConfirm('Apply session is running. Go back anyway?')) {

      history.pushState(null, '', '#step-5');

      return;

    }

  }

  const key = 'step' + n;

  if (n === 1 || _stepAvail[key]) {

    if (n === 2) { setWizardStep(2); loadScrapedJobs(); }

    else if (n === 3) {

      setWizardStep(3);

      document.getElementById('match-progress').classList.add('hidden');

      renderStep3Results();  // always check DB — works after page refresh too

    }

    else if (n === 4) {

      setWizardStep(4);

      if (_matchedJobs.length) {

        document.getElementById('match-filter-bar').classList.remove('hidden');

        document.getElementById('matched-table-wrap').classList.remove('hidden');

        document.getElementById('match-empty').classList.add('hidden');

        _applyMatchFilters(); renderMatchedTable();

      } else { renderMatchedJobs(); }

    }

    else setWizardStep(n);

  }

});



async function loadCacheStats() {

  try {

    const d = await (await fetch('/api/cache/stats')).json();

    if (d.error) return;

    document.getElementById('acs-total').textContent     = d.total_cached;

    document.getElementById('acs-unseen').textContent    = d.unseen;

    document.getElementById('acs-applied').textContent   = d.applied;

    document.getElementById('acs-dismissed').textContent = d.dismissed;

    if (d.total_cached > 0) {

      const el = document.getElementById('agent-cache-stats');

      el.classList.remove('hidden');

      el.style.display = 'flex';

    }

  } catch (_) {}

}



// ── Shared helpers ────────────────────────────────────────────────────────────

function esc(s) {

  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;')

    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');

}

// Normalise any date/timestamp to "1 Jun 2026" format.
// Passes through already-formatted strings like "17 Apr 2026" unchanged.
function _normDate(s) {
  if (!s) return '';
  if (/^\\d{1,2}\\s+[A-Z][a-z]{2}\\s+\\d{4}$/.test(String(s).trim())) return String(s).trim();
  try {
    const d = new Date(s);
    if (isNaN(d.getTime())) return s;
    const M = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    return d.getUTCDate() + ' ' + M[d.getUTCMonth()] + ' ' + d.getUTCFullYear();
  } catch(e) { return s; }
}



function showToast(msg, type) {

  const t = document.getElementById('toast');

  t.textContent = msg;

  t.className = 'toast show' + (type === 'error' ? ' toast-error' : '');

  clearTimeout(t._tid);

  t._tid = setTimeout(() => t.classList.remove('show'), 3200);

}



function showConfirm(msg) {

  return new Promise(resolve => {

    document.getElementById('confirm-msg').textContent = msg;

    const modal = document.getElementById('modal-confirm');

    modal.classList.remove('hidden');

    const yes = document.getElementById('confirm-yes');

    const no  = document.getElementById('confirm-no');

    function cleanup(val) {

      modal.classList.add('hidden');

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



// ── Dashboard sub-tabs ────────────────────────────────────────────────────────

let _dashData   = null;

let _activeSubTab = 'overview';



function showSubTab(name) {

  _activeSubTab = name;

  document.querySelectorAll('.sub-content').forEach(el => el.classList.add('hidden'));

  document.querySelectorAll('.card[data-sub]').forEach(el => el.classList.remove('card-active'));

  const section = document.getElementById('sub-' + name);

  if (section) section.classList.remove('hidden');

  const card = document.querySelector('.card[data-sub="' + name + '"]');

  if (card) card.classList.add('card-active');

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

function applyBadge(row) {

  const by   = (row.applied_by || '').toLowerCase();

  const type = (row.apply_type  || '').toLowerCase();

  const S = 'display:inline-block;padding:2px 8px;border-radius:10px;font-size:0.74rem;font-weight:600;white-space:nowrap;';

  if (by === 'agent') {

    if (type.includes('easy apply') || type.includes('linkedin')) {

      return '<span style="' + S + 'background:#1a4a2e;color:#4ade80;border:1px solid #4ade80;">🤖 Applied (Agent) · Easy Apply</span>';

    }

    if (type.includes('external') || type.includes('ats') || type.includes('ai')) {

      return '<span style="' + S + 'background:#1a3a4a;color:#60b4ff;border:1px solid #60b4ff;">🤖 Applied (Agent) · External</span>';

    }

    return '<span style="' + S + 'background:#1a4a2e;color:#4ade80;border:1px solid #4ade80;">🤖 Applied (Agent)</span>';

  }

  if (by === 'user') {

    return '<span style="' + S + 'background:#3a2a1a;color:#fb923c;border:1px solid #fb923c;">✋ Applied (Manual)</span>';

  }

  return '<span style="' + S + 'background:#2a2a2a;color:#888;border:1px solid #555;">Applied</span>';

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

  const isIV  = tabKey === 'interviews';
  const isREJ = tabKey === 'rejected';

  const tableHtml = `

    <div class="table-wrap">

      <div class="table-bar"><span id="tbar-${tabKey}"></span></div>

      <div class="scroll-x">

        <table>

          <thead><tr>

            ${isIV ? '' : isREJ
              ? `<th onclick="sortTable('${tabKey}','_rej_date')" data-col="_rej_date">Rejection Date<span class="arr">↕</span></th>`
              : `<th onclick="sortTable('${tabKey}','date_applied')" data-col="date_applied">Date<span class="arr">↕</span></th>`}

            <th onclick="sortTable('${tabKey}','company')"          data-col="company">           Company<span class="arr">↕</span></th>

            <th onclick="sortTable('${tabKey}','role')"             data-col="role">              Role   <span class="arr">↕</span></th>

            ${isIV ? '' : `<th onclick="sortTable('${tabKey}','location')" data-col="location">Location<span class="arr">↕</span></th>`}

            ${isIV ? '' : `<th onclick="sortTable('${tabKey}','match_pct')" data-col="match_pct">Match %<span class="arr">↕</span></th>`}

            ${isIV ? '' : `<th onclick="sortTable('${tabKey}','interview_chance')" data-col="interview_chance">Chance<span class="arr">↕</span></th>`}

            ${isIV ? '' : `<th onclick="sortTable('${tabKey}','language')" data-col="language">German<span class="arr">↕</span></th>`}

            <th onclick="sortTable('${tabKey}','status')"           data-col="status">            Status <span class="arr">↕</span></th>

            ${isIV ? '' : '<th>Archive</th>'}

            ${isIV ? `<th onclick="sortTable('interviews','_iv_date')" data-col="_iv_date">Interview Date<span class="arr">↕</span></th><th>Type</th><th>Time</th><th>Notes</th><th></th>` : ''}

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

    const d = tabKey === 'rejected'
      ? (r.last_email_date || r.date_applied || '')
      : (r.date_applied || '');

    if (dfr && d && d < dfr) return false;

    if (dto && d && d > dto) return false;

    return true;

  });



  const sc = _sortColMap[tabKey] || 'date_applied';

  const sa = _sortAscMap[tabKey] ?? false;

  filtered = [...filtered].sort((a, b) => {

    let av = a[sc] ?? '', bv = b[sc] ?? '';

    if (sc === 'match_pct') { av = a.match_pct || 0; bv = b.match_pct || 0; }

    if (sc === '_rej_date') { av = a.last_email_date || a.date_applied || ''; bv = b.last_email_date || b.date_applied || ''; }

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

    if (tabKey === 'interviews') {

      // Clean focused layout: no Date Applied, Location, Match%, Chance, German, Archive

      const ivDate = j._iv_date ? '<span style="color:#e2e8f0;font-weight:600;">' + esc(j._iv_date) + '</span>' : '<span style="color:#4b5563;">—</span>';

      const ivTime = j._iv_time ? '<span style="color:#8b949e;">' + esc(j._iv_time) + '</span>' : '';

      const ivType = j._iv_type ? '<span style="font-size:0.76rem;background:#0d2818;color:#34d399;padding:2px 7px;border-radius:99px;">' + esc(j._iv_type) + '</span>' : '';

      const ivNotes = j._iv_notes

        ? '<td style="max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:0.79rem;color:#58a6ff;cursor:pointer;" title="' + esc(j._iv_notes) + '" onclick="openEventEdit(' + j.id + ',' + (j._iv_event_id||'null') + ')">' + esc(j._iv_notes) + '</td>'

        : '<td style="color:#4b5563;font-size:0.79rem;">—</td>';

      return '<tr>'

        + '<td class="td-company">' + esc(j.company || '') + '</td>'

        + '<td class="td-role">' + roleCell + '</td>'

        + '<td>' + statusBadge(j.status) + '</td>'

        + '<td style="white-space:nowrap;">' + ivDate + (ivTime ? ' <span style="color:#4b5563;font-size:0.75rem;">·</span> ' + ivTime : '') + '</td>'

        + '<td>' + ivType + '</td>'

        + ivNotes

        + '<td style="white-space:nowrap;">'

        + '<button class="btn-sm" onclick="openEventEdit(' + j.id + ',' + (j._iv_event_id||'null') + ')" style="margin-right:4px;" title="Edit interview details">&#9998; Edit</button>'

        + '<button class="btn-sm" style="background:#7f1d1d;color:#fca5a5;border-color:#991b1b;" onclick="rejectInterview(' + j.id + ')" title="Mark as Rejected">&#10005; Reject</button>'

        + '</td>'

        + '</tr>';

    }

    return '<tr>'

      + '<td class="td-date">' + esc(_normDate(tabKey === 'rejected' ? (j.last_email_date || j.date_applied) : j.date_applied)) + '</td>'

      + '<td class="td-company">' + esc(j.company || '') + '</td>'

      + '<td class="td-role">' + roleCell + '</td>'

      + '<td>' + esc(j.location || '') + '</td>'

      + '<td class="td-score ' + (sc2 != null ? scoreClass(sc2) : 's-na') + '">' + esc(scoreTxt) + '</td>'

      + '<td>' + chanceChip(j.interview_chance) + '</td>'

      + '<td>' + germanCell(j.language) + '</td>'

      + '<td>' + (tabKey === 'applied' ? applyBadge(j) : statusBadge(j.status)) + '</td>'

      + '<td class="td-archive">' + archiveLink(j.archive_path) + '</td>'

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



  const tabs = [{key:'applied',name:'Applied'},{key:'interviews',name:'Interviews'},{key:'rejected',name:'Rejected'},{key:'offers',name:'Offer'}];

  tabs.forEach(({key, name}) => {

    const cnt = (ov[name] ?? 0);

    const el = document.getElementById('cnt-' + name);

    if (el) el.textContent = cnt ? ' ' + cnt : '';

    const container = document.getElementById('sub-' + key);

    const rows = d[name] || [];

    _tableData[key] = rows;

    container.innerHTML = buildTableSection(key, rows);

    _sortColMap[key] = key === 'interviews' ? '_iv_date' : key === 'rejected' ? '_rej_date' : 'date_applied';

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

    for (const key of ['resume', 'cover_letter']) {

      const info = d[key];

      if (info && info.exists) {

        const statusEl = document.getElementById('ust-' + key);

        const slotEl   = document.getElementById('us-' + key);

        if (statusEl) { statusEl.textContent = '✓ ' + info.filename; statusEl.style.color = '#34d399'; }

        if (slotEl)   slotEl.classList.add('uploaded');

      }

    }

  } catch (_) {}

}



// ── Wizard ───────────────────────────────────────────────────────────────────

let _wizStep            = 1;

let _highestStepReached = 1;

let _stepAvail          = {step1:true, step2:false, step3:false, step4:false, step5:false};

let _applySessionStarted = false;

let _pollTimer            = null;

let _scrapeProgressTimer  = null;

let _matchProgressTimer   = null;

let _scrapedJobs     = [];

let _scrapedFiltered = [];

let _matchedJobs      = [];

let _matchedFiltered  = [];

let _step2MatchLimit  = 9999;   // how many of the reviewed jobs to send to the matcher
let _step2LimitIds    = [];     // ordered scraped_job IDs for those jobs

let _step3Jobs        = [];

let _step3Threshold   = 70;

let aboveThresholdIds = [];

let _dismissedJobIds  = new Set();

let _showDismissed    = false;

let _dismissTimers    = {};



function setWizardStep(n) {

  _wizStep = n;

  if (n > _highestStepReached) _highestStepReached = n;

  [1,2,3,4,5].forEach(i => {

    document.getElementById('wz-' + i).classList.toggle('hidden', i !== n);

    const ind   = document.getElementById('step-ind-' + i);

    const avail = _stepAvail['step' + i];

    ind.classList.remove('active', 'done');

    ind.querySelector('.wz-num').textContent = i;



    if (i === n) {

      ind.classList.add('active');

      ind.disabled = false;

    } else if (i < n) {

      // All past steps are always clickable, regardless of _stepAvail

      ind.classList.add('done');

      ind.querySelector('.wz-num').textContent = '✓';

      ind.disabled = false;

    } else if (i <= _highestStepReached || avail) {

      ind.disabled = false;

    } else {

      ind.disabled = true;

    }

  });

  const hash = '#step-' + n;

  if (location.hash !== hash) history.pushState(null, '', hash);

}



async function _refreshStepAvail() {

  try {

    const d = await (await fetch('/api/pipeline/step_availability')).json();

    _stepAvail = d;

  } catch (_) {}

}



async function navigateToStep(n) {

  await _refreshStepAvail();

  const key = 'step' + n;

  // Allow going back to any step that has been reached in this session,

  // or forward if the backend says the step is available.

  if (n !== 1 && n > _highestStepReached && !_stepAvail[key]) return;



  // Guard: leaving step 5 while applying

  if (_wizStep === 5 && n !== 5 && _applyRunning) {

    if (!await showConfirm('Apply session is running. Go back anyway?')) return;

  }



  setWizardStep(n);

  if (n === 2) {

    await loadScrapedJobs();

  } else if (n === 3) {

    document.getElementById('match-progress').classList.add('hidden');

  } else if (n === 4) {

    await renderMatchedJobs();

  } else if (n === 5) {

    // Auto-start only if jobs are queued and session not yet started

    if (_applyJobs.length > 0 && !_applySessionStarted && !_applyRunning) {

      document.getElementById('btn-apply-start').style.display = '';

    }

    document.getElementById('apply-log-body').innerHTML = '';

    connectApplyStream();

    loadManualQueue();

  }

}



function _startPoll() { if (_pollTimer) clearInterval(_pollTimer); _pollTimer = setInterval(pollPipeline, 3000); }

function _stopPoll()  { if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; } }



function _startScrapeProgress() {

  if (_scrapeProgressTimer) clearInterval(_scrapeProgressTimer);

  _scrapeProgressTimer = setInterval(async () => {

    try {

      const d    = await (await fetch('/api/pipeline/scrape_progress')).json();

      const fill = document.getElementById('scrape-progress-fill');

      const msg  = document.getElementById('scrape-msg');

      const cur  = document.getElementById('scrape-current');

      const pct  = d.total_combinations > 0

        ? Math.round((d.completed_combinations / d.total_combinations) * 100) : 0;

      if (fill) fill.style.width = pct + '%';

      if (msg)  msg.textContent  = 'Scraping LinkedIn — ' + d.completed_combinations + ' / ' +

        d.total_combinations + ' searches done · ' + d.jobs_found_so_far + ' jobs found so far';

      if (cur)  cur.textContent  = d.current_search ? 'Current search: "' + d.current_search + '"' : '';

    } catch (_) {}

  }, 2000);

}



function _stopScrapeProgress() {

  if (_scrapeProgressTimer) { clearInterval(_scrapeProgressTimer); _scrapeProgressTimer = null; }

  const fill = document.getElementById('scrape-progress-fill');

  if (fill) fill.style.width = '100%';

}



function _startMatchProgress() {

  if (_matchProgressTimer) clearInterval(_matchProgressTimer);

  _matchProgressTimer = setInterval(async () => {

    try {

      const d    = await (await fetch('/api/pipeline/match_progress')).json();

      const fill = document.getElementById('match-progress-fill');

      const msg  = document.getElementById('match-msg');

      const cur  = document.getElementById('match-current');

      const pct  = d.total > 0 ? Math.round((d.scored / d.total) * 100) : 0;

      if (fill) fill.style.width = pct + '%';

      let statusMsg;

      if (d.resume_changed) {

        statusMsg = '⚠ Resume changed — re-scoring all jobs… ' + d.scored + ' / ' + d.total +

          ' done, ' + d.remaining + ' remaining';

      } else {

        statusMsg = 'Scoring with Claude AI — ' + d.scored + ' / ' + d.total +

          ' done, ' + d.remaining + ' remaining';

        if (d.cache_hits > 0)

          statusMsg += ' (' + d.fresh + ' fresh · ' + d.cache_hits + ' from cache)';

      }

      if (msg)  msg.textContent  = statusMsg;

      if (cur)  cur.textContent  = d.current_job ? 'Currently scoring: "' + d.current_job + '"' : '';

    } catch (_) {}

  }, 2000);

}



function _stopMatchProgress() {

  if (_matchProgressTimer) { clearInterval(_matchProgressTimer); _matchProgressTimer = null; }

  const fill = document.getElementById('match-progress-fill');

  if (fill) { fill.style.width = '100%'; fill.style.background = '#22c55e'; }

}



// ── Scrape button state helpers ───────────────────────────────────────────────

let _statusPollTimer = null;



function _setScrapeRunning() {

  const btn = document.getElementById('btn-scrape');

  btn.className = 'btn-stop btn-sm';

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

  btn.className = 'btn-primary btn-sm';

  btn.innerHTML = '&#128270; Start Scraping';

  btn.onclick   = startScrape;

  btn.disabled  = false;

  const msg = document.getElementById('scrape-msg');

  msg.className = '';

  _stopStatusPoll();

  _stopScrapeProgress();

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

      _stopPoll(); _stopStatusPoll(); _stopScrapeProgress(); _resetScrapeButton();

      document.getElementById('scrape-progress').classList.add('hidden');

      showToast('✓ Scraping complete — ' + d.jobs_count + ' jobs found');

      await _refreshStepAvail();

      setWizardStep(2); await loadScrapedJobs();

    } else if (d.step === 3) {

      // match progress timer handles the message update

    } else if (d.step === 4) {

      _stopPoll(); _stopMatchProgress();

      showToast('✓ Matching complete — ' + d.matched_count + ' jobs matched');

      await renderStep3Results();

    } else if (d.step === 5) {

      // legacy pipeline apply step — no-op in new flow

    } else if (d.step === 6) {

      _stopPoll();

      showToast('✓ Apply session done.');

      fetchDashboard();

    } else if (d.step < 0) {

      _stopPoll(); _stopStatusPoll(); _stopScrapeProgress(); _stopMatchProgress(); _resetScrapeButton();

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

    _startScrapeProgress();

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

  _stopScrapeProgress();

  try { await fetch('/api/agent/stop', {method: 'POST'}); } catch (_) {}

  document.getElementById('scrape-progress').classList.add('hidden');

  _resetScrapeButton();

  showToast('Scraping stopped.');

}



async function restartPipeline() {

  if (!await showConfirm('This stops any running scrape/match/apply job and resets the pipeline to Step 1. Continue?')) return;

  try { await fetch('/api/pipeline/reset', {method: 'POST'}); } catch (_) {}

  // Tear down every pipeline-related timer/stream so nothing keeps polling stale state.
  _stopPoll(); _stopStatusPoll(); _stopScrapeProgress(); _stopMatchProgress();
  if (_applyEvtSrc) { _applyEvtSrc.close(); _applyEvtSrc = null; }

  _resetScrapeButton();
  document.getElementById('scrape-progress').classList.add('hidden');

  const matchBtn = document.getElementById('btn-match');
  if (matchBtn) matchBtn.disabled = false;
  document.getElementById('match-progress').classList.add('hidden');

  _applyRunning = false;
  _applySessionStarted = false;
  const startBtn = document.getElementById('btn-apply-start');
  const stopBtn  = document.getElementById('btn-apply-stop');
  if (startBtn) startBtn.style.display = '';
  if (stopBtn)  stopBtn.style.display  = 'none';

  setWizardStep(1);
  showToast('Pipeline reset — ready for a fresh run.');

}



// ── Step 2: Scraped Jobs table ────────────────────────────────────────────────



async function loadScrapedJobs() {

  try {

    const [r, sr] = await Promise.all([

      fetch('/api/pipeline/scraped_jobs'),

      fetch('/api/cache/stats'),

    ]);

    const d = await r.json();

    const s = await sr.json();

    _scrapedJobs = d.jobs || [];

    _scrapedFiltered = [..._scrapedJobs];

    if (!_scrapedJobs.length) {

      showToast('Scraping finished but no jobs were saved to the database — check the agent log for errors.', 'error');

    }

    // Show cache breakdown stats bar

    const newCount    = _scrapedJobs.filter(j => (j.cache_status||'new') === 'new').length;

    const cachedCount = _scrapedJobs.filter(j => j.cache_status === 'cached').length;

    document.getElementById('cs-new').textContent    = newCount;

    document.getElementById('cs-cached').textContent = cachedCount;

    const outsideWrap = document.getElementById('cs-outside-wrap');

    const outsideCount = (s && s.outside_window) ? s.outside_window : 0;

    if (outsideCount > 0) {

      document.getElementById('cs-outside').textContent = outsideCount;

      outsideWrap.classList.remove('hidden');

    } else {

      outsideWrap.classList.add('hidden');

    }

    document.getElementById('scrape-cache-stats').classList.remove('hidden');

    if (_scrapedJobs.length > 0) {

      _step2MatchLimit = _scrapedJobs.length;   // default: match all

      document.getElementById('step2-limit-banner').classList.remove('hidden');

      document.getElementById('step2-limit-bar').classList.remove('hidden');

    }

    _updateStep2Limit(_step2MatchLimit);

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

  _updateStep2Limit(_step2MatchLimit);

}



function _updateStep2Limit(val) {

  const total = _scrapedFiltered.length;

  if (total === 0) return;

  _step2MatchLimit = Math.min(total, Math.max(1, parseInt(val) || total));

  const sliderEl = document.getElementById('step2-limit-slider');

  const numEl    = document.getElementById('step2-limit-num');

  const hintVal  = document.getElementById('step2-limit-hint-val');

  const aboveEl  = document.getElementById('step2-above-cnt');

  const belowEl  = document.getElementById('step2-below-cnt');

  if (sliderEl) { sliderEl.max = total; sliderEl.value = _step2MatchLimit; }

  if (numEl)    { numEl.max = total;    numEl.value    = _step2MatchLimit; }

  if (hintVal)  hintVal.textContent = _step2MatchLimit;

  if (aboveEl)  aboveEl.textContent = _step2MatchLimit;

  if (belowEl)  belowEl.textContent = total - _step2MatchLimit;

  _step2LimitIds = _scrapedFiltered.slice(0, _step2MatchLimit).map(j => j.id).filter(Boolean);

  renderScrapedTable();

}



function onLimitSlider() { _updateStep2Limit(document.getElementById('step2-limit-slider').value); }

function onLimitNum()    { _updateStep2Limit(document.getElementById('step2-limit-num').value); }



function _fmtDate(ts) {

  if (!ts) return '';

  const d = new Date(ts.replace(' ', 'T') + 'Z');

  if (isNaN(d)) return ts;

  return d.toLocaleDateString(undefined, {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'});

}



function _fmtPostedDate(pd) {

  if (!pd) return '—';

  const m = (pd || '').match(/^(\\d{4}-\\d{2}-\\d{2})/);

  if (!m) return pd;

  const d = new Date(m[1] + 'T12:00:00Z');

  if (isNaN(d)) return pd;

  const diffDays = Math.floor((Date.now() - d.getTime()) / 86400000);

  if (diffDays < 0)  return m[1];

  if (diffDays === 0) return 'Today';

  if (diffDays === 1) return 'Yesterday';

  if (diffDays < 7)  return diffDays + 'd ago';

  if (diffDays < 60) return Math.floor(diffDays / 7) + 'w ago';

  return m[1];

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

  let _passedLimit = true;

  tbody.innerHTML = _scrapedFiltered.map((j, idx) => {

    const isSelected = idx < _step2MatchLimit;

    const cs  = j.cache_status === 'cached' ? 'cached' : 'new';

    const lbl = cs === 'new' ? 'New today' : 'From cache';

    const easyApplyBadge = j.has_easy_apply

      ? `<span class="badge-easy-apply" style="background:#10b981;color:#fff;padding:2px 6px;border-radius:4px;font-size:0.7rem;font-weight:600;margin-left:6px;display:inline-block;">⚡ Easy Apply</span>`

      : '';

    let sep = '';

    if (_passedLimit && !isSelected) {

      const excl = _scrapedFiltered.length - _step2MatchLimit;

      sep = `<tr><td colspan="6" style="text-align:center;font-size:0.74rem;color:#475569;padding:4px 8px;`

          + `background:#0d1117;font-style:italic;border-top:1px dashed #21262d;">`

          + `&#8212; excluded from matching (${excl} job${excl !== 1 ? 's' : ''}) &#8212;</td></tr>`;

      _passedLimit = false;

    }

    const rowStyle = isSelected ? '' : 'opacity:0.35;background:#0b0f14;';

    return sep + `<tr style="${rowStyle}" data-id="${j.id||''}">

      <td class="td-title">${esc(j.title||'?')}${easyApplyBadge}</td>

      <td>${esc(j.company||'')}</td>

      <td>${esc(j.location||'')}</td>

      <td style="white-space:nowrap;font-size:0.76rem;color:#94a3b8;">${_fmtPostedDate(j.posted_date)}</td>

      <td><span class="badge-${cs}">${lbl}</span></td>

      <td class="td-actions">

        <div class="man-row-btns">

          ${j.url ? `<a href="${esc(j.url)}" target="_blank" class="btn-sm" style="text-decoration:none;">View Job</a>` : ''}

          <button class="btn-icon del" title="Dismiss forever" data-url="${esc(j.url||'')}" onclick="dismissJob(this.dataset.url,this.closest('tr'))">&#215;</button>

        </div>

      </td>

    </tr>`;

  }).join('');

}



async function removeScrapedJob(id, btn) {

  btn.disabled = true;

  try {

    await fetch('/api/pipeline/scraped_jobs/' + id, {method: 'DELETE'});

    _scrapedJobs = _scrapedJobs.filter(j => j.id !== id);

    filterScrapedJobs();

  } catch (err) { showToast(err.message, 'error'); btn.disabled = false; }

}



async function dismissJob(url, row) {

  if (!url) return;

  try {

    await fetch('/api/jobs/dismiss', {

      method: 'POST',

      headers: {'Content-Type': 'application/json'},

      body: JSON.stringify({url}),

    });

    // Remove from both lists and animate out

    if (row) { row.style.opacity = '0'; row.style.transition = 'opacity 0.2s'; setTimeout(() => row.remove(), 200); }

    _scrapedJobs    = _scrapedJobs.filter(j => j.url !== url);

    _matchedJobs    = _matchedJobs.filter(j => j.url !== url);

    _scrapedFiltered = _scrapedFiltered.filter(j => j.url !== url);

    _matchedFiltered = _matchedFiltered.filter(j => j.url !== url);

    // Update counts without full re-render (row already removed from DOM)

    document.getElementById('scraped-count').textContent =

      _scrapedFiltered.length + ' of ' + _scrapedJobs.length + ' jobs';

    // Keep limit in sync after removal (re-render after the fade animation)

    setTimeout(() => { if (_scrapedFiltered.length > 0) _updateStep2Limit(_step2MatchLimit); }, 250);

    if (_matchedFiltered.length !== _matchedJobs.length ||

        document.getElementById('matched-tbody').querySelector(`[data-url="${CSS.escape(url)}"]`)) {

      renderMatchedTable();

    }

  } catch (err) { showToast('Dismiss failed: ' + err.message, 'error'); }

}



function goToMatch() { _stepAvail.step2 = true; setWizardStep(3); }



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

  document.getElementById('match-filter-bar').classList.add('hidden');

  document.getElementById('match-score-stats').classList.add('hidden');

  document.getElementById('match-results-panel').classList.add('hidden');

  document.getElementById('matched-table-wrap').classList.add('hidden');

  const _topBtnR = document.getElementById('btn-goto-select-top');

  if (_topBtnR) { _topBtnR.disabled = true; _topBtnR.style.display = 'none'; }

  const fill = document.getElementById('match-progress-fill');

  if (fill) { fill.style.width = '0%'; fill.style.background = ''; }

  document.getElementById('match-msg').textContent = 'Starting AI scoring…';

  try {

    const _matchIds = (_step2LimitIds.length && _step2LimitIds.length < _scrapedJobs.length)
      ? _step2LimitIds : null;

    const r = await fetch('/api/pipeline/match', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ job_ids: _matchIds }),
    });

    const d = await r.json();

    if (d.error) throw new Error(d.error);

    _startPoll();

    _startMatchProgress();

  } catch (err) {

    showToast(err.message, 'error');

    document.getElementById('btn-match').disabled = false;

    document.getElementById('match-progress').classList.add('hidden');

    document.getElementById('match-empty').classList.remove('hidden');

  }

}



function _applyMatchFilters() {

  const q      = (document.getElementById('matched-search').value || '').toLowerCase();

  const chance = (document.getElementById('match-chance-filter').value || '').toLowerCase();

  // Score is NOT a hard filter — below-threshold rows are shown dimmed, not hidden.

  // Only German-level and text/chance search are hard filters here.

  _matchedFiltered = _matchedJobs.filter(j => {

    if ((j.skip_reason||'').toLowerCase().includes('german level')) return false;

    if (chance && (j.interview_chance||'').toLowerCase() !== chance) return false;

    if (q && !(

      (j.title||'').toLowerCase().includes(q) ||

      (j.company||'').toLowerCase().includes(q) ||

      (j.location||'').toLowerCase().includes(q)

    )) return false;

    return true;

  });

}



function filterMatchedJobs() {

  _applyMatchFilters();

  renderMatchedTable();

}



async function refilterMatchedJobs() {

  const minSc = parseInt(document.getElementById('match-min-score').value) || 0;

  try {

    const cfgR = await fetch('/api/config');

    const cfg  = await cfgR.json();

    cfg.min_match_score = minSc;

    await fetch('/api/config', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(cfg)});

  } catch (_) { /* non-fatal — filter still applies client-side */ }

  _applyMatchFilters();

  renderMatchedTable();

}



function toggleSelectAll(cb) {

  document.querySelectorAll('.job-select-checkbox').forEach(c => c.checked = cb.checked);

  // Sync both checkboxes

  ['matched-select-all','select-all-footer'].forEach(id => {

    const el = document.getElementById(id);

    if (el) el.checked = cb.checked;

  });

  _updateApplySelectedCount();

}



function _updateApplySelectedCount() {

  const n = document.querySelectorAll('.job-select-checkbox:checked').length;

  const btn = document.getElementById('btn-apply-selected');

  const cnt = document.getElementById('apply-selected-count');

  if (cnt) cnt.textContent = '(' + n + ')';

}



function renderMatchedTable() {

  const minSc    = parseInt(document.getElementById('match-min-score').value) || 0;

  const total    = _matchedFiltered.length;

  const aboveCnt = _matchedFiltered.filter(j => {

    const id = j.scraped_id || j.id || 0;

    return !_dismissedJobIds.has(id) && (parseInt(j.match_score)||0) >= minSc;

  }).length;



  document.getElementById('matched-count').textContent =

    total + ' matched by Claude · ' + aboveCnt + ' above your ' + minSc + '% threshold';

  document.getElementById('match-filter-count').textContent =

    aboveCnt + ' above threshold · ' + (total - aboveCnt) + ' below';



  const dismissedBtn = document.getElementById('btn-show-dismissed');

  if (dismissedBtn) dismissedBtn.textContent = '👁 Show dismissed (' + _dismissedJobIds.size + ')';



  const tbody = document.getElementById('matched-tbody');

  if (!total) {

    tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;color:#64748b;padding:20px;">No jobs match the filter.</td></tr>';

    return;

  }

  tbody.innerHTML = _matchedFiltered.map(j => {

    const sc          = parseInt(j.match_score) || 0;

    const jobId       = j.scraped_id || j.id || 0;

    const isDismissed = _dismissedJobIds.has(jobId);

    if (isDismissed && !_showDismissed) return '';

    const dimmed  = sc < minSc;

    const rowCls  = 'clickable' + (dimmed || isDismissed ? ' row-dimmed' : '');

    const easyApplyBadge = j.has_easy_apply

      ? `<span class="badge-easy-apply" style="background:#10b981;color:#fff;padding:2px 6px;border-radius:4px;font-size:0.7rem;font-weight:600;margin-left:6px;display:inline-block;">⚡ Easy Apply</span>`

      : '';

    const titleEl = isDismissed

      ? `<s style="color:#64748b;">${esc(j.title||'?')}</s>${easyApplyBadge}`

      : `${esc(j.title||'?')}${easyApplyBadge}`;

    const scoreBadgeEl = isDismissed

      ? `<span class="badge-de">Dismissed</span>`

      : _scoreBadge(sc);

    const chanceEl = isDismissed ? '' : _chanceBadge(j.interview_chance);

    const notIntBtn = isDismissed

      ? `<button class="btn-sm" onclick="event.stopPropagation();undismissJobById(${jobId})">&#8617; Restore</button>`

      : `<button class="btn-sm" style="color:#f87171;" onclick="event.stopPropagation();dismissJobById(${jobId},this.closest('tr'))">&#128078; Not Interested</button>`;

    const viewBtn = j.url

      ? `<a href="${esc(j.url)}" target="_blank" rel="noopener" class="btn-sm" style="text-decoration:none;">&#128279; View</a>`

      : `<button class="btn-sm" disabled title="No URL available" style="opacity:0.4;cursor:not-allowed;">&#128279; View</button>`;

    return `<tr class="${rowCls}" data-url="${esc(j.url||'')}" onclick="rowClick(event,this)">

      <td onclick="event.stopPropagation()"><input type="checkbox" class="job-select-checkbox row-cb"

        data-job-id="${jobId}" data-dismissed="${isDismissed?'1':'0'}" value="${esc(j.url||'')}"

        ${isDismissed?'disabled':''} onchange="_updateApplySelectedCount()"></td>

      <td class="td-title">${titleEl}</td>

      <td>${esc(j.company||'')}</td>

      <td style="color:#8b949e;">${esc(j.location||'—')}</td>

      <td>${scoreBadgeEl}</td>

      <td>${chanceEl}</td>

      <td onclick="event.stopPropagation()" style="text-align:center;">
        ${isDismissed ? '' : `<input type="checkbox" class="job-tailor-checkbox" data-job-id="${jobId}" title="Generate a tailored resume for this job before applying">`}
      </td>

      <td class="td-actions" onclick="event.stopPropagation()">

        <div class="man-row-btns">

          ${dimmed && !isDismissed ? `<span class="badge-below">Below ${minSc}%</span>` : ''}

          ${viewBtn}

          ${notIntBtn}

        </div>

      </td>

    </tr>`;

  }).join('');

  _updateApplySelectedCount();

}



let _jdCurrentUrl = '';



function rowClick(e, tr) {

  const url = tr.dataset.url;

  if (url) openJobDetail(url);

}



function openJobDetail(url) {

  const j = _matchedJobs.find(x => x.url === url);

  if (!j) return;

  _jdCurrentUrl = url;



  document.getElementById('jd-title').textContent = j.title || '?';

  document.getElementById('jd-meta').textContent =

    [j.company, j.location].filter(Boolean).join(' · ');



  const sc = parseInt(j.match_score) || 0;

  document.getElementById('jd-badges').innerHTML =

    _scoreBadge(sc) + ' ' + _chanceBadge(j.interview_chance);



  const sumWrap = document.getElementById('jd-summary-wrap');

  if (j.match_summary) {

    document.getElementById('jd-summary').textContent = j.match_summary;

    sumWrap.classList.remove('hidden');

  } else {

    sumWrap.classList.add('hidden');

  }



  const skipWrap = document.getElementById('jd-skip-wrap');

  if (j.skip_reason) {

    document.getElementById('jd-skip').textContent = j.skip_reason;

    skipWrap.classList.remove('hidden');

  } else {

    skipWrap.classList.add('hidden');

  }



  const desc       = (j.description || '').trim();

  const descEl     = document.getElementById('jd-desc');

  const descToggle = document.getElementById('jd-desc-toggle');

  descEl.textContent = desc || '—';

  if (descToggle) descToggle.classList.add('hidden');



  const viewLink = document.getElementById('jd-view-link');

  if (j.url) { viewLink.href = j.url; viewLink.style.display = ''; }

  else        { viewLink.style.display = 'none'; }

  document.getElementById('jd-apply-btn').dataset.url = j.url || '';



  document.getElementById('job-detail-overlay').classList.remove('hidden');

  document.getElementById('job-detail-panel').classList.add('open');

}



function closeJobDetail() {

  document.getElementById('job-detail-overlay').classList.add('hidden');

  document.getElementById('job-detail-panel').classList.remove('open');

  _jdCurrentUrl = '';

}



async function dismissJobById(jobId, row) {

  if (!jobId) return;

  try {

    await fetch(`/api/jobs/${jobId}/dismiss`, {method:'POST'});

    _dismissedJobIds.add(jobId);

    // Immediate DOM feedback: dim + strikethrough

    if (row) {

      row.style.opacity    = '0.3';

      row.style.fontStyle  = 'italic';

      row.style.transition = 'opacity 0.2s';

      const titleTd = row.querySelector('.td-title');

      if (titleTd) titleTd.innerHTML = '<s style="color:#64748b;">' + (titleTd.textContent||'') + '</s>';

      const cb = row.querySelector('.job-select-checkbox');

      if (cb) { cb.disabled = true; cb.dataset.dismissed = '1'; }

      // Swap button to Undo

      const niBtn = row.querySelector('button[onclick*="dismissJobById"]');

      if (niBtn) {

        niBtn.textContent = '↩ Undo';

        niBtn.style.color = '#58a6ff';

        niBtn.setAttribute('onclick', `event.stopPropagation();_undoTimer(${jobId},this.closest('tr'))`);

      }

    }

    const dismissedBtn = document.getElementById('btn-show-dismissed');

    if (dismissedBtn) dismissedBtn.textContent = '👁 Show dismissed (' + _dismissedJobIds.size + ')';

    _updateApplySelectedCount();

    // Hide row after 5 s unless Undo clicked

    if (_dismissTimers[jobId]) clearTimeout(_dismissTimers[jobId]);

    _dismissTimers[jobId] = setTimeout(() => {

      delete _dismissTimers[jobId];

      if (!_showDismissed) { _applyMatchFilters(); renderMatchedTable(); }

    }, 5000);

  } catch (err) { showToast('Could not dismiss: ' + err.message, 'error'); }

}



async function _undoTimer(jobId, row) {

  if (_dismissTimers[jobId]) { clearTimeout(_dismissTimers[jobId]); delete _dismissTimers[jobId]; }

  try {

    await fetch(`/api/jobs/${jobId}/undismiss`, {method:'POST'});

    _dismissedJobIds.delete(jobId);

    _applyMatchFilters(); renderMatchedTable();

  } catch (err) { showToast('Could not undo: ' + err.message, 'error'); }

}



async function undismissJobById(jobId) {

  try {

    await fetch(`/api/jobs/${jobId}/undismiss`, {method:'POST'});

    _dismissedJobIds.delete(jobId);

    _applyMatchFilters(); renderMatchedTable();

  } catch (err) { showToast('Could not restore: ' + err.message, 'error'); }

}



function toggleShowDismissed() {

  _showDismissed = !_showDismissed;

  const btn = document.getElementById('btn-show-dismissed');

  if (btn) btn.style.background = _showDismissed ? '#1e3a5f' : '';

  _applyMatchFilters(); renderMatchedTable();

}



function _auditSection(label, jobs, totalScored) {

  if (!jobs.length) return '';

  const rows = jobs.map(j => {

    const sc = parseInt(j.match_score) || 0;

    const reason = j.skip_reason ? `<div class="audit-job-reason">${esc(j.skip_reason)}</div>` : '';

    return `<div class="audit-job-row">

      <div><div class="audit-job-title">${esc(j.title||'?')}</div>

           <div class="audit-job-co">${esc(j.company||'')}</div></div>

      <div class="audit-job-score">${_scoreBadge(sc)}</div>

      <div class="audit-job-score">${_chanceBadge(j.interview_chance)}</div>

      ${reason}

    </div>`;

  }).join('');

  return `<details class="audit-section">

    <summary>${label} <span style="color:#475569;font-weight:400;">(${jobs.length})</span></summary>

    <div class="audit-jobs">${rows}</div>

  </details>`;

}



async function openAuditModal() {

  document.getElementById('audit-modal').classList.remove('hidden');

  document.getElementById('audit-modal-body').innerHTML =

    '<div style="color:#64748b;font-size:0.84rem;text-align:center;padding:20px;">Loading…</div>';

  try {

    const d = await (await fetch('/api/matcher/audit')).json();

    if (d.error) throw new Error(d.error);

    const s = d.summary;

    const t = s.total_scored || 1;

    const pct = n => Math.round(n / t * 100) + '%';

    const bd = d.breakdown;



    document.getElementById('audit-modal-body').innerHTML = `

      <p style="font-size:0.78rem;color:#64748b;margin:0 0 12px;">

        ${t} scored jobs &mdash; min score threshold: <strong style="color:#c9d1d9;">${s.min_match_score}</strong>

      </p>

      <div class="audit-summary">

        <div class="audit-row passed">

          <div class="audit-label">&#10003; Passed threshold</div>

          <div class="audit-n">${s.passed}</div>

          <div class="audit-pct">${pct(s.passed)}</div>

        </div>

        <div class="audit-row failed">

          <div class="audit-label">&#215; Score too low</div>

          <div class="audit-n">${s.failed_score}</div>

          <div class="audit-pct">${pct(s.failed_score)}</div>

        </div>

        <div class="audit-row failed">

          <div class="audit-label">&#215; German too high</div>

          <div class="audit-n">${s.failed_german}</div>

          <div class="audit-pct">${pct(s.failed_german)}</div>

        </div>

        <div class="audit-row failed">

          <div class="audit-label">&#215; Wrong tech stack</div>

          <div class="audit-n">${s.failed_stack}</div>

          <div class="audit-pct">${pct(s.failed_stack)}</div>

        </div>

      </div>

      ${_auditSection('&#10003; Passed', bd.passed, t)}

      ${_auditSection('&#215; Score too low', bd.failed_score, t)}

      ${_auditSection('&#215; German too high', bd.failed_german, t)}

      ${_auditSection('&#215; Wrong tech stack', bd.failed_stack, t)}

    `;

  } catch(err) {

    document.getElementById('audit-modal-body').innerHTML =

      `<div style="color:#f87171;font-size:0.84rem;">Error: ${esc(err.message)}</div>`;

  }

}



function closeAuditModal() {

  document.getElementById('audit-modal').classList.add('hidden');

}



document.addEventListener('keydown', e => {

  if (e.key === 'Escape') {

    if (_jdCurrentUrl) closeJobDetail();

    else closeAuditModal();

  }

});



function toggleJobDesc() {

  const descEl = document.getElementById('jd-desc');

  const btn    = document.getElementById('jd-desc-toggle');

  const full   = descEl.dataset.full || '';

  const SHORT  = 400;

  if (descEl.dataset.expanded === '1') {

    descEl.textContent     = full.slice(0, SHORT) + '…';

    descEl.dataset.expanded = '0';

    btn.textContent        = 'Show more';

  } else {

    descEl.textContent      = full;

    descEl.dataset.expanded = '1';

    btn.textContent         = 'Show less';

  }

}



function _jdApply() {

  const url = document.getElementById('jd-apply-btn').dataset.url;

  if (url) applySingle(url);

}



async function renderMatchedJobs() {

  try {

    const [matchR, cfgR] = await Promise.all([

      fetch('/api/pipeline/matched_jobs'),

      fetch('/api/config'),

    ]);

    const d   = await matchR.json();

    const cfg = await cfgR.json();

    _matchedJobs = d.jobs || [];



    // Seed filter bar from saved config

    const savedMin = cfg.min_match_score ?? 70;

    document.getElementById('match-min-score').value = savedMin;



    document.getElementById('match-progress').classList.add('hidden');

    document.getElementById('match-empty').classList.add('hidden');

    document.getElementById('btn-match').disabled = false;



    if (!_matchedJobs.length) {

      document.getElementById('match-empty').textContent = 'No jobs passed the matching filters.';

      document.getElementById('match-empty').classList.remove('hidden');

      return;

    }



    document.getElementById('match-filter-bar').classList.remove('hidden');

    document.getElementById('matched-table-wrap').classList.remove('hidden');

    _applyMatchFilters();

    renderMatchedTable();



    // Show scoring stats bar — always visible after matching completes

    try {

      const sp = await (await fetch('/api/pipeline/match_progress')).json();

      const statsEl = document.getElementById('match-score-stats');

      if (statsEl && sp.scored > 0) {

        let txt = sp.scored + ' scored by Claude'

          + ' · ' + sp.passed + ' above ' + sp.min_score + '% threshold'

          + ' · ' + sp.below_threshold + ' below threshold';

        if (sp.resume_changed) {

          txt += ' · ⚠ resume changed — all re-scored';

        } else if (sp.fresh > 0 || sp.cache_hits > 0) {

          txt += ' · ' + sp.fresh + ' fresh';

          if (sp.cache_hits > 0) txt += ' · ' + sp.cache_hits + ' from cache';

        }

        statsEl.textContent = txt;

        statsEl.classList.remove('hidden');

      }

    } catch (_) {}

  } catch (err) { showToast(err.message, 'error'); }

}



async function goToSelect() {

  // If step3 data not yet loaded (e.g. user navigated here cold), fetch it first

  if (!_step3Jobs.length) await renderStep3Results();

  if (!_step3Jobs.length) {

    showToast('Run matching first to score jobs before selecting.', 'error');

    return;

  }

  _stepAvail.step3 = true;

  setWizardStep(4);

  if (_step3Jobs.length && aboveThresholdIds.length) {

    // Seed Step 4 with only the jobs that passed the threshold in Step 3

    _matchedJobs = _step3Jobs.filter(j => {

      const id = j.scraped_id || j.id;

      return id && aboveThresholdIds.includes(id);

    });

    const minEl = document.getElementById('match-min-score');

    if (minEl) minEl.value = _step3Threshold;

    document.getElementById('match-filter-bar').classList.remove('hidden');

    document.getElementById('matched-table-wrap').classList.remove('hidden');

    document.getElementById('match-empty').classList.add('hidden');

    _applyMatchFilters();

    renderMatchedTable();

  } else {

    await renderMatchedJobs();

  }

}



// ── Step 3: threshold controls + results render ───────────────────────────────



function _updateStep3Threshold(val) {

  _step3Threshold = Math.min(100, Math.max(0, parseInt(val) || 0));

  const sliderEl = document.getElementById('match-threshold-slider');

  const numEl    = document.getElementById('match-threshold-num');

  const hintVal  = document.getElementById('match-threshold-hint-val');

  if (sliderEl) sliderEl.value = _step3Threshold;

  if (numEl)    numEl.value    = _step3Threshold;

  if (hintVal)  hintVal.textContent = _step3Threshold;



  const visible = _step3Jobs.filter(j => !(j.skip_reason||'').toLowerCase().includes('german level'));

  const above   = visible.filter(j => (parseInt(j.match_score)||0) >= _step3Threshold);

  const below   = visible.filter(j => (parseInt(j.match_score)||0) <  _step3Threshold);

  aboveThresholdIds = above.map(j => j.scraped_id || j.id).filter(Boolean);



  const aboveEl = document.getElementById('match-above-cnt');

  const belowEl = document.getElementById('match-below-cnt');

  if (aboveEl) aboveEl.textContent = above.length;

  if (belowEl) belowEl.textContent = below.length;



  const btnLabel = `Next: Select Jobs (${above.length}) →`;

  const btnTop = document.getElementById('btn-goto-select-top');

  const btnBot = document.getElementById('btn-goto-select');

  if (btnTop) btnTop.textContent = btnLabel;

  if (btnBot) btnBot.textContent = btnLabel;



  renderStep3Table();

}



function onThresholdSlider() {

  _updateStep3Threshold(document.getElementById('match-threshold-slider').value);

}



function onThresholdNum() {

  _updateStep3Threshold(document.getElementById('match-threshold-num').value);

}



async function saveThreshold() {

  try {

    const cfgR = await fetch('/api/config');

    const cfg  = await cfgR.json();

    cfg.min_match_score = _step3Threshold;

    await fetch('/api/config', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(cfg)});

    const savedEl = document.getElementById('match-threshold-saved');

    if (savedEl) {

      savedEl.style.opacity = '1';

      setTimeout(() => { savedEl.style.opacity = '0'; }, 2000);

    }

  } catch (err) { showToast('Could not save threshold: ' + err.message, 'error'); }

}



function _detectPlatform(job) {

  const url = (job.url || job.job_url || '').toLowerCase();

  if (url.includes('linkedin.com'))      return 'LinkedIn';

  if (url.includes('greenhouse.io'))     return 'Greenhouse';

  if (url.includes('lever.co'))          return 'Lever';

  if (url.includes('smartrecruiters'))   return 'SmartRecruiters';

  if (url.includes('ashbyhq'))           return 'Ashby';

  if (url.includes('workable'))          return 'Workable';

  if (url.includes('personio'))          return 'Personio';

  if (url.includes('recruitee'))         return 'Recruitee';

  if (url.includes('bamboohr'))          return 'BambooHR';

  if (url.includes('teamtailor'))        return 'TeamTailor';

  if (url.includes('workday'))           return 'Workday';

  if (url.includes('stepstone'))         return 'StepStone';

  if (url.includes('xing.com'))          return 'Xing';

  if (url.includes('indeed.com'))        return 'Indeed';

  if (url.includes('jobvite'))           return 'Jobvite';

  if (url.includes('icims'))             return 'iCIMS';

  if (url.includes('taleo'))             return 'Taleo';

  if (url.includes('successfactors'))    return 'SAP SF';

  return job.source || 'External';

}



function renderStep3Table() {

  const tbody = document.getElementById('match-results-tbody');

  if (!tbody) return;

  const visible = _step3Jobs.filter(j => !(j.skip_reason||'').toLowerCase().includes('german level'));

  const sorted  = [...visible].sort((a, b) => {

    const aA = (parseInt(a.match_score)||0) >= _step3Threshold;

    const bA = (parseInt(b.match_score)||0) >= _step3Threshold;

    if (aA !== bA) return bA ? 1 : -1;

    return (parseInt(b.match_score)||0) - (parseInt(a.match_score)||0);

  });

  if (!sorted.length) {

    tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:#64748b;padding:20px;">No scored jobs found.</td></tr>';

    return;

  }

  let passedThreshold = true;

  tbody.innerHTML = sorted.map(j => {

    const sc      = parseInt(j.match_score) || 0;

    const isAbove = sc >= _step3Threshold;

    let sep = '';

    if (passedThreshold && !isAbove) {

      sep = `<tr><td colspan="7" style="text-align:center;font-size:0.74rem;color:#475569;padding:4px 8px;`

          + `background:#0d1117;font-style:italic;border-top:1px dashed #21262d;">`

          + `&#8212; below threshold (${_step3Threshold}%) &#8212;</td></tr>`;

      passedThreshold = false;

    }

    const skipHtml  = (!isAbove && j.skip_reason)

      ? `<div style="font-size:0.72rem;color:#94a3b8;font-style:italic;margin-top:2px;">${esc(j.skip_reason)}</div>` : '';

    const rowStyle  = isAbove ? '' : 'opacity:0.35;background:#0b0f14;';

    const scStyle   = isAbove ? '' : 'font-style:italic;';

    const scCls     = sc >= 75 ? 'badge-score-hi' : sc >= 60 ? 'badge-score-mid' : 'badge-score-lo';

    const easyApplyBadge = j.has_easy_apply

      ? `<span class="badge-easy-apply" style="background:#10b981;color:#fff;padding:2px 6px;border-radius:4px;font-size:0.7rem;font-weight:600;margin-left:6px;display:inline-block;">⚡ Easy Apply</span>`

      : '';

    const platform  = _detectPlatform(j);

    const jobUrl    = j.url || j.job_url || '';

    const locText   = esc(j.location || '—');

    const viewBtn   = jobUrl

      ? `<a href="${esc(jobUrl)}" target="_blank" rel="noopener" class="btn-sm" style="text-decoration:none;">&#128279; View</a>`

      : `<button class="btn-sm" disabled title="No URL available" style="opacity:0.4;cursor:not-allowed;">&#128279; View</button>`;

    return sep + `<tr style="${rowStyle}" title="${isAbove ? '' : 'Below ' + _step3Threshold + '% threshold'}">

      <td class="td-title" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(j.title||'?')}${easyApplyBadge}${skipHtml}</td>

      <td style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(j.company||'')}</td>

      <td style="color:#8b949e;">${locText}</td>

      <td><span class="badge-score ${scCls}" style="${scStyle}">${sc}%</span></td>

      <td>${_chanceBadge(j.interview_chance)}</td>

      <td>${platform ? `<span class="badge-source">${esc(platform)}</span>` : ''}</td>

      <td>${viewBtn}</td>

    </tr>`;

  }).join('');

}



async function renderStep3Results() {

  try {

    const [resR, cfgR] = await Promise.all([

      fetch('/api/matcher/results'),

      fetch('/api/config'),

    ]);

    const d   = await resR.json();

    const cfg = await cfgR.json();

    if (d.error) throw new Error(d.error);

    _step3Jobs = d.jobs || [];



    const savedMin = cfg.min_match_score ?? 70;

    const nextBtn  = document.getElementById('btn-goto-select');



    document.getElementById('match-progress').classList.add('hidden');

    document.getElementById('btn-match').disabled = false;



    if (!d.has_results) {

      // No DB data — leave panel hidden, keep Next button disabled

      if (nextBtn) nextBtn.disabled = true;

      return;

    }



    document.getElementById('match-results-panel').classList.remove('hidden');

    if (nextBtn) nextBtn.disabled = false;

    const topBtn = document.getElementById('btn-goto-select-top');

    if (topBtn) { topBtn.disabled = false; topBtn.style.display = ''; }

    _stepAvail.step3 = true;

    _stepAvail.step4 = true;

    _updateStep3Threshold(savedMin);

  } catch (err) { showToast('Results load error: ' + err.message, 'error'); }

}



// ── Step 4 → 5: collect jobs and start apply session ──────────────────────────



function _tailorRequestedIds() {

  return Array.from(document.querySelectorAll('.job-tailor-checkbox:checked'))
    .map(cb => parseInt(cb.dataset.jobId)).filter(Boolean);

}



async function applySelectedJobs() {

  const checked = document.querySelectorAll('.job-select-checkbox:checked');

  const ids = Array.from(checked)

    .filter(cb => cb.dataset.dismissed !== '1')

    .map(cb => parseInt(cb.dataset.jobId)).filter(Boolean);

  if (!ids.length) {

    showToast('No jobs selected. Please check at least one job.', 'error');

    return;

  }

  await _startApplyWithIds(ids, _tailorRequestedIds());

}



async function applyAllJobs() {

  const all = document.querySelectorAll('.job-select-checkbox');

  const ids = Array.from(all)

    .filter(cb => cb.dataset.dismissed !== '1')

    .map(cb => parseInt(cb.dataset.jobId)).filter(Boolean);

  if (!ids.length) {

    showToast('No jobs to apply to. Run matching first.', 'error');

    return;

  }

  await _startApplyWithIds(ids, _tailorRequestedIds());

}



async function _startApplyWithIds(jobIds, tailorIds) {

  _applySessionStarted = false;

  try {

    const r = await fetch('/api/apply/start', {

      method: 'POST',

      headers: {'Content-Type': 'application/json'},

      body: JSON.stringify({job_ids: jobIds, tailor_ids: tailorIds || []}),

    });

    const d = await r.json();

    if (d.error) throw new Error(d.error);

    _applySessionStarted = true;

    _applyRunning = true;

    _applyStats = {total: d.total || jobIds.length, success: 0, manual: 0, failed: 0};

    _applyJobs  = d.jobs || [];

    document.getElementById('asb-total').textContent   = _applyStats.total;

    document.getElementById('asb-success').textContent = '0';

    document.getElementById('asb-manual').textContent  = '0';

    document.getElementById('asb-failed').textContent  = '0';

    renderApplyCards();

    document.getElementById('apply-log-body').innerHTML = '';

    connectApplyStream();

    await _refreshStepAvail();

    _stepAvail.step5 = true;

    setWizardStep(5);

    document.getElementById('btn-apply-start').style.display = 'none';

    document.getElementById('btn-apply-stop').style.display  = '';

  } catch(err) {

    showToast(err.message, 'error');

    _applyRunning = false;

  }

}



// Legacy single-job helper (used from job detail panel)

function applySingle(url) {

  const j = _matchedJobs.find(x => x.url === url);

  if (!j) { showToast('Job not found.', 'error'); return; }

  const id = j.scraped_id || 0;

  if (!id) { showToast('Job ID missing — cannot apply.', 'error'); return; }

  _startApplyWithIds([id]);

}



// Keep applyAll/applySelected as aliases (called from job detail panel, etc.)

function applyAll() { applyAllJobs(); }

function applySelected() { applySelectedJobs(); }



// ── Apply tab ─────────────────────────────────────────────────────────────────

let _applyJobs     = [];   // jobs queued for this session

let _applyRunning  = false;

let _applyEvtSrc   = null; // EventSource for SSE stream

let _applyStats    = {total:0, success:0, manual:0, failed:0};



function initApplyStep() {

  loadManualQueue();

  renderApplyCards();

}



function setApplyJobs(jobs) {

  _applyJobs = jobs || [];

  const t = document.getElementById('asb-total');

  const s = document.getElementById('asb-success');

  const m = document.getElementById('asb-manual');

  const f = document.getElementById('asb-failed');

  if (t) t.textContent = _applyJobs.length;

  if (s) s.textContent = '0';

  if (m) m.textContent = '0';

  if (f) f.textContent = '0';

  _applyStats = {total:_applyJobs.length, success:0, manual:0, failed:0};

  renderApplyCards();

}



function _platformBadgeClass(p) {

  const known = ['linkedin','greenhouse','lever','smartrecruiters','ashby','workday',

                 'personio','workable','recruitee','teamtailor'];

  return known.includes(p) ? 'pb-' + p : 'pb-unknown';

}



function _buildJobCard(j, idx, section) {

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

  const _sitMap = {
    done:    `<span class="ajc-situation sit-done">&#10003; Applied</span>`,
    manual:  `<span class="ajc-situation sit-manual">&#9888; ${esc(j._applyNote || 'Manual Queue')}</span>`,
    failed:  `<span class="ajc-situation sit-failed">&#10007; ${esc(j._applyNote || 'Failed')}</span>`,
    expired: `<span class="ajc-situation sit-expired">&#8856; No longer accepting</span>`,
    running: `<span class="ajc-situation sit-pending">&#9654; Applying…</span>`,
  };

  const situationBadge = _sitMap[j._applyStatus] || '';

  const manualMarkBtn = section === 'manual'
    ? `<button class="ajc-btn ajc-btn-applied" onclick="markJobApplied(${idx})" title="Mark as manually applied">&#10003; I Applied</button>`
    : '';

  const retryBtn = section !== 'pending'
    ? `<button class="ajc-btn ajc-btn-retry" onclick="retryJob(${idx})" title="Reset to pending and retry">&#8617; Retry</button>`
    : '';

  const actionBtns = `<div class="ajc-actions">
         ${situationBadge}
         ${manualMarkBtn}
         ${retryBtn}
         <button class="ajc-btn ajc-btn-remove" onclick="removeApplyCard(${idx})" title="Remove from list">&#10005; Remove</button>
         ${openBtn}
       </div>`;

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

    document.getElementById('section-failed').style.display  = 'none';

    return;

  }

  if (empty) empty.style.display = 'none';



  const pending = _applyJobs.filter((j,i) =>

    !j._applyStatus || j._applyStatus === 'pending' || j._applyStatus === 'running');

  const expired = _applyJobs.filter((j,i) => j._applyStatus === 'expired');

  const failed  = _applyJobs.filter((j,i) => j._applyStatus === 'failed');

  const manual  = _applyJobs.filter((j,i) => j._applyStatus === 'manual');

  const applied = _applyJobs.filter((j,i) => j._applyStatus === 'done');



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



  // Expired section

  const expiredSection = document.getElementById('section-expired');

  const expiredContainer = document.getElementById('cards-expired');

  const expiredCount = document.getElementById('count-expired');

  if (expired && expired.length) {

    expiredSection.style.display = '';

    expiredContainer.innerHTML = expired.map(j => _buildJobCard(j, _applyJobs.indexOf(j), 'expired')).join('');

    if (expiredCount) expiredCount.textContent = `(${expired.length})`;

  } else if (expiredSection) {

    expiredSection.style.display = 'none';

  }



  // Failed section

  const failedSection = document.getElementById('section-failed');

  const failedContainer = document.getElementById('cards-failed');

  const failedCount = document.getElementById('count-failed');

  if (failedSection) {

    if (failed.length) {

      failedSection.style.display = '';

      failedContainer.innerHTML = failed.map(j => _buildJobCard(j, _applyJobs.indexOf(j), 'failed')).join('');

      if (failedCount) failedCount.textContent = `(${failed.length})`;

    } else {

      failedSection.style.display = 'none';

    }

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

  showToast('Job returned to queue \u2014 run Apply again to retry', 'info');

}



function removeFromManual(idx) {

  const j = _applyJobs[idx];

  if (!j) return;

  _applyJobs.splice(idx, 1);

  _applyStats.manual = Math.max(0, (_applyStats.manual||0) - 1);

  document.getElementById('asb-manual').textContent = _applyStats.manual;

  renderApplyCards();

  showToast('Removed from manual queue', 'info');

}



function removeApplyCard(idx) {

  const j = _applyJobs[idx];

  if (!j) return;

  const st = j._applyStatus;

  // Tell the backend to skip this job if the session is still running
  if (!st || st === 'pending' || st === 'running') {

    if (j.url) fetch('/api/apply/skip', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({url: j.url})
    });

  }

  if (st === 'manual')  _applyStats.manual  = Math.max(0, (_applyStats.manual||0)  - 1);

  if (st === 'failed')  _applyStats.failed  = Math.max(0, (_applyStats.failed||0)  - 1);

  if (st === 'done')    _applyStats.success = Math.max(0, (_applyStats.success||0) - 1);

  _applyJobs.splice(idx, 1);

  updateApplySessionBar();

  renderApplyCards();

  showToast('Removed', 'info');

}



async function excludeExpiredJob(idx) {

  const j = _applyJobs[idx];

  if (!j) return;

  try {

    await fetch('/api/applications/manual', {

      method: 'POST',

      headers: {'Content-Type':'application/json'},

      body: JSON.stringify({

        job_url: j.url, title: j.title, company: j.company,

        platform: _detectPlatformJS(j.url||''), action: 'exclude',

      }),

    });

  } catch(_) {}

  _applyJobs.splice(idx, 1);

  renderApplyCards();

  showToast('Job excluded — will never appear again', 'info');

}



function _detectPlatformJS(url) {

  const u = (url||'').toLowerCase();

  if (u.includes('linkedin.com'))       return 'linkedin';

  if (u.includes('greenhouse.io') || u.includes('boards.greenhouse')) return 'greenhouse';

  if (u.includes('lever.co'))           return 'lever';

  if (u.includes('smartrecruiters'))    return 'smartrecruiters';

  if (u.includes('ashbyhq') || u.includes('jobs.ashby')) return 'ashby';

  if (u.includes('myworkdayjobs') || u.includes('workday')) return 'workday';

  if (u.includes('taleo.net'))          return 'taleo';

  if (u.includes('icims.com'))          return 'icims';

  if (u.includes('bamboohr'))           return 'bamboohr';

  if (u.includes('personio'))           return 'personio';

  if (u.includes('workable'))           return 'workable';

  if (u.includes('recruitee'))          return 'recruitee';

  if (u.includes('jazzhr') || u.includes('resumatorjobs')) return 'jazzhr';

  if (u.includes('teamtailor'))         return 'teamtailor';

  if (u.includes('jobvite'))            return 'jobvite';

  if (u.includes('successfactors'))     return 'successfactors';

  if (u.includes('stepstone'))          return 'stepstone';

  return 'unknown';

}



function applyLogLine(type, text) {

  const body = document.getElementById('apply-log-body');

  const div  = document.createElement('div');

  div.className = 'alog-line alog-' + type;

  div.textContent = text;

  body.appendChild(div);

  body.scrollTop = body.scrollHeight;

}



function _pipelineLogApply(d) {

  const box = document.getElementById('log-viewer');

  if (!box) return;

  let text = '', color = '';

  if (d.type === 'apply_start') {

    text = '[apply] ▶ Starting session — ' + d.total + ' job(s)';

    color = '#93c5fd';

  } else if (d.type === 'platform_detected') {

    text = '[apply] [' + d.platform + '] ' + (d.url||'').split('/').pop();

  } else if (d.type === 'apply_step') {

    const step = d.step || '';

    text = '[apply]   → ' + step;

    if (step.startsWith('⚠')) color = '#fb923c';

  } else if (d.type === 'apply_answer') {

    text = '[apply]   ✎ ' + d.label + ' = ' + d.answer;

  } else if (d.type === 'apply_result') {

    if (d.success) { text = '[apply] ✓ Applied: ' + d.company + ' — ' + d.title; color = '#4ade80'; }

    else if (d.manual) { text = '[apply] ⚠ Manual: ' + d.company + ' — ' + (d.note||''); color = '#fb923c'; }

    else { text = '[apply] ✗ Failed: ' + d.company + ' — ' + (d.note||''); color = '#f87171'; }

  } else if (d.type === 'session_done') {

    text = '[apply] ■ Done — ✓ ' + d.success + '  ⚠ ' + d.manual + '  ✗ ' + d.failed;

    color = '#93c5fd';

  }

  if (!text) return;

  const div = document.createElement('div');

  div.className = 'log-line log-default';

  div.textContent = text;

  if (color) div.style.color = color;

  box.appendChild(div);

  while (box.children.length > 500) box.children[0].remove();

  if (box.scrollHeight - box.scrollTop - box.clientHeight < 80) box.scrollTop = box.scrollHeight;

}



function handleApplyEvent(evt) {

  try {

    const d = JSON.parse(evt.data);

    switch (d.type) {

      case 'apply_start':

        document.getElementById('apply-log-body').innerHTML = '';

        _applyStats = {total: d.total || 0, success: 0, manual: 0, failed: 0};

        document.getElementById('asb-total').textContent   = _applyStats.total;

        document.getElementById('asb-success').textContent = '0';

        document.getElementById('asb-manual').textContent  = '0';

        document.getElementById('asb-failed').textContent  = '0';

        applyLogLine('start', '▶ Starting apply session — ' + d.total + ' job(s)');

        _pipelineLogApply(d);

        break;

      case 'platform_detected':

        applyLogLine('step', '  [' + d.platform + '] ' + (d.url||'').split('/').pop());

        // mark card as running

        const idx = _applyJobs.findIndex(j => j.url === d.url);

        if (idx >= 0) { _applyJobs[idx]._applyStatus = 'running'; renderApplyCards(); }

        _pipelineLogApply(d);

        break;

      case 'apply_step': {

        const step = d.step || '';

        const body = document.getElementById('apply-log-body');

        const entry = document.createElement('div');

        entry.className = 'alog-line alog-step';

        entry.textContent = '    → ' + step;

        if (step.startsWith('⚠️')) {

          entry.style.color = '#e6a817';

          entry.style.fontWeight = 'bold';

        }

        body.appendChild(entry);

        body.scrollTop = body.scrollHeight;

        _pipelineLogApply(d);

        break;

      }

      case 'apply_answer':

        applyLogLine('answer', '    ✎ ' + d.label + ' = ' + d.answer);

        _pipelineLogApply(d);

        break;

      case 'apply_result': {

        const i = _applyJobs.findIndex(j => j.url === d.url);

        if (d.success) {

          if (i>=0){ _applyJobs[i]._applyStatus='done'; _applyJobs[i]._applyNote=''; }

          _applyStats.success++;

          applyLogLine('success', '  ✓ Applied: ' + d.company + ' — ' + d.title + (d.apply_type ? ' [' + d.apply_type + ']' : ''));

        } else if (d.expired) {

          if (i>=0){ _applyJobs[i]._applyStatus='expired'; _applyJobs[i]._applyNote=d.note||'No longer accepting applications'; }

          _applyStats.failed++;

          applyLogLine('failed', '  ⊘ Expired: ' + d.company + ' — ' + d.title);

        } else if (d.manual) {

          if (i>=0){ _applyJobs[i]._applyStatus='manual'; _applyJobs[i]._applyNote=d.note; }

          _applyStats.manual++;

          applyLogLine('manual', '  ⚠ Manual: ' + d.company + ' — ' + (d.note||''));

        } else {

          if (i>=0){ _applyJobs[i]._applyStatus='failed'; _applyJobs[i]._applyNote=d.note; }

          _applyStats.failed++;

          applyLogLine('failed', '  ✗ Failed: ' + d.company + ' — ' + (d.note||''));

        }

        updateApplySessionBar();

        renderApplyCards();

        _pipelineLogApply(d);

        break;

      }

      case 'delay':

        applyLogLine('delay', '  ⏳ ' + d.seconds + 's delay…');

        break;

      case 'session_done':

        applyLogLine('done', '■ Session done — ✓ ' + d.success + '  ⚠ ' + d.manual + '  ✗ ' + d.failed);

        _applyRunning = false;

        _applySessionStarted = false;

        const startBtn = document.getElementById('btn-apply-start');

        const stopBtn  = document.getElementById('btn-apply-stop');

        if (startBtn) startBtn.style.display = '';

        if (stopBtn)  stopBtn.style.display  = 'none';

        // Show summary banner above the job cards

        const summaryEl = document.getElementById('apply-session-summary');

        if (summaryEl) {

          const allFailed  = d.success === 0 && d.manual === 0 && d.failed > 0;

          const allSuccess = d.success > 0   && d.manual === 0 && d.failed === 0;

          let bg = '#0d1f2d', border = '#1d4ed8', color = '#93c5fd'; // mixed — blue

          if (allSuccess) { bg='#0d2b1a'; border='#16a34a'; color='#4ade80'; }

          if (allFailed)  { bg='#2b0a0a'; border='#dc2626'; color='#f87171'; }

          summaryEl.style.cssText = `display:block;margin-bottom:12px;padding:12px 16px;

            border-radius:8px;background:${bg};border:1px solid ${border};color:${color};font-size:0.9rem;`;

          const parts = [];

          if (d.success) parts.push('<strong style="font-size:1rem">✓ ' + d.success + ' applied</strong>');

          if (d.manual)  parts.push('<strong style="font-size:1rem">⚠ ' + d.manual + ' manual queue</strong>');

          if (d.failed)  parts.push('<strong style="font-size:1rem">✗ ' + d.failed + ' failed</strong>');

          summaryEl.innerHTML = 'Session complete — ' + parts.join('  ·  ');

        }

        if (_applyEvtSrc) { _applyEvtSrc.close(); _applyEvtSrc = null; }

        // Auto-dismiss jobs still in failed/expired at session end (user showed no interest)
        _applyJobs.forEach(j => {
          if ((j._applyStatus === 'failed' || j._applyStatus === 'expired') && j.url) {
            fetch('/api/jobs/dismiss', {
              method: 'POST',
              headers: {'Content-Type':'application/json'},
              body: JSON.stringify({url: j.url})
            });
          }
        });

        loadManualQueue();

        fetchDashboard();

        _pipelineLogApply(d);

        break;

    }

  } catch(_) {}

}



function updateApplySessionBar() {

  document.getElementById('asb-success').textContent = _applyStats.success;

  document.getElementById('asb-manual').textContent  = _applyStats.manual;

  document.getElementById('asb-failed').textContent  = _applyStats.failed;

}



async function startApplySession() {

  if (_applyRunning) { showToast('Already running.', 'error'); return; }

  if (!_applyJobs.length) {

    // Load matched jobs from DB and filter out already-applied

    showToast('Loading matched jobs from database…', 'info');

    try {

      const r = await fetch('/api/pipeline/matched_jobs');

      const d = await r.json();

      const allJobs = d.jobs || [];

      const appliedUrls = new Set();

      for (const group of ['Applied', 'Interviews', 'Rejected', 'Offer']) {

        for (const app of ((_dashData || {})[group] || [])) {

          if (app.job_url) appliedUrls.add(app.job_url);

        }

      }

      const toApply = allJobs.filter(j =>

        !(j.skip_reason || '').toLowerCase().includes('german level') &&

        j.url && !appliedUrls.has(j.url)

      );

      if (!toApply.length) { showToast('No new matched jobs to apply to.', 'error'); return; }

      const ids = toApply.map(j => j.scraped_id).filter(Boolean);

      if (!ids.length) { showToast('Cannot determine job IDs.', 'error'); return; }

      await _startApplyWithIds(ids);

    } catch (e) {

      showToast('Failed to load matched jobs: ' + e.message, 'error');

    }

    return;

  }

  const ids = _applyJobs.map(j => j.scraped_id || j._id).filter(Boolean);

  if (ids.length) {

    await _startApplyWithIds(ids);

  } else {

    showToast('Cannot determine job IDs. Please re-select from Step 4.', 'error');

  }

}



async function stopApplySession() {

  await fetch('/api/apply/stop', {method:'POST'});

  applyLogLine('failed', '■ Stop requested…');

}



async function debugApply() {

  const url     = document.getElementById('debug-job-url').value.trim();

  const title   = document.getElementById('debug-job-title').value.trim();

  const company = document.getElementById('debug-job-company').value.trim();

  const log     = document.getElementById('debug-apply-log');

  // Clear previous result banner

  const prev = document.getElementById('debug-result-banner');

  if (prev) prev.remove();

  if (!url) { showToast('Paste a URL first'); return; }

  log.textContent = 'Starting apply...\\n';



  try {

    const res = await fetch('/api/apply/debug', {

      method: 'POST',

      headers: {'Content-Type': 'application/json'},

      body: JSON.stringify({url, title, company}),

    });

    const data = await res.json();

    log.textContent += JSON.stringify(data, null, 2) + '\\n\\n';

    if (!res.ok) return;

  } catch (e) {

    log.textContent += 'Error: ' + e.message;

    return;

  }



  // Connect to SSE stream to show live progress

  const es = new EventSource('/api/apply/stream');

  es.onmessage = e => {

    try {

      const evt = JSON.parse(e.data);

      // Pretty-print each event type

      let line = '', color = '#94a3b8', weight = '400', bg = '';

      if (evt.type === 'platform_detected') {

        line = '\u2b21 platform  ' + evt.platform.toUpperCase();

        color = '#60a5fa';

      } else if (evt.type === 'apply_step') {

        const step = (evt.step || '').toLowerCase();

        if (step.includes('applied') || step.includes('submitted') || step.includes('success') || step.includes('page closed after submit')) {

          line = '\u2713 ' + evt.step; color = '#4ade80'; weight = '600';

        } else if (step.includes('manual') || step.includes('captcha') || step.includes('going to manual')) {

          line = '\u26a0 ' + evt.step; color = '#fb923c';

        } else if (step.includes('error') || step.includes('crash') || step.includes('failed')) {

          line = '\u2717 ' + evt.step; color = '#f87171';

        } else if (step.includes('retry') || step.includes('attempt')) {

          line = '\u21ba ' + evt.step; color = '#c084fc';

        } else if (step.includes('resume')) {

          line = ' ' + evt.step; color = '#34d399';

        } else if (step.includes('delay') || step.includes('wait') || step.includes('s delay')) {

          line = '\u23f3 ' + evt.step; color = '#475569';

        } else {

          line = '\u2192 ' + evt.step; color = '#93c5fd';

        }

      } else if (evt.type === 'apply_answer') {

        line = '\u270e ' + evt.label + ' \u2192 ' + evt.answer;

        color = '#a78bfa';

      } else if (evt.type === 'apply_result') {

        if (evt.success) {

          line = '\u2713 Applied successfully' + (evt.note ? ' \u2014 ' + evt.note : '');

          color = '#4ade80'; weight = '700'; bg = 'rgba(34,197,94,0.08)';

        } else if (evt.manual) {

          line = '\u26a0 Sent to manual queue' + (evt.note ? ' \u2014 ' + evt.note : '');

          color = '#fb923c'; weight = '600'; bg = 'rgba(251,146,60,0.08)';

        } else {

          line = '\u2717 Failed' + (evt.note ? ' \u2014 ' + evt.note : '');

          color = '#f87171'; weight = '600'; bg = 'rgba(248,113,113,0.08)';

        }

      } else if (evt.type === 'delay') {

        line = '\u23f3 ' + evt.seconds + 's delay'; color = '#475569';

      } else if (evt.type === 'job_expired') {

        const i = _applyJobs.findIndex(j => j.url === evt.url);

        if (i >= 0) {

          _applyJobs[i]._applyStatus = 'expired';

          _applyJobs[i]._applyNote   = evt.reason || 'No longer accepting applications';

          renderApplyCards();

        }

        line = '\u2298 Expired \u2014 ' + (evt.reason || 'No longer accepting applications');

        color = '#6b7280';

      } else if (evt.type === 'session_done') {

        line = '\u25a0 Session complete \u2014 ' + (evt.success||0) + ' applied \u00b7 ' + (evt.manual||0) + ' manual \u00b7 ' + (evt.failed||0) + ' failed';

        color = '#f1f5f9'; weight = '700'; bg = 'rgba(255,255,255,0.05)';

      } else {

        line = '\u00b7 ' + (evt.type||'?') + ' \u2014 ' + (evt.step || evt.note || evt.detail || '');

        color = '#64748b';

      }

      if (line) {

        const el = document.createElement('div');

        el.style.cssText = 'color:' + color + ';font-weight:' + weight + ';white-space:pre-wrap;padding:1px 4px;border-radius:3px;' + (bg ? 'background:' + bg + ';' : '');

        el.textContent = line;

        log.appendChild(el);

      }

      log.scrollTop = log.scrollHeight;



      // Show result banner when apply_result arrives

      if (evt.type === 'apply_result') {

        es.close();

        const panel = document.getElementById('debug-apply-panel');

        const banner = document.createElement('div');

        banner.id = 'debug-result-banner';

        if (evt.success) {

          banner.style.cssText = 'margin-top:10px;padding:10px 14px;border-radius:8px;background:#0d2b1a;border:1px solid #16a34a;color:#4ade80;font-weight:600;font-size:0.9rem;';

          const _t = evt.title || title || '';

          const _c = evt.company || (company !== 'Unknown' ? company : '');

          banner.innerHTML = '✓ Application submitted successfully for <strong>' + (_t + (_c ? ' @ ' + _c : '') || 'this job') + '</strong>';

        } else if (evt.manual) {

          banner.style.cssText = 'margin-top:10px;padding:10px 14px;border-radius:8px;background:#2b1a00;border:1px solid #d97706;color:#fbbf24;font-weight:600;font-size:0.9rem;';

          banner.innerHTML = '⚠ Added to Manual Apply Queue — <span style="font-weight:400">' + (evt.note||'requires manual action') + '</span>';

        } else {

          banner.style.cssText = 'margin-top:10px;padding:10px 14px;border-radius:8px;background:#2b0a0a;border:1px solid #dc2626;color:#f87171;font-weight:600;font-size:0.9rem;';

          banner.innerHTML = '✗ Apply failed — <span style="font-weight:400">' + (evt.note||'unknown error') + '</span>';

        }

        panel.appendChild(banner);

      }

    } catch (_) {}

  };

  es.onerror = () => es.close();

}



function connectApplyStream() {

  if (_applyEvtSrc) { _applyEvtSrc.close(); }

  _applyEvtSrc = new EventSource('/api/apply/stream');

  _applyEvtSrc.onmessage = handleApplyEvent;

  _applyEvtSrc.onerror   = () => {};

}



let _mqAllSessions = false;

let _mqItems = [];



function toggleMqAllSessions() {

  _mqAllSessions = !_mqAllSessions;

  const btn = document.getElementById('btn-mq-all-sessions');

  if (btn) btn.textContent = _mqAllSessions ? '📋 Current session only' : '📋 Show all sessions';

  loadManualQueue();

}



async function loadManualQueue() {

  try {

    const url = _mqAllSessions ? '/api/apply/manual_queue?all_sessions=true' : '/api/apply/manual_queue';

    const d = await (await fetch(url)).json();

    _mqItems = d.items || [];

    const empty = document.getElementById('manual-queue-empty');

    const table = document.getElementById('manual-queue-table');

    const tbody = document.getElementById('manual-queue-tbody');

    if (!_mqItems.length) {

      empty.style.display = ''; table.style.display = 'none';

      return;

    }

    empty.style.display = 'none'; table.style.display = '';

    tbody.innerHTML = _mqItems.map((item, idx) => `<tr>

      <td style="font-weight:600;color:#f1f5f9;">${esc(item.company||'')}</td>

      <td>${esc(item.title||'')}</td>

      <td><span class="platform-badge ${_platformBadgeClass(item.platform||'unknown')}">${esc(item.platform||'?')}</span></td>

      <td style="color:#94a3b8;font-size:0.78rem;">${esc(item.note||'')}</td>

      <td>${item.job_url ? `<a href="${esc(item.job_url)}" target="_blank" class="btn-sm">Open</a>` : '—'}</td>

      <td style="white-space:nowrap;display:flex;gap:4px;">

        <button class="btn-sm" style="background:#166534;color:#4ade80;border:1px solid #16a34a;cursor:pointer;font-weight:600;" onclick="mqAction(${idx},'applied',this)">&#10003; I Applied</button>

        <button class="btn-sm" style="background:#450a0a;color:#f87171;border:1px solid #7f1d1d;cursor:pointer;" onclick="mqAction(${idx},'skip',this)">&#10005; Not Interested</button>

      </td>

    </tr>`).join('');

  } catch(_) {}

}



async function clearManualQueue() {
  if (!_mqItems.length) return;
  // Fire-and-forget exclude calls to backend
  _mqItems.forEach(item => {
    fetch('/api/applications/manual', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        id: item.id, job_url: item.job_url || item.url,
        title: item.title, company: item.company,
        platform: item.platform, action: 'exclude',
      }),
    }).catch(() => {});
  });
  // Clear UI immediately without re-fetching
  _mqItems = [];
  const empty = document.getElementById('manual-queue-empty');
  const table = document.getElementById('manual-queue-table');
  if (empty) empty.style.display = '';
  if (table) table.style.display = 'none';
  showToast('Manual queue cleared', 'info');
}



async function mqAction(idx, action, btn) {

  const item = _mqItems[idx];

  if (!item) return;

  if (btn) { btn.disabled = true; btn.closest('tr').style.opacity = '0.5'; }

  try {

    const r = await fetch('/api/applications/manual', {

      method: 'POST',

      headers: {'Content-Type': 'application/json'},

      body: JSON.stringify({

        id: item.id, job_url: item.job_url, title: item.title,

        company: item.company, platform: item.platform,

        action: action,

      }),

    });

    const d = await r.json();

    if (d.error) {

      showToast(d.error, 'error');

      if (btn) { btn.disabled = false; btn.closest('tr').style.opacity = ''; }

      return;

    }

    showToast(action === 'applied' ? 'Marked as Applied ✓' : 'Job excluded — will not appear again');

    await loadManualQueue();

    if (action === 'applied') fetchDashboard();

  } catch (e) {

    showToast(e.message, 'error');

    if (btn) { btn.disabled = false; btn.closest('tr').style.opacity = ''; }

  }

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

const _TEXT_KEYS   = ['notify_email'];

const _NUM_KEYS    = ['min_match_score','max_applications_per_day','scrape_pool_size'];

const _TOG_KEYS    = ['auto_confirm_recruiter_call','auto_confirm_technical','headless','confirm_before_apply','retry_captcha_as_manual','smart_scrape','only_easy_apply','skip_cached_jobs'];

// smart_scrape and only_easy_apply rendered inline with scrape_pool_size — still in _TOG_KEYS so loadConfig/saveConfig handle it automatically

const _SELECT_KEYS = ['notice_period','salary_currency','willing_to_travel'];

// application_profile sub-fields (read/written under d.application_profile)

const _PROFILE_TEXT_KEYS = ['first_name','last_name','email','phone','linkedin_url','github_url','portfolio_url','work_permit','current_location'];

const _PROFILE_NUM_KEYS  = ['years_of_experience','salary_expectation'];

const _PROFILE_TOG_KEYS  = ['willing_to_relocate'];

const _PROFILE_SEL_KEYS  = ['salary_currency','notice_period','willing_to_travel'];

const _TAG_KEYS    = ['locations','skip_german_levels'];

let _languages     = [];



function updatePoolCostEstimate() {

  const el   = document.getElementById('cfg-scrape_pool_size');

  const hint = document.getElementById('pool-cost-hint');

  if (!el || !hint) return;

  const pool      = parseInt(el.value) || 25;

  const roles     = (_tagLists['locations'] ? _tagLists['locations'].length : 1);  // use location count as proxy

  const locations = (_cfgData && _cfgData.locations) ? _cfgData.locations.length : 1;

  const rolesCount = (_cfgData && _cfgData.roles) ? _cfgData.roles.length : 1;

  const total = rolesCount * locations * pool;

  const cost  = (total * 0.001).toFixed(2);

  hint.textContent =

    `Higher = more candidates but more Apify cost (~$0.001 per job). ` +

    `With ${rolesCount} role(s) × ${locations} location(s) × ${pool} = ${total} jobs fetched per run ≈ $${cost}`;

}



async function loadConfig() {

  try {

    const r = await fetch('/api/config');

    const d = await r.json();

    if (d.error) throw new Error(d.error);

    _cfgData = d;

    const p = d.application_profile || {};

    // Top-level agent settings

    _TEXT_KEYS.forEach(k   => { const el = document.getElementById('cfg-' + k); if (el) el.value = d[k] || ''; });

    _NUM_KEYS.forEach(k    => { const el = document.getElementById('cfg-' + k); if (el) el.value = d[k] ?? ''; });

    _TOG_KEYS.forEach(k    => { const el = document.getElementById('cfg-' + k); if (el) el.checked = !!d[k]; });

    _SELECT_KEYS.forEach(k => { const el = document.getElementById('cfg-' + k); if (el && d[k] != null) el.value = d[k]; });

    _TAG_KEYS.forEach(k    => { _tagLists[k] = [...(d[k] || [])]; renderTagList(k); });

    const plEl = document.getElementById('cfg-posted_limit');

    if (plEl && d.posted_limit) plEl.value = d.posted_limit;

    // Application profile fields

    _PROFILE_TEXT_KEYS.forEach(k => { const el = document.getElementById('cfg-' + k); if (el) el.value = p[k] || ''; });

    _PROFILE_NUM_KEYS.forEach(k  => { const el = document.getElementById('cfg-' + k); if (el) el.value = p[k] ?? ''; });

    _PROFILE_TOG_KEYS.forEach(k  => { const el = document.getElementById('cfg-' + k); if (el) el.checked = !!p[k]; });

    _PROFILE_SEL_KEYS.forEach(k  => { const el = document.getElementById('cfg-' + k); if (el && p[k] != null) el.value = p[k]; });

    _languages = (p.languages || d.languages || []).map(l => ({language: l.language || '', level: l.level || ''}));

    renderLanguages();

    updatePoolCostEstimate();

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



// EU Locations autocomplete

const _EU_LOCATIONS = [

  // EU Countries

  'Austria', 'Belgium', 'Bulgaria', 'Croatia', 'Cyprus', 'Czech Republic', 'Czechia',

  'Denmark', 'Estonia', 'Finland', 'France', 'Germany', 'Greece', 'Hungary',

  'Ireland', 'Italy', 'Latvia', 'Lithuania', 'Luxembourg', 'Malta', 'Netherlands',

  'Poland', 'Portugal', 'Romania', 'Slovakia', 'Slovenia', 'Spain', 'Sweden',

  // Major EU Cities

  'Amsterdam', 'Athens', 'Barcelona', 'Belfast', 'Berlin', 'Bern', 'Bratislava',

  'Brno', 'Brussels', 'Bucharest', 'Budapest', 'Cardiff', 'Copenhagen', 'Cork',

  'Dublin', 'Dubrovnik', 'Edinburgh', 'Frankfurt', 'Geneva', 'Ghent', 'Granada',

  'Hamburg', 'Helsinki', 'Krakow', 'Lisbon', 'London', 'Luxembourg City', 'Lyon',

  'Madrid', 'Malta', 'Manchester', 'Milan', 'Munich', 'Newcastle', 'Nice', 'Oslo',

  'Prague', 'Rome', 'Rotterdam', 'Salzburg', 'Sofia', 'Stockholm', 'Strasbourg',

  'Stuttgart', 'Tallinn', 'Tel Aviv', 'Turin', 'Valencia', 'Venice', 'Vienna',

  'Vilnius', 'Warsaw', 'Zurich'

];



function showLocationAutocomplete(query) {

  const inp = document.getElementById('tl-input-locations');

  const box = document.getElementById('ac-locations');

  const q = query.toLowerCase().trim();

  if (!q || q.length < 1) {

    box.classList.remove('show');

    return;

  }

  const matches = _EU_LOCATIONS.filter(loc => loc.toLowerCase().includes(q) && !(_tagLists['locations'] || []).includes(loc));

  if (!matches.length) {

    box.classList.remove('show');

    return;

  }

  box.innerHTML = matches.slice(0, 8).map(loc =>

    `<div class="tag-autocomplete-item" onclick="selectLocationAutocomplete('${esc(loc)}')">${esc(loc)}</div>`

  ).join('');

  box.classList.add('show');

}



function selectLocationAutocomplete(loc) {

  const inp = document.getElementById('tl-input-locations');

  const box = document.getElementById('ac-locations');

  inp.value = '';

  box.classList.remove('show');

  if (!(_tagLists['locations'] || []).includes(loc)) {

    (_tagLists['locations'] = _tagLists['locations'] || []).push(loc);

    renderTagList('locations');

  }

}

async function saveConfig() {

  const payload = {..._cfgData};

  // Top-level agent settings

  _TEXT_KEYS.forEach(k   => { const el = document.getElementById('cfg-' + k); if (el) payload[k] = el.value; });

  _NUM_KEYS.forEach(k    => { const el = document.getElementById('cfg-' + k); if (el) payload[k] = el.value !== '' ? Number(el.value) : null; });

  _TOG_KEYS.forEach(k    => { const el = document.getElementById('cfg-' + k); if (el) payload[k] = el.checked; });

  _SELECT_KEYS.forEach(k => { const el = document.getElementById('cfg-' + k); if (el) payload[k] = el.value; });

  _TAG_KEYS.forEach(k    => { payload[k] = _tagLists[k] || []; });

  const plEl = document.getElementById('cfg-posted_limit');

  if (plEl) payload.posted_limit = plEl.value;

  // Application profile — always write as nested object

  const prof = payload.application_profile || {};

  _PROFILE_TEXT_KEYS.forEach(k => { const el = document.getElementById('cfg-' + k); if (el) prof[k] = el.value; });

  _PROFILE_NUM_KEYS.forEach(k  => { const el = document.getElementById('cfg-' + k); if (el) prof[k] = el.value !== '' ? Number(el.value) : null; });

  _PROFILE_TOG_KEYS.forEach(k  => { const el = document.getElementById('cfg-' + k); if (el) prof[k] = el.checked; });

  _PROFILE_SEL_KEYS.forEach(k  => { const el = document.getElementById('cfg-' + k); if (el) prof[k] = el.value; });

  prof.languages = _languages.filter(l => (l.language || '').trim());

  payload.application_profile = prof;

  delete payload.paths; delete payload.contact;

  // Remove legacy top-level duplicates that are now only in application_profile

  delete payload.full_name; delete payload.hotmail_address; delete payload.phone;

  delete payload.earliest_start; delete payload.languages;

  const btn = document.getElementById('btn-save-config');

  btn.disabled = true; btn.textContent = 'Saving…';

  try {

    const r = await fetch('/api/config', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)});

    const d = await r.json();

    d.ok ? showToast('Config saved ✓') : showToast('Save failed: ' + d.error, 'error');

  } catch (err) { showToast('Error: ' + err.message, 'error'); }

  finally { btn.disabled = false; btn.textContent = 'Save Config'; }

}



// ── Language widget ───────────────────────────────────────────────────────────

function renderLanguages() {

  const box = document.getElementById('lang-list');

  if (!box) return;

  if (!_languages.length) { box.innerHTML = ''; return; }

  box.innerHTML = _languages.map((l, i) => `

    <div class="lang-row">

      <input type="text" placeholder="Language (e.g. English)" value="${esc(l.language||'')}"

             oninput="_languages[${i}].language=this.value">

      <input type="text" placeholder="Level (e.g. Fluent, B1)" value="${esc(l.level||'')}"

             oninput="_languages[${i}].level=this.value" style="max-width:130px;">

      <button class="lang-rm" onclick="_languages.splice(${i},1);renderLanguages()" title="Remove">&#215;</button>

    </div>`).join('');

}



function addLanguage() {

  _languages.push({language:'', level:''});

  renderLanguages();

  const inputs = document.querySelectorAll('#lang-list .lang-row:last-child input');

  if (inputs[0]) inputs[0].focus();

}



// ── Support documents (slot-based) ────────────────────────────────────────────

const _SUPPORT_SLOTS = ['cover_letter','reference_1','reference_2','reference_3',

                        'photo','german_cert','university_doc','other'];



async function loadSupportDocs() {

  try {

    const r = await fetch('/api/upload/support');

    const d = await r.json();

    renderSupportDocs(d.slots || {});

  } catch (_) {}

}



function renderSupportDocs(slots) {

  const box = document.getElementById('sdoc-list');

  if (!box) return;

  box.innerHTML = Object.entries(slots).map(([key, info]) => `

    <div class="sdoc-row">

      <span class="sdoc-label">&#128196; ${esc(info.label)}</span>

      ${info.exists

        ? `<span class="sdoc-status uploaded">&#10003; ${esc(info.filename)}</span>

           <button class="btn-sm" style="padding:2px 8px;font-size:0.75rem;"

                   onclick="removeSlot('${esc(key)}')">&#215;</button>`

        : `<span class="sdoc-status">—</span>

           <button class="btn-sm" style="padding:2px 8px;font-size:0.75rem;"

                   onclick="triggerSlotUpload('${esc(key)}')">Upload</button>`

      }

      <input type="file" id="sdoc-input-${esc(key)}" accept=".pdf,.docx,.doc" class="hidden"

             onchange="uploadSlot('${esc(key)}', this)">

    </div>`).join('');

}



function triggerSlotUpload(slot) {

  const el = document.getElementById('sdoc-input-' + slot);

  if (el) el.click();

}



async function uploadSlot(slot, input) {

  if (!input.files.length) return;

  const fd = new FormData();

  fd.append('file', input.files[0]);

  showToast('Uploading…');

  try {

    const r = await fetch('/api/upload/support/' + encodeURIComponent(slot), {method:'POST', body:fd});

    const d = await r.json();

    if (d.error) throw new Error(d.error);

    showToast(d.filename + ' uploaded');

    await loadSupportDocs();

  } catch(err) { showToast(err.message, 'error'); }

  input.value = '';

}



async function removeSlot(slot) {

  try {

    const r = await fetch('/api/upload/support/' + encodeURIComponent(slot), {method:'DELETE'});

    const d = await r.json();

    if (d.error) throw new Error(d.error);

    showToast('Removed');

    await loadSupportDocs();

  } catch(err) { showToast(err.message, 'error'); }

}



// ── LinkedIn connection ───────────────────────────────────────────────────────

let _liPollTimer = null;



async function checkLinkedInSession() {

  try {

    const r = await fetch('/api/linkedin/session_status');

    const d = await r.json();

    const box = document.getElementById('li-session-status');

    if (d.connected) {

      box.style.background = '#0d2e1e';

      box.style.borderColor = '#2d6a4f';

      box.style.color = '#4caf82';

      box.textContent = '✓ LinkedIn connected — session saved ' + d.saved_at + ' (no login needed for future runs)';

    } else {

      box.style.background = '#1c2128';

      box.style.borderColor = '#30363d';

      box.style.color = '#8b949e';

      box.textContent = 'Not connected — click "Connect LinkedIn" to authenticate';

    }

  } catch (_) {}

}



async function startLinkedInLogin() {

  const email    = document.getElementById('li-email').value.trim();

  const password = document.getElementById('li-password').value;

  const btn      = document.getElementById('btn-li-connect');

  const cancel   = document.getElementById('btn-li-cancel');

  const msg      = document.getElementById('li-login-msg');



  if (!email || !password) { showToast('Enter LinkedIn email and password first.', 'error'); return; }



  // Save credentials to .env

  try {

    await fetch('/api/linkedin/set_credentials', {

      method: 'POST',

      headers: {'Content-Type':'application/json'},

      body: JSON.stringify({email, password}),

    });

  } catch (_) {}



  btn.disabled = true;

  cancel.style.display = 'inline-flex';

  msg.style.color = '#58a6ff';

  msg.textContent = 'Browser window opening…';



  try {

    const r = await fetch('/api/linkedin/manual_login', {method: 'POST'});

    const d = await r.json();

    if (d.error) throw new Error(d.error);

  } catch (err) {

    msg.style.color = '#f87171';

    msg.textContent = 'Error: ' + err.message;

    btn.disabled = false;

    cancel.style.display = 'none';

    return;

  }



  // Poll login status every 3 s until done or error

  if (_liPollTimer) clearInterval(_liPollTimer);

  _liPollTimer = setInterval(async () => {

    try {

      const r = await fetch('/api/linkedin/login_status');

      const d = await r.json();

      if (d.status === 'done') {

        clearInterval(_liPollTimer); _liPollTimer = null;

        btn.disabled = false;

        cancel.style.display = 'none';

        msg.style.color = '#4caf82';

        msg.textContent = '✓ Connected — session saved, no login needed for future runs';

        checkLinkedInSession();

      } else if (d.status === 'error') {

        clearInterval(_liPollTimer); _liPollTimer = null;

        btn.disabled = false;

        cancel.style.display = 'none';

        msg.style.color = '#f87171';

        msg.textContent = 'Connection failed: ' + d.message;

      } else if (d.message) {

        msg.style.color = '#58a6ff';

        msg.textContent = d.message;

      }

    } catch (_) {}

  }, 3000);

}



async function cancelLinkedInLogin() {

  try { await fetch('/api/linkedin/cancel_login', {method: 'POST'}); } catch (_) {}

  document.getElementById('btn-li-cancel').style.display = 'none';

  document.getElementById('btn-li-connect').disabled = false;

  const msg = document.getElementById('li-login-msg');

  msg.style.color = '#8b949e';

  msg.textContent = 'Cancelled.';

  if (_liPollTimer) { clearInterval(_liPollTimer); _liPollTimer = null; }

}



// ── Live log ──────────────────────────────────────────────────────────────────

let _logES = null;

function _logStatusEl() { return document.getElementById('log-status'); }

function _setLogStatus(text, color) {

  const el = _logStatusEl();

  if (el) { el.textContent = text; el.style.color = color; }

}

function startLogStream() {

  if (_logES) { _logES.close(); _logES = null; }

  const box = document.getElementById('log-viewer');

  _setLogStatus('● connecting…', '#64748b');

  _logES = new EventSource('/api/log/stream');

  _logES.onopen = function() {

    _setLogStatus('● connected', '#4ade80');

  };

  _logES.onmessage = function(e) {

    _setLogStatus('● connected', '#4ade80');

    const line = JSON.parse(e.data);

    const div  = document.createElement('div');

    div.className = 'log-line ' + logClass(line);

    div.textContent = line;

    box.appendChild(div);

    while (box.children.length > 500) box.children[0].remove();

    if (box.scrollHeight - box.scrollTop - box.clientHeight < 80) box.scrollTop = box.scrollHeight;

  };

  _logES.onerror = function() {

    _setLogStatus('● disconnected — reconnecting…', '#f87171');

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

    _autoEvents = d.auto_events || [];

    renderEvents(_autoEvents);

    renderTasks(d.priority_tasks || []);

    renderManualInterviews();

  } catch (err) { console.error('Overview fetch error:', err.message); }

}



function _eventBadge(status) {

  const sl = (status || '').toLowerCase();

  if (sl.includes('call'))      return '<span class="badge bs-call">'  + esc(status) + '</span>';

  if (sl.includes('technical')) return '<span class="badge bs-tech">'  + esc(status) + '</span>';

  if (sl.includes('final'))     return '<span class="badge bs-final">' + esc(status) + '</span>';

  return '<span class="badge bs-scheduled">' + esc(status) + '</span>';

}



function _evTypeBadge(t) {
  const map = {
    interview:      ['#0d2818','#34d399','Interview'],
    code_challenge: ['#2d1a06','#fb923c','Code Challenge'],
    call:           ['#0c2a4a','#60a5fa','Call'],
    meeting:        ['#1a1a2e','#818cf8','Meeting'],
    task:           ['#2d2006','#fbbf24','Task'],
  };
  const [bg, fg, label] = map[(t||'').toLowerCase()] || ['#1c2128','#64748b', t || 'Event'];
  return `<span style="font-size:0.72rem;background:${bg};color:${fg};padding:2px 8px;border-radius:99px;font-weight:600;">${esc(label)}</span>`;
}

function renderEvents(items) {

  const box = document.getElementById('ov-events');

  if (!box) return;

  // Show future events first; past events still shown but dimmed
  const today = new Date().toISOString().slice(0, 10);

  const sorted = [...items].sort((a, b) => {
    const ad = a.event_date || '9999', bd = b.event_date || '9999';
    return ad < bd ? -1 : ad > bd ? 1 : 0;
  });

  if (!sorted.length) { box.innerHTML = '<div class="ov-empty">No upcoming events.</div>'; return; }

  box.innerHTML = sorted.map(e => {
    const isPast = e.event_date && e.event_date < today;
    const timeStr = e.event_time ? ` · ${esc(e.event_time)}` : '';
    return `
    <div class="ov-item" style="${isPast ? 'opacity:0.5;' : ''}">
      <div class="ov-item-main">
        <div class="ov-company">${esc(e.company || '—')}</div>
        <div class="ov-role" style="font-size:0.78rem;color:#8b949e;">${esc(e.title || e.role || '—')}</div>
        <div style="margin-top:5px;">${_evTypeBadge(e.event_type)}</div>
      </div>
      <div class="ov-item-right">
        <div class="ov-date">${esc(_normDate(e.event_date) + timeStr)}</div>
        ${isPast ? '<div style="font-size:0.7rem;color:#64748b;">past</div>' : ''}
      </div>
    </div>`;
  }).join('');

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

  if (e.target.classList.contains('modal-backdrop')) {

    if (e.target.id === 'modal-confirm') {

      document.getElementById('confirm-no')?.click();

    } else {

      closeModal(e.target.id);

    }

  }

});

document.addEventListener('keydown', e => {

  if (e.key === 'Escape') {

    ['modal-interview','modal-task','modal-event-edit'].forEach(id => {

      const el = document.getElementById(id);

      if (el && !el.classList.contains('hidden')) closeModal(id);

    });

    const ivOv = document.getElementById('iv-notes-overlay');
    if (ivOv && ivOv.style.display === 'flex') ivOv.style.display = 'none';

    // Confirm modal: pressing Escape is same as Cancel
    const cm = document.getElementById('modal-confirm');

    if (cm && !cm.classList.contains('hidden')) {

      document.getElementById('confirm-no')?.click();

    }

  }

});



// ── Manual Interviews ─────────────────────────────────────────────────────────

let _interviews  = [];
let _autoEvents  = [];



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

  // Convert auto (email-extracted) events to the same shape as manual interviews
  const autoRows = _autoEvents.map(e => ({
    _auto:          true,
    _event_id:      e.id,
    date:           e.event_date  || '',
    company:        e.company     || '',
    role:           e.role        || '',
    interview_type: (e.event_type || '').replace(/_/g, ' '),
    time_berlin:    e.event_time  || '',
    _description:   e.description || '',
    job_url:        null,
  }));

  // Merge: manual entries first (they have edit/delete buttons), then auto
  // Deduplicate by (date + company) so a manually-added row hides the auto one
  const manualKeys = new Set(_interviews.map(iv => (iv.date||'') + '|' + (iv.company||'').toLowerCase()));
  const filtered   = autoRows.filter(r => !manualKeys.has((r.date||'') + '|' + r.company.toLowerCase()));
  const combined   = [..._interviews, ...filtered];

  // Sort: future dates first (ascending), then past (descending)
  const today = new Date().toISOString().slice(0, 10);
  const future = combined.filter(r => !r.date || r.date >= today).sort((a,b) => (a.date||'').localeCompare(b.date||''));
  const past   = combined.filter(r => r.date && r.date < today).sort((a,b) => b.date.localeCompare(a.date));
  const rows   = [...future, ...past];

  if (!rows.length) {
    tbody.innerHTML = '';
    if (empty) empty.classList.remove('hidden');
    return;
  }

  if (empty) empty.classList.add('hidden');

  tbody.innerHTML = rows.map(iv => {

    const isPast = _isPast(iv.date);

    const companyCell = iv.job_url
      ? `<a href="${esc(iv.job_url)}" target="_blank" rel="noopener" style="color:#58a6ff;">${esc(iv.company||'')}</a>`
      : `<span style="font-weight:600;">${esc(iv.company||'')}</span>`;

    const actionBtn = iv._auto
      ? `<button class="btn-icon" title="Edit event" onclick="openEventEdit(null,${iv._event_id})">&#9998;</button>`
      : `<button class="btn-icon" title="Edit" onclick="openInterviewModal(${iv.id})">&#9998;</button>
         <button class="btn-icon del" title="Delete" onclick="deleteInterview(${iv.id})">&#215;</button>`;

    const typeLabel = `<span style="font-size:0.72rem;background:#0d2818;color:#34d399;padding:2px 8px;border-radius:99px;">${esc((iv.interview_type||'—').replace(/_/g,' '))}</span>`;

    // Notes: for auto events use description; for manual use format/notes
    const notesRaw  = iv._auto ? (iv._description || '') : (iv.format || iv.notes || '');
    const header    = [iv.company, iv.role].filter(Boolean).join(' — ');
    const notesCell = notesRaw
      ? `<td style="font-size:0.78rem;color:#8b949e;max-width:320px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;cursor:pointer;border-bottom:1px dotted #444;"
             title="Click to expand"
             onclick="ivShowNotes(${JSON.stringify(header)},${JSON.stringify(notesRaw)})">${esc(notesRaw)}</td>`
      : `<td style="color:#4b5563;">—</td>`;

    return `<tr class="${isPast ? 'past-row' : ''}" id="iv-row-${iv._auto ? 'a'+iv._event_id : iv.id}">
      <td style="white-space:nowrap;font-weight:${isPast?'400':'600'};color:${isPast?'#8b949e':'#e2e8f0'};">${esc(_normDate(iv.date))}</td>
      <td>${companyCell}</td>
      <td style="color:#94a3b8;">${esc(iv.role||'')}</td>
      <td>${typeLabel}</td>
      <td style="white-space:nowrap;color:#8b949e;">${esc(iv.time_berlin||'')}</td>
      ${notesCell}
      <td><div class="man-row-btns">${actionBtn}</div></td>
    </tr>`;

  }).join('');

}



function ivShowNotes(header, body) {
  document.getElementById('iv-notes-header').textContent = header;
  document.getElementById('iv-notes-body').textContent   = body;
  const ov = document.getElementById('iv-notes-overlay');
  ov.style.display = 'flex';
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

  if (!await showConfirm('Delete this interview?')) return;

  try {

    const r = await fetch('/api/interviews/upcoming/' + id, {method:'DELETE'});

    const d = await r.json();

    if (d.error) { showToast(d.error, 'error'); return; }

    showToast('Interview deleted');

    await fetchManualInterviews();

  } catch (err) { showToast(err.message, 'error'); }

}



// ── Interviews sub-tab enrichment ─────────────────────────────────────────────

let _eventsCache = [];



async function enrichInterviewsTab() {

  const rows = _tableData['interviews'];

  if (!rows) return;

  try {

    const r = await fetch('/email-admin/api/events');

    const data = await r.json();

    _eventsCache = Array.isArray(data) ? data : (data.events || []);

  } catch (_) {

    _eventsCache = [];

  }

  // Build lookup: app_id → first scheduled event for that app

  const lookup = {};

  _eventsCache.forEach(ev => {

    if (ev.app_id != null && !lookup[ev.app_id]) lookup[ev.app_id] = ev;

  });

  _tableData['interviews'] = rows.map(row => {

    const ev = lookup[row.id] || {};

    return Object.assign({}, row, {

      _iv_event_id: ev.id           || null,

      _iv_date:     ev.event_date   || '',

      _iv_type:     ev.event_type   || '',

      _iv_time:     ev.event_time   || '',

      _iv_format:   '',

      _iv_notes:    ev.description  || '',

    });

  });

  renderTable('interviews');

}



// ── Interview event edit / reject ──────────────────────────────────────────────

function openEventEdit(appId, eventId) {

  const ev = eventId ? (_eventsCache.find(e => e.id === eventId) || {}) : {};

  document.getElementById('ev-app-id').value   = appId;

  document.getElementById('ev-event-id').value = eventId || '';

  document.getElementById('ev-title').value    = ev.title       || '';

  document.getElementById('ev-date').value     = ev.event_date  || '';

  document.getElementById('ev-time').value     = ev.event_time  || '';

  document.getElementById('ev-tz').value       = ev.timezone    || '';

  document.getElementById('ev-priority').value = ev.priority    || 'high';

  document.getElementById('ev-desc').value     = ev.description || '';

  document.getElementById('modal-event-edit').classList.remove('hidden');

}



async function saveEventEdit() {

  const appId   = parseInt(document.getElementById('ev-app-id').value, 10);

  const eventId = document.getElementById('ev-event-id').value;

  const payload = {

    app_id:      appId,

    title:       document.getElementById('ev-title').value.trim(),

    event_date:  document.getElementById('ev-date').value,

    event_time:  document.getElementById('ev-time').value,

    timezone:    document.getElementById('ev-tz').value.trim(),

    priority:    document.getElementById('ev-priority').value,

    description: document.getElementById('ev-desc').value.trim(),

  };

  const url    = eventId ? '/email-admin/api/events/' + eventId : '/email-admin/api/events';

  const method = eventId ? 'PUT' : 'POST';

  try {

    const r = await fetch(url, {

      method,

      headers: {'Content-Type': 'application/json'},

      body: JSON.stringify(payload),

    });

    const d = await r.json();

    if (!d.ok) throw new Error(d.error || 'Save failed');

    closeModal('modal-event-edit');

    showToast('Interview details saved \\u2713');

    await enrichInterviewsTab();

  } catch (err) { showToast(err.message, 'error'); }

}



async function rejectInterview(appId) {

  if (!await showConfirm('Mark this application as Rejected and cancel any scheduled events?')) return;

  try {

    const r = await fetch('/api/applications/' + appId + '/reject', {method: 'POST'});

    const d = await r.json();

    if (!d.ok) throw new Error(d.error || 'Reject failed');

    showToast('Marked as Rejected \\u2713');

    await fetchDashboard();

  } catch (err) { showToast(err.message, 'error'); }

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

  if (!await showConfirm('Delete this task?')) return;

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

  <!-- ═══ EMAIL ADMIN TAB ═══ -->

  <div id="tab-email-admin" class="tab-content hidden">
    <div id="email-admin-mount"></div>
  </div>

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

        from dedup import db as _db

        _db.init_db()

        return jsonify(_db.get_dashboard_data())

    except Exception as exc:

        return jsonify({"error": str(exc), "overview": {}, "Applied": [],

                        "Interviews": [], "Rejected": [], "import_done": False})





# ── Upcoming interviews (manual CRUD) ────────────────────────────────────────



@app.route("/api/interviews/upcoming", methods=["GET"])

def api_interviews_get():

    try:

        from dedup import db as _db

        _db.init_db()

        return jsonify({"interviews": _db.get_upcoming_interviews()})

    except Exception as exc:

        return jsonify({"error": str(exc)}), 500





@app.route("/api/interviews/upcoming", methods=["POST"])

def api_interviews_post():

    try:

        data = request.get_json(force=True) or {}

        from dedup import db as _db

        _db.init_db()

        new_id = _db.add_interview(data)

        return jsonify({"ok": True, "id": new_id})

    except Exception as exc:

        return jsonify({"error": str(exc)}), 500





@app.route("/api/interviews/upcoming/<int:interview_id>", methods=["PUT"])

def api_interviews_put(interview_id: int):

    try:

        data = request.get_json(force=True) or {}

        from dedup import db as _db

        _db.init_db()

        _db.update_interview(interview_id, data)

        return jsonify({"ok": True})

    except Exception as exc:

        return jsonify({"error": str(exc)}), 500





@app.route("/api/interviews/upcoming/<int:interview_id>", methods=["DELETE"])

def api_interviews_delete(interview_id: int):

    try:

        from dedup import db as _db

        _db.init_db()

        _db.delete_interview(interview_id)

        return jsonify({"ok": True})

    except Exception as exc:

        return jsonify({"error": str(exc)}), 500





# ── Priority tasks (manual CRUD) ──────────────────────────────────────────────



@app.route("/api/tasks", methods=["GET"])

def api_tasks_get():

    try:

        from dedup import db as _db

        _db.init_db()

        return jsonify({"tasks": _db.get_priority_tasks_manual()})

    except Exception as exc:

        return jsonify({"error": str(exc)}), 500





@app.route("/api/tasks", methods=["POST"])

def api_tasks_post():

    try:

        data = request.get_json(force=True) or {}

        from dedup import db as _db

        _db.init_db()

        new_id = _db.add_task_manual(data)

        return jsonify({"ok": True, "id": new_id})

    except Exception as exc:

        return jsonify({"error": str(exc)}), 500





@app.route("/api/tasks/<int:task_id>", methods=["PUT"])

def api_tasks_put(task_id: int):

    try:

        data = request.get_json(force=True) or {}

        from dedup import db as _db

        _db.init_db()

        _db.update_task_manual(task_id, data)

        return jsonify({"ok": True})

    except Exception as exc:

        return jsonify({"error": str(exc)}), 500





@app.route("/api/tasks/<int:task_id>", methods=["DELETE"])

def api_tasks_delete(task_id: int):

    try:

        from dedup import db as _db

        _db.init_db()

        _db.delete_task_manual(task_id)

        return jsonify({"ok": True})

    except Exception as exc:

        return jsonify({"error": str(exc)}), 500





# ── Overview enriched ─────────────────────────────────────────────────────────



@app.route("/api/overview")

def api_overview():

    try:

        from dedup import db as _db

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

        from dedup import db as _db

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





@app.route("/api/applications/<int:app_id>/reject", methods=["POST"])

def api_reject_application(app_id: int):

    try:

        from dedup import db as _db

        _db.init_db()

        with _db._conn() as conn:

            row = conn.execute(

                "SELECT id FROM applications WHERE id=?", (app_id,)

            ).fetchone()

            if not row:

                return jsonify({"error": "Application not found"}), 404

            conn.execute(

                "UPDATE applications SET status='Rejected' WHERE id=?", (app_id,)

            )

        _db.cancel_app_events(app_id)

        return jsonify({"ok": True})

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

        from dedup import db as _db

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

        from dedup import db as _db

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

        # Deep-merge application_profile so partial saves never wipe existing values

        incoming_profile = data.pop("application_profile", None)

        existing.update(data)

        if isinstance(incoming_profile, dict):

            base = existing.get("application_profile") or {}

            # Only write fields that have a non-empty value; keep existing otherwise

            for k, v in incoming_profile.items():

                if v is not None and v != "" and v != []:

                    base[k] = v

                elif k not in base:

                    base[k] = v

            existing["application_profile"] = base

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

        from scraper import scraper as scraper_module

        roles = scraper_module.extract_roles_from_resume(cfg)

        return jsonify({"roles": roles})

    except Exception as exc:

        return jsonify({"error": str(exc), "roles": []}), 200





# ── File uploads (resume + cover letter only) ─────────────────────────────────



_ALLOWED_DOC    = {".pdf", ".docx"}
_ALLOWED_RESUME = {".docx"}  # tailoring edits the docx XML directly — a PDF can't be tailored
_RESUME_DIR     = _UPLOADS_DIR / "resume"




def _save_upload(file_storage, stem: str, allowed: set[str],
                  dest_dir: "Path | None" = None) -> "tuple[str, str]":

    ext = Path(file_storage.filename).suffix.lower()

    if ext not in allowed:

        raise ValueError(f"File type {ext} not allowed (expected {allowed})")

    target_dir = dest_dir or _UPLOADS_DIR

    target_dir.mkdir(parents=True, exist_ok=True)

    for old in target_dir.glob(f"{stem}.*"):

        old.unlink(missing_ok=True)

    filename = f"{stem}{ext}"

    dest = target_dir / filename

    file_storage.save(str(dest))

    return filename, str(dest)





@app.route("/api/upload/resume", methods=["POST"])

def api_upload_resume():

    f = request.files.get("file")

    if not f:

        return jsonify({"error": "No file"}), 400

    try:

        filename, _ = _save_upload(f, "resume", _ALLOWED_RESUME, dest_dir=_RESUME_DIR)

        from scraper import scraper as _scraper

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

        filename, _ = _save_upload(f, "cover_letter", _ALLOWED_DOC, dest_dir=_RESUME_DIR)

        return jsonify({"ok": True, "filename": filename})

    except Exception as exc:

        return jsonify({"error": str(exc)}), 400





@app.route("/api/upload/status")

def api_upload_status():

    result = {}

    _resume_dir = _UPLOADS_DIR / "resume"

    for stem, key in [("resume", "resume"), ("cover_letter", "cover_letter")]:

        found = next(_resume_dir.glob(f"{stem}.*"), None) if _resume_dir.exists() else None

        result[key] = {"exists": found is not None, "filename": found.name if found else None}

    return jsonify(result)





# ── Support documents upload (slot-based) ─────────────────────────────────────



_SUPPORT_DIR = _UPLOADS_DIR / "support"

_ALLOWED_SUPPORT = {".pdf", ".docx", ".doc"}

_SUPPORT_SLOTS = {

    "cover_letter":   "Cover Letter",

    "reference_1":    "Reference Letter 1",

    "reference_2":    "Reference Letter 2",

    "reference_3":    "Reference Letter 3",

    "photo":          "Personal Photo",

    "german_cert":    "German Language Certificate",

    "university_doc": "University Document",

    "other":          "Other",

}





def _slot_file(slot: str) -> Path | None:

    """Return the existing file for a slot (any allowed extension), or None."""

    if slot not in _SUPPORT_SLOTS:

        return None

    for ext in _ALLOWED_SUPPORT:

        p = _SUPPORT_DIR / (slot + ext)

        if p.exists():

            return p

    return None





@app.route("/api/upload/support", methods=["GET"])

def api_upload_support_list():

    _SUPPORT_DIR.mkdir(parents=True, exist_ok=True)

    slots = {}

    for key, label in _SUPPORT_SLOTS.items():

        f = _slot_file(key)

        slots[key] = {

            "label":    label,

            "exists":   f is not None,

            "filename": f.name if f else None,

        }

    return jsonify({"slots": slots})





@app.route("/api/upload/support/<slot>", methods=["POST"])

def api_upload_support_slot(slot: str):

    if slot not in _SUPPORT_SLOTS:

        return jsonify({"error": "Unknown slot"}), 400

    f = request.files.get("file")

    if not f:

        return jsonify({"error": "No file"}), 400

    ext = Path(f.filename).suffix.lower()

    if ext not in _ALLOWED_SUPPORT:

        return jsonify({"error": "File type not allowed"}), 400

    _SUPPORT_DIR.mkdir(parents=True, exist_ok=True)

    # Remove any previously uploaded file for this slot

    old = _slot_file(slot)

    if old:

        old.unlink(missing_ok=True)

    dest = _SUPPORT_DIR / (slot + ext)

    f.save(str(dest))

    return jsonify({"ok": True, "filename": dest.name})





@app.route("/api/upload/support/<slot>", methods=["DELETE"])

def api_upload_support_slot_delete(slot: str):

    if slot not in _SUPPORT_SLOTS:

        return jsonify({"error": "Unknown slot"}), 400

    f = _slot_file(slot)

    if not f:

        return jsonify({"error": "No file for this slot"}), 404

    f.unlink()

    return jsonify({"ok": True})





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

        _setup_logging(load_config())

        from core import main as _main

        jobs = _main.run_scrape_only()

        _pipeline_set(step=2, jobs_count=len(jobs))

    except Exception as exc:

        log.exception("Scrape worker crashed: %s", exc)

        _pipeline_set(step=-1, error=str(exc))





def _match_worker(job_ids=None):

    _pipeline_set(step=3, error=None)

    try:

        _setup_logging(load_config())

        from core import main as _main

        from dedup import db as _db

        jobs = _main.run_match_only(job_ids=job_ids)

        # Auto-dismiss jobs scored below 50 — not worth seeing ever again
        _db.init_db()
        purged = _db.purge_low_score_jobs(min_score=50)
        if purged:
            log.info("Auto-dismissed %d jobs with match_score < 50", purged)
            # Drop purged jobs from the in-memory result list too
            jobs = [j for j in jobs if (j.get("match_score") or 0) >= 50]

        # Sync matched_jobs scores back into seen_jobs so audit and cache stats stay consistent

        with _db._conn() as db:

            rows = db.execute("""

                SELECT sj.url, mj.match_score, mj.interview_chance,

                       mj.skip_reason, mj.german_level

                FROM matched_jobs mj

                JOIN scraped_jobs sj ON sj.id = mj.scraped_job_id

            """).fetchall()

            db.executemany("""

                UPDATE seen_jobs

                SET match_score=?, interview_chance=?, skip_reason=?,

                    german_level_required=?

                WHERE url=?

            """, [(r[1], r[2], r[3], r[4], r[0]) for r in rows])

        _pipeline_set(step=4, matched_count=len(jobs))

    except Exception as exc:

        log.exception("Match worker crashed: %s", exc)

        _pipeline_set(step=-1, error=str(exc))





def _apply_worker(job_urls):

    _pipeline_set(step=5, error=None)

    try:

        _setup_logging(load_config())

        from core import main as _main

        results = _main.run_apply_only(job_urls=job_urls)

        _pipeline_set(step=6, results=results)

    except Exception as exc:

        log.exception("Apply worker crashed: %s", exc)

        _pipeline_set(step=-1, error=str(exc))





@app.route("/api/pipeline/scrape", methods=["POST"])

def api_pipeline_scrape():

    if not _run_in_thread(_scrape_worker):

        return jsonify({"error": "Pipeline already running"}), 409

    return jsonify({"ok": True})





@app.route("/api/pipeline/match", methods=["POST"])

def api_pipeline_match():

    body = request.get_json(force=True, silent=True) or {}

    job_ids = body.get("job_ids") or None

    if not _run_in_thread(_match_worker, job_ids):

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





@app.route("/api/pipeline/reset", methods=["POST"])

def api_pipeline_reset():

    """Force-clear all pipeline/apply/agent run state.

    The scrape/match/apply workers run as background threads that Python
    cannot forcibly kill, and each one only checks its stop signal at a
    few checkpoints. If a worker is stuck (or its stop signal never reached
    it), the thread reference stays "alive" forever and every subsequent
    /api/pipeline/* or /api/apply/* call 409s with "already running". This
    endpoint is the manual escape hatch: it best-effort signals every
    worker to stop, then forgets the stale thread/process references so
    new runs are accepted immediately regardless of what the old one does.
    """

    global _pipeline_thread, _agent_proc, _apply_thread

    try:

        from scraper import scraper as _scraper

        _scraper.stop_scraping()

    except Exception as exc:

        log.warning("pipeline reset: failed to signal scraper stop: %s", exc)

    _apply_stop_flag.set()

    with _agent_lock:

        if _agent_proc and _agent_proc.poll() is None:

            _agent_proc.terminate()

            try:

                _agent_proc.wait(timeout=5)

            except subprocess.TimeoutExpired:

                _agent_proc.kill()

        _agent_proc = None

    with _pipeline_lock:

        _pipeline_thread = None

        _pipeline.update(step=0, jobs_count=0, matched_count=0, error=None, results=[])

    with _apply_lock:

        _apply_thread = None

    return jsonify({"ok": True})





@app.route("/api/pipeline/step_availability")

def api_pipeline_step_availability():

    """Return which wizard steps have data available to navigate to."""

    try:

        from dedup import db as _db

        _db.init_db()

        with _db._conn() as db:

            has_scraped = db.execute("SELECT 1 FROM scraped_jobs LIMIT 1").fetchone() is not None

            has_matched = db.execute("SELECT 1 FROM matched_jobs LIMIT 1").fetchone() is not None

            has_applied = db.execute(

                "SELECT 1 FROM applications WHERE date_applied IS NOT NULL LIMIT 1"

            ).fetchone() is not None

            has_apply_session = db.execute(

                "SELECT 1 FROM apply_sessions LIMIT 1"

            ).fetchone() is not None

        return jsonify({

            "step1": True,

            "step2": has_scraped,

            "step3": has_matched,

            "step4": has_matched,

            "step5": has_apply_session,

        })

    except Exception:

        return jsonify({"step1": True, "step2": False, "step3": False, "step4": False, "step5": False})





@app.route("/api/pipeline/scrape_progress")

def api_pipeline_scrape_progress():

    from scraper import scraper as _scraper

    return jsonify(_scraper.get_scrape_progress())





@app.route("/api/pipeline/match_progress")

def api_pipeline_match_progress():

    try:

        from dedup import db as _db

        _db.init_db()

        cfg = load_config()

        min_score = int(cfg.get("min_match_score", 70))

        with _db._conn() as db:

            total   = db.execute("SELECT COUNT(*) FROM scraped_jobs").fetchone()[0]

            scored  = db.execute("SELECT COUNT(*) FROM matched_jobs").fetchone()[0]

            from matcher import matcher as _matcher_mod

            _run_stats = _matcher_mod.get_match_stats()

            if _run_stats.get("total") is not None:

                total  = _run_stats["total"]

                scored = _run_stats.get("fresh", 0) + _run_stats.get("cache_hits", 0)

            passed  = db.execute(

                "SELECT COUNT(*) FROM matched_jobs WHERE match_score >= ?"

                " AND (skip_reason IS NULL OR skip_reason = '')",

                (min_score,)

            ).fetchone()[0]

            last    = db.execute("""

                SELECT s.title, s.company FROM matched_jobs m

                JOIN scraped_jobs s ON s.id = m.scraped_job_id

                ORDER BY m.id DESC LIMIT 1

            """).fetchone()

        current_job = f"{last[0]} @ {last[1]}" if last else ""

        return jsonify({

            "total":           total,

            "scored":          scored,

            "remaining":       max(0, total - scored),

            "current_job":     current_job,

            "fresh":           _run_stats.get("fresh", 0),

            "cache_hits":      _run_stats.get("cache_hits", 0),

            "resume_changed":  _run_stats.get("resume_changed", False),

            "passed":          passed,

            "below_threshold": max(0, scored - passed),

            "min_score":       min_score,

        })

    except Exception:

        return jsonify({

            "total": 0, "scored": 0, "remaining": 0, "current_job": "",

            "fresh": 0, "cache_hits": 0, "resume_changed": False,

            "passed": 0, "below_threshold": 0, "min_score": 70,

        })





@app.route("/api/pipeline/scraped_jobs")

def api_pipeline_scraped_jobs():

    try:

        from dedup import db as _db

        _db.init_db()

        with _db._conn() as db:

            rows = db.execute(

                "SELECT id, title, company, location, url, source,"

                "       posted_date, cache_status, scraped_at, has_easy_apply"

                " FROM scraped_jobs"

                " ORDER BY"

                "   CASE WHEN posted_date != '' AND posted_date IS NOT NULL"

                "        THEN posted_date ELSE scraped_at END DESC"

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

        from dedup import db as _db

        _db.init_db()

        with _db._conn() as db:

            db.execute("DELETE FROM scraped_jobs WHERE id = ?", (job_id,))

        return jsonify({"ok": True})

    except Exception as exc:

        return jsonify({"error": str(exc)}), 500





@app.route("/api/jobs/dismiss", methods=["POST"])

def api_jobs_dismiss():

    url = (request.get_json(silent=True) or {}).get("url", "").strip()

    if not url:

        return jsonify({"error": "url required"}), 400

    try:

        from dedup import db as _db

        _db.init_db()

        _db.purge_job_by_url(url, reason="dismissed by user")

        return jsonify({"ok": True})

    except Exception as exc:

        return jsonify({"error": str(exc)}), 500





@app.route("/api/jobs/<int:job_id>/dismiss", methods=["POST"])

def api_jobs_dismiss_by_id(job_id):

    try:

        from dedup import db as _db

        _db.init_db()

        with _db._conn() as db:

            # job_id comes from scraped_jobs.id (the Select/Match step tables)
            row = db.execute("SELECT url FROM scraped_jobs WHERE id=?", (job_id,)).fetchone()

            if not row:

                # Fallback: cache manager uses seen_jobs.id
                row = db.execute("SELECT url FROM seen_jobs WHERE id=?", (job_id,)).fetchone()

        url = (row["url"] if row else None)

        if url:

            _db.purge_job_by_url(url, reason="dismissed by user")

        else:

            _db.dismiss_job_by_id(job_id)   # last resort: mark seen_jobs by id

        return jsonify({"ok": True})

    except Exception as exc:

        return jsonify({"error": str(exc)}), 500





@app.route("/api/jobs/<int:job_id>/undismiss", methods=["POST"])

def api_jobs_undismiss_by_id(job_id):

    try:

        from dedup import db as _db

        _db.init_db()

        _db.undismiss_job_by_id(job_id)

        return jsonify({"ok": True})

    except Exception as exc:

        return jsonify({"error": str(exc)}), 500





@app.route("/api/cache/stats")

def api_cache_stats():

    try:

        from dedup import db as _db

        _db.init_db()

        cfg = load_config()

        stats  = _db.get_cache_stats()

        wstats = _db.get_cache_window_stats(cfg)

        return jsonify({**stats, **wstats})

    except Exception as exc:

        return jsonify({"error": str(exc)}), 500





@app.route("/api/matcher/results")

def api_matcher_results():

    """Return ALL scored jobs from the current run (above AND below threshold)."""

    try:

        from dedup import db as _db

        _db.init_db()

        with _db._conn() as db:

            rows = db.execute("""

                SELECT s.id AS scraped_id, s.title, s.company, s.location,

                       s.url, s.source, s.description, s.has_easy_apply,

                       m.match_score, m.interview_chance, m.skip_reason,

                       m.german_level AS german_level_required, m.match_summary

                FROM matched_jobs m

                JOIN scraped_jobs s ON s.id = m.scraped_job_id

                WHERE s.url NOT IN (

                    SELECT url FROM excluded_jobs WHERE url IS NOT NULL AND url != ''

                )

                AND s.url NOT IN (

                    SELECT url FROM seen_jobs WHERE dismissed = 1 AND url IS NOT NULL

                )

                ORDER BY m.match_score DESC

            """).fetchall()

            # Fallback: if scraped_jobs was cleared, look in seen_jobs for recent scores

            if not rows:

                rows = db.execute("""

                    SELECT url, title, company, location, source, description,

                           match_score, interview_chance, skip_reason,

                           german_level_required, match_summary

                    FROM seen_jobs

                    WHERE match_score IS NOT NULL AND match_score > 0

                      AND dismissed = 0

                      AND url NOT IN (

                          SELECT url FROM excluded_jobs WHERE url IS NOT NULL AND url != ''

                      )

                    ORDER BY match_score DESC

                    LIMIT 200

                """).fetchall()

        jobs = [dict(r) for r in rows]

        return jsonify({"has_results": len(jobs) > 0, "jobs": jobs})

    except Exception as exc:

        return jsonify({"has_results": False, "jobs": [], "error": str(exc)})





@app.route("/api/pipeline/matched_jobs")

def api_pipeline_matched_jobs():

    try:

        from dedup import db as _db

        _db.init_db()

        with _db._conn() as db:

            rows = db.execute("""

                SELECT s.id AS scraped_id, s.title, s.company, s.location, s.url, s.source,

                       s.description, s.has_easy_apply,

                       m.match_score, m.interview_chance, m.german_level AS german_level_required,

                       m.match_summary, m.skip_reason

                FROM matched_jobs m

                JOIN scraped_jobs s ON s.id = m.scraped_job_id

                WHERE s.url NOT IN (

                    SELECT url FROM excluded_jobs WHERE url IS NOT NULL AND url != ''

                )

                AND s.url NOT IN (

                    SELECT url FROM seen_jobs WHERE dismissed = 1 AND url IS NOT NULL

                )

                ORDER BY m.match_score DESC

            """).fetchall()

        return jsonify({"jobs": [dict(r) for r in rows]})

    except Exception:

        p = _UPLOADS_DIR / "matched_jobs.json"

        if not p.exists():

            return jsonify({"jobs": []})

        return jsonify({"jobs": json.loads(p.read_text(encoding="utf-8"))})





@app.route("/api/matcher/audit")

def api_matcher_audit():

    try:

        from dedup import db as _db

        _db.init_db()

        cfg = load_config()

        min_score = int(cfg.get("min_match_score", 70))

        with _db._conn() as db:

            rows = db.execute("""

                SELECT s.title, s.company, s.url,

                       m.match_score, m.interview_chance,

                       m.german_level AS german_level_required, m.skip_reason

                FROM matched_jobs m

                JOIN scraped_jobs s ON s.id = m.scraped_job_id

                ORDER BY m.match_score DESC

            """).fetchall()

        passed, failed_score, failed_german, failed_stack = [], [], [], []

        for r in rows:

            j    = dict(r)

            skip = (j.get("skip_reason") or "").lower()

            sc   = j.get("match_score") or 0

            if "german level" in skip:

                failed_german.append(j)

            elif "stack" in skip or "language" in skip:

                failed_stack.append(j)

            elif sc >= min_score and not skip:

                passed.append(j)

            else:

                failed_score.append(j)

        total = len(passed) + len(failed_score) + len(failed_german) + len(failed_stack)

        return jsonify({

            "summary": {

                "total_scored":    total,

                "min_match_score": min_score,

                "passed":          len(passed),

                "failed_score":    len(failed_score),

                "failed_german":   len(failed_german),

                "failed_stack":    len(failed_stack),

            },

            "breakdown": {

                "passed":        passed,

                "failed_score":  failed_score,

                "failed_german": failed_german,

                "failed_stack":  failed_stack,

            },

        })

    except Exception as exc:

        return jsonify({"error": str(exc)}), 500





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

    # Signal scraper to stop immediately (kills Apify actor)

    try:

        from scraper import scraper

        scraper.stop_scraping()

        log.info("Scraper stop signal sent")

    except Exception as exc:

        log.warning("Failed to signal scraper stop: %s", exc)



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





# ── LinkedIn session ──────────────────────────────────────────────────────────



_SESSION_FILE      = _UPLOADS_DIR / "linkedin_session.json"

_LI_LOGIN_LOCK     = threading.Lock()

_li_login_thread: "threading.Thread | None" = None

_li_login_state: dict  = {"status": "idle", "message": ""}   # idle | running | done | error

_li_cancel_requested   = False





def _linkedin_login_worker():

    """Open visible Chromium for LinkedIn 2FA; keep browser open until success or cancel."""

    import asyncio as _asyncio

    from playwright.async_api import async_playwright as _apw



    async def _do_login():

        global _li_cancel_requested

        _li_cancel_requested = False



        email    = os.getenv("LINKEDIN_EMAIL", "")

        password = os.getenv("LINKEDIN_PASSWORD", "")

        if not email or not password:

            _li_login_state.update(status="error",

                                   message="LINKEDIN_EMAIL / LINKEDIN_PASSWORD not set in .env")

            return



        async with _apw() as pw:

            browser = await pw.chromium.launch(channel="chrome", headless=False)

            context = await browser.new_context(

                user_agent=(

                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "

                    "AppleWebKit/537.36 (KHTML, like Gecko) "

                    "Chrome/124.0.0.0 Safari/537.36"

                ),

                viewport={"width": 1280, "height": 800},

            )

            page = await context.new_page()

            logged_in = False

            try:

                await page.goto("https://www.linkedin.com/login", timeout=30_000)

                # Fill credentials if the login form is present
                try:
                    await page.wait_for_selector("#username", timeout=5000)
                    await page.fill("#username", email)
                    await page.fill("#password", password)
                    await page.click("button[type='submit']")
                    _li_login_state["message"] = (
                        "Credentials submitted — approve the LinkedIn notification or complete any verification in the browser…"
                    )
                except Exception:
                    # Challenge page or already logged in — let user handle it
                    _li_login_state["message"] = (
                        "LinkedIn requires manual verification — please complete it in the browser window"
                    )



                # Poll every 3 s; show 10-minute countdown but keep browser open after

                deadline = time.time() + 600

                while True:

                    if _li_cancel_requested:

                        _li_login_state.update(status="error", message="Cancelled by user")

                        return  # finally closes browser



                    try:

                        current_url = page.url

                    except Exception:

                        _li_login_state.update(status="error",

                                               message="Browser was closed — run again to retry")

                        return



                    if "feed" in current_url or "mynetwork" in current_url or "jobs" in current_url:

                        logged_in = True

                        break



                    remaining = max(0, int(deadline - time.time()))

                    if remaining > 0:

                        m, s = divmod(remaining, 60)

                        _li_login_state["message"] = (

                            f"Browser open — waiting for login approval ({m}:{s:02d} remaining)"

                        )

                    else:

                        _li_login_state["message"] = (

                            "Browser still open — log in or click Cancel to close"

                        )



                    await _asyncio.sleep(3)



                if logged_in:

                    _UPLOADS_DIR.mkdir(exist_ok=True)

                    await context.storage_state(path=str(_SESSION_FILE))

                    _li_login_state.update(status="done",

                                           message="✓ Session saved — connected")

            except Exception as _exc:

                _li_login_state.update(status="error", message=f"Error: {_exc}")

            finally:

                if logged_in or _li_cancel_requested:

                    try:

                        await browser.close()

                    except Exception:

                        pass

                # On timeout: browser stays open — user closes it manually



    _asyncio.run(_do_login())





@app.route("/api/linkedin/manual_login", methods=["POST"])

def api_linkedin_manual_login():

    global _li_login_thread

    with _LI_LOGIN_LOCK:

        if _li_login_thread and _li_login_thread.is_alive():

            return jsonify({"error": "Login already in progress"}), 409

        _li_login_state.update(status="running",

                               message="Browser opening — fill in credentials and approve 2FA")

        _li_login_thread = threading.Thread(target=_linkedin_login_worker, daemon=True)

        _li_login_thread.start()

    return jsonify({"ok": True, "message": "Login started — approve the notification on your phone"})





@app.route("/api/linkedin/login_status")

def api_linkedin_login_status():

    return jsonify(dict(_li_login_state))





@app.route("/api/linkedin/session_status")

def api_linkedin_session_status():

    if _SESSION_FILE.exists():

        saved_at = time.strftime(

            "%Y-%m-%d %H:%M:%S",

            time.localtime(_SESSION_FILE.stat().st_mtime),

        )

        return jsonify({"connected": True, "saved_at": saved_at})

    return jsonify({"connected": False, "saved_at": None})





@app.route("/api/linkedin/set_credentials", methods=["POST"])

def api_linkedin_set_credentials():

    data = request.get_json(silent=True) or {}

    email    = (data.get("email") or "").strip()

    password = (data.get("password") or "").strip()



    env_path = _PROJECT_ROOT / ".env"

    if not env_path.exists():

        env_path.touch()



    try:

        if email:

            dotenv_set_key(str(env_path), "LINKEDIN_EMAIL", email)

        if password:

            dotenv_set_key(str(env_path), "LINKEDIN_PASSWORD", password)

        # Reload env vars in this process so the login worker picks them up

        load_dotenv(str(env_path), override=True)

        return jsonify({"ok": True})

    except Exception as exc:

        return jsonify({"error": str(exc)}), 500





@app.route("/api/linkedin/cancel_login", methods=["POST"])

def api_linkedin_cancel_login():

    global _li_cancel_requested

    _li_cancel_requested = True

    return jsonify({"ok": True})





@app.route("/api/linkedin/credentials")

def api_linkedin_credentials():

    return jsonify({"email": os.getenv("LINKEDIN_EMAIL", "")})





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





# ── Apply tab endpoints ───────────────────────────────────────────────────────



_apply_stop_flag: threading.Event = threading.Event()

_apply_thread:    "threading.Thread | None" = None

_apply_lock       = threading.Lock()





def _apply_tab_worker(jobs: list[dict]) -> None:

    global _apply_stop_flag

    try:

        import applier

        cfg = load_config()

        _setup_logging(cfg)

        applier.run(jobs, cfg, _apply_stop_flag)

    except Exception as exc:

        log.exception("Apply-tab worker crashed: %s", exc)

        import applier

        applier._emit("session_done", {"success": 0, "manual": 0, "failed": len(jobs),

                                       "error": str(exc)})





# ── SSE apply-progress stream ─────────────────────────────────────────────────

@app.route("/api/apply/stream")

def api_apply_stream():

    """Server-Sent Events stream for live apply progress."""

    import applier as _applier

    import queue as _queue



    def generate():

        while True:

            try:

                evt = _applier._event_queue.get(timeout=15)

                yield "data: " + json.dumps(evt) + _SSE_SEP

                if evt.get("type") == "session_done":

                    break

            except _queue.Empty:

                yield ": keepalive" + _SSE_SEP



    return Response(generate(), mimetype="text/event-stream",

                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})





# ── Manual queue endpoint ──────────────────────────────────────────────────────

@app.route("/api/apply/manual_queue")

def api_apply_manual_queue():

    """Return jobs in the manual apply queue."""

    try:

        from dedup import db as _db

        all_sessions = request.args.get("all_sessions", "false").lower() == "true"

        session_id = None if all_sessions else _db.get_latest_manual_session_id()

        items = _db.get_manual_queue(session_id=session_id)

        return jsonify({"items": items})

    except Exception as exc:

        return jsonify({"items": [], "error": str(exc)})





@app.route("/api/apply/skip", methods=["POST"])

def api_apply_skip():

    """Mark a job URL to be skipped in the current apply session."""

    url = (request.get_json(silent=True) or {}).get("url", "").strip()

    if not url:

        return jsonify({"error": "url required"}), 400

    from applier.applier import skip_url

    skip_url(url)

    return jsonify({"ok": True})



@app.route("/api/apply/stop", methods=["POST"])

def api_apply_stop():

    """Signal the running apply session to stop before its next job."""

    _apply_stop_flag.set()

    return jsonify({"ok": True})



@app.route("/api/apply/debug", methods=["POST"])

def api_apply_debug():

    """Test a single job application directly."""

    global _apply_thread, _apply_stop_flag

    with _apply_lock:

        if _apply_thread and _apply_thread.is_alive():

            return jsonify({"error": "Apply session already running"}), 409

        body    = request.get_json(force=True) or {}

        url     = (body.get("url") or "").strip()

        title   = (body.get("title") or "").strip()

        company = (body.get("company") or "").strip()



        if not url:

            return jsonify({"error": "url is required"}), 400



        # Create a job dict from the provided info

        job = {

            "url": url,

            "title": title or "Unknown",

            "company": company or "Unknown",

            "description": ""

        }



        _apply_stop_flag = threading.Event()

        _apply_thread = threading.Thread(

            target=_apply_tab_worker, args=([job],), daemon=True

        )

        _apply_thread.start()

        return jsonify({"ok": True, "message": f"Apply started for {url}"})





@app.route("/api/apply/start", methods=["POST"])

def api_apply_start():

    """Start applying to multiple jobs by their IDs."""

    global _apply_thread, _apply_stop_flag

    with _apply_lock:

        if _apply_thread and _apply_thread.is_alive():

            return jsonify({"error": "Apply session already running"}), 409

        body       = request.get_json(force=True) or {}

        job_ids    = body.get("job_ids") or []  # list of job.id ints

        tailor_ids = set(body.get("tailor_ids") or [])  # subset to generate a tailored resume for



        if not job_ids:

            return jsonify({"error": "job_ids is required"}), 400



        # Convert IDs to integers and validate

        try:

            job_ids = [int(jid) for jid in job_ids]

        except (ValueError, TypeError):

            return jsonify({"error": "job_ids must be a list of integers"}), 400



        # Fetch jobs directly by scraped_id (same source as Step 4 table)

        try:

            from dedup import db as dedup_db

            dedup_db.init_db()

            id_set = set(job_ids)

            placeholders = ",".join("?" * len(id_set))

            with dedup_db._conn() as db:

                rows = db.execute(f"""

                    SELECT s.id AS scraped_id, s.title, s.company, s.location,

                           s.url, s.source, s.description,

                           m.match_score, m.interview_chance,

                           m.german_level AS german_level_required, m.match_summary

                    FROM matched_jobs m

                    JOIN scraped_jobs s ON s.id = m.scraped_job_id

                    WHERE s.id IN ({placeholders})

                """, list(id_set)).fetchall()

            jobs = [dict(r) for r in rows]

            # Normalise field names expected by applier

            for j in jobs:

                j["id"] = j["scraped_id"]

                j["_tailor_requested"] = j["scraped_id"] in tailor_ids

            if not jobs:

                return jsonify({"error": "No jobs found with given IDs"}), 404

        except Exception as e:

            return jsonify({"error": f"Failed to fetch jobs: {str(e)}"}), 500



        # Start apply session

        _apply_stop_flag = threading.Event()

        _apply_thread = threading.Thread(

            target=_apply_tab_worker, args=(jobs,), daemon=True

        )

        _apply_thread.start()

        return jsonify({"ok": True, "total": len(jobs), "jobs": jobs, "message": f"Apply started for {len(jobs)} jobs"})





@app.route("/api/applications/manual", methods=["POST"])

def api_applications_manual():

    """Handle I Applied / Not Interested actions from the manual queue."""

    try:

        from dedup import db as _db

        body     = request.get_json(force=True) or {}

        entry_id = body.get("id")

        job_url  = body.get("job_url", "")

        title    = body.get("title", "")

        company  = body.get("company", "")

        platform = body.get("platform", "")

        action   = body.get("action", "")



        if action == "applied":

            job = {

                "url":          job_url,

                "title":        title,

                "company":      company,

                "platform":     platform,

                "apply_method": "manual",

            }

            _db.log_application(job, archive_path="", status="Applied",

                                apply_method="manual")

            if entry_id:

                _db.update_manual_queue_status(entry_id, "applied")

            return jsonify({"ok": True})

        elif action in ("exclude", "not_interested"):

            _db.exclude_job(job_url, company=company, title=title,

                            reason="Not interested")

            if entry_id:

                _db.update_manual_queue_status(entry_id, "excluded")

            return jsonify({"ok": True})

        else:

            return jsonify({"error": f"Unknown action: {action}"}), 400

    except Exception as exc:

        return jsonify({"error": str(exc)}), 500









if __name__ == "__main__":
    import logging as _logging
    _logging.getLogger("werkzeug").setLevel(_logging.ERROR)
    app.run(host="127.0.0.1", port=5000, debug=False)

