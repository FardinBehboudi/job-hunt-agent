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
    _cover_letter_path,
    _get_label,
    _reliable_fill,
    _handle_autocomplete,
    is_numeric_question,
    answer_custom_question,
)

try:
    from browser_use import Agent as _BUAgent, Browser as _BUBrowser
    # browser-use >=0.2 requires its own LLM wrapper (implements .provider/
    # .model for its internal bookkeeping) rather than a raw LangChain
    # BaseChatModel — passing langchain_anthropic.ChatAnthropic here crashes
    # with "'ChatAnthropic' object has no attribute 'provider'" (verified
    # live) because that class was never meant to satisfy this interface.
    from browser_use.llm.anthropic.chat import ChatAnthropic as _BUChatAnthropic
    _BROWSER_USE_OK = True
except ImportError:
    _BUAgent = _BUBrowser = _BUChatAnthropic = None  # type: ignore
    _BROWSER_USE_OK = False

log = logging.getLogger(__name__)

# ── Cookie / privacy consent popup dismissal ──────────────────────────────────
_CONSENT_ACCEPT_RE = re.compile(
    r"^(accept|accept all|accept all cookies|alle akzeptieren|akzeptieren|accept cookies|"
    r"alle cookies akzeptieren|i accept|agree|agree all|agree to all|"
    r"allow all|allow cookies|allow all cookies|ok|got it|"
    r"verstanden|zustimmen|einverstanden|accepter tout|tout accepter|"
    r"aceitar tudo|aceptar todo|akkoord|alles accepteren)$",
    re.I,
)


async def _dismiss_consent_popups(page: Page) -> None:
    """Click Accept on any cookie/privacy consent dialog. Tries text match then CMP selectors."""
    try:
        buttons = await page.query_selector_all("button, [role='button']")
        for btn in buttons:
            try:
                if not await btn.is_visible():
                    continue
                txt = (await btn.inner_text()).strip()
                if _CONSENT_ACCEPT_RE.match(txt):
                    await btn.click()
                    await asyncio.sleep(0.8)
                    log.info("Dismissed consent popup: clicked '%s'", txt[:40])
                    return
            except Exception:
                pass
        for sel in [
            "#onetrust-accept-btn-handler",
            ".cc-btn.cc-allow",
            "[data-testid='uc-accept-all-button']",
            "#CybotCookiebotDialogBodyButtonAccept",
            ".accept-all",
            "[aria-label*='accept all' i]",
        ]:
            try:
                loc = page.locator(sel)
                if await loc.count() and await loc.first.is_visible():
                    await loc.first.click()
                    await asyncio.sleep(0.8)
                    log.info("Dismissed consent popup via selector: %s", sel)
                    return
            except Exception:
                pass
    except Exception:
        pass


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
    (r"successfactors\.\w+|sap\.com", "successfactors"),
    (r"stepstone\.de",              "stepstone"),
    (r"xing\.com",                  "xing"),
    (r"indeed\.com",                "indeed"),
    (r"csod\.com|cornerstone",      "csod"),
    (r"rexx-systems\.com",          "rexx"),
    (r"softgarden\.io|softgarden\.de", "softgarden"),
    (r"erecruiter\.net",            "erecruiter"),
    (r"pinpoint\.com",              "pinpoint"),
    (r"rippling\.com",              "rippling"),
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
        "csod":           ["csod.com/ux/ats", "csod.com/careers", "cornerstone"],
        "softgarden":     ["softgarden.io", "softgarden.de"],
        "rexx":           ["rexx-systems.com"],
        "rippling":       ["rippling.com/jobs", "app.rippling.com"],
    }
    for platform, domains in _URL_PATTERNS.items():
        if any(d in url_lower for d in domains):
            log.info("🎯 Platform detected: %s @ %s", platform, url[:80])
            return platform
    log.info("🎯 Platform detected: generic @ %s", url[:80])
    return "generic"


# ── Resume / cover letter upload ──────────────────────────────────────────────

async def _upload_resume_and_cover_letter(page: Page, job: dict, cfg: dict, url: str) -> None:
    """Fill every visible file-upload input on the page — routing to the
    tailored cover letter when the field's own label says "cover letter", the
    resume otherwise. Structured ATS pages (Greenhouse, Lever, Ashby) commonly
    have separate Resume/CV and Cover Letter upload fields."""
    lang = job.get("_resume_lang", "en")
    company = job.get("company")
    resume = _resume_path(cfg, lang, company=company)
    cover_letter = _cover_letter_path(cfg, lang, company=company)

    try:
        inputs = page.locator("input[type='file']")
        count = await inputs.count()
    except Exception:
        return

    resume_used = False
    for i in range(count):
        try:
            fi = inputs.nth(i)
            if not await fi.is_visible():
                continue
            label = (await _get_label(page, fi) or "").lower()
            wants_cl = any(w in label for w in ("cover letter", "anschreiben", "motivation"))
            if wants_cl and cover_letter:
                await fi.set_input_files(str(cover_letter))
                await asyncio.sleep(1.0)
                _emit("apply_step", {"url": url, "step": f"  ✎ Cover letter uploaded ({cover_letter.name})"})
            elif resume and not resume_used:
                await fi.set_input_files(str(resume))
                await asyncio.sleep(1.0)
                _emit("apply_step", {"url": url, "step": f"  ✎ Resume uploaded ({resume.name})"})
                resume_used = True
        except Exception:
            pass


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
        # Match against the path+query only, never the hostname — verified
        # live: a job that redirected to career5.successfactors.eu (a
        # legitimate ATS provider whose name happens to contain "success")
        # got a false "confirmed" verdict from this check before any field
        # was even filled, because "success" matched inside "successfactors".
        # ATS/company names commonly contain thank/success/complete-ish
        # substrings; genuine confirmation signals live in the path, not
        # the domain.
        from urllib.parse import urlparse
        _url_parts = urlparse(url)
        _url_tail = (_url_parts.path or "") + "?" + (_url_parts.query or "")
        _confirm_url_patterns = [
            "thank", "thanks", "danke", "success", "erfolgreich",
            "confirm", "bestatig", "submitted", "bewerbung-eingegangen",
            "application-sent", "applied", "complete", "finished",
        ]
        if any(p in _url_tail for p in _confirm_url_patterns):
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
            "vielen dank", "danke für ihre bewerbung", "bewerbung erhalten",
            "we have received", "wir haben deine bewerbung",
            "application complete", "you have applied",
            "erfolgreich beworben", "erfolgreich eingereicht",
            "bewerbung erfolgreich", "ihre bewerbung wurde",
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
        if required_empty == 0 and score > 0:
            # Only boost if other signals already lean positive — "no required fields"
            # alone is unreliable (many custom sites don't use the required attribute).
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
            // type=password excluded — never expose an authentication field
            // to the AI form-filler; account creation is the user's call.
            const inputs = document.querySelectorAll(
                'input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=password]),' +
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
        _model = "claude-sonnet-4-6"
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

    # Must exclude type=password exactly like _get_form_snapshot above, or
    # the indices the AI was given (built from that filtered list) point at
    # the wrong elements here.
    elements = await page.query_selector_all(
        'input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=password]),'
        'textarea, select'
    )
    visible = []
    for el in elements:
        if await el.is_visible():
            visible.append(el)

    if idx >= len(visible):
        return
    el = visible[idx]

    tag = (await (await el.get_property("tagName")).json_value()).lower()
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
    page: Page, job: dict, resume_text: str, profile: dict, cfg: dict
) -> None:
    """Fill all visible unfilled fields on the current ATS page.
    Handles text/number/textarea, <select>, comboboxes, radio groups, and consent checkboxes.
    """
    job_desc = job.get("description", "")
    url = job.get("url", "")
    prior: list[dict] = []

    # ── Text / number / email / tel inputs ────────────────────────────────────
    try:
        inputs = page.locator(
            "input[type='text']:not([disabled]), input[type='number']:not([disabled]), "
            "input[type='email']:not([disabled]), input[type='tel']:not([disabled]), "
            "input[type='date']:not([disabled])"
        )
        for i in range(min(await inputs.count(), 25)):
            try:
                inp = inputs.nth(i)
                if not await inp.is_visible():
                    continue
                if (await inp.input_value()).strip():
                    continue
                # Skip combobox inputs — handled separately below
                _inp_role  = (await inp.get_attribute("role") or "").lower()
                _inp_popup = (await inp.get_attribute("aria-haspopup") or "").lower()
                if _inp_role == "combobox" or _inp_popup in ("listbox", "true"):
                    continue
                label = await _get_label(page, inp)
                if not label:
                    continue
                inp_type = (await inp.get_attribute("type") or "text").lower()
                eff_type = "number" if is_numeric_question(label, inp_type) else inp_type if inp_type in ("email", "tel") else "text"
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

    # ── Textareas ─────────────────────────────────────────────────────────────
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

    # ── <select> dropdowns ────────────────────────────────────────────────────
    try:
        _PH = {"", "select", "select an option", "select an option...", "---",
               "please select", "choose one", "choose", "bitte wählen", "auswählen"}
        for i in range(min(await page.locator("select:not([disabled])").count(), 12)):
            try:
                sel = page.locator("select:not([disabled])").nth(i)
                if not await sel.is_visible():
                    continue
                cur = (await sel.evaluate("el => (el.options[el.selectedIndex]||{}).text||''")).strip().lower()
                if cur and cur not in _PH:
                    continue
                label = await _get_label(page, sel)
                opts = [o.strip() for o in await sel.locator("option").all_text_contents()
                        if o.strip() and o.strip().lower() not in _PH]
                if not opts:
                    continue
                # Some custom-styled selects set aria-label to the CURRENTLY
                # DISPLAYED value rather than the field's question (verified
                # live: a country select's native selectedIndex read as
                # empty/placeholder, but its aria-label was "Germany" — one
                # of its own options — which then got mistaken for the
                # question and "answered" into an unrelated country). A real
                # question is never verbatim one of its own dropdown options.
                if label and label.strip().lower() in {o.lower() for o in opts}:
                    continue
                answer = answer_custom_question(label or "", "select", opts, resume_text, profile, job_desc, prior_answers=prior)
                if answer:
                    try:
                        await sel.select_option(label=answer)
                    except Exception:
                        # fuzzy match
                        for opt in opts:
                            if answer.lower() in opt.lower() or opt.lower() in answer.lower():
                                try:
                                    await sel.select_option(label=opt)
                                    answer = opt
                                    break
                                except Exception:
                                    pass
                    # FIX: dispatch change so React/Angular forms update their state
                    await sel.dispatch_event("change")
                    _emit("apply_step", {"url": url, "step": f"  ✎ {(label or 'select')[:40]}: {answer[:40]}"})
                    prior.append({"question": label or "select", "answer": answer})
            except Exception:
                pass
    except Exception:
        pass

    # ── Combobox / typeahead dropdowns ────────────────────────────────────────
    try:
        _PH2 = {"", "select", "select an option", "---", "please select", "choose one", "choose",
                "no selection", "none selected", "nothing selected"}
        comboboxes = page.locator(
            "input[role='combobox']:not([disabled]), "
            "input[aria-haspopup='listbox']:not([disabled]), "
            "input[aria-haspopup='true']:not([disabled]), "
            "input[aria-autocomplete='list']:not([disabled])"
        )
        for i in range(min(await comboboxes.count(), 10)):
            try:
                cb_input = comboboxes.nth(i)
                if not await cb_input.is_visible():
                    continue
                cur_val = ((await cb_input.input_value()) or "").strip().lower()
                if cur_val and cur_val not in _PH2:
                    continue
                label = await _get_label(page, cb_input)
                if not label:
                    continue

                # Open the dropdown
                await cb_input.click()
                await asyncio.sleep(0.6)

                # FIX: broad option selector without `:visible` — filter with is_visible() per element
                _opt_loc = page.locator(
                    "[role='option'], [role='listbox'] [role='option'], "
                    "li[data-value], [class*='option']:not([class*='no-option']), "
                    "[class*='dropdown-item'], [class*='list-item']"
                )
                # Some dropdowns (country/nationality pickers especially) list
                # 200+ entries in no particular order — verified live: a
                # country combobox had 250 options with "Germany" nowhere
                # near the front, so capping this scan at 30 meant the actual
                # answer (and the mislabel guard below, which needs to find
                # the label among the options to detect a stale aria-label)
                # never saw it at all. text_content() is cheap; do it in bulk
                # first and only visibility-check the ones that look relevant.
                opts: list[str] = []
                _raw_opt_count = await _opt_loc.count()
                for oi in range(min(_raw_opt_count, 300)):
                    try:
                        opt_el = _opt_loc.nth(oi)
                        if not await opt_el.is_visible():
                            continue
                        txt = (await opt_el.text_content() or "").strip()
                        if txt and txt.lower() not in _PH2:
                            opts.append(txt)
                    except Exception:
                        pass

                if not opts:
                    await cb_input.press("Escape")
                    continue

                # Same guard as the <select> loop above: some custom comboboxes
                # set aria-label to the CURRENTLY DISPLAYED value rather than
                # the field's question (verified live: a country combobox's
                # label was "Germany" — one of its own dropdown options —
                # which then got "answered" into an unrelated country). A real
                # question is never verbatim one of its own options.
                # But the underlying <input> is genuinely empty regardless of
                # what's shown, so the field still fails validation if we just
                # skip it (verified live: doing so left the form stuck) — the
                # label itself IS the already-known-correct answer, so select
                # it explicitly instead of asking the LLM.
                _label_is_stale_value = label.strip().lower() in {o.lower() for o in opts}
                answer = label if _label_is_stale_value else answer_custom_question(
                    label, "select", opts, resume_text, profile, job_desc, prior_answers=prior
                )
                if not answer:
                    await cb_input.press("Escape")
                    continue

                # Click the matching option (re-query live DOM — not stale
                # snapshot). Two passes: exact match first, then substring —
                # a pure substring check picks whichever option happens to
                # come first in DOM order, which is wrong whenever a
                # placeholder-ish option contains the real answer as a
                # substring (verified live: answer "No" matched "No
                # Selection" — the blank/placeholder entry — before ever
                # reaching the real "No" option, since "no" is literally
                # inside "no selection").
                _opt_texts: dict[int, str] = {}
                for oi in range(min(await _opt_loc.count(), 300)):
                    try:
                        opt_el = _opt_loc.nth(oi)
                        if not await opt_el.is_visible():
                            continue
                        _opt_texts[oi] = (await opt_el.text_content() or "").strip()
                    except Exception:
                        pass
                clicked = False
                for _pass in ("exact", "substring"):
                    for oi, txt in _opt_texts.items():
                        matched = (txt.lower() == answer.lower() if _pass == "exact"
                                   else answer.lower() in txt.lower())
                        if not matched:
                            continue
                        try:
                            await _opt_loc.nth(oi).click()
                            clicked = True
                        except Exception:
                            pass
                        break
                    if clicked:
                        break

                if not clicked:
                    # Fallback: type the answer and let autocomplete pick it
                    await cb_input.fill(answer[:20])
                    await asyncio.sleep(0.5)
                    for oi in range(min(await _opt_loc.count(), 10)):
                        try:
                            opt_el = _opt_loc.nth(oi)
                            if await opt_el.is_visible():
                                await opt_el.click()
                                clicked = True
                                break
                        except Exception:
                            pass
                    if not clicked:
                        await cb_input.press("Escape")
                        continue

                _emit("apply_step", {"url": url, "step": f"  ✎ {label[:40]}: {answer[:40]}"})
                prior.append({"question": label, "answer": answer})
            except Exception:
                pass
    except Exception:
        pass

    # ── Radio groups (fieldsets) ──────────────────────────────────────────────
    try:
        for i in range(min(await page.locator("fieldset").count(), 12)):
            try:
                fs = page.locator("fieldset").nth(i)
                if not await fs.is_visible():
                    continue
                radios = fs.locator("input[type='radio']")
                r_count = await radios.count()
                if not r_count:
                    continue
                # Skip if already answered
                any_checked = False
                for j in range(r_count):
                    try:
                        if await radios.nth(j).is_checked():
                            any_checked = True
                            break
                    except Exception:
                        pass
                if any_checked:
                    continue

                legend = fs.locator("legend")
                question = (await legend.first.text_content() or "").strip() if await legend.count() else ""

                opt_vals: list[str] = []
                for j in range(r_count):
                    try:
                        val = await radios.nth(j).get_attribute("value") or ""
                        if val:
                            opt_vals.append(val)
                    except Exception:
                        pass
                if not opt_vals:
                    opt_vals = ["Yes", "No"]

                answer = answer_custom_question(
                    question or "Yes/No", "radio", opt_vals,
                    resume_text, profile, job_desc, prior_answers=prior
                )
                # Click the matching radio
                for j in range(r_count):
                    try:
                        val = (await radios.nth(j).get_attribute("value") or "")
                        if answer.lower() in val.lower() or val.lower() in answer.lower():
                            rid = await radios.nth(j).get_attribute("id") or ""
                            if rid:
                                lbl = page.locator(f"label[for='{rid}']")
                                if await lbl.count():
                                    await lbl.first.click()
                                else:
                                    await radios.nth(j).click(force=True)
                            else:
                                await radios.nth(j).click(force=True)
                            await asyncio.sleep(0.3)
                            prior.append({"question": question, "answer": answer})
                            _emit("apply_step", {"url": url, "step": f"  ✎ {(question or 'radio')[:40]}: {answer[:40]}"})
                            break
                    except Exception:
                        pass
            except Exception:
                pass
    except Exception:
        pass

    # ── File uploads (resume / CV) ────────────────────────────────────────────
    try:
        file_inputs = page.locator("input[type='file']:not([disabled])")
        for i in range(min(await file_inputs.count(), 5)):
            try:
                fi = file_inputs.nth(i)
                if not await fi.is_visible():
                    continue
                # Detect accepted types — prefer PDF, fallback to any
                accept = (await fi.get_attribute("accept") or "").lower()
                if accept and "pdf" not in accept and "doc" not in accept and "*" not in accept:
                    continue  # not a document upload (e.g. profile photo)
                label = await _get_label(page, fi)
                label_low = label.lower()
                # Skip if it looks like a photo/avatar upload
                if any(w in label_low for w in ("photo", "picture", "avatar", "bild", "foto")):
                    continue
                _lang = job.get("_resume_lang", "en")
                _company = job.get("company")
                wants_cl = any(w in label_low for w in ("cover letter", "anschreiben", "motivation"))
                doc_path = (_cover_letter_path(cfg, _lang, company=_company) if wants_cl else None) \
                    or _resume_path(cfg, _lang, company=_company)
                if not doc_path:
                    continue
                await fi.set_input_files(str(doc_path))
                await asyncio.sleep(0.5)
                _kind = "cover letter" if wants_cl else "resume"
                _emit("apply_step", {"url": url, "step": f"  📎 Uploaded {_kind}: {doc_path.name}"})
            except Exception:
                pass
    except Exception:
        pass

    # Some ATS platforms (verified live: SAP SuccessFactors) render NO
    # <input type=file> at all until a "+"-style attach icon is clicked —
    # the widget shows "Resume/CV is required" with nothing to upload to.
    # Clicking that icon opens a "choose upload source" popup and reveals a
    # real (if visually overlaid/invisible) file input, which can then be
    # set directly like any other. Only run this if nothing was uploaded via
    # a plain visible input above, so this never double-uploads on ATS
    # platforms whose file input was already there from the start.
    try:
        _resume_doc = _resume_path(cfg, job.get("_resume_lang", "en"), company=job.get("company"))
        if _resume_doc and not await page.locator(
            "input[type='file']:not([disabled])"
        ).count():
            _attach_triggers = page.locator(
                "[class*='addAttachment'], [id*='attachIcon' i], "
                "[aria-label*='upload' i][role='button'], "
                "[title*='upload' i][role='button']"
            )
            for i in range(min(await _attach_triggers.count(), 3)):
                try:
                    trig = _attach_triggers.nth(i)
                    if not await trig.is_visible():
                        continue
                    await trig.click()
                    await asyncio.sleep(0.8)
                    revealed = page.locator("input[type='file']:not([disabled])")
                    if not await revealed.count():
                        continue
                    await revealed.first.set_input_files(str(_resume_doc))
                    await asyncio.sleep(1.5)
                    _emit("apply_step", {"url": url,
                          "step": f"  📎 Uploaded resume via attach dialog: {_resume_doc.name}"})
                    break
                except Exception:
                    pass
    except Exception:
        pass

    # ── Consent / GDPR checkboxes ─────────────────────────────────────────────
    try:
        _CONSENT = re.compile(
            r"\bagree\b|\bconfirm\b|\bauthorize\b|\bconsent\b|\backnowledge\b"
            r"|\bterms\b|\bprivacy\b|\bdatenschutz\b|\beinverstanden\b"
            # "Allow us to process your personal information" (verified live,
            # osapiens/Greenhouse-style Rails ATS) — a mandatory GDPR data-
            # processing checkbox phrased without any of the keywords above.
            r"|process.*(personal|your).*(information|data)|processing of.*data"
            r"|gdpr|data protection",
            re.I
        )
        for i in range(min(await page.locator("input[type='checkbox']").count(), 15)):
            try:
                cb = page.locator("input[type='checkbox']").nth(i)
                if not await cb.is_visible() or await cb.is_checked():
                    continue
                label = await _get_label(page, cb)
                if label and _CONSENT.search(label):
                    await cb.click()
                    await asyncio.sleep(0.3)
                    _emit("apply_step", {"url": url, "step": f"  ✎ Consent checkbox: {label[:50]}"})
            except Exception:
                pass
    except Exception:
        pass


# ── Validation error detection & repair ───────────────────────────────────────

async def _get_ext_error_texts(page: Page) -> list[str]:
    """Return visible validation error messages on the page."""
    errors: list[str] = []
    try:
        locs = page.locator(
            "[aria-invalid='true'], "
            "[class*='error']:not([class*='error-boundary']):not([class*='error-page']), "
            "[class*='invalid']:not([class*='invalid-feedback'] ~ *), "
            "[class*='validation-error'], "
            ".artdeco-inline-feedback--error, "
            "[data-test-form-element-error-message]"
        )
        for i in range(min(await locs.count(), 10)):
            try:
                txt = (await locs.nth(i).text_content() or "").strip()
                if txt and 3 < len(txt) < 200 and txt not in errors:
                    errors.append(txt)
            except Exception:
                pass
    except Exception:
        pass
    return errors


async def _fix_ext_validation_errors(
    page: Page,
    resume_text: str,
    profile: dict,
    job_desc: str,
    prior: list[dict],
) -> int:
    """
    Find fields currently showing validation errors and re-fill them with
    corrected answers.  Returns the number of fields successfully re-filled.
    """
    fixed = 0
    try:
        errored = await page.evaluate("""() => {
            const out = [];
            // type=password excluded everywhere below — never auto-fill an
            // authentication field. Account creation must be a human's call.
            const sels = [
                'input[aria-invalid="true"]:not([type=hidden]):not([type=password])',
                'textarea[aria-invalid="true"]',
                'select[aria-invalid="true"]',
                '[class*="error"]:not([class*="error-boundary"]) input:not([type=hidden]):not([type=password]):not([disabled])',
                '[class*="error"]:not([class*="error-boundary"]) textarea:not([disabled])',
                '[class*="error"]:not([class*="error-boundary"]) select:not([disabled])',
                '[class*="invalid"] input:not([type=hidden]):not([type=password]):not([disabled])',
                '[class*="invalid"] textarea:not([disabled])',
            ];
            for (const sel of sels) {
                for (const el of document.querySelectorAll(sel)) {
                    if (!el.offsetParent) continue;
                    const id = el.id || '';
                    const lbl = document.querySelector('label[for="' + id + '"]');
                    const label = lbl ? lbl.innerText.trim() :
                        (el.getAttribute('aria-label') || el.getAttribute('placeholder') || '');
                    if (!out.find(r => r.id && r.id === id)) {
                        out.push({ id, tag: el.tagName.toLowerCase(), label, type: el.type || '' });
                    }
                }
            }
            return out;
        }""")
    except Exception:
        return 0

    for fi in (errored or [])[:6]:
        fid   = fi.get("id", "")
        label = (fi.get("label") or "").strip() or "field"
        ftag  = fi.get("tag", "input")
        ftype = fi.get("type", "text")
        try:
            if not fid:
                continue
            if ftype == "password":
                # Never auto-fill an authentication field, no matter what
                # asked for it — account creation is the user's call, not
                # something this pipeline decides on its own. Flag it
                # clearly instead of silently skipping.
                _emit("apply_step", {"url": "",
                      "step": f"  🔒 Account creation required ({label[:40]}) — needs manual completion"})
                continue
            el = page.locator(f"#{fid}")
            if not await el.count() or not await el.first.is_visible():
                continue

            if ftag == "select":
                opts = [
                    o.strip() for o in await el.first.locator("option").all_text_contents()
                    if o.strip() and o.strip().lower() not in {"", "select", "please select",
                                                               "---", "choose", "bitte wählen"}
                ]
                if not opts:
                    continue
                answer = answer_custom_question(label, "select", opts, resume_text, profile, job_desc, prior_answers=prior)
                if answer:
                    try:
                        await el.first.select_option(label=answer)
                    except Exception:
                        await el.first.select_option(value=answer)
                    await el.first.dispatch_event("change")
                    fixed += 1
                    prior.append({"question": label, "answer": answer})
            else:
                eff_type = "textarea" if ftag == "textarea" else ("number" if ftype == "number" else "text")
                answer = answer_custom_question(label, eff_type, [], resume_text, profile, job_desc, prior_answers=prior)
                if answer:
                    if ftype == "number":
                        answer = re.sub(r"[^\d]", "", answer.split(".")[0]) or "1"
                    await el.first.click()
                    await asyncio.sleep(0.1)
                    await el.first.fill("")
                    await asyncio.sleep(0.1)
                    await _reliable_fill(page, el.first, answer)
                    # Fields like Greenhouse's Google-Places-backed "Location
                    # (City)" only register a real value when an actual
                    # suggestion is clicked — typed text alone leaves the
                    # hidden place-id unset, so validation keeps failing on
                    # every retry forever (verified live: this exact field
                    # got "fixed" and resubmitted 4 times, same error each
                    # time, because this repair path never picked a
                    # suggestion the way the initial fill pass does).
                    await _handle_autocomplete(page, el.first, answer)
                    fixed += 1
                    prior.append({"question": label, "answer": answer})
                    _emit("apply_step", {"url": "", "step": f"  🔧 Fixed: {label[:40]} → {answer[:40]}"})
        except Exception:
            pass
    return fixed


# ── Navigation helpers ────────────────────────────────────────────────────────

_SUBMIT_WORDS_RE = re.compile(
    r"\b(submit|apply|send|absenden|senden|bewerb|einreichen)\b", re.I
)


async def _click_next_or_submit(page: Page) -> str:
    """
    Find and click the most appropriate navigation button.
    Tries Next/Continue (multi-step) first, then Submit/Apply.
    Returns 'next', 'submit', or 'none'.
    """
    # ── Next / Continue (step forward without submitting) ────────────────────
    for sel in [
        "button:has-text('Next')",
        "button:has-text('Continue')",
        "button:has-text('Weiter')",
        "button:has-text('Next step')",
        "button:has-text('Nächster Schritt')",
        "button:has-text('Fortfahren')",
        "button:has-text('Proceed')",
        "a:has-text('Next')",
        "[aria-label*='next step' i]",
        "[data-testid*='next-btn' i]",
        "[data-test*='next-button' i]",
    ]:
        try:
            loc = page.locator(sel)
            if await loc.count() and await loc.first.is_visible():
                btn_text = (await loc.first.text_content() or "").strip()
                # Guard: skip if it looks like a submit/apply button
                if _SUBMIT_WORDS_RE.search(btn_text):
                    continue
                await loc.first.scroll_into_view_if_needed()
                await asyncio.sleep(0.3)
                await loc.first.click()
                return "next"
        except Exception:
            pass

    # ── Submit / Apply ────────────────────────────────────────────────────────
    for sel in [
        "button[type='submit']",
        "input[type='submit']",
        "button:has-text('Submit application')",
        "button:has-text('Submit')",
        "button:has-text('Apply now')",
        "button:has-text('Apply')",
        "button:has-text('Send application')",
        "button:has-text('Send')",
        "button:has-text('Complete application')",
        "button:has-text('Complete')",
        "button:has-text('Finish')",
        "button:has-text('Jetzt bewerben')",
        "button:has-text('Bewerben')",
        "button:has-text('Bewerbung absenden')",
        "button:has-text('Bewerbung senden')",
        "button:has-text('Absenden')",
        "button:has-text('Einreichen')",
        "button:has-text('Ja, das passt zu mir')",
        "a:has-text('Apply now')",
        "a:has-text('Bewerben')",
        "[data-test-submit-button]",
        "[data-testid*='submit' i]",
        "[data-testid*='apply-btn' i]",
        "[class*='submit-btn']",
        "[class*='apply-btn']",
    ]:
        try:
            loc = page.locator(sel)
            if await loc.count() and await loc.first.is_visible():
                await loc.first.scroll_into_view_if_needed()
                await asyncio.sleep(0.3)
                await loc.first.click()
                return "submit"
        except Exception:
            pass

    # ── Claude vision fallback ────────────────────────────────────────────────
    try:
        import base64 as _b64
        _api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if _api_key:
            _client_v = anthropic.Anthropic(api_key=_api_key)
            b64 = _b64.b64encode(await page.screenshot()).decode()
            resp = _client_v.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=120,
                messages=[{"role": "user", "content": [
                    {"type": "image", "source": {
                        "type": "base64", "media_type": "image/png", "data": b64}},
                    {"type": "text", "text":
                        "Find the button to advance this job application form "
                        "(Submit, Apply, Next, Continue, Weiter, Bewerben). "
                        "Return JSON only: "
                        '{"text":"exact button text","kind":"next_or_submit"} or {} if none.'}
                ]}]
            )
            raw = resp.content[0].text.strip()
            s, e = raw.find("{"), raw.rfind("}") + 1
            if s >= 0 and e > s:
                data = json.loads(raw[s:e])
                btn_text = data.get("text", "")
                if btn_text:
                    loc2 = page.locator(
                        f"button:has-text('{btn_text}'), a:has-text('{btn_text}')"
                    )
                    if await loc2.count() and await loc2.first.is_visible():
                        await loc2.first.scroll_into_view_if_needed()
                        await asyncio.sleep(0.3)
                        await loc2.first.click()
                        return "submit" if _SUBMIT_WORDS_RE.search(btn_text) else "next"
    except Exception:
        pass

    return "none"


async def _get_form_frame(page: Page):
    """
    Return the Playwright frame that contains the application form.

    Many ATS platforms (some Taleo, iCIMS, BambooHR, custom portals) embed
    their form inside an <iframe>.  Playwright exposes frames via page.frames;
    we find the first one that has at least 2 visible form fields and return it.
    Falls back to `page` itself if no iframe qualifies.
    """
    try:
        for frame in page.frames:
            if frame is page.main_frame:
                continue
            try:
                # A real application form has at least two interactive fields
                count = await frame.locator(
                    "input:not([type=hidden]):not([disabled]), "
                    "textarea:not([disabled]), select:not([disabled])"
                ).count()
                if count >= 2:
                    log.info("Form detected in iframe: %s", frame.url[:80])
                    _emit("apply_step", {"url": page.url,
                          "step": f"  🖼️ Form is inside an iframe — switching context"})
                    return frame
            except Exception:
                pass
    except Exception:
        pass
    return page  # no qualifying iframe found — use the top-level page


async def _run_ats_form(
    page: Page,
    job: dict,
    cfg: dict,
    resume_text: str,
    profile: dict,
    apply_type: str = "External",
    max_steps: int = 7,
) -> dict:
    """
    Multi-step ATS form runner shared by Greenhouse, Lever, Ashby, and generic handlers.

    Each iteration:
      1. Fill all visible fields (_fill_remaining_questions)
      2. Fix any visible validation errors (up to 2 cycles)
      3. Click Next or Submit
      4. On Submit: verify; on Next: loop

    Returns a standard result dict {success, manual, note, apply_type}.
    """
    url = page.url
    job_desc = job.get("description", "")
    prior: list[dict] = []

    # Resolve the frame that actually contains the form (may be an iframe)
    ctx = await _get_form_frame(page)
    # Wrap job with the frame's URL if we switched context
    _job_ctx = dict(job, url=getattr(ctx, "url", url))

    _last_url = page.url
    _stuck_count = 0  # consecutive steps where URL didn't change and action was "next"

    for step in range(max_steps):
        # Guard: if page navigated completely away (closed tab / redirect to unrelated domain)
        try:
            _cur_url = page.url
        except Exception:
            log.warning("Page closed mid-form at step %d", step)
            break
        _emit("apply_step", {"url": url, "step": f"  📋 Form step {step + 1}/{max_steps}…"})

        # 1. Fill all visible fields (use the frame context)
        await _fill_remaining_questions(ctx, _job_ctx, resume_text, profile, cfg)
        await asyncio.sleep(0.4)

        # 2. Fix validation errors (up to 2 repair passes before clicking anything)
        for _fp in range(2):
            n_fixed = await _fix_ext_validation_errors(ctx, resume_text, profile, job_desc, prior)
            if not n_fixed:
                break
            await asyncio.sleep(0.4)

        # 3. Click Next or Submit (try frame context first, then top-level page)
        action = await _click_next_or_submit(ctx)
        if action == "none" and ctx is not page:
            action = await _click_next_or_submit(page)

        if action == "none":
            _emit("apply_step", {"url": url, "step": "  ⚠ No navigation button found"})
            break

        _emit("apply_step", {"url": url, "step": f"  → Clicked '{action}' button on step {step + 1}"})

        # Wait for the page to settle (use top-level page for load state — frames don't have it)
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=10_000)
        except Exception:
            pass
        await asyncio.sleep(1.0)

        if action == "submit":
            try:
                await page.wait_for_load_state("networkidle", timeout=8_000)
            except Exception:
                pass

            if await _verify_submission(page):
                return {"success": True, "manual": False, "apply_type": apply_type}

            # Post-submit validation errors? Fix and retry once.
            errs = await _get_ext_error_texts(ctx)
            if errs:
                _emit("apply_step", {"url": url,
                      "step": f"  ⚠️ Post-submit errors: {errs[0][:80]}"})
                for _fp in range(2):
                    n_fixed = await _fix_ext_validation_errors(ctx, resume_text, profile, job_desc, prior)
                    if not n_fixed:
                        break
                    await asyncio.sleep(0.5)
                action2 = await _click_next_or_submit(ctx)
                if action2 == "none" and ctx is not page:
                    action2 = await _click_next_or_submit(page)
                if action2 == "submit":
                    try:
                        await page.wait_for_load_state("networkidle", timeout=8_000)
                    except Exception:
                        pass
                    if await _verify_submission(page):
                        return {"success": True, "manual": False, "apply_type": apply_type}

            # If there are still form fields, we either navigated to an apply form
            # (job listing → apply button click) or moved to the next wizard step.
            # Either way: keep filling instead of giving up.
            still_form = await ctx.locator(
                "input:not([type=hidden]):not([disabled]), "
                "textarea:not([disabled]), select:not([disabled])"
            ).count()
            if not still_form:
                # Check top-level page too
                try:
                    still_form = await page.locator(
                        "input:not([type=hidden]):not([disabled]), "
                        "textarea:not([disabled]), select:not([disabled])"
                    ).count()
                except Exception:
                    pass
            if not still_form:
                # No form anywhere = likely confirmation page — but the
                # verification above may have run before the page finished
                # redirecting/settling (verified live: a Rails-style ATS
                # navigated to a genuine confirmation page here, yet this
                # code used to just `break` without ever re-checking it,
                # silently falling through to "could not confirm submission"
                # even though its own reasoning said success was likely).
                # Give it one more, patient verification pass before giving up.
                try:
                    await page.wait_for_load_state("networkidle", timeout=5_000)
                except Exception:
                    pass
                if await _verify_submission(page):
                    return {"success": True, "manual": False, "apply_type": apply_type}
                break

            # Stuck detection: a genuine multi-page wizard changes URL on
            # each step. If "submit" left the URL unchanged for 2 consecutive
            # attempts while a form is still present, this isn't progress —
            # it's the same page re-rendering with a validation error we
            # can't resolve, and re-filling/re-submitting it up to max_steps
            # times just burns API calls in a loop (verified live: an
            # unresolved country-select error looped 11 times re-answering
            # itself before finally giving up). Escalate instead of looping.
            if page.url == _last_url:
                _stuck_count += 1
                if _stuck_count >= 2:
                    _emit("apply_step", {"url": url,
                          "step": "  ⚠ Form appears stuck (same URL after 2 submits) — handing off"})
                    break
            else:
                _stuck_count = 0
            _last_url = page.url

            # Re-resolve frame — multi-step forms sometimes swap iframes
            ctx = await _get_form_frame(page)
            _job_ctx = dict(job, url=getattr(ctx, "url", url))
            _emit("apply_step", {"url": url,
                  "step": f"  ↻ Submit navigated to new form — continuing fill…"})
            continue  # go back to top of loop and fill the new page

        # After a "next" click only do a cheap URL check — never run vision on
        # intermediate steps (it causes false positives on multi-step forms).
        if action == "next":
            url_now = page.url.lower()
            # Same hostname-vs-path fix as _verify_submission above — match
            # only the path+query so an ATS provider's own domain name
            # (e.g. successfactors.eu) can't masquerade as a confirmation.
            from urllib.parse import urlparse as _urlparse_next
            _url_now_parts = _urlparse_next(url_now)
            _url_now_tail = (_url_now_parts.path or "") + "?" + (_url_now_parts.query or "")
            _confirm_url_kw = [
                "thank", "thanks", "danke", "success", "erfolgreich",
                "confirm", "bestatig", "submitted", "bewerbung-eingegangen",
                "application-sent", "applied", "complete", "finished",
            ]
            if any(kw in _url_now_tail for kw in _confirm_url_kw):
                return {"success": True, "manual": False, "apply_type": apply_type}
            # Stuck detection: if URL hasn't changed for 2 consecutive "next" clicks,
            # validation errors are likely blocking us — break so vision fallback can try.
            if page.url == _last_url:
                _stuck_count += 1
                if _stuck_count >= 2:
                    _emit("apply_step", {"url": url,
                          "step": "  ⚠ Form appears stuck (same URL after 2 next clicks) — handing off"})
                    break
            else:
                _stuck_count = 0
            _last_url = page.url

    return {"success": False, "manual": True, "note": f"{apply_type}: could not confirm submission"}


# ── ATS handlers ──────────────────────────────────────────────────────────────

async def _apply_greenhouse(page: Page, job: dict, cfg: dict, resume_text: str, profile: dict) -> dict:
    url = page.url
    _emit("apply_step", {"url": url, "step": "🌿 Greenhouse — filling contact fields"})
    try:
        p = profile
        # Known Greenhouse field selectors — fill upfront before the generic loop
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
                    existing = await el.first.input_value()
                    if not existing.strip():
                        await el.first.fill(val)
                        await asyncio.sleep(0.2)
            except Exception:
                pass

        # Resume + cover letter upload
        await _upload_resume_and_cover_letter(page, job, cfg, url)

        # LinkedIn URL
        try:
            li = page.locator("input[id*='linkedin' i], input[placeholder*='LinkedIn' i]")
            if await li.count() and await li.first.is_visible():
                if not (await li.first.input_value()).strip():
                    await li.first.fill(p.get("linkedin_url", ""))
        except Exception:
            pass

        # Multi-step form loop: fill remaining + validate + Next/Submit
        return await _run_ats_form(page, job, cfg, resume_text, profile, "External (Greenhouse)")
    except PWTimeout:
        return {"success": False, "manual": False, "note": "Timeout"}
    except Exception as exc:
        return {"success": False, "manual": True, "note": str(exc)}


async def _apply_lever(page: Page, job: dict, cfg: dict, resume_text: str, profile: dict) -> dict:
    url = page.url
    _emit("apply_step", {"url": url, "step": "⚙️ Lever — filling contact fields"})
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

        # Resume + cover letter upload
        await _upload_resume_and_cover_letter(page, job, cfg, url)

        return await _run_ats_form(page, job, cfg, resume_text, profile, "External (Lever)")
    except PWTimeout:
        return {"success": False, "manual": False, "note": "Timeout"}
    except Exception as exc:
        return {"success": False, "manual": True, "note": str(exc)}


async def _apply_ashby(page: Page, job: dict, cfg: dict, resume_text: str, profile: dict) -> dict:
    url = page.url
    _emit("apply_step", {"url": url, "step": "🔷 Ashby — filling contact fields"})
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

        # Resume + cover letter upload
        await _upload_resume_and_cover_letter(page, job, cfg, url)

        return await _run_ats_form(page, job, cfg, resume_text, profile, "External (Ashby)")
    except PWTimeout:
        return {"success": False, "manual": False, "note": "Timeout"}
    except Exception as exc:
        return {"success": False, "manual": True, "note": str(exc)}


async def _apply_workday(page: Page, job: dict, cfg: dict, resume_text: str, profile: dict) -> dict:
    """Workday: try CSS form runner first (handles standard wizard steps), fall back to browser-use."""
    url = job.get("url", "")
    _emit("apply_step", {"url": url, "step": "⚙️ Workday — trying CSS form runner first…"})
    try:
        result = await _run_ats_form(page, job, cfg, resume_text, profile,
                                     apply_type="External (Workday)", max_steps=10)
        if result.get("success"):
            return result
        log.info("Workday CSS runner: %s — escalating to browser-use", result.get("note", ""))
    except Exception as _we:
        log.warning("Workday CSS runner error: %s", _we)
    _emit("apply_step", {"url": url, "step": "⚙️ Workday CSS incomplete — using AI agent"})
    return await _apply_generic_browser_use(page, job, cfg, resume_text, profile)


async def _apply_taleo(page: Page, job: dict, cfg: dict, resume_text: str, profile: dict) -> dict:
    """Taleo: try CSS form runner (works for guest/quick-apply instances), fall back to manual."""
    url = job.get("url", "")
    _emit("apply_step", {"url": url, "step": "⚙️ Taleo — checking for guest apply form…"})
    try:
        has_form = await page.locator(
            "input:not([type=hidden]):not([disabled]), "
            "textarea:not([disabled]), select:not([disabled])"
        ).count()
        if has_form:
            result = await _run_ats_form(page, job, cfg, resume_text, profile,
                                         apply_type="External (Taleo)", max_steps=8)
            if result.get("success"):
                return result
            log.info("Taleo CSS runner: %s", result.get("note", ""))
        else:
            log.info("Taleo: no visible form fields — login wall or unsupported page")
    except Exception as _te:
        log.warning("Taleo CSS runner error: %s", _te)
    _emit("apply_step", {"url": url, "step": "⚠️ Going to manual: Taleo (login-gated or unsupported)"})
    return {"success": False, "manual": True, "note": "Taleo requires manual apply (login-gated)"}


async def _apply_icims(page: Page, job: dict, cfg: dict, resume_text: str, profile: dict) -> dict:
    """iCIMS: try CSS form runner (works for public-facing forms), fall back to manual."""
    url = job.get("url", "")
    _emit("apply_step", {"url": url, "step": "⚙️ iCIMS — checking for public apply form…"})
    try:
        has_form = await page.locator(
            "input:not([type=hidden]):not([disabled]), "
            "textarea:not([disabled]), select:not([disabled])"
        ).count()
        if has_form:
            result = await _run_ats_form(page, job, cfg, resume_text, profile,
                                         apply_type="External (iCIMS)", max_steps=8)
            if result.get("success"):
                return result
            log.info("iCIMS CSS runner: %s", result.get("note", ""))
        else:
            log.info("iCIMS: no visible form fields — login wall or unsupported page")
    except Exception as _ie:
        log.warning("iCIMS CSS runner error: %s", _ie)
    _emit("apply_step", {"url": url, "step": "⚠️ Going to manual: iCIMS (login-gated or unsupported)"})
    return {"success": False, "manual": True, "note": "iCIMS requires manual apply (login-gated)"}


async def _apply_successfactors(page: Page, job: dict, cfg: dict, resume_text: str, profile: dict) -> dict:
    """SuccessFactors: standard-ish HTML forms — the shared CSS form runner
    already makes real, verified progress on these (first/last name, email,
    phone, LinkedIn, salary, notice period, cover-letter question, country,
    prior-employment, work eligibility all filled correctly in a live test)
    even before this handler existed, so use it instead of forcing manual."""
    url = page.url
    _emit("apply_step", {"url": url, "step": "🏢 SuccessFactors — filling form"})
    try:
        return await _run_ats_form(page, job, cfg, resume_text, profile,
                                   apply_type="External (SuccessFactors)", max_steps=10)
    except PWTimeout:
        return {"success": False, "manual": False, "note": "Timeout"}
    except Exception as exc:
        return {"success": False, "manual": True, "note": str(exc)}


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

0. Navigate to this URL first — the browser may start on a blank page with
   nothing loaded: {url}

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
                browser = _BUBrowser(cdp_url=_cdp_url)
                log.debug("browser-use: reusing CDP at %s", _cdp_url)
        except Exception as _cdp_err:
            log.debug("browser-use: CDP reuse failed (%s)", _cdp_err)

        if browser is None:
            browser = _BUBrowser(headless=cfg.get("headless", False))

        llm = _BUChatAnthropic(model="claude-sonnet-4-6")
        agent = _BUAgent(task=task, llm=llm, browser=browser)
        result = await agent.run(max_steps=40)

        final = (result.final_result() or "").lower()
        # The agent's own explanation of FAILURE routinely contains these same
        # positive keywords ("I was unable to complete the job application")
        # — verified live: this exact sentence got scored as a success purely
        # because "complete" appears in it. Check for negation/failure
        # language first and let it veto the positive match; a bare
        # substring check with no negation awareness isn't safe here.
        _negative_kw = [
            "unable to", "could not", "couldn't", "cannot", "can't",
            "did not", "didn't", "was not able", "wasn't able",
            "no url", "no form", "blank page", "about:blank",
            "failed to", "not provided", "please provide",
        ]
        _has_negative = any(kw in final for kw in _negative_kw)
        success = (not _has_negative) and any(kw in final for kw in [
            "success", "submitted", "applied", "complete", "confirmed",
            "application sent", "thank you", "danke"
        ])
        if success:
            _emit("apply_step", {"url": url, "step": "  ✓ browser-use: application submitted"})
            try:
                from applier.memory import get_memory
                get_memory().save_application_result(url, "browser-use", True, [])
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
    # Specific handlers with known HTML structure
    "greenhouse":     _apply_greenhouse,
    "lever":          _apply_lever,
    "ashby":          _apply_ashby,
    # Generic AI/browser-use handlers (no special structure known)
    "smartrecruiters":_apply_generic_browser_use,
    "workable":       _apply_generic_browser_use,
    "personio":       _apply_generic_browser_use,
    "recruitee":      _apply_generic_browser_use,
    "bamboohr":       _apply_generic_browser_use,
    "teamtailor":     _apply_generic_browser_use,
    "jazzhr":         _apply_generic_browser_use,
    "jobvite":        _apply_generic_browser_use,
    "stepstone":      _apply_generic_browser_use,
    "csod":           _apply_generic_browser_use,
    "softgarden":     _apply_generic_browser_use,
    "rexx":           _apply_generic_browser_use,
    "rippling":       _apply_generic_browser_use,
    "erecruiter":     _apply_generic_browser_use,
    "pinpoint":       _apply_generic_browser_use,
    # Special cases
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
    # Dismiss cookie/privacy consent popups before any form interaction
    await _dismiss_consent_popups(page)

    handler = PLATFORM_HANDLERS.get(platform)
    if handler and platform not in ("linkedin", "unknown"):
        new_url = page.url
        log.info("Routing to %s handler (URL: %s)", platform, new_url)
        return await handler(page, dict(job, url=new_url), cfg, resume_text, profile)

    # Unknown / custom ATS — try to land on the actual form first.
    # Many portals open on a job-description page; we must click their Apply button
    # before any form-fill logic runs.
    _navigated = await _click_apply_on_listing(page)

    # That click can land on a recognized ATS even though `platform` (detected
    # from the URL *before* this click) was "unknown" — verified live: a
    # company's own "shell" career page linked straight to SuccessFactors,
    # but the dedicated handler never ran because routing had already
    # committed to the generic path based on the pre-click URL. Re-detect
    # and route properly before falling through to the generic runner.
    if _navigated:
        _new_platform = detect_platform(page.url)
        _new_handler = PLATFORM_HANDLERS.get(_new_platform)
        if _new_handler and _new_platform not in ("linkedin", "unknown"):
            log.info("Re-routing to %s handler after listing-page click (URL: %s)",
                     _new_platform, page.url)
            return await _new_handler(page, dict(job, url=page.url), cfg, resume_text, profile)

    # Two-tier fallback:
    # Tier 1: CSS-selector form runner (_run_ats_form).  Fast, works on any standard
    #         HTML form (most custom company portals use standard elements).
    # Tier 2: Claude vision smart apply.  Slower but handles React/Angular SPAs and
    #         non-standard UI patterns.
    _emit("apply_step", {"url": job.get("url", ""),
          "step": f"🔍 Unknown/custom ATS — trying CSS form runner first…"})
    try:
        _css_result = await _run_ats_form(
            page, dict(job, url=page.url), cfg, resume_text, profile,
            apply_type="External (custom)", max_steps=12,
        )
        if _css_result.get("success"):
            return _css_result
        # If the CSS runner couldn't find a submit button at all (note contains
        # "could not confirm"), escalate to Claude vision.  If it DID click submit
        # but verification failed, still escalate — Claude vision may do better.
        log.info("CSS form runner: %s — escalating to Claude vision", _css_result.get("note", ""))
    except Exception as _ce:
        log.warning("CSS form runner error: %s", _ce)

    _emit("apply_step", {"url": job.get("url", ""),
          "step": "🧠 CSS runner could not complete — trying Claude vision smart apply…"})
    try:
        from applier.smart_filler import smart_apply_page
        _smart = await smart_apply_page(page, job, profile, resume_text, cfg)
        if _smart.get("success"):
            return _smart
    except Exception as _se:
        log.warning("smart_apply_page error: %s", _se)

    log.warning("DEBUG pre-manual: URL=%s platform=%s", page.url, platform)
    _emit("apply_step", {"url": job.get("url", ""),
          "step": f"⚠️ Going to manual: could not complete external apply ({platform})"})
    return {"success": False, "manual": True,
            "note": f"External apply — unknown platform: {platform}"}


async def _click_apply_on_listing(page: Page) -> bool:
    """
    If we're on a job-description / listing page (no form fields visible),
    find and click the Apply / Bewerben button to navigate to the actual form.
    Returns True if a button was clicked.
    """
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=8_000)
    except Exception:
        pass
    await asyncio.sleep(1.5)

    # Prefer a link whose href points at a known ATS domain — unambiguous
    # signal that this is a "shell" company career page fronting a real ATS,
    # regardless of what else is on the page. Checked before the form-count
    # heuristic below because that heuristic is flaky in practice (verified
    # live: a lingering cookie-consent widget's toggle inputs were enough to
    # trip "already on a form" and skip this click entirely, stranding the
    # flow on the shell page forever).
    try:
        _known_ats_href = ", ".join(
            f"a[href*='{d}']" for d in (
                "successfactors", "greenhouse.io", "lever.co", "ashbyhq.com",
                "myworkdayjobs.com", "smartrecruiters.com", "icims.com",
                "taleo.net", "bamboohr.com", "workable.com", "personio",
                "recruitee.com", "jazz.co", "resumatorjobs.com", "jobvite.com",
            )
        )
        _ats_link = page.locator(_known_ats_href)
        if await _ats_link.count() and await _ats_link.first.is_visible():
            _href = await _ats_link.first.get_attribute("href")
            log.info("_click_apply_on_listing: clicking known-ATS link -> %s", (_href or "")[:100])
            _emit("apply_step", {"url": page.url,
                  "step": f"  🔗 Job listing detected — following ATS link"})
            await _ats_link.first.scroll_into_view_if_needed()
            await asyncio.sleep(0.4)
            await _ats_link.first.click()
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=10_000)
            except Exception:
                pass
            await asyncio.sleep(2.0)
            return True
    except Exception:
        pass

    # Count visible form fields — if already on a form, do nothing
    try:
        form_count = await page.locator(
            "input:not([type=hidden]):not([disabled]),"
            "textarea:not([disabled]),select:not([disabled])"
        ).count()
        if form_count >= 2:
            return False  # already on a form
    except Exception:
        pass

    # Click the first visible Apply/Bewerben button
    _APPLY_SELS = [
        "a:has-text('Jetzt bewerben')",
        "button:has-text('Jetzt bewerben')",
        "a:has-text('jetzt bewerben')",
        "button:has-text('jetzt bewerben')",
        "a:has-text('Bewerben')",
        "button:has-text('Bewerben')",
        "a:has-text('Apply now')",
        "button:has-text('Apply now')",
        "a:has-text('Apply')",
        "button:has-text('Apply')",
        "a:has-text('Jetzt bewerben')",
        "[data-testid*='apply' i]",
        "[class*='apply-btn' i]",
        "[class*='bewerbung' i]",
    ]
    for sel in _APPLY_SELS:
        try:
            loc = page.locator(sel)
            if await loc.count() and await loc.first.is_visible():
                btn_text = (await loc.first.text_content() or "").strip()
                log.info("_click_apply_on_listing: clicking '%s'", btn_text[:40])
                _emit("apply_step", {"url": page.url,
                      "step": f"  🔗 Job listing detected — clicking '{btn_text[:40]}'"})
                await loc.first.scroll_into_view_if_needed()
                await asyncio.sleep(0.4)
                await loc.first.click()
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=10_000)
                except Exception:
                    pass
                await asyncio.sleep(2.0)
                return True
        except Exception:
            pass
    return False


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
