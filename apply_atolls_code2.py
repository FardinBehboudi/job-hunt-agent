"""
Atolls: fill form → submit → wait for code input → enter Yet8mbO3 → final submit.
"""
import asyncio, json, sys, io
from pathlib import Path
from datetime import datetime
from playwright.async_api import async_playwright, Frame

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
SESSION_FILE = Path("uploads/linkedin_session.json")
RESUME = str(Path("uploads/resume_en.pdf").resolve())
CODE = "Yet8mbO3"

P = {"first_name":"Felix","last_name":"Behboudi","email":"f_behboud@hotmail.com",
     "phone":"+491744548555","location":"Berlin","salary":"75000","years_exp":"5"}

def ts(): return datetime.now().strftime("%H%M%S")

async def fill_text(frame, sel, val, lbl=""):
    try:
        await frame.evaluate("""([s,v])=>{const e=document.querySelector(s);if(!e)return;
            const p=e.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;
            const d=Object.getOwnPropertyDescriptor(p,'value');
            if(d?.set)d.set.call(e,v);else e.value=v;
            ['input','change','blur'].forEach(t=>e.dispatchEvent(new Event(t,{bubbles:true})));
        }""", [sel, val])
        el = await frame.query_selector(sel)
        if el: await el.click(click_count=3); await el.fill(val)
        print(f"    ✓ {lbl}: '{val[:40]}'")
    except Exception as e: print(f"    ✗ {lbl}: {e}")

async def select_dd(frame, input_id, option):
    try:
        await frame.evaluate("""([id])=>{
            const inp=document.querySelector('[id="'+id+'"]');if(!inp)return;
            let c=inp;for(let i=0;i<8;i++){if(!c.parentElement)break;c=c.parentElement;
                if((c.className||'').includes('input-wrapper'))break;}
            const ind=c.querySelector('[class*="indicator"]:not([class*="clear"])');
            if(ind)ind.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));
            const ctrl=c.querySelector('[class*="control"]');if(ctrl)ctrl.click();
        }""", [input_id])
        await asyncio.sleep(0.8)
        el = await frame.query_selector(f'[id="{input_id}"]')
        if el:
            await el.click(); await el.type(option[:3], delay=50); await asyncio.sleep(0.5)
            picked = await frame.evaluate("""(dl)=>{
                dl=dl.toLowerCase().trim();
                const vis=Array.from(document.querySelectorAll('[class*="option"]'))
                    .filter(o=>{const r=o.getBoundingClientRect();return r.width>0&&r.height>0;});
                for(const o of vis){const t=o.textContent.trim().toLowerCase();
                    if(t===dl||t.startsWith(dl)||(dl.length>=4&&t.startsWith(dl.slice(0,5)))){o.click();return 'ok:'+o.textContent.trim();}}
                for(const o of vis){const t=o.textContent.trim().toLowerCase();
                    if(t.includes(dl)){o.click();return 'inc:'+o.textContent.trim();}}
                if(vis.length>0){vis[0].click();return 'first:'+vis[0].textContent.trim();}
                return 'none';
            }""", option)
            print(f"    ✓ {input_id}: {picked}")
        await frame.evaluate("document.body.click()"); await asyncio.sleep(0.3)
    except Exception as e: print(f"    ✗ {input_id}: {e}")

async def main():
    print(f"=== ATOLLS COMPLETE — code: {CODE} ===")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False,
            args=["--disable-blink-features=AutomationControlled","--start-maximized"])
        ctx = await browser.new_context(viewport={"width":1366,"height":768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
            locale="en-US", timezone_id="Europe/Berlin")
        await ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        if SESSION_FILE.exists():
            s = json.loads(SESSION_FILE.read_text())
            c = s if isinstance(s,list) else s.get("cookies",[])
            if c: await ctx.add_cookies(c)

        page = await ctx.new_page()
        await page.goto("https://boards.greenhouse.io/atolls/jobs/7564145",
                        wait_until="domcontentloaded", timeout=25_000)
        await page.wait_for_timeout(3000)
        for sel in ["button:has-text('Accept')"]:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible(): await el.click(); await page.wait_for_timeout(2000)
            except Exception: pass
        await page.wait_for_timeout(6000)

        frame = None
        for _ in range(25):
            for f in page.frames:
                if "greenhouse.io" in f.url and "embed" in f.url:
                    try:
                        if len(await f.query_selector_all("input")) >= 3: frame = f; break
                    except Exception: pass
            if frame: break
            await page.wait_for_timeout(500)
        if not frame: print("ERROR: no frame"); await browser.close(); return
        print("GH iframe ready")

        # Fill form
        await fill_text(frame, "#first_name", P["first_name"], "first_name")
        await fill_text(frame, "#last_name",  P["last_name"],  "last_name")
        await fill_text(frame, "#email",      P["email"],      "email")
        await fill_text(frame, "#phone",      P["phone"],      "phone")

        loc = await frame.query_selector("#candidate-location")
        if loc:
            await loc.click(); await loc.type(P["location"], delay=80); await asyncio.sleep(2)
            await frame.evaluate("""()=>{const vis=Array.from(document.querySelectorAll('[class*="option"]'))
                .filter(o=>{const r=o.getBoundingClientRect();return r.width>0&&r.height>0;});
                if(vis.length>0)vis[0].click();}""")
            await frame.evaluate("document.body.click()"); await asyncio.sleep(0.3)
            print("    ✓ location: Berlin")

        await select_dd(frame, "country", "Germany")

        fi = await frame.query_selector("input[type='file']")
        if fi: await fi.set_input_files(RESUME); await page.wait_for_timeout(3000); print("    ✓ resume")

        await fill_text(frame, "#question_63011684", P["years_exp"], "years")
        await fill_text(frame, "#question_63011685", "Yes",          "java17")
        await fill_text(frame, "#question_63011689", P["salary"],    "salary")
        await select_dd(frame, "question_63011686", "Yes")
        await select_dd(frame, "question_63011687", "Yes")
        await select_dd(frame, "question_63011688", "Yes")
        await select_dd(frame, "question_63011690", "No")
        await select_dd(frame, "question_63011691[]", "LinkedIn")

        for cb_id in ["question_63011692[]_621426174", "gdpr_processing_consent_given_1"]:
            await frame.evaluate("""(id)=>{
                const lbl=document.querySelector('label[for="'+id+'"]');
                if(lbl){lbl.click();return;}
                const cb=document.getElementById(id)||document.querySelector('[id="'+id+'"]');
                if(cb)cb.click();
            }""", cb_id); await asyncio.sleep(0.2)
        print("    ✓ checkboxes")

        await page.wait_for_timeout(500)
        await page.screenshot(path=f"uploads/c2_ready_{ts()}.png")

        # Submit #1 — correct selector
        sub = await frame.query_selector("button[type='submit']")
        if not sub:
            sub = await frame.query_selector("button:has-text('Submit application')")
        if sub:
            print(f"\n  → Submit #1...")
            await sub.scroll_into_view_if_needed(timeout=3000)
            await sub.click()
            await page.wait_for_timeout(8000)
            await page.screenshot(path=f"uploads/c2_after_submit1_{ts()}.png")
        else:
            print("  ❌ Submit button not found!"); await browser.close(); return

        # Look for the code input — it should appear now
        print(f"\n  → Looking for code input...")
        code_inp = None
        for _ in range(10):
            for code_sel in [
                "#security_code", "input[name='security_code']",
                "input[id*='code']", "input[name*='code']",
                "input[placeholder*='code' i]", "input[aria-label*='code' i]",
            ]:
                try:
                    el = await frame.query_selector(code_sel)
                    if el and await el.is_visible():
                        code_inp = el
                        print(f"    Found: {code_sel}")
                        break
                except Exception: pass
            if code_inp: break
            # Also check for any NEW visible inputs
            all_inp = await frame.query_selector_all("input[type='text'], input[type='number'], input:not([type])")
            for inp in all_inp:
                try:
                    if await inp.is_visible():
                        id_a = await inp.get_attribute("id") or ""
                        ph = await inp.get_attribute("placeholder") or ""
                        nm = await inp.get_attribute("name") or ""
                        if any(k in (id_a+ph+nm).lower() for k in ["code","verif","token","security","pin"]):
                            code_inp = inp
                            print(f"    Found by scan: id={id_a!r}")
                            break
                except Exception: pass
            if code_inp: break
            await page.wait_for_timeout(1000)

        if code_inp:
            print(f"  → Entering code: {CODE}")
            await code_inp.click()
            await code_inp.fill(CODE)
            await page.wait_for_timeout(1000)
            await page.screenshot(path=f"uploads/c2_code_entered_{ts()}.png")

            # Final submit
            sub2 = await frame.query_selector("button[type='submit'], input[type='submit']")
            if sub2:
                print("  → Final submit...")
                await sub2.click()
                await page.wait_for_timeout(10000)
                await page.screenshot(path=f"uploads/c2_final_{ts()}.png")

                fb = ""
                try: fb = (await frame.inner_text("body")).lower()
                except Exception: pass
                pb = ""
                try: pb = (await page.inner_text("body")).lower()
                except Exception: pass
                ok = any(k in (fb+pb) for k in ["thank you","application received","successfully",
                    "submitted","we received","thanks for applying","your application has been"])
                print(f"  Frame: {fb[:400]}")
                print(f"\n  {'✅ SUCCESS — Atolls submitted!' if ok else '❌ FAILED'}")
        else:
            # Code field not found — check what's on screen
            fb = ""
            try: fb = (await frame.inner_text("body")).lower()
            except Exception: pass
            print(f"  Code input not found. Frame body: {fb[:400]}")
            await page.screenshot(path=f"uploads/c2_no_code_field_{ts()}.png")

        await browser.close()

asyncio.run(main())
