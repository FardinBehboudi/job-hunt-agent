"""
Log into Outlook via Playwright, find the Greenhouse verification email,
print ONLY the code (8 chars) to stdout.
"""
import asyncio, json, os, re, sys, io
from pathlib import Path
from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()
EMAIL = "f_behboud@hotmail.com"
PASS  = os.getenv("HOTMAIL_PASSWORD", "")

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        ctx  = await browser.new_context(viewport={"width":1280,"height":800})
        page = await ctx.new_page()

        await page.goto("https://login.live.com/login.srf?wa=wsignin1.0&wreply=https://outlook.live.com/owa/",
                        wait_until="domcontentloaded", timeout=20_000)
        await page.wait_for_timeout(2000)

        # Enter email
        for sel in ["input[type='email']","#i0116","input[name='loginfmt']"]:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    await el.fill(EMAIL); break
            except Exception: pass
        for sel in ["input[type='submit']","#idSIButton9","button:has-text('Next')"]:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    await el.click(); await page.wait_for_timeout(3000); break
            except Exception: pass

        # "Sign in another way" -> Use password
        for sel in ["button:has-text('Use your password')"]:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    await el.click(); await page.wait_for_timeout(2000); break
            except Exception: pass

        # Enter password
        for sel in ["input[type='password']","#i0118"]:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    await el.fill(PASS); break
            except Exception: pass
        for sel in ["input[type='submit']","#idSIButton9","button:has-text('Sign in')"]:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    await el.click(); await page.wait_for_timeout(5000); break
            except Exception: pass

        # "Stay signed in?" -> No
        for sel in ["#idBtn_Back","button:has-text('No')"]:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    await el.click(); await page.wait_for_timeout(3000); break
            except Exception: pass

        await page.wait_for_timeout(5000)
        print(f"[outlook] at: {page.url[:60]}", file=sys.stderr)

        # Check if we're in the inbox
        if "outlook.live.com" not in page.url and "mail" not in page.url:
            # Try navigating directly
            await page.goto("https://outlook.live.com/mail/0/inbox",
                           wait_until="domcontentloaded", timeout=20_000)
            await page.wait_for_timeout(5000)

        print(f"[outlook] inbox: {page.url[:60]}", file=sys.stderr)

        # Wait up to 2 minutes for the Greenhouse email
        code = None
        for attempt in range(12):
            body = await page.inner_text("body")
            # Look for Atolls / Greenhouse email
            if any(k in body.lower() for k in ["atolls", "greenhouse", "verif", "application code"]):
                print(f"[attempt {attempt}] Found relevant content, searching for code...", file=sys.stderr)
                # Click on the email if we can find it in the list
                for em_sel in [
                    "div[role='option']:has-text('Atolls')",
                    "div:has-text('Atolls') div[role='option']",
                    "[data-convid]",
                    "div[role='listitem']",
                ]:
                    try:
                        ems = await page.query_selector_all(em_sel)
                        for em in ems[:5]:
                            txt = await em.inner_text()
                            if any(k in txt.lower() for k in ["atolls","greenhouse","verif","code","application"]):
                                await em.click()
                                await page.wait_for_timeout(2000)
                                email_body = await page.inner_text("body")
                                # Extract verification code
                                for pat in [
                                    r'(?:verification|confirm|code)[:\s]+([A-Za-z0-9]{6,10})',
                                    r'\b([A-Z0-9]{8})\b',
                                    r'\b([0-9]{6,8})\b',
                                ]:
                                    for m in re.findall(pat, email_body, re.I):
                                        if len(m) >= 6 and m.upper() not in ["UNSUBSCR","CLICK HER","PRIVACYPO"]:
                                            code = m
                                            print(f"[code] {code}", file=sys.stderr)
                                            print(code)  # This is the only stdout output
                                            break
                                    if code: break
                                break
                        if code: break
                    except Exception: pass
                if code: break

            print(f"[attempt {attempt+1}] No code yet, refreshing...", file=sys.stderr)
            # Try to refresh
            try:
                ref = await page.query_selector("[aria-label*='Refresh']")
                if ref: await ref.click()
            except Exception: pass
            await page.wait_for_timeout(10000)

        if not code:
            print("NO_CODE", file=sys.stderr)
        await browser.close()

asyncio.run(main())
