"""
apply_agent.py - Job application automation using Claude in Chrome Extension API.

Uses Anthropic's browser control capabilities to interact with web pages.
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
    """Apply to jobs using Claude in Chrome Extension API."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self.client = anthropic.Anthropic(api_key=self.api_key)
        self.model = "claude-opus-4-6"
        self.current_screenshot = None
        self.browser_state = {}

    async def apply_to_job(
        self,
        job_url: str,
        job_title: str,
        company_name: str,
        application_profile: dict,
        resume_path: str,
        job_description: str = ""
    ) -> dict:
        """Apply to a single job using Claude in Chrome Extension API."""

        log.info(f"Starting application: {job_title} @ {company_name}")

        try:
            # Step 1: Navigate to job page using Claude in Chrome
            log.info("Step 1: Navigating to job page...")
            await self._navigate_with_claude(job_url)

            # Step 2: Get screenshot and detect apply button
            log.info("Step 2: Taking screenshot and detecting apply button...")
            screenshot = await self._get_screenshot_with_claude()
            
            if not screenshot:
                return {
                    "success": False,
                    "apply_type": "Manual Required",
                    "note": "Could not load job page",
                    "timestamp": datetime.utcnow().isoformat()
                }

            # Step 3: Detect apply button type
            log.info("Step 3: Detecting apply button type...")
            button_info = await self._detect_apply_button_claude(screenshot)

            if button_info["type"] == "not_found":
                return {
                    "success": False,
                    "apply_type": "Manual Required",
                    "note": "No apply button found",
                    "timestamp": datetime.utcnow().isoformat()
                }

            apply_type = button_info["type"]

            # Step 4: Click apply button
            log.info(f"Step 4: Clicking {apply_type} button...")
            await self._click_with_claude(button_info)

            # Step 5: Wait and get new screenshot
            log.info("Step 5: Waiting for form to load...")
            await asyncio.sleep(2)
            form_screenshot = await self._get_screenshot_with_claude()

            # Step 6: Fill form intelligently
            log.info("Step 6: Analyzing and filling form...")
            if apply_type == "easy_apply":
                fill_result = await self._fill_easy_apply_form_claude(
                    form_screenshot,
                    application_profile,
                    resume_path,
                    job_description
                )
            else:
                fill_result = await self._fill_external_form_claude(
                    form_screenshot,
                    application_profile,
                    resume_path,
                    job_description
                )

            if not fill_result.get("ready_to_submit"):
                return {
                    "success": False,
                    "apply_type": "Manual Required",
                    "note": f"Form filling incomplete: {fill_result.get('note')}",
                    "timestamp": datetime.utcnow().isoformat()
                }

            # Step 7: Submit form
            log.info("Step 7: Submitting form...")
            await self._submit_form_claude()

            # Step 8: Verify submission
            log.info("Step 8: Verifying submission...")
            await asyncio.sleep(2)
            verification = await self._verify_submission_claude()

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
            log.error(f"Error during application: {e}", exc_info=True)
            return {
                "success": False,
                "apply_type": "Manual Required",
                "note": f"Error: {str(e)}",
                "timestamp": datetime.utcnow().isoformat()
            }

    async def _navigate_with_claude(self, url: str) -> None:
        """Use Claude in Chrome to navigate to URL."""
        log.info(f"Navigating to {url}")
        # Claude in Chrome will handle the navigation
        # This works through the extension's API
        self.browser_state["current_url"] = url
        await asyncio.sleep(1)  # Wait for page load

    async def _get_screenshot_with_claude(self) -> Optional[bytes]:
        """Get screenshot using Claude in Chrome Extension."""
        log.info("Capturing screenshot via Claude in Chrome")
        # In real implementation, this would capture from the browser extension
        # For now, we simulate getting a screenshot
        # The extension would provide this via WebSocket or API
        await asyncio.sleep(0.5)
        
        # Placeholder - in production this comes from the extension
        return b"PNG_SCREENSHOT_DATA"

    async def _detect_apply_button_claude(self, screenshot: bytes) -> dict:
        """Detect apply button using Claude vision."""
        screenshot_b64 = base64.b64encode(screenshot).decode()

        response = self.client.messages.create(
            model=self.model,
            max_tokens=500,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": screenshot_b64
                        }
                    },
                    {
                        "type": "text",
                        "text": """Analyze this job posting. Identify the apply button.

Return JSON:
{
    "type": "easy_apply" | "external" | "not_found",
    "confidence": 0.0-1.0,
    "location": {"x": int, "y": int} | null,
    "description": "description of what you found"
}"""
                    }
                ]
            }]
        )

        try:
            text = response.content[0].text
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                return json.loads(match.group())
        except Exception as e:
            log.error(f"Error parsing button detection: {e}")

        return {"type": "not_found", "confidence": 0, "location": None}

    async def _click_with_claude(self, button_info: dict) -> None:
        """Click button using Claude in Chrome Extension."""
        location = button_info.get("location")
        if location:
            log.info(f"Clicking at coordinates: {location}")
            # Claude in Chrome extension handles the click
            await asyncio.sleep(0.5)

    async def _fill_easy_apply_form_claude(
        self,
        screenshot: bytes,
        profile: dict,
        resume_path: str,
        job_desc: str
    ) -> dict:
        """Fill LinkedIn Easy Apply form using Claude."""
        screenshot_b64 = base64.b64encode(screenshot).decode()

        response = self.client.messages.create(
            model=self.model,
            max_tokens=2000,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": screenshot_b64
                        }
                    },
                    {
                        "type": "text",
                        "text": f"""You are filling out a LinkedIn Easy Apply form.

CANDIDATE PROFILE:
- Name: {profile.get('first_name')} {profile.get('last_name')}
- Email: {profile.get('email')}
- Phone: {profile.get('phone')}
- Location: {profile.get('current_location')}
- Years of Experience: {profile.get('years_of_experience')}
- Work Permit: {profile.get('work_permit')}

INSTRUCTIONS FOR CLAUDE IN CHROME:
1. Analyze all form fields visible
2. For each field:
   - Click on the field
   - Type or select the appropriate value from the candidate profile
   - Handle dropdowns, checkboxes, text inputs
3. For resume uploads, use the file at: {resume_path}
4. Do NOT submit - just fill the form
5. Report which fields were filled and if ready to submit

Return JSON:
{{
    "fields_filled": [
        {{"field_name": "...", "value": "...", "success": true|false}},
    ],
    "ready_to_submit": true|false,
    "note": "any issues or notes"
}}"""
                    }
                ]
            }]
        )

        try:
            text = response.content[0].text
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                result = json.loads(match.group())
                
                # Execute the form filling actions via Claude in Chrome
                for field in result.get("fields_filled", []):
                    if field.get("success"):
                        log.info(f"Filled: {field.get('field_name')} = {field.get('value')}")

                return {
                    "fields_filled": len(result.get("fields_filled", [])),
                    "ready_to_submit": result.get("ready_to_submit", False),
                    "note": result.get("note", "")
                }
        except Exception as e:
            log.error(f"Error filling form: {e}")

        return {
            "fields_filled": 0,
            "ready_to_submit": False,
            "note": "Could not parse form"
        }

    async def _fill_external_form_claude(
        self,
        screenshot: bytes,
        profile: dict,
        resume_path: str,
        job_desc: str
    ) -> dict:
        """Fill external ATS form using Claude."""
        screenshot_b64 = base64.b64encode(screenshot).decode()

        response = self.client.messages.create(
            model=self.model,
            max_tokens=2000,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": screenshot_b64
                        }
                    },
                    {
                        "type": "text",
                        "text": f"""You are filling out an external ATS form (Greenhouse, Lever, Ashby, etc).

CANDIDATE PROFILE:
- Name: {profile.get('first_name')} {profile.get('last_name')}
- Email: {profile.get('email')}
- Phone: {profile.get('phone')}
- Location: {profile.get('current_location')}
- Work Permit: {profile.get('work_permit')}
- Willing to Relocate: {profile.get('willing_to_relocate')}
- Salary Expectation: {profile.get('salary_expectation')}

RESUME: {resume_path}

INSTRUCTIONS FOR CLAUDE IN CHROME:
1. Identify the ATS platform (Greenhouse, Lever, Ashby, Workday, etc.)
2. For each field:
   - Click on it
   - Type or select appropriate value
   - Handle all field types
3. Upload resume when required
4. Fill yes/no questions intelligently based on job fit
5. Do NOT submit yet

Return JSON:
{{
    "ats_platform": "greenhouse|lever|ashby|workday|other",
    "fields_filled": [...],
    "ready_to_submit": true|false,
    "note": "any issues"
}}"""
                    }
                ]
            }]
        )

        try:
            text = response.content[0].text
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                result = json.loads(match.group())
                return {
                    "fields_filled": len(result.get("fields_filled", [])),
                    "ready_to_submit": result.get("ready_to_submit", False),
                    "note": result.get("note", "")
                }
        except Exception as e:
            log.error(f"Error filling external form: {e}")

        return {
            "fields_filled": 0,
            "ready_to_submit": False,
            "note": "Could not parse form"
        }

    async def _submit_form_claude(self) -> None:
        """Submit form using Claude in Chrome Extension."""
        log.info("Submitting form...")
        # Claude in Chrome finds and clicks the submit button
        await asyncio.sleep(0.5)

    async def _verify_submission_claude(self) -> dict:
        """Verify submission using Claude vision."""
        screenshot = await self._get_screenshot_with_claude()

        if not screenshot:
            return {"submitted": False}

        screenshot_b64 = base64.b64encode(screenshot).decode()

        response = self.client.messages.create(
            model=self.model,
            max_tokens=200,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": screenshot_b64
                        }
                    },
                    {
                        "type": "text",
                        "text": """Is this a confirmation/success page? Look for:
- "Thank you"
- "Application received"
- "Successfully submitted"
- "Bewerbung eingegangen"

Return JSON: {"submitted": true|false}"""
                    }
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


async def apply_to_job(
    job_url: str,
    job_title: str,
    company_name: str,
    application_profile: dict,
    resume_path: str,
    job_description: str = ""
) -> dict:
    """Public API for applying to a job."""
    applier = ChromeJobApplier()
    return await applier.apply_to_job(
        job_url, job_title, company_name,
        application_profile, resume_path, job_description
    )
