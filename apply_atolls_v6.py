"""
Atolls v6 — keyboard navigation for React-Select dropdowns.
Click indicator → type filter → ArrowDown → Enter to select.
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
    try:
        el = await frame.query_selector(sel)
        if not el:
            print(f"    ✗ {lbl}: not found"); return
        await frame.evaluate(
            """([s,v])=>{const e=document.querySelector(s);if(!e)return;
               const p=e.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;
               const d=Object.getOwnPropertyDescriptor(p,'value');
               if(d?.set)d.set.call(e,v);else e.value=v;
               ['input','change','blur'].forEach(t=>e.dispatchEvent(new Event(t,{bubbles:true})));
            }""", [sel, val])
        await el.click(click_count=3)
        await el.fill(val)
        print(f"    ✓ {lbl or sel}: '{val[:40]}'")
    except Exception as e:
        print(f"    ✗ {lbl or sel}: {e}")


async def select_by_keyboard(frame: Frame, input_id: str, option_text: str) -> bool:
    """
    Open React-Select by clicking its dropdown indicator, then use keyboard
    to filter and select the option.
    """
    id_attr = input_id  # may contain []
    sel = f'[id="{id_attr}"]'

    try:
        # First: click the dropdown indicator (▼) within the same container
        result = await frame.evaluate(
            """([id_attr]) => {
                const inp = document.querySelector('[id="' + id_attr + '"]');
                if (!inp) return 'no_input';

                // Walk up to find the select container
                let container = inp;
                for (let i = 0; i < 8; i++) {
                    if (!container.parentElement) break;
                    container = container.parentElement;
                    const cls = container.className || '';
                    if (cls.includes('input-wrapper') || cls.includes('select__container')) break;
                }

                // Find the dropdown indicator within this container
                const indicators = container.querySelectorAll(
                    '[class*="indicator"], [class*="Indicator"]'
                );
                let indicator = null;
                for (const ind of indicators) {
                    if (!ind.className.includes('clear')) {
                        indicator = ind;
                        break;
                    }
                }

                if (indicator) {
                    indicator.dispatchEvent(new MouseEvent('mousedown', {bubbles:true}));
                    indicator.click();
                    return 'indicator_clicked';
                }
                // Fallback: mousedown on the control
                const control = container.querySelector('[class*="select__control"]') ||
                                 container.querySelector('[class*="__control"]');
                if (control) {
                    control.dispatchEvent(new MouseEvent('mousedown', {bubbles:true}));
                    control.click();
                    return 'control_clicked';
                }
                inp.click();
                return 'input_clicked';
            }""", [id_attr]
        )
        print(f"    open ({id_attr}): {result}")
        await asyncio.sleep(0.8)

        # Take a quick snapshot to see if options appeared
        options_visible = await frame.evaluate(
            """(desired) => {
                const opts = document.querySelectorAll('[class*="option"]');
                const vis = Array.from(opts).filter(o=>{
                    const r=o.getBoundingClientRect();return r.width>0&&r.height>0;
                });
                return vis.map(o=>o.textContent.trim().slice(0,30));
            }""", option_text
        )
        print(f"    visible options: {options_visible[:5]}")

        if options_visible:
            # Click the matching option
            picked = await frame.evaluate(
                """(desired) => {
                    const dl = desired.toLowerCase().trim();
                    const opts = document.querySelectorAll('[class*="option"]');
                    const vis = Array.from(opts).filter(o=>{
                        const r=o.getBoundingClientRect();return r.width>0&&r.height>0;
                    });
                    for (const o of vis) {
                        const t = o.textContent.trim().toLowerCase();
                        if (t === dl || t.startsWith(dl)) { o.click(); return 'match:'+o.textContent.trim(); }
                    }
                    if (vis.length > 0) { vis[0].click(); return 'first:'+vis[0].textContent.trim(); }
                    return 'no_match';
                }""", option_text
            )
            print(f"    ✓ {id_attr}: {picked}")
            await asyncio.sleep(0.3)
            # Close the dropdown
            await frame.evaluate("document.body.click()")
            await asyncio.sleep(0.2)
            return True

        # No options visible via click — try keyboard approach
        print(f"    No options via click, trying keyboard on {id_attr}")
        el = await frame.query_selector(sel)
        if el:
            await el.click()
            await asyncio.sleep(0.3)
            # Type to filter
            await el.type(option_text[:3], delay=50)
            await asyncio.sleep(0.5)
            options_after_type = await frame.evaluate(
                """(desired) => {
                    const opts = document.querySelectorAll('[class*="option"]');
                    const vis = Array.from(opts).filter(o=>{
                        const r=o.getBoundingClientRect();return r.width>0&&r.height>0;
                    });
                    return vis.map(o=>o.textContent.trim().slice(0,30));
                }""", option_text
            )
            print(f"    after typing: {options_after_type[:5]}")
            if options_after_type:
                picked = await frame.evaluate(
                    """(desired) => {
                        const dl = desired.toLowerCase().trim();
                        const opts = document.querySelectorAll('[class*="option"]');
                        const vis = Array.from(opts).filter(o=>{
                            const r=o.getBoundingClientRect();return r.width>0&&r.height>0;
                        });
                        for (const o of vis) {
                            const t = o.textContent.trim().toLowerCase();
                            // Exact match or starts-with match (not contains, to avoid Algeria matching Germany)
                            if (t === dl || t.startsWith(dl) || (dl.length >= 4 && t.startsWith(dl.slice(0,5)))) {
                                o.click(); return 'type_match:'+o.textContent.trim();
                            }
                        }
                        // Second pass: contains match if no starts-with found
                        for (const o of vis) {
                            const t = o.textContent.trim().toLowerCase();
                            if (t.includes(dl)) { o.click(); return 'contains:'+o.textContent.trim(); }
                        }
                        if (vis.length > 0) { vis[0].click(); return 'type_first:'+vis[0].textContent.trim(); }
                        return 'no_match';
                    }""", option_text
                )
                print(f"    ✓ {id_attr} (keyboard): {picked}")
                await frame.evaluate("document.body.click()")
                return True
            # Last resort: ArrowDown + Enter
            await el.press("ArrowDown")
            await asyncio.sleep(0.3)
            await el.press("Enter")
            print(f"    ✓ {id_attr} (arrow+enter)")
            return True

    except Exception as e:
        print(f"    ✗ {id_attr}: {e}")
    return False


async def main():
    print("=== ATOLLS v6 ===")
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
                    await el.click(); await page.wait_for_timeout(2000)
            except Exception: pass
        await page.wait_for_timeout(6000)

        frame = None
        for _ in range(25):
            for f in page.frames:
                if "greenhouse.io" in f.url and "embed" in f.url:
                    try:
                        if len(await f.query_selector_all("input")) >= 3:
                            frame = f; break
                    except Exception: pass
            if frame: break
            await page.wait_for_timeout(500)
        if not frame:
            print("ERROR"); await browser.close(); return
        print("GH iframe ready")

        # Screenshot what the dropdowns look like when opened
        print("\n--- Debugging dropdowns ---")
        await page.screenshot(path=f"uploads/v6_initial_{ts()}.png")

        # Open Spring Boot dropdown and screenshot options
        await frame.evaluate(
            """() => {
                const inp = document.getElementById('question_63011686');
                if (!inp) return;
                let c = inp.parentElement;
                for(let i=0;i<8;i++){
                    if(!c.parentElement) break;
                    c = c.parentElement;
                    if((c.className||'').includes('input-wrapper')) break;
                }
                const ind = c.querySelector('[class*="indicator"]:not([class*="clear"])');
                if(ind) ind.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));
                const ctrl = c.querySelector('[class*="control"]');
                if(ctrl) ctrl.click();
            }"""
        )
        await asyncio.sleep(1)
        await page.screenshot(path=f"uploads/v6_dropdown_open_{ts()}.png")

        # Check what's visible
        visible = await frame.evaluate(
            """() => {
                const opts = document.querySelectorAll('[class*="option"]');
                const vis = Array.from(opts).filter(o=>{
                    const r=o.getBoundingClientRect();return r.width>0&&r.height>0;
                });
                return vis.map(o=>({text:o.textContent.trim(),cls:o.className.slice(0,50)}));
            }"""
        )
        print(f"Visible options when Spring Boot dropdown opened: {visible}")

        # Also check what ALL option-like elements exist
        all_opts = await frame.evaluate(
            """() => {
                return Array.from(document.querySelectorAll('[class*="option"], [role="option"]'))
                    .map(o=>({text:o.textContent.trim().slice(0,30),
                              cls:o.className.slice(0,40),
                              visible: o.getBoundingClientRect().width > 0}));
            }"""
        )
        print(f"All options in frame: {all_opts[:10]}")

        # Close dropdown
        await frame.evaluate("document.body.click()")
        await asyncio.sleep(0.5)

        # ── Fill form ─────────────────────────────────────────────
        await fill_text(frame, "#first_name", P["first_name"], "first_name")
        await fill_text(frame, "#last_name",  P["last_name"],  "last_name")
        await fill_text(frame, "#email",      P["email"],      "email")
        await fill_text(frame, "#phone",      P["phone"],      "phone")

        # Location
        loc = await frame.query_selector("#candidate-location")
        if loc:
            await loc.click()
            await loc.type(P["location"], delay=80)
            await asyncio.sleep(2)
            first_opt = await frame.evaluate(
                """() => {
                    const opts = document.querySelectorAll('[class*="option"]');
                    const vis = Array.from(opts).filter(o=>{
                        const r=o.getBoundingClientRect(); return r.width>0&&r.height>0;
                    });
                    if(vis.length>0){vis[0].click();return vis[0].textContent.trim();}
                    return 'none';
                }"""
            )
            print(f"    ✓ location: {first_opt}")
            if first_opt == "none":
                await loc.press("ArrowDown")
                await loc.press("Enter")
            await frame.evaluate("document.body.click()")
            await asyncio.sleep(0.3)

        # Country
        await select_by_keyboard(frame, "country", "Germany")

        # Resume
        fi = await frame.query_selector("input[type='file']")
        if fi:
            await fi.set_input_files(RESUME)
            await page.wait_for_timeout(3000)
            print("    ✓ resume")

        # Text questions
        await fill_text(frame, "#question_63011684", P["years_exp"], "years")
        await fill_text(frame, "#question_63011685", "Yes",          "java17")
        await fill_text(frame, "#question_63011689", P["salary"],    "salary")

        # Combobox questions
        await select_by_keyboard(frame, "question_63011686", "Yes")   # Spring Boot
        await select_by_keyboard(frame, "question_63011687", "Yes")   # microservices
        await select_by_keyboard(frame, "question_63011688", "Yes")   # in Berlin
        await select_by_keyboard(frame, "question_63011690", "No")    # heard of Atolls
        await select_by_keyboard(frame, "question_63011691[]", "LinkedIn")  # channel

        # Checkboxes — click the label (triggers React handler) or the checkbox itself
        cbs = await frame.query_selector_all("input[type='checkbox']")
        for cb in cbs:
            try:
                cb_id = await cb.get_attribute("id") or ""
                # Click the associated label first (React listens on label)
                if cb_id:
                    clicked_label = await frame.evaluate(
                        """(id) => {
                            const lbl = document.querySelector('label[for="' + id + '"]');
                            if (lbl) { lbl.click(); return 'label'; }
                            // Fallback: click the input
                            const cb = document.getElementById(id);
                            if (cb) { cb.click(); return 'input'; }
                            return 'not_found';
                        }""", cb_id
                    )
                    print(f"    ✓ checkbox {cb_id}: {clicked_label}")
                else:
                    # No id — click the checkbox directly
                    await cb.evaluate("el => el.click()")
                    print("    ✓ checkbox (no id): clicked")
                await asyncio.sleep(0.2)
            except Exception as e:
                print(f"    ✗ checkbox: {e}")
        print("    ✓ all checkboxes")

        await page.wait_for_timeout(500)
        await frame.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(500)
        await page.screenshot(path=f"uploads/v6_prefilled_{ts()}.png")

        # Submit
        for sub_sel in ["input[type='submit']", "#submit_app",
                        "button[type='submit']", "button:has-text('Submit application')"]:
            try:
                btn = await frame.query_selector(sub_sel)
                if btn:
                    val = await btn.get_attribute("value") or await btn.inner_text() or "submit"
                    print(f"\n  → Submitting: '{val.strip()}'")
                    await btn.scroll_into_view_if_needed(timeout=3000)
                    await btn.click()
                    await page.wait_for_timeout(10000)
                    await page.screenshot(path=f"uploads/v6_submitted_{ts()}.png")
                    frame_body = ""
                    try: frame_body = (await frame.inner_text("body")).lower()
                    except Exception: pass
                    page_body = ""
                    try: page_body = (await page.inner_text("body")).lower()
                    except Exception: pass
                    combined = frame_body + " " + page_body
                    kws = ["thank you","application received","successfully","submitted",
                           "danke","we received","thanks for applying","your application"]
                    ok = any(k in combined for k in kws)
                    print(f"  Frame: {frame_body[:300]}")
                    print(f"\n  {'✅ SUCCESS!' if ok else '❌ FAILED'}")
                    break
            except Exception as e:
                print(f"  Submit err: {e}")

        await browser.close()

asyncio.run(main())
