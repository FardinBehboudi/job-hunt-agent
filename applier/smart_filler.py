"""
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

    # Load memory context
    mem_context = ""
    try:
        from applier.memory import get_memory
        _url = page.url
        _plat = _url.split("/")[2].replace("www.","").split(".")[0] if _url else ""
        mem_context = get_memory().build_prompt_context(platform=_plat)
    except Exception:
        pass

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
                        f"{instruction}\n\n"                        f"{mem_context + chr(10) if mem_context else ""}"                        f"Candidate: {prof_summary}\n\n"
                        f"Form fields inventory (JSON):\n{dom_snap[:3000]}\n\n"
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

            else:  # fill — use universal handler from linkedin_applier
                try:
                    from applier.linkedin_applier import fill_field_smart
                    ok = await fill_field_smart(page, el, value, label=lbl, cfg={})
                    if ok:
                        executed += 1
                    else:
                        raise Exception('fill_field_smart returned False')
                except Exception:
                    if typ == "number":
                        value = re.sub(r"[^\d]", "", value.split(".")[0]) or "1"
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
                "application submitted", "we\'ll be in touch",
                "bewerbung eingegangen", "vielen dank", "erfolgreich",
            ]):
                _emit("apply_step", {"url": url, "step": "  ✅ Smart apply: submission confirmed"})
                try:
                    from applier.memory import get_memory
                    _plat2 = page.url.split("/")[2].replace("www.","").split(".")[0]
                    get_memory().save_application_result(url, _plat2, True, [])
                except Exception:
                    pass
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
