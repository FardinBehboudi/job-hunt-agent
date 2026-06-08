"""
Apply v2 — Fixes:
1. Use page.evaluate() to set React input values + dispatch events
2. Scroll each element into view before filling
3. Wait longer for iframe to fully render
4. Check iframe body for confirmation (not main page)
5. Handle Flix form field IDs properly
"""
import asyncio, json, re, sys, io
from pathlib import Path
from datetime import datetime
from playwright.async_api import async_playwright, Page, Frame, TimeoutError as PWTimeout

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

SESSION_FILE = Path("uploads/linkedin_session.json")
RESUME = str(Path("uploads/resume_en.pdf").resolve())

PROFILE = {
    "first_name": "Felix",
    "last_name": "Behboudi",
    "email": "f_behboud@hotmail.com",
    "phone": "+491744548555",
    "linkedin": "https://www.linkedin.com/in/fbehboudi/",
    "github": "https://github.com/FardinBehboudi",
    "location": "Berlin",
    "salary": "75000",
    "cover_letter": (
        "I am a Senior Java Backend Engineer with 5+ years of experience building "
        "scalable microservices and distributed systems using Java, Spring Boot, "
        "Docker, Kubernetes, and AWS. I'm based in Berlin, Germany, available "
        "immediately, and excited about this opportunity."
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
                    txt = (await el.inner_text()).strip()
                    print(f"    [cookie] '{txt}'")
                    await el.click()
                    await page.wait_for_timeout(1500)
                    clicked = True
                    break
            except Exception:
                pass
        if not clicked:
            break


async def react_fill(frame: Frame, selector: str, value: str) -> bool:
    """Fill a React-controlled input by dispatching proper events."""
    try:
        el = await frame.query_selector(selector)
        if not el:
            return False
        # Scroll into view
        await el.scroll_into_view_if_needed()
        await frame.evaluate(
            """([sel, val]) => {
                const el = document.querySelector(sel);
                if (!el) return false;
                const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value'
                ) || Object.getOwnPropertyDescriptor(
                    window.HTMLTextAreaElement.prototype, 'value'
                );
                if (nativeInputValueSetter && nativeInputValueSetter.set) {
                    nativeInputValueSetter.set.call(el, val);
                } else {
                    el.value = val;
                }
                el.dispatchEvent(new Event('input', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
                el.dispatchEvent(new KeyboardEvent('keydown', {bubbles: true}));
                el.dispatchEvent(new KeyboardEvent('keyup', {bubbles: true}));
                return true;
            }""",
            [selector, value]
        )
        # Also use Playwright's fill to reinforce
        await el.click()
        await el.fill(value)
        return True
    except Exception as e:
        print(f"      react_fill error ({selector}): {e}")
        return False


async def fill_form(frame: Frame, page: Page, name: str) -> bool:
    """Fill all form fields in the given frame."""
    print(f"  === fill_form on frame: {frame.url[:80]} ===")

    # Print all inputs in the frame for debugging
    try:
        inputs = await frame.query_selector_all(
            "input:not([type='hidden']):not([type='submit']):not([type='button']):not([type='file']), "
            "textarea, select"
        )
        print(f"  Found {len(inputs)} fillable elements in frame")
        for inp in inputs[:30]:
            try:
                typ = await inp.get_attribute("type") or "text"
                id_a = await inp.get_attribute("id") or ""
                name_a = await inp.get_attribute("name") or ""
                ph = await inp.get_attribute("placeholder") or ""
                aria = await inp.get_attribute("aria-label") or ""
                # Get label
                lbl = ""
                if id_a:
                    try:
                        l = await frame.query_selector(f"label[for='{id_a}']")
                        if l: lbl = (await l.inner_text()).strip()[:40]
                    except Exception:
                        pass
                print(f"    [{typ}] id={id_a!r} name={name_a!r} ph={ph!r} aria={aria!r} lbl={lbl!r}")
            except Exception:
                pass
    except Exception as e:
        print(f"  Error listing inputs: {e}")

    # Mapping: (selectors_to_try) → value
    field_fills = [
        # First name
        (["#first_name", "input[name='first_name']", "input[id*='firstName']",
          "input[placeholder*='first name' i]", "input[aria-label*='first name' i]"],
         PROFILE["first_name"]),
        # Last name
        (["#last_name", "input[name='last_name']", "input[id*='lastName']",
          "input[placeholder*='last name' i]", "input[aria-label*='last name' i]"],
         PROFILE["last_name"]),
        # Email
        (["#email", "input[type='email']", "input[name='email']",
          "input[placeholder*='email' i]", "input[aria-label*='email' i]"],
         PROFILE["email"]),
        # Phone
        (["#phone", "input[type='tel']", "input[name='phone']",
          "input[placeholder*='phone' i]", "input[aria-label*='phone' i]",
          "input[placeholder*='mobile' i]"],
         PROFILE["phone"]),
        # LinkedIn
        (["#linkedin_url", "input[name='linkedin_url']", "input[id*='linkedin']",
          "input[placeholder*='linkedin' i]", "input[aria-label*='linkedin' i]"],
         PROFILE["linkedin"]),
        # Location/City
        (["#location", "input[name='location']", "input[id*='location']",
          "input[id*='city']", "input[placeholder*='city' i]",
          "input[placeholder*='location' i]"],
         PROFILE["location"]),
        # GitHub/website
        (["#website", "input[name='website']", "input[id*='github']",
          "input[id*='website']", "input[placeholder*='github' i]"],
         PROFILE["github"]),
    ]

    filled_count = 0
    for selectors, value in field_fills:
        for sel in selectors:
            try:
                el = await frame.query_selector(sel)
                if el:
                    vis = True
                    try:
                        vis = await el.is_visible()
                    except Exception:
                        pass
                    existing = ""
                    try:
                        existing = await el.input_value()
                    except Exception:
                        pass
                    if existing:
                        print(f"    skip {sel!r} (already has: '{existing[:20]}')")
                        filled_count += 1
                        break
                    ok = await react_fill(frame, sel, value)
                    if ok:
                        print(f"    filled {sel!r}: '{value[:30]}'")
                        filled_count += 1
                        await page.wait_for_timeout(200)
                        break
            except Exception:
                pass

    # Cover letter in textarea
    for sel in ["#cover_letter", "textarea[name='cover_letter']", "textarea[id*='cover']",
                "textarea[placeholder*='cover' i]", "textarea[placeholder*='message' i]",
                "textarea[id*='message']", "textarea"]:
        try:
            el = await frame.query_selector(sel)
            if el:
                existing = await el.input_value()
                if not existing:
                    await el.scroll_into_view_if_needed()
                    await el.fill(PROFILE["cover_letter"])
                    print(f"    filled textarea {sel!r}")
                    break
        except Exception:
            pass

    # Country dropdown / select
    for sel in ["#country", "select[name='country']", "select[id*='country']"]:
        try:
            el = await frame.query_selector(sel)
            if el:
                try:
                    await el.select_option(label="Germany")
                    print("    selected country: Germany")
                except Exception:
                    try:
                        await el.select_option(value="DE")
                        print("    selected country: DE")
                    except Exception:
                        pass
        except Exception:
            pass

    # Country combobox (Greenhouse uses a custom country selector)
    try:
        country_inp = await frame.query_selector("#country")
        if country_inp:
            tag = await country_inp.evaluate("el => el.tagName.toLowerCase()")
            if tag == "input":
                existing = await country_inp.input_value()
                if not existing:
                    await country_inp.scroll_into_view_if_needed()
                    await country_inp.click()
                    await country_inp.fill("Germany")
                    await page.wait_for_timeout(1000)
                    # Try to pick from dropdown
                    for opt in ["li:has-text('Germany')", "[role='option']:has-text('Germany')",
                                "div:has-text('Germany')"]:
                        try:
                            o = await frame.query_selector(opt)
                            if o and await o.is_visible():
                                await o.click()
                                print("    selected Germany from combobox")
                                break
                        except Exception:
                            pass
    except Exception:
        pass

    # Resume upload
    try:
        file_inp = await frame.query_selector("input[type='file']")
        if file_inp:
            await file_inp.set_input_files(RESUME)
            print(f"    uploaded resume: {Path(RESUME).name}")
            await page.wait_for_timeout(2000)
    except Exception as e:
        print(f"    resume upload failed: {e}")

    await page.screenshot(path=f"uploads/v2_{name}_filled_{ts()}.png", full_page=False)
    print(f"  Filled {filled_count} fields")

    # Submit
    for sub_sel in [
        "#submit_app",
        "input[type='submit']",
        "button[type='submit']",
        "button:has-text('Submit application')",
        "button:has-text('Submit Application')",
        "button:has-text('Submit your application')",
        "button:has-text('Submit')",
        "button:has-text('Apply now')",
        "button:has-text('Apply Now')",
        "[data-submits-form]",
    ]:
        try:
            btn = await frame.query_selector(sub_sel)
            if btn:
                vis = True
                try:
                    vis = await btn.is_visible()
                except Exception:
                    pass
                val = ""
                try:
                    val = (await btn.inner_text()).strip() or (await btn.get_attribute("value") or "")
                except Exception:
                    pass
                print(f"  [submit] clicking '{val}' (visible={vis})")
                await btn.scroll_into_view_if_needed()
                await btn.click()
                await page.wait_for_timeout(6000)
                await page.screenshot(path=f"uploads/v2_{name}_submitted_{ts()}.png", full_page=False)

                # Check confirmation in BOTH main page and iframe
                body_main = (await page.inner_text("body")).lower()
                body_frame = ""
                try:
                    body_frame = (await frame.inner_text("body")).lower()
                except Exception:
                    pass
                combined = body_main + " " + body_frame

                success_kws = ["thank you", "application received", "successfully submitted",
                               "submitted", "danke", "we received your application",
                               "your application has been", "applied successfully",
                               "we'll be in touch", "confirmation"]
                success = any(kw in combined for kw in success_kws)
                print(f"  Frame body (300): {body_frame[:300]}")
                print(f"  Success: {success}")
                return success
        except Exception as e:
            print(f"  Submit error: {e}")

    print("  No submit button clicked")
    return False


async def apply_job(context, name: str, url: str, job_title: str) -> dict:
    print(f"\n{'='*60}")
    print(f"Job: {job_title}")
    print(f"URL: {url}")
    page = await context.new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=25_000)
        await page.wait_for_timeout(3000)
        await accept_cookies(page)
        await page.wait_for_timeout(4000)
        await page.screenshot(path=f"uploads/v2_{name}_loaded_{ts()}.png", full_page=False)
        print(f"  Loaded: {page.url[:100]}")

        # Find form: check iframes first, then main frame
        target_frame = None

        # Wait up to 15s for iframe with substantial inputs
        for attempt in range(15):
            for frame in page.frames:
                if "about:blank" in frame.url:
                    continue
                try:
                    n = len(await frame.query_selector_all(
                        "input:not([type='hidden']):not([type='submit'])"
                    ))
                    if n >= 3 and frame != page.main_frame:
                        print(f"  iframe [{attempt}s]: {frame.url[:60]} ({n} inputs)")
                        target_frame = frame
                        break
                except Exception:
                    pass
            if target_frame:
                break
            await page.wait_for_timeout(1000)

        if target_frame is None:
            # Use main frame
            main_inputs = len(await page.query_selector_all(
                "input:not([type='hidden']):not([type='submit'])"
            ))
            print(f"  No sub-iframe found. Main frame has {main_inputs} inputs.")
            target_frame = page.main_frame

        # Click "Apply" / "Apply for Job" if visible (some pages show job description first)
        for btn_sel in [
            "button:has-text('Apply for job')",
            "a:has-text('Apply for job')",
            "button:has-text('Apply now')",
            ".apply-button",
        ]:
            try:
                btn = await page.query_selector(btn_sel)
                if btn and await btn.is_visible():
                    txt = (await btn.inner_text()).strip()
                    print(f"  Clicking pre-apply button: '{txt}'")
                    await btn.click()
                    await page.wait_for_timeout(3000)
                    break
            except Exception:
                pass

        success = await fill_form(target_frame, page, name)
        await page.close()
        return {"success": success}
    except Exception as e:
        print(f"  ERROR: {e}")
        try:
            await page.screenshot(path=f"uploads/v2_{name}_error_{ts()}.png", full_page=False)
        except Exception:
            pass
        try:
            await page.close()
        except Exception:
            pass
        return {"success": False, "note": str(e)}


async def apply_ca(context) -> dict:
    """Apply to C&A via LinkedIn popup → SAP SuccessFactors."""
    print(f"\n{'='*60}")
    print("C&A Backend Engineer — SuccessFactors")
    page = await context.new_page()
    popup = None
    async def on_popup(p):
        nonlocal popup
        popup = p
    page.on("popup", on_popup)

    try:
        await page.goto("https://www.linkedin.com/jobs/view/4398579944",
                        wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(3000)

        # Get ATS URL from href
        ats_url = None
        for sel in ["a:has-text('Apply')"]:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    href = await el.get_attribute("href") or ""
                    if "linkedin.com/safety/go" in href:
                        import urllib.parse
                        qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
                        ats_url = qs.get("url", [""])[0]
                    await el.evaluate("el => el.click()")
                    await asyncio.sleep(4)
                    break
            except Exception:
                pass

        active = popup if popup else page
        if popup:
            ats_url = popup.url

        print(f"  C&A URL: {ats_url[:100] if ats_url else 'unknown'}")
        if not ats_url or "linkedin.com" in ats_url:
            await page.close()
            return {"success": False, "note": "No C&A ATS URL"}

        await active.wait_for_timeout(2000)
        await accept_cookies(active)
        await active.wait_for_timeout(4000)
        await active.screenshot(path=f"uploads/v2_ca_loaded_{ts()}.png", full_page=False)

        page_txt = (await active.inner_text("body")).lower()
        print(f"  Page text: {page_txt[:300]}")

        # Check for SAP SuccessFactors / Jobvite / etc
        src = await active.content()
        print(f"  SF={('successfactor' in src.lower())}, "
              f"GH={('greenhouse' in src.lower())}, "
              f"personio={('personio' in src.lower())}")

        # Look for "Apply now" button
        apply_clicked = False
        for btn_sel in [
            "button:has-text('Apply now')", "a:has-text('Apply now')",
            "button:has-text('Jetzt bewerben')", "a:has-text('Jetzt bewerben')",
            ".apply-btn", "[data-action='apply']",
        ]:
            try:
                btn = await active.query_selector(btn_sel)
                if btn and await btn.is_visible():
                    txt = (await btn.inner_text()).strip()
                    print(f"  Clicking: '{txt}'")
                    popup2 = None
                    async def on_p2(p):
                        nonlocal popup2
                        popup2 = p
                    active.on("popup", on_p2)
                    await btn.evaluate("el => el.click()")
                    await asyncio.sleep(4)
                    if popup2:
                        active = popup2
                        print(f"  SuccessFactors popup: {popup2.url[:80]}")
                    apply_clicked = True
                    break
            except Exception:
                pass

        await active.wait_for_timeout(4000)
        await active.screenshot(path=f"uploads/v2_ca_after_apply_{ts()}.png", full_page=False)

        # SuccessFactors typically requires login - check
        body2 = (await active.inner_text("body")).lower()
        print(f"  After apply click body (300): {body2[:300]}")

        if "sign in" in body2 or "login" in body2 or "create account" in body2:
            print("  ❌ SuccessFactors requires account login - cannot auto-apply")
            await page.close()
            return {"success": False, "note": "C&A SuccessFactors requires account login"}

        # Try to fill form
        success = await fill_form(active.main_frame, active, "ca")
        await page.close()
        return {"success": success, "note": "C&A SuccessFactors"}

    except Exception as e:
        print(f"  ERROR: {e}")
        try:
            await page.close()
        except Exception:
            pass
        return {"success": False, "note": str(e)}


async def main():
    print("=== APPLY V2 ===")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled", "--start-maximized"],
        )
        context = await browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
            locale="en-US", timezone_id="Europe/Berlin",
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        if SESSION_FILE.exists():
            s = json.loads(SESSION_FILE.read_text())
            cookies = s if isinstance(s, list) else s.get("cookies", [])
            if cookies:
                await context.add_cookies(cookies)

        results = {}

        # Job 1: Atolls → boards.greenhouse.io (which redirects to atolls.com/careers with iframe)
        results["atolls"] = await apply_job(
            context, "atolls",
            "https://boards.greenhouse.io/atolls/jobs/7564145",
            "Atolls - Senior Java Backend Engineer"
        )

        # Job 2: C&A → SuccessFactors
        results["ca"] = await apply_ca(context)

        # Job 3: Flix → flix.careers (Greenhouse-based)
        results["flix"] = await apply_job(
            context, "flix",
            "https://flix.careers/job/?jobid=8545023002&gh_src=fc023d502",
            "Flix - Senior Software Engineer"
        )

        await browser.close()

    print("\n" + "="*60)
    print("FINAL RESULTS:")
    for name, r in results.items():
        status = "✅ SUCCESS" if r.get("success") else "❌ FAILED"
        note = r.get("note", "")
        print(f"  {name}: {status}{' — ' + note if note else ''}")

asyncio.run(main())
