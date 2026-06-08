"""Debug the Atolls Greenhouse form structure."""
import asyncio, json
from pathlib import Path
from playwright.async_api import async_playwright

SESSION_FILE = Path("uploads/linkedin_session.json")

async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False, args=["--start-maximized"])
        context = await browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
        )
        page = await context.new_page()

        print("Navigating to Atolls Greenhouse...")
        await page.goto(
            "https://atolls.com/careers/application/?gh_jid=7564145&gh_src=xw7kxwcz1us",
            wait_until="domcontentloaded", timeout=20_000
        )
        await page.wait_for_timeout(3000)
        await page.screenshot(path="uploads/debug_atolls_1_initial.png", full_page=True)

        # Accept cookies
        for sel in ["button:has-text('Accept')", "#onetrust-accept-btn-handler"]:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    txt = (await el.inner_text()).strip()
                    print(f"Clicking: '{txt}'")
                    await el.click()
                    await page.wait_for_timeout(3000)
                    break
            except Exception:
                pass

        await page.screenshot(path="uploads/debug_atolls_2_after_consent.png", full_page=True)

        # Check for secondary consent dialog
        print("\n=== All buttons on page ===")
        buttons = await page.query_selector_all("button")
        for btn in buttons:
            try:
                if await btn.is_visible():
                    txt = (await btn.inner_text()).strip()
                    print(f"  Button: '{txt}'")
            except Exception:
                pass

        # Wait longer for JS to load
        print("\nWaiting 8 seconds for JS/iframe to load...")
        await page.wait_for_timeout(8000)
        await page.screenshot(path="uploads/debug_atolls_3_waited.png", full_page=True)

        # List all frames
        print("\n=== All frames ===")
        for i, frame in enumerate(page.frames):
            print(f"  Frame {i}: {frame.url[:100]}")
            try:
                inputs = await frame.query_selector_all("input, textarea")
                print(f"    Inputs: {len(inputs)}")
                for inp in inputs[:5]:
                    try:
                        typ = await inp.get_attribute("type") or "text"
                        id_attr = await inp.get_attribute("id") or ""
                        name_attr = await inp.get_attribute("name") or ""
                        print(f"      [{typ}] id={id_attr!r} name={name_attr!r}")
                    except Exception:
                        pass
            except Exception as e:
                print(f"    Error: {e}")

        # Page source snippet
        src = await page.content()
        # Find iframe tags
        import re
        iframes = re.findall(r'<iframe[^>]*>', src, re.I)
        print("\n=== Iframes in source ===")
        for iframe in iframes:
            print(f"  {iframe[:200]}")

        # Check for Greenhouse widget script
        gh_scripts = re.findall(r'<script[^>]*greenhouse[^>]*>', src, re.I)
        print(f"\n=== Greenhouse scripts ===")
        for s in gh_scripts:
            print(f"  {s[:200]}")

        # Also look for the embed token
        if 'gh_jid' in src:
            idx = src.index('gh_jid')
            print(f"\ngh_jid context: ...{src[max(0,idx-100):idx+200]}...")

        # Check for div with form
        form_divs = re.findall(r'id=["\']application[^"\']*["\']', src)
        print(f"\nForm-like divs: {form_divs}")

        # Save full source
        Path("uploads/debug_atolls_source.html").write_text(src[:50000], encoding="utf-8", errors="replace")
        print("\nSaved source to uploads/debug_atolls_source.html")

        await browser.close()

asyncio.run(main())
