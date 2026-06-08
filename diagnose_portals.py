"""Screenshot each external portal to see what we're dealing with."""
import asyncio, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from playwright.async_api import async_playwright
from pathlib import Path

PORTALS = [
    ("csod",       "https://dkv-mobility.csod.com/ux/ats/careersite/10/requisition/4115/application?lang=en-US&cultureId=1&source=Linkedin"),
    ("nttdata",    "https://de.nttdata.com/jobs/21494--snbu21494ts--servicenow-senior-solution-architect-wmx"),
    ("glasfaser",  "https://www.deutsche-glasfaser.de/unternehmen/karriere/jobportal/744000114991918"),
    ("deloitte",   "https://job.deloitte.com/job-senior-servicenow-architekt-mwd-_48967"),
    ("rossmann",   "https://jobs.rossmann.de/stellenanzeige/solution-architect-m-w-d-system-analytiker-servicenow-fuer-rsm-oder-csm-am-standort-burgwedel-6-159949.html"),
]

async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        ctx = await browser.new_context(viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36")
        for name, url in PORTALS:
            page = await ctx.new_page()
            print(f"\n{'='*50}")
            print(f"Visiting: {name}")
            print(f"URL: {url[:80]}")
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
                await page.wait_for_timeout(5000)  # let JS load

                # Dismiss cookie popups
                for btn_text in ["Accept", "Accept all", "Alle akzeptieren", "Alle Cookies akzeptieren",
                                  "Zustimmen", "OK", "Accept cookies", "Got it"]:
                    try:
                        btn = page.get_by_role("button", name=btn_text, exact=False)
                        if await btn.count() and await btn.first.is_visible():
                            await btn.first.click()
                            await page.wait_for_timeout(1000)
                            print(f"  Dismissed: {btn_text}")
                            break
                    except Exception:
                        pass

                await page.wait_for_timeout(2000)
                ss = f"uploads/diag_{name}.png"
                await page.screenshot(path=ss, full_page=False)
                print(f"  Screenshot: {ss}")
                print(f"  Final URL: {page.url[:100]}")

                # Print visible text (first 400 chars)
                try:
                    txt = (await page.inner_text("body")).strip()[:400]
                    print(f"  Page text: {txt}")
                except Exception:
                    pass
            except Exception as e:
                print(f"  ERROR: {e}")
            await page.close()
        await browser.close()

asyncio.run(main())
