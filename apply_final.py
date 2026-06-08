"""
Final apply script — uses direct ATS URLs, fills Greenhouse forms in iframes or direct boards.
"""
import asyncio, json, re, sys, io
from pathlib import Path
from datetime import datetime
from playwright.async_api import async_playwright, Page, TimeoutError as PWTimeout

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
    "years_exp": "5",
    "cover_letter": (
        "I am a Senior Java Backend Engineer with 5+ years of experience building "
        "scalable microservices and distributed systems using Java, Spring Boot, "
        "Docker, Kubernetes, and AWS. I'm based in Berlin, Germany, available "
        "immediately, and excited about this opportunity."
    ),
}


def ss(name: str) -> str:
    ts = datetime.now().strftime("%H%M%S")
    return f"uploads/final_{name}_{ts}.png"


async def accept_cookies(page: Page, label=""):
    """Accept all cookie consent dialogs."""
    for _ in range(3):
        clicked = False
        for sel in [
            "button:has-text('Accept All Cookies')",
            "button:has-text('Accept all cookies')",
            "button:has-text('Accept All')",
            "button:has-text('Accept all')",
            "button:has-text('Accept')",
            "button:has-text('Alle akzeptieren')",
            "#onetrust-accept-btn-handler",
            "[data-testid='uc-accept-all-button']",
            "#CybotCookiebotDialogBodyButtonAccept",
            "button[id*='accept']",
            ".wp-consent-accept",
        ]:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    txt = (await el.inner_text()).strip()
                    print(f"  {label}[cookie] clicking '{txt}'")
                    await el.click()
                    await page.wait_for_timeout(1500)
                    clicked = True
                    break
            except Exception:
                pass
        if not clicked:
            break


async def fill_gh_frame(frame, page: Page, name: str) -> bool:
    """Fill fields in a Greenhouse iframe and submit."""
    print(f"  Filling Greenhouse frame ({frame.url[:60]})")
    await page.screenshot(path=ss(name + "_gh_before"), full_page=False)

    # Map field IDs to values
    fields = {
        "first_name": PROFILE["first_name"],
        "last_name": PROFILE["last_name"],
        "email": PROFILE["email"],
        "phone": PROFILE["phone"],
        "location": PROFILE["location"],
        "linkedin_url": PROFILE["linkedin"],
        "website": PROFILE["github"],
        "cover_letter": PROFILE["cover_letter"],
    }

    for field_id, value in fields.items():
        try:
            el = await frame.query_selector(f"#{field_id}")
            if not el:
                # Try by name or label
                el = await frame.query_selector(f"input[name='{field_id}'], textarea[name='{field_id}']")
            if el and await el.is_visible():
                tag = await el.evaluate("el => el.tagName.toLowerCase()")
                if tag == "textarea":
                    await el.fill(value)
                else:
                    await el.triple_click()
                    await el.fill(value)
                print(f"    filled #{field_id}: '{value[:30]}'")
        except Exception as e:
            pass

    # Handle unknown text inputs by label matching
    field_map = {
        r"first.?name|vorname": PROFILE["first_name"],
        r"last.?name|nachname": PROFILE["last_name"],
        r"email": PROFILE["email"],
        r"phone|tel": PROFILE["phone"],
        r"location|city|ort": PROFILE["location"],
        r"linkedin": PROFILE["linkedin"],
        r"github|website|portfolio": PROFILE["github"],
        r"cover.?letter|anschreiben|message": PROFILE["cover_letter"],
        r"salary|gehalt|compensation": PROFILE["salary"],
    }

    try:
        all_inputs = await frame.query_selector_all(
            "input[type='text']:not([readonly]), input[type='email'], "
            "input[type='tel'], input[type='number'], textarea"
        )
        for inp in all_inputs:
            try:
                if not await inp.is_visible():
                    continue
                existing = await inp.input_value()
                if existing:
                    continue  # Already filled
                label = ""
                inp_id = await inp.get_attribute("id")
                if inp_id:
                    lbl = await frame.query_selector(f"label[for='{inp_id}']")
                    if lbl:
                        label = (await lbl.inner_text()).strip()
                if not label:
                    label = (await inp.get_attribute("aria-label") or
                             await inp.get_attribute("placeholder") or
                             await inp.get_attribute("name") or "")
                for pattern, value in field_map.items():
                    if re.search(pattern, label, re.I):
                        await inp.triple_click()
                        await inp.fill(value)
                        print(f"    filled via pattern '{pattern}': '{value[:30]}'")
                        break
            except Exception:
                pass
    except Exception:
        pass

    # Upload resume
    try:
        file_inp = await frame.query_selector("input[type='file']")
        if file_inp:
            await file_inp.set_input_files(RESUME)
            print(f"    uploaded resume")
            await page.wait_for_timeout(2000)
    except Exception as e:
        print(f"    resume upload failed: {e}")

    # Handle dropdowns (country, etc.)
    try:
        selects = await frame.query_selector_all("select")
        for sel_el in selects:
            sel_id = await sel_el.get_attribute("id") or ""
            if "country" in sel_id.lower():
                # Try to select Germany
                for val in ["Germany", "DE", "276", "german"]:
                    try:
                        await sel_el.select_option(label=val)
                        print(f"    selected country: {val}")
                        break
                    except Exception:
                        try:
                            await sel_el.select_option(value=val)
                            break
                        except Exception:
                            pass
    except Exception:
        pass

    # Handle custom dropdowns/comboboxes for country
    try:
        country_input = await frame.query_selector("#country, input[id*='country']")
        if country_input:
            existing = await country_input.input_value()
            if not existing:
                await country_input.fill("Germany")
                await page.wait_for_timeout(500)
                # Look for dropdown option
                for opt_sel in ["li:has-text('Germany')", "[role='option']:has-text('Germany')"]:
                    try:
                        opt = await frame.query_selector(opt_sel)
                        if opt:
                            await opt.click()
                            print("    selected Germany from dropdown")
                            break
                    except Exception:
                        pass
    except Exception:
        pass

    await page.screenshot(path=ss(name + "_gh_filled"), full_page=False)

    # Submit
    for sub_sel in [
        "input[type='submit'][value*='Submit' i]",
        "input[type='submit']",
        "button[type='submit']",
        "#submit_app",
        "button:has-text('Submit Application')",
        "button:has-text('Submit')",
        "button:has-text('Apply Now')",
    ]:
        try:
            btn = await frame.query_selector(sub_sel)
            if btn and await btn.is_visible():
                val = (await btn.get_attribute("value") or
                       await btn.inner_text() or "submit")
                print(f"  [submit] '{val.strip()}'")
                await btn.click()
                await page.wait_for_timeout(6000)
                await page.screenshot(path=ss(name + "_gh_submitted"), full_page=False)
                body = (await page.inner_text("body")).lower()
                success = any(kw in body for kw in [
                    "thank you", "application received", "successfully submitted",
                    "submitted", "danke", "we received your application",
                    "your application has been", "applied successfully"
                ])
                print(f"  Post-submit body (200 chars): {body[:200]}")
                print(f"  Success keywords found: {success}")
                return success
        except Exception as e:
            print(f"  Submit error: {e}")

    print("  No submit button found!")
    return False


async def apply_greenhouse_board(context, name: str, url: str, job_name: str) -> dict:
    """Apply via a direct Greenhouse job board URL (no iframe)."""
    print(f"\n{'='*60}")
    print(f"Applying to {job_name} via Greenhouse: {url}")

    page = await context.new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=25_000)
        await page.wait_for_timeout(3000)
        await accept_cookies(page, label=job_name + " ")
        await page.wait_for_timeout(5000)  # Wait for JS

        await page.screenshot(path=ss(name + "_loaded"), full_page=False)
        print(f"  URL: {page.url[:100]}")

        # Check if form is in an iframe
        gh_frame = None
        for _ in range(10):  # Wait up to 10s for iframe
            for frame in page.frames:
                if "greenhouse.io" in frame.url or "job_app" in frame.url:
                    try:
                        inputs = await frame.query_selector_all("input")
                        if len(inputs) > 2:
                            gh_frame = frame
                            print(f"  Found Greenhouse iframe with {len(inputs)} inputs: {frame.url[:60]}")
                            break
                    except Exception:
                        pass
            if gh_frame:
                break
            await page.wait_for_timeout(1000)

        if gh_frame:
            success = await fill_gh_frame(gh_frame, page, name)
        else:
            # Form is directly on the page
            print(f"  No iframe found — trying main page form")
            success = await fill_gh_frame(page.main_frame, page, name)

        await page.close()
        return {"success": success, "url": page.url}

    except Exception as e:
        print(f"  ERROR: {e}")
        try:
            await page.screenshot(path=ss(name + "_error"), full_page=False)
        except Exception:
            pass
        try:
            await page.close()
        except Exception:
            pass
        return {"success": False, "note": str(e)}


async def get_ca_url_and_apply(context) -> dict:
    """Get C&A ATS URL from LinkedIn popup and apply."""
    print(f"\n{'='*60}")
    print("Applying to C&A Backend Engineer")

    page = await context.new_page()
    popup_page = None

    async def on_popup(p):
        nonlocal popup_page
        popup_page = p

    page.on("popup", on_popup)

    try:
        await page.goto("https://www.linkedin.com/jobs/view/4398579944",
                        wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(3000)

        # Get the href first to decode URL
        ats_url = None
        for sel in ["a:has-text('Apply')", "button:has-text('Apply')"]:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    txt = (await el.inner_text()).strip()
                    if "Easy" not in txt:
                        href = await el.get_attribute("href") or ""
                        print(f"  Apply href: {href[:120]}")
                        # Decode URL from LinkedIn safety redirect
                        if "linkedin.com/safety/go" in href:
                            import urllib.parse
                            parsed = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
                            if "url" in parsed:
                                ats_url = parsed["url"][0]
                                print(f"  Decoded ATS URL: {ats_url[:100]}")
                        await el.evaluate("el => el.click()")
                        await asyncio.sleep(4)
                        break
            except Exception:
                pass

        active = popup_page if popup_page else page
        if popup_page:
            print(f"  Popup URL: {popup_page.url[:100]}")
            ats_url = popup_page.url

        if not ats_url or "linkedin.com" in (ats_url or ""):
            print("  Could not get ATS URL — trying direct navigation")
            await page.close()
            return {"success": False, "note": "Could not get C&A ATS URL"}

        print(f"  C&A ATS: {ats_url[:100]}")
        await active.wait_for_timeout(2000)
        await accept_cookies(active, "C&A ")
        await active.wait_for_timeout(4000)
        await active.screenshot(path=ss("job2_ca_ats"), full_page=False)

        # Check for Greenhouse
        src = await active.content()
        print(f"  ATS hints: gh={('greenhouse' in src.lower() or 'gh_jid' in src.lower())}, "
              f"lever={('lever' in src.lower())}, personio={('personio' in src.lower())}, "
              f"sf={('successfactor' in src.lower())}")

        # Check for "Apply now" button on C&A careers page
        for btn_sel in [
            "button:has-text('Apply now')", "a:has-text('Apply now')",
            "button:has-text('Jetzt bewerben')", "a:has-text('Jetzt bewerben')",
            "a:has-text('Apply')",
        ]:
            try:
                btn = await active.query_selector(btn_sel)
                if btn and await btn.is_visible():
                    txt = (await btn.inner_text()).strip()
                    print(f"  Found button: '{txt}'")
                    popup2 = None
                    async def on_popup2(p):
                        nonlocal popup2
                        popup2 = p
                    active.on("popup", on_popup2)
                    await btn.evaluate("el => el.click()")
                    await asyncio.sleep(4)
                    if popup2:
                        active = popup2
                        print(f"  Popup 2: {popup2.url[:80]}")
                    break
            except Exception:
                pass

        await active.wait_for_timeout(3000)
        await active.screenshot(path=ss("job2_ca_final"), full_page=False)
        body = (await active.inner_text("body")).lower()
        print(f"  Page text (300 chars): {body[:300]}")

        # Check for greenhouse in iframe
        gh_frame = None
        for frame in active.frames:
            if "greenhouse.io" in frame.url:
                gh_frame = frame
                break

        if gh_frame:
            print("  Found Greenhouse iframe!")
            success = await fill_gh_frame(gh_frame, active, "job2_ca")
        else:
            # Try generic form fill
            inputs = await active.query_selector_all(
                "input[type='text'], input[type='email'], input[type='tel'], textarea"
            )
            print(f"  Found {len(inputs)} inputs on page")

            field_map_ca = {
                r"first.?name|vorname": PROFILE["first_name"],
                r"last.?name|nachname": PROFILE["last_name"],
                r"email": PROFILE["email"],
                r"phone|tel": PROFILE["phone"],
                r"location|city": PROFILE["location"],
                r"linkedin": PROFILE["linkedin"],
                r"message|cover|motivation|anschreiben": PROFILE["cover_letter"],
            }
            filled = 0
            for inp in inputs:
                try:
                    if not await inp.is_visible():
                        continue
                    existing = await inp.input_value()
                    if existing:
                        continue
                    label = ""
                    inp_id = await inp.get_attribute("id")
                    if inp_id:
                        lbl = await active.query_selector(f"label[for='{inp_id}']")
                        if lbl: label = (await lbl.inner_text()).strip()
                    if not label:
                        label = (await inp.get_attribute("aria-label") or
                                 await inp.get_attribute("placeholder") or
                                 await inp.get_attribute("name") or "")
                    for pattern, value in field_map_ca.items():
                        if re.search(pattern, label, re.I):
                            await inp.triple_click()
                            await inp.fill(value)
                            print(f"    filled '{label[:30]}': '{value[:30]}'")
                            filled += 1
                            break
                except Exception:
                    pass

            # Upload resume
            try:
                file_inp = await active.query_selector("input[type='file']")
                if file_inp:
                    await file_inp.set_input_files(RESUME)
                    print("    uploaded resume")
                    await active.wait_for_timeout(2000)
            except Exception:
                pass

            success = filled > 0
            print(f"  Filled {filled} fields, success={success}")
            await active.screenshot(path=ss("job2_ca_filled"), full_page=False)

        await page.close()
        return {"success": success, "url": ats_url}

    except Exception as e:
        print(f"  ERROR: {e}")
        try:
            await page.screenshot(path=ss("job2_ca_error"), full_page=False)
        except Exception:
            pass
        try:
            await page.close()
        except Exception:
            pass
        return {"success": False, "note": str(e)}


async def main():
    print("=== FINAL APPLY SCRIPT ===")
    print(f"Resume: {RESUME}")
    print(f"Profile: {PROFILE['first_name']} {PROFILE['last_name']} <{PROFILE['email']}>")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled", "--start-maximized"],
        )
        context = await browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            timezone_id="Europe/Berlin",
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        if SESSION_FILE.exists():
            session = json.loads(SESSION_FILE.read_text())
            cookies = session if isinstance(session, list) else session.get("cookies", [])
            if cookies and isinstance(cookies, list):
                await context.add_cookies(cookies)
                print(f"LinkedIn session: {len(cookies)} cookies")

        results = {}

        # Job 1: Atolls — Greenhouse on custom domain
        # Try boards.greenhouse.io direct first
        results["job1_atolls"] = await apply_greenhouse_board(
            context, "job1_atolls",
            "https://boards.greenhouse.io/atolls/jobs/7564145",
            "Atolls Senior Java Backend Engineer"
        )

        # Job 2: C&A — via LinkedIn popup, custom ATS
        results["job2_ca"] = await get_ca_url_and_apply(context)

        # Job 3: Flix — Greenhouse-based at flix.careers
        results["job3_flix"] = await apply_greenhouse_board(
            context, "job3_flix",
            "https://flix.careers/job/?jobid=8545023002&gh_src=fc023d502",
            "Flix Senior Software Engineer"
        )

        await browser.close()

    print("\n" + "="*60)
    print("RESULTS:")
    for name, r in results.items():
        status = "✅ SUCCESS" if r.get("success") else "❌ FAILED"
        note = r.get("note", r.get("url", ""))
        if note: note = str(note)[:80]
        print(f"  {name}: {status} — {note}")


asyncio.run(main())
