from pathlib import Path

dash = Path("dashboard/dashboard.py")
content = dash.read_text(encoding="utf-8")

OLD = (
    "async function clearManualQueue() {\n"
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
    "}"
)

NEW = (
    "async function clearManualQueue() {\n"
    "  if (!_mqItems.length) return;\n"
    "  try {\n"
    "    await Promise.all(_mqItems.map(item =>\n"
    "      fetch('/api/applications/manual', {\n"
    "        method: 'POST',\n"
    "        headers: {'Content-Type':'application/json'},\n"
    "        body: JSON.stringify({\n"
    "          id: item.id, job_url: item.job_url || item.url,\n"
    "          title: item.title, company: item.company,\n"
    "          platform: item.platform, action: 'exclude',\n"
    "        }),\n"
    "      })\n"
    "    ));\n"
    "  } catch(_) {}\n"
    "  _mqItems = [];\n"
    "  const empty = document.getElementById('manual-queue-empty');\n"
    "  const table = document.getElementById('manual-queue-table');\n"
    "  if (empty) empty.style.display = '';\n"
    "  if (table) table.style.display = 'none';\n"
    "  showToast('Manual queue cleared', 'info');\n"
    "}"
)

if "async function clearManualQueue()" in content:
    content = content.replace(OLD, NEW, 1)
    if OLD not in content:  # was replaced
        print("✅ Fixed clearManualQueue: no confirm, correct action, immediate UI clear")
    else:
        # Try simpler approach - just patch the two problems
        content = content.replace(
            "  if (!confirm('Clear all items from the manual queue? This cannot be undone.')) return;\n",
            "", 1
        )
        content = content.replace("action: 'skip',", "action: 'exclude',", 1)
        content = content.replace(
            "  _mqItems = [];\n  await loadManualQueue();",
            "  _mqItems = [];\n"
            "  const empty = document.getElementById('manual-queue-empty');\n"
            "  const table = document.getElementById('manual-queue-table');\n"
            "  if (empty) empty.style.display = '';\n"
            "  if (table) table.style.display = 'none';",
            1
        )
        print("✅ Fixed via partial patches")
else:
    print("⚠️  clearManualQueue not found")

dash.write_text(content, encoding="utf-8")

import subprocess
r = subprocess.run(["python", "-m", "py_compile", "dashboard/dashboard.py"], capture_output=True, text=True)
print("✅ Syntax OK" if r.returncode == 0 else f"❌ {r.stderr}")
