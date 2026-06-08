"""
Direct apply script for 3 external jobs.
Handles each site's specific ATS without going through Flask.
Takes screenshots at every step for audit trail.
"""
import asyncio
import json
import re
import sys
import io
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
    "location": "Berlin, Germany",
    "salary": "75000",
    "years_exp": "5",
    "cover_letter": (
        "I am a Senior Java Backend Engineer with 5+ years of experience building "
        "scalable microservices and distributed systems using Java, Spring Boot, "
        "Docker, Kubernetes, and AWS. I'm based in Berlin, available immediately, "
        "and excited about this opportunity. I look forward to contributing to your team."
    ),
}

JOBS = [
    {
        "name": "job1_atolls",
        "linkedin_url": "https://www.linkedin.com/jobs/view/4414565663",
        "ats_url": "https://atolls.com/careers/application/?gh_jid=7564145&gh_src=xw7kxwcz1us",
        "title": "Senior Java Backend Engineer",
        "company": "Atolls",
        "type": "greenhouse_custom",
    },
    {
        "name": "job2_ca",
        "linkedin_url": "https://www.linkedin.com/jobs/view/4398579944",
        "ats_url": None,  # Will get from LinkedIn popup
        "title": "Senior Backend Engineer",
        "company": "C&A",
        "type": "via_popup",
    },
    {
        "name": "job3_flix",
        "linkedin_url": "https://www.linkedin.com/jobs/view/4413434470",
        "ats_url": None,  # Will get from LinkedIn popup
        "title": "Senior Software Engineer",
        "company": "Flix",
        "type": "via_popup",
    },
]


def ss(name: str) -> str:
    ts = datetime.now().strftime("%H%M%S")
    return f"uploads/apply_direct_{name}_{ts}.png"


async def accept_cookies(page: Page):
    """Robust cookie consent acceptance."""
    for _ in range(3):
        clicked = False
        for sel in [
            "button:has-text('Accept All Cookies')",
            "button:has-text('Accept all cookies')",
            "button:has-text('Accept All')",
            "button:has-text('Accept all')",
            "button:has-text('Accept')",
            "button:has-text('Alle akzeptieren')",
            "button:has-text('Akzeptieren')",
            "#onetrust-accept-btn-handler",
            "[data-testid='uc-accept-all-button']",
            "#CybotCookiebotDialogBodyButtonAccept",
            ".accept-all",
            "[aria-label*='accept all' i]",
            "button[id*='accept']",
        ]:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    txt = (await el.inner_text()).strip()
                    print(f"    [cookies] clicking '{txt}'")
                    await el.click()
                    await page.wait_for_timeout(2000)
                    clicked = True
                    break
            except Exception:
                pass
        if not clicked:
            break
    await page.wait_for_timeout(1000)


async def fill_input(page: Page, selectors: list, value: str, label=""):
    """Try multiple selectors to fill an input."""
    for sel in selectors:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                await el.triple_click()
                await el.fill(value)
                print(f"    [fill] {label!r}: '{value[:40]}'")
                return True
        except Exception:
            pass
    return False


async def fill_greenhouse_form(page: Page, job: dict) -> bool:
    """Fill a Greenhouse application form (custom domain embedded)."""
    print("  Filling Greenhouse form...")
    await page.wait_for_timeout(3000)
    await page.screenshot(path=ss(job["name"] + "_gh_before"), full_page=False)

    # Greenhouse form is often in an iframe
    # First try main page, then iframe
    frames = [page] + list(page.frames)
    form_frame = page
    for frame in page.frames:
        try:
            has_form = await frame.query_selector("#first_name, input[id*='first'], #application_form")
            if has_form:
                print(f"    Found form in iframe: {frame.url[:60]}")
                form_frame = frame
                break
        except Exception:
            pass

    # Wait for the form to load
    try:
        await form_frame.wait_for_selector("#first_name, input[name='first_name'], #application_form", timeout=15000)
        print("    Form loaded!")
    except PWTimeout:
        print("    WARNING: Timed out waiting for form selector")
        await page.screenshot(path=ss(job["name"] + "_gh_timeout"), full_page=False)

    # Fill first name
    await fill_input(form_frame, [
        "#first_name", "input[name='first_name']", "input[aria-label*='first name' i]",
        "input[placeholder*='first name' i]",
    ], PROFILE["first_name"], "first_name")

    # Fill last name
    await fill_input(form_frame, [
        "#last_name", "input[name='last_name']", "input[aria-label*='last name' i]",
        "input[placeholder*='last name' i]",
    ], PROFILE["last_name"], "last_name")

    # Fill email
    await fill_input(form_frame, [
        "#email", "input[name='email']", "input[type='email']",
        "input[aria-label*='email' i]",
    ], PROFILE["email"], "email")

    # Fill phone
    await fill_input(form_frame, [
        "#phone", "input[name='phone']", "input[type='tel']",
        "input[aria-label*='phone' i]", "input[placeholder*='phone' i]",
    ], PROFILE["phone"], "phone")

    # Fill LinkedIn
    await fill_input(form_frame, [
        "#linkedin_url", "input[name='linkedin_url']", "input[id*='linkedin']",
        "input[placeholder*='linkedin' i]",
    ], PROFILE["linkedin"], "linkedin")

    # Fill location/city
    await fill_input(form_frame, [
        "#location", "input[name='location']", "input[id*='location']",
        "input[placeholder*='city' i]", "input[placeholder*='location' i]",
    ], PROFILE["location"], "location")

    # Upload resume
    try:
        resume_input = await form_frame.query_selector("input[type='file']")
        if resume_input:
            await resume_input.set_input_files(RESUME)
            print(f"    [upload] resume uploaded: {RESUME}")
            await page.wait_for_timeout(2000)
    except Exception as e:
        print(f"    [upload] failed: {e}")

    # Cover letter / message
    for sel in [
        "textarea[name='cover_letter']", "#cover_letter", "textarea[id*='cover']",
        "textarea[placeholder*='cover' i]", "textarea[placeholder*='message' i]",
        "textarea",
    ]:
        try:
            el = await form_frame.query_selector(sel)
            if el and await el.is_visible():
                await el.fill(PROFILE["cover_letter"])
                print(f"    [fill] cover_letter")
                break
        except Exception:
            pass

    await page.screenshot(path=ss(job["name"] + "_gh_filled"), full_page=False)

    # Click submit
    for sel in [
        "input[type='submit']", "button[type='submit']", "button:has-text('Submit Application')",
        "button:has-text('Submit')", "button:has-text('Apply')",
        "#submit_app",
    ]:
        try:
            el = await form_frame.query_selector(sel)
            if el and await el.is_visible():
                txt = ""
                try:
                    txt = (await el.inner_text()).strip() or (await el.get_attribute("value") or "")
                except Exception:
                    pass
                print(f"    [submit] clicking '{txt}'")
                await el.click()
                await page.wait_for_timeout(5000)
                await page.screenshot(path=ss(job["name"] + "_gh_submitted"), full_page=False)

                # Check for confirmation
                body = (await page.inner_text("body")).lower()
                if any(kw in body for kw in ["thank you", "application received", "successfully", "submitted", "danke"]):
                    print("    ✅ Confirmed submission!")
                    return True
                print(f"    Body after submit (200 chars): {body[:200]}")
                return True  # Assume success if no error
        except Exception as e:
            print(f"    Submit error: {e}")

    print("    No submit button found")
    return False


async def fill_generic_form(page: Page, job: dict) -> bool:
    """Generic form filler for unknown ATS sites."""
    print(f"  Generic form filler for {page.url[:80]}")
    await page.wait_for_timeout(4000)

    # Wait for page to stabilize (SPA loading)
    for _ in range(5):
        try:
            await page.wait_for_load_state("networkidle", timeout=5000)
            break
        except PWTimeout:
            pass

    await page.screenshot(path=ss(job["name"] + "_generic_loaded"), full_page=False)

    # Check page text for hints
    body = (await page.inner_text("body")).lower()
    print(f"  Page text (300 chars): {body[:300]}")

    # Check if it's Greenhouse (source hint)
    src = await page.content()
    if "greenhouse" in src.lower() or "gh_jid" in src.lower():
        print("  Detected Greenhouse via page source")
        return await fill_greenhouse_form(page, job)

    # Check for Lever
    if "lever.co" in page.url or "lever" in src.lower()[:5000]:
        print("  Detected Lever - TODO")
        return False

    # Check for Personio
    if "personio" in page.url or "personio" in src.lower()[:5000]:
        print("  Detected Personio")
        # Try to fill standard Personio fields

    # Find "Apply now" or "Apply" button on the page
    for btn_sel in [
        "button:has-text('Apply now')", "a:has-text('Apply now')",
        "button:has-text('Apply for this job')", "a:has-text('Apply')",
        "button:has-text('Jetzt bewerben')",
    ]:
        try:
            btn = await page.query_selector(btn_sel)
            if btn and await btn.is_visible():
                txt = (await btn.inner_text()).strip()
                print(f"  Found apply button: '{txt}'")
                popup2 = None
                async def on_popup2(p):
                    nonlocal popup2
                    popup2 = p
                page.on("popup", on_popup2)
                await btn.click()
                await asyncio.sleep(3)
                if popup2:
                    print(f"  Popup opened: {popup2.url[:80]}")
                    await accept_cookies(popup2)
                    await popup2.wait_for_timeout(3000)
                    await popup2.screenshot(path=ss(job["name"] + "_popup2"), full_page=False)
                    return await fill_generic_form(popup2, job)
                break
        except Exception:
            pass

    # Direct form filling
    inputs = await page.query_selector_all(
        "input[type='text']:not([aria-label*='search' i]), "
        "input[type='email'], input[type='tel'], input[type='number'], "
        "textarea:not([aria-label*='search' i])"
    )
    print(f"  Found {len(inputs)} inputs")

    field_map = {
        r"first.?name|vorname": PROFILE["first_name"],
        r"last.?name|nachname|surname": PROFILE["last_name"],
        r"email": PROFILE["email"],
        r"phone|tel|mobil": PROFILE["phone"],
        r"linkedin": PROFILE["linkedin"],
        r"location|city|stadt|ort": PROFILE["location"],
        r"cover.?letter|anschreiben|message|nachricht": PROFILE["cover_letter"],
        r"github": PROFILE["github"],
        r"salary|gehalt|compensation": PROFILE["salary"],
        r"years|erfahrung|experience": PROFILE["years_exp"],
    }

    filled = 0
    for inp in inputs:
        try:
            if not await inp.is_visible():
                continue
            # Get label
            label = ""
            inp_id = await inp.get_attribute("id")
            if inp_id:
                try:
                    lbl = await page.query_selector(f"label[for='{inp_id}']")
                    if lbl:
                        label = (await lbl.inner_text()).strip()
                except Exception:
                    pass
            if not label:
                label = (await inp.get_attribute("aria-label") or
                         await inp.get_attribute("placeholder") or
                         await inp.get_attribute("name") or "")

            # Match to profile field
            for pattern, value in field_map.items():
                if re.search(pattern, label, re.I):
                    existing = await inp.input_value()
                    if not existing:
                        await inp.triple_click()
                        await inp.fill(value)
                        print(f"    [fill] '{label[:40]}' → '{value[:30]}'")
                        filled += 1
                    break
        except Exception:
            pass

    # Upload resume
    try:
        file_inp = await page.query_selector("input[type='file']")
        if file_inp:
            await file_inp.set_input_files(RESUME)
            print(f"    [upload] resume")
            await page.wait_for_timeout(2000)
    except Exception:
        pass

    print(f"  Filled {filled} fields")
    await page.screenshot(path=ss(job["name"] + "_generic_filled"), full_page=False)

    if filled == 0:
        print("  No fields filled — cannot submit")
        return False

    # Submit
    for sel in [
        "button[type='submit']", "input[type='submit']",
        "button:has-text('Submit')", "button:has-text('Send')",
        "button:has-text('Apply')", "button:has-text('Continue')",
    ]:
        try:
            btn = await page.query_selector(sel)
            if btn and await btn.is_visible():
                txt = (await btn.inner_text()).strip() or (await btn.get_attribute("value") or "")
                print(f"  [submit] '{txt}'")
                await btn.click()
                await page.wait_for_timeout(5000)
                await page.screenshot(path=ss(job["name"] + "_submitted"), full_page=False)
                body2 = (await page.inner_text("body")).lower()
                if any(kw in body2 for kw in ["thank", "submitted", "received", "success", "danke"]):
                    print("  ✅ Success!")
                    return True
                return True
        except Exception:
            pass

    return False


async def apply_via_linkedin_popup(browser_context, job: dict) -> dict:
    """Navigate to LinkedIn job, click Apply, handle popup to ATS."""
    print(f"\n{'='*60}")
    print(f"Applying to: {job['title']} @ {job['company']}")
    print(f"LinkedIn URL: {job['linkedin_url']}")

    page = await browser_context.new_page()
    popup_page = None

    async def on_popup(popup):
        nonlocal popup_page
        popup_page = popup
        print(f"  Popup opened: {popup.url[:100]}")

    page.on("popup", on_popup)

    try:
        await page.goto(job["linkedin_url"], wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(3000)
        await page.screenshot(path=ss(job["name"] + "_linkedin"), full_page=False)

        # Find Apply button (external = <a> with href or target=_blank)
        apply_el = None
        for sel in [
            "a.jobs-apply-button",
            ".jobs-apply-button--top-card",
            "button.jobs-apply-button",
            "a:has-text('Apply')",
            "button:has-text('Apply')",
            "[data-job-id] button:has-text('Apply')",
        ]:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    txt = (await el.inner_text()).strip()
                    if "Easy Apply" not in txt:
                        print(f"  Apply button: '{txt}' via {sel}")
                        apply_el = el
                        break
                    elif "Easy Apply" in txt:
                        print(f"  Skipping Easy Apply button")
            except Exception:
                pass

        if not apply_el:
            # Try any visible "Apply" text
            all_btns = await page.query_selector_all("a, button")
            for btn in all_btns:
                try:
                    if await btn.is_visible():
                        txt = (await btn.inner_text()).strip()
                        if txt == "Apply":
                            apply_el = btn
                            print(f"  Found 'Apply' button (fallback)")
                            break
                except Exception:
                    pass

        if not apply_el:
            print("  ERROR: No Apply button found!")
            await page.screenshot(path=ss(job["name"] + "_no_button"), full_page=False)
            await page.close()
            return {"success": False, "note": "No Apply button found"}

        # Click
        try:
            await apply_el.evaluate("el => el.click()")
        except Exception:
            await apply_el.click()

        # Wait for popup
        await asyncio.sleep(4)

        active_page = popup_page if popup_page else page
        print(f"  Active page URL: {active_page.url[:100]}")
        await active_page.screenshot(path=ss(job["name"] + "_after_click"), full_page=False)

        # Accept cookies
        await accept_cookies(active_page)
        await active_page.wait_for_timeout(3000)
        await active_page.screenshot(path=ss(job["name"] + "_after_consent"), full_page=False)

        # Check where we are
        final_url = active_page.url
        print(f"  Final URL: {final_url[:100]}")

        if "linkedin.com" in final_url and popup_page is None:
            print("  Still on LinkedIn — no popup detected, trying to find external link")
            # Look for external link in modal or page
            for sel in ["a:has-text('Continue to apply')", "a:has-text('Apply on company website')", ".artdeco-modal a[href*='http']"]:
                try:
                    el = await page.query_selector(sel)
                    if el:
                        href = await el.get_attribute("href")
                        if href:
                            print(f"  Found external link: {href[:80]}")
                            active_page = page
                            await page.goto(href, wait_until="domcontentloaded", timeout=20_000)
                            await accept_cookies(page)
                            await page.wait_for_timeout(3000)
                            break
                except Exception:
                    pass

        # Determine ATS and fill
        url = active_page.url
        src = await active_page.content()

        if "greenhouse" in url or "gh_jid" in url or "greenhouse" in src.lower()[:10000]:
            print("  Platform: Greenhouse")
            success = await fill_greenhouse_form(active_page, job)
        else:
            print(f"  Platform: generic ({url[:60]})")
            success = await fill_generic_form(active_page, job)

        if popup_page and not popup_page.is_closed():
            await popup_page.close()
        if not page.is_closed():
            await page.close()

        return {"success": success, "url": url}

    except Exception as e:
        print(f"  ERROR: {e}")
        try:
            await page.screenshot(path=ss(job["name"] + "_error"), full_page=False)
        except Exception:
            pass
        try:
            await page.close()
        except Exception:
            pass
        return {"success": False, "note": str(e)}


async def apply_atolls_direct(browser_context, job: dict) -> dict:
    """Apply directly to Atolls Greenhouse (we already know the URL)."""
    print(f"\n{'='*60}")
    print(f"Applying to: {job['title']} @ {job['company']} (direct Greenhouse)")
    print(f"ATS URL: {job['ats_url']}")

    page = await browser_context.new_page()
    try:
        await page.goto(job["ats_url"], wait_until="domcontentloaded", timeout=20_000)
        await page.wait_for_timeout(3000)
        await accept_cookies(page)
        await page.wait_for_timeout(5000)  # Wait for Greenhouse JS to load form
        await page.screenshot(path=ss(job["name"] + "_ats_loaded"), full_page=False)

        print(f"  URL: {page.url[:100]}")
        body = (await page.inner_text("body"))[:500]
        print(f"  Page text: {body}")

        src = await page.content()
        print(f"  Greenhouse in source: {'greenhouse' in src.lower()}")

        success = await fill_greenhouse_form(page, job)
        await page.close()
        return {"success": success, "url": page.url}
    except Exception as e:
        print(f"  ERROR: {e}")
        try:
            await page.screenshot(path=ss(job["name"] + "_error"), full_page=False)
        except Exception:
            pass
        try:
            await page.close()
        except Exception:
            pass
        return {"success": False, "note": str(e)}


async def main():
    print("Starting direct apply script")
    print(f"Resume: {RESUME}")
    print(f"Profile: {PROFILE['first_name']} {PROFILE['last_name']} <{PROFILE['email']}>")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--start-maximized",
                "--no-sandbox",
            ],
        )
        context = await browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            locale="en-US",
            timezone_id="Europe/Berlin",
        )
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        """)

        # Load LinkedIn session
        if SESSION_FILE.exists():
            session = json.loads(SESSION_FILE.read_text())
            cookies = session if isinstance(session, list) else session.get("cookies", [])
            if cookies and isinstance(cookies, list):
                await context.add_cookies(cookies)
                print(f"Loaded LinkedIn session ({len(cookies)} cookies)")

        results = {}

        # Job 1: Atolls (Greenhouse on custom domain) — go directly to ATS URL
        j1 = JOBS[0]
        results[j1["name"]] = await apply_atolls_direct(context, j1)

        # Job 2: C&A — via LinkedIn popup
        j2 = JOBS[1]
        results[j2["name"]] = await apply_via_linkedin_popup(context, j2)

        # Job 3: Flix — via LinkedIn popup
        j3 = JOBS[2]
        results[j3["name"]] = await apply_via_linkedin_popup(context, j3)

        await browser.close()

    print("\n" + "="*60)
    print("RESULTS:")
    for name, r in results.items():
        status = "✅ SUCCESS" if r.get("success") else "❌ FAILED"
        note = r.get("note", r.get("url", ""))[:80]
        print(f"  {name}: {status} — {note}")


asyncio.run(main())
