from pathlib import Path

applier = Path("applier/applier.py")
content = applier.read_text(encoding="utf-8")

OLD = "        return await fill_easy_apply(page, job, profile, resume_text, cfg)"

NEW = (
    "        _easy_result = await fill_easy_apply(page, job, profile, resume_text, cfg)\n"
    "        # If form filling failed, check if job is actually expired\n"
    "        if _easy_result.get(\"manual\"):\n"
    "            try:\n"
    "                _page_text2 = (await page.inner_text(\"body\")).lower()\n"
    "                _exp2 = [\n"
    "                    \"no longer accepting applications\",\n"
    "                    \"unable to load the page\",\n"
    "                    \"job posting has been removed\",\n"
    "                    \"this job is no longer available\",\n"
    "                    \"no longer available\",\n"
    "                    \"job has expired\",\n"
    "                ]\n"
    "                if any(p in _page_text2 for p in _exp2):\n"
    "                    log.info(\"Job expired (form failed + expired text): %s\", job.get(\"url\"))\n"
    "                    _emit(\"job_expired\", {\"url\": job.get(\"url\", \"\"),\n"
    "                                          \"title\": job.get(\"title\", \"\"),\n"
    "                                          \"reason\": \"No longer accepting applications\"})\n"
    "                    return {\"success\": False, \"expired\": True,\n"
    "                            \"note\": \"No longer accepting applications\"}\n"
    "            except Exception:\n"
    "                pass\n"
    "        return _easy_result"
)

if OLD in content:
    content = content.replace(OLD, NEW, 1)
    applier.write_text(content, encoding="utf-8")
    print("✅ Added expired check after fill_easy_apply fails")
else:
    print("⚠️  Line not found exactly")

import subprocess
r = subprocess.run(["python", "-m", "py_compile", "applier/applier.py"], capture_output=True, text=True)
print("✅ Syntax OK" if r.returncode == 0 else f"❌ {r.stderr}")
