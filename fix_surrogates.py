from pathlib import Path

dash = Path("dashboard/dashboard.py")

# Read raw bytes, find and fix bad UTF-8 sequences
raw = dash.read_bytes()

# The emoji 📄 encodes to F0 9F 93 84 in UTF-8
# But if written as surrogates it becomes invalid - just strip any invalid UTF-8
text = raw.decode("utf-8", errors="replace")

# Remove Unicode replacement characters (U+FFFD) left from invalid bytes
text = text.replace("\ufffd", "")

# Also clean up any remaining JS surrogate escape sequences written as literal text
import re
text = re.sub(r"\\ud83d\\udcc4", "", text)   # literal \ud83d\udcc4 in JS
text = re.sub(r"'📄 ' \+", "", text)         # if emoji somehow got in

dash.write_text(text, encoding="utf-8")

import subprocess
r = subprocess.run(["python", "-m", "py_compile", "dashboard/dashboard.py"],
                   capture_output=True, text=True)
if r.returncode == 0:
    print("✅ Syntax OK")
else:
    print(f"❌ Syntax error: {r.stderr}")

try:
    dash.read_text(encoding="utf-8").encode("utf-8")
    print("✅ UTF-8 encode OK — dashboard will load")
except UnicodeEncodeError as e:
    print(f"❌ Bad char at position {e.start}")
    t = dash.read_text(encoding="utf-8")
    print(f"   Context: {repr(t[max(0,e.start-30):e.start+30])}")
