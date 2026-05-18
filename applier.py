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
import traceback
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

# ── In-session answer cache (keyed by question+type+options, cleared per run) ─
_answer_cache: dict[tuple, str] = {}


def _cache_key(question: str, field_type: str, options: list[str]) -> tuple:
    return (question.lower().strip(), field_type.lower(), tuple(sorted(o.lower() for o in options)))


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


async def _reliable_fill(page: Page, element, value: str) -> None:
    """Fill an input/textarea; verify React accepted the value, dispatch blur."""
    try:
        await element.click()
        await asyncio.sleep(0.1)
        await element.fill(value)
        await asyncio.sleep(0.15)
        current = await element.input_value()
        if current.strip() != value.strip():
            try:
                await page.evaluate(
                    "(v)=>{"
                    "const e=document.activeElement;if(!e)return;"
                    "const P=e.tagName==='TEXTAREA'?"
                    "HTMLTextAreaElement.prototype:HTMLInputElement.prototype;"
                    "Object.getOwnPropertyDescriptor(P,'value').set.call(e,v);"
                    "e.dispatchEvent(new Event('input',{bubbles:true}));"
                    "e.dispatchEvent(new Event('change',{bubbles:true}));}",
                    value
                )
            except Exception:
                pass
        try:
            await element.dispatchEvent("blur")
        except Exception:
            pass
    except Exception:
        pass


async def _has_captcha(page: Page) -> bool:
    url = page.url.lower()
    if any(x in url for x in [
        "challenge", "checkpoint/challenge", "recaptcha", "hcaptcha",
        "captcha", "/security/check",
    ]):
        return True
    for sel in [
        "iframe[src*='recaptcha']",
        "iframe[src*='hcaptcha']",
        ".g-recaptcha",
        ".h-captcha",
        "#captcha-internal",
        "iframe[title*='challenge']",
        "iframe[title*='CAPTCHA']",
    ]:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                return True
        except Exception:
            pass
    return False


def _profile(cfg: dict) -> dict:
    """Return a flat profile dict sourced entirely from application_profile in config."""
    p = cfg.get("application_profile") or {}
    def g(key, fallback=""):
        return p.get(key) or fallback
    first = g("first_name")
    last  = g("last_name")
    return {
        "first_name":         first,
        "last_name":          last,
        "full_name":          f"{first} {last}".strip(),
        "email":              g("email"),
        "phone":              g("phone"),
        "linkedin_url":       g("linkedin_url"),
        "github_url":         g("github_url"),
        "portfolio_url":      g("portfolio_url"),
        "current_location":   g("current_location"),
        "notice_period":      g("notice_period"),
        "salary_expectation": str(g("salary_expectation", "0")),
        "salary_currency":    g("salary_currency", "EUR"),
        "willing_to_relocate":bool(p.get("willing_to_relocate")),
        "willing_to_travel":  g("willing_to_travel"),
        "work_permit":        g("work_permit"),
        "years_of_experience":str(g("years_of_experience", "0")),
        "languages":          p.get("languages") or [],
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
    (re.compile(r"salary|gehalt|compensation|expected.*pay|pay.*expectation", re.I), "salary_expectation"),
    (re.compile(r"notice period|k.ndigungsfrist|when.*start|earliest.*start|availability", re.I), "notice_period"),
    (re.compile(r"years.*(experience|erfahrung)|experience.*years", re.I), "years_of_experience"),
    (re.compile(r"work.*(permit|authorization|authorisation|visa)|right.to.work|eligible.*work", re.I), "work_permit"),
    (re.compile(r"comfort.*commut|willing.*commut|commut.*comfort", re.I), "willing_to_relocate"),
    (re.compile(r"relocat", re.I), "willing_to_relocate"),
    (re.compile(r"travel", re.I), "willing_to_travel"),
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


def _profile_summary(profile: dict) -> str:
    """Format profile as a readable key-value block for Claude prompts."""
    langs = profile.get("languages") or []
    lang_str = ", ".join(f"{l.get('language')} ({l.get('level')})" for l in langs) if langs else ""
    lines = [
        f"Name: {profile.get('first_name', '')} {profile.get('last_name', '')}",
        f"Email: {profile.get('email', '')}",
        f"Phone: {profile.get('phone', '')}",
        f"Location: {profile.get('current_location', '')}",
        f"Years of experience: {profile.get('years_of_experience', '')}",
        f"Work permit: {profile.get('work_permit', '')}",
        f"Notice period: {profile.get('notice_period', '')}",
        f"Salary expectation: {profile.get('salary_expectation', '')} {profile.get('salary_currency', 'EUR')}",
        f"Willing to relocate: {profile.get('willing_to_relocate', '')}",
        f"Willing to travel: {profile.get('willing_to_travel', '')}",
        f"Languages: {lang_str}",
        f"LinkedIn: {profile.get('linkedin_url', '')}",
        f"GitHub: {profile.get('github_url', '')}",
    ]
    return "\n".join(l for l in lines if l.split(": ", 1)[-1].strip())


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
            # Tech-specific questions ("with Java", "using Kafka", "in Python") → Claude infers from resume
            if key == "years_of_experience" and re.search(r"\b(with|using|in)\b.{1,40}$", question_text, re.I):
                continue
            val = profile.get(key)
            if val is None:
                continue
            if isinstance(val, bool):
                if field_type == "radio" and options:
                    return options[0] if val else (options[1] if len(options) > 1 else "No")
                return "Yes" if val else "No"
            val = str(val)
            if field_type == "select" and options:
                val_lower = val.lower()
                for opt in options:
                    if val_lower in opt.lower() or opt.lower() in val_lower:
                        return opt
                return options[0]
            return val

    # Claude fallback for open/unknown questions — pass full profile + resume
    # Check in-session cache first to avoid redundant API calls across jobs
    _ck = _cache_key(question_text, field_type, options)
    if _ck in _answer_cache:
        log.debug("Cache hit for question: %r", question_text[:60])
        return _answer_cache[_ck]

    try:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            return options[0] if options else "Yes"
        client = anthropic.Anthropic(api_key=api_key)
        opts_txt = (", ".join(f'"{o}"' for o in options[:8])) if options else "free text"
        if field_type == "number":
            system = (
                "CRITICAL: Return ONLY a single integer. No words. No sentences. No units. "
                "Just a number like: 2\n"
                "If unsure or no direct experience, return: 1\n\n"
                f"You ARE {profile.get('first_name', 'the candidate')} {profile.get('last_name', '')} — "
                "you are filling in your own job application form. "
                "Return ONLY a plain integer. No words, no units, no sentences. "
                "If the candidate has little or no experience with this specific technology, "
                "return 1 — never return 0."
            )
        else:
            _no_refusal = (
                "If the candidate has little or no direct experience with the specific technology asked about, "
                "do NOT say 'no experience', 'I have not used', or 'I cannot provide'. "
                "Instead write 1-2 honest sentences in first person acknowledging limited hands-on exposure "
                "while connecting it to related skills or general experience "
                "(e.g. 'I have some foundational exposure to X through related work with Y and am actively building on this.')."
                if field_type in ("text", "textarea") else ""
            )
            system = (
                f"You ARE {profile.get('first_name', 'the candidate')} {profile.get('last_name', '')} — "
                "you are filling in your own job application form. "
                "Write all answers in first person (I, my, me). "
                "Use the profile data below as the source of truth for all personal details. "
                + (_no_refusal + " " if _no_refusal else "")
                + "Answer with just the answer value — no preamble, no 'Based on my profile', no meta-commentary. "
                "For textarea/open questions write 2-4 natural sentences as yourself. "
                "If there are options, return exactly one of them verbatim."
            )
        user = (
            f"Question: {question_text}\n"
            f"Field type: {field_type}\n"
            f"Available options: {opts_txt}\n\n"
            f"My application profile (use this as ground truth):\n{_profile_summary(profile)}\n\n"
            f"Job description:\n{job_desc[:600]}\n\n"
            f"My resume:\n{resume_text[:1200]}"
        )
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        answer = resp.content[0].text.strip().strip('"').strip("'")
        if options and answer not in options:
            for opt in options:
                if answer.lower() in opt.lower():
                    answer = opt
                    break
            else:
                answer = options[0]
        _answer_cache[_ck] = answer
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

async def _handle_autocomplete(page: Page, inp, value: str) -> bool:
    """After filling a text field, detect and select from any autocomplete dropdown."""
    await asyncio.sleep(0.6)
    suggestion_selectors = [
        "[role='listbox'] [role='option']",
        "[role='listbox'] li",
        ".basic-typeahead__selectable",
        ".search-typeahead-v2__hit",
        "ul.typeahead-list li",
        "[data-test-autocomplete-result]",
        ".autocomplete__results li",
    ]
    for sel in suggestion_selectors:
        try:
            suggestions = page.locator(sel)
            count = await suggestions.count()
            if count > 0:
                value_lower = value.lower()
                best = None
                for i in range(min(count, 8)):
                    txt = (await suggestions.nth(i).text_content() or "").strip()
                    if value_lower in txt.lower() or txt.lower() in value_lower:
                        best = suggestions.nth(i)
                        break
                target = best or suggestions.first
                await target.click()
                await asyncio.sleep(0.4)
                _emit("apply_step", {"url": "", "step":
                    f"  ✎ Autocomplete selected: '{(await target.text_content() or '').strip()[:50]}'"})
                return True
        except Exception:
            continue
    return False


async def _fill_profile_fields(page: Page, profile: dict) -> None:
    """Fill standard Easy Apply / ATS form fields from the profile dict.

    Personal identity fields (email, name, phone) are ALWAYS overwritten —
    platforms like LinkedIn pre-fill these from the account but we want
    the candidate's preferred contact details from config.
    All other fields only fill if currently empty.
    """
    url = ""  # used for _emit — not critical if missing
    async def _force_fill(label: str, value: str, locators: list):
        """Try each locator in order, force-type value into first visible input.
        Only emits a log line on success. Skips <select> elements."""
        if not value:
            return
        for loc in locators:
            try:
                count = await loc.count()
                for i in range(min(count, 3)):
                    el = loc.nth(i)
                    try:
                        if not await el.is_visible():
                            continue
                        # Skip dropdowns — they need select_option not keyboard typing
                        tag = await el.evaluate("e => e.tagName.toLowerCase()")
                        if tag == "select":
                            continue
                    except Exception:
                        continue
                    try:
                        await el.click()
                        await asyncio.sleep(0.15)
                        await page.keyboard.press("Control+a")
                        await asyncio.sleep(0.05)
                        await page.keyboard.press("Backspace")
                        await asyncio.sleep(0.1)
                        for char in str(value):
                            await page.keyboard.type(char)
                            await asyncio.sleep(random.uniform(0.03, 0.07))
                        await asyncio.sleep(0.3)
                        current = await el.input_value()
                        # JS fallback if React reset value — target the focused element
                        if current.strip() != str(value).strip():
                            try:
                                await page.evaluate(
                                    "(v)=>{"
                                    "const e=document.activeElement;if(!e||e.tagName!=='INPUT')return;"
                                    "Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')"
                                    ".set.call(e,v);"
                                    "e.dispatchEvent(new Event('input',{bubbles:true}));"
                                    "e.dispatchEvent(new Event('change',{bubbles:true}));}",
                                    str(value)
                                )
                                current = await el.input_value()
                            except Exception:
                                pass
                        _emit("apply_step", {"url": url, "step": f"  ✎ {label}: {current or value}"})
                        return  # done
                    except Exception:
                        pass
            except Exception:
                pass
        pass  # field not on this page — silent

    import re as _re

    # LinkedIn Easy Apply does NOT render first name, last name, or email as editable
    # text inputs — name is read-only from the LinkedIn account, email is a <select>
    # handled by _handle_email_dropdown. Only attempt these on non-LinkedIn ATS pages.
    # We detect LinkedIn Easy Apply by the presence of the modal class.
    try:
        is_linkedin_modal = bool(await page.locator(
            ".jobs-easy-apply-modal, .jobs-easy-apply-content"
        ).count())
    except Exception:
        is_linkedin_modal = False

    if not is_linkedin_modal:
        await _force_fill("First name", profile["first_name"], [
            page.get_by_label(_re.compile(r"first\s*name", _re.I)),
            page.get_by_label(_re.compile(r"vorname", _re.I)),
            page.locator("input[name='firstName'], input[id*='firstName' i], input[autocomplete='given-name']"),
        ])
        await _force_fill("Last name", profile["last_name"], [
            page.get_by_label(_re.compile(r"last\s*name|surname|nachname", _re.I)),
            page.locator("input[name='lastName'], input[id*='lastName' i], input[autocomplete='family-name']"),
        ])
        await _force_fill("Email", profile["email"], [
            page.get_by_label(_re.compile(r"e.?mail", _re.I)),
            page.locator("input[type='email'], input[name='email'], input[id*='email' i], input[autocomplete='email']"),
        ])

    # Phone — use label-based detection (no JS evaluate) to check for country code dropdown.
    # If a label "Phone country code" or "Telefonvorwahl" exists on the page, the form
    # has a separate dropdown for the prefix, so we fill only the local subscriber number.
    phone_value = profile["phone"]
    try:
        cc_label = page.locator("label").filter(
            has_text=_re.compile(r"phone country|country code|telefonvorwahl", _re.I)
        )
        if await cc_label.count():
            phone_value = _local_phone(phone_value)
            _emit("apply_step", {"url": url,
                "step": f"  ℹ Country code field detected — filling local number: {phone_value}"})
    except Exception:
        pass

    await _force_fill("Phone", phone_value, [
        page.get_by_label(_re.compile(r"mobile\s*phone\s*number", _re.I)),
        page.get_by_label(_re.compile(r"^phone\s*(?:number)?$", _re.I)),
        page.get_by_label(_re.compile(r"telefonnummer|handynummer", _re.I)),
        page.locator("input[type='tel'], input[name='phoneNumber'], input[id*='phoneNumber' i]"),
    ])

    # Fields filled only if currently empty
    fill_map = [
        ("Full name",          "input[name='name'], input[aria-label*='full name' i]",                                                                                   profile["full_name"]),
        ("LinkedIn",           "input[aria-label*='linkedin' i], input[id*='linkedin' i]",                                                                               profile["linkedin_url"]),
        ("GitHub",             "input[aria-label*='github' i], input[id*='github' i]",                                                                                   profile["github_url"]),
        ("Portfolio",          "input[aria-label*='portfolio' i], input[aria-label*='website' i]",                                                                       profile["portfolio_url"]),
        ("Salary",             "input[aria-label*='salary' i], input[aria-label*='expected salary' i], input[id*='salary' i]",                                           profile["salary_expectation"]),
        ("Notice period",      "input[aria-label*='notice period' i], input[aria-label*='notice' i], input[id*='notice' i]",                                             profile["notice_period"]),
        ("Years experience",   "input[aria-label*='years of experience' i], input[aria-label*='experience' i]",                                                          profile["years_of_experience"]),
        ("Work permit",        "input[aria-label*='work authorization' i], input[aria-label*='work permit' i], input[aria-label*='visa' i], input[aria-label*='right to work' i]", profile["work_permit"]),
        ("Location",           "input[aria-label*='current city' i], input[aria-label*='current location' i], input[aria-label*='location' i]",                          profile["current_location"]),
    ]
    for label, selector, value in fill_map:
        if not value:
            continue
        try:
            loc = page.locator(selector)
            if await loc.count():
                el = loc.first
                if await el.is_visible():
                    existing = await el.input_value()
                    if not existing.strip():
                        await _type_slowly(page, selector, str(value))
                        await _handle_autocomplete(page, el, str(value))
                        _emit("apply_step", {"url": url, "step": f"  ✎ {label}: {value}"})
        except Exception:
            pass

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


async def _select_or_upload_resume(page: Page, cfg: dict) -> bool:
    """Select the resume already on LinkedIn — never uploads from local disk."""
    try:
        # ── Style A: label text "Select/Deselect resume {name}" ──────────────────
        all_labels = page.locator("label")
        label_count = await all_labels.count()

        select_labels: list = []
        deselect_labels: list = []

        for i in range(label_count):
            try:
                lbl = all_labels.nth(i)
                if not await lbl.is_visible():
                    continue
                tl = (await lbl.text_content() or "").strip().lower()
                if tl.startswith("deselect resume"):
                    deselect_labels.append(lbl)
                elif tl.startswith("select resume"):
                    select_labels.append(lbl)
            except Exception:
                pass

        if deselect_labels:
            log.info("✓ Resume already selected")
            _emit("apply_step", {"url": "", "step": "✓ Resume already selected"})
            return True

        if select_labels:
            await select_labels[0].click()
            await asyncio.sleep(0.7)
            log.info("✓ Resume selected")
            _emit("apply_step", {"url": "", "step": "✓ Resume selected"})
            return True

        # ── Style B: radio button pattern (current LinkedIn UI) ──────────────────
        radios = page.locator("input[type='radio']")
        r_count = await radios.count()
        if r_count:
            resume_radios: list = []
            for i in range(r_count):
                try:
                    radio = radios.nth(i)
                    if not await radio.is_visible():
                        continue

                    label_text = ""
                    radio_id = (await radio.get_attribute("id") or "").strip()

                    if radio_id:
                        try:
                            lbl = page.locator(f"label[for='{radio_id}']")
                            if await lbl.count():
                                label_text = (await lbl.first.text_content() or "").strip()
                        except Exception:
                            pass

                    if not label_text:
                        try:
                            anc = radio.locator("xpath=ancestor::label[1]")
                            if await anc.count():
                                label_text = (await anc.first.text_content() or "").strip()
                        except Exception:
                            pass

                    if not label_text:
                        try:
                            par = radio.locator("xpath=../..")
                            if await par.count():
                                label_text = (await par.first.text_content() or "").strip()
                        except Exception:
                            pass

                    if any(kw in label_text.lower() for kw in ("pdf", "doc", "kb", "mb")):
                        resume_radios.append((radio, radio_id, label_text))
                except Exception:
                    pass

            if resume_radios:
                for radio, radio_id, label_text in resume_radios:
                    try:
                        if await radio.is_checked():
                            log.info("✓ Resume already selected")
                            _emit("apply_step", {"url": "", "step": "✓ Resume already selected"})
                            return True
                    except Exception:
                        pass

                radio, radio_id, label_text = resume_radios[0]
                clicked = False
                if radio_id:
                    try:
                        lbl = page.locator(f"label[for='{radio_id}']")
                        if await lbl.count():
                            await lbl.first.click()
                            clicked = True
                    except Exception:
                        pass
                if not clicked:
                    await radio.click()
                await asyncio.sleep(0.7)
                log.info("✓ Resume selected")
                _emit("apply_step", {"url": "", "step": "✓ Resume selected"})
                return True

    except Exception as e:
        log.warning("_select_or_upload_resume error: %s", e)

    return False


async def _upload_resume(page: Page, cfg: dict, index: int = 0) -> bool:
    """Upload resume from local disk — for external ATS (Greenhouse, Lever, etc.) only."""
    resume = _resume_path(cfg)
    if not resume:
        log.warning("_upload_resume: no resume file found — skipping")
        return False
    inputs = page.locator("input[type='file']")
    if await inputs.count():
        await inputs.first.set_input_files(str(resume))
        await asyncio.sleep(1.0)
        _emit("apply_step", {"url": "", "step": f"  ✎ Resume: uploaded '{resume.name}'"})
        return True
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


# ── LinkedIn-specific helpers ────────────────────────────────────────────────

async def _handle_email_dropdown(page: Page, profile: dict, url: str) -> None:
    """Select the correct email from LinkedIn's email dropdown and log every step."""
    try:
        # LinkedIn uses aria-labelledby (not aria-label) on the select element,
        # so attribute selectors like select[aria-label*='email'] miss it.
        # Strategy: find every <select> inside the modal, read its associated label text,
        # and pick the one whose label says "email".
        email_select = None

        # Method 1: get_by_label — Playwright resolves aria-labelledby automatically
        try:
            lbl_loc = page.get_by_label(re.compile(r"e.?mail", re.I))
            for i in range(await lbl_loc.count()):
                el = lbl_loc.nth(i)
                try:
                    if await el.is_visible():
                        tag = await el.evaluate("e => e.tagName.toLowerCase()")
                        if tag == "select":
                            email_select = el
                            _emit("apply_step", {"url": url,
                                "step": "  📧 Email <select> found via get_by_label"})
                            break
                except Exception:
                    pass
        except Exception:
            pass

        # Method 2: any visible select whose option values look like email addresses
        if not email_select:
            try:
                all_selects = page.locator("select")
                for i in range(await all_selects.count()):
                    sel = all_selects.nth(i)
                    try:
                        if not await sel.is_visible():
                            continue
                        opts = await sel.locator("option").all_text_contents()
                        if any("@" in o for o in opts):
                            email_select = sel
                            _emit("apply_step", {"url": url,
                                "step": "  📧 Email <select> found by scanning option values"})
                            break
                    except Exception:
                        pass
            except Exception:
                pass

        if not email_select:
            return  # no email dropdown on this page — silent

        target_email = profile["email"].strip().lower()
        options = await email_select.locator("option").all_text_contents()
        options = [o.strip() for o in options if o.strip()]

        _emit("apply_step", {"url": url,
            "step": f"  📧 Email options: {', '.join(options)}"})

        match = next((o for o in options if target_email in o.lower()), None)
        if match:
            await email_select.select_option(label=match)
            await page.wait_for_timeout(300)
            _emit("apply_step", {"url": url, "step": f"  ✎ Email: selected '{match}'"})
        else:
            current = await email_select.input_value()
            _emit("apply_step", {"url": url,
                "step": f"  ⚠ Email: wanted '{profile['email']}' — not in dropdown. "
                        f"Current: '{current}'. Options: {', '.join(options)}"})
    except Exception as exc:
        _emit("apply_step", {"url": url, "step": f"  ⚠ Email dropdown error: {exc}"})


def _local_phone(phone: str, country_code: str = "+49") -> str:
    """Strip international prefix from phone number (e.g. +491744548555 → 1744548555)."""
    if phone.startswith(country_code):
        return phone[len(country_code):]
    if phone.startswith("+"):
        # Strip any +XX or +XXX prefix
        return re.sub(r"^\+\d{1,3}", "", phone)
    return phone


# ── Platform handlers ─────────────────────────────────────────────────────────

async def _route_to_handler(page: Page, job: dict, cfg: dict, resume_text: str,
                             profile: dict, platform: str) -> dict:
    """Route an external apply redirect to the appropriate ATS handler."""
    handler = _PLATFORM_HANDLERS.get(platform)
    if handler and platform not in ("linkedin", "unknown"):
        new_url = page.url
        log.info("Routing to %s handler (URL: %s)", platform, new_url)
        return await handler(page, dict(job, url=new_url), cfg, resume_text, profile)
    log.warning("DEBUG pre-manual: URL=%s", page.url)
    log.warning("DEBUG pre-manual: Page title=%s", await page.title())
    log.warning("DEBUG pre-manual: Reason=External apply, unknown platform: %s", platform)
    _emit("apply_step", {"url": job.get("url", ""), "step": f"⚠️ Going to manual: External apply — unknown platform: {platform}"})
    return {"success": False, "manual": True, "note": f"External apply — unknown platform: {platform}"}


async def _verify_submission(page: Page) -> bool:
    """Check if page shows a submission confirmation."""
    await page.wait_for_timeout(2000)
    try:
        text = (await page.content()).lower()
        for phrase in [
            "thank you", "application received", "successfully submitted",
            "application submitted", "we'll be in touch", "your application",
            "bewerbung eingegangen", "vielen dank", "erfolgreich",
        ]:
            if phrase in text:
                return True
    except Exception:
        pass
    return False


async def _get_page_errors(page: Page) -> list[str]:
    errors: list[str] = []
    try:
        error_locs = page.locator(
            ".artdeco-inline-feedback--error, "
            "[data-test-form-element-error-message], "
            ".fb-form-element__error-field, "
            "[aria-live='assertive']"
        )
        count = await error_locs.count()
        for i in range(min(count, 5)):
            try:
                txt = (await error_locs.nth(i).text_content() or "").strip()
                if txt and txt not in errors:
                    errors.append(txt)
            except Exception:
                pass
    except Exception:
        pass
    return errors


async def _apply_linkedin(page: Page, job: dict, cfg: dict, resume_text: str, profile: dict) -> dict:
    job_id = job.get("scraped_id", job.get("id", "unknown"))
    try:
        await page.goto(job["url"], wait_until="domcontentloaded", timeout=30_000)

        # Session validity check
        if "linkedin.com/login" in page.url or "linkedin.com/authwall" in page.url:
            log.error("LinkedIn session expired — need to re-login (URL: %s)", page.url)
            return {"success": False, "manual": False, "note": "LinkedIn session expired — re-login required"}

        # Cookie consent overlay
        for cs in [
            "button[action-type='ACCEPT']",
            "button[data-tracking-control-name='cookie-accept-all-button']",
            ".artdeco-global-alert button",
        ]:
            try:
                el = await page.query_selector(cs)
                if el and await el.is_visible():
                    await el.click()
                    await page.wait_for_timeout(1000)
                    log.info("Dismissed cookie consent via: %s", cs)
                    break
            except Exception:
                pass

        if await _has_captcha(page):
            log.warning("DEBUG pre-manual: URL=%s", page.url)
            log.warning("DEBUG pre-manual: Page title=%s", await page.title())
            log.warning("DEBUG pre-manual: Reason=CAPTCHA detected")
            _emit("apply_step", {"url": job.get("url", ""), "step": "⚠️ Going to manual: CAPTCHA on LinkedIn"})
            return {"success": False, "manual": True, "note": "CAPTCHA on LinkedIn"}

        # ── Easy Apply detection ───────────────────────────────────────────────
        _EA_SELECTORS = [
            "button[aria-label*='Easy Apply']",
            "button[aria-label*='easy apply']",
            "button.jobs-apply-button--top-card",
            ".jobs-apply-button",
            ".jobs-s-apply button",
            "button.artdeco-button--primary",
        ]

        async def _sel_hit():
            for sel in _EA_SELECTORS:
                try:
                    el = await page.query_selector(sel)
                    if el and await el.is_visible():
                        return el
                except Exception:
                    pass
            return None

        async def _text_hit():
            try:
                for btn in await page.query_selector_all("button"):
                    text = (await btn.text_content() or "").strip()
                    if "easy apply" in text.lower() and await btn.is_visible():
                        return btn
            except Exception:
                pass
            return None

        async def _do_click(el, label: str) -> bool:
            try:
                await el.scroll_into_view_if_needed()
                await page.wait_for_timeout(500)
                await el.click()
                log.info("Easy Apply clicked: %s", label)
                return True
            except Exception:
                return False

        button_found = False
        apply_is_external = False
        apply_type = "Easy Apply"

        # Wait for page content to fully render (job card area)
        await page.wait_for_timeout(3000)
        await page.evaluate("window.scrollTo(0, 0)")
        await page.wait_for_timeout(1000)

        # ── Upfront apply-type detection ──────────────────────────────────────
        # Check BEFORE clicking so external-only jobs bypass the Easy Apply methods.
        _upfront_has_easy = False
        _upfront_ext_el   = None
        try:
            _ea_role = page.get_by_role("button", name=re.compile(r"easy apply", re.IGNORECASE))
            _upfront_has_easy = await _ea_role.count() > 0 and await _ea_role.first.is_visible()
        except Exception:
            pass
        if not _upfront_has_easy:
            try:
                for _xsel in [
                    "button.jobs-apply-button--top-card",
                    ".jobs-apply-button button",
                    ".jobs-s-apply button",
                    ".jobs-apply-button",
                ]:
                    _xel = await page.query_selector(_xsel)
                    if _xel and await _xel.is_visible():
                        _xtxt = (await _xel.text_content() or "").strip().lower()
                        if "easy apply" not in _xtxt and "apply" in _xtxt:
                            _upfront_ext_el = _xel
                            break
            except Exception:
                pass

        # External-only: route immediately, skip all Easy Apply methods
        if _upfront_ext_el and not _upfront_has_easy:
            log.info("External apply detected upfront — handling directly")
            _emit("apply_step", {"url": job.get("url", ""), "step": "Detected external apply (not Easy Apply)"})
            try:
                async with page.context.expect_page(timeout=10000) as _ntab_info:
                    await _upfront_ext_el.click()
                _new_page = await _ntab_info.value
                await _new_page.wait_for_load_state("domcontentloaded", timeout=15000)
                _ext_url = _new_page.url
                log.info("External apply opened new tab: %s", _ext_url)
                _ext_platform = detect_platform(_ext_url)
                _emit("apply_step", {"url": job.get("url", ""),
                                     "step": f"External apply — {_ext_platform}: {_ext_url[:80]}"})
                _ext_result = await _route_to_handler(_new_page, job, cfg, resume_text, profile, _ext_platform)
                try:
                    if not _new_page.is_closed():
                        await _new_page.close()
                except Exception:
                    pass
                return _ext_result
            except PWTimeout:
                # No new tab — check if current page navigated away from LinkedIn
                _cur_url = page.url
                if "linkedin.com" not in _cur_url:
                    _ext_platform = detect_platform(_cur_url)
                    _emit("apply_step", {"url": job.get("url", ""),
                                         "step": f"External apply (same tab) — {_ext_platform}"})
                    return await _route_to_handler(page, job, cfg, resume_text, profile, _ext_platform)
                # Still on LinkedIn — check for modal with an external link
                for _psel in [
                    "a:text('Apply on company website')",
                    "a:text('Continue')",
                    ".artdeco-modal a[href]",
                ]:
                    try:
                        _plink = await page.query_selector(_psel)
                        if _plink and await _plink.is_visible():
                            _phref = await _plink.get_attribute("href")
                            if _phref:
                                await page.goto(_phref, timeout=20000)
                                try:
                                    await page.wait_for_load_state("networkidle", timeout=15000)
                                except PWTimeout:
                                    pass
                                _ext_platform = detect_platform(page.url)
                                return await _route_to_handler(page, job, cfg, resume_text, profile, _ext_platform)
                    except Exception:
                        pass
                _emit("apply_step", {"url": job.get("url", ""),
                                     "step": "⚠️ Going to manual: External apply — no redirect detected"})
                return {"success": False, "manual": True, "note": "External apply — no redirect detected"}
            except Exception as _ext_e:
                log.warning("External apply upfront handler failed: %s", _ext_e)
                _emit("apply_step", {"url": job.get("url", ""),
                                     "step": f"⚠️ Going to manual: External apply error — {str(_ext_e)[:80]}"})
                return {"success": False, "manual": True, "note": f"External apply error — {str(_ext_e)[:80]}"}

        # Method 1: Playwright role-based locator (most reliable)
        try:
            import re as _re
            ea_loc = page.get_by_role("button", name=_re.compile(r"easy apply", _re.IGNORECASE))
            if await ea_loc.count():
                await ea_loc.first.scroll_into_view_if_needed()
                await page.wait_for_timeout(500)
                await ea_loc.first.click()
                log.info("Easy Apply clicked via get_by_role")
                button_found = True
        except Exception as _e:
            log.debug("get_by_role method failed: %s", _e)

        # Method 2: Playwright text locator
        if not button_found:
            try:
                ea_loc = page.locator("button").filter(has_text="Easy Apply")
                if await ea_loc.count():
                    await ea_loc.first.scroll_into_view_if_needed()
                    await page.wait_for_timeout(500)
                    await ea_loc.first.click()
                    log.info("Easy Apply clicked via locator filter has_text")
                    button_found = True
            except Exception as _e:
                log.debug("has_text filter method failed: %s", _e)

        # Method 3: CSS selector scan
        if not button_found:
            el = await _sel_hit()
            if el:
                button_found = await _do_click(el, "selector")

        # Method 4: full DOM text scan
        if not button_found:
            el = await _text_hit()
            if el:
                t = (await el.text_content() or "").strip()
                button_found = await _do_click(el, f"text='{t}'")

        # Method 5: scroll down 300px and retry all
        if not button_found:
            await page.evaluate("window.scrollTo(0, 300)")
            await page.wait_for_timeout(1500)
            try:
                ea_loc = page.get_by_role("button", name=_re.compile(r"easy apply", _re.IGNORECASE))
                if await ea_loc.count():
                    await ea_loc.first.scroll_into_view_if_needed()
                    await ea_loc.first.click()
                    log.info("Easy Apply clicked via get_by_role after scroll")
                    button_found = True
            except Exception:
                pass
            if not button_found:
                el = await _sel_hit() or await _text_hit()
                if el:
                    t = (await el.text_content() or "").strip()
                    button_found = await _do_click(el, f"after scroll: '{t}'")

        # Method 6: JS deep search — walks ALL elements including SVG containers
        if not button_found:
            clicked = await page.evaluate("""
                () => {
                    // Search buttons
                    const btns = Array.from(document.querySelectorAll('button'));
                    let btn = btns.find(b =>
                        (b.textContent || '').toLowerCase().includes('easy apply') ||
                        (b.getAttribute('aria-label') || '').toLowerCase().includes('easy apply')
                    );
                    // Also search anchors styled as buttons
                    if (!btn) {
                        const anchors = Array.from(document.querySelectorAll('a'));
                        btn = anchors.find(a =>
                            (a.textContent || '').toLowerCase().includes('easy apply') ||
                            (a.getAttribute('aria-label') || '').toLowerCase().includes('easy apply')
                        );
                    }
                    if (btn) { btn.click(); return btn.textContent.trim().substring(0, 50); }
                    return null;
                }
            """)
            if clicked:
                log.info("Easy Apply clicked via JS deep search (text='%s')", clicked)
                button_found = True
                await page.wait_for_timeout(2000)

        # External apply button fallback (standard "Apply" instead of "Easy Apply")
        if not button_found:
            for ext_sel in [
                "button.jobs-apply-button--top-card",
                ".jobs-apply-button",
                "button:text('Apply')",
                "button[aria-label*='Apply']",
            ]:
                try:
                    ext_el = await page.query_selector(ext_sel)
                    if not ext_el or not await ext_el.is_visible():
                        continue
                    ext_text = (await ext_el.text_content() or "").strip()
                    if "easy apply" in ext_text.lower():
                        continue
                    log.info("External Apply button (text='%s'), clicking...", ext_text)
                    try:
                        async with page.expect_navigation(timeout=15000):
                            await ext_el.click()
                    except PWTimeout:
                        pass  # no navigation — popup/modal may have appeared instead
                    apply_is_external = True
                    apply_type = "External"
                    button_found = True
                    break
                except Exception as _ee:
                    log.debug("External apply selector %s failed: %s", ext_sel, _ee)

        # Handle external apply redirect or popup
        if apply_is_external:
            new_url = page.url
            log.info("After Apply click — URL: %s", new_url)
            if "linkedin.com" not in new_url:
                new_platform = detect_platform(new_url)
                log.info("Redirected to %s platform: %s", new_platform, new_url)
                return await _route_to_handler(page, job, cfg, resume_text, profile, new_platform)
            # Still on LinkedIn — check for popup/modal with external link
            for psel in [
                "a:text('Apply on company website')",
                "a:text('Continue')",
                ".artdeco-modal a[href]",
                "a[href*='apply']",
            ]:
                try:
                    link = await page.query_selector(psel)
                    if link and await link.is_visible():
                        href = await link.get_attribute("href")
                        log.info("External apply popup link: %s", href)
                        if href:
                            await page.goto(href, timeout=20000)
                            try:
                                await page.wait_for_load_state("networkidle", timeout=15000)
                            except PWTimeout:
                                pass
                            new_platform = detect_platform(page.url)
                            return await _route_to_handler(page, job, cfg, resume_text, profile, new_platform)
                except Exception:
                    pass
            # Popup found but no actionable link — fall through to debug screenshot
            button_found = False

        # Method 5: debug screenshot + give up
        if not button_found:
            debug_path = Path("uploads") / f"debug_apply_{job_id}.png"
            try:
                debug_path.write_bytes(await page.screenshot(full_page=True))
                log.warning("DEBUG: Screenshot saved to %s", debug_path)
            except Exception as se:
                log.warning("DEBUG: Screenshot failed: %s", se)
            try:
                btns = [(await b.text_content() or "").strip()
                        for b in await page.query_selector_all("button")]
                log.warning("DEBUG: Buttons on page: %s", [t for t in btns if t])
            except Exception:
                pass
            log.warning("DEBUG pre-manual: URL=%s", page.url)
            log.warning("DEBUG pre-manual: Page title=%s", await page.title())
            log.warning("DEBUG pre-manual: Reason=No Apply button found (Easy Apply or external)")
            _emit("apply_step", {"url": job.get("url", ""), "step": "⚠️ Going to manual: No Apply button found — debug screenshot saved"})
            return {"success": False, "manual": True, "note": "No Apply button found — debug screenshot saved"}

        # ── Easy Apply form wizard ────────────────────────────────────────────
        _emit("apply_step", {"url": job["url"], "step": "Opening Easy Apply form"})
        await _delay()

        # Wait for the Easy Apply modal to fully render before filling
        try:
            await page.wait_for_selector(
                ".jobs-easy-apply-modal, .jobs-easy-apply-content, "
                "[data-test-modal], .artdeco-modal__content",
                timeout=10000, state="visible"
            )
            await page.wait_for_timeout(800)  # extra settle time for React render
        except Exception:
            await page.wait_for_timeout(2000)

        # Wait for first interactive form field
        try:
            await page.wait_for_selector(
                "input[type='text']:not([disabled]), input[type='tel']:not([disabled]), "
                "input[type='email']:not([disabled]), select:not([disabled])",
                timeout=8000, state="visible",
            )
            await page.wait_for_timeout(500)
        except Exception:
            await page.wait_for_timeout(2000)

        # Handle email dropdown — AFTER modal renders (LinkedIn shows <select> when multiple emails)
        await _handle_email_dropdown(page, profile, job["url"])

        await _fill_profile_fields(page, profile)

        _prev_page_labels: list[str] = []
        _stuck_count = 0
        for step_n in range(12):
            await _maybe_attach_support_docs(page)
            # Try resume picker on every page — it only appears on the resume step
            await _select_or_upload_resume(page, cfg)

            # Log only non-trivial visible labels (skip "Select language" boilerplate)
            _BORING_LABELS = {"select language", "select an option", "upload resume",
                              "upload a resume", "change resume"}
            visible_labels = []
            try:
                for inp in await page.query_selector_all(
                    ".jobs-easy-apply-form-section__group label, "
                    ".fb-dash-form-element label, fieldset legend"
                ):
                    if await inp.is_visible():
                        t = (await inp.text_content() or "").strip()
                        if t and len(t) < 80 and t.lower() not in _BORING_LABELS:
                            visible_labels.append(t)
                if visible_labels:
                    _emit("apply_step", {"url": job["url"],
                        "step": f"Page {step_n + 1} questions: {', '.join(dict.fromkeys(visible_labels[:8]))}"}
                    )
            except Exception:
                pass

            # Detect stuck page — same labels two iterations in a row means nothing changed
            if visible_labels and visible_labels == _prev_page_labels:
                _stuck_count += 1
                if _stuck_count >= 2:
                    log.warning("Wizard stuck on same page (step %d) — giving up", step_n + 1)
                    break
            else:
                _stuck_count = 0
            _prev_page_labels = visible_labels

            await _answer_visible_questions(page, resume_text, profile, job.get("description", ""))

            submit = page.locator(
                "button:has-text('Submit application'), "
                "button:has-text('Submit'), "
                "button[aria-label*='Submit']"
            ).filter(has_text=re.compile(r"submit", re.I))
            nxt = page.locator(
                "button:has-text('Next'), button:has-text('Continue'), "
                "button:has-text('Review'), button:has-text('Weiter'), "
                "button:has-text('Fortfahren')"
            ).filter(has_text=re.compile(r"next|continue|review|weiter|fortfahren", re.I))

            if await submit.count():
                _emit("apply_step", {"url": job["url"], "step": "Submitting application…"})
                await submit.first.click()
                await _delay()
                return {"success": True, "manual": False, "note": "", "apply_type": apply_type}
            elif await nxt.count():
                nxt_text = (await nxt.first.text_content() or "Next").strip()
                _emit("apply_step", {"url": job["url"], "step": f"Page {step_n + 1} → clicking '{nxt_text}'"})
                await nxt.first.click()
                await _delay()
                _step_errs = await _get_page_errors(page)
                if _step_errs:
                    for _se in _step_errs:
                        _emit("apply_step", {"url": job["url"], "step": f"  ⚠️ Validation: {_se[:100]}"})
                # After clicking Next/Review LinkedIn may auto-submit or close the modal.
                try:
                    await _handle_email_dropdown(page, profile, job["url"])
                    await _fill_profile_fields(page, profile)
                except Exception as _nav_err:
                    _nav_msg = str(_nav_err).lower()
                    if any(x in _nav_msg for x in ["closed", "target page", "context", "destroyed"]):
                        _emit("apply_step", {"url": job["url"],
                            "step": "✓ Page closed after navigation — application submitted"})
                        return {"success": True, "manual": False,
                                "note": "Auto-submitted on Review", "apply_type": apply_type}
                    raise
            else:
                try:
                    btns = [(await b.text_content() or "").strip()
                            for b in await page.query_selector_all("button")]
                    log.warning("Wizard stuck at step %d — buttons: %s", step_n + 1, [t for t in btns if t])
                    debug_path = Path("uploads") / f"debug_wizard_{job_id}_step{step_n}.png"
                    debug_path.write_bytes(await page.screenshot(full_page=True))
                    log.warning("Wizard debug screenshot: %s", debug_path)
                except Exception:
                    pass
                break

        log.warning("DEBUG pre-manual: URL=%s", page.url)
        log.warning("DEBUG pre-manual: Page title=%s", await page.title())
        log.warning("DEBUG pre-manual: Reason=Could not complete Easy Apply form (step %d)", step_n + 1)
        _page_errs = await _get_page_errors(page)
        if _page_errs:
            for _pe in _page_errs:
                _emit("apply_step", {"url": job.get("url", ""), "step": f"  ⚠️ Page error: {_pe[:100]}"})
        try:
            _vis_btns = [(await b.text_content() or "").strip()
                         for b in await page.query_selector_all("button")]
            _vis_btns = [t for t in _vis_btns if t]
            log.warning("DEBUG pre-manual: Step=%d, Visible buttons=%s", step_n + 1, _vis_btns)
            _emit("apply_step", {"url": job.get("url", ""),
                                 "step": f"  ⚠️ Stuck at step {step_n + 1}, buttons: {', '.join(_vis_btns[:6]) or 'none'}"})
        except Exception:
            pass
        _emit("apply_step", {"url": job.get("url", ""), "step": "⚠️ Going to manual: Could not complete LinkedIn form"})
        return {"success": False, "manual": True, "note": "Could not complete LinkedIn form"}

    except PWTimeout:
        return {"success": False, "manual": False, "note": "Timeout"}
    except Exception as exc:
        _exc_str = str(exc).lower()
        _exc_type = type(exc).__name__.lower()
        if any(x in _exc_str or x in _exc_type for x in
               ["targetclosed", "target page", "context destroyed", "target closed"]):
            _emit("apply_step", {"url": job.get("url", ""), "step":
                "✓ Page closed after submit — application likely submitted"})
            return {"success": True, "manual": False,
                    "note": "Submitted (page closed)", "apply_type": "Easy Apply"}
        log.error("_apply_linkedin error: %s\n%s", exc, traceback.format_exc())
        return {"success": False, "manual": False, "note": str(exc)}


async def _get_form_snapshot(page: Page) -> str:
    """Extract all visible form fields from the current page for Claude."""
    try:
        snapshot = await page.evaluate("""() => {
            const fields = [];
            const inputs = document.querySelectorAll(
                'input:not([type=hidden]):not([type=submit]):not([type=button]),' +
                'textarea, select'
            );
            inputs.forEach((el, idx) => {
                if (!el.offsetParent) return;
                const label = document.querySelector(
                    `label[for="${el.id}"]`
                )?.innerText ||
                el.getAttribute('aria-label') ||
                el.getAttribute('placeholder') ||
                el.name || '';
                const options = el.tagName === 'SELECT'
                    ? Array.from(el.options).map(o => o.text).join(', ')
                    : '';
                fields.push({
                    idx, tag: el.tagName.toLowerCase(),
                    type: el.type || '',
                    label: label.trim(),
                    value: el.value || '',
                    options,
                    required: el.required
                });
            });
            return JSON.stringify(fields);
        }""")
        return snapshot or ""
    except Exception:
        return ""


async def _ai_decide_form_actions(
    page_snapshot: str, profile: dict,
    resume_text: str, job_desc: str
) -> list[dict]:
    """Ask Claude to analyse the form and return a list of fill actions."""
    try:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            return []
        client = anthropic.Anthropic(api_key=api_key)

        system = (
            "You are filling a job application form on behalf of the candidate. "
            "Analyse the form fields and return a JSON array of actions to take. "
            "Each action is one of:\n"
            '  {"action":"fill", "idx":N, "value":"..."}\n'
            '  {"action":"select", "idx":N, "value":"..."}\n'
            '  {"action":"check", "idx":N}\n'
            "Rules:\n"
            "- For number fields return ONLY a digit (e.g. 5). Never a sentence.\n"
            "- Never say 'no experience' — use 1 for unknown tech, brief positive for text.\n"
            "- Skip fields that are already filled with a real value.\n"
            "- Skip file upload inputs (type=file).\n"
            "- Return ONLY the JSON array, no explanation.\n"
            f"\nCandidate profile:\n{_profile_summary(profile)}\n"
            f"\nJob description (first 400 chars):\n{job_desc[:400]}\n"
            f"\nResume (first 800 chars):\n{resume_text[:800]}"
        )

        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=800,
            system=system,
            messages=[{"role": "user", "content":
                f"Form fields (JSON):\n{page_snapshot[:3000]}\n\n"
                "Return the JSON array of actions to fill this form."}],
        )
        raw = resp.content[0].text.strip()
        start = raw.find("[")
        end   = raw.rfind("]") + 1
        if start >= 0 and end > start:
            actions = json.loads(raw[start:end])
            return actions if isinstance(actions, list) else []
        return []
    except Exception as e:
        log.warning("AI form decision failed: %s", e)
        return []


async def _ai_execute_action(page: Page, action: dict) -> None:
    """Execute a single form action decided by Claude."""
    idx   = action.get("idx", 0)
    kind  = action.get("action", "fill")
    value = str(action.get("value", ""))

    elements = await page.query_selector_all(
        'input:not([type=hidden]):not([type=submit]):not([type=button]),'
        'textarea, select'
    )
    visible = []
    for el in elements:
        if await el.is_visible():
            visible.append(el)

    if idx >= len(visible):
        return
    el = visible[idx]

    tag = (await el.get_property("tagName")).json_value().lower()
    typ = ((await el.get_attribute("type")) or "").lower()

    if kind == "select" or tag == "select":
        try:
            await el.select_option(label=value)
        except Exception:
            await el.select_option(value=value)
        await _handle_autocomplete(page, el, value)
    elif kind == "check" or typ in ("checkbox", "radio"):
        await el.click()
    else:
        if typ == "number":
            value = re.sub(r"[^\d]", "", value.split(".")[0]) or "1"
        await el.triple_click()
        await el.fill(value)
        await _handle_autocomplete(page, el, value)

    label = (await el.get_attribute("aria-label") or
             await el.get_attribute("placeholder") or
             await el.get_attribute("name") or "field")
    _emit("apply_step", {"url": "", "step": f"  ✎ {label[:40]} = {value[:40]}"})


async def _apply_external_ai(
    page: Page, job: dict, cfg: dict,
    resume_text: str, profile: dict
) -> dict:
    """AI-powered external apply agent. Uses Claude to read any ATS form and
    decide what to fill — no platform-specific code needed."""
    url = job.get("url", "")
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(2)

        if await _has_captcha(page):
            return {"success": False, "manual": True, "note": "CAPTCHA"}

        async def _click_apply_button(pg) -> bool:
            # Strategy 1 — get_by_role with regex (most reliable)
            try:
                btn = pg.get_by_role("button", name=re.compile(r"^apply", re.I))
                if await btn.count() and await btn.first.is_visible():
                    txt = (await btn.first.text_content() or "").strip()
                    _emit("apply_step", {"url": url, "step": f"  ✓ Apply button (role): '{txt}'"})
                    await btn.first.click()
                    return True
            except Exception:
                pass

            # Strategy 2 — CSS class selectors (ATS-specific known classes)
            for sel in [
                "button.jobs-apply-button--top-card",
                ".jobs-apply-button button",
                ".jobs-s-apply button",
                "a.apply-button",
                "button[data-qa='btn-apply']",
                "a[data-qa='btn-apply']",
                "#apply-button",
                ".apply-btn",
            ]:
                try:
                    el = await pg.query_selector(sel)
                    if el and await el.is_visible():
                        txt = (await el.text_content() or "").strip()
                        _emit("apply_step", {"url": url, "step": f"  ✓ Apply button (css): '{txt}'"})
                        await el.click()
                        return True
                except Exception:
                    pass

            # Strategy 3 — locator filter with text regex
            try:
                btn = pg.locator("button, a").filter(
                    has_text=re.compile(
                        r"^(apply|apply now|apply for this job|"
                        r"jetzt bewerben|bewerben|bewerbung)$", re.I)
                )
                if await btn.count() and await btn.first.is_visible():
                    txt = (await btn.first.text_content() or "").strip()
                    _emit("apply_step", {"url": url, "step": f"  ✓ Apply button (filter): '{txt}'"})
                    await btn.first.click()
                    return True
            except Exception:
                pass

            # Strategy 4 — JS deep search by text content
            try:
                clicked = await pg.evaluate("""() => {
                    const texts = ['apply now','apply for this job','apply',
                                   'jetzt bewerben','bewerben'];
                    for (const el of document.querySelectorAll('button,a')) {
                        const t = el.innerText?.trim().toLowerCase();
                        if (texts.includes(t) && el.offsetParent !== null) {
                            el.click();
                            return el.innerText.trim();
                        }
                    }
                    return null;
                }""")
                if clicked:
                    _emit("apply_step", {"url": url, "step": f"  ✓ Apply button (JS): '{clicked}'"})
                    return True
            except Exception:
                pass

            _emit("apply_step", {"url": url,
                "step": "  ⚠ No Apply button found — attempting form fill directly"})
            return False

        clicked = await _click_apply_button(page)
        if clicked:
            await asyncio.sleep(2)
            if len(page.context.pages) > 1:
                page = page.context.pages[-1]
                await page.wait_for_load_state("domcontentloaded", timeout=15000)

        _emit("apply_step", {"url": url, "step": "🤖 AI agent taking over external form"})

        await _upload_resume(page, cfg)

        for step in range(10):
            page_text = await _get_form_snapshot(page)
            if not page_text.strip():
                break

            actions = await _ai_decide_form_actions(
                page_text, profile, resume_text, job.get("description", "")
            )

            if not actions:
                break

            for action in actions:
                try:
                    await _ai_execute_action(page, action)
                    await asyncio.sleep(0.3)
                except Exception as e:
                    log.debug("Action failed: %s — %s", action, e)

            await asyncio.sleep(1)

            await _upload_resume(page, cfg)

            submit = page.locator(
                "button[type='submit'], input[type='submit'], "
                "button:has-text('Submit'), button:has-text('Send'), "
                "button:has-text('Senden'), button:has-text('Apply'), "
                "button:has-text('Complete application')"
            ).filter(has_text=re.compile(
                r"submit|send|apply|complete|senden|absenden", re.I))

            if await submit.count() and await submit.first.is_visible():
                _emit("apply_step", {"url": url, "step": f"  Step {step+1}: submitting"})
                await submit.first.click()
                await asyncio.sleep(3)
                await _verify_submission(page)
                return {"success": True, "manual": False,
                        "note": "", "apply_type": "External (AI)"}

            nxt = page.locator(
                "button:has-text('Next'), button:has-text('Continue'), "
                "button:has-text('Weiter'), button:has-text('Fortfahren')"
            )
            if await nxt.count() and await nxt.first.is_visible():
                _emit("apply_step", {"url": url, "step": f"  Step {step+1}: moving to next page"})
                await nxt.first.click()
                await asyncio.sleep(2)
            else:
                break

        return {"success": False, "manual": True, "note": "AI agent could not complete form"}

    except PWTimeout:
        return {"success": False, "manual": False, "note": "Timeout"}
    except Exception as exc:
        return {"success": False, "manual": True, "note": str(exc)}


async def _apply_workday(page: Page, job: dict, cfg: dict, resume_text: str, profile: dict) -> dict:
    _emit("apply_step", {"url": job.get("url", ""), "step": "⚠️ Going to manual: Workday requires manual apply (complex forms)"})
    return {"success": False, "manual": True, "note": "Workday requires manual apply (complex forms)"}


async def _apply_taleo(page: Page, job: dict, cfg: dict, resume_text: str, profile: dict) -> dict:
    _emit("apply_step", {"url": job.get("url", ""), "step": "⚠️ Going to manual: Taleo requires manual apply (login-gated)"})
    return {"success": False, "manual": True, "note": "Taleo requires manual apply (login-gated)"}


async def _apply_icims(page: Page, job: dict, cfg: dict, resume_text: str, profile: dict) -> dict:
    _emit("apply_step", {"url": job.get("url", ""), "step": "⚠️ Going to manual: iCIMS requires manual apply (login-gated)"})
    return {"success": False, "manual": True, "note": "iCIMS requires manual apply (login-gated)"}


async def _apply_successfactors(page: Page, job: dict, cfg: dict, resume_text: str, profile: dict) -> dict:
    _emit("apply_step", {"url": job.get("url", ""), "step": "⚠️ Going to manual: SuccessFactors requires manual apply"})
    return {"success": False, "manual": True, "note": "SuccessFactors requires manual apply"}


async def _apply_xing(page: Page, job: dict, cfg: dict, resume_text: str, profile: dict) -> dict:
    _emit("apply_step", {"url": job.get("url", ""), "step": "⚠️ Going to manual: XING requires manual apply"})
    return {"success": False, "manual": True, "note": "XING requires manual apply"}


async def _apply_indeed(page: Page, job: dict, cfg: dict, resume_text: str, profile: dict) -> dict:
    _emit("apply_step", {"url": job.get("url", ""), "step": "⚠️ Going to manual: Indeed requires manual apply (login-gated)"})
    return {"success": False, "manual": True, "note": "Indeed requires manual apply (login-gated)"}


async def _apply_unknown(page: Page, job: dict, cfg: dict, resume_text: str, profile: dict) -> dict:
    _emit("apply_step", {"url": job.get("url", ""), "step": "⚠️ Going to manual: Unknown platform — manual apply required"})
    return {"success": False, "manual": True, "note": "Unknown platform — manual apply required"}


_PLATFORM_HANDLERS = {
    "linkedin":       _apply_linkedin,
    "greenhouse":     _apply_external_ai,
    "lever":          _apply_external_ai,
    "smartrecruiters":_apply_external_ai,
    "ashby":          _apply_external_ai,
    "workable":       _apply_external_ai,
    "personio":       _apply_external_ai,
    "recruitee":      _apply_external_ai,
    "bamboohr":       _apply_external_ai,
    "teamtailor":     _apply_external_ai,
    "jazzhr":         _apply_external_ai,
    "jobvite":        _apply_external_ai,
    "stepstone":      _apply_external_ai,
    "workday":        _apply_workday,
    "taleo":          _apply_taleo,
    "icims":          _apply_icims,
    "successfactors": _apply_successfactors,
    "xing":           _apply_xing,
    "indeed":         _apply_indeed,
    "unknown":        _apply_unknown,
}


# ── Custom question scanner ────────────────────────────────────────────────────

def is_numeric_question(label: str, inp_type: str) -> bool:
    """True when the input expects a numeric answer regardless of DOM type attribute."""
    if inp_type == "number":
        return True
    if inp_type == "text" and re.search(
        r"how many|years of|years with|number of|anzahl|wie viele",
        label, re.I
    ):
        return True
    return False


async def _answer_visible_questions(page: Page, resume_text: str, profile: dict, job_desc: str) -> None:
    """Scan for unanswered form questions and fill them using answer_custom_question."""
    try:
        # Text inputs not yet filled
        inputs = page.locator(
            "input[type='text']:not([aria-label*='search' i]), "
            "input[type='number']"
        )
        count = await inputs.count()
        for i in range(min(count, 15)):
            try:
                inp = inputs.nth(i)
                if not await inp.is_visible():
                    continue
                label_text = await _get_label(page, inp)
                if not label_text:
                    continue
                existing = await inp.input_value()
                if existing.strip():
                    continue
                inp_type = (await inp.get_attribute("type") or "text").lower()
                effective_type = "number" if is_numeric_question(label_text, inp_type) else inp_type
                _emit("apply_step", {"url": "", "step":
                    f"  [field] '{label_text[:40]}' type={inp_type} effective={effective_type}"})
                answer = answer_custom_question(label_text, effective_type, [], resume_text, profile, job_desc)
                if effective_type == "number":
                    raw_answer = answer
                    answer = re.sub(r"[^\d]", "", answer.split(".")[0]) or "1"
                    _emit("apply_step", {"url": "", "step":
                        f"  [number] '{label_text[:40]}' raw='{raw_answer[:30]}' → '{answer}'"})
                if answer:
                    if effective_type == "number":
                        try:
                            await inp.triple_click()
                            await inp.fill(answer)
                        except Exception:
                            await _reliable_fill(page, inp, answer)
                    else:
                        await _reliable_fill(page, inp, answer)
                        await _handle_autocomplete(page, inp, answer)
                    _emit("apply_answer", {"label": label_text[:60], "answer": answer[:80]})
            except Exception:
                pass

        # Textareas not yet filled (custom open-ended questions)
        textareas = page.locator("textarea")
        ta_count = await textareas.count()
        for i in range(min(ta_count, 10)):
            try:
                ta = textareas.nth(i)
                if not await ta.is_visible():
                    continue
                label_text = await _get_label(page, ta)
                if not label_text:
                    continue
                existing = await ta.input_value()
                if existing.strip():
                    continue
                answer = answer_custom_question(label_text, "textarea", [], resume_text, profile, job_desc)
                if answer:
                    await _reliable_fill(page, ta, answer)
                    _emit("apply_answer", {"label": label_text[:60], "answer": answer[:120]})
            except Exception:
                pass

        # Radio button groups — LinkedIn wraps these in <fieldset><legend>question</legend>
        # Each group must be answered before the wizard allows Next/Review/Submit.
        try:
            fieldsets = page.locator("fieldset")
            fs_count = await fieldsets.count()
            for i in range(min(fs_count, 10)):
                try:
                    fs = fieldsets.nth(i)
                    if not await fs.is_visible():
                        continue
                    radios = fs.locator("input[type='radio']")  # scoped to THIS fieldset
                    r_count = await radios.count()
                    if not r_count:
                        continue
                    # Check if any radio in THIS group is already checked
                    already_answered = False
                    for j in range(r_count):
                        try:
                            if await radios.nth(j).is_checked():
                                already_answered = True
                                break
                        except Exception:
                            pass
                    # Get question from legend
                    legend = fs.locator("legend")
                    question = (await legend.first.text_content() or "").strip() if await legend.count() else ""
                    # Collect option values/labels
                    option_values = []
                    for j in range(r_count):
                        try:
                            val = await radios.nth(j).get_attribute("value") or ""
                            if val:
                                option_values.append(val)
                        except Exception:
                            pass
                    if not option_values:
                        option_values = ["Yes", "No"]
                    _emit("apply_step", {"url": "", "step":
                        f"  [radio found] '{(question or 'radio')[:60]}' "
                        f"options={option_values} already_answered={already_answered}"})
                    if already_answered:
                        continue
                    answer = answer_custom_question(
                        question or "Yes/No", "radio",
                        option_values, resume_text, profile, job_desc
                    )
                    # Click the matching radio — label click first, force-click fallback
                    clicked = False
                    for j in range(r_count):
                        try:
                            val = await radios.nth(j).get_attribute("value") or ""
                            if answer.lower() in val.lower() or val.lower() in answer.lower():
                                radio_id = await radios.nth(j).get_attribute("id")
                                if radio_id:
                                    lbl = page.locator(f"label[for='{radio_id}']")
                                    if await lbl.count():
                                        await lbl.first.click()
                                        await asyncio.sleep(0.3)
                                        checked = await radios.nth(j).is_checked()
                                        if not checked:
                                            await radios.nth(j).click(force=True)
                                            await asyncio.sleep(0.3)
                                            checked = await radios.nth(j).is_checked()
                                        _emit("apply_step", {"url": "", "step":
                                            f"  [radio click] '{val}' checked={checked}"})
                                        clicked = True
                                        break
                                # No label found — go direct with force
                                await radios.nth(j).click(force=True)
                                await asyncio.sleep(0.3)
                                clicked = True
                                break
                        except Exception:
                            pass
                    if clicked:
                        _emit("apply_answer", {"label": (question or "radio")[:60], "answer": answer[:80]})
                    else:
                        log.warning("Radio group '%s' — no option matched answer '%s'",
                                    (question or "?")[:60], answer)
                except Exception as _fs_err:
                    log.warning("Fieldset %d failed: %s", i, _fs_err)
                    continue
        except Exception:
            pass

        # Retry any fieldsets still flagged as invalid by LinkedIn
        try:
            for retry_sel in ["fieldset[aria-invalid='true']", "fieldset.has-error"]:
                inv_fsets = page.locator(retry_sel)
                inv_count = await inv_fsets.count()
                if not inv_count:
                    continue
                _emit("apply_step", {"url": "", "step":
                    f"  [radio retry] {inv_count} invalid fieldset(s) still detected"})
                for k in range(inv_count):
                    try:
                        inv_fs = inv_fsets.nth(k)
                        inv_radios = inv_fs.locator("input[type='radio']")
                        inv_r_count = await inv_radios.count()
                        if not inv_r_count:
                            continue
                        already = False
                        for m in range(inv_r_count):
                            try:
                                if await inv_radios.nth(m).is_checked():
                                    already = True
                                    break
                            except Exception:
                                pass
                        if already:
                            continue
                        radio_id = await inv_radios.first.get_attribute("id")
                        if radio_id:
                            lbl = page.locator(f"label[for='{radio_id}']")
                            if await lbl.count():
                                await lbl.first.click()
                                await asyncio.sleep(0.3)
                                continue
                        await inv_radios.first.click(force=True)
                        await asyncio.sleep(0.3)
                    except Exception:
                        pass
                break
        except Exception:
            pass

        # Selects not yet chosen
        _PLACEHOLDER_OPTION_TEXTS = {
            "", "select", "select an option", "select an option...", "select one",
            "---", "please select", "choose one", "choose an option",
            "bitte wählen", "auswählen", "keine angabe",
        }
        selects = page.locator("select")
        sel_count = await selects.count()
        for i in range(min(sel_count, 10)):
            try:
                sel = selects.nth(i)
                if not await sel.is_visible():
                    continue
                label_text = await _get_label(page, sel)
                options_raw = await sel.locator("option").all_text_contents()
                options = [o.strip() for o in options_raw if o.strip() and o.strip().lower() not in _PLACEHOLDER_OPTION_TEXTS]
                if not options:
                    continue
                # Check whether a real (non-placeholder) option is already chosen.
                # We must inspect the selected option TEXT, not just the value attribute,
                # because LinkedIn often gives placeholder options a non-empty value string
                # which would make `input_value()` truthy even though nothing is chosen.
                try:
                    selected_text = (await sel.evaluate(
                        "el => (el.options[el.selectedIndex] || {}).text || ''"
                    )).strip().lower()
                except Exception:
                    selected_text = ""
                if selected_text and selected_text not in _PLACEHOLDER_OPTION_TEXTS:
                    continue  # already has a real selection
                answer = answer_custom_question(label_text or "", "select", options, resume_text, profile, job_desc)
                if answer:
                    _sel_ok = False
                    try:
                        await sel.select_option(label=answer)
                        _sel_ok = True
                    except Exception:
                        pass
                    if not _sel_ok:
                        try:
                            await sel.select_option(value=answer)
                            _sel_ok = True
                        except Exception:
                            pass
                    if not _sel_ok:
                        for _opt in options:
                            if answer.lower() in _opt.lower() or _opt.lower() in answer.lower():
                                try:
                                    await sel.select_option(label=_opt)
                                    _sel_ok = True
                                    break
                                except Exception:
                                    pass
                    if _sel_ok:
                        try:
                            await sel.dispatchEvent("change")
                        except Exception:
                            pass
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

    # Clear per-session answer cache so stale answers from a previous run don't bleed in
    _answer_cache.clear()
    log.debug("Answer cache cleared for new apply session.")

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
        browser = await pw.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-infobars",
                "--start-maximized",
            ],
        )
        try:
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
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins',   {get: () => [1, 2, 3]});
                Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
                window.chrome = {runtime: {}};
            """)

            # Load LinkedIn session cookies before any navigation
            if _SESSION_FILE.exists():
                session_data = json.loads(_SESSION_FILE.read_text())
                cookies = session_data if isinstance(session_data, list) \
                          else session_data.get("cookies", [])
                if cookies:
                    await context.add_cookies(cookies)
                    log.info("LinkedIn session loaded (%d cookies)", len(cookies))
                else:
                    log.warning("Session file empty — may not be logged in")
            else:
                log.warning("No LinkedIn session file found")

            page = await context.new_page()

            # Visit feed first to activate session properly
            await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=20_000)
            await page.wait_for_timeout(2000)
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
            log.info("Session verified — ready to apply")

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
                company   = job.get("company", "")

                # Detect platform
                platform = detect_platform(url)
                _emit("platform_detected", {"url": url, "platform": platform})
                log.info("Apply [%s] %s @ %s", platform, title, company)

                MAX_RETRIES = 2
                handler = _PLATFORM_HANDLERS.get(platform, _apply_unknown)
                result = None
                for attempt in range(1, MAX_RETRIES + 1):
                    if attempt > 1:
                        _emit("apply_step", {"url": url, "step": f"↺ Retry attempt {attempt}/{MAX_RETRIES}..."})
                        await asyncio.sleep(3)
                        try:
                            await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
                            await asyncio.sleep(2)
                        except Exception:
                            pass
                    result = await handler(page, job, cfg, resume_text, prof)
                    if result["success"]:
                        break
                    if result["manual"] and result.get("note") in ("CAPTCHA", "LinkedIn session expired"):
                        break  # no point retrying these
                    if result["manual"]:
                        if attempt < MAX_RETRIES:
                            _emit("apply_step", {"url": url, "step": f"  Attempt {attempt} failed — will retry"})
                            continue
                        else:
                            _emit("apply_step", {"url": url, "step": f"  All {MAX_RETRIES} attempts failed — sending to manual queue"})
                            break

                _emit("apply_result", {
                    "url":        url,
                    "title":      title,
                    "company":    company,
                    "platform":   platform,
                    "apply_type": result.get("apply_type", "Unknown"),
                    "success":    result["success"],
                    "manual":     result["manual"],
                    "note":       result.get("note", ""),
                })

                is_debug = job.get("id") == -1
                if result["success"]:
                    success_count += 1
                    applied_count += 1
                    if not is_debug:
                        _db.log_application(job, status="Applied \u2713", applied_by="Agent")
                    else:
                        log.info("DEBUG job \u2014 skipping DB write (Applied \u2713)")
                    log.info("Applied: %s @ %s", title, company)
                else:
                    # ALL non-success outcomes (manual=True or outright failures) go to manual queue
                    manual_count += 1
                    note = result.get("note", "Unknown failure")
                    if not is_debug:
                        _db.log_manual_apply(url, title, company, platform, note,
                                             session_id=str(session_id))
                        _emit("apply_step", {"url": url, "step": f"  \u26a0 Added to manual queue: {note[:80]}"})
                    else:
                        log.info("DEBUG job \u2014 skipping DB write (manual/failed: %s)", note)
                    log.info("Manual queue: %s @ %s \u2014 %s", title, company, note)

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
    log.info("Apply session done \u2014 %d applied, %d manual, %d failed",
             success_count, manual_count, failed_count)


def run(jobs: list[dict], cfg: dict | None = None,
        stop_flag: threading.Event | None = None) -> None:
    """Synchronous entry point called from dashboard worker thread."""
    if cfg is None:
        cfg = load_config()
    if stop_flag is None:
        stop_flag = threading.Event()
    try:
        asyncio.run(_run_apply(jobs, cfg, stop_flag))
    except Exception as exc:
        log.error("_run_apply crashed: %s", exc, exc_info=True)
        _emit("apply_step", {"url": "", "step": f"❌ Crash: {exc}"})
        _emit("session_done", {"success": 0, "manual": 0, "failed": len(jobs)})


# \u2500\u2500 Legacy single-job wrapper (used by main.py) \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

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
