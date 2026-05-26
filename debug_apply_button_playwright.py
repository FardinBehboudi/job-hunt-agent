"""
LinkedIn Apply Button Debugging Script (Playwright version)
Finds the external Apply button on LinkedIn job pages
"""

import asyncio
from playwright.async_api import async_playwright
import re

JOB_URL = "https://www.linkedin.com/jobs/view/4324889941/"

async def debug_apply_button():
    """Debug Apply button detection using Playwright"""

    async with async_playwright() as p:
        # Use existing Chrome profile (logged in)
        browser = await p.chromium.launch_persistent_context(
            user_data_dir=r"C:\Users\f_beh\AppData\Local\Google\Chrome\User Data",
            headless=False
        )

        page = await browser.new_page()

        print(f"Loading: {JOB_URL}")
        await page.goto(JOB_URL)
        await page.wait_for_load_state("networkidle")
        print("✅ Page loaded\n")

        # Analyze page structure
        print("="*80)
        print("PAGE STRUCTURE ANALYSIS")
        print("="*80 + "\n")

        title = await page.title()
        print(f"Page Title: {title}\n")

        # Test selectors for Easy Apply
        print("Testing Easy Apply button selectors:")
        print("-" * 40)

        easy_apply_selectors = [
            'button:has-text("Easy Apply")',
            'button[aria-label*="Easy Apply"]',
            'button.jobs-apply-button--top-card',
            'text="Easy Apply"',
        ]

        for sel in easy_apply_selectors:
            try:
                element = page.locator(sel)
                count = await element.count()
                if count > 0:
                    visible = await element.first.is_visible()
                    text = await element.first.text_content()
                    print(f"✅ FOUND: {sel}")
                    print(f"   Count: {count}, Visible: {visible}, Text: {text}\n")
            except Exception as e:
                print(f"❌ NOT FOUND: {sel}\n")

        # Test selectors for External Apply
        print("\nTesting External Apply button selectors:")
        print("-" * 40)

        external_apply_selectors = [
            'button:has-text("Apply") >> text=/^Apply$/',
            'button[aria-label*="Apply"]',
            'button.jobs-apply-button',
            'button >> text="Apply"',
            'xpath=//button[contains(text(), "Apply") and not(contains(text(), "Easy"))]',
            'xpath=//button[contains(@aria-label, "Apply")]',
        ]

        for sel in external_apply_selectors:
            try:
                element = page.locator(sel)
                count = await element.count()
                if count > 0:
                    visible = await element.first.is_visible()
                    text = await element.first.text_content()
                    print(f"✅ FOUND: {sel}")
                    print(f"   Count: {count}, Visible: {visible}, Text: {text}\n")
            except Exception as e:
                print(f"❌ NOT FOUND: {sel}\n")

        # Find ALL buttons on page
        print("\nALL BUTTONS ON PAGE:")
        print("-" * 40)

        buttons = await page.query_selector_all("button")
        print(f"Found {len(buttons)} buttons total\n")

        for i, button in enumerate(buttons):
            try:
                text = await button.text_content()
                visible = await button.is_visible()
                aria_label = await button.get_attribute("aria-label")
                classes = await button.get_attribute("class")

                if text and text.strip():  # Only show buttons with text
                    print(f"Button {i}:")
                    print(f"  Text: {text.strip()}")
                    print(f"  Visible: {visible}")
                    print(f"  aria-label: {aria_label}")
                    print(f"  Classes: {classes}")
                    print()
            except:
                pass

        # Summary
        print("\n" + "="*80)
        print("NEXT STEPS")
        print("="*80)
        print("""
When you identify which selector works, update linkedin_clicker.py:

For External Apply button, look for the selector that finds:
- A button with text "Apply" (not "Easy Apply")
- That's visible and clickable
- In the top card section

Then update the is_external_apply_button() and click functions accordingly.

Browser will close in 10 seconds...
        """)

        await asyncio.sleep(10)
        await browser.close()

if __name__ == "__main__":
    asyncio.run(debug_apply_button())
