from pathlib import Path
import re

sf = Path("applier/smart_filler.py")
sc = sf.read_text(encoding="utf-8")

# Find and remove the broken content around line 357
# The broken part has the unterminated string
lines = sc.split("\n")
bad_start = None
bad_end = None
for i, line in enumerate(lines):
    if "'selector': '...'" in line or '"selector": "..."' in line or "selector.*text.*{" in line:
        bad_start = i - 5
    if bad_start and i > bad_start and "return False" in line:
        bad_end = i
        break

if bad_start and bad_end:
    print(f"Found bad block at lines {bad_start}-{bad_end}")
    # Remove bad block, keep everything else
    lines = lines[:bad_start] + ["    return False"] + lines[bad_end+1:]
    sc = "\n".join(lines)
    sf.write_text(sc, encoding="utf-8")
    print("Removed bad block")

# Now find the _find_and_click_submit function and replace it cleanly
CLEAN_FUNC = '''async def _find_and_click_submit(page) -> bool:
    """Try multiple strategies to find and click a submit button."""
    strategies = [
        page.locator("button[type=submit]"),
        page.locator("input[type=submit]"),
        page.locator("button:has-text('Submit')"),
        page.locator("button:has-text('Apply')"),
        page.locator("button:has-text('Send application')"),
        page.locator("button:has-text('Complete application')"),
        page.locator("button:has-text('Jetzt bewerben')"),
        page.locator("button:has-text('Bewerbung absenden')"),
        page.locator("button:has-text('Bewerben')"),
        page.locator("button:has-text('Ja, das passt zu mir')"),
        page.locator("button:has-text('Bewerbung senden')"),
        page.locator("a:has-text('Bewerben')"),
        page.locator("a:has-text('Apply now')"),
        page.locator("[data-test-submit-button]"),
        page.locator("[class*=submit]"),
    ]
    for loc in strategies:
        try:
            if await loc.count() and await loc.first.is_visible():
                await loc.first.scroll_into_view_if_needed()
                await asyncio.sleep(0.4)
                await loc.first.click()
                return True
        except Exception:
            pass

    # Claude vision fallback — ask Claude which button to click
    try:
        import base64
        import json as _json
        client = _get_client()
        if client:
            b64 = base64.b64encode(await page.screenshot()).decode()
            resp = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=100,
                messages=[{"role": "user", "content": [
                    {"type": "image", "source": {
                        "type": "base64", "media_type": "image/png", "data": b64}},
                    {"type": "text", "text":
                        "Find the apply or submit button on this page. "
                        "Return JSON only with the exact button text: "
                        '{"text": "button text here"} or {} if none found.'}
                ]}]
            )
            raw = resp.content[0].text.strip()
            s, e = raw.find("{"), raw.rfind("}") + 1
            if s >= 0 and e > s:
                data = _json.loads(raw[s:e])
                btn_text = data.get("text", "")
                if btn_text:
                    loc = page.locator(
                        f"button:has-text('{btn_text}'), "
                        f"a:has-text('{btn_text}')"
                    )
                    if await loc.count() and await loc.first.is_visible():
                        await loc.first.scroll_into_view_if_needed()
                        await asyncio.sleep(0.3)
                        await loc.first.click()
                        return True
    except Exception:
        pass
    return False'''

# Replace the entire function
sc = sf.read_text(encoding="utf-8")
pattern = re.compile(
    r'async def _find_and_click_submit\(page\).*?(?=^async def |\Z)',
    re.DOTALL | re.MULTILINE
)
m = pattern.search(sc)
if m:
    sc = sc[:m.start()] + CLEAN_FUNC + "\n\n" + sc[m.end():]
    sf.write_text(sc, encoding="utf-8")
    print("✅ _find_and_click_submit replaced cleanly")
else:
    print("⚠️ function not found by regex")

import subprocess
r = subprocess.run(["python", "-m", "py_compile", "applier/smart_filler.py"],
                   capture_output=True, text=True)
print("✅ Syntax OK" if r.returncode == 0 else f"❌ {r.stderr}")
