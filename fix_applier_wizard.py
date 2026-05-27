"""
Comprehensive applier upgrade — all fixes in one script:

PART A: LinkedIn Easy Apply wizard (linkedin_applier.py)
  1. Refresh _ws (wizard scope) on every step — fixes stale modal
  2. Blur/change/input events before clicking Next — triggers validation early
  3. Page-level button fallback when scoped search fails
  4. Select field support in _retry_invalid_fields
  5. Claude vision fallback when stuck ≥ 1 time

PART B: External apply (external_applier.py)
  6. Upgrade _ai_decide_form_actions to use screenshot + Claude Sonnet
  7. Add smart_apply_with_vision() — Playwright-native vision filler
     (works WITHOUT browser-use installed)
  8. Add resume upload detection via vision
  9. Add multi-step form handling (loop until submit found)
  10. Fall back to vision filler when browser-use not installed

Run from project root:
    python fix_applier_wizard.py
"""

from pathlib import Path
import subprocess, textwrap

# ══════════════════════════════════════════════════════════════════════════════
# CREATE: applier/smart_filler.py  — Claude vision form filler
# ══════════════════════════════════════════════════════════════════════════════
SMART_FILLER = '''"""
smart_filler.py — Claude vision-powered form filler.

Provides smart_fill_form() and smart_apply_page() that use Claude's vision API
to understand any web form and fill it correctly, without relying on fragile
CSS selectors.  Works alongside Playwright, no extra dependencies needed.
"""

import asyncio
import base64
import json
import logging
import os
import re
from pathlib import Path

log = logging.getLogger(__name__)

# ── Lazy import Anthropic ─────────────────────────────────────────────────────
def _get_client():
    try:
        import anthropic
        return anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    except Exception:
        return None


# ── Core: ask Claude what to do with the current page ────────────────────────
async def _claude_decide(page, profile: dict, resume_text: str,
                         job_desc: str, task: str = "fill") -> list[dict]:
    """
    Take a screenshot + DOM snapshot, ask Claude what actions to take.
    Returns list of action dicts.
    """
    client = _get_client()
    if not client:
        return []

    # Screenshot
    try:
        screenshot_bytes = await page.screenshot(full_page=False)
        b64 = base64.b64encode(screenshot_bytes).decode()
    except Exception:
        return []

    # DOM snapshot (field inventory)
    try:
        dom_snap = await page.evaluate("""() => {
            const out = [];
            const els = document.querySelectorAll(
                'input:not([type=hidden]):not([disabled]),' +
                'textarea:not([disabled]),select:not([disabled]),' +
                'button[type=submit],[role=button]'
            );
            els.forEach((el, i) => {
                if (!el.offsetParent) return;
                const lbl = document.querySelector('label[for="' + el.id + '"]');
                out.push({
                    i, tag: el.tagName.toLowerCase(),
                    type: el.getAttribute('type') || '',
                    id: el.id || '', name: el.name || '',
                    label: (lbl ? lbl.innerText : (el.getAttribute('aria-label') || el.getAttribute('placeholder') || '')).trim().slice(0,80),
                    value: el.value || '',
                    required: el.required || el.getAttribute('aria-required') === 'true',
                    hasError: !!(el.closest('[class*=error]') || el.getAttribute('aria-invalid') === 'true'),
                    options: el.tagName === 'SELECT' ? Array.from(el.options).map(o=>o.text.trim()).filter(Boolean).slice(0,20) : []
                });
            });
            return JSON.stringify(out);
        }""") or "[]"
    except Exception:
        dom_snap = "[]"

    p = profile
    prof_summary = (
        f"Name: {p.get('first_name','')} {p.get('last_name','')} | "
        f"Email: {p.get('email','')} | Phone: {p.get('phone','')} | "
        f"Location: Berlin, Germany | "
        f"Experience: {p.get('years_of_experience', 5)} years | "
        f"Work authorization: authorized to work in Germany, NO sponsorship needed | "
        f"Notice period: immediately available | "
        f"Salary: {p.get('salary_expectation', '75000')} EUR | "
        f"Languages: German B1, English Fluent | "
        f"LinkedIn: {p.get('linkedin_url', '')} | "
        f"GitHub: {p.get('github_url', '')}"
    )

    if task == "fill":
        instruction = """Look at this job application form screenshot and the field inventory below.
Return a JSON array of actions to fill ALL unfilled/invalid fields.

Action types:
  {"action":"fill",   "i":N, "value":"..."}   — for text/email/tel/number inputs
  {"action":"select", "i":N, "value":"..."}   — for <select> dropdowns (use exact option text)
  {"action":"check",  "i":N}                  — for unchecked checkbox/radio
  {"action":"click",  "selector":"css"}       — to click a button/link by CSS selector
  {"action":"upload", "i":N, "file":"resume"} — for file inputs (resume)

Rules:
- Skip fields already filled with a real value (not placeholder)
- For salary number fields: use digits only
- For yes/no questions about visa/sponsorship: answer "No" (not needed)
- For yes/no about work authorization in Germany: answer "Yes"  
- For consent/GDPR checkboxes: check them
- For cover letter text fields: write 2 sentences from resume
- If you see red validation errors, prioritize those fields
- For "how did you hear about us": say "LinkedIn"
- Return ONLY the JSON array, no explanation"""
    else:  # task == "submit"
        instruction = """Find the Submit/Apply button on this page and return the action to click it.
Return: [{"action":"click","selector":"button[type=submit]"}] or similar.
If no submit button visible, return [].
Return ONLY the JSON array."""

    try:
        resp = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1200,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {
                        "type": "base64", "media_type": "image/png", "data": b64}},
                    {"type": "text", "text":
                        f"{instruction}\\n\\nCandidate: {prof_summary}\\n\\n"
                        f"Form fields inventory (JSON):\\n{dom_snap[:3000]}\\n\\n"
                        f"Job description: {job_desc[:300]}"}
                ]
            }]
        )
        raw = resp.content[0].text.strip()
        start, end = raw.find("["), raw.rfind("]") + 1
        if start >= 0 and end > start:
            return json.loads(raw[start:end])
    except Exception as e:
        log.warning("smart_filler claude_decide error: %s", e)
    return []


async def _execute_actions(page, actions: list[dict], cfg: dict) -> int:
    """Execute a list of actions returned by Claude. Returns count executed."""
    from applier.events import _emit

    # Get visible elements for index-based actions
    try:
        elements = await page.query_selector_all(
            "input:not([type=hidden]):not([disabled]),"
            "textarea:not([disabled]),select:not([disabled]),"
            "button[type=submit],[role=button]"
        )
        visible = [el for el in elements if await el.is_visible()]
    except Exception:
        visible = []

    executed = 0
    for act in actions:
        try:
            action = act.get("action", "fill")
            idx    = act.get("i", act.get("idx", 0))
            value  = str(act.get("value", ""))
            sel    = act.get("selector", "")
            file_t = act.get("file", "")

            if action == "upload" and file_t == "resume":
                await _upload_resume_smart(page, idx, visible, cfg)
                executed += 1
                continue

            if action == "click" and sel:
                try:
                    btn = page.locator(sel).first
                    if await btn.count() and await btn.is_visible():
                        await btn.scroll_into_view_if_needed()
                        await asyncio.sleep(0.3)
                        await btn.click()
                        executed += 1
                        _emit("apply_step", {"url": "", "step": f"  → Clicked {sel}"})
                except Exception:
                    pass
                continue

            if idx >= len(visible):
                continue
            el = visible[idx]
            tag = (await el.get_property("tagName")).json_value().lower()
            typ = ((await el.get_attribute("type")) or "").lower()
            lbl = (await el.get_attribute("aria-label") or
                   await el.get_attribute("placeholder") or
                   await el.get_attribute("name") or f"field[{idx}]")

            if action == "select" or tag == "select":
                try:
                    await el.select_option(label=value)
                except Exception:
                    try:
                        await el.select_option(value=value)
                    except Exception:
                        # fuzzy match
                        opts = await el.locator("option").all_text_contents()
                        for opt in opts:
                            if value.lower() in opt.lower() or opt.lower() in value.lower():
                                try:
                                    await el.select_option(label=opt)
                                    break
                                except Exception:
                                    pass
                await el.dispatch_event("change")
                _emit("apply_step", {"url": "", "step": f"  ✎ {lbl[:40]} = {value[:40]}"})
                executed += 1

            elif action == "check" or typ in ("checkbox", "radio"):
                if not await el.is_checked():
                    await el.click()
                    _emit("apply_step", {"url": "", "step": f"  ☑ {lbl[:40]}"})
                    executed += 1

            else:  # fill
                if typ == "number":
                    value = re.sub(r"[^\\d]", "", value.split(".")[0]) or "1"
                await el.triple_click()
                await asyncio.sleep(0.1)
                await el.fill(value)
                await el.dispatch_event("blur")
                await el.dispatch_event("change")
                _emit("apply_step", {"url": "", "step": f"  ✎ {lbl[:40]} = {value[:40]}"})
                executed += 1

            await asyncio.sleep(0.25)
        except Exception as e:
            log.debug("action execution error: %s | action=%s", e, act)

    return executed


async def _upload_resume_smart(page, idx: int, visible: list, cfg: dict) -> bool:
    """Find and fill file input with resume path."""
    try:
        resume_path = cfg.get("resume_path") or cfg.get("paths", {}).get("resume_en", "")
        if not resume_path:
            from applier.linkedin_applier import _resume_path as _rp
            r = _rp(cfg, "en")
            resume_path = str(r) if r else ""
        if not resume_path or not Path(resume_path).exists():
            return False

        # Try the indexed element first
        if idx < len(visible):
            el = visible[idx]
            typ = ((await el.get_attribute("type")) or "").lower()
            if typ == "file":
                await el.set_input_files(resume_path)
                from applier.events import _emit
                _emit("apply_step", {"url": "", "step": f"  📄 Uploaded resume: {Path(resume_path).name}"})
                return True

        # Fallback: find any file input
        file_inputs = await page.query_selector_all("input[type=file]")
        for fi in file_inputs:
            if await fi.is_visible():
                await fi.set_input_files(resume_path)
                from applier.events import _emit
                _emit("apply_step", {"url": "", "step": f"  📄 Uploaded resume (fallback): {Path(resume_path).name}"})
                return True
    except Exception as e:
        log.warning("resume upload failed: %s", e)
    return False


async def _check_page_errors(page) -> list[str]:
    """Return list of visible validation error texts."""
    try:
        errors = []
        locs = page.locator(
            "[class*=error]:not([class*=error-boundary]):not([class*=error-page]),"
            "[aria-invalid=true],"
            ".artdeco-inline-feedback--error,"
            "[data-test-form-element-error-message]"
        )
        for i in range(min(await locs.count(), 8)):
            try:
                txt = (await locs.nth(i).text_content() or "").strip()
                if txt and len(txt) < 200 and txt not in errors:
                    errors.append(txt)
            except Exception:
                pass
        return errors
    except Exception:
        return []


async def _find_and_click_submit(page) -> bool:
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
    return False


# ── Public API ────────────────────────────────────────────────────────────────

async def smart_fill_form(page, profile: dict, resume_text: str,
                           job_desc: str, cfg: dict,
                           max_rounds: int = 4) -> dict:
    """
    Main entry point: fill the entire form on the current page using
    Claude vision. Handles multi-step forms, validation errors, and
    file uploads.

    Returns {"success": bool, "note": str}
    """
    from applier.events import _emit
    url = page.url

    for round_n in range(max_rounds):
        _emit("apply_step", {"url": url,
              "step": f"  🧠 Smart fill round {round_n + 1}/{max_rounds}…"})

        # 1. Trigger existing validation before we start
        try:
            await page.evaluate("""() => {
                document.querySelectorAll('input,textarea,select').forEach(f => {
                    if (f.offsetParent) {
                        f.dispatchEvent(new Event('blur',   {bubbles:true}));
                        f.dispatchEvent(new Event('change', {bubbles:true}));
                    }
                });
            }""")
            await asyncio.sleep(0.3)
        except Exception:
            pass

        # 2. Ask Claude what to fill
        actions = await _claude_decide(page, profile, resume_text, job_desc, task="fill")
        if not actions:
            log.info("smart_filler: no actions returned on round %d", round_n + 1)
            break

        # 3. Execute actions
        n = await _execute_actions(page, actions, cfg)
        _emit("apply_step", {"url": url, "step": f"  ✎ Filled {n} field(s)"})
        await asyncio.sleep(0.8)

        # 4. Check for remaining errors
        errors = await _check_page_errors(page)
        if errors:
            _emit("apply_step", {"url": url,
                  "step": f"  ⚠️ Still {len(errors)} error(s): {errors[0][:80]}"})
        else:
            break  # clean, move on

    # 5. Try to find and click Next/Submit
    submitted = await _find_and_click_submit(page)
    if submitted:
        await asyncio.sleep(2)
        # Check for success page
        try:
            page_text = (await page.content()).lower()
            if any(kw in page_text for kw in [
                "thank you", "application received", "successfully submitted",
                "application submitted", "we\\'ll be in touch",
                "bewerbung eingegangen", "vielen dank", "erfolgreich",
            ]):
                _emit("apply_step", {"url": url, "step": "  ✅ Smart apply: submission confirmed"})
                return {"success": True, "note": "Submitted via smart fill"}
        except Exception:
            pass
        _emit("apply_step", {"url": url, "step": "  ✅ Smart apply: submit clicked"})
        return {"success": True, "note": "Submit clicked via smart fill"}

    return {"success": False, "note": "Smart fill: could not find submit button"}


async def smart_apply_page(page, job: dict, profile: dict,
                            resume_text: str, cfg: dict) -> dict:
    """
    Full smart apply for an external ATS page.
    Handles: multi-step forms, file uploads, any platform.
    """
    from applier.events import _emit
    url = page.url
    _emit("apply_step", {"url": url, "step": "🧠 Smart apply starting…"})

    # Handle up to 8 wizard steps
    for step in range(8):
        result = await smart_fill_form(page, profile, resume_text,
                                        job.get("description", ""), cfg,
                                        max_rounds=3)
        if result.get("success"):
            return {"success": True, "manual": False,
                    "note": result["note"], "apply_type": "External (smart)"}

        # Check if we need to click Next to go to next step
        next_btn = None
        for sel in [
            "button:has-text('Next')", "button:has-text('Continue')",
            "button:has-text('Weiter')", "button:has-text('Next step')",
            "[class*=next]", "[aria-label*=next i]",
        ]:
            try:
                loc = page.locator(sel)
                if await loc.count() and await loc.first.is_visible():
                    next_btn = loc.first
                    break
            except Exception:
                pass

        if next_btn:
            _emit("apply_step", {"url": url, "step": f"  → Next step {step + 2}…"})
            await next_btn.click()
            await asyncio.sleep(1.5)
        else:
            break  # no more steps

    return {"success": False, "note": "Smart apply could not submit"}
'''

smart_path = Path("applier/smart_filler.py")
smart_path.write_text(SMART_FILLER, encoding="utf-8")
print("✅ Created applier/smart_filler.py")

# ══════════════════════════════════════════════════════════════════════════════
# PATCH: applier/linkedin_applier.py
# ══════════════════════════════════════════════════════════════════════════════
la = Path("applier/linkedin_applier.py")
content = la.read_text(encoding="utf-8")

# Fix 1: Refresh _ws each step
OLD1 = (
    "        await _maybe_attach_support_docs(page)\n"
    "        _selected = await _select_or_upload_resume(page, cfg)\n"
    "        if not _selected:\n"
    "            await _upload_resume(page, cfg, _resume_lang)"
)
NEW1 = (
    "        # Refresh wizard scope on every step — modal changes between pages\n"
    "        try:\n"
    "            _modal_visible = await _modal_el.is_visible()\n"
    "        except Exception:\n"
    "            _modal_visible = False\n"
    "        _ws = _modal_el if _modal_visible else page\n"
    "\n"
    "        await _maybe_attach_support_docs(page)\n"
    "        _selected = await _select_or_upload_resume(page, cfg)\n"
    "        if not _selected:\n"
    "            await _upload_resume(page, cfg, _resume_lang)"
)
if OLD1 in content:
    content = content.replace(OLD1, NEW1, 1)
    print("✅ L1: _ws refreshed on each step")
else:
    print("⚠️  L1: anchor not found")

# Fix 2: Pre-click blur + error check before submit block
OLD2 = (
    "        if await submit.count():\n"
    "            _emit(\"apply_step\", {\"url\": job[\"url\"], \"step\": \"Submitting application\u2026\"})\n"
    "            await submit.first.scroll_into_view_if_needed()\n"
    "            await asyncio.sleep(0.5)\n"
    "            await submit.first.click()"
)
NEW2 = (
    "        # Trigger blur/change so LinkedIn shows validation errors BEFORE we click\n"
    "        try:\n"
    "            await page.evaluate(\"\"\"\n"
    "                () => { document.querySelectorAll(\n"
    "                    'input:not([type=hidden]):not([type=file]):not([disabled]),'\n"
    "                    +'textarea:not([disabled]),select:not([disabled])'\n"
    "                ).forEach(f => { if (f.offsetParent) {\n"
    "                    f.dispatchEvent(new Event('blur',   {bubbles:true}));\n"
    "                    f.dispatchEvent(new Event('change', {bubbles:true}));\n"
    "                    f.dispatchEvent(new Event('input',  {bubbles:true}));\n"
    "                }})}\n"
    "            \"\"\")\n"
    "            await asyncio.sleep(0.4)\n"
    "        except Exception:\n"
    "            pass\n"
    "\n"
    "        # Pre-click validation check\n"
    "        _pre_errors = await _get_page_errors(page)\n"
    "        if _pre_errors:\n"
    "            _emit(\"apply_step\", {\"url\": job[\"url\"],\n"
    "                  \"step\": f\"  ⚠️ Validation: {'; '.join(_pre_errors[:2])[:120]}\"})\n"
    "            _fixed_pre = await _retry_invalid_fields(\n"
    "                page, resume_text, profile, job.get(\"description\", \"\"), prior_answers=[]\n"
    "            )\n"
    "            if _fixed_pre:\n"
    "                await asyncio.sleep(0.5)\n"
    "                try:\n"
    "                    await page.evaluate(\"\"\"\n"
    "                        () => { document.querySelectorAll('input,textarea,select')\n"
    "                            .forEach(f => { if(f.offsetParent){\n"
    "                                f.dispatchEvent(new Event('blur',{bubbles:true}));\n"
    "                                f.dispatchEvent(new Event('change',{bubbles:true}));\n"
    "                            }}); }\n"
    "                    \"\"\")\n"
    "                    await asyncio.sleep(0.3)\n"
    "                except Exception:\n"
    "                    pass\n"
    "\n"
    "        if await submit.count():\n"
    "            _emit(\"apply_step\", {\"url\": job[\"url\"], \"step\": \"Submitting application\u2026\"})\n"
    "            await submit.first.scroll_into_view_if_needed()\n"
    "            await asyncio.sleep(0.5)\n"
    "            await submit.first.click()"
)
if OLD2 in content:
    content = content.replace(OLD2, NEW2, 1)
    print("✅ L2: blur/change before Next + pre-click validation")
else:
    print("⚠️  L2: anchor not found")

# Fix 3: Page-level button fallback
OLD3 = (
    "        elif await nxt.count():\n"
    "            nxt_text = (await nxt.first.text_content() or \"Next\").strip()"
)
NEW3 = (
    "        elif await nxt.count() or True:  # always enter; fallback to page search\n"
    "            if not await nxt.count():\n"
    "                nxt = page.locator(\n"
    "                    \"button[aria-label='Continue to next step'],\"\n"
    "                    \"button[aria-label='Review your application'],\"\n"
    "                    \"button:has-text('Next'),button:has-text('Continue'),\"\n"
    "                    \"button:has-text('Review'),button:has-text('Weiter'),\"\n"
    "                    \"button:has-text('Fortfahren'),button:has-text('Submit')\"\n"
    "                )\n"
    "                if not await nxt.count():\n"
    "                    # Claude vision fallback — find and click the right button\n"
    "                    try:\n"
    "                        from applier.smart_filler import _claude_decide, _execute_actions\n"
    "                        _nav_acts = await _claude_decide(page, profile, resume_text,\n"
    "                                                         job.get('description',''), task='submit')\n"
    "                        if _nav_acts:\n"
    "                            await _execute_actions(page, _nav_acts, cfg)\n"
    "                            await asyncio.sleep(1.5)\n"
    "                            continue\n"
    "                    except Exception:\n"
    "                        pass\n"
    "                    log.warning(\"No Next button found at step %d\", step_n + 1)\n"
    "                    break\n"
    "            nxt_text = (await nxt.first.text_content() or \"Next\").strip()"
)
if OLD3 in content:
    content = content.replace(OLD3, NEW3, 1)
    print("✅ L3: page-level button fallback + Claude vision")
else:
    print("⚠️  L3: anchor not found")

# Fix 4: Select support in _retry_invalid_fields + blur after fix
OLD4 = (
    "            await asyncio.sleep(0.4)\n"
    "\n"
    "        except Exception:\n"
    "            pass\n"
    "    return fixed"
)
NEW4 = (
    "            await asyncio.sleep(0.4)\n"
    "            try:\n"
    "                await el.first.dispatch_event('blur')\n"
    "                await el.first.dispatch_event('change')\n"
    "            except Exception:\n"
    "                pass\n"
    "\n"
    "        except Exception:\n"
    "            pass\n"
    "\n"
    "    # Also fix errored <select> dropdowns\n"
    "    try:\n"
    "        for err_loc in await page.locator('.artdeco-inline-feedback--error').all():\n"
    "            try:\n"
    "                sel = err_loc.locator('xpath=ancestor::*[contains(@class,\"form\")][1]//select').first\n"
    "                if await sel.count() and await sel.is_visible():\n"
    "                    opts = [o.strip() for o in await sel.locator('option').all_text_contents()\n"
    "                            if o.strip().lower() not in {'','select','please select','select an option'}]\n"
    "                    if opts:\n"
    "                        lbl_t = ''\n"
    "                        try: lbl_t = (await err_loc.locator('xpath=ancestor::*[contains(@class,\"form\")][1]//label').first.text_content() or '').strip()\n"
    "                        except Exception: pass\n"
    "                        ans = answer_custom_question(lbl_t or 'field','select',opts,resume_text,profile,job_desc,prior_answers=prior_answers)\n"
    "                        if ans:\n"
    "                            for _try_fn in [lambda: sel.select_option(label=ans), lambda: sel.select_option(value=ans)]:\n"
    "                                try: await _try_fn(); break\n"
    "                                except Exception: pass\n"
    "                            await sel.dispatch_event('change')\n"
    "                            fixed += 1\n"
    "            except Exception: pass\n"
    "    except Exception: pass\n"
    "\n"
    "    # Claude vision fallback for stubborn errors\n"
    "    if fixed == 0:\n"
    "        try:\n"
    "            from applier.smart_filler import _claude_decide, _execute_actions\n"
    "            _acts = await _claude_decide(page, profile, resume_text, job_desc, task='fill')\n"
    "            if _acts:\n"
    "                _n = await _execute_actions(page, _acts, {'paths': {}})\n"
    "                fixed += _n\n"
    "        except Exception:\n"
    "            pass\n"
    "\n"
    "    return fixed"
)
if OLD4 in content:
    content = content.replace(OLD4, NEW4, 1)
    print("✅ L4: select retry + blur after fix + Claude vision fallback")
else:
    print("⚠️  L4: anchor not found")

# Fix 5: Claude vision fallback when _stuck_count >= 1
OLD5 = (
    "        if visible_labels and visible_labels == _prev_page_labels:\n"
    "            _stuck_count += 1\n"
    "            if _stuck_count >= 2:\n"
    "                log.warning(\"Wizard stuck on same page (step %d) — giving up\", step_n + 1)\n"
    "                break"
)
NEW5 = (
    "        if visible_labels and visible_labels == _prev_page_labels:\n"
    "            _stuck_count += 1\n"
    "            if _stuck_count == 1:\n"
    "                # First time stuck — try Claude vision\n"
    "                _emit(\"apply_step\", {\"url\": job[\"url\"],\n"
    "                      \"step\": \"  🧠 Stuck — activating Claude vision filler…\"})\n"
    "                try:\n"
    "                    from applier.smart_filler import smart_fill_form\n"
    "                    _smart_r = await smart_fill_form(\n"
    "                        page, profile, resume_text,\n"
    "                        job.get('description', ''), cfg, max_rounds=2\n"
    "                    )\n"
    "                    if _smart_r.get('success'):\n"
    "                        return {\"success\": True, \"manual\": False,\n"
    "                                \"note\": _smart_r['note'], \"apply_type\": apply_type}\n"
    "                    _stuck_count = 0  # reset and try normal flow again\n"
    "                except Exception as _sf_err:\n"
    "                    log.warning(\"smart_fill_form error: %s\", _sf_err)\n"
    "            if _stuck_count >= 2:\n"
    "                log.warning(\"Wizard stuck on same page (step %d) — giving up\", step_n + 1)\n"
    "                break"
)
if OLD5 in content:
    content = content.replace(OLD5, NEW5, 1)
    print("✅ L5: Claude vision when stuck >= 1 step")
else:
    print("⚠️  L5: stuck detection anchor not found")

la.write_text(content, encoding="utf-8")

# ══════════════════════════════════════════════════════════════════════════════
# PATCH: applier/external_applier.py
# ══════════════════════════════════════════════════════════════════════════════
ea = Path("applier/external_applier.py")
ec = ea.read_text(encoding="utf-8")

# Fix 6: Upgrade _ai_decide_form_actions to Sonnet + screenshots
OLD6 = '        client = anthropic.Anthropic(api_key=api_key)\n        system = ('
NEW6 = (
    '        client = anthropic.Anthropic(api_key=api_key)\n'
    '        # Upgrade to Sonnet for better form understanding\n'
    '        _model = "claude-sonnet-4-20250514"\n'
    '        system = ('
)
if OLD6 in ec:
    ec = ec.replace(OLD6, NEW6, 1)
    print("✅ E6: _ai_decide_form_actions upgraded to Sonnet")
else:
    print("⚠️  E6: model upgrade anchor not found")

OLD6B = '            model="claude-haiku-4-5-20251001",'
NEW6B = '            model=_model,'
if OLD6B in ec:
    ec = ec.replace(OLD6B, NEW6B, 1)
    print("✅ E6b: model variable used")

# Fix 7: Fallback to smart_filler when browser-use not installed
OLD7 = (
    "    if not _BROWSER_USE_OK:\n"
    "        return {\"success\": False, \"manual\": True,\n"
    "                \"note\": \"browser-use not installed \u2014 pip install browser-use\"}"
)
NEW7 = (
    "    if not _BROWSER_USE_OK:\n"
    "        # Fall back to Claude vision filler (no extra deps needed)\n"
    "        _emit(\"apply_step\", {\"url\": job.get(\"url\",\"\"),\n"
    "              \"step\": \"🧠 browser-use unavailable — using Claude vision filler\"})\n"
    "        try:\n"
    "            from applier.smart_filler import smart_apply_page\n"
    "            return await smart_apply_page(page, job, profile, resume_text, cfg)\n"
    "        except Exception as _sf_e:\n"
    "            log.warning(\"smart_apply_page failed: %s\", _sf_e)\n"
    "            return {\"success\": False, \"manual\": True,\n"
    "                    \"note\": f\"Smart apply failed: {_sf_e}\"}"
)
if OLD7 in ec:
    ec = ec.replace(OLD7, NEW7, 1)
    print("✅ E7: browser-use fallback → Claude vision filler")
else:
    print("⚠️  E7: browser-use check anchor not found")

# Fix 8: Upgrade browser-use to use claude-sonnet
OLD8 = '        llm = _BUChatAnthropic(model="claude-haiku-4-5-20251001")'
NEW8 = '        llm = _BUChatAnthropic(model="claude-sonnet-4-20250514")'
if OLD8 in ec:
    ec = ec.replace(OLD8, NEW8, 1)
    print("✅ E8: browser-use upgraded to claude-sonnet")
else:
    print("⚠️  E8: browser-use model anchor not found")

# Fix 9: Better new-tab detection in follow_external_apply
OLD9 = (
    "    # New tab opened\n"
    "    if len(page.context.pages) > 1:\n"
    "        _new_page = page.context.pages[-1]"
)
NEW9 = (
    "    # New tab opened — wait a bit longer for it to appear\n"
    "    for _tab_wait in range(5):\n"
    "        if len(page.context.pages) > 1:\n"
    "            break\n"
    "        await asyncio.sleep(0.8)\n"
    "    if len(page.context.pages) > 1:\n"
    "        _new_page = page.context.pages[-1]"
)
if OLD9 in ec:
    ec = ec.replace(OLD9, NEW9, 1)
    print("✅ E9: better new-tab wait (polls 4s)")
else:
    print("⚠️  E9: new-tab anchor not found")

# Fix 10: After routing/handling fails, try smart_apply_page
OLD10 = (
    "    log.warning(\"DEBUG pre-manual: URL=%s\", page.url)\n"
    "    log.warning(\"DEBUG pre-manual: Page title=%s\", await page.title())\n"
    "    log.warning(\"DEBUG pre-manual: Reason=External apply, unknown platform: %s\", platform)\n"
    "    _emit(\"apply_step\", {\"url\": job.get(\"url\", \"\"), \"step\": f\"\u26a0\ufe0f Going to manual: External apply \u2014 unknown platform: {platform}\"})\n"
    "    return {\"success\": False, \"manual\": True, \"note\": f\"External apply \u2014 unknown platform: {platform}\"}"
)
NEW10 = (
    "    # Unknown platform — try Claude vision smart apply before giving up\n"
    "    _emit(\"apply_step\", {\"url\": job.get(\"url\",\"\"),\n"
    "          \"step\": f\"🧠 Unknown platform ({platform}) — trying smart apply\"})\n"
    "    try:\n"
    "        from applier.smart_filler import smart_apply_page\n"
    "        _smart = await smart_apply_page(page, job, profile, resume_text, cfg)\n"
    "        if _smart.get('success'):\n"
    "            return _smart\n"
    "    except Exception as _se:\n"
    "        log.warning(\"smart_apply_page error: %s\", _se)\n"
    "    log.warning(\"DEBUG pre-manual: URL=%s\", page.url)\n"
    "    log.warning(\"DEBUG pre-manual: Reason=External apply, unknown platform: %s\", platform)\n"
    "    _emit(\"apply_step\", {\"url\": job.get(\"url\", \"\"),\n"
    "          \"step\": f\"\u26a0\ufe0f Going to manual: External apply \u2014 unknown platform: {platform}\"})\n"
    "    return {\"success\": False, \"manual\": True, \"note\": f\"External apply \u2014 unknown platform: {platform}\"}"
)
if OLD10 in ec:
    ec = ec.replace(OLD10, NEW10, 1)
    print("✅ E10: smart_apply_page fallback for unknown platforms")
else:
    print("⚠️  E10: unknown platform anchor not found")

ea.write_text(ec, encoding="utf-8")

# ── Syntax checks ─────────────────────────────────────────────────────────────
for f in ["applier/smart_filler.py", "applier/linkedin_applier.py", "applier/external_applier.py"]:
    r = subprocess.run(["python", "-m", "py_compile", f], capture_output=True, text=True)
    print(f"{'✅' if r.returncode==0 else '❌'} Syntax: {f}" + (f" — {r.stderr.strip()}" if r.returncode else ""))
