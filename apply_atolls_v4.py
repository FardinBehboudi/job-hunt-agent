"""
Atolls v4: fixes for location autocomplete and custom dropdown questions.
"""
import asyncio, json, sys, io
from pathlib import Path
from datetime import datetime
from playwright.async_api import async_playwright, Frame, TimeoutError as PWTimeout

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

SESSION_FILE = Path("uploads/linkedin_session.json")
RESUME = str(Path("uploads/resume_en.pdf").resolve())

P = {
    "first_name": "Felix",
    "last_name": "Behboudi",
    "email": "f_behboud@hotmail.com",
    "phone": "+491744548555",
    "linkedin": "https://www.linkedin.com/in/fbehboudi/",
    "location": "Berlin",
    "salary": "75000",
    "years_exp": "5",
}


def ts(): return datetime.now().strftime("%H%M%S")


async def react_set(frame: Frame, selector: str, value: str, label="") -> bool:
    """Set value and fire React events."""
    try:
        el = await frame.query_selector(selector)
        if not el:
            print(f"    ✗ {label}: not found")
            return False
        try:
            await el.scroll_into_view_if_needed(timeout=3000)
        except Exception:
            pass
        # Native setter + events
        await frame.evaluate(
            """([sel, val]) => {
                const el = document.querySelector(sel);
                if (!el) return;
                const proto = el.tagName==='TEXTAREA'
                    ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
                const setter = Object.getOwnPropertyDescriptor(proto,'value');
                if (setter?.set) setter.set.call(el, val); else el.value = val;
                ['input','change','blur'].forEach(e =>
                    el.dispatchEvent(new Event(e,{bubbles:true})));
            }""", [selector, value]
        )
        await el.click(click_count=3)
        await el.fill(value)
        print(f"    ✓ {label or selector}: '{value[:40]}'")
        return True
    except Exception as e:
        print(f"    ✗ {label or selector}: {e}")
        return False


async def type_location(frame: Frame, selector: str, value: str) -> bool:
    """Type location char-by-char to trigger autocomplete, then pick first suggestion."""
    try:
        el = await frame.query_selector(selector)
        if not el:
            return False
        await el.scroll_into_view_if_needed(timeout=3000)
        await el.click()
        await el.type(value, delay=80)  # Type character by character
        await asyncio.sleep(2)  # Wait for autocomplete

        # Try to pick first suggestion (Google Places or custom)
        for opt_sel in [
            ".pac-item:first-child",           # Google Places
            ".autocomplete-item:first-child",
            "[role='option']:first-child",
            "li[role='option']:first-child",
            ".dropdown-item:first-child",
        ]:
            try:
                opt = await frame.query_selector(opt_sel)
                if opt and await opt.is_visible():
                    await opt.click()
                    print(f"    ✓ location: picked suggestion")
                    return True
            except Exception:
                pass

        # No suggestion appeared — try pressing Enter or Tab to confirm
        await el.press("ArrowDown")
        await asyncio.sleep(0.5)
        await el.press("Enter")
        print(f"    ✓ location: typed '{value}' + Enter")
        return True
    except Exception as e:
        print(f"    ✗ location type: {e}")
        return False


async def select_greenhouse_dropdown(frame: Frame, input_id: str, desired_value: str) -> bool:
    """
    Handle Greenhouse custom select (looks like 'Select...' but is a custom component).
    Finds the container, clicks to open, then selects the desired option.
    """
    try:
        # Find the input element first
        inp = await frame.query_selector(f"#{input_id}")
        if not inp:
            print(f"    ✗ dropdown {input_id}: not found")
            return False

        # Look for the parent container (usually a div wrapping the input)
        # Try clicking the visible "Select..." trigger near the input
        container = await frame.query_selector(
            f"#{input_id} ~ div, #{input_id} + div, "
            f"div:has(#{input_id})"
        )

        # Try: find .select__control, .select-input, or just the label container
        # Greenhouse often uses Select2 or React-Select
        click_targets = []

        # Try the input itself
        click_targets.append(f"#{input_id}")
        # Try surrounding divs
        for cls in [".select__control", ".select__value-container",
                     "[class*='select']", "[class*='Select']"]:
            try:
                # Find closest ancestor with this class
                result = await frame.evaluate(
                    f"""(inputId) => {{
                        const inp = document.getElementById(inputId);
                        if (!inp) return null;
                        // Walk up to find select container
                        let el = inp.parentElement;
                        for (let i=0; i<5; i++) {{
                            if (!el) break;
                            if (el.className && (
                                el.className.includes('select') ||
                                el.className.includes('Select') ||
                                el.getAttribute('role') === 'combobox' ||
                                el.getAttribute('role') === 'listbox'
                            )) return el.className;
                            el = el.parentElement;
                        }}
                        return null;
                    }}""", input_id
                )
                if result:
                    print(f"    Found container class: {result[:50]}")
                    break
            except Exception:
                pass

        # Try clicking the input to trigger the dropdown
        await inp.scroll_into_view_if_needed(timeout=3000)
        await inp.click()
        await asyncio.sleep(1)

        # Check if a dropdown appeared
        for opt_sel in [
            f"[data-value*='{desired_value}' i]",
            f"option:has-text('{desired_value}')",
            f"li:has-text('{desired_value}')",
            f"[role='option']:has-text('{desired_value}')",
            f".select__option:has-text('{desired_value}')",
            f"[class*='option']:has-text('{desired_value}')",
        ]:
            try:
                opt = await frame.query_selector(opt_sel)
                if opt and await opt.is_visible():
                    await opt.click()
                    print(f"    ✓ dropdown {input_id}: selected '{desired_value}'")
                    return True
            except Exception:
                pass

        # If it's a native <select>, use select_option
        tag = await inp.evaluate("el => el.tagName.toLowerCase()")
        if tag == "select":
            for val in [desired_value, desired_value.lower(), desired_value.upper()]:
                try:
                    await inp.select_option(label=val)
                    print(f"    ✓ select {input_id}: '{val}'")
                    return True
                except Exception:
                    pass

        # Last resort: just type the value
        await react_set(frame, f"#{input_id}", desired_value, f"dropdown_text_{input_id}")
        return True

    except Exception as e:
        print(f"    ✗ dropdown {input_id}: {e}")
        return False


async def main():
    print("=== ATOLLS APPLY V4 ===")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled", "--start-maximized"],
        )
        ctx = await browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
            locale="en-US", timezone_id="Europe/Berlin",
        )
        await ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        if SESSION_FILE.exists():
            s = json.loads(SESSION_FILE.read_text())
            cookies = s if isinstance(s, list) else s.get("cookies", [])
            if cookies: await ctx.add_cookies(cookies)

        page = await ctx.new_page()
        await page.goto("https://boards.greenhouse.io/atolls/jobs/7564145",
                        wait_until="domcontentloaded", timeout=25_000)
        await page.wait_for_timeout(3000)

        # Accept cookies
        for sel in ["button:has-text('Accept')", "#onetrust-accept-btn-handler"]:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    await el.click()
                    await page.wait_for_timeout(2000)
                    break
            except Exception:
                pass
        await page.wait_for_timeout(5000)

        # Wait for GH iframe
        frame = None
        for _ in range(20):
            for f in page.frames:
                if "greenhouse.io" in f.url and "embed" in f.url:
                    try:
                        n = len(await f.query_selector_all("input"))
                        if n >= 3:
                            frame = f
                            print(f"GH iframe found: {n} inputs")
                            break
                    except Exception:
                        pass
            if frame:
                break
            await page.wait_for_timeout(500)

        if not frame:
            print("ERROR: no iframe")
            await browser.close()
            return

        await page.screenshot(path=f"uploads/v4_loaded_{ts()}.png")

        # Print ALL inputs with full details for debugging
        print("\n=== ALL INPUTS IN GREENHOUSE IFRAME ===")
        all_inputs = await frame.query_selector_all("input, textarea, select")
        for inp in all_inputs:
            try:
                typ = await inp.get_attribute("type") or await inp.evaluate("el=>el.tagName.toLowerCase()")
                id_a = await inp.get_attribute("id") or ""
                name_a = await inp.get_attribute("name") or ""
                cls = (await inp.get_attribute("class") or "")[:40]
                visible = await inp.is_visible()
                # get label
                lbl = ""
                if id_a:
                    try:
                        l = await frame.query_selector(f"label[for='{id_a}']")
                        if l: lbl = (await l.inner_text()).strip()[:50]
                    except Exception:
                        pass
                print(f"  [{typ}] id={id_a!r} name={name_a!r} cls={cls!r} vis={visible} lbl={lbl!r}")
            except Exception:
                pass

        # ── Fill standard fields ──────────────────────────────────
        await react_set(frame, "#first_name", P["first_name"], "first_name")
        await react_set(frame, "#last_name",  P["last_name"],  "last_name")
        await react_set(frame, "#email",      P["email"],      "email")
        await react_set(frame, "#phone",      P["phone"],      "phone")
        await react_set(frame, "#linkedin_url", P["linkedin"], "linkedin")

        # Location — use type() to trigger autocomplete
        print("  Filling location with type()...")
        loc_ok = await type_location(frame, "#candidate-location", P["location"])
        if not loc_ok:
            # Fallback: direct JS + fill
            await react_set(frame, "#candidate-location", P["location"], "location_fallback")

        # Wait a moment and screenshot
        await page.wait_for_timeout(1000)
        await page.screenshot(path=f"uploads/v4_basic_fields_{ts()}.png")

        # ── Resume upload ─────────────────────────────────────────
        try:
            fi = await frame.query_selector("input[type='file']")
            if fi:
                await fi.set_input_files(RESUME)
                await page.wait_for_timeout(3000)
                print("  ✓ resume uploaded")
        except Exception as e:
            print(f"  ✗ resume: {e}")

        await page.screenshot(path=f"uploads/v4_after_resume_{ts()}.png")

        # ── Custom text questions ─────────────────────────────────
        await react_set(frame, "#question_63011684", P["years_exp"], "years_exp")
        await react_set(frame, "#question_63011689", P["salary"],    "salary")
        await react_set(frame, "#question_63011690", "LinkedIn",     "heard_of_atolls")

        # ── Dropdown/select questions ─────────────────────────────
        # First inspect what these dropdowns look like
        print("\n  Inspecting dropdown questions...")
        for qid in ["question_63011685", "question_63011686", "question_63011687",
                    "question_63011688", "question_63011691"]:
            try:
                inp = await frame.query_selector(f"#{qid}")
                if not inp:
                    # try with brackets
                    inp = await frame.query_selector(f"[id*='{qid}']")
                if inp:
                    tag = await inp.evaluate("el => el.tagName.toLowerCase()")
                    visible = await inp.is_visible()
                    role = await inp.get_attribute("role") or ""
                    # Get parent HTML
                    parent_html = await inp.evaluate(
                        "el => el.parentElement ? el.parentElement.outerHTML.slice(0,300) : ''"
                    )
                    print(f"    {qid}: tag={tag} vis={visible} role={role!r}")
                    print(f"    parent: {parent_html[:200]}")
            except Exception as e:
                print(f"    {qid}: error - {e}")

        # Try handling dropdown questions using click + select option
        dropdown_q = {
            "question_63011685": "Yes",  # Java 17
            "question_63011686": "Yes",  # Spring Boot
            "question_63011687": "Yes",  # microservices
            "question_63011688": "Yes",  # based in Berlin
        }
        for qid, val in dropdown_q.items():
            await select_greenhouse_dropdown(frame, qid, val)
            await page.wait_for_timeout(500)

        # Channel question (may be multi-select checkbox)
        channel_sel = "input[id*='question_63011691']"
        channel_inputs = await frame.query_selector_all(channel_sel)
        print(f"  Channel inputs: {len(channel_inputs)}")
        for ci in channel_inputs:
            try:
                cid = await ci.get_attribute("id") or ""
                typ = await ci.get_attribute("type") or "text"
                lbl_el = await frame.query_selector(f"label[for='{cid}']")
                lbl = (await lbl_el.inner_text()).strip() if lbl_el else ""
                print(f"    channel option: [{typ}] id={cid!r} label={lbl!r}")
                if "linkedin" in lbl.lower() or typ == "text":
                    if typ == "checkbox":
                        await ci.evaluate("el => { el.checked=true; el.dispatchEvent(new Event('change',{bubbles:true})); }")
                        print(f"    ✓ checked: {lbl}")
                    else:
                        await react_set(frame, f"#{cid}", "LinkedIn", "channel")
            except Exception:
                pass

        # Acknowledge/confirm checkboxes
        cbs = await frame.query_selector_all("input[type='checkbox']")
        for cb in cbs:
            try:
                cb_id = await cb.get_attribute("id") or ""
                checked = await cb.is_checked()
                if not checked:
                    await frame.evaluate(
                        "(id) => { const el=document.getElementById(id); if(el){el.checked=true; el.dispatchEvent(new Event('change',{bubbles:true}));} }",
                        cb_id
                    )
                    print(f"  ✓ checked: {cb_id}")
            except Exception:
                pass

        await page.screenshot(path=f"uploads/v4_all_filled_{ts()}.png")
        print("\nTaking screenshot of filled form...")

        # Scroll down to see all fields
        await frame.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(1000)
        await page.screenshot(path=f"uploads/v4_scrolled_down_{ts()}.png")

        # ── Submit ───────────────────────────────────────────────
        for sub_sel in ["input[type='submit']", "#submit_app", "button[type='submit']",
                        "button:has-text('Submit application')"]:
            try:
                btn = await frame.query_selector(sub_sel)
                if btn:
                    val = await btn.get_attribute("value") or await btn.inner_text() or "submit"
                    print(f"\nSubmitting: '{val.strip()}'")
                    await btn.scroll_into_view_if_needed(timeout=3000)
                    await btn.click()
                    await page.wait_for_timeout(8000)
                    await page.screenshot(path=f"uploads/v4_submitted_{ts()}.png")

                    frame_body = ""
                    try:
                        frame_body = (await frame.inner_text("body")).lower()
                    except Exception:
                        pass
                    main_body = (await page.inner_text("body")).lower()
                    combined = frame_body + " " + main_body

                    success_kws = [
                        "thank you", "application received", "successfully", "submitted",
                        "danke", "confirmation", "we received", "thanks for applying",
                        "your application", "application has been"
                    ]
                    success = any(k in combined for k in success_kws)
                    print(f"Frame body: {frame_body[:400]}")
                    print(f"\n{'✅ SUCCESS' if success else '❌ FAILED'}")
                    break
            except Exception as e:
                print(f"Submit error: {e}")

        await browser.close()

asyncio.run(main())
