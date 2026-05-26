from pathlib import Path
import re

dash = Path("dashboard/dashboard.py")
content = dash.read_text(encoding="utf-8")

# 1. Monospace font
OLD_CSS = ".apply-log-body { flex:1; overflow-y:auto; padding:10px 14px; }"
NEW_CSS = ".apply-log-body { flex:1; overflow-y:auto; padding:10px 14px; font-family:'Courier New',monospace; font-size:0.76rem; line-height:1.6; }"
if OLD_CSS in content:
    content = content.replace(OLD_CSS, NEW_CSS, 1)
    print("✅ CSS: monospace log font")

# 2. Replace the entire if/else chain + the append line using regex
# Match from "let line = '';" to "log.textContent += line + ...;"
pattern = re.compile(
    r"(      let line = '';.*?log\.textContent \+= line \+ .+?;)",
    re.DOTALL
)

NEW_BLOCK = r"""      let line = '', color = '#94a3b8', weight = '400', bg = '';
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
          line = '\ud83d\udcc4 ' + evt.step; color = '#34d399';
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
      }"""

match = pattern.search(content)
if match:
    content = content[:match.start()] + NEW_BLOCK + content[match.end():]
    print("✅ Log: colored lines applied")
else:
    print("⚠️  Regex pattern not found")

dash.write_text(content, encoding="utf-8")

import subprocess
r = subprocess.run(["python", "-m", "py_compile", "dashboard/dashboard.py"], capture_output=True, text=True)
print("✅ Syntax OK" if r.returncode == 0 else f"❌ {r.stderr}")
