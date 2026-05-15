"""
applier.py — multi-platform job application automation via Playwright.

Platform detection by URL pattern first, then page-source fallback.
Custom questions answered by Claude Haiku with fast-path for known profile fields.
SSE events emitted on _event_queue for consumption by the Flask /api/apply/stream endpoint.
Anti-bot: random delays, human-like typing, max 10 jobs/session.
CAPTCHA → logged as manual apply needed, skipped.
"""

import asyncio
import json
import logging
import os
import queue
import random
import re
import threading
from datetime import datetime
from pathlib import Path

import anthropic
from dotenv import load_dotenv
from playwright.async_api import async_playwright, Page, TimeoutError as PWTimeout

import db as _db
from config import load_config

load_dotenv()
log = logging.getLogger(__name__)

_MAX_PER_SESSION = 10
_DELAY_MIN       = 1.0
_DELAY_MAX       = 3.5
_SUPPORT_DIR     = Path(__file__).resolve().parent / "uploads" / "support"
_SESSION_FILE    = Path(__file__).resolve().parent / "uploads" / "linkedin_session.json"

# ── SSE event queue (consumed by Flask /api/apply/stream) ────────────────────
_event_queue: queue.Queue = queue.Queue()


def _emit(event_type: str, data: dict) -> None:
    _event_queue.put({"type": event_type, **data})


# ── Utilities ─────────────────────────────────────────────────────────────────

async def _delay(extra: float = 0.0) -> None:
    await asyncio.sleep(random.uniform(_DELAY_MIN, _DELAY_MAX) + extra)


async def _type_slowly(page: Page, selector: str, text: str) -> None:
    try:
        await page.click(selector, timeout=4000)
        for char in text:
            await page.keyboard.type(char)
            await asyncio.sleep(random.uniform(0.03, 0.10))
    except Exception:
        pass


async def _fill_if_empty(page: Page, selector: str, value: str) -> None:
    if not value:
        return
    try:
        loc = page.locator(selector)
        if await loc.count():
            existing = await loc.first.input_value()
            if not existing.strip():
                await _type_slowly(page, selector, value)
                await asyncio.sleep(0.3)
    except Exception:
        pass


async def _captcha_present(page: Page) -> bool:
    content = (await page.content()).lower()
    return any(k in content for k in ["captcha", "recaptcha", "hcaptcha", "challenge", "robot"])


def _profile(cfg: dict) -> dict:
    """Return the application_profile dict, falling back to top-level keys."""
    p = cfg.get("application_profile") or {}
    def g(key, fallback=None):
        return p.get(key) or cfg.get(key) or fallback or ""
    return {
        "first_name":         p.get("first_name") or (cfg.get("full_name") or "").split()[0],
        "last_name":          p.get("last_name")  or " ".join((cfg.get("full_name") or "").split()[1:]),
        "full_name":          cfg.get("full_name") or "",
        "email":              g("email", cfg.get("hotmail_address")),
        "phone":              g("phone"),
        "linkedin_url":       g("linkedin_url"),
        "github_url":         g("github_url"),
        "portfolio_url":      g("portfolio_url"),
        "current_location":   g("current_location"),
        "notice_period":      g("notice_period"),
        "salary_expectation": str(g("salary_expectation")),
        "salary_currency":    g("salary_currency", "EUR"),
        "willing_to_relocate":bool(p.get("willing_to_relocate") or cfg.get("willing_to_relocate")),
        "willing_to_travel":  g("willing_to_travel"),
        "work_permit":        g("work_permit"),
        "years_of_experience":str(g("years_of_experience")),
        "languages":          p.get("languages") or cfg.get("languages") or [],
    }


# ── Platform detection ────────────────────────────────────────────────────────

_PLATFORM_PATTERNS: list[tuple[str, str]] = [
    (r"linkedin\.com",              "linkedin"),
    (r"greenhouse\.io|boards\.greenhouse", "greenhouse"),
    (r"lever\.co",                  "lever"),
    (r"smartrecruiters\.com",       "smartrecruiters"),
    (r"ashbyhq\.com|jobs\.ashby",   "ashby"),
    (r"myworkdayjobs\.com|workday\.com", "workday"),
    (r"taleo\.net",                 "taleo"),
    (r"icims\.com",                 "icims"),
    (r"bamboohr\.com",              "bamboohr"),
    (r"personio\.de|personio\.com", "personio"),
    (r"workable\.com",              "workable"),
    (r"recruitee\.com",             "recruitee"),
    (r"jazzhr\.com|resumatorjobs\.com", "jazzhr"),
    (r"teamtailor\.com",            "teamtailor"),
    (r"jobvite\.com",               "jobvite"),
    (r"successfactors\.com|sap\.com", "successfactors"),
    (r"stepstone\.de",              "stepstone"),
    (r"xing\.com",                  "xing"),
    (r"indeed\.com",                "indeed"),
]

_PAGE_HINTS: list[tuple[str, str]] = [
    ("greenhouse-job-board",   "greenhouse"),
    ("lever-jobs-site",        "lever"),
    ("smartrecruiters",        "smartrecruiters"),
    ("ashby",                  "ashby"),
    ("workday",                "workday"),
    ("taleo",                  "taleo"),
    ("icims",                  "icims"),
    ("bamboohr",               "bamboohr"),
    ("personio",               "personio"),
    ("workable",               "workable"),
    ("recruitee",              "recruitee"),
    ("jazzhr",                 "jazzhr"),
    ("teamtailor",             "teamtailor"),
    ("jobvite",                "jobvite"),
    ("successfactors",         "successfactors"),
]


def detect_platform(url: str, page_source: str = "") -> str:
    url_lower = (url or "").lower()
    for pattern, platform in _PLATFORM_PATTERNS:
        if re.search(pattern, url_lower):
            return platform
    if page_source:
        src = page_source.lower()
        for hint, platform in _PAGE_HINTS:
            if hint in src:
                return platform
    return "unknown"


# ── Custom question answering ─────────────────────────────────────────────────

_FAST_PATH_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"salary|gehalt|compensation", re.I), "salary_expectation"),
    (re.compile(r"notice period|k.ndigungsfrist|notice", re.I), "notice_period"),
    (re.compile(r"years.*(experience|erfahrung)|experience.*years", re.I), "years_of_experience"),
    (re.compile(r"work.*(permit|authorization|authorisation|visa)|right.to.work", re.I), "work_permit"),
    (re.compile(r"relocat", re.I), "willing_to_relocate"),
    (re.compile(r"first.?name|vorname", re.I), "first_name"),
    (re.compile(r"last.?name|surname|nachname", re.I), "last_name"),
    (re.compile(r"full.?name|name", re.I), "full_name"),
    (re.compile(r"email|e-mail", re.I), "email"),
    (re.compile(r"phone|telefon|mobile", re.I), "phone"),
    (re.compile(r"location|city|stadt|current.*location", re.I), "current_location"),
    (re.compile(r"linkedin", re.I), "linkedin_url"),
    (re.compile(r"github", re.I), "github_url"),
    (re.compile(r"portfolio|website", re.I), "portfolio_url"),
]


def answer_custom_question(
    question_text: str,
    field_type: str,
    options: list[str],
    resume_text: str,
    profile: dict,
    job_desc: str,
) -> str:
    # Fast path: known profile fields
    for pattern, key in _FAST_PATH_PATTERNS:
        if pattern.search(question_text):
            val = profile.get(key)
            if val is None:
                continue
            if isinstance(val, bool):
                if field_type == "radio" and options:
                    return options[0] if val else (options[1] if len(options) > 1 else "No")
                return "Yes" if val else "No"
            val = str(val)
            if field_type == "select" and options:
                # Try to find the best matching option
                val_lower = val.lower()
                for opt in options:
                    if val_lower in opt.lower() or opt.lower() in val_lower:
                        return opt
                return options[0]
            return val

    # Claude fallback for truly unknown questions
    try:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            return options[0] if options else "Yes"
        client = anthropic.Anthropic(api_key=api_key)
        opts_txt = (", ".join(f'"{o}"' for o in options[:8])) if options else "free text"
        system = (
            "You are filling in a job application form on behalf of the candidate. "
            "Answer the question with just the answer value — no explanation, no punctuation. "
            "If there are options, return exactly one of them verbatim."
        )
        user = (
            f"Question: {question_text}\n"
            f"Field type: {field_type}\n"
            f"Available options: {opts_txt}\n\n"
            f"Candidate profile summary:\n{json.dumps(profile, ensure_ascii=False)[:600]}\n\n"
            f"Job description excerpt:\n{job_desc[:800]}\n\n"
            f"Resume excerpt:\n{resume_text[:600]}"
        )
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=64,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        answer = resp.content[0].text.strip().strip('"').strip("'")
        if options and answer not in options:
            for opt in options:
                if answer.lower() in opt.lower():
                    return opt
            return options[0]
        return answer
    except Exception as exc:
        log.warning("answer_custom_question Claude call failed: %s", exc)
        return options[0] if options else ""


# ── File helpers ─────────────────────────────────────────────────────────────

def _resume_path(cfg: dict) -> Path | None:
    p = cfg.get("paths", {}).get("resume_en")
    if p and Path(p).exists():
        return Path(p)
    default = Path(__file__).resolve().parent / "uploads" / "resume_en.pdf"
    return default if default.exists() else None


def _support_files() -> list[Path]:
    if not _SUPPORT_DIR.exists():
        return []
    return [p for p in sorted(_SUPPORT_DIR.iterdir())
            if p.is_file() and p.suffix.lower() in {".pdf", ".docx"}]


# ── Generic form-fill helpers ─────────────────────────────────────────────────

async def _fill_profile_fields(page: Page, profile: dict) -> None:
    """Fill standard Easy Apply / ATS form fields from the profile dict."""
    field_map = [
        ("input[name='firstName'], input[aria-label*='first name' i], input[id*='firstName' i]",
         profile["first_name"]),
        ("input[name='lastName'], input[aria-label*='last name' i], input[id*='lastName' i]",
         profile["last_name"]),
        ("input[name='name'], input[aria-label*='full name' i]",
         profile["full_name"]),
        ("input[type='email'], input[name='email'], input[aria-label*='email' i]",
         profile["email"]),
        ("input[name='phoneNumber'], input[aria-label*='phone' i], input[id*='phone' i]",
         profile["phone"]),
        ("input[aria-label*='linkedin' i], input[id*='linkedin' i]",
         profile["linkedin_url"]),
        ("input[aria-label*='github' i], input[id*='github' i]",
         profile["github_url"]),
        ("input[aria-label*='portfolio' i], input[aria-label*='website' i]",
         profile["portfolio_url"]),
        ("input[aria-label*='salary' i], input[aria-label*='expected salary' i], input[id*='salary' i]",
         profile["salary_expectation"]),
        ("input[aria-label*='notice period' i], input[aria-label*='notice' i], input[id*='notice' i]",
         profile["notice_period"]),
        ("input[aria-label*='years of experience' i], input[aria-label*='experience' i]",
         profile["years_of_experience"]),
        ("input[aria-label*='work authorization' i], input[aria-label*='work permit' i], "
         "input[aria-label*='visa' i], input[aria-label*='right to work' i]",
         profile["work_permit"]),
        ("input[aria-label*='current city' i], input[aria-label*='current location' i], "
         "input[aria-label*='location' i]",
         profile["current_location"]),
    ]
    for selector, value in field_map:
        if value:
            await _fill_if_empty(page, selector, str(value))

    if profile["willing_to_relocate"]:
        try:
            loc = page.locator(
                "label:has-text('Yes')[for*='relocate' i], "
                "input[type='radio'][aria-label*='relocate' i][value*='yes' i]"
            )
            if await loc.count():
                await loc.first.click()
        except Exception:
            pass


async def _upload_resume(page: Page, cfg: dict, index: int = 0) -> bool:
    resume = _resume_path(cfg)
    if not resume:
        return False
    try:
        inputs = page.locator("input[type='file']")
        if await inputs.count() > index:
            await inputs.nth(index).set_input_files(str(resume))
            await asyncio.sleep(0.5)
            return True
    except Exception:
        pass
    return False


async def _maybe_attach_support_docs(page: Page) -> None:
    files = _support_files()
    if not files:
        return
    labels = ["reference", "certificate", "additional", "document", "anlage", "attachment"]
    for label in labels:
        try:
            loc = page.locator(
                f"input[type='file'][aria-label*='{label}' i], "
                f"label:has-text('{label}') ~ input[type='file']"
            )
            if await loc.count():
                await loc.first.set_input_files([str(p) for p in files])
                await asyncio.sleep(0.4)
                break
        except Exception:
            pass


# ── Platform handlers ─────────────────────────────────────────────────────────

async def _apply_linkedin(page: Page, job: dict, cfg: dict, resume_text: str, profile: dict) -> dict:
    try:
        await page.goto(job["url"], timeout=30_000)
        await _delay()

        if await _captcha_present(page):
            return {"success": False, "manual": True, "note": "CAPTCHA on LinkedIn"}

        btn = page.locator("button:has-text('Easy Apply'), .jobs-apply-button")
        if not await btn.count():
            return {"success": False, "manual": True, "note": "No Easy Apply button"}

        _emit("apply_step", {"url": job["url"], "step": "Opening Easy Apply form"})
        await btn.first.click()
        await _delay()

        await _fill_profile_fields(page, profile)
        await _upload_resume(page, cfg)

        for step_n in range(12):
            await _maybe_attach_support_docs(page)

            # Answer any visible custom questions
            await _answer_visible_questions(page, resume_text, profile, job.get("description", ""))

            submit = page.locator("button:has-text('Submit application'), button:has-text('Submit')")
            nxt    = page.locator("button:has-text('Next'), button:has-text('Continue'), button:has-text('Review')")

            if await submit.count():
                _emit("apply_step", {"url": job["url"], "step": "Submitting"})
                await submit.first.click()
                await _delay()
                return {"success": True, "manual": False, "note": ""}
            elif await nxt.count():
                _emit("apply_step", {"url": job["url"], "step": f"Step {step_n + 1}"})
                await nxt.first.click()
                await _delay()
                await _fill_profile_fields(page, profile)
            else:
                break

        return {"success": False, "manual": True, "note": "Could not complete LinkedIn form"}

    except PWTimeout:
        return {"success": False, "manual": False, "note": "Timeout"}
    except Exception as exc:
        return {"success": False, "manual": False, "note": str(exc)}


async def _apply_greenhouse(page: Page, job: dict, cfg: dict, resume_text: str, profile: dict) -> dict:
    try:
        await page.goto(job["url"], timeout=30_000)
        await _delay()

        if await _captcha_present(page):
            return {"success": False, "manual": True, "note": "CAPTCHA"}

        _emit("apply_step", {"url": job["url"], "step": "Loading Greenhouse form"})

        await _fill_profile_fields(page, profile)
        await _upload_resume(page, cfg)
        await _answer_visible_questions(page, resume_text, profile, job.get("description", ""))

        submit = page.locator("input[type='submit'], button[type='submit'], button:has-text('Submit')")
        if await submit.count():
            _emit("apply_step", {"url": job["url"], "step": "Submitting"})
            await submit.first.click()
            await _delay()
            return {"success": True, "manual": False, "note": ""}

        return {"success": False, "manual": True, "note": "No submit button found"}

    except PWTimeout:
        return {"success": False, "manual": False, "note": "Timeout"}
    except Exception as exc:
        return {"success": False, "manual": False, "note": str(exc)}


async def _apply_lever(page: Page, job: dict, cfg: dict, resume_text: str, profile: dict) -> dict:
    try:
        await page.goto(job["url"], timeout=30_000)
        await _delay()

        if await _captcha_present(page):
            return {"success": False, "manual": True, "note": "CAPTCHA"}

        apply_btn = page.locator("a:has-text('Apply'), button:has-text('Apply')")
        if await apply_btn.count():
            await apply_btn.first.click()
            await _delay()

        _emit("apply_step", {"url": job["url"], "step": "Filling Lever form"})
        await _fill_profile_fields(page, profile)
        await _upload_resume(page, cfg)
        await _answer_visible_questions(page, resume_text, profile, job.get("description", ""))

        submit = page.locator("button:has-text('Submit application'), button[type='submit']")
        if await submit.count():
            _emit("apply_step", {"url": job["url"], "step": "Submitting"})
            await submit.first.click()
            await _delay()
            return {"success": True, "manual": False, "note": ""}

        return {"success": False, "manual": True, "note": "No submit button"}

    except PWTimeout:
        return {"success": False, "manual": False, "note": "Timeout"}
    except Exception as exc:
        return {"success": False, "manual": False, "note": str(exc)}


async def _apply_smartrecruiters(page: Page, job: dict, cfg: dict, resume_text: str, profile: dict) -> dict:
    try:
        await page.goto(job["url"], timeout=30_000)
        await _delay()

        btn = page.locator("button:has-text('Apply'), a:has-text('Apply Now')")
        if await btn.count():
            await btn.first.click()
            await _delay()

        _emit("apply_step", {"url": job["url"], "step": "Filling SmartRecruiters form"})
        await _fill_profile_fields(page, profile)
        await _upload_resume(page, cfg)
        await _answer_visible_questions(page, resume_text, profile, job.get("description", ""))

        for _ in range(8):
            submit = page.locator("button:has-text('Submit'), button:has-text('Send Application')")
            nxt    = page.locator("button:has-text('Next'), button:has-text('Continue')")
            if await submit.count():
                await submit.first.click()
                await _delay()
                return {"success": True, "manual": False, "note": ""}
            elif await nxt.count():
                await nxt.first.click()
                await _delay()
                await _fill_profile_fields(page, profile)
                await _answer_visible_questions(page, resume_text, profile, job.get("description", ""))
            else:
                break

        return {"success": False, "manual": True, "note": "Could not complete SmartRecruiters form"}

    except PWTimeout:
        return {"success": False, "manual": False, "note": "Timeout"}
    except Exception as exc:
        return {"success": False, "manual": False, "note": str(exc)}


async def _apply_ashby(page: Page, job: dict, cfg: dict, resume_text: str, profile: dict) -> dict:
    try:
        await page.goto(job["url"], timeout=30_000)
        await _delay()

        btn = page.locator("button:has-text('Apply'), a:has-text('Apply')")
        if await btn.count():
            await btn.first.click()
            await _delay()

        _emit("apply_step", {"url": job["url"], "step": "Filling Ashby form"})
        await _fill_profile_fields(page, profile)
        await _upload_resume(page, cfg)
        await _answer_visible_questions(page, resume_text, profile, job.get("description", ""))

        submit = page.locator("button:has-text('Submit'), button[type='submit']")
        if await submit.count():
            await submit.first.click()
            await _delay()
            return {"success": True, "manual": False, "note": ""}

        return {"success": False, "manual": True, "note": "No submit button"}

    except PWTimeout:
        return {"success": False, "manual": False, "note": "Timeout"}
    except Exception as exc:
        return {"success": False, "manual": False, "note": str(exc)}


async def _apply_workable(page: Page, job: dict, cfg: dict, resume_text: str, profile: dict) -> dict:
    try:
        await page.goto(job["url"], timeout=30_000)
        await _delay()

        btn = page.locator("a:has-text('Apply for this job'), button:has-text('Apply')")
        if await btn.count():
            await btn.first.click()
            await _delay()

        _emit("apply_step", {"url": job["url"], "step": "Filling Workable form"})
        await _fill_profile_fields(page, profile)
        await _upload_resume(page, cfg)
        await _answer_visible_questions(page, resume_text, profile, job.get("description", ""))

        submit = page.locator("button[type='submit'], button:has-text('Submit')")
        if await submit.count():
            await submit.first.click()
            await _delay()
            return {"success": True, "manual": False, "note": ""}

        return {"success": False, "manual": True, "note": "No submit button"}

    except PWTimeout:
        return {"success": False, "manual": False, "note": "Timeout"}
    except Exception as exc:
        return {"success": False, "manual": False, "note": str(exc)}


async def _apply_personio(page: Page, job: dict, cfg: dict, resume_text: str, profile: dict) -> dict:
    try:
        await page.goto(job["url"], timeout=30_000)
        await _delay()

        btn = page.locator("a:has-text('Jetzt bewerben'), a:has-text('Apply now'), button:has-text('Apply')")
        if await btn.count():
            await btn.first.click()
            await _delay()

        _emit("apply_step", {"url": job["url"], "step": "Filling Personio form"})
        await _fill_profile_fields(page, profile)
        await _upload_resume(page, cfg)
        await _answer_visible_questions(page, resume_text, profile, job.get("description", ""))

        submit = page.locator("button[type='submit'], button:has-text('Senden'), button:has-text('Submit')")
        if await submit.count():
            await submit.first.click()
            await _delay()
            return {"success": True, "manual": False, "note": ""}

        return {"success": False, "manual": True, "note": "No submit button"}

    except PWTimeout:
        return {"success": False, "manual": False, "note": "Timeout"}
    except Exception as exc:
        return {"success": False, "manual": False, "note": str(exc)}


async def _apply_recruitee(page: Page, job: dict, cfg: dict, resume_text: str, profile: dict) -> dict:
    return await _apply_generic(page, job, cfg, resume_text, profile, "Recruitee")


async def _apply_bamboohr(page: Page, job: dict, cfg: dict, resume_text: str, profile: dict) -> dict:
    return await _apply_generic(page, job, cfg, resume_text, profile, "BambooHR")


async def _apply_teamtailor(page: Page, job: dict, cfg: dict, resume_text: str, profile: dict) -> dict:
    return await _apply_generic(page, job, cfg, resume_text, profile, "Teamtailor")


async def _apply_jazzhr(page: Page, job: dict, cfg: dict, resume_text: str, profile: dict) -> dict:
    return await _apply_generic(page, job, cfg, resume_text, profile, "JazzHR")


async def _apply_jobvite(page: Page, job: dict, cfg: dict, resume_text: str, profile: dict) -> dict:
    return await _apply_generic(page, job, cfg, resume_text, profile, "Jobvite")


async def _apply_workday(page: Page, job: dict, cfg: dict, resume_text: str, profile: dict) -> dict:
    return {"success": False, "manual": True, "note": "Workday requires manual apply (complex forms)"}


async def _apply_taleo(page: Page, job: dict, cfg: dict, resume_text: str, profile: dict) -> dict:
    return {"success": False, "manual": True, "note": "Taleo requires manual apply (login-gated)"}


async def _apply_icims(page: Page, job: dict, cfg: dict, resume_text: str, profile: dict) -> dict:
    return {"success": False, "manual": True, "note": "iCIMS requires manual apply (login-gated)"}


async def _apply_successfactors(page: Page, job: dict, cfg: dict, resume_text: str, profile: dict) -> dict:
    return {"success": False, "manual": True, "note": "SuccessFactors requires manual apply"}


async def _apply_stepstone(page: Page, job: dict, cfg: dict, resume_text: str, profile: dict) -> dict:
    try:
        await page.goto(job["url"], timeout=30_000)
        await _delay()

        if await _captcha_present(page):
            return {"success": False, "manual": True, "note": "CAPTCHA on Stepstone"}

        btn = page.locator("a:has-text('Jetzt bewerben'), a:has-text('Apply now'), "
                           "button:has-text('Jetzt bewerben'), button:has-text('Apply now')")
        if not await btn.count():
            return {"success": False, "manual": True, "note": "No apply button on Stepstone"}

        await btn.first.click()
        await _delay()

        _emit("apply_step", {"url": job["url"], "step": "Filling Stepstone form"})
        await _upload_resume(page, cfg)

        submit = page.locator("button:has-text('Jetzt bewerben'), button:has-text('Submit'), "
                              "button:has-text('Senden'), button:has-text('Bewerbung absenden')")
        if await submit.count():
            await submit.first.click()
            await _delay()
            return {"success": True, "manual": False, "note": ""}

        return {"success": False, "manual": True, "note": "Could not complete Stepstone form"}

    except PWTimeout:
        return {"success": False, "manual": False, "note": "Timeout"}
    except Exception as exc:
        return {"success": False, "manual": False, "note": str(exc)}


async def _apply_xing(page: Page, job: dict, cfg: dict, resume_text: str, profile: dict) -> dict:
    return {"success": False, "manual": True, "note": "XING requires manual apply"}


async def _apply_indeed(page: Page, job: dict, cfg: dict, resume_text: str, profile: dict) -> dict:
    return {"success": False, "manual": True, "note": "Indeed requires manual apply (login-gated)"}


async def _apply_generic(page: Page, job: dict, cfg: dict, resume_text: str, profile: dict, platform_name: str) -> dict:
    try:
        await page.goto(job["url"], timeout=30_000)
        await _delay()

        if await _captcha_present(page):
            return {"success": False, "manual": True, "note": f"CAPTCHA on {platform_name}"}

        apply_btn = page.locator(
            "button:has-text('Apply'), a:has-text('Apply'), "
            "button:has-text('Bewerben'), a:has-text('Jetzt bewerben')"
        )
        if await apply_btn.count():
            await apply_btn.first.click()
            await _delay()

        _emit("apply_step", {"url": job["url"], "step": f"Filling {platform_name} form"})
        await _fill_profile_fields(page, profile)
        await _upload_resume(page, cfg)
        await _answer_visible_questions(page, resume_text, profile, job.get("description", ""))

        for _ in range(8):
            submit = page.locator(
                "button[type='submit'], input[type='submit'], "
                "button:has-text('Submit'), button:has-text('Send'), "
                "button:has-text('Senden'), button:has-text('Apply')"
            )
            nxt = page.locator("button:has-text('Next'), button:has-text('Continue')")
            if await submit.count():
                await submit.first.click()
                await _delay()
                return {"success": True, "manual": False, "note": ""}
            elif await nxt.count():
                await nxt.first.click()
                await _delay()
                await _fill_profile_fields(page, profile)
                await _answer_visible_questions(page, resume_text, profile, job.get("description", ""))
            else:
                break

        return {"success": False, "manual": True, "note": f"Could not complete {platform_name} form"}

    except PWTimeout:
        return {"success": False, "manual": False, "note": "Timeout"}
    except Exception as exc:
        return {"success": False, "manual": False, "note": str(exc)}


async def _apply_unknown(page: Page, job: dict, cfg: dict, resume_text: str, profile: dict) -> dict:
    return {"success": False, "manual": True, "note": "Unknown platform — manual apply required"}


_PLATFORM_HANDLERS = {
    "linkedin":       _apply_linkedin,
    "greenhouse":     _apply_greenhouse,
    "lever":          _apply_lever,
    "smartrecruiters":_apply_smartrecruiters,
    "ashby":          _apply_ashby,
    "workday":        _apply_workday,
    "taleo":          _apply_taleo,
    "icims":          _apply_icims,
    "bamboohr":       _apply_bamboohr,
    "personio":       _apply_personio,
    "workable":       _apply_workable,
    "recruitee":      _apply_recruitee,
    "jazzhr":         _apply_jazzhr,
    "teamtailor":     _apply_teamtailor,
    "jobvite":        _apply_jobvite,
    "successfactors": _apply_successfactors,
    "stepstone":      _apply_stepstone,
    "xing":           _apply_xing,
    "indeed":         _apply_indeed,
    "unknown":        _apply_unknown,
}


# ── Custom question scanner ────────────────────────────────────────────────────

async def _answer_visible_questions(page: Page, resume_text: str, profile: dict, job_desc: str) -> None:
    """Scan for unanswered form questions and fill them using answer_custom_question."""
    try:
        # Text inputs not yet filled
        inputs = page.locator(
            "input[type='text']:not([value]):not([aria-label*='search' i]), "
            "input[type='number']:not([value])"
        )
        count = await inputs.count()
        for i in range(min(count, 15)):
            try:
                inp = inputs.nth(i)
                label_text = await _get_label(page, inp)
                if not label_text:
                    continue
                existing = await inp.input_value()
                if existing.strip():
                    continue
                answer = answer_custom_question(label_text, "text", [], resume_text, profile, job_desc)
                if answer:
                    await inp.click()
                    await inp.fill(answer)
                    _emit("apply_answer", {"label": label_text[:60], "answer": answer[:80]})
            except Exception:
                pass

        # Selects not yet chosen
        selects = page.locator("select")
        sel_count = await selects.count()
        for i in range(min(sel_count, 10)):
            try:
                sel = selects.nth(i)
                label_text = await _get_label(page, sel)
                options_raw = await sel.locator("option").all_text_contents()
                options = [o.strip() for o in options_raw if o.strip() and o.strip().lower() not in ("select", "---", "please select", "")]
                if not options:
                    continue
                current = await sel.input_value()
                if current:
                    continue
                answer = answer_custom_question(label_text or "", "select", options, resume_text, profile, job_desc)
                if answer:
                    await sel.select_option(label=answer)
                    _emit("apply_answer", {"label": (label_text or "select")[:60], "answer": answer[:80]})
            except Exception:
                pass

    except Exception:
        pass


async def _get_label(page: Page, element) -> str:
    """Try to find label text associated with a form element."""
    try:
        el_id = await element.get_attribute("id")
        aria  = await element.get_attribute("aria-label")
        placeholder = await element.get_attribute("placeholder")
        if aria:
            return aria
        if placeholder:
            return placeholder
        if el_id:
            label = page.locator(f"label[for='{el_id}']")
            if await label.count():
                return (await label.first.text_content() or "").strip()
        parent_label = element.locator("xpath=ancestor::label[1]")
        if await parent_label.count():
            return (await parent_label.first.text_content() or "").strip()
    except Exception:
        pass
    return ""


# ── Main apply loop ───────────────────────────────────────────────────────────

async def _run_apply(jobs: list[dict], cfg: dict, stop_flag: threading.Event) -> None:
    import pdfplumber

    resume_path = _resume_path(cfg)
    resume_text = ""
    if resume_path:
        try:
            with pdfplumber.open(resume_path) as pdf:
                resume_text = "\n".join(p.extract_text() or "" for p in pdf.pages)
        except Exception:
            pass

    prof = _profile(cfg)
    headless = cfg.get("headless", True)
    session_id = _db.create_apply_session(len(jobs))

    _emit("apply_start", {"total": len(jobs)})

    success_count = 0
    manual_count  = 0
    failed_count  = 0
    applied_count = 0

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        try:
            ctx_kwargs = dict(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
            )
            if _SESSION_FILE.exists():
                ctx_kwargs["storage_state"] = str(_SESSION_FILE)

            context = await browser.new_context(**ctx_kwargs)

            # Validate LinkedIn session if present
            if _SESSION_FILE.exists():
                page = await context.new_page()
                await page.goto("https://www.linkedin.com/feed/", timeout=20_000)
                if "login" in page.url or "authwall" in page.url:
                    log.error("LinkedIn session expired — re-authenticate via web UI")
                    _emit("apply_result", {
                        "url": "", "title": "", "company": "",
                        "platform": "linkedin", "success": False,
                        "manual": False, "note": "LinkedIn session expired"
                    })
                    await browser.close()
                    _db.update_apply_session(session_id, datetime.utcnow().isoformat(),
                                             0, 0, len(jobs))
                    _emit("session_done", {"success": 0, "manual": 0, "failed": len(jobs)})
                    return
                await page.close()

            page = await context.new_page()

            for job in jobs:
                if stop_flag.is_set():
                    log.info("Apply session stopped by user")
                    break
                if applied_count >= _MAX_PER_SESSION:
                    log.info("Reached max per session (%d)", _MAX_PER_SESSION)
                    break

                url       = job.get("url", "")
                title     = job.get("title", "")
                company   = job.get("company", "")

                # Detect platform
                platform = detect_platform(url)
                _emit("platform_detected", {"url": url, "platform": platform})
                log.info("Apply [%s] %s @ %s", platform, title, company)

                handler = _PLATFORM_HANDLERS.get(platform, _apply_unknown)
                result  = await handler(page, job, cfg, resume_text, prof)

                _emit("apply_result", {
                    "url":      url,
                    "title":    title,
                    "company":  company,
                    "platform": platform,
                    "success":  result["success"],
                    "manual":   result["manual"],
                    "note":     result.get("note", ""),
                })

                if result["success"]:
                    success_count += 1
                    applied_count += 1
                    _db.log_application(job, status="Applied ✓")
                    log.info("Applied: %s @ %s", title, company)
                elif result["manual"]:
                    manual_count += 1
                    _db.log_manual_apply(url, title, company, platform, result.get("note", ""))
                    log.info("Manual apply needed: %s @ %s — %s", title, company, result.get("note"))
                else:
                    failed_count += 1
                    log.warning("Failed: %s @ %s — %s", title, company, result.get("note"))

                _emit("delay", {"seconds": round(random.uniform(_DELAY_MIN, _DELAY_MAX), 1)})
                await _delay()

        finally:
            await browser.close()

    finished = datetime.utcnow().isoformat()
    _db.update_apply_session(session_id, finished, success_count, manual_count, failed_count)
    _emit("session_done", {
        "success": success_count,
        "manual":  manual_count,
        "failed":  failed_count,
    })
    log.info("Apply session done — %d applied, %d manual, %d failed",
             success_count, manual_count, failed_count)


def run(jobs: list[dict], cfg: dict | None = None,
        stop_flag: threading.Event | None = None) -> None:
    """Synchronous entry point called from dashboard worker thread."""
    if cfg is None:
        cfg = load_config()
    if stop_flag is None:
        stop_flag = threading.Event()
    asyncio.run(_run_apply(jobs, cfg, stop_flag))


# ── Legacy single-job wrapper (used by main.py) ───────────────────────────────

def apply(job: dict, archive_path: Path, cfg: dict | None = None) -> bool:
    """Single-job synchronous wrapper kept for backward compatibility."""
    if cfg is None:
        cfg = load_config()
    results: list[bool] = []

    async def _one():
        resume_path = _resume_path(cfg)
        resume_text = ""
        if resume_path:
            try:
                import pdfplumber
                with pdfplumber.open(resume_path) as pdf:
                    resume_text = "\n".join(p.extract_text() or "" for p in pdf.pages)
            except Exception:
                pass
        prof     = _profile(cfg)
        platform = detect_platform(job.get("url", ""))
        handler  = _PLATFORM_HANDLERS.get(platform, _apply_unknown)
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=cfg.get("headless", True))
            ctx_kw  = dict(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0",
                viewport={"width": 1280, "height": 800},
            )
            if _SESSION_FILE.exists():
                ctx_kw["storage_state"] = str(_SESSION_FILE)
            context = await browser.new_context(**ctx_kw)
            page    = await context.new_page()
            try:
                result = await handler(page, job, cfg, resume_text, prof)
                results.append(result["success"])
            finally:
                await browser.close()

    asyncio.run(_one())
    return results[0] if results else False


if __name__ == "__main__":
    from config import setup_logging
    cfg = load_config()
    setup_logging(cfg)
    cfg["headless"] = False
    sample = {
        "title": "Backend Engineer",
        "company": "TestCorp GmbH",
        "location": "Berlin",
        "url": "https://www.linkedin.com/jobs/view/1234567890",
        "source": "LinkedIn",
    }
    run([sample], cfg)
