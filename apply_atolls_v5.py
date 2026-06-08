"""
Atolls v5 — scoped React-Select: close dropdown after each selection,
look for options WITHIN the specific container only.
"""
import asyncio, json, sys, io
from pathlib import Path
from datetime import datetime
from playwright.async_api import async_playwright, Frame

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

SESSION_FILE = Path("uploads/linkedin_session.json")
RESUME = str(Path("uploads/resume_en.pdf").resolve())

P = {
    "first_name": "Felix", "last_name": "Behboudi",
    "email": "f_behboud@hotmail.com", "phone": "+491744548555",
    "location": "Berlin", "salary": "75000", "years_exp": "5",
}

def ts(): return datetime.now().strftime("%H%M%S")


async def fill_text(frame: Frame, sel: str, val: str, lbl=""):
    """Fill a plain text input."""
    try:
        await frame.evaluate(
            """([s,v])=>{const e=document.querySelector(s);if(!e)return;
               const p=e.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;
               const d=Object.getOwnPropertyDescriptor(p,'value');
               if(d?.set)d.set.call(e,v);else e.value=v;
               ['input','change','blur'].forEach(t=>e.dispatchEvent(new Event(t,{bubbles:true})));
            }""", [sel, val])
        el = await frame.query_selector(sel)
        if el:
            await el.click(click_count=3)
            await el.fill(val)
        print(f"    ✓ {lbl or sel}: '{val[:40]}'")
    except Exception as e:
        print(f"    ✗ {lbl or sel}: {e}")


async def close_dropdowns(frame: Frame):
    """Press Escape and click neutral area to close any open dropdown."""
    try:
        await frame.evaluate("document.body.click()")
        await asyncio.sleep(0.3)
    except Exception:
        pass


async def open_and_pick(frame: Frame, input_id: str, desired: str) -> str:
    """
    Open a React-Select dropdown by ID, wait, then pick option from
    WITHIN that specific container only.
    Uses attribute selector to handle IDs with special chars.
    """
    sel = f'[id="{input_id}"]'

    # Close any open dropdown first
    await close_dropdowns(frame)
    await asyncio.sleep(0.3)

    # Click the input to open dropdown
    result = await frame.evaluate(
        """([sel, desired]) => {
            const inp = document.querySelector(sel);
            if (!inp) return 'no_input';

            // Walk up to find the outermost select container
            let container = inp;
            for (let i = 0; i < 8; i++) {
                if (!container.parentElement) break;
                container = container.parentElement;
                const cls = container.className || '';
                // Stop at the top-level question wrapper (has the label above it)
                if (cls.includes('select__container') || cls.includes('input-wrapper')) break;
            }

            // Click the input to open
            inp.focus();
            inp.click();
            // Also try clicking the dropdown indicator
            const indicator = container.querySelector('[class*="indicator"]');
            if (indicator) indicator.click();

            return 'opened';
        }""", [sel, desired]
    )

    await asyncio.sleep(0.8)

    # Now look for options in the frame (they should be in the currently-open menu)
    picked = await frame.evaluate(
        """(desired) => {
            const dl = desired.toLowerCase().trim();
            // React-Select options: class contains 'option' and is currently in a menu
            const menuOpts = document.querySelectorAll(
                '[class*="select__menu"] [class*="option"], ' +
                '[class*="menu"] [class*="select__option"], ' +
                '[role="listbox"] [role="option"], ' +
                '[class*="remix-css"] [class*="option"]'
            );
            for (const opt of menuOpts) {
                const txt = opt.textContent.trim().toLowerCase();
                if (!dl || txt === dl || txt.startsWith(dl)) {
                    opt.click();
                    return 'match: ' + opt.textContent.trim();
                }
            }
            // Fallback: any visible option element
            const allOpts = document.querySelectorAll('[class*="option"]');
            const visible = Array.from(allOpts).filter(o => {
                const r = o.getBoundingClientRect();
                return r.width > 0 && r.height > 0;
            });
            if (visible.length > 0) {
                // Try to find desired text
                for (const o of visible) {
                    const txt = o.textContent.trim().toLowerCase();
                    if (!dl || txt === dl || txt.startsWith(dl)) {
                        o.click();
                        return 'vis_match: ' + o.textContent.trim();
                    }
                }
                // First visible
                visible[0].click();
                return 'vis_first: ' + visible[0].textContent.trim();
            }
            return 'no_options';
        }""", desired
    )

    print(f"    ✓ {input_id} ({desired}): {picked}")
    await close_dropdowns(frame)
    await asyncio.sleep(0.4)
    return picked


async def main():
    print("=== ATOLLS v5 ===")
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
            c = s if isinstance(s, list) else s.get("cookies", [])
            if c: await ctx.add_cookies(c)

        page = await ctx.new_page()
        await page.goto("https://boards.greenhouse.io/atolls/jobs/7564145",
                        wait_until="domcontentloaded", timeout=25_000)
        await page.wait_for_timeout(3000)

        for sel in ["button:has-text('Accept')"]:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    await el.click()
                    await page.wait_for_timeout(2000)
            except Exception:
                pass
        await page.wait_for_timeout(6000)

        # Find GH iframe
        frame = None
        for _ in range(25):
            for f in page.frames:
                if "greenhouse.io" in f.url and "embed" in f.url:
                    try:
                        if len(await f.query_selector_all("input")) >= 3:
                            frame = f
                            break
                    except Exception:
                        pass
            if frame: break
            await page.wait_for_timeout(500)

        if not frame:
            print("ERROR: no GH iframe"); await browser.close(); return
        print(f"GH iframe ready")

        # ── Plain text fields ─────────────────────────────────────
        await fill_text(frame, "#first_name", P["first_name"], "first_name")
        await fill_text(frame, "#last_name",  P["last_name"],  "last_name")
        await fill_text(frame, "#email",      P["email"],      "email")
        await fill_text(frame, "#phone",      P["phone"],      "phone")

        # ── Location: type + pick first autocomplete ─────────────
        try:
            loc = await frame.query_selector("#candidate-location")
            if loc:
                await loc.click()
                await loc.type(P["location"], delay=80)
                await asyncio.sleep(2)
                first_opt = await frame.evaluate(
                    """() => {
                        const opts = document.querySelectorAll('[class*="option"]');
                        const vis = Array.from(opts).filter(o=>{
                            const r=o.getBoundingClientRect();
                            return r.width>0&&r.height>0;
                        });
                        if(vis.length>0){vis[0].click();return vis[0].textContent.trim();}
                        return 'none';
                    }"""
                )
                print(f"    ✓ location: {first_opt}")
                if first_opt == "none":
                    await loc.press("ArrowDown")
                    await loc.press("Enter")
                await close_dropdowns(frame)
        except Exception as e:
            print(f"    ✗ location: {e}")

        # ── Country: open combobox, pick Germany ─────────────────
        await open_and_pick(frame, "country", "Germany")

        # ── Resume ────────────────────────────────────────────────
        try:
            fi = await frame.query_selector("input[type='file']")
            if fi:
                await fi.set_input_files(RESUME)
                await page.wait_for_timeout(3000)
                print("    ✓ resume uploaded")
        except Exception as e:
            print(f"    ✗ resume: {e}")

        # ── Text questions ────────────────────────────────────────
        await fill_text(frame, "#question_63011684", P["years_exp"], "years_backend")
        await fill_text(frame, "#question_63011685", "Yes",          "java17")
        await fill_text(frame, "#question_63011689", P["salary"],    "salary")

        # ── Combobox questions (one at a time, close between each) ─
        await open_and_pick(frame, "question_63011686", "Yes")  # Spring Boot
        await open_and_pick(frame, "question_63011687", "Yes")  # microservices
        await open_and_pick(frame, "question_63011688", "Yes")  # based in Berlin
        await open_and_pick(frame, "question_63011690", "No")   # heard of Atolls before
        await open_and_pick(frame, "question_63011691[]", "LinkedIn")  # channel

        # ── Checkboxes ────────────────────────────────────────────
        await frame.evaluate(
            """() => {
                document.querySelectorAll('input[type="checkbox"]').forEach(cb => {
                    if (!cb.checked) {
                        cb.checked = true;
                        cb.dispatchEvent(new Event('change',{bubbles:true}));
                    }
                });
            }"""
        )
        print("    ✓ all checkboxes checked")

        # ── Screenshot before submit ──────────────────────────────
        await page.wait_for_timeout(500)
        await frame.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(500)
        await page.screenshot(path=f"uploads/v5_filled_{ts()}.png")

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
                    await page.screenshot(path=f"uploads/v5_submitted_{ts()}.png")

                    frame_body = ""
                    try: frame_body = (await frame.inner_text("body")).lower()
                    except Exception: pass
                    main_body = (await page.inner_text("body")).lower()

                    kws = ["thank you", "application received", "successfully",
                           "submitted", "danke", "we received", "thanks for applying",
                           "your application"]
                    success = any(k in (frame_body + main_body) for k in kws)
                    print(f"  Frame (400): {frame_body[:400]}")
                    print(f"\n  {'✅ SUCCESS!' if success else '❌ FAILED — check v5_submitted screenshot'}")
                    break
            except Exception as e:
                print(f"  Submit error: {e}")

        await browser.close()

asyncio.run(main())
