"""
Removes chrome_extension_bridge import and all /api/extension/* routes
from dashboard/dashboard.py.

Run from project root:
    python fix_remove_bridge.py
"""
from pathlib import Path

dash = Path("dashboard/dashboard.py")
content = dash.read_text(encoding="utf-8")

# Remove import
content = content.replace("from chrome_extension_bridge import get_bridge\n", "", 1)

# Remove all 4 extension routes + the bridge section comment
BRIDGE_SECTION = '''# ═══════════════════════════════════════════════════════════════════════════════
# ═══ CLAUDE IN CHROME EXTENSION BRIDGE ═══════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════════════════
'''
content = content.replace(BRIDGE_SECTION, "", 1)

# Remove each route by finding from @app.route to next @app.route or if __name__
import re

# Remove all /api/extension/* routes
content = re.sub(
    r'@app\.route\("/api/extension/[^"]+\".*?\n(?:def \w+\(\):.*?)(?=@app\.route|if __name__)',
    '',
    content,
    flags=re.DOTALL
)

dash.write_text(content, encoding="utf-8")

# Verify
final = dash.read_text(encoding="utf-8")
print("Import removed:", "chrome_extension_bridge" not in final)
print("get_bridge removed:", "get_bridge" not in final)
print("Extension routes removed:", "/api/extension/" not in final)

import subprocess
result = subprocess.run(["python", "-m", "py_compile", "dashboard/dashboard.py"], 
                       capture_output=True, text=True)
if result.returncode == 0:
    print("✅ Syntax OK — dashboard.py is clean")
else:
    print("❌ Syntax error:", result.stderr)
