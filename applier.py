"""
applier.py — auto-apply to LinkedIn Easy Apply and Stepstone jobs via Playwright.

Bot-detection mitigations: random delays (1-4 s), human-like typing, max 10 jobs/session.
CAPTCHA → log as "manual apply needed", skip, continue.
Pre-submit: dedup.pre_log() is called immediately before the submit click so we never
lose track of a job if the process crashes between submit and the success callback.
"""

import asyncio
import logging
import random
from pathlib import Path

from dotenv import load_dotenv
from playwright.async_api import async_playwright, Page, TimeoutError as PWTimeout

import dedup as _dedup
from config import load_config

load_dotenv()
log = logging.getLogger(__name__)

_MAX_PER_SESSION = 10
_DELAY_MIN = 1.0
_DELAY_MAX = 4.0


async def _delay():
    await asyncio.sleep(random.uniform(_DELAY_MIN, _DELAY_MAX))


async def _type_slowly(page: Page, selector: str, text: str) -> None:
    await page.click(selector)
    for char in text:
        await page.keyboard.type(char)
        await asyncio.sleep(random.uniform(0.04, 0.12))


async def _fill_if_empty(page: Page, selector: str, value: str) -> None:
    if not value:
        return
    locator = page.locator(selector)
    if await locator.count():
        existing = await locator.first.input_value()
        if not existing.strip():
            await _type_slowly(page, selector, value)
            await _delay()


async def _captcha_present(page: Page) -> bool:
    indicators = ["captcha", "recaptcha", "hcaptcha", "challenge"]
    content = (await page.content()).lower()
    return any(ind in content for ind in indicators)


# ── LinkedIn Easy Apply ───────────────────────────────────────────────────────

async def _apply_linkedin(page: Page, job: dict, archive: Path,
                          cfg: dict) -> bool:
    try:
        await page.goto(job["url"], timeout=30_000)
        await _delay()

        if await _captcha_present(page):
            log.warning("CAPTCHA on LinkedIn: %s @ %s — manual apply needed",
                        job["title"], job["company"])
            return False

        easy_apply = page.locator("button:has-text('Easy Apply'), .jobs-apply-button")
        if not await easy_apply.count():
            log.info("No Easy Apply button: %s @ %s — skipping",
                     job["title"], job["company"])
            return False

        await easy_apply.first.click()
        await _delay()

        # Fill contact fields
        contact = cfg.get("contact", {})
        await _fill_if_empty(
            page,
            "input[name='name'], input[aria-label*='name'], input[id*='name']",
            contact.get("name", ""),
        )
        await _fill_if_empty(
            page,
            "input[type='email'], input[name='email'], input[aria-label*='email']",
            contact.get("email", ""),
        )
        await _fill_if_empty(
            page,
            "input[name='phoneNumber'], input[aria-label*='phone'], input[id*='phone']",
            contact.get("phone", ""),
        )

        # Upload resume (prefer PDF from archive, fall back to docx)
        resume_pdf = archive / "resume_en.pdf"
        if not resume_pdf.exists():
            resume_pdf = archive / "resume_en.docx"
        if resume_pdf.exists():
            upload_inputs = page.locator("input[type='file']")
            if await upload_inputs.count():
                await upload_inputs.first.set_input_files(str(resume_pdf))
                await _delay()

        # Upload cover letter if a second file input is present
        cl_pdf = archive / "cover_letter.pdf"
        if not cl_pdf.exists():
            cl_pdf = archive / "cover_letter.docx"
        if cl_pdf.exists():
            upload_inputs = page.locator("input[type='file']")
            if await upload_inputs.count() > 1:
                await upload_inputs.nth(1).set_input_files(str(cl_pdf))
                await _delay()

        # Paginate through multi-step form
        for _ in range(10):
            # Check for references upload field
            await _maybe_attach_references(page, cfg)

            next_btn   = page.locator("button:has-text('Next'), button:has-text('Continue'), button:has-text('Review')")
            submit_btn = page.locator("button:has-text('Submit application'), button:has-text('Submit')")

            if await submit_btn.count():
                # ── Pre-submit crash guard ──────────────────────────────────
                _dedup.pre_log(job, archive, cfg)
                await submit_btn.first.click()
                await _delay()
                log.info("Submitted LinkedIn Easy Apply: %s @ %s",
                         job["title"], job["company"])
                return True
            elif await next_btn.count():
                await next_btn.first.click()
                await _delay()
            else:
                break

        log.warning("Could not complete LinkedIn form: %s @ %s",
                    job["title"], job["company"])
        return False

    except PWTimeout:
        log.warning("Timeout on LinkedIn: %s @ %s", job["title"], job["company"])
        return False


async def _maybe_attach_references(page: Page, cfg: dict) -> None:
    """Attach reference PDFs if a 'references' file-upload field appears."""
    support_dir: Path = cfg["paths"]["support_folder"]
    if not support_dir.exists():
        return

    ref_pdfs = [
        p for p in support_dir.iterdir()
        if p.suffix.lower() == ".pdf" and "reference" in p.name.lower()
    ]
    if not ref_pdfs:
        return

    # Look for a file input labelled "reference" or "additional documents"
    ref_labels = ["reference", "additional", "document", "anlage"]
    for label in ref_labels:
        locator = page.locator(
            f"input[type='file'][aria-label*='{label}' i], "
            f"label:has-text('{label}') + input[type='file']"
        )
        if await locator.count():
            await locator.first.set_input_files([str(p) for p in ref_pdfs])
            await _delay()
            log.info("Attached %d reference PDF(s)", len(ref_pdfs))
            break


# ── Stepstone ─────────────────────────────────────────────────────────────────

async def _apply_stepstone(page: Page, job: dict, archive: Path,
                           cfg: dict) -> bool:
    try:
        await page.goto(job["url"], timeout=30_000)
        await _delay()

        if await _captcha_present(page):
            log.warning("CAPTCHA on Stepstone: %s @ %s — manual apply needed",
                        job["title"], job["company"])
            return False

        apply_btn = page.locator(
            "a:has-text('Jetzt bewerben'), a:has-text('Apply now'), "
            "button:has-text('Jetzt bewerben'), button:has-text('Apply now')"
        )
        if not await apply_btn.count():
            log.info("No apply button on Stepstone: %s @ %s",
                     job["title"], job["company"])
            return False

        await apply_btn.first.click()
        await _delay()

        if await _captcha_present(page):
            return False

        # Choose German or English resume based on JD language
        jd_text = job.get("description", "").lower()
        de_words = sum(1 for w in ["der", "die", "das", "und", "für", "mit"] if w in jd_text)
        use_german = de_words > 3
        resume_key = "resume_de.pdf" if use_german else "resume_en.pdf"
        resume_path = archive / resume_key
        # Fallback chain: other language → .docx
        if not resume_path.exists():
            resume_path = archive / ("resume_en.pdf" if use_german else "resume_de.pdf")
        if not resume_path.exists():
            resume_path = archive / resume_key.replace(".pdf", ".docx")

        if resume_path.exists():
            upload = page.locator("input[type='file']")
            if await upload.count():
                await upload.first.set_input_files(str(resume_path))
                await _delay()

        submit = page.locator(
            "button:has-text('Jetzt bewerben'), button:has-text('Submit'), "
            "button:has-text('Senden'), button:has-text('Bewerbung absenden')"
        )
        if await submit.count():
            # ── Pre-submit crash guard ──────────────────────────────────────
            _dedup.pre_log(job, archive, cfg)
            await submit.first.click()
            await _delay()
            log.info("Submitted Stepstone: %s @ %s", job["title"], job["company"])
            return True

        log.warning("Could not complete Stepstone form: %s @ %s",
                    job["title"], job["company"])
        return False

    except PWTimeout:
        log.warning("Timeout on Stepstone: %s @ %s", job["title"], job["company"])
        return False


# ── Public API ────────────────────────────────────────────────────────────────

async def _apply_one(page: Page, job: dict, archive: Path, cfg: dict) -> bool:
    source = job.get("source", "").lower()
    if source == "linkedin":
        return await _apply_linkedin(page, job, archive, cfg)
    if source == "stepstone":
        return await _apply_stepstone(page, job, archive, cfg)
    log.warning("Unknown source '%s' for %s @ %s — skipping",
                source, job["title"], job["company"])
    return False


_SESSION_FILE = Path(__file__).resolve().parent / "uploads" / "linkedin_session.json"


async def run_async(jobs_with_archives: list[tuple[dict, Path]],
                    cfg: dict | None = None) -> list[tuple[dict, bool]]:
    if cfg is None:
        cfg = load_config()

    log.info("Checking for LinkedIn session at: %s", str(_SESSION_FILE.resolve()))
    if not _SESSION_FILE.exists():
        log.error(
            "No saved LinkedIn session found — go to the web UI and click "
            "'Connect LinkedIn' to authenticate before applying"
        )
        return []

    headless: bool = cfg.get("headless", True)
    results: list[tuple[dict, bool]] = []
    applied_count = 0

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        try:
            context = await browser.new_context(
                storage_state=str(_SESSION_FILE),
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
            )
            log.info("Loaded saved LinkedIn session — skipping login")
        except Exception as exc:
            log.error("Failed to load LinkedIn session (%s) — re-authenticate via web UI", exc)
            await browser.close()
            return []

        page = await context.new_page()

        # Quick session validity check — LinkedIn redirects to login if session expired
        await page.goto("https://www.linkedin.com/feed/", timeout=20_000)
        if "login" in page.url or "authwall" in page.url:
            log.error(
                "LinkedIn session expired — go to the web UI and click "
                "'Connect LinkedIn' to re-authenticate"
            )
            await browser.close()
            return []
        log.info("LinkedIn session valid — proceeding with applications")

        for job, archive in jobs_with_archives:
            if applied_count >= _MAX_PER_SESSION:
                log.info("Reached max per session (%d) — stopping", _MAX_PER_SESSION)
                break

            success = await _apply_one(page, job, archive, cfg)
            results.append((job, success))
            if success:
                applied_count += 1
            await _delay()

        await browser.close()

    return results


def apply(job: dict, archive_path: Path, cfg: dict | None = None) -> bool:
    """Synchronous single-job wrapper used by main.py."""
    results = asyncio.run(run_async([(job, archive_path)], cfg))
    return results[0][1] if results else False


if __name__ == "__main__":
    from config import setup_logging
    cfg = load_config()
    setup_logging(cfg)
    cfg["headless"] = False  # watch the browser while testing
    sample = {
        "title": "Data Engineer",
        "company": "TestCorp GmbH",
        "location": "Berlin",
        "url": "https://www.linkedin.com/jobs/view/1234567890",
        "source": "LinkedIn",
    }
    ok = apply(sample, Path(r"C:\Users\f_beh\Dropbox\CV\application_history\test"), cfg)
    print("Applied:", ok)
