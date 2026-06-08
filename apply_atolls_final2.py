"""
Atolls final2 — two browser pages in parallel:
- Page 1: fills Greenhouse form → triggers verification email
- Page 2: opens Outlook → reads verification code
- Page 1: enters code → submits
"""
import asyncio, json, re, sys, io, os
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from playwright.async_api import async_playwright, Page, Frame

load_dotenv()
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

SESSION_FILE = Path("uploads/linkedin_session.json")
RESUME = str(Path("uploads/resume_en.pdf").resolve())
EMAIL = "f_behboud@hotmail.com"
EMAIL_PASS = os.getenv("HOTMAIL_PASSWORD", "")

P = {
    "first_name": "Felix", "last_name": "Behboudi",
    "email": EMAIL, "phone": "+491744548555",
    "location": "Berlin", "salary": "75000", "years_exp": "5",
}

def ts(): return datetime.now().strftime("%H%M%S")


async def fill_text(frame: Frame, sel: str, val: str, lbl=""):
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
            await el.click(click_count=3); await el.fill(val)
        print(f"    ✓ {lbl}: '{val[:40]}'")
    except Exception as e:
        print(f"    ✗ {lbl}: {e}")


async def select_dropdown(frame: Frame, input_id: str, option: str):
    try:
        await frame.evaluate(
            """([id]) => {
                const inp = document.querySelector('[id="'+id+'"]');
                if(!inp) return;
                let c=inp;
                for(let i=0;i<8;i++){
                    if(!c.parentElement)break;c=c.parentElement;
                    if((c.className||'').includes('input-wrapper'))break;
                }
                const ind=c.querySelector('[class*="indicator"]:not([class*="clear"])');
                if(ind)ind.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));
                const ctrl=c.querySelector('[class*="control"]');
                if(ctrl)ctrl.click();
            }""", [input_id]
        )
        await asyncio.sleep(0.8)
        el = await frame.query_selector(f'[id="{input_id}"]')
        if el:
            await el.click()
            await el.type(option[:3], delay=50)
            await asyncio.sleep(0.5)
            picked = await frame.evaluate(
                """(desired) => {
                    const dl=desired.toLowerCase().trim();
                    const vis=Array.from(document.querySelectorAll('[class*="option"]'))
                        .filter(o=>{const r=o.getBoundingClientRect();return r.width>0&&r.height>0;});
                    for(const o of vis){
                        const t=o.textContent.trim().toLowerCase();
                        if(t===dl||t.startsWith(dl)||(dl.length>=4&&t.startsWith(dl.slice(0,5)))){
                            o.click();return 'ok:'+o.textContent.trim();}
                    }
                    for(const o of vis){
                        const t=o.textContent.trim().toLowerCase();
                        if(t.includes(dl)){o.click();return 'inc:'+o.textContent.trim();}
                    }
                    if(vis.length>0){vis[0].click();return 'first:'+vis[0].textContent.trim();}
                    return 'none';
                }""", option
            )
            print(f"    ✓ {input_id}: {picked}")
        await frame.evaluate("document.body.click()")
        await asyncio.sleep(0.3)
    except Exception as e:
        print(f"    ✗ {input_id}: {e}")


async def login_outlook(page: Page):
    """Log into Outlook web."""
    await page.goto("https://outlook.live.com/mail/0/inbox",
                   wait_until="domcontentloaded", timeout=30_000)
    await page.wait_for_timeout(3000)
    if "login" in page.url or "signup" in page.url or "account.microsoft" in page.url:
        for sel in ["input[type='email']", "#i0116"]:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    await el.fill(EMAIL); break
            except Exception: pass
        for sel in ["input[type='submit']", "#idSIButton9", "button:has-text('Next')"]:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    await el.click(); await page.wait_for_timeout(3000); break
            except Exception: pass
        for sel in ["input[type='password']", "#i0118"]:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    await el.fill(EMAIL_PASS); break
            except Exception: pass
        for sel in ["input[type='submit']", "#idSIButton9"]:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    await el.click(); await page.wait_for_timeout(5000); break
            except Exception: pass
        # Stay signed in -> No
        for sel in ["#idBtn_Back", "button:has-text('No')"]:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    await el.click(); await page.wait_for_timeout(3000); break
            except Exception: pass
    await page.wait_for_timeout(3000)
    print(f"  Outlook ready: {page.url[:60]}")


async def wait_for_code(outlook_page: Page, max_wait=120) -> str | None:
    """Poll Outlook for Greenhouse verification code."""
    print(f"  Polling Outlook for code (up to {max_wait}s)...")
    for attempt in range(max_wait // 10):
        # Try to refresh/reload inbox
        try:
            ref = await outlook_page.query_selector("[aria-label*='Refresh'], [title*='Refresh']")
            if ref: await ref.click(); await asyncio.sleep(1)
        except Exception: pass

        # Look for relevant emails
        body_txt = await outlook_page.inner_text("body")
        # Check for email subjects mentioning verification/application
        if any(k in body_txt.lower() for k in ["greenhouse", "atolls", "verif", "verification code"]):
            # Try to click the email
            for sel in [
                "div[role='option']:has-text('Atolls')",
                "div[role='option']:has-text('verification')",
                "div[role='option']:has-text('Greenhouse')",
                "div[role='listitem']:has-text('Atolls')",
                "[data-convid]",
            ]:
                try:
                    emails_found = await outlook_page.query_selector_all(sel)
                    for em in emails_found[:3]:
                        txt = await em.inner_text()
                        if any(k in txt.lower() for k in ["atolls", "greenhouse", "verif"]):
                            await em.click()
                            await asyncio.sleep(2)
                            email_body = await outlook_page.inner_text("body")
                            print(f"  Email body snippet: {email_body[:300]}")
                            for pat in [
                                r'code[:\s]+([A-Za-z0-9]{6,10})',
                                r'\b([A-Z0-9]{8})\b',
                                r'\b([0-9]{6,8})\b',
                            ]:
                                for m in re.findall(pat, email_body, re.I):
                                    if len(m) >= 6:
                                        print(f"  Found code: {m}")
                                        return m
                except Exception: pass

        print(f"  No code yet (attempt {attempt+1}), waiting 10s...")
        await asyncio.sleep(10)
    return None


async def main():
    print("=== ATOLLS FINAL WITH EMAIL VERIFICATION ===")
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
            c = s if isinstance(s, list) else s.get("cookies", [])
            if c: await ctx.add_cookies(c)

        # Open Outlook in background first
        outlook_page = await ctx.new_page()
        print("Opening Outlook...")
        await login_outlook(outlook_page)
        await outlook_page.screenshot(path=f"uploads/fa_outlook_{ts()}.png")

        # Open Atolls form
        atolls_page = await ctx.new_page()
        await atolls_page.goto("https://boards.greenhouse.io/atolls/jobs/7564145",
                               wait_until="domcontentloaded", timeout=25_000)
        await atolls_page.wait_for_timeout(3000)
        for sel in ["button:has-text('Accept')"]:
            try:
                el = await atolls_page.query_selector(sel)
                if el and await el.is_visible():
                    await el.click(); await atolls_page.wait_for_timeout(2000)
            except Exception: pass
        await atolls_page.wait_for_timeout(6000)

        frame = None
        for _ in range(25):
            for f in atolls_page.frames:
                if "greenhouse.io" in f.url and "embed" in f.url:
                    try:
                        if len(await f.query_selector_all("input")) >= 3:
                            frame = f; break
                    except Exception: pass
            if frame: break
            await atolls_page.wait_for_timeout(500)

        if not frame: print("ERROR: no GH frame"); await browser.close(); return
        print("GH iframe ready")

        # Fill all fields
        await fill_text(frame, "#first_name", P["first_name"], "first_name")
        await fill_text(frame, "#last_name",  P["last_name"],  "last_name")
        await fill_text(frame, "#email",      P["email"],      "email")
        await fill_text(frame, "#phone",      P["phone"],      "phone")

        loc = await frame.query_selector("#candidate-location")
        if loc:
            await loc.click(); await loc.type(P["location"], delay=80)
            await asyncio.sleep(2)
            await frame.evaluate(
                """() => {const vis=Array.from(document.querySelectorAll('[class*="option"]'))
                   .filter(o=>{const r=o.getBoundingClientRect();return r.width>0&&r.height>0;});
                   if(vis.length>0)vis[0].click();}"""
            )
            await frame.evaluate("document.body.click()"); await asyncio.sleep(0.3)
            print("    ✓ location: Berlin")

        await select_dropdown(frame, "country", "Germany")

        fi = await frame.query_selector("input[type='file']")
        if fi:
            await fi.set_input_files(RESUME); await atolls_page.wait_for_timeout(3000)
            print("    ✓ resume")

        await fill_text(frame, "#question_63011684", P["years_exp"], "years")
        await fill_text(frame, "#question_63011685", "Yes",          "java17")
        await fill_text(frame, "#question_63011689", P["salary"],    "salary")
        await select_dropdown(frame, "question_63011686", "Yes")
        await select_dropdown(frame, "question_63011687", "Yes")
        await select_dropdown(frame, "question_63011688", "Yes")
        await select_dropdown(frame, "question_63011690", "No")
        await select_dropdown(frame, "question_63011691[]", "LinkedIn")

        for cb_id in ["question_63011692[]_621426174", "gdpr_processing_consent_given_1"]:
            await frame.evaluate(
                """(id) => {
                    const lbl=document.querySelector('label[for="'+id+'"]');
                    if(lbl){lbl.click();return;}
                    const cb=document.getElementById(id)||document.querySelector('[id="'+id+'"]');
                    if(cb)cb.click();
                }""", cb_id
            )
            await asyncio.sleep(0.2)
        print("    ✓ checkboxes")

        await atolls_page.screenshot(path=f"uploads/fa_ready_{ts()}.png")

        # Submit → triggers verification email
        sub = await frame.query_selector("input[type='submit'], #submit_app")
        if sub:
            print("\n  → Clicking Submit (will trigger verification email)...")
            await sub.scroll_into_view_if_needed(timeout=3000)
            await sub.click()
            await atolls_page.wait_for_timeout(5000)
            await atolls_page.screenshot(path=f"uploads/fa_after_submit_{ts()}.png")

        frame_body = ""
        try: frame_body = (await frame.inner_text("body")).lower()
        except Exception: pass
        print(f"  Frame after submit: {frame_body[:300]}")

        if "verification" in frame_body or "code" in frame_body or "sent" in frame_body:
            print("  Email verification required")

            # Switch to Outlook and wait for code
            await outlook_page.bring_to_front()
            code = await wait_for_code(outlook_page, max_wait=120)

            if code:
                print(f"\n  Got code: {code}")
                # Switch back to Atolls
                await atolls_page.bring_to_front()
                await atolls_page.screenshot(path=f"uploads/fa_before_code_{ts()}.png")

                # Find code input
                for code_sel in [
                    "input[placeholder*='code' i]",
                    "input[aria-label*='code' i]",
                    "input[name*='code' i]",
                    "input[id*='code' i]",
                    "input[id*='token' i]",
                    "input[type='text']:not([id*='name']):not([id*='email']):not([id*='phone'])",
                ]:
                    try:
                        code_inp = await frame.query_selector(code_sel)
                        if code_inp and await code_inp.is_visible():
                            await code_inp.click()
                            await code_inp.fill(code)
                            print(f"  Entered code via {code_sel}")
                            break
                    except Exception: pass

                await atolls_page.screenshot(path=f"uploads/fa_code_entered_{ts()}.png")

                # Final submit
                sub2 = await frame.query_selector(
                    "input[type='submit'], #submit_app, button[type='submit']"
                )
                if sub2:
                    await sub2.click()
                    await atolls_page.wait_for_timeout(10000)
                    await atolls_page.screenshot(path=f"uploads/fa_final_{ts()}.png")
                    frame_body2 = ""
                    try: frame_body2 = (await frame.inner_text("body")).lower()
                    except Exception: pass
                    page_body2 = ""
                    try: page_body2 = (await atolls_page.inner_text("body")).lower()
                    except Exception: pass
                    ok = any(k in (frame_body2+page_body2) for k in [
                        "thank you","application received","successfully","submitted",
                        "danke","we received","thanks for applying"
                    ])
                    print(f"  Final body: {frame_body2[:400]}")
                    print(f"\n  {'✅ ATOLLS SUCCESS!' if ok else '❌ Final submit failed'}")
            else:
                print("  ❌ Could not get verification code from Outlook")
        else:
            ok = any(k in frame_body for k in [
                "thank you","application received","successfully","submitted"
            ])
            print(f"\n  {'✅ ATOLLS SUCCESS!' if ok else '❌ No verification and no confirmation'}")

        await browser.close()

asyncio.run(main())
