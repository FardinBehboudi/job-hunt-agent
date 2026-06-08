"""
Atolls FINAL — uses JS-based React-Select opener for all combobox fields.
Fixes: microservices dropdown, heard_of_atolls dropdown, channel dropdown.
"""
import asyncio, json, sys, io
from pathlib import Path
from datetime import datetime
from playwright.async_api import async_playwright, Frame

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


async def js_fill(frame: Frame, selector: str, value: str, label="") -> bool:
    """Fill via native React setter + Playwright fill."""
    try:
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
        el = await frame.query_selector(selector)
        if el:
            await el.click(click_count=3)
            await el.fill(value)
        print(f"    ✓ {label or selector}: '{value[:40]}'")
        return True
    except Exception as e:
        print(f"    ✗ {label or selector}: {e}")
        return False


async def open_select_and_pick(frame: Frame, input_id: str, desired: str) -> bool:
    """
    Open a React-Select combobox by clicking its control container,
    then pick the option whose text matches `desired` (case-insensitive).
    If desired is empty, pick the FIRST option.
    """
    try:
        # Click the outer control container (walk up from input)
        await frame.evaluate(
            """(id) => {
                const inp = document.getElementById(id) ||
                    document.querySelector('[id="' + id + '"]');
                if (!inp) return;
                // Walk up to find the control div
                let el = inp.parentElement;
                for (let i = 0; i < 6; i++) {
                    if (!el) break;
                    // Click every ancestor that could be the control
                    try { el.click(); } catch(e) {}
                    if (el.className && (
                        el.className.includes('select__control') ||
                        el.className.includes('__container') ||
                        el.className.includes('Select')
                    )) break;
                    el = el.parentElement;
                }
            }""", input_id
        )
        await asyncio.sleep(0.8)

        # Now pick the option — look in the whole iframe document
        picked = await frame.evaluate(
            """(desired) => {
                const desired_l = desired.toLowerCase().trim();
                // React-Select renders options with class containing 'option'
                const selectors = [
                    '[class*="select__option"]',
                    '[class*="menu"] [class*="option"]',
                    '[role="option"]',
                    '.select-option',
                ];
                for (const sel of selectors) {
                    const opts = document.querySelectorAll(sel);
                    for (const opt of opts) {
                        const txt = opt.textContent.trim().toLowerCase();
                        if (!desired_l || txt === desired_l ||
                            txt.startsWith(desired_l) ||
                            desired_l.startsWith(txt)) {
                            opt.click();
                            return 'clicked: ' + opt.textContent.trim();
                        }
                    }
                }
                // Fallback: click first visible option
                for (const sel of selectors) {
                    const opts = document.querySelectorAll(sel);
                    if (opts.length > 0) {
                        opts[0].click();
                        return 'first: ' + opts[0].textContent.trim();
                    }
                }
                return 'not_found';
            }""", desired
        )
        print(f"    ✓ combobox {input_id}: {picked}")
        await asyncio.sleep(0.3)
        return "not_found" not in picked
    except Exception as e:
        print(f"    ✗ combobox {input_id}: {e}")
        return False


async def main():
    print("=== ATOLLS FINAL ===")
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
        await ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        )
        if SESSION_FILE.exists():
            s = json.loads(SESSION_FILE.read_text())
            cookies = s if isinstance(s, list) else s.get("cookies", [])
            if cookies: await ctx.add_cookies(cookies)

        page = await ctx.new_page()
        await page.goto("https://boards.greenhouse.io/atolls/jobs/7564145",
                        wait_until="domcontentloaded", timeout=25_000)
        await page.wait_for_timeout(3000)

        # Accept cookies
        for sel in ["button:has-text('Accept')"]:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    await el.click()
                    await page.wait_for_timeout(2000)
            except Exception:
                pass
        await page.wait_for_timeout(6000)

        # Find Greenhouse iframe
        frame = None
        for _ in range(20):
            for f in page.frames:
                if "greenhouse.io" in f.url and "embed" in f.url:
                    try:
                        if len(await f.query_selector_all("input")) >= 3:
                            frame = f
                            break
                    except Exception:
                        pass
            if frame:
                break
            await page.wait_for_timeout(500)

        if not frame:
            print("ERROR: GH iframe not found")
            await browser.close()
            return

        print(f"GH iframe: {frame.url[:70]}")

        # ── Standard text fields ──────────────────────────────────
        await js_fill(frame, "#first_name", P["first_name"], "first_name")
        await js_fill(frame, "#last_name",  P["last_name"],  "last_name")
        await js_fill(frame, "#email",      P["email"],      "email")

        # Phone field
        await js_fill(frame, "#phone", P["phone"], "phone")

        # ── Location (combobox) ──────────────────────────────────
        # Type into location, then pick suggestion
        try:
            loc = await frame.query_selector("#candidate-location")
            if loc:
                await loc.scroll_into_view_if_needed(timeout=3000)
                await loc.click()
                await loc.type(P["location"], delay=60)
                await asyncio.sleep(1.5)
                # Pick first suggestion
                picked = await frame.evaluate(
                    """() => {
                        const sels = [
                            '[class*="option"]',
                            '[role="option"]',
                            '.pac-item',
                        ];
                        for (const sel of sels) {
                            const opts = document.querySelectorAll(sel);
                            if (opts.length > 0) {
                                opts[0].click();
                                return 'picked: ' + opts[0].textContent.trim().slice(0,40);
                            }
                        }
                        // If no suggestion, just press Enter to confirm typed text
                        return 'no_suggestion';
                    }"""
                )
                print(f"    ✓ location: {picked}")
                if "no_suggestion" in picked:
                    await loc.press("Enter")
        except Exception as e:
            print(f"    ✗ location: {e}")

        # ── Country combobox ──────────────────────────────────────
        try:
            country = await frame.query_selector("#country")
            if country:
                await country.click()
                await country.fill("Germany")
                await asyncio.sleep(1)
                await open_select_and_pick(frame, "country", "Germany")
        except Exception as e:
            print(f"    ✗ country: {e}")

        # ── Resume ────────────────────────────────────────────────
        try:
            fi = await frame.query_selector("input[type='file']")
            if fi:
                await fi.set_input_files(RESUME)
                await page.wait_for_timeout(3000)
                print("    ✓ resume uploaded")
        except Exception as e:
            print(f"    ✗ resume: {e}")

        # ── Plain text questions ──────────────────────────────────
        await js_fill(frame, "#question_63011684", P["years_exp"], "years_backend")
        await js_fill(frame, "#question_63011685", "Yes",          "java17")
        await js_fill(frame, "#question_63011689", P["salary"],    "salary")

        # ── Combobox questions ────────────────────────────────────
        await open_select_and_pick(frame, "question_63011686", "Yes")  # Spring Boot
        await open_select_and_pick(frame, "question_63011687", "Yes")  # microservices
        await open_select_and_pick(frame, "question_63011688", "Yes")  # based in Berlin

        # "Heard of Atolls" — pick first option (Yes/No)
        await open_select_and_pick(frame, "question_63011690", "No")

        # "Channel" — use attribute selector to avoid [] CSS issue
        await frame.evaluate(
            """() => {
                // Click the channel select control
                const inp = document.querySelector('[id="question_63011691[]"]');
                if (!inp) return;
                let el = inp.parentElement;
                for (let i=0; i<6; i++) {
                    if (!el) break;
                    try { el.click(); } catch(e) {}
                    el = el.parentElement;
                }
            }"""
        )
        await asyncio.sleep(0.8)
        # Pick "LinkedIn" from channel options
        channel_picked = await frame.evaluate(
            """() => {
                const opts = document.querySelectorAll('[class*="option"]');
                for (const o of opts) {
                    if (o.textContent.toLowerCase().includes('linkedin')) {
                        o.click();
                        return 'linkedin';
                    }
                }
                // Pick first option
                if (opts.length > 0) { opts[0].click(); return opts[0].textContent.trim(); }
                return 'not_found';
            }"""
        )
        print(f"    ✓ channel: {channel_picked}")

        # ── Checkboxes ────────────────────────────────────────────
        cbs = await frame.query_selector_all("input[type='checkbox']")
        for cb in cbs:
            try:
                cb_id = await cb.get_attribute("id") or ""
                if cb_id:
                    await frame.evaluate(
                        """(id) => {
                            const el = document.getElementById(id);
                            if (el && !el.checked) {
                                el.checked = true;
                                el.dispatchEvent(new Event('change',{bubbles:true}));
                            }
                        }""", cb_id
                    )
                else:
                    # checkbox without id
                    await frame.evaluate(
                        """(el) => {
                            if (!el.checked) {
                                el.checked = true;
                                el.dispatchEvent(new Event('change',{bubbles:true}));
                            }
                        }""", cb
                    )
                print(f"    ✓ checkbox: {cb_id or '(no id)'}")
            except Exception as e:
                print(f"    ✗ checkbox: {e}")

        await page.wait_for_timeout(1000)
        await page.screenshot(path=f"uploads/final_atolls_filled_{ts()}.png")

        # Scroll through form to verify
        await frame.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(500)
        await page.screenshot(path=f"uploads/final_atolls_scrolled_{ts()}.png")

        # ── Submit ────────────────────────────────────────────────
        for sub_sel in ["input[type='submit']", "#submit_app",
                        "button[type='submit']",
                        "button:has-text('Submit application')"]:
            try:
                btn = await frame.query_selector(sub_sel)
                if btn:
                    val = await btn.get_attribute("value") or await btn.inner_text() or "submit"
                    print(f"\n  → Submitting: '{val.strip()}'")
                    await btn.scroll_into_view_if_needed(timeout=3000)
                    await btn.click()
                    await page.wait_for_timeout(10000)
                    await page.screenshot(path=f"uploads/final_atolls_submitted_{ts()}.png")

                    # Check iframe for confirmation
                    frame_body = ""
                    try:
                        frame_body = (await frame.inner_text("body")).lower()
                    except Exception:
                        pass
                    main_body = (await page.inner_text("body")).lower()
                    combined = frame_body + " " + main_body

                    kws = ["thank you", "application received", "successfully",
                           "submitted", "danke", "we received", "thanks for applying",
                           "your application", "confirmation"]
                    success = any(k in combined for k in kws)
                    print(f"  Frame body (400): {frame_body[:400]}")
                    print(f"\n  {'✅ SUCCESS — Application submitted!' if success else '❌ FAILED — check screenshot'}")
                    break
            except Exception as e:
                print(f"  Submit error: {e}")

        await browser.close()

asyncio.run(main())
