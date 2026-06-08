"""
Scout the 3 external job application pages to understand their form structures.
Takes screenshots at each step so we know exactly what to fill.
"""
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright

SESSION_FILE = Path("uploads/linkedin_session.json")
JOBS = [
    ("https://www.linkedin.com/jobs/view/4414565663", "job1_atolls"),
    ("https://www.linkedin.com/jobs/view/4398579944", "job2_ca"),
    ("https://www.linkedin.com/jobs/view/4413434470", "job3_flix"),
]

async def scout_job(page, li_url: str, name: str):
    print(f"\n=== Scouting {name} ===")
    print(f"LinkedIn URL: {li_url}")

    await page.goto(li_url, wait_until="domcontentloaded", timeout=30_000)
    await page.wait_for_timeout(3000)
    await page.screenshot(path=f"uploads/scout_{name}_1_linkedin.png", full_page=False)
    print(f"  Saved LinkedIn screenshot")

    # Find the Apply button
    apply_btn = None
    for sel in [
        "button:has-text('Apply')",
        "a:has-text('Apply')",
        ".jobs-apply-button",
        "[data-control-name='jobdetails_topcard_inapply']",
    ]:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                txt = (await el.inner_text()).strip()
                print(f"  Found button: '{txt}' via {sel}")
                apply_btn = el
                break
        except:
            pass

    if not apply_btn:
        print("  No apply button found!")
        return

    # Track popup
    popup_page = None
    async def on_popup(popup):
        nonlocal popup_page
        popup_page = popup
        print(f"  Popup: {popup.url[:80]}")

    page.on("popup", on_popup)

    btn_txt = (await apply_btn.inner_text()).strip()
    print(f"  Clicking: '{btn_txt}'")
    try:
        await apply_btn.evaluate("el => el.click()")
    except:
        try:
            await apply_btn.click()
        except Exception as e:
            print(f"  Click error: {e}")

    await asyncio.sleep(3)

    # Use popup if opened
    active = popup_page if popup_page else page
    await asyncio.sleep(2)

    print(f"  Current URL: {active.url[:100]}")
    await active.screenshot(path=f"uploads/scout_{name}_2_after_click.png", full_page=False)

    # Try to accept cookie consent
    for _ in range(3):
        for sel in [
            "button:has-text('Accept All Cookies')",
            "button:has-text('Accept All')",
            "button:has-text('Accept all')",
            "button:has-text('Accept')",
            "button:has-text('Alle akzeptieren')",
            "#onetrust-accept-btn-handler",
            "[data-testid='uc-accept-all-button']",
            ".accept-all",
        ]:
            try:
                el = await active.query_selector(sel)
                if el and await el.is_visible():
                    txt = (await el.inner_text()).strip()
                    print(f"  Clicking consent: '{txt}'")
                    await el.click()
                    await asyncio.sleep(2)
                    break
            except:
                pass
        break

    await active.wait_for_timeout(3000)
    await active.screenshot(path=f"uploads/scout_{name}_3_after_consent.png", full_page=False)
    print(f"  URL after consent: {active.url[:100]}")

    # Wait longer for SPA to load
    await active.wait_for_timeout(4000)
    await active.screenshot(path=f"uploads/scout_{name}_4_loaded.png", full_page=False)

    # Get page text to understand structure
    try:
        body_text = await active.inner_text("body")
        print(f"  Page text (first 500 chars):\n{body_text[:500]}")
    except:
        pass

    # List all forms and inputs
    try:
        inputs = await active.query_selector_all("input:not([type='hidden']), textarea, select")
        print(f"  Found {len(inputs)} inputs/selects")
        for inp in inputs[:15]:
            try:
                typ = await inp.get_attribute("type") or "text"
                name_attr = await inp.get_attribute("name") or ""
                placeholder = await inp.get_attribute("placeholder") or ""
                label_txt = ""
                try:
                    inp_id = await inp.get_attribute("id")
                    if inp_id:
                        lbl = await active.query_selector(f"label[for='{inp_id}']")
                        if lbl:
                            label_txt = (await lbl.inner_text()).strip()[:50]
                except:
                    pass
                print(f"    [{typ}] name={name_attr!r} placeholder={placeholder!r} label={label_txt!r}")
            except:
                pass
    except Exception as e:
        print(f"  Input enumeration error: {e}")

    # Check for file upload
    try:
        file_inputs = await active.query_selector_all("input[type='file']")
        print(f"  File inputs: {len(file_inputs)}")
    except:
        pass

    print(f"  === Done scouting {name} ===")


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled", "--start-maximized"],
        )
        ctx_kw = dict(
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
            locale="en-US",
        )
        if SESSION_FILE.exists():
            session = json.loads(SESSION_FILE.read_text())
            cookies = session if isinstance(session, list) else session.get("cookies", [])
            ctx_kw["storage_state"] = str(SESSION_FILE) if "origins" in session else None
            context = await browser.new_context(**ctx_kw)
            if cookies and isinstance(cookies, list):
                await context.add_cookies(cookies)
        else:
            context = await browser.new_context(**ctx_kw)

        page = await context.new_page()

        # Warm up LinkedIn session
        await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=20_000)
        await page.wait_for_timeout(2000)
        print(f"LinkedIn status: {'logged in' if 'feed' in page.url else 'NOT logged in: ' + page.url}")

        for li_url, name in JOBS:
            try:
                await scout_job(page, li_url, name)
                await page.wait_for_timeout(2000)
            except Exception as e:
                print(f"Error scouting {name}: {e}")

        await browser.close()
        print("\nDone. Check uploads/scout_*.png")

asyncio.run(main())
