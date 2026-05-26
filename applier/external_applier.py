"""
external_applier.py
Handle external ATS redirects after clicking Apply on a LinkedIn job page.

Public API:
  detect_platform(url, page_source) -> str
  follow_external_apply(page, job, profile, resume_text, cfg) -> dict
"""
import asyncio
import json
import logging
import os
import re

import anthropic
from playwright.async_api import Page, TimeoutError as PWTimeout

from applier.events import _emit
from applier.linkedin_applier import (
    _profile_summary,
    _resume_path,
    _get_label,
    _reliable_fill,
    _handle_autocomplete,
    is_numeric_question,
    answer_custom_question,
)

try:
    from browser_use import Agent as _BUAgent, Browser as _BUBrowser, BrowserConfig as _BUBrowserConfig
    from langchain_anthropic import ChatAnthropic as _BUChatAnthropic
    _BROWSER_USE_OK = True
except ImportError:
    _BUAgent = _BUBrowser = _BUBrowserConfig = _BUChatAnthropic = None  # type: ignore
    _BROWSER_USE_OK = False

log = logging.getLogger(__name__)

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


def _detect_platform_by_url(url: str) -> str:
    url_lower = (url or "").lower()
    _URL_PATTERNS = {
        "greenhouse":     ["boards.greenhouse.io", "greenhouse.io/applications"],
        "lever":          ["jobs.lever.co", "lever.co/"],
        "ashby":          ["jobs.ashbyhq.com", "ashbyhq.com"],
        "workday":        ["myworkdayjobs.com", "workday.com/en-us/site"],
        "rippling":       ["rippling.com/jobs", "app.rippling.com"],
        "smartrecruiters":["jobs.smartrecruiters.com", "smartrecruiters.com/apply"],
        "icims":          [".icims.com/jobs/", "icims.com/"],
        "taleo":          [".taleo.net/", "oracle.com/taleo"],
        "bamboohr":       ["bamboohr.com/careers", "bamboohr.com/jobs"],
        "jobvite":        ["jobs.jobvite.com", "hire.jobvite.com"],
        "workable":       ["apply.workable.com", "workable.com/j/"],
        "personio":       ["apply.personio.de", "personio.com/job-listings"],
        "recruitee":      ["recruitee.com/o/", ".recruitee.com"],
        "teamtailor":     [".teamtailor.com", "career.teamtailor.com"],
        "jazzhr":         ["resumatorjobs.com", "jazz.co/"],
        "stepstone":      ["stepstone.de/stellenangebote", "stepstone.de/job"],
    }
    for platform, domains in _URL_PATTERNS.items():
        if any(d in url_lower for d in domains):
            log.info("🎯 Platform detected: %s @ %s", platform, url[:80])
            return platform
    log.info("🎯 Platform detected: generic @ %s", url[:80])
    return "generic"


# ── Submission helpers ────────────────────────────────────────────────────────

async def _verify_submission(page: Page) -> bool:
    """
    Multi-signal submission verifier. Returns True only when confident
    the application was actually submitted (not just a redirect).
    """
    await page.wait_for_timeout(2000)
    score = 0

    # Signal 1: URL pattern
    try:
        url = page.url.lower()
        _confirm_url_patterns = [
            "thank", "thanks", "danke", "success", "erfolgreich",
            "confirm", "bestatig", "submitted", "bewerbung-eingegangen",
            "application-sent", "applied", "complete", "finished",
        ]
        if any(p in url for p in _confirm_url_patterns):
            score += 3
            log.info("Submission signal: confirmation URL (%s)", url[:80])
    except Exception:
        pass

    # Signal 2: Visible text confirmation
    try:
        text = (await page.inner_text("body")).lower()
        _confirm_phrases = [
            "thank you", "thanks for applying", "application received",
            "successfully submitted", "application submitted",
            "we'll be in touch", "we will be in touch",
            "your application has been", "bewerbung eingegangen",
            "vielen dank", "danke für", "erfolgreich", "bewerbung erhalten",
            "we have received", "wir haben deine bewerbung",
            "application complete", "you have applied",
        ]
        _negative_phrases = [
            "please fill", "required field", "pflichtfeld",
            "bitte ausfüllen", "error", "fehler",
        ]
        matches = [p for p in _confirm_phrases if p in text]
        if matches:
            score += 3
            log.info("Submission signal: confirmation text (%s)", matches[0])
        neg = [p for p in _negative_phrases if p in text]
        if neg:
            score -= 3
            log.info("Submission negative signal: error text (%s)", neg[0])
    except Exception:
        pass

    # Signal 3: Form-gone check (no empty required fields = likely submitted)
    try:
        required_empty = await page.evaluate("""() => {
            const fields = document.querySelectorAll(
                'input[required]:not([type=hidden]), textarea[required], select[required]'
            );
            let empty = 0;
            fields.forEach(f => {
                if (f.offsetParent && !f.value.trim()) empty++;
            });
            return empty;
        }""")
        if required_empty == 0:
            score += 1
        elif required_empty > 2:
            score -= 2
            log.info("Submission negative signal: %d empty required fields", required_empty)
    except Exception:
        pass

    # If already confident, skip vision call
    if score >= 3:
        log.info("Submission confirmed (score=%d, no vision needed)", score)
        return True
    if score <= -3:
        log.info("Submission rejected (score=%d)", score)
        return False

    # Signal 4: Claude vision (only when uncertain)
    try:
        import base64, json as _json, os
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if api_key:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            screenshot = await page.screenshot()
            b64 = base64.b64encode(screenshot).decode()
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=60,
                messages=[{"role": "user", "content": [
                    {"type": "image", "source": {
                        "type": "base64", "media_type": "image/png", "data": b64}},
                    {"type": "text", "text":
                        "Is this a job application CONFIRMATION page (showing the app was submitted)? "
                        'Reply with JSON only: {"confirmed": true} or {"confirmed": false}'}
                ]}]
            )
            raw = resp.content[0].text.strip()
            s, e = raw.find("{"), raw.rfind("}") + 1
            if s >= 0 and e > s:
                data = _json.loads(raw[s:e])
                if data.get("confirmed"):
                    score += 4
                    log.info("Submission signal: Claude vision confirmed")
                else:
                    score -= 2
                    log.info("Submission negative signal: Claude vision not confirmed")
    except Exception as _ve:
        log.debug("Vision verify failed: %s", _ve)

    confirmed = score >= 3
    log.info("Submission verification score=%d → %s", score, "CONFIRMED" if confirmed else "REJECTED")
    return confirmed


async def _get_form_snapshot(page: Page) -> str:
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
    try:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            return []
        client = anthropic.Anthropic(api_key=api_key)
        # Upgrade to Sonnet for better form understanding
        _model = "claude-sonnet-4-20250514"
        # Inject memory context into system prompt
        _mem_context = ""
        try:
            from applier.memory import get_memory
            _mem_context = get_memory().build_prompt_context()
        except Exception:
            pass
        system = (
            "You are filling a job application form on behalf of the candidate. "
            + (_mem_context + "\n\n" if _mem_context else "")
            + "Analyse the form fields and return a JSON array of actions to take. "
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
            model=_model,
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


async def _fill_remaining_questions(
    page: Page, job: dict, resume_text: str, profile: dict
) -> None:
    job_desc = job.get("description", "")
    url = job.get("url", "")
    prior: list[dict] = []

    try:
        inputs = page.locator(
            "input[type='text']:not([disabled]), input[type='number']:not([disabled]), "
            "input[type='email']:not([disabled]), input[type='tel']:not([disabled])"
        )
        for i in range(min(await inputs.count(), 20)):
            try:
                inp = inputs.nth(i)
                if not await inp.is_visible():
                    continue
                if (await inp.input_value()).strip():
                    continue
                label = await _get_label(page, inp)
                if not label:
                    continue
                inp_type = (await inp.get_attribute("type") or "text").lower()
                eff_type = "number" if is_numeric_question(label, inp_type) else "text"
                answer = answer_custom_question(label, eff_type, [], resume_text, profile, job_desc, prior_answers=prior)
                if eff_type == "number":
                    answer = re.sub(r"[^\d]", "", answer.split(".")[0]) or "1"
                if answer:
                    await _reliable_fill(page, inp, answer)
                    await _handle_autocomplete(page, inp, answer)
                    _emit("apply_step", {"url": url, "step": f"  ✎ {label[:40]}: {answer[:40]}"})
                    prior.append({"question": label, "answer": answer})
            except Exception:
                pass
    except Exception:
        pass

    try:
        for i in range(min(await page.locator("textarea:not([disabled])").count(), 10)):
            try:
                ta = page.locator("textarea:not([disabled])").nth(i)
                if not await ta.is_visible() or (await ta.input_value()).strip():
                    continue
                label = await _get_label(page, ta)
                if not label:
                    continue
                answer = answer_custom_question(label, "textarea", [], resume_text, profile, job_desc, prior_answers=prior)
                if answer:
                    await _reliable_fill(page, ta, answer)
                    _emit("apply_step", {"url": url, "step": f"  ✎ {label[:40]}: {answer[:60]}"})
                    prior.append({"question": label, "answer": answer})
            except Exception:
                pass
    except Exception:
        pass

    try:
        _PH = {"", "select", "select an option", "---", "please select", "choose one", "bitte wählen"}
        for i in range(min(await page.locator("select:not([disabled])").count(), 10)):
            try:
                sel = page.locator("select:not([disabled])").nth(i)
                if not await sel.is_visible():
                    continue
                cur = (await sel.evaluate("el => (el.options[el.selectedIndex]||{}).text||''")).strip().lower()
                if cur and cur not in _PH:
                    continue
                label = await _get_label(page, sel)
                opts = [o.strip() for o in await sel.locator("option").all_text_contents() if o.strip() and o.strip().lower() not in _PH]
                if not opts:
                    continue
                answer = answer_custom_question(label or "", "select", opts, resume_text, profile, job_desc, prior_answers=prior)
                if answer:
                    try:
                        await sel.select_option(label=answer)
                    except Exception:
                        await sel.select_option(value=answer)
                    _emit("apply_step", {"url": url, "step": f"  ✎ {(label or 'select')[:40]}: {answer[:40]}"})
                    prior.append({"question": label or "select", "answer": answer})
            except Exception:
                pass
    except Exception:
        pass

    try:
        _CONSENT = re.compile(r"\bagree\b|\bconfirm\b|\bauthorize\b|\bconsent\b|\backnowledge\b|\bterms\b|\bprivacy\b", re.I)
        for i in range(min(await page.locator("input[type='checkbox']").count(), 15)):
            try:
                cb = page.locator("input[type='checkbox']").nth(i)
                if not await cb.is_visible() or await cb.is_checked():
                    continue
                label = await _get_label(page, cb)
                if label and _CONSENT.search(label):
                    await cb.click()
                    await asyncio.sleep(0.3)
                    _emit("apply_step", {"url": url, "step": f"  ✎ Consent checkbox checked: {label[:50]}"})
            except Exception:
                pass
    except Exception:
        pass


# ── ATS handlers ──────────────────────────────────────────────────────────────

async def _apply_greenhouse(page: Page, job: dict, cfg: dict, resume_text: str, profile: dict) -> dict:
    url = page.url
    _emit("apply_step", {"url": url, "step": "🌿 Greenhouse form — filling standard fields"})
    try:
        p = profile
        for sel, val in [
            ("input#first_name", p.get("first_name", "")),
            ("input#last_name",  p.get("last_name", "")),
            ("input#email",      p.get("email", "")),
            ("input#phone",      p.get("phone", "")),
        ]:
            if not val:
                continue
            try:
                el = page.locator(sel)
                if await el.count() and await el.first.is_visible():
                    await el.first.fill(val)
                    await asyncio.sleep(0.2)
            except Exception:
                pass

        try:
            resume = _resume_path(cfg, job.get("_resume_lang", "en"))
            if resume:
                fi = page.locator("input[type='file']").first
                if await fi.count():
                    await fi.set_input_files(str(resume))
                    await asyncio.sleep(1.5)
                    _emit("apply_step", {"url": url, "step": "  ✎ Resume uploaded"})
        except Exception:
            pass

        try:
            li = page.locator("input[id*='linkedin' i], input[placeholder*='LinkedIn' i]")
            if await li.count() and await li.first.is_visible():
                if not (await li.first.input_value()).strip():
                    await li.first.fill(p.get("linkedin_url", ""))
        except Exception:
            pass

        await _fill_remaining_questions(page, job, resume_text, profile)

        submit = page.locator("input[type='submit'], button[type='submit']")
        if await submit.count():
            await submit.last.scroll_into_view_if_needed()
            await asyncio.sleep(0.5)
            await submit.last.click()
            await page.wait_for_load_state("networkidle", timeout=15000)
            if await _verify_submission(page):
                return {"success": True, "manual": False, "apply_type": "External (Greenhouse)"}

        return {"success": False, "manual": True, "note": "Greenhouse: submit not confirmed"}
    except PWTimeout:
        return {"success": False, "manual": False, "note": "Timeout"}
    except Exception as exc:
        return {"success": False, "manual": True, "note": str(exc)}


async def _apply_lever(page: Page, job: dict, cfg: dict, resume_text: str, profile: dict) -> dict:
    url = page.url
    _emit("apply_step", {"url": url, "step": "⚙️ Lever form — filling standard fields"})
    try:
        p = profile
        for sel, val in [
            ("input[name='name']",           f"{p.get('first_name','')} {p.get('last_name','')}".strip()),
            ("input[name='email']",          p.get("email", "")),
            ("input[name='phone']",          p.get("phone", "")),
            ("input[name='urls[LinkedIn]']", p.get("linkedin_url", "")),
            ("input[name='urls[GitHub]']",   p.get("github_url", "")),
            ("input[name='org']",            p.get("current_location", "")),
        ]:
            if not val:
                continue
            try:
                el = page.locator(sel)
                if await el.count() and await el.first.is_visible():
                    existing = await el.first.input_value()
                    if not existing.strip():
                        await el.first.fill(val)
                        await asyncio.sleep(0.2)
            except Exception:
                pass

        try:
            resume = _resume_path(cfg, job.get("_resume_lang", "en"))
            if resume:
                fi = page.locator("input[type='file'][name='resume'], input[type='file']").first
                if await fi.count():
                    await fi.set_input_files(str(resume))
                    await asyncio.sleep(1.5)
                    _emit("apply_step", {"url": url, "step": "  ✎ Resume uploaded"})
        except Exception:
            pass

        await _fill_remaining_questions(page, job, resume_text, profile)

        submit = page.locator("button[type='submit'], input[type='submit']")
        if await submit.count():
            await submit.last.scroll_into_view_if_needed()
            await asyncio.sleep(0.5)
            await submit.last.click()
            await page.wait_for_load_state("networkidle", timeout=15000)
            if await _verify_submission(page):
                return {"success": True, "manual": False, "apply_type": "External (Lever)"}

        return {"success": False, "manual": True, "note": "Lever: submit not confirmed"}
    except PWTimeout:
        return {"success": False, "manual": False, "note": "Timeout"}
    except Exception as exc:
        return {"success": False, "manual": True, "note": str(exc)}


async def _apply_ashby(page: Page, job: dict, cfg: dict, resume_text: str, profile: dict) -> dict:
    url = page.url
    _emit("apply_step", {"url": url, "step": "🔷 Ashby form — filling standard fields"})
    try:
        p = profile
        for testid, val in [
            ("input-firstName", p.get("first_name", "")),
            ("input-lastName",  p.get("last_name", "")),
            ("input-email",     p.get("email", "")),
            ("input-phone",     p.get("phone", "")),
        ]:
            if not val:
                continue
            try:
                el = page.locator(f"[data-testid='{testid}'], input[id*='{testid}' i]")
                if await el.count() and await el.first.is_visible():
                    if not (await el.first.input_value()).strip():
                        await el.first.fill(val)
                        await asyncio.sleep(0.2)
            except Exception:
                pass

        try:
            resume = _resume_path(cfg, job.get("_resume_lang", "en"))
            if resume:
                fi = page.locator("input[type='file']").first
                if await fi.count():
                    await fi.set_input_files(str(resume))
                    await asyncio.sleep(1.5)
                    _emit("apply_step", {"url": url, "step": "  ✎ Resume uploaded"})
        except Exception:
            pass

        await _fill_remaining_questions(page, job, resume_text, profile)

        submit = page.locator("button[type='submit']")
        if await submit.count():
            await submit.last.scroll_into_view_if_needed()
            await asyncio.sleep(0.5)
            await submit.last.click()
            await page.wait_for_load_state("networkidle", timeout=15000)
            if await _verify_submission(page):
                return {"success": True, "manual": False, "apply_type": "External (Ashby)"}

        return {"success": False, "manual": True, "note": "Ashby: submit not confirmed"}
    except PWTimeout:
        return {"success": False, "manual": False, "note": "Timeout"}
    except Exception as exc:
        return {"success": False, "manual": True, "note": str(exc)}


async def _apply_workday(page: Page, job: dict, cfg: dict, resume_text: str, profile: dict) -> dict:
    _emit("apply_step", {"url": job.get("url", ""), "step": "⚙️ Workday detected — using AI agent"})
    return await _apply_generic_browser_use(page, job, cfg, resume_text, profile)


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


async def _apply_generic_browser_use(
    page: Page, job: dict, cfg: dict,
    resume_text: str, profile: dict
) -> dict:
    if not _BROWSER_USE_OK:
        # Fall back to Claude vision filler (no extra deps needed)
        _emit("apply_step", {"url": job.get("url",""),
              "step": "🧠 browser-use unavailable — using Claude vision filler"})
        try:
            from applier.smart_filler import smart_apply_page
            return await smart_apply_page(page, job, profile, resume_text, cfg)
        except Exception as _sf_e:
            log.warning("smart_apply_page failed: %s", _sf_e)
            return {"success": False, "manual": True,
                    "note": f"Smart apply failed: {_sf_e}"}

    url = page.url or job.get("url", "")
    p = profile
    resume_path = cfg.get("paths", {}).get("resume_en", "")

    task = f"""
You are filling out a job application form. Complete ALL visible form fields and submit the application.

APPLICANT PROFILE:
- Name: {p.get('first_name', '')} {p.get('last_name', '')}
- Email: {p.get('email', '')}
- Phone: {p.get('phone', '')}
- Location: Berlin, Germany
- LinkedIn: {p.get('linkedin_url', '')}
- GitHub: {p.get('github_url', '')}
- Years of experience: {p.get('years_of_experience', 5)}
- Work authorization: German citizen, NO visa sponsorship needed
- Notice period: immediately available
- Salary expectation: {p.get('salary_expectation', '75000')} EUR
- Languages: German B1, English Fluent

RULES:
1. If asked about visa/sponsorship → always answer NO, not needed
2. If asked for salary as text → say "Open to discussion based on total compensation"
3. If asked for salary as number → enter {p.get('salary_expectation', '75000')}
4. If asked about relocation → YES
5. Upload resume from: {resume_path}
6. For "country" fields → select Germany or Deutschland
7. For consent/agreement checkboxes → check them
8. STOP if you see a login/account creation form — log it and abort
9. STOP and mark success if you see a confirmation page
10. Maximum 40 steps total
"""

    _emit("apply_step", {"url": url, "step": "🤖 browser-use agent starting generic apply"})

    browser = None
    try:
        try:
            _pw_browser = page.context.browser
            _cdp_url = None
            if _pw_browser:
                _endpoint = getattr(_pw_browser, "_impl_obj", None)
                if _endpoint:
                    _ws = getattr(_endpoint, "_connection", None)
                    _cdp_url = getattr(_ws, "url", None) if _ws else None
            if _cdp_url:
                browser = _BUBrowser(config=_BUBrowserConfig(cdp_url=_cdp_url))
                log.debug("browser-use: reusing CDP at %s", _cdp_url)
        except Exception as _cdp_err:
            log.debug("browser-use: CDP reuse failed (%s)", _cdp_err)

        if browser is None:
            browser = _BUBrowser(config=_BUBrowserConfig(headless=cfg.get("headless", False)))

        llm = _BUChatAnthropic(model="claude-sonnet-4-20250514")
        agent = _BUAgent(task=task, llm=llm, browser=browser)
        result = await agent.run(max_steps=40)

        final = (result.final_result() or "").lower()
        success = any(kw in final for kw in [
            "success", "submitted", "applied", "complete", "confirmed",
            "application sent", "thank you", "danke"
        ])
        if success:
            _emit("apply_step", {"url": url, "step": "  ✓ browser-use: application submitted"})
            try:
                from applier.memory import get_memory
                get_memory().save_application_result(url, platform, True, [])
            except Exception:
                pass
            return {"success": True, "manual": False,
                    "note": result.final_result() or "", "apply_type": "External (browser-use)"}
        _emit("apply_step", {"url": url,
            "step": f"  ⚠ browser-use unclear result: {(result.final_result() or '')[:120]}"})
        return {"success": False, "manual": True,
                "note": result.final_result() or "Agent did not confirm submission"}

    except PWTimeout:
        return {"success": False, "manual": False, "note": "Timeout"}
    except Exception as exc:
        log.warning("browser-use agent failed: %s", exc)
        return {"success": False, "manual": True, "note": str(exc)}
    finally:
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass


async def _apply_external_ai(
    page: Page, job: dict, cfg: dict,
    resume_text: str, profile: dict
) -> dict:
    url = page.url or job.get("url", "")
    platform = _detect_platform_by_url(url)
    _emit("apply_step", {"url": url, "step": f"🌐 External apply — platform: {platform}"})

    _HANDLERS: dict = {
        "greenhouse":      _apply_greenhouse,
        "lever":           _apply_lever,
        "ashby":           _apply_ashby,
        "workday":         _apply_generic_browser_use,
        "rippling":        _apply_generic_browser_use,
        "smartrecruiters": _apply_generic_browser_use,
        "icims":           _apply_generic_browser_use,
        "bamboohr":        _apply_generic_browser_use,
        "jobvite":         _apply_generic_browser_use,
        "workable":        _apply_generic_browser_use,
        "personio":        _apply_generic_browser_use,
        "recruitee":       _apply_generic_browser_use,
        "teamtailor":      _apply_generic_browser_use,
        "jazzhr":          _apply_generic_browser_use,
        "stepstone":       _apply_generic_browser_use,
        "generic":         _apply_generic_browser_use,
    }
    handler = _HANDLERS.get(platform, _apply_generic_browser_use)
    return await handler(page, job, cfg, resume_text, profile)


async def _apply_unknown(page: Page, job: dict, cfg: dict, resume_text: str, profile: dict) -> dict:
    _emit("apply_step", {"url": job.get("url", ""), "step": "🤖 Unknown platform — attempting generic AI apply"})
    return await _apply_generic_browser_use(page, job, cfg, resume_text, profile)


PLATFORM_HANDLERS: dict = {
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


async def _route_to_handler(
    page: Page, job: dict, cfg: dict,
    resume_text: str, profile: dict, platform: str
) -> dict:
    handler = PLATFORM_HANDLERS.get(platform)
    if handler and platform not in ("linkedin", "unknown"):
        new_url = page.url
        log.info("Routing to %s handler (URL: %s)", platform, new_url)
        return await handler(page, dict(job, url=new_url), cfg, resume_text, profile)
    # Unknown platform — try Claude vision smart apply before giving up
    _emit("apply_step", {"url": job.get("url",""),
          "step": f"🧠 Unknown platform ({platform}) — trying smart apply"})
    try:
        from applier.smart_filler import smart_apply_page
        _smart = await smart_apply_page(page, job, profile, resume_text, cfg)
        if _smart.get('success'):
            return _smart
    except Exception as _se:
        log.warning("smart_apply_page error: %s", _se)
    log.warning("DEBUG pre-manual: URL=%s", page.url)
    log.warning("DEBUG pre-manual: Reason=External apply, unknown platform: %s", platform)
    _emit("apply_step", {"url": job.get("url", ""),
          "step": f"⚠️ Going to manual: External apply — unknown platform: {platform}"})
    return {"success": False, "manual": True, "note": f"External apply — unknown platform: {platform}"}


# ── Public entry point ────────────────────────────────────────────────────────

async def follow_external_apply(
    page: Page,
    job: dict,
    profile: dict,
    resume_text: str,
    cfg: dict,
) -> dict:
    """Handle external apply redirect — call immediately after the Apply button was clicked."""
    _emit("apply_step", {"url": job.get("url", ""),
        "step": "Detected external apply — waiting for redirect…"})

    await asyncio.sleep(2)
    _cur_url = page.url
    log.info("After Apply click — URL: %s", _cur_url)

    # New tab opened — wait a bit longer for it to appear
    for _tab_wait in range(5):
        if len(page.context.pages) > 1:
            break
        await asyncio.sleep(0.8)
    if len(page.context.pages) > 1:
        _new_page = page.context.pages[-1]
        try:
            await _new_page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass
        _ext_url = _new_page.url
        log.info("External apply new tab: %s", _ext_url)
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

    # Same-tab navigation
    if "linkedin.com" not in _cur_url:
        _ext_platform = detect_platform(_cur_url)
        _emit("apply_step", {"url": job.get("url", ""),
            "step": f"External apply (same tab) — {_ext_platform}"})
        return await _route_to_handler(page, job, cfg, resume_text, profile, _ext_platform)

    # Still on LinkedIn — look for modal link to external site
    for _psel in [
        "a:text('Apply on company website')", "a:text('Continue')",
        ".artdeco-modal a[href]", "a[href*='apply']",
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
