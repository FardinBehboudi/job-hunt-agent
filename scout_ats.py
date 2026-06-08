"""Scout the actual ATS pages directly, and check job 3 (FlixBus)."""
import asyncio, json
from pathlib import Path
from playwright.async_api import async_playwright

RESUME = str(Path("uploads/resume_en.pdf").resolve())
SESSION_FILE = Path("uploads/linkedin_session.json")

ATS_URLS = {
    "atolls_greenhouse": "https://atolls.com/careers/application/?gh_jid=7564145&gh_src=xw7kxwcz1us",
    "ca_career": "https://www.c-and-a.com/eu/en/corporate/company/careers/jobs/senior-backend-engineer-mwd-64600.html",
    "job3_linkedin": "https://www.linkedin.com/jobs/view/4413434470",
}

async def scout_ats(page, name: str, url: str):
    print(f"\n=== Scouting {name} ===")
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
    except Exception as e:
        print(f"  Navigation error: {e}")
        try:
            await page.goto(url, wait_until="commit", timeout=15_000)
        except Exception as e2:
            print(f"  Also failed with commit: {e2}")
            return

    await page.wait_for_timeout(3000)
    print(f"  URL: {page.url[:120]}")
    await page.screenshot(path=f"uploads/ats_{name}_1.png", full_page=False)

    # Accept cookie consent
    for sel in [
        "button:has-text('Accept All Cookies')",
        "button:has-text('Accept all')",
        "button:has-text('Accept All')",
        "button:has-text('Accept')",
        "#onetrust-accept-btn-handler",
        "[data-testid='uc-accept-all-button']",
        "button:has-text('Alle akzeptieren')",
        ".accept-all",
        "[aria-label*='accept all' i]",
    ]:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                txt = (await el.inner_text()).strip()
                print(f"  Cookie consent: clicking '{txt}'")
                await el.click()
                await page.wait_for_timeout(2000)
                break
        except:
            pass

    await page.wait_for_timeout(3000)
    await page.screenshot(path=f"uploads/ats_{name}_2_after_consent.png", full_page=False)
    print(f"  URL after consent: {page.url[:100]}")

    # Page text
    try:
        txt = (await page.inner_text("body"))[:800]
        print(f"  Page text:\n{txt}\n---")
    except:
        pass

    # List inputs
    inputs = await page.query_selector_all("input:not([type='hidden']):not([type='submit']):not([type='button']), textarea, select")
    print(f"  Found {len(inputs)} form fields:")
    for inp in inputs[:20]:
        try:
            typ = await inp.get_attribute("type") or "text"
            name_attr = await inp.get_attribute("name") or ""
            placeholder = await inp.get_attribute("placeholder") or ""
            label_txt = ""
            try:
                inp_id = await inp.get_attribute("id")
                if inp_id:
                    lbl = await page.query_selector(f"label[for='{inp_id}']")
                    if lbl: label_txt = (await lbl.inner_text()).strip()[:60]
            except:
                pass
            aria = await inp.get_attribute("aria-label") or ""
            print(f"    [{typ}] name={name_attr!r} aria={aria!r} placeholder={placeholder!r} label={label_txt!r}")
        except:
            pass

    # Check for file upload
    file_inputs = await page.query_selector_all("input[type='file']")
    print(f"  File inputs: {len(file_inputs)}")

    # Check page source for ATS hints
    try:
        src = await page.content()
        for hint in ["greenhouse", "lever", "ashby", "workday", "successfactor", "smartrecruiters", "personio", "teamtailor", "gh_jid", "job-board"]:
            if hint in src.lower():
                print(f"  Page source hint: '{hint}'")
    except:
        pass


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled", "--start-maximized"],
        )
        context = await browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
            locale="en-US",
        )
        if SESSION_FILE.exists():
            session = json.loads(SESSION_FILE.read_text())
            cookies = session if isinstance(session, list) else session.get("cookies", [])
            if cookies and isinstance(cookies, list):
                await context.add_cookies(cookies)

        page = await context.new_page()

        for name, url in ATS_URLS.items():
            try:
                await scout_ats(page, name, url)
            except Exception as e:
                print(f"Error: {e}")
            await page.wait_for_timeout(1000)

        await browser.close()
        print("\nDone. Check uploads/ats_*.png")

asyncio.run(main())
