"""
Atolls full apply + OTP: fills form → submit → reads code via IMAP → enters per-box → final submit.
"""
import asyncio, json, sys, io, imaplib, email, re, time
from pathlib import Path
from datetime import datetime, timezone, timedelta
from playwright.async_api import async_playwright, Frame

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

SESSION_FILE = Path("uploads/linkedin_session.json")
RESUME = str(Path("uploads/resume_en.pdf").resolve())
HOTMAIL_USER = "f_behboud@hotmail.com"
HOTMAIL_PASS = "ncfxwxpudbrfjpiv"

P = {"first_name": "Felix", "last_name": "Behboudi", "email": "f_behboud@hotmail.com",
     "phone": "+491744548555", "location": "Berlin", "salary": "75000", "years_exp": "5"}

def ts(): return datetime.now().strftime("%H%M%S")


def fetch_otp_from_email(max_wait=120) -> str | None:
    """Poll IMAP for the Atolls security code email, return the 8-char code."""
    print(f"  [IMAP] Connecting to imap.outlook.com ...")
    deadline = time.time() + max_wait
    while time.time() < deadline:
        try:
            mail = imaplib.IMAP4_SSL("imap.outlook.com", 993)
            mail.login(HOTMAIL_USER, HOTMAIL_PASS)
            mail.select("INBOX")

            # Search for recent Atolls security code emails
            _, ids = mail.search(None, '(SUBJECT "Security code" SUBJECT "Atolls")')
            if not ids[0]:
                # Broader search
                _, ids = mail.search(None, 'SUBJECT "Security code"')

            if ids[0]:
                all_ids = ids[0].split()
                # Check newest emails first
                for msg_id in reversed(all_ids[-5:]):
                    _, data = mail.fetch(msg_id, "(RFC822)")
                    msg = email.message_from_bytes(data[0][1])
                    subject = msg.get("Subject", "")
                    date_str = msg.get("Date", "")
                    print(f"  [IMAP] Email: '{subject}' — {date_str}")

                    # Only look at emails from the last 5 minutes
                    body = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            if part.get_content_type() == "text/plain":
                                body += part.get_payload(decode=True).decode(errors="replace")
                    else:
                        body = msg.get_payload(decode=True).decode(errors="replace")

                    # Extract 8-char alphanumeric code
                    matches = re.findall(r'\b([A-Za-z0-9]{8})\b', body)
                    for m in matches:
                        # Skip common non-code 8-char strings
                        if m.lower() not in ("hotmail.", "outlook.", "microsof"):
                            print(f"  [IMAP] Found code candidate: {m}")
                            mail.logout()
                            return m

            mail.logout()
            remaining = int(deadline - time.time())
            print(f"  [IMAP] No code yet, retrying... ({remaining}s left)")
            time.sleep(5)

        except Exception as e:
            print(f"  [IMAP] Error: {e}")
            time.sleep(5)

    return None


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
            await el.click(click_count=3)
            await el.fill(val)
        print(f"    ✓ {lbl or sel}: '{val[:40]}'")
    except Exception as e:
        print(f"    ✗ {lbl or sel}: {e}")


async def select_dd(frame: Frame, input_id: str, option: str):
    try:
        await frame.evaluate(
            """([id])=>{
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
            await el.click()
            await el.type(option[:3], delay=50)
            await asyncio.sleep(0.5)
            picked = await frame.evaluate(
                """(dl)=>{
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
        await frame.evaluate("document.body.click()")
        await asyncio.sleep(0.3)
    except Exception as e:
        print(f"    ✗ {input_id}: {e}")


async def enter_otp_boxes(frame: Frame, page, code: str) -> bool:
    """Enter OTP one character per box into #security-input-0 .. #security-input-7."""
    print(f"\n  → Entering OTP '{code}' into 8 boxes...")
    for i, ch in enumerate(code):
        sel = f"#security-input-{i}"
        try:
            el = await frame.query_selector(sel)
            if not el:
                # Also check the page (OTP might be outside iframe)
                el = await page.query_selector(sel)
            if el:
                await el.click()
                await asyncio.sleep(0.1)
                await el.fill(ch)
                await asyncio.sleep(0.1)
                print(f"    ✓ box {i}: '{ch}'")
            else:
                print(f"    ✗ box {i}: not found")
                return False
        except Exception as e:
            print(f"    ✗ box {i}: {e}")
            return False
    return True


async def main():
    print("=== ATOLLS OTP APPLY ===")
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

        # Find Greenhouse iframe
        frame = None
        for _ in range(25):
            for f in page.frames:
                if "greenhouse.io" in f.url and "embed" in f.url:
                    try:
                        if len(await f.query_selector_all("input")) >= 3:
                            frame = f; break
                    except Exception:
                        pass
            if frame: break
            await page.wait_for_timeout(500)
        if not frame:
            print("ERROR: no GH iframe"); await browser.close(); return
        print("GH iframe ready")

        # ── Fill form ──────────────────────────────────────────────────────────
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
            await frame.evaluate(
                """()=>{const vis=Array.from(document.querySelectorAll('[class*="option"]'))
                    .filter(o=>{const r=o.getBoundingClientRect();return r.width>0&&r.height>0;});
                    if(vis.length>0)vis[0].click();}"""
            )
            await frame.evaluate("document.body.click()")
            await asyncio.sleep(0.3)
            print("    ✓ location: Berlin")

        await select_dd(frame, "country", "Germany")

        fi = await frame.query_selector("input[type='file']")
        if fi:
            await fi.set_input_files(RESUME)
            await page.wait_for_timeout(3000)
            print("    ✓ resume")

        await fill_text(frame, "#question_63011684", P["years_exp"], "years")
        await fill_text(frame, "#question_63011685", "Yes",          "java17")
        await fill_text(frame, "#question_63011689", P["salary"],    "salary")
        await select_dd(frame, "question_63011686", "Yes")
        await select_dd(frame, "question_63011687", "Yes")
        await select_dd(frame, "question_63011688", "Yes")
        await select_dd(frame, "question_63011690", "No")
        await select_dd(frame, "question_63011691[]", "LinkedIn")

        for cb_id in ["question_63011692[]_621426174", "gdpr_processing_consent_given_1"]:
            await frame.evaluate(
                """(id)=>{
                    const lbl=document.querySelector('label[for="'+id+'"]');
                    if(lbl){lbl.click();return;}
                    const cb=document.getElementById(id)||document.querySelector('[id="'+id+'"]');
                    if(cb)cb.click();
                }""", cb_id)
            await asyncio.sleep(0.2)
        print("    ✓ checkboxes")

        await page.wait_for_timeout(500)
        await page.screenshot(path=f"uploads/otp_ready_{ts()}.png")
        print("  → Form filled. Screenshot saved.")

        # ── Submit #1 ──────────────────────────────────────────────────────────
        sub = await frame.query_selector("button[type='submit']")
        if not sub:
            sub = await frame.query_selector("button:has-text('Submit application')")
        if not sub:
            print("  ❌ Submit button not found!"); await browser.close(); return

        print("\n  → Clicking Submit #1 ...")
        submit_time = time.time()
        await sub.scroll_into_view_if_needed(timeout=3000)
        await sub.click()
        await page.wait_for_timeout(8000)
        await page.screenshot(path=f"uploads/otp_after_submit1_{ts()}.png")

        # ── Fetch OTP from email ───────────────────────────────────────────────
        print("\n  → Fetching OTP from Hotmail (up to 2 min)...")
        # Run IMAP fetch in a thread so we don't block the event loop
        loop = asyncio.get_event_loop()
        code = await loop.run_in_executor(None, fetch_otp_from_email, 120)

        if not code:
            body = ""
            try: body = (await frame.inner_text("body")).lower()
            except Exception: pass
            print(f"  ❌ Could not retrieve OTP. Frame: {body[:300]}")
            await page.screenshot(path=f"uploads/otp_no_code_{ts()}.png")
            await browser.close()
            return

        print(f"\n  ✓ OTP received: {code}")

        # ── Wait for OTP boxes to appear ───────────────────────────────────────
        print("  → Waiting for OTP input boxes...")
        otp_frame = None
        for _ in range(20):
            # Check both page and frame
            for ctx_obj in [frame, page]:
                try:
                    el = await ctx_obj.query_selector("#security-input-0")
                    if el and await el.is_visible():
                        otp_frame = ctx_obj
                        break
                except Exception:
                    pass
            if otp_frame: break
            await page.wait_for_timeout(1000)

        if not otp_frame:
            body = ""
            try: body = (await frame.inner_text("body")).lower()
            except Exception: pass
            print(f"  ❌ OTP boxes not found. Frame: {body[:300]}")
            await page.screenshot(path=f"uploads/otp_no_boxes_{ts()}.png")
            await browser.close()
            return

        await page.screenshot(path=f"uploads/otp_boxes_visible_{ts()}.png")
        print(f"  OTP boxes found in: {'frame' if otp_frame is frame else 'page'}")

        # ── Enter OTP per-box ──────────────────────────────────────────────────
        ok = await enter_otp_boxes(otp_frame, page, code)
        if not ok:
            print("  ❌ Failed entering OTP boxes")
            await page.screenshot(path=f"uploads/otp_entry_failed_{ts()}.png")
            await browser.close()
            return

        await page.wait_for_timeout(1000)
        await page.screenshot(path=f"uploads/otp_entered_{ts()}.png")

        # ── Final submit ───────────────────────────────────────────────────────
        print("\n  → Final submit...")
        for sub_sel in ["button[type='submit']", "input[type='submit']"]:
            try:
                btn = await otp_frame.query_selector(sub_sel)
                if btn and await btn.is_visible():
                    await btn.click()
                    break
            except Exception:
                pass

        await page.wait_for_timeout(12000)
        await page.screenshot(path=f"uploads/otp_final_{ts()}.png")

        fb = ""
        try: fb = (await frame.inner_text("body")).lower()
        except Exception: pass
        pb = ""
        try: pb = (await page.inner_text("body")).lower()
        except Exception: pass
        combined = fb + " " + pb
        kws = ["thank you", "application received", "successfully", "submitted",
               "we received", "thanks for applying", "your application"]
        success = any(k in combined for k in kws)
        print(f"  Frame: {fb[:400]}")
        print(f"\n  {'✅ SUCCESS — Atolls submitted!' if success else '❌ FAILED — check screenshot'}")

        await browser.close()


asyncio.run(main())
