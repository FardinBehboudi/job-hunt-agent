"""Get verification code from Outlook web and return it."""
import asyncio, json, re, sys, io, os
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

EMAIL = "f_behboud@hotmail.com"
EMAIL_PASS = os.getenv("HOTMAIL_PASSWORD", "")

def ts(): return datetime.now().strftime("%H%M%S")

async def get_code_from_outlook():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = await browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
        )
        page = await ctx.new_page()

        print("Navigating to Outlook...")
        await page.goto("https://outlook.live.com/mail/0/inbox",
                       wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(3000)
        await page.screenshot(path=f"uploads/outlook_{ts()}.png")
        print(f"URL: {page.url[:80]}")

        # Check if we need to log in
        if "login" in page.url or "signup" in page.url or "account.microsoft" in page.url:
            print("Need to log in...")
            # Enter email
            for sel in ["input[type='email']", "#i0116", "input[name='loginfmt']"]:
                try:
                    el = await page.query_selector(sel)
                    if el and await el.is_visible():
                        await el.fill(EMAIL)
                        print(f"  Entered email via {sel}")
                        break
                except Exception: pass

            # Click Next
            for sel in ["input[type='submit']", "button:has-text('Next')", "#idSIButton9"]:
                try:
                    el = await page.query_selector(sel)
                    if el and await el.is_visible():
                        await el.click()
                        await page.wait_for_timeout(3000)
                        break
                except Exception: pass

            await page.screenshot(path=f"uploads/outlook_login_{ts()}.png")

            # Enter password
            for sel in ["input[type='password']", "#i0118"]:
                try:
                    el = await page.query_selector(sel)
                    if el and await el.is_visible():
                        await el.fill(EMAIL_PASS)
                        print("  Entered password")
                        break
                except Exception: pass

            # Click Sign in
            for sel in ["input[type='submit']", "button:has-text('Sign in')", "#idSIButton9"]:
                try:
                    el = await page.query_selector(sel)
                    if el and await el.is_visible():
                        await el.click()
                        await page.wait_for_timeout(5000)
                        break
                except Exception: pass

            # Stay signed in? -> No
            for sel in ["#idBtn_Back", "button:has-text('No')"]:
                try:
                    el = await page.query_selector(sel)
                    if el and await el.is_visible():
                        await el.click()
                        await page.wait_for_timeout(3000)
                        break
                except Exception: pass

        await page.wait_for_timeout(5000)
        await page.screenshot(path=f"uploads/outlook_inbox_{ts()}.png")
        print(f"After login URL: {page.url[:80]}")

        # Look for email from Greenhouse or about "verification"
        code = None
        for _ in range(12):  # Wait up to 2 minutes
            # Find emails in inbox
            for email_sel in [
                "[aria-label*='Atolls']",
                "[title*='verification']",
                "[title*='verify']",
                "[title*='Greenhouse']",
                "[title*='application']",
                "div[role='option']",
            ]:
                try:
                    emails = await page.query_selector_all(email_sel)
                    for em in emails[:5]:
                        txt = (await em.inner_text()).lower()
                        if any(k in txt for k in ["greenhouse", "atolls", "verif", "code", "application"]):
                            print(f"  Found relevant email: {txt[:60]}")
                            await em.click()
                            await page.wait_for_timeout(2000)
                            # Read email body
                            body_txt = await page.inner_text("body")
                            print(f"  Email body (500): {body_txt[:500]}")
                            # Extract code
                            for pat in [
                                r'\b([A-Z0-9]{8})\b',
                                r'code[:\s]+([A-Za-z0-9]{6,10})',
                                r'\b([0-9]{6,8})\b',
                                r'\b([a-z0-9]{8})\b',
                            ]:
                                matches = re.findall(pat, body_txt, re.I)
                                for m in matches:
                                    if len(m) in (6, 7, 8) and m not in ["UNSUB SCR", "CLICK HER"]:
                                        print(f"  Code candidate: {m}")
                                        code = m
                                        break
                                if code: break
                            break
                except Exception as e:
                    pass
            if code: break
            print("  No code found yet, waiting 10s...")
            await page.wait_for_timeout(10000)
            # Refresh inbox
            try:
                refresh = await page.query_selector("[aria-label='Refresh']")
                if refresh: await refresh.click()
                await page.wait_for_timeout(2000)
            except Exception: pass

        await browser.close()
        return code


if __name__ == "__main__":
    code = asyncio.run(get_code_from_outlook())
    print(f"\nVerification code: {code}")
