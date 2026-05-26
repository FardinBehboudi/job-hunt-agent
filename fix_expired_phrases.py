from pathlib import Path

applier = Path("applier/applier.py")
content = applier.read_text(encoding="utf-8")

EXTRA = (
    '                "unable to load the page",\n'
    '                "job id provided may not be valid",\n'
    '                "job posting has been removed",\n'
    '                "page not found",\n'
    '                "this job is no longer",\n'
)

# Add to both _expired_phrases lists
OLD_PHRASE = '"no longer accepting applications",\n'
count = content.count(OLD_PHRASE)
new_content = content.replace(
    OLD_PHRASE,
    OLD_PHRASE + EXTRA,
)
# Only replace first two occurrences (the two lists)
applier.write_text(new_content, encoding="utf-8")

import subprocess
r = subprocess.run(["python", "-m", "py_compile", "applier/applier.py"], capture_output=True, text=True)
print(f"✅ Added LinkedIn 'Unable to load' phrases to {count} detection point(s)")
print("✅ Syntax OK" if r.returncode == 0 else f"❌ {r.stderr}")
