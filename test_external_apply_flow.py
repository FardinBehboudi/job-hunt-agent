#!/usr/bin/env python3
"""
Test External Apply Flow End-to-End
Tests the complete workflow:
1. Navigate to LinkedIn job page
2. Detect and click external Apply button
3. Follow redirect to external ATS
4. Detect platform and fill form
5. Submit application
"""

import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from playwright.async_api import async_playwright, Page

from applier.applier import _apply_linkedin, _profile
from applier.linkedin_clicker import click_apply_button, dismiss_overlays
from applier.external_applier import follow_external_apply
from core.config import load_config

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ── Test Job URLs ──────────────────────────────────────────────────────────
# These URLs have EXTERNAL Apply buttons (not LinkedIn Easy Apply)
TEST_JOBS = [
    {
        "title": "Backend Engineer",
        "company": "Example Company",
        "url": "https://www.linkedin.com/jobs/view/4324889941/",  # Known external apply
        "description": "Senior backend engineer role",
    }
]


async def test_external_apply_flow(cfg: dict) -> None:
    """Test the complete external apply workflow."""

    log.info("=" * 80)
    log.info("EXTERNAL APPLY FLOW TEST")
    log.info("=" * 80)

    # Get config and profile
    profile = _profile(cfg)
    resume_path = Path(cfg.get("paths", {}).get("resume_en", ""))
    resume_text = ""

    if resume_path.exists():
        try:
            import pdfplumber
            with pdfplumber.open(resume_path) as pdf:
                resume_text = "\n".join(p.extract_text() or "" for p in pdf.pages)
        except Exception as e:
            log.warning("Could not read resume: %s", e)

    log.info("Profile: %s %s", profile.get("first_name"), profile.get("last_name"))
    log.info("Resume: %s", resume_path.name if resume_path else "Not found")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ]
        )

        try:
            # Create context with user agent spoofing
            context = await browser.new_context(
                viewport={"width": 1366, "height": 768},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="en-US",
                timezone_id="Europe/Berlin",
            )

            # Load LinkedIn session
            session_file = Path(__file__).resolve().parent / "uploads" / "linkedin_session.json"
            if session_file.exists():
                try:
                    import json
                    session_data = json.loads(session_file.read_text())
                    cookies = session_data if isinstance(session_data, list) \
                              else session_data.get("cookies", [])
                    if cookies:
                        await context.add_cookies(cookies)
                        log.info("✅ LinkedIn session loaded (%d cookies)", len(cookies))
                except Exception as e:
                    log.warning("Could not load session: %s", e)

            page = await context.new_page()

            # Test each job
            for job in TEST_JOBS:
                log.info("\n" + "=" * 80)
                log.info("Testing: %s @ %s", job["title"], job["company"])
                log.info("URL: %s", job["url"])
                log.info("=" * 80 + "\n")

                try:
                    # Navigate to job page
                    log.info("📍 Navigating to job page...")
                    await page.goto(job["url"], wait_until="domcontentloaded", timeout=30_000)

                    # Check if logged in
                    if "linkedin.com/login" in page.url or "linkedin.com/authwall" in page.url:
                        log.error("❌ Not logged in — please authenticate first")
                        continue

                    # Dismiss overlays
                    log.info("🔄 Dismissing overlays...")
                    n_dismissed = await dismiss_overlays(page)
                    if n_dismissed:
                        await page.wait_for_timeout(500)
                        log.info("  Dismissed %d overlay(s)", n_dismissed)

                    # Wait for page to fully load
                    await page.wait_for_timeout(3000)
                    await page.evaluate("window.scrollTo(0, 0)")

                    # Click Apply button
                    log.info("🔘 Clicking Apply button...")
                    click_result = await click_apply_button(page)
                    log.info("  Result: %s", click_result)

                    if click_result == "not_found":
                        log.error("❌ No Apply button found")
                        continue

                    if click_result == "already":
                        log.warning("⏭️ Already applied to this job")
                        continue

                    if click_result == "modal_failed":
                        log.error("❌ Modal never opened")
                        continue

                    if click_result == "easy_apply":
                        log.info("✅ LinkedIn Easy Apply — filling modal...")
                        # Easy Apply handling would happen here
                        log.info("  (Easy Apply not tested in this script)")
                        continue

                    if click_result == "external":
                        log.info("✅ EXTERNAL APPLY DETECTED!")
                        log.info("🌐 Following external redirect...")

                        # This is the key function that handles external apply
                        result = await follow_external_apply(
                            page, job, profile, resume_text, cfg
                        )

                        log.info("\n" + "=" * 80)
                        log.info("RESULT")
                        log.info("=" * 80)
                        log.info("Success: %s", result.get("success"))
                        log.info("Apply Type: %s", result.get("apply_type"))
                        log.info("Manual: %s", result.get("manual"))
                        log.info("Note: %s", result.get("note"))
                        log.info("=" * 80 + "\n")

                        if result.get("success"):
                            log.info("✅ APPLICATION SUBMITTED SUCCESSFULLY!")
                        else:
                            log.warning("⚠️ Application requires manual review: %s", result.get("note"))

                except Exception as e:
                    log.error("❌ Error during test: %s", e, exc_info=True)

        finally:
            await browser.close()


async def main():
    """Main entry point."""
    cfg = load_config()
    cfg["headless"] = False  # Run in visible mode for testing

    log.info("\n")
    log.info("╔" + "=" * 78 + "╗")
    log.info("║" + " " * 20 + "EXTERNAL APPLY FLOW TEST" + " " * 35 + "║")
    log.info("║" + " " * 78 + "║")
    log.info("║ This test verifies the complete external apply workflow:" + " " * 23 + "║")
    log.info("║ 1. Detect external Apply button on LinkedIn job page" + " " * 24 + "║")
    log.info("║ 2. Click it and follow redirect to external ATS" + " " * 30 + "║")
    log.info("║ 3. Detect platform (Greenhouse, Lever, Ashby, etc)" + " " * 26 + "║")
    log.info("║ 4. Fill form fields intelligently using AI" + " " * 34 + "║")
    log.info("║ 5. Submit application" + " " * 55 + "║")
    log.info("╚" + "=" * 78 + "╝\n")

    await test_external_apply_flow(cfg)


if __name__ == "__main__":
    asyncio.run(main())
