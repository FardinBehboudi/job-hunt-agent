from pathlib import Path

applier = Path("applier/applier.py")
content = applier.read_text(encoding="utf-8")

OLD = (
    '        if outcome == "not_found":\n'
    '            _emit("apply_step", {"url": job.get("url", ""), "step": "\u2019\ufe0f Going to manual: No Apply button found"})\n'
    '            return {"success": False, "manual": True, "note": "No Apply button found"}'
)

NEW = (
    '        if outcome == "not_found":\n'
    '            # Check if the job is expired before sending to manual queue\n'
    '            try:\n'
    '                _page_text = (await page.inner_text("body")).lower()\n'
    '                _expired_phrases = [\n'
    '                    "no longer accepting applications",\n'
    '                    "this job is no longer available",\n'
    '                    "job is closed", "position has been filled",\n'
    '                    "this posting has expired", "no longer available",\n'
    '                    "job has expired", "application period has ended",\n'
    '                ]\n'
    '                if any(p in _page_text for p in _expired_phrases):\n'
    '                    log.info("Job expired (no apply button + expired text): %s", job.get("url"))\n'
    '                    _emit("job_expired", {"url": job.get("url", ""), "title": job.get("title", ""),\n'
    '                                          "reason": "No longer accepting applications"})\n'
    '                    return {"success": False, "expired": True, "note": "No longer accepting applications"}\n'
    '            except Exception:\n'
    '                pass\n'
    '            _emit("apply_step", {"url": job.get("url", ""), "step": "\u2019\ufe0f Going to manual: No Apply button found"})\n'
    '            return {"success": False, "manual": True, "note": "No Apply button found"}'
)

if 'if outcome == "not_found":' in content:
    # Try exact match first
    if OLD in content:
        content = content.replace(OLD, NEW, 1)
        print("✅ Fixed: expired check added to not_found path (exact match)")
    else:
        # Fallback: find the not_found block and patch it with regex
        import re
        pattern = re.compile(
            r'(        if outcome == "not_found":\n'
            r'.*?return \{"success": False, "manual": True, "note": "No Apply button found"\})',
            re.DOTALL
        )
        m = pattern.search(content)
        if m:
            content = content[:m.start()] + NEW + content[m.end():]
            print("✅ Fixed: expired check added to not_found path (regex match)")
        else:
            print("⚠️  Could not patch automatically — add manually:")
            print('   After `if outcome == "not_found":` and before the _emit/return,')
            print('   check page text for expired phrases and emit job_expired if found.')
else:
    print("⚠️  not_found block not found in applier.py")

applier.write_text(content, encoding="utf-8")

import subprocess
r = subprocess.run(["python", "-m", "py_compile", "applier/applier.py"], capture_output=True, text=True)
print("✅ Syntax OK" if r.returncode == 0 else f"❌ {r.stderr}")
