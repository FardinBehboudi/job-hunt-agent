"""
apply_agent.py - Job application automation using Claude in Chrome.

This module handles:
- Navigating to job pages
- Detecting apply button type (Easy Apply vs External)
- Filling forms intelligently using Claude AI
- Handling external redirects
- Verifying application submission
"""

import asyncio
import base64
import json
import logging
import os
import re
from pathlib import Path
from datetime import datetime
from typing import Optional

import anthropic

log = logging.getLogger(__name__)


class ChromeJobApplier:
    """Apply to jobs using Claude in Chrome."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self.client = anthropic.Anthropic(api_key=self.api_key)
        self.model = "claude-opus-4-6"

    async def apply_to_job(
        self,
        job_url: str,
        job_title: str,
        company_name: str,
        application_profile: dict,
        resume_path: str,
        job_description: str = ""
    ) -> dict:
        """Apply to a single job using Claude in Chrome."""
        log.info(f"Starting application: {job_title} @ {company_name}")

        try:
            # Step 1: Take screenshot
            log.info("Step 1: Navigating to job page...")
            initial_screenshot = await self._get_page_screenshot(job_url)
            if not initial_screenshot:
                return {
                    "success": False,
                    "apply_type": "Manual Required",
                    "note": "Could not load job page",
                    "timestamp": datetime.utcnow().isoformat()
                }

            # Step 2: Detect apply button
            log.info("Step 2: Detecting apply button type...")
            button_info = await self._detect_apply_button(initial_screenshot)
            if button_info["type"] == "not_found":
                return {
                    "success": False,
                    "apply_type": "Manual Required",
                    "note": "No apply button found",
                    "timestamp": datetime.utcnow().isoformat()
                }

            apply_type = button_info["type"]

            # Step 3: Click apply button
            log.info(f"Step 3: Clicking {apply_type} button...")
            if not await self._click_apply_button(button_info):
                return {
                    "success": False,
                    "apply_type": "Manual Required",
                    "note": "Could not click apply button",
                    "timestamp": datetime.utcnow().isoformat()
                }

            # Step 4-5: Wait and fill form
            log.info("Step 4: Waiting for form...")
            await asyncio.sleep(2)
            form_screenshot = await self._get_page_screenshot()

            if apply_type == "easy_apply":
                log.info("Step 5: Filling LinkedIn Easy Apply...")
                fill_result = await self._fill_easy_apply_form(form_screenshot, application_profile, resume_path, job_description)
            else:
                log.info("Step 5: Filling external form...")
                fill_result = await self._fill_external_form(form_screenshot, application_profile, resume_path, job_description)

            if not fill_result.get("ready_to_submit"):
                return {
                    "success": False,
                    "apply_type": "Manual Required",
                    "note": f"Form filling incomplete: {fill_result.get('note')}",
                    "timestamp": datetime.utcnow().isoformat()
                }

            # Step 6: Submit
            log.info("Step 6: Submitting...")
            await self._submit_form()

            # Step 7: Verify
            log.info("Step 7: Verifying submission...")
            await asyncio.sleep(2)
            verification = await self._verify_submission()

            if verification.get("submitted"):
                return {
                    "success": True,
                    "apply_type": "Easy Apply" if apply_type == "easy_apply" else "External",
                    "note": "Successfully submitted",
                    "timestamp": datetime.utcnow().isoformat()
                }
            else:
                return {
                    "success": False,
                    "apply_type": "Manual Required",
                    "note": "Submission could not be verified",
                    "timestamp": datetime.utcnow().isoformat()
                }

        except Exception as e:
            log.error(f"Error: {e}", exc_info=True)
            return {
                "success": False,
                "apply_type": "Manual Required",
                "note": f"Error: {str(e)}",
                "timestamp": datetime.utcnow().isoformat()
            }

    async def _get_page_screenshot(self, url: Optional[str] = None) -> Optional[bytes]:
        """Get screenshot - TODO: Implement with Claude in Chrome"""
        pass

    async def _detect_apply_button(self, screenshot: bytes) -> dict:
        """Detect apply button using Claude vision"""
        screenshot_b64 = base64.b64encode(screenshot).decode()
        response = self.client.messages.create(
            model=self.model,
            max_tokens=500,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": screenshot_b64}},
                    {"type": "text", "text": "Identify if this is an Easy Apply or External apply button. Return JSON: {\"type\": \"easy_apply\"|\"external\"|\"not_found\"}"}
                ]
            }]
        )
        try:
            text = response.content[0].text
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                return json.loads(match.group())
        except:
            pass
        return {"type": "not_found"}

    async def _click_apply_button(self, button_info: dict) -> bool:
        """Click button - TODO: Implement with Claude in Chrome"""
        pass

    async def _fill_easy_apply_form(self, screenshot: bytes, profile: dict, resume_path: str, job_desc: str) -> dict:
        """Fill LinkedIn Easy Apply form"""
        screenshot_b64 = base64.b64encode(screenshot).decode()
        response = self.client.messages.create(
            model=self.model,
            max_tokens=1000,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": screenshot_b64}},
                    {"type": "text", "text": f"Fill this form with profile: {profile}. Resume: {resume_path}. Return JSON with fields_to_fill and ready_to_submit."}
                ]
            }]
        )
        try:
            text = response.content[0].text
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                form_plan = json.loads(match.group())
                return {
                    "fields_filled": len(form_plan.get("fields_to_fill", [])),
                    "ready_to_submit": form_plan.get("ready_to_submit", False),
                    "note": form_plan.get("note", "")
                }
        except:
            pass
        return {"fields_filled": 0, "ready_to_submit": False, "note": "Could not parse form"}

    async def _fill_external_form(self, screenshot: bytes, profile: dict, resume_path: str, job_desc: str) -> dict:
        """Fill external ATS form"""
        return await self._fill_easy_apply_form(screenshot, profile, resume_path, job_desc)

    async def _submit_form(self) -> bool:
        """Submit form - TODO: Implement"""
        pass

    async def _verify_submission(self) -> dict:
        """Verify submission"""
        screenshot = await self._get_page_screenshot()
        if not screenshot:
            return {"submitted": False}
        screenshot_b64 = base64.b64encode(screenshot).decode()
        response = self.client.messages.create(
            model=self.model,
            max_tokens=200,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": screenshot_b64}},
                    {"type": "text", "text": "Is this a confirmation page? Look for 'Thank you', 'Application received', etc. Return JSON: {\"submitted\": true|false}"}
                ]
            }]
        )
        try:
            text = response.content[0].text
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                return json.loads(match.group())
        except:
            pass
        return {"submitted": False}


async def apply_to_job(job_url: str, job_title: str, company_name: str, application_profile: dict, resume_path: str, job_description: str = "") -> dict:
    """Public API for applying to a job."""
    applier = ChromeJobApplier()
    return await applier.apply_to_job(job_url, job_title, company_name, application_profile, resume_path, job_description)
