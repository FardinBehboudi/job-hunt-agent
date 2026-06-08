"""
Apply v3 — targeted fills for each specific form field.
Learned from screenshots:
- Atolls: #candidate-location, custom question IDs, acknowledge checkbox
- Flix: salary/date/radio/tech/kotlin/privacy fields by name
- C&A: wait longer for SuccessFactors SPA
"""
import asyncio, json, re, sys, io
from pathlib import Path
from datetime import datetime, timedelta
from playwright.async_api import async_playwright, Page, Frame, TimeoutError as PWTimeout

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

SESSION_FILE = Path("uploads/linkedin_session.json")
RESUME = str(Path("uploads/resume_en.pdf").resolve())

P = {
    "first_name": "Felix",
    "last_name": "Behboudi",
    "email": "f_behboud@hotmail.com",
    "phone": "+491744548555",
    "linkedin": "https://www.linkedin.com/in/fbehboudi/",
    "github": "https://github.com/FardinBehboudi",
    "location": "Berlin",
    "salary": "75000",
    "start_date": "immediately",
    "years_exp": "5",
    "technologies": (
        "Java (17+), Spring Boot, Spring Reactive, Kotlin, Python, "
        "Docker, Kubernetes, AWS, PostgreSQL, MongoDB, Kafka, RabbitMQ, "
        "Microservices, REST APIs, CI/CD, Git"
    ),
    "kotlin_exp": (
        "I have worked with Kotlin on backend microservice projects, "
        "including REST APIs and data processing pipelines. "
        "I'm comfortable with Kotlin's coroutines and idioms."
    ),
    "cover": (
        "I am a Senior Backend Engineer with 5+ years of Java/Spring Boot "
        "experience building scalable microservices. Based in Berlin, "
        "available immediately, no visa sponsorship needed."
    ),
}


def ts() -> str:
    return datetime.now().strftime("%H%M%S")


async def accept_cookies(page: Page):
    for _ in range(3):
        clicked = False
        for sel in [
            "button:has-text('Accept All Cookies')",
            "button:has-text('Accept all')",
            "button:has-text('Accept')",
            "#onetrust-accept-btn-handler",
            "[data-testid='uc-accept-all-button']",
            ".wp-consent-accept",
        ]:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    await el.click()
                    await page.wait_for_timeout(1500)
                    clicked = True
                    break
            except Exception:
                pass
        if not clicked:
            break


async def smart_fill(frame: Frame, selector: str, value: str, label="") -> bool:
    """Fill using React-native setter + Playwright fill."""
    try:
        el = await frame.query_selector(selector)
        if not el:
            return False
        try:
            await el.scroll_into_view_if_needed(timeout=5000)
        except Exception:
            pass
        # Use native value setter to bypass React's synthetic event system
        await frame.evaluate(
            """([sel, val]) => {
                const el = document.querySelector(sel);
                if (!el) return;
                const proto = el.tagName === 'TEXTAREA'
                    ? window.HTMLTextAreaElement.prototype
                    : window.HTMLInputElement.prototype;
                const setter = Object.getOwnPropertyDescriptor(proto, 'value');
                if (setter && setter.set) setter.set.call(el, val);
                else el.value = val;
                ['input','change','blur'].forEach(t =>
                    el.dispatchEvent(new Event(t, {bubbles:true})));
            }""",
            [selector, value]
        )
        # Reinforce with Playwright fill (click_count=3 replaces triple_click)
        tag = await el.evaluate("el => el.tagName.toLowerCase()")
        if tag != "textarea":
            await el.click(click_count=3)
        await el.fill(value)
        print(f"    ✓ {label or selector}: '{value[:40]}'")
        return True
    except Exception as e:
        print(f"    ✗ {label or selector}: {e}")
        return False


async def check_box(frame: Frame, selector: str, label="") -> bool:
    try:
        el = await frame.query_selector(selector)
        if not el:
            return False
        try:
            await el.scroll_into_view_if_needed(timeout=5000)
        except Exception:
            pass
        # Force-check via JS in case element is visually hidden
        await frame.evaluate(
            """(sel) => { const el = document.querySelector(sel); if(el) el.checked = true; }""",
            selector
        )
        await el.evaluate("el => el.dispatchEvent(new Event('change', {bubbles:true}))")
        print(f"    ✓ checkbox {label or selector}")
        return True
    except Exception as e:
        print(f"    ✗ checkbox {label or selector}: {e}")
        return False


async def click_radio(frame: Frame, selector: str, label="") -> bool:
    try:
        el = await frame.query_selector(selector)
        if not el:
            return False
        try:
            await el.scroll_into_view_if_needed(timeout=5000)
        except Exception:
            pass
        await el.evaluate("el => { el.checked = true; el.click(); el.dispatchEvent(new Event('change',{bubbles:true})); }")
        print(f"    ✓ radio {label or selector}")
        return True
    except Exception as e:
        print(f"    ✗ radio {label or selector}: {e}")
        return False


# ─────────────────────────────────────────────────────────────
# ATOLLS (Greenhouse iframe at job-boards.greenhouse.io)
# ─────────────────────────────────────────────────────────────
async def apply_atolls(context) -> dict:
    print(f"\n{'='*60}\nATOLLS — Senior Java Backend Engineer")
    page = await context.new_page()
    try:
        await page.goto(
            "https://boards.greenhouse.io/atolls/jobs/7564145",
            wait_until="domcontentloaded", timeout=25_000
        )
        await page.wait_for_timeout(3000)
        await accept_cookies(page)
        await page.wait_for_timeout(5000)
        await page.screenshot(path=f"uploads/v3_atolls_loaded_{ts()}.png")

        # Wait for Greenhouse iframe
        frame = None
        for _ in range(20):
            for f in page.frames:
                if "greenhouse.io" in f.url and "embed" in f.url:
                    try:
                        n = len(await f.query_selector_all("input"))
                        if n >= 3:
                            frame = f
                            print(f"  GH iframe found: {n} inputs")
                            break
                    except Exception:
                        pass
            if frame:
                break
            await page.wait_for_timeout(500)

        if not frame:
            print("  ERROR: Greenhouse iframe not found")
            await page.close()
            return {"success": False, "note": "iframe not found"}

        # ── Standard fields ──────────────────────────────────────
        await smart_fill(frame, "#first_name", P["first_name"], "first_name")
        await smart_fill(frame, "#last_name",  P["last_name"],  "last_name")
        await smart_fill(frame, "#email",      P["email"],      "email")

        # Phone: Greenhouse splits into country code + number
        # Try filling the full number first, then just digits
        phone_filled = await smart_fill(frame, "#phone", P["phone"], "phone")
        if not phone_filled:
            await smart_fill(frame, "input[type='tel']", P["phone"], "phone(tel)")

        # Location (City) — specific ID
        loc_filled = await smart_fill(frame, "#candidate-location", P["location"], "location(city)")
        if not loc_filled:
            await smart_fill(frame, "input[aria-label*='City' i]", P["location"], "location(aria)")

        # Country combobox
        try:
            country_el = await frame.query_selector("#country")
            if country_el:
                await country_el.scroll_into_view_if_needed()
                await country_el.click()
                await country_el.fill("Germany")
                await page.wait_for_timeout(1000)
                for opt_sel in [
                    "li:has-text('Germany')",
                    "[role='option']:has-text('Germany')",
                    "[data-display='Germany']",
                ]:
                    try:
                        opt = await frame.query_selector(opt_sel)
                        if opt and await opt.is_visible():
                            await opt.click()
                            print("    ✓ country: Germany")
                            break
                    except Exception:
                        pass
        except Exception as e:
            print(f"    ✗ country: {e}")

        # LinkedIn URL
        await smart_fill(frame, "#linkedin_url", P["linkedin"], "linkedin")

        # Resume upload — do it early and wait
        try:
            fi = await frame.query_selector("input[type='file']")
            if fi:
                await fi.set_input_files(RESUME)
                await page.wait_for_timeout(3000)
                print(f"    ✓ resume uploaded")
        except Exception as e:
            print(f"    ✗ resume: {e}")

        # ── Custom questions ─────────────────────────────────────
        q_map = {
            "#question_63011684": P["years_exp"],           # years backend exp
            "#question_63011685": "Yes",                    # Java 17+
            "#question_63011686": "Yes",                    # Spring Boot / Reactive
            "#question_63011687": "Yes",                    # microservices
            "#question_63011688": "Yes",                    # based in Berlin
            "#question_63011689": P["salary"],              # desired salary
            "#question_63011690": "LinkedIn",               # how did you find us
        }
        for sel, val in q_map.items():
            await smart_fill(frame, sel, val, sel)

        # Channel question (may be checkbox or radio — try text fill first)
        # question_63011691[] — "through what channel"
        for sel in ["#question_63011691\\[\\]", "input[id*='question_63011691']",
                    "input[name*='question_63011691']"]:
            try:
                el = await frame.query_selector(sel)
                if el:
                    await smart_fill(frame, sel, "LinkedIn", "channel")
                    break
            except Exception:
                pass

        # Acknowledge checkbox
        await check_box(frame, "#question_63011692\\[\\]_621426174", "acknowledge")
        # Also try without escaping
        try:
            cbs = await frame.query_selector_all("input[type='checkbox']")
            for cb in cbs:
                try:
                    if not await cb.is_checked():
                        cb_id = await cb.get_attribute("id") or ""
                        if "acknowledge" in cb_id.lower() or "confirm" in cb_id.lower() or "692" in cb_id:
                            await cb.check()
                            print(f"    ✓ checkbox {cb_id}")
                except Exception:
                    pass
        except Exception:
            pass

        await page.screenshot(path=f"uploads/v3_atolls_filled_{ts()}.png")

        # Scroll to submit and click
        try:
            sub = await frame.query_selector("input[type='submit'], button[type='submit'], #submit_app")
            if sub:
                await sub.scroll_into_view_if_needed()
                val = await sub.get_attribute("value") or await sub.inner_text() or "submit"
                print(f"  Submitting: '{val.strip()}'")
                await sub.click()
                await page.wait_for_timeout(8000)
                await page.screenshot(path=f"uploads/v3_atolls_submitted_{ts()}.png")

                # Check iframe for confirmation
                frame_body = ""
                try:
                    frame_body = (await frame.inner_text("body")).lower()
                except Exception:
                    pass
                main_body = (await page.inner_text("body")).lower()
                combined = frame_body + main_body
                success = any(k in combined for k in [
                    "thank you", "application received", "successfully", "submitted",
                    "danke", "confirmation", "we received"
                ])
                print(f"  frame body: {frame_body[:300]}")
                print(f"  success={success}")
                await page.close()
                return {"success": success}
        except Exception as e:
            print(f"  Submit error: {e}")

        await page.close()
        return {"success": False, "note": "no submit"}

    except Exception as e:
        print(f"  CRASH: {e}")
        try:
            await page.screenshot(path=f"uploads/v3_atolls_error_{ts()}.png")
        except Exception:
            pass
        try:
            await page.close()
        except Exception:
            pass
        return {"success": False, "note": str(e)}


# ─────────────────────────────────────────────────────────────
# FLIX (Greenhouse-based at flix.careers)
# Fields found: name='first_name', 'last_name', 'email', 'phone',
#   'resume_text', question_36481327002 (salary), question_36481328002 (start),
#   question_36481329002 (radio Yes/No - legal right to work),
#   question_36499988002 (technologies), question_36499989002 (location/relocation),
#   question_36505088002 (kotlin), demographic_4027976002, privacy_policy
# ─────────────────────────────────────────────────────────────
async def apply_flix(context) -> dict:
    print(f"\n{'='*60}\nFLIX — Senior Software Engineer")
    page = await context.new_page()
    try:
        await page.goto(
            "https://flix.careers/job/?jobid=8545023002&gh_src=fc023d502",
            wait_until="domcontentloaded", timeout=25_000
        )
        await page.wait_for_timeout(3000)
        await accept_cookies(page)
        await page.wait_for_timeout(3000)

        # Click "Apply for Job" button to show the form
        for btn_sel in ["button:has-text('Apply for Job')", "a:has-text('Apply for Job')",
                        ".apply-button", "[data-action='apply']"]:
            try:
                btn = await page.query_selector(btn_sel)
                if btn and await btn.is_visible():
                    await btn.click()
                    await page.wait_for_timeout(3000)
                    print("  Clicked Apply for Job")
                    break
            except Exception:
                pass

        await page.screenshot(path=f"uploads/v3_flix_loaded_{ts()}.png")

        # Flix form is directly on the page (no iframe)
        # Use main frame
        f = page.main_frame

        # ── Standard fields ──────────────────────────────────────
        await smart_fill(f, "input[name='first_name']", P["first_name"], "first_name")
        await smart_fill(f, "input[name='last_name']",  P["last_name"],  "last_name")
        await smart_fill(f, "input[name='email']",      P["email"],      "email")
        await smart_fill(f, "input[name='phone']",      P["phone"],      "phone")

        # Resume upload
        try:
            fi = await f.query_selector("input[type='file']")
            if fi:
                await fi.set_input_files(RESUME)
                await page.wait_for_timeout(3000)
                print("    ✓ resume uploaded")
        except Exception as e:
            print(f"    ✗ resume: {e}")

        # ── Custom questions ─────────────────────────────────────
        await smart_fill(f, "input[name='question_36481327002']", P["salary"], "salary")
        await smart_fill(f, "input[name='question_36481328002']", P["start_date"], "start_date")

        # Radio: legal right to work → Yes (option _1)
        await click_radio(f, "#question_36481329002_1", "legal_right_to_work=Yes")

        await smart_fill(f, "input[name='question_36499988002']", P["technologies"], "technologies")
        await smart_fill(f,
            "input[name='question_36499989002']",
            "Berlin, Germany. I am already based in Berlin, no relocation needed.",
            "location/relocation"
        )
        await smart_fill(f, "input[name='question_36505088002']", P["kotlin_exp"], "kotlin")

        # Privacy policy checkbox
        await check_box(f, "#privacy_policy", "privacy_policy")

        # LinkedIn (if there's a field for it)
        for li_sel in ["input[name='linkedin_url']", "#linkedin_url",
                       "input[placeholder*='linkedin' i]"]:
            try:
                el = await f.query_selector(li_sel)
                if el:
                    await smart_fill(f, li_sel, P["linkedin"], "linkedin")
                    break
            except Exception:
                pass

        await page.screenshot(path=f"uploads/v3_flix_filled_{ts()}.png")

        # Submit
        for sub_sel in [
            "button:has-text('Submit your application')",
            "button:has-text('Submit application')",
            "button[type='submit']",
            "input[type='submit']",
        ]:
            try:
                btn = await page.query_selector(sub_sel)
                if btn:
                    await btn.scroll_into_view_if_needed()
                    val = await btn.inner_text() or await btn.get_attribute("value") or "submit"
                    print(f"  Submitting: '{val.strip()}'")
                    await btn.click()
                    await page.wait_for_timeout(8000)
                    await page.screenshot(path=f"uploads/v3_flix_submitted_{ts()}.png")
                    body = (await page.inner_text("body")).lower()
                    success = any(k in body for k in [
                        "thank you", "application received", "successfully", "submitted",
                        "danke", "confirmation", "we received"
                    ])
                    print(f"  body: {body[:300]}")
                    print(f"  success={success}")
                    await page.close()
                    return {"success": success}
            except Exception as e:
                print(f"  Submit error: {e}")

        await page.close()
        return {"success": False, "note": "no submit"}

    except Exception as e:
        print(f"  CRASH: {e}")
        try:
            await page.screenshot(path=f"uploads/v3_flix_error_{ts()}.png")
        except Exception:
            pass
        try:
            await page.close()
        except Exception:
            pass
        return {"success": False, "note": str(e)}


# ─────────────────────────────────────────────────────────────
# C&A (SAP SuccessFactors — loads in iframe after "Apply now")
# ─────────────────────────────────────────────────────────────
async def apply_ca(context) -> dict:
    print(f"\n{'='*60}\nC&A — Senior Backend Engineer (SuccessFactors)")
    page = await context.new_page()
    popup = None

    async def on_popup(p):
        nonlocal popup
        popup = p
        print(f"  Popup: {p.url[:80]}")

    page.on("popup", on_popup)

    try:
        await page.goto(
            "https://www.linkedin.com/jobs/view/4398579944",
            wait_until="domcontentloaded", timeout=30_000
        )
        await page.wait_for_timeout(3000)

        # Click Apply
        for sel in ["a:has-text('Apply')"]:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    txt = (await el.inner_text()).strip()
                    if "Easy" not in txt:
                        await el.evaluate("el => el.click()")
                        await asyncio.sleep(4)
                        break
            except Exception:
                pass

        # Wait up to 8s for popup to appear
        for _ in range(16):
            if popup:
                break
            await asyncio.sleep(0.5)

        active = popup if popup else page
        print(f"  Active: {active.url[:100]}")

        await accept_cookies(active)
        await active.wait_for_timeout(5000)
        await active.screenshot(path=f"uploads/v3_ca_step1_{ts()}.png")

        # Click "Apply now" on the C&A job description page
        for sel in ["button:has-text('Apply now')", "a:has-text('Apply now')",
                    "button:has-text('Jetzt bewerben')"]:
            try:
                btn = await active.query_selector(sel)
                if btn and await btn.is_visible():
                    txt = (await btn.inner_text()).strip()
                    print(f"  Clicking: '{txt}'")
                    popup2 = None
                    async def on_p2(p):
                        nonlocal popup2
                        popup2 = p
                    active.on("popup", on_p2)
                    await btn.evaluate("el => el.click()")
                    await asyncio.sleep(5)
                    if popup2:
                        active = popup2
                        print(f"  SF popup: {popup2.url[:80]}")
                    break
            except Exception:
                pass

        # Wait longer for SuccessFactors SPA to load
        print("  Waiting 15s for SuccessFactors to load...")
        await active.wait_for_timeout(15000)
        await active.screenshot(path=f"uploads/v3_ca_sf_loaded_{ts()}.png")

        body = (await active.inner_text("body")).lower()
        print(f"  SF body: {body[:400]}")

        # Check if login required
        if any(k in body for k in ["sign in", "log in", "create account", "register", "anmelden"]):
            print("  ❌ SuccessFactors requires account login")
            await page.close()
            return {"success": False, "note": "Requires SAP SuccessFactors account login"}

        # SF form detection
        src = await active.content()
        print(f"  Frames: {len(active.frames)}")
        for fr in active.frames:
            print(f"    frame: {fr.url[:80]}")

        # Try to find form fields in any frame
        target_frame = active.main_frame
        for fr in active.frames:
            try:
                n = len(await fr.query_selector_all("input:not([type='hidden'])"))
                if n > 3 and fr != active.main_frame:
                    print(f"  Using frame with {n} inputs: {fr.url[:60]}")
                    target_frame = fr
                    break
            except Exception:
                pass

        inputs = await target_frame.query_selector_all(
            "input:not([type='hidden']):not([type='submit']), textarea"
        )
        print(f"  Inputs in target frame: {len(inputs)}")
        for inp in inputs[:20]:
            try:
                typ = await inp.get_attribute("type") or "text"
                id_a = await inp.get_attribute("id") or ""
                name_a = await inp.get_attribute("name") or ""
                ph = await inp.get_attribute("placeholder") or ""
                aria = await inp.get_attribute("aria-label") or ""
                print(f"    [{typ}] id={id_a!r} name={name_a!r} ph={ph!r} aria={aria!r}")
            except Exception:
                pass

        if len(inputs) == 0:
            print("  ❌ No form fields found in SuccessFactors")
            await page.close()
            return {"success": False, "note": "SuccessFactors: no form fields accessible"}

        # Fill SF form
        sf_fields = {
            r"first.?name|vorname": P["first_name"],
            r"last.?name|nachname": P["last_name"],
            r"email": P["email"],
            r"phone|tel": P["phone"],
            r"linkedin": P["linkedin"],
            r"cover|motivation|letter|anschreiben": P["cover"],
        }
        filled = 0
        for inp in inputs:
            try:
                label = (await inp.get_attribute("aria-label") or
                         await inp.get_attribute("placeholder") or
                         await inp.get_attribute("name") or "")
                for pat, val in sf_fields.items():
                    if re.search(pat, label, re.I):
                        existing = await inp.input_value()
                        if not existing:
                            await smart_fill(target_frame,
                                             f"[aria-label='{label}']" if label else "input",
                                             val, label[:30])
                            filled += 1
                        break
            except Exception:
                pass

        await active.screenshot(path=f"uploads/v3_ca_filled_{ts()}.png")
        print(f"  Filled {filled} fields")

        await page.close()
        return {"success": False, "note": f"SF partially filled ({filled} fields) - needs manual review"}

    except Exception as e:
        print(f"  CRASH: {e}")
        try:
            await page.close()
        except Exception:
            pass
        return {"success": False, "note": str(e)}


# ─────────────────────────────────────────────────────────────
async def main():
    print("=== APPLY V3 ===")
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
            if cookies:
                await ctx.add_cookies(cookies)

        results = {}
        results["atolls"] = await apply_atolls(ctx)
        results["flix"]   = await apply_flix(ctx)
        results["ca"]     = await apply_ca(ctx)

        await browser.close()

    print("\n" + "="*60)
    print("RESULTS:")
    for name, r in results.items():
        icon = "✅" if r.get("success") else "❌"
        note = r.get("note", "")
        print(f"  {icon} {name}: {note}")

asyncio.run(main())
