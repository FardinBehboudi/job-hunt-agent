from pathlib import Path

dash = Path("dashboard/dashboard.py")
content = dash.read_text(encoding="utf-8")

# 1. Add Clear button next to Refresh in the HTML
OLD_BTN = '                  <button class="btn-sm" onclick="loadManualQueue()">Refresh</button>'
NEW_BTN = ('                  <button class="btn-sm" onclick="loadManualQueue()">Refresh</button>\n'
           '                  <button class="btn-sm" style="color:#f87171;" onclick="clearManualQueue()">&#10005; Clear All</button>')

if OLD_BTN in content:
    content = content.replace(OLD_BTN, NEW_BTN, 1)
    print("✅ HTML: Added Clear All button")
else:
    print("⚠️  Refresh button not found")

# 2. Add clearManualQueue() JS function after loadManualQueue()
OLD_ANCHOR = "async function mqAction(idx, action, btn) {"
NEW_ANCHOR = ("async function clearManualQueue() {\n"
              "  if (!_mqItems.length) return;\n"
              "  if (!confirm('Clear all items from the manual queue? This cannot be undone.')) return;\n"
              "  try {\n"
              "    await Promise.all(_mqItems.map(item =>\n"
              "      fetch('/api/applications/manual', {\n"
              "        method: 'POST',\n"
              "        headers: {'Content-Type':'application/json'},\n"
              "        body: JSON.stringify({\n"
              "          id: item.id, job_url: item.job_url, title: item.title,\n"
              "          company: item.company, platform: item.platform, action: 'skip',\n"
              "        }),\n"
              "      })\n"
              "    ));\n"
              "  } catch(_) {}\n"
              "  _mqItems = [];\n"
              "  await loadManualQueue();\n"
              "  showToast('Manual queue cleared', 'info');\n"
              "}\n"
              "\n"
              "async function mqAction(idx, action, btn) {")

if "async function mqAction(idx, action, btn) {" in content:
    content = content.replace(OLD_ANCHOR, NEW_ANCHOR, 1)
    print("✅ JS: Added clearManualQueue() function")
else:
    print("⚠️  mqAction not found")

dash.write_text(content, encoding="utf-8")

import subprocess
r = subprocess.run(["python", "-m", "py_compile", "dashboard/dashboard.py"], capture_output=True, text=True)
print("✅ Syntax OK" if r.returncode == 0 else f"❌ {r.stderr}")
