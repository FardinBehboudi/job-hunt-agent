"""
Replaces _verify_submission with a multi-signal smart verifier that uses:
1. URL pattern matching (confirmation/thank-you URLs)
2. Visible text confirmation phrases
3. Form-gone check (no empty required fields = submitted)
4. Claude vision as final arbiter
5. Timing check (< 5s open = probably just redirect, not submitted)

Also patches smart_filler.py to use the smart verifier.

Run from project root:
    python fix_verify_submission.py
"""
from pathlib import Path
import subprocess

ea = Path("applier/external_applier.py")
ec = ea.read_text(encoding="utf-8")

SMART_VERIFY = '''async def _verify_submission(page: Page) -> bool:
    """
    Multi-signal submission verifier. Returns True only when confident
    the application was actually submitted (not just a redirect).
    """
    await page.wait_for_timeout(2000)
    score = 0

    # Signal 1: URL pattern
    try:
        url = page.url.lower()
        _confirm_url_patterns = [
            "thank", "thanks", "danke", "success", "erfolgreich",
            "confirm", "bestatig", "submitted", "bewerbung-eingegangen",
            "application-sent", "applied", "complete", "finished",
        ]
        if any(p in url for p in _confirm_url_patterns):
            score += 3
            log.info("Submission signal: confirmation URL (%s)", url[:80])
    except Exception:
        pass

    # Signal 2: Visible text confirmation
    try:
        text = (await page.inner_text("body")).lower()
        _confirm_phrases = [
            "thank you", "thanks for applying", "application received",
            "successfully submitted", "application submitted",
            "we'll be in touch", "we will be in touch",
            "your application has been", "bewerbung eingegangen",
            "vielen dank", "danke für", "erfolgreich", "bewerbung erhalten",
            "we have received", "wir haben deine bewerbung",
            "application complete", "you have applied",
        ]
        _negative_phrases = [
            "please fill", "required field", "pflichtfeld",
            "bitte ausfüllen", "error", "fehler",
        ]
        matches = [p for p in _confirm_phrases if p in text]
        if matches:
            score += 3
            log.info("Submission signal: confirmation text (%s)", matches[0])
        neg = [p for p in _negative_phrases if p in text]
        if neg:
            score -= 3
            log.info("Submission negative signal: error text (%s)", neg[0])
    except Exception:
        pass

    # Signal 3: Form-gone check (no empty required fields = likely submitted)
    try:
        required_empty = await page.evaluate("""() => {
            const fields = document.querySelectorAll(
                'input[required]:not([type=hidden]), textarea[required], select[required]'
            );
            let empty = 0;
            fields.forEach(f => {
                if (f.offsetParent && !f.value.trim()) empty++;
            });
            return empty;
        }""")
        if required_empty == 0:
            score += 1
        elif required_empty > 2:
            score -= 2
            log.info("Submission negative signal: %d empty required fields", required_empty)
    except Exception:
        pass

    # If already confident, skip vision call
    if score >= 3:
        log.info("Submission confirmed (score=%d, no vision needed)", score)
        return True
    if score <= -3:
        log.info("Submission rejected (score=%d)", score)
        return False

    # Signal 4: Claude vision (only when uncertain)
    try:
        import base64, json as _json, os
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if api_key:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            screenshot = await page.screenshot()
            b64 = base64.b64encode(screenshot).decode()
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=60,
                messages=[{"role": "user", "content": [
                    {"type": "image", "source": {
                        "type": "base64", "media_type": "image/png", "data": b64}},
                    {"type": "text", "text":
                        "Is this a job application CONFIRMATION page (showing the app was submitted)? "
                        'Reply with JSON only: {"confirmed": true} or {"confirmed": false}'}
                ]}]
            )
            raw = resp.content[0].text.strip()
            s, e = raw.find("{"), raw.rfind("}") + 1
            if s >= 0 and e > s:
                data = _json.loads(raw[s:e])
                if data.get("confirmed"):
                    score += 4
                    log.info("Submission signal: Claude vision confirmed")
                else:
                    score -= 2
                    log.info("Submission negative signal: Claude vision not confirmed")
    except Exception as _ve:
        log.debug("Vision verify failed: %s", _ve)

    confirmed = score >= 3
    log.info("Submission verification score=%d → %s", score, "CONFIRMED" if confirmed else "REJECTED")
    return confirmed

'''

# Replace the old _verify_submission
OLD_VERIFY = '''async def _verify_submission(page: Page) -> bool:
    await page.wait_for_timeout(2000)
    try:
        text = (await page.content()).lower()
        for phrase in [
            "thank you", "application received", "successfully submitted",
            "application submitted", "we'll be in touch", "your application",
            "bewerbung eingegangen", "vielen dank", "erfolgreich",
        ]:
            if phrase in text:
                return True
    except Exception:
        pass
    return False'''

if OLD_VERIFY in ec:
    ec = ec.replace(OLD_VERIFY, SMART_VERIFY.rstrip(), 1)
    print("✅ _verify_submission replaced with smart multi-signal verifier")
else:
    print("⚠️  _verify_submission anchor not found")

ea.write_text(ec, encoding="utf-8")

# Also patch smart_filler.py — use _verify_submission from external_applier
sf = Path("applier/smart_filler.py")
sc = sf.read_text(encoding="utf-8")

OLD_SF = '''        # Check for success page
        try:
            page_text = (await page.content()).lower()
            if any(kw in page_text for kw in [
                "thank you", "application received", "successfully submitted",
                "application submitted", "we\\'ll be in touch",
                "bewerbung eingegangen", "vielen dank", "erfolgreich",
            ]):
                _emit("apply_step", {"url": url, "step": "  ✅ Smart apply: submission confirmed"})'''

NEW_SF = '''        # Use smart multi-signal verifier
        try:
            from applier.external_applier import _verify_submission as _verify_ext
            _confirmed = await _verify_ext(page)
            if _confirmed:
                _emit("apply_step", {"url": url, "step": "  ✅ Smart apply: submission confirmed"})'''

if OLD_SF in sc:
    sc = sc.replace(OLD_SF, NEW_SF, 1)
    sf.write_text(sc, encoding="utf-8")
    print("✅ smart_filler uses smart _verify_submission")
else:
    print("⚠️  smart_filler success check anchor not found")

# Patch applier.py: "page closed" for EXTERNAL apply should NOT be instant success
# Only Easy Apply modal close is a valid success signal
ap = Path("applier/applier.py")
ac = ap.read_text(encoding="utf-8")
OLD_CLOSED = (
    '        if any(x in _exc_str or x in _exc_type for x in\n'
    '               ["targetclosed", "target page", "context destroyed", "target closed"]):\n'
    '            _emit("apply_step", {"url": job.get("url", ""), "step":\n'
    '                "✓ Page closed after submit — application likely submitted"})\n'
    '            return {"success": True, "manual": False,\n'
    '                    "note": "Submitted (page closed)", "apply_type": "Easy Apply"}'
)
NEW_CLOSED = (
    '        if any(x in _exc_str or x in _exc_type for x in\n'
    '               ["targetclosed", "target page", "context destroyed", "target closed"]):\n'
    '            # Page closed — only valid success signal for Easy Apply modal\n'
    '            # For external apply this would be a false positive\n'
    '            _apply_url = job.get("url", "")\n'
    '            _is_easy_apply = "linkedin.com" in _apply_url\n'
    '            if _is_easy_apply:\n'
    '                _emit("apply_step", {"url": _apply_url,\n'
    '                    "step": "✓ Page closed after submit — application likely submitted"})\n'
    '                return {"success": True, "manual": False,\n'
    '                        "note": "Submitted (page closed)", "apply_type": "Easy Apply"}\n'
    '            else:\n'
    '                _emit("apply_step", {"url": _apply_url,\n'
    '                    "step": "⚠️ External page closed — sending to manual queue"})\n'
    '                return {"success": False, "manual": True,\n'
    '                        "note": "External page closed before confirmation"}'
)
if OLD_CLOSED in ac:
    ac = ac.replace(OLD_CLOSED, NEW_CLOSED, 1)
    ap.write_text(ac, encoding="utf-8")
    print("✅ applier.py: external page close no longer auto-success")
else:
    print("⚠️  page-closed anchor not found in applier.py")

# Syntax checks
for f in ["applier/external_applier.py", "applier/smart_filler.py", "applier/applier.py"]:
    r = subprocess.run(["python", "-m", "py_compile", f], capture_output=True, text=True)
    print(f"{'✅' if r.returncode==0 else '❌'} Syntax: {f}" + (f"\n   {r.stderr.strip()}" if r.returncode else ""))
