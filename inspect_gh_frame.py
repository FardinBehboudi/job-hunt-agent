"""Inspect all inputs in the Greenhouse iframe and the C&A/Flix ATS URLs."""
import asyncio, json
from pathlib import Path
from playwright.async_api import async_playwright
from playwright.async_api import TimeoutError as PWTimeout

SESSION_FILE = Path("uploads/linkedin_session.json")

async def inspect_frame(page, frame, name):
    print(f"\n=== Frame: {name} ({frame.url[:80]}) ===")
    try:
        inputs = await frame.query_selector_all("input:not([type='hidden']), textarea, select")
        print(f"  Total inputs: {len(inputs)}")
        for inp in inputs:
            try:
                typ = await inp.get_attribute("type") or "text"
                id_attr = await inp.get_attribute("id") or ""
                name_attr = await inp.get_attribute("name") or ""
                placeholder = await inp.get_attribute("placeholder") or ""
                aria = await inp.get_attribute("aria-label") or ""
                # Get associated label
                label_txt = ""
                if id_attr:
                    try:
                        lbl = await frame.query_selector(f"label[for='{id_attr}']")
                        if lbl: label_txt = (await lbl.inner_text()).strip()[:50]
                    except Exception:
                        pass
                print(f"    [{typ}] id={id_attr!r} name={name_attr!r} ph={placeholder!r} aria={aria!r} lbl={label_txt!r}")
            except Exception:
                pass

        # File inputs
        file_inputs = await frame.query_selector_all("input[type='file']")
        print(f"  File inputs: {len(file_inputs)}")

        # Submit buttons
        subs = await frame.query_selector_all("button[type='submit'], input[type='submit'], button:has-text('Submit')")
        for s in subs:
            try:
                txt = (await s.inner_text()).strip() or (await s.get_attribute("value") or "")
                print(f"  Submit button: '{txt}'")
            except Exception:
                pass
    except Exception as e:
        print(f"  Error: {e}")


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False, args=["--start-maximized"])
        context = await browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
            locale="en-US",
        )
        # Load session for LinkedIn
        if SESSION_FILE.exists():
            session = json.loads(SESSION_FILE.read_text())
            cookies = session if isinstance(session, list) else session.get("cookies", [])
            if cookies: await context.add_cookies(cookies)

        page = await context.new_page()

        # --- Atolls Greenhouse iframe ---
        print("=== ATOLLS GREENHOUSE ===")
        await page.goto("https://atolls.com/careers/application/?gh_jid=7564145&gh_src=xw7kxwcz1us",
                        wait_until="domcontentloaded", timeout=20_000)
        await page.wait_for_timeout(2000)
        # Accept cookies
        for sel in ["button:has-text('Accept')"]:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    await el.click()
                    await page.wait_for_timeout(2000)
                    break
            except Exception: pass
        await page.wait_for_timeout(6000)

        # Find Greenhouse iframe
        gh_frame = None
        for frame in page.frames:
            if "greenhouse.io" in frame.url:
                gh_frame = frame
                print(f"Found GH frame: {frame.url[:100]}")
                break
        if gh_frame:
            await inspect_frame(page, gh_frame, "greenhouse_iframe")
        else:
            print("No greenhouse frame found!")

        # --- C&A via LinkedIn popup ---
        print("\n=== C&A via LinkedIn ===")
        page2 = await context.new_page()
        popup_ca = None
        async def on_popup(p):
            nonlocal popup_ca
            popup_ca = p
            print(f"Popup: {p.url[:100]}")
        page2.on("popup", on_popup)
        try:
            await page2.goto("https://www.linkedin.com/jobs/view/4398579944",
                            wait_until="domcontentloaded", timeout=30_000)
            await page2.wait_for_timeout(3000)
            # Find Apply button
            for sel in ["a:has-text('Apply')", "button:has-text('Apply')"]:
                try:
                    el = await page2.query_selector(sel)
                    if el and await el.is_visible():
                        txt = (await el.inner_text()).strip()
                        if "Easy" not in txt:
                            href = await el.get_attribute("href") or ""
                            print(f"Apply button: '{txt}' href={href[:100]!r}")
                            await el.evaluate("el => el.click()")
                            await asyncio.sleep(4)
                            break
                except Exception: pass
            if popup_ca:
                print(f"C&A ATS URL: {popup_ca.url[:100]}")
                await popup_ca.wait_for_timeout(2000)
                await popup_ca.screenshot(path="uploads/debug_ca_ats.png", full_page=False)
                # Accept cookies
                for sel in ["button:has-text('Accept All Cookies')", "button:has-text('Accept')"]:
                    try:
                        el = await popup_ca.query_selector(sel)
                        if el and await el.is_visible():
                            await el.click()
                            await popup_ca.wait_for_timeout(2000)
                            break
                    except Exception: pass
                await popup_ca.wait_for_timeout(4000)
                await popup_ca.screenshot(path="uploads/debug_ca_ats2.png", full_page=False)
                await inspect_frame(popup_ca, popup_ca.main_frame, "ca_main")
                # Check child frames
                for frame in popup_ca.frames:
                    if frame != popup_ca.main_frame:
                        await inspect_frame(popup_ca, frame, f"ca_frame_{frame.url[:30]}")
        except Exception as e:
            print(f"C&A error: {e}")
        await page2.close()

        # --- Flix via LinkedIn popup ---
        print("\n=== FLIX via LinkedIn ===")
        page3 = await context.new_page()
        popup_flix = None
        async def on_popup3(p):
            nonlocal popup_flix
            popup_flix = p
            print(f"Flix popup: {p.url[:100]}")
        page3.on("popup", on_popup3)
        try:
            await page3.goto("https://www.linkedin.com/jobs/view/4413434470",
                            wait_until="domcontentloaded", timeout=30_000)
            await page3.wait_for_timeout(3000)
            for sel in ["a:has-text('Apply')", "button:has-text('Apply')"]:
                try:
                    el = await page3.query_selector(sel)
                    if el and await el.is_visible():
                        txt = (await el.inner_text()).strip()
                        if "Easy" not in txt:
                            href = await el.get_attribute("href") or ""
                            print(f"Flix Apply: '{txt}' href={href[:100]!r}")
                            await el.evaluate("el => el.click()")
                            await asyncio.sleep(5)
                            break
                except Exception: pass
            if popup_flix:
                print(f"Flix ATS URL: {popup_flix.url[:120]}")
                await popup_flix.wait_for_timeout(3000)
                await popup_flix.screenshot(path="uploads/debug_flix_ats.png", full_page=False)
                await accept_cookies_p(popup_flix)
                await popup_flix.wait_for_timeout(5000)
                await popup_flix.screenshot(path="uploads/debug_flix_ats2.png", full_page=False)
                # Check for iframe or main form
                src = await popup_flix.content()
                print(f"Flix source hints: greenhouse={('greenhouse' in src.lower())}, lever={('lever' in src.lower())}, personio={('personio' in src.lower())}")
                await inspect_frame(popup_flix, popup_flix.main_frame, "flix_main")
                for frame in popup_flix.frames:
                    if frame != popup_flix.main_frame and "about:blank" not in frame.url:
                        await inspect_frame(popup_flix, frame, f"flix_frame")
            else:
                print("No Flix popup detected")
                print(f"Current URL: {page3.url}")
        except Exception as e:
            print(f"Flix error: {e}")
        await page3.close()

        await browser.close()


async def accept_cookies_p(page):
    for sel in [
        "button:has-text('Accept All Cookies')", "button:has-text('Accept all')",
        "button:has-text('Accept')", "#onetrust-accept-btn-handler",
    ]:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                await el.click()
                await page.wait_for_timeout(2000)
                break
        except Exception:
            pass

asyncio.run(main())
