"""
Atolls complete — fills form, waits for verification code email, enters it.
"""
import asyncio, imaplib, email as emaillib, json, re, sys, io, os, time
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from playwright.async_api import async_playwright, Frame

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


def fetch_greenhouse_code(max_wait=120) -> str | None:
    """Poll Hotmail IMAP for Greenhouse verification code email."""
    print(f"  Checking email for verification code (up to {max_wait}s)...")
    deadline = time.time() + max_wait
    while time.time() < deadline:
        try:
            mail = imaplib.IMAP4_SSL("imap-mail.outlook.com", 993)
            mail.login(EMAIL, EMAIL_PASS)
            mail.select("INBOX")
            # Search for recent emails from Greenhouse
            _, uids = mail.search(None, 'UNSEEN SUBJECT "application"')
            if not uids[0]:
                _, uids = mail.search(None, 'UNSEEN SUBJECT "verify"')
            if not uids[0]:
                _, uids = mail.search(None, 'UNSEEN FROM "greenhouse"')
            if not uids[0]:
                # Just get last 5 unseen emails
                _, uids = mail.search(None, 'UNSEEN')
            if uids[0]:
                uid_list = uids[0].split()
                # Check most recent emails
                for uid in reversed(uid_list[-5:]):
                    _, msg_data = mail.fetch(uid, "(RFC822)")
                    raw = msg_data[0][1]
                    msg = emaillib.message_from_bytes(raw)
                    subj = msg.get("Subject", "")
                    frm = msg.get("From", "")
                    print(f"    Email: from={frm[:40]!r} subj={subj[:60]!r}")

                    # Get body
                    body = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            ct = part.get_content_type()
                            if ct in ("text/plain", "text/html"):
                                try:
                                    body += part.get_payload(decode=True).decode("utf-8", errors="replace")
                                except Exception:
                                    pass
                    else:
                        try:
                            body = msg.get_payload(decode=True).decode("utf-8", errors="replace")
                        except Exception:
                            pass

                    # Extract 8-character code
                    # Greenhouse codes are typically 8 alphanumeric chars
                    patterns = [
                        r'\b([A-Z0-9]{8})\b',
                        r'code[:\s]+([A-Z0-9]{6,10})',
                        r'([0-9]{6,8})',  # numeric codes
                        r'\b([a-z0-9]{8})\b',
                    ]
                    for pat in patterns:
                        matches = re.findall(pat, body, re.I)
                        for m in matches:
                            # Skip common false positives
                            if m.upper() not in ["UNSUB SCR", "CLICK HER", "PRIVACYPO"]:
                                print(f"    Found code candidate: {m}")
                                mail.logout()
                                return m
            mail.logout()
        except Exception as e:
            print(f"    IMAP error: {e}")

        print(f"    No code yet, waiting 10s...")
        time.sleep(10)
    return None


async def fill_text(frame: Frame, sel: str, val: str, lbl=""):
    try:
        el = await frame.query_selector(sel)
        if not el: print(f"    ✗ {lbl}: not found"); return
        await frame.evaluate(
            """([s,v])=>{const e=document.querySelector(s);if(!e)return;
               const p=e.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;
               const d=Object.getOwnPropertyDescriptor(p,'value');
               if(d?.set)d.set.call(e,v);else e.value=v;
               ['input','change','blur'].forEach(t=>e.dispatchEvent(new Event(t,{bubbles:true})));
            }""", [sel, val])
        await el.click(click_count=3); await el.fill(val)
        print(f"    ✓ {lbl or sel}: '{val[:40]}'")
    except Exception as e:
        print(f"    ✗ {lbl or sel}: {e}")


async def select_by_keyboard(frame: Frame, input_id: str, option_text: str) -> bool:
    id_attr = input_id
    try:
        await frame.evaluate(
            """([id_attr]) => {
                const inp = document.querySelector('[id="' + id_attr + '"]');
                if (!inp) return;
                let c = inp;
                for (let i=0;i<8;i++){
                    if(!c.parentElement) break; c=c.parentElement;
                    if((c.className||'').includes('input-wrapper')) break;
                }
                const ind = c.querySelector('[class*="indicator"]:not([class*="clear"])');
                if(ind) ind.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));
                const ctrl = c.querySelector('[class*="control"]');
                if(ctrl) ctrl.click();
            }""", [id_attr]
        )
        await asyncio.sleep(0.8)

        el = await frame.query_selector(f'[id="{id_attr}"]')
        if el:
            await el.click()
            await el.type(option_text[:3], delay=50)
            await asyncio.sleep(0.5)
            picked = await frame.evaluate(
                """(desired) => {
                    const dl = desired.toLowerCase().trim();
                    const opts = document.querySelectorAll('[class*="option"]');
                    const vis = Array.from(opts).filter(o=>{
                        const r=o.getBoundingClientRect();return r.width>0&&r.height>0;
                    });
                    for (const o of vis) {
                        const t = o.textContent.trim().toLowerCase();
                        if (t===dl||t.startsWith(dl)||(dl.length>=4&&t.startsWith(dl.slice(0,5)))) {
                            o.click(); return 'match:'+o.textContent.trim();
                        }
                    }
                    for (const o of vis) {
                        const t=o.textContent.trim().toLowerCase();
                        if(t.includes(dl)){o.click();return 'contains:'+o.textContent.trim();}
                    }
                    if(vis.length>0){vis[0].click();return 'first:'+vis[0].textContent.trim();}
                    // Keyboard fallback
                    return 'no_match';
                }""", option_text
            )
            print(f"    ✓ {id_attr}: {picked}")
            await frame.evaluate("document.body.click()")
            await asyncio.sleep(0.3)
            return "no_match" not in picked
    except Exception as e:
        print(f"    ✗ {id_attr}: {e}")
    return False


async def main():
    print("=== ATOLLS COMPLETE (with email verification) ===")
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
        if not frame: print("ERROR: no frame"); await browser.close(); return
        print("GH iframe ready")

        # Fill all fields
        await fill_text(frame, "#first_name", P["first_name"], "first_name")
        await fill_text(frame, "#last_name",  P["last_name"],  "last_name")
        await fill_text(frame, "#email",      P["email"],      "email")
        await fill_text(frame, "#phone",      P["phone"],      "phone")

        # Location
        loc = await frame.query_selector("#candidate-location")
        if loc:
            await loc.click(); await loc.type(P["location"], delay=80)
            await asyncio.sleep(2)
            first_opt = await frame.evaluate(
                """() => {
                    const vis = Array.from(document.querySelectorAll('[class*="option"]'))
                        .filter(o=>{const r=o.getBoundingClientRect();return r.width>0&&r.height>0;});
                    if(vis.length>0){vis[0].click();return vis[0].textContent.trim();}
                    return 'none';
                }"""
            )
            print(f"    ✓ location: {first_opt}")
            if first_opt == "none": await loc.press("ArrowDown"); await loc.press("Enter")
            await frame.evaluate("document.body.click()"); await asyncio.sleep(0.3)

        await select_by_keyboard(frame, "country", "Germany")

        fi = await frame.query_selector("input[type='file']")
        if fi:
            await fi.set_input_files(RESUME); await page.wait_for_timeout(3000)
            print("    ✓ resume")

        await fill_text(frame, "#question_63011684", P["years_exp"], "years")
        await fill_text(frame, "#question_63011685", "Yes",          "java17")
        await fill_text(frame, "#question_63011689", P["salary"],    "salary")

        await select_by_keyboard(frame, "question_63011686", "Yes")
        await select_by_keyboard(frame, "question_63011687", "Yes")
        await select_by_keyboard(frame, "question_63011688", "Yes")
        await select_by_keyboard(frame, "question_63011690", "No")
        await select_by_keyboard(frame, "question_63011691[]", "LinkedIn")

        # Checkboxes — click labels
        for cb_id in ["question_63011692[]_621426174", "gdpr_processing_consent_given_1"]:
            try:
                r = await frame.evaluate(
                    """(id) => {
                        const lbl = document.querySelector('label[for="' + id + '"]');
                        if(lbl){lbl.click();return 'label';}
                        const cb = document.getElementById(id)||document.querySelector('[id="'+id+'"]');
                        if(cb){cb.click();return 'input';}
                        return 'not_found';
                    }""", cb_id
                )
                print(f"    ✓ {cb_id}: {r}")
                await asyncio.sleep(0.2)
            except Exception as e:
                print(f"    ✗ {cb_id}: {e}")

        await page.wait_for_timeout(500)
        await page.screenshot(path=f"uploads/complete_atolls_ready_{ts()}.png")

        # Submit
        sub = await frame.query_selector("input[type='submit'], #submit_app, button[type='submit']")
        if not sub:
            print("No submit button!"); await browser.close(); return

        val = await sub.get_attribute("value") or "submit"
        print(f"\n  → Submitting: '{val}'")
        await sub.scroll_into_view_if_needed(timeout=3000)
        await sub.click()
        await page.wait_for_timeout(5000)
        await page.screenshot(path=f"uploads/complete_atolls_after_submit_{ts()}.png")

        # Check if verification code is needed
        frame_body = ""
        try: frame_body = (await frame.inner_text("body")).lower()
        except Exception: pass
        page_body = ""
        try: page_body = (await page.inner_text("body")).lower()
        except Exception: pass

        print(f"  Frame after submit (500): {frame_body[:500]}")

        if "verification" in frame_body or "code was sent" in frame_body or "verify" in frame_body:
            print("\n  📧 Verification code needed! Checking email...")
            code = fetch_greenhouse_code(max_wait=120)
            if code:
                print(f"  Got code: {code}")
                # Find the verification code input
                code_inp = await frame.query_selector(
                    "input[placeholder*='code' i], input[aria-label*='code' i], "
                    "input[name*='code' i], input[id*='code' i], "
                    "input[type='text']:not([id])"
                )
                if code_inp:
                    await code_inp.click()
                    await code_inp.fill(code)
                    print(f"  Entered code: {code}")
                    await page.wait_for_timeout(1000)
                    await page.screenshot(path=f"uploads/complete_atolls_code_entered_{ts()}.png")

                    # Submit again
                    sub2 = await frame.query_selector(
                        "input[type='submit'], #submit_app, button[type='submit'], "
                        "button:has-text('Submit')"
                    )
                    if sub2:
                        await sub2.click()
                        await page.wait_for_timeout(8000)
                        await page.screenshot(path=f"uploads/complete_atolls_final_{ts()}.png")
                        frame_body2 = ""
                        try: frame_body2 = (await frame.inner_text("body")).lower()
                        except Exception: pass
                        page_body2 = ""
                        try: page_body2 = (await page.inner_text("body")).lower()
                        except Exception: pass
                        combined2 = frame_body2 + " " + page_body2
                        ok2 = any(k in combined2 for k in [
                            "thank you", "application received", "successfully",
                            "submitted", "danke", "we received", "thanks for applying"
                        ])
                        print(f"  Final body: {frame_body2[:300]}")
                        print(f"\n  {'✅ SUCCESS!' if ok2 else '❌ Still failing'}")
                else:
                    print("  ❌ Could not find code input field")
            else:
                print("  ❌ No verification code found in email within timeout")
        else:
            kws = ["thank you","application received","successfully","submitted",
                   "danke","we received","thanks for applying"]
            ok = any(k in (frame_body+page_body) for k in kws)
            print(f"\n  {'✅ SUCCESS!' if ok else '❌ FAILED (no verification and no confirmation)'}")

        await browser.close()

asyncio.run(main())
