from pathlib import Path

dash = Path("dashboard/dashboard.py")
content = dash.read_text(encoding="utf-8")

OLD = "    renderApplyCards();\n    connectApplyStream();"
NEW = "    renderApplyCards();\n    document.getElementById('apply-log-body').innerHTML = '';\n    connectApplyStream();"

if OLD in content:
    content = content.replace(OLD, NEW, 1)
    dash.write_text(content, encoding="utf-8")
    print("✅ Apply log now clears at the start of each session")
else:
    print("⚠️  Pattern not found")
