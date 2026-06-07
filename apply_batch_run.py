"""
Autonomous batch apply for a fixed list of LinkedIn job URLs.
Runs headless=False so we can watch, saves screenshots on every result.
"""
import asyncio
import json
import logging
import sys
import io
from pathlib import Path
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv()

from playwright.async_api import async_playwright
from core.config import load_config
from applier.applier import _apply_linkedin, _profile
from applier.linkedin_applier import init_answer_cache, _resume_path, _detect_job_language
from applier.events import _emit

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("batch_run")

JOBS = [
    {"url": "https://www.linkedin.com/jobs/view/4413863832", "title": "Job 1", "company": "", "description": "", "scraped_id": "4413863832"},
    {"url": "https://www.linkedin.com/jobs/view/4253392898", "title": "Job 2", "company": "", "description": "", "scraped_id": "4253392898"},
    {"url": "https://www.linkedin.com/jobs/view/4386512137", "title": "Job 3", "company": "", "description": "", "scraped_id": "4386512137"},
    {"url": "https://www.linkedin.com/jobs/view/4368671939", "title": "Job 4", "company": "", "description": "", "scraped_id": "4368671939"},
    {"url": "https://www.linkedin.com/jobs/view/4390837713", "title": "Job 5", "company": "", "description": "", "scraped_id": "4390837713"},
]

SESSION_FILE = Path("uploads/linkedin_session.json")
RESULTS_FILE = Path("uploads/batch_run_results.json")


async def scrape_job_details(page, job: dict) -> dict:
    """Navigate to the LinkedIn job page and extract title, company, description."""
    try:
        await page.goto(job["url"], wait_until="domcontentloaded", timeout=25_000)
        await page.wait_for_timeout(3000)

        # Title
        for sel in [
            "h1.t-24", "h1.job-title", "h1[class*='title']",
            ".job-details-jobs-unified-top-card__job-title h1",
            ".jobs-unified-top-card__job-title",
            "h1",
        ]:
            try:
                el = page.locator(sel)
                if await el.count():
                    txt = (await el.first.text_content() or "").strip()
                    if txt:
                        job["title"] = txt
                        break
            except Exception:
                pass

        # Company
        for sel in [
            ".job-details-jobs-unified-top-card__company-name a",
            ".jobs-unified-top-card__company-name a",
            "[data-test-job-card-company-name]",
            ".topcard__org-name-link",
        ]:
            try:
                el = page.locator(sel)
                if await el.count():
                    txt = (await el.first.text_content() or "").strip()
                    if txt:
                        job["company"] = txt
                        break
            except Exception:
                pass

        # Description
        for sel in [
            ".jobs-description__content",
            ".jobs-description-content__text",
            "#job-details",
            ".show-more-less-html__markup",
        ]:
            try:
                el = page.locator(sel)
                if await el.count():
                    txt = (await el.first.text_content() or "").strip()
                    if txt:
                        job["description"] = txt[:3000]
                        break
            except Exception:
                pass

        log.info("Job details: %s @ %s", job.get("title", "?"), job.get("company", "?"))
    except Exception as e:
        log.warning("scrape_job_details failed for %s: %s", job["url"], e)
    return job


async def main():
    cfg = load_config()
    cfg["headless"] = False
    profile = _profile(cfg)
    init_answer_cache(cfg)

    results = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled", "--start-maximized"],
        )
        ctx_kwargs = dict(
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
            locale="en-US",
            timezone_id="Europe/Berlin",
        )
        if SESSION_FILE.exists():
            ctx_kwargs["storage_state"] = str(SESSION_FILE)

        context = await browser.new_context(**ctx_kwargs)
        await context.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        )

        for i, job in enumerate(JOBS, 1):
            log.info("\n" + "="*60)
            log.info("JOB %d/%d — %s", i, len(JOBS), job["url"])
            log.info("="*60)

            page = await context.new_page()
            try:
                # 1. Scrape job details first
                job = await scrape_job_details(page, job)

                # 2. Detect resume language and load resume text
                lang = _detect_job_language(job)
                job["_resume_lang"] = lang
                resume_path = _resume_path(cfg, lang)
                resume_text = ""
                if resume_path:
                    try:
                        import pdfplumber
                        with pdfplumber.open(resume_path) as pdf:
                            resume_text = "\n".join(p.extract_text() or "" for p in pdf.pages)
                    except Exception as e:
                        log.warning("Could not read resume: %s", e)

                # 3. Apply
                result = await _apply_linkedin(page, job, cfg, resume_text, profile)
                result["job_id"] = job["scraped_id"]
                result["title"]  = job.get("title", "?")
                result["company"] = job.get("company", "?")
                result["url"]    = job["url"]

                status = "✅ SUCCESS" if result.get("success") else ("👤 MANUAL" if result.get("manual") else "❌ FAILED")
                log.info("Result: %s — %s", status, result.get("note", ""))
                results.append(result)

                # Screenshot
                try:
                    ss = f"uploads/batch_{job['scraped_id']}_{datetime.now().strftime('%H%M%S')}.png"
                    await page.screenshot(path=ss, full_page=False)
                    log.info("Screenshot: %s", ss)
                except Exception:
                    pass

                # Save session after each job
                try:
                    await context.storage_state(path=str(SESSION_FILE))
                except Exception:
                    pass

            except Exception as e:
                log.error("Unexpected error on job %s: %s", job["url"], e, exc_info=True)
                results.append({
                    "job_id": job["scraped_id"],
                    "url": job["url"],
                    "title": job.get("title", "?"),
                    "success": False,
                    "manual": True,
                    "note": str(e),
                })
            finally:
                try:
                    await page.close()
                except Exception:
                    pass

            # Delay between jobs
            await asyncio.sleep(3)

        await browser.close()

    # Save results
    RESULTS_FILE.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Print summary
    print("\n" + "="*60)
    print("BATCH APPLY SUMMARY")
    print("="*60)
    for r in results:
        status = "✅" if r.get("success") else ("👤" if r.get("manual") else "❌")
        print(f"{status} {r.get('title','?')} @ {r.get('company','?')} — {r.get('note','')[:60]}")
    success = sum(1 for r in results if r.get("success"))
    manual  = sum(1 for r in results if r.get("manual") and not r.get("success"))
    failed  = len(results) - success - manual
    print(f"\nTotal: {success} submitted, {manual} manual, {failed} failed")
    print(f"Full results: {RESULTS_FILE}")


asyncio.run(main())
