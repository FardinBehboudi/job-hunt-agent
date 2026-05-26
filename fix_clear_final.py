from pathlib import Path
dash = Path("dashboard/dashboard.py")
content = dash.read_text(encoding="utf-8")

OLD = """async function clearManualQueue() {
  if (!_mqItems.length) return;
  try {
    await Promise.all(_mqItems.map(item =>
      fetch('/api/applications/manual', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({
          id: item.id, job_url: item.job_url, title: item.title,
          company: item.company, platform: item.platform, action: 'skip',
        }),
      })
    ));
  } catch(_) {}
  _mqItems = [];
  await loadManualQueue();
  showToast('Manual queue cleared', 'info');
}"""

NEW = """async function clearManualQueue() {
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
}"""

if OLD in content:
    content = content.replace(OLD, NEW, 1)
    dash.write_text(content, encoding="utf-8")
    print("✅ Fixed clearManualQueue")
else:
    print("⚠️  Not found — trying line-based replace")
    lines = content.split('\n')
    # Find the function start
    start = next((i for i, l in enumerate(lines) if 'async function clearManualQueue()' in l), None)
    if start is not None:
        # Find closing brace
        end = next((i for i, l in enumerate(lines[start:], start) if l.strip() == '}'), None)
        if end:
            lines[start:end+1] = NEW.split('\n')
            dash.write_text('\n'.join(lines), encoding="utf-8")
            print("✅ Fixed via line replace")
        else:
            print("⚠️  Could not find function end")
    else:
        print("⚠️  Function not found at all")

import subprocess
r = subprocess.run(["python", "-m", "py_compile", "dashboard/dashboard.py"], capture_output=True, text=True)
print("✅ Syntax OK" if r.returncode == 0 else f"❌ {r.stderr}")
