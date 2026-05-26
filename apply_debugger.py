"""
apply_debugger.py - Debug tool for testing job applications.
"""

import asyncio
import logging
import argparse
from config_loader import load_config
from apply_agent import apply_to_job

logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger(__name__)


async def test_job_application(job_url: str):
    """Test applying to a single job."""
    log.info(f"Testing job application: {job_url}")
    
    # Load configuration
    config = load_config()
    profile = config.get("application_profile", {})
    resume_en = config.get("resume_paths", {}).get("en")
    
    if not resume_en:
        log.error("❌ Resume not found in Dropbox CV folder")
        log.info(f"Expected at: {config.get('paths', {}).get('dropbox_cv')}/resume_en.pdf")
        return
    
    log.info(f"✅ Resume found: {resume_en}")
    log.info(f"✅ Profile: {profile.get('first_name')} {profile.get('last_name')}")
    
    # Apply to job
    result = await apply_to_job(
        job_url=job_url,
        job_title="Test Job",
        company_name="Test Company",
        application_profile=profile,
        resume_path=resume_en,
        job_description="Test job description"
    )
    
    log.info(f"\n{'='*50}")
    log.info(f"Result: {result}")
    log.info(f"Success: {result.get('success')}")
    log.info(f"Apply Type: {result.get('apply_type')}")
    log.info(f"Note: {result.get('note')}")
    log.info(f"Timestamp: {result.get('timestamp')}")
    log.info(f"{'='*50}")


def main():
    parser = argparse.ArgumentParser(description="Debug tool for testing job applications")
    parser.add_argument("--url", required=True, help="LinkedIn job URL to test")
    args = parser.parse_args()
    
    asyncio.run(test_job_application(args.url))


if __name__ == "__main__":
    main()
