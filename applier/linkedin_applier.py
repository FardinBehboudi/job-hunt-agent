"""
linkedin_applier.py
Fill the LinkedIn Easy Apply wizard and answer custom form questions.

Public API:
  init_answer_cache(cfg)                    — call once per apply session
  fill_easy_apply(page, job, profile, resume_text, cfg) -> dict
  answer_custom_question(...)               — used by external_applier too
  _profile_summary(profile)                 — used by external_applier too
  _resume_path(cfg)                         — used by external_applier too
  _get_label(page, element)                 — used by external_applier too
  _reliable_fill(page, element, value)      — used by external_applier too
  _handle_autocomplete(page, inp, value)    — used by external_applier too
  is_numeric_question(label, inp_type)      — used by external_applier too
"""
import asyncio
import json
import logging
import os
import random
import re
from pathlib import Path

import anthropic
from playwright.async_api import Page, TimeoutError as PWTimeout

from applier.events import _emit
from applier.linkedin_clicker import MODAL_SEL

log = logging.getLogger(__name__)

_SUPPORT_DIR = Path(__file__).resolve().parent.parent / "uploads" / "support"

# ── Answer cache ──────────────────────────────────────────────────────────────
_answer_cache: dict[tuple, str] = {}
_answer_cache_file: Path | None = None


def init_answer_cache(cfg: dict) -> None:
    """Clear and reload the answer cache at the start of each apply session."""
    global _answer_cache_file
    _answer_cache.clear()
    _answer_cache_file = _build_answer_cache_path(cfg)
    _load_answer_cache(_answer_cache_file)
    log.debug("Answer cache initialised from %s", _answer_cache_file)


def _cache_key(question: str, field_type: str, options: list[str]) -> tuple:
    return (question.lower().strip(), field_type.lower(), tuple(sorted(o.lower() for o in options)))


def _build_answer_cache_path(cfg: dict) -> Path:
    log_file = cfg.get("log_file", "agent/applied_jobs_log.json")
    answers_file = str(log_file).replace(".json", "_answers.json")
    p = Path(answers_file)
    if not p.is_absolute():
        p = Path(__file__).resolve().parent / p
    return p


def _load_answer_cache(path: Path) -> None:
    if not path or not path.exists():
        return
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        loaded = 0
        for entry in raw:
            try:
                k = entry["key"]
                key = (k[0], k[1], tuple(k[2]))
                _answer_cache[key] = entry["answer"]
                loaded += 1
            except Exception:
                pass
        log.debug("Loaded %d cached answers from %s", loaded, path)
    except Exception as e:
        log.debug("Could not load answer cache: %s", e)


def _save_answer_cache_entry(key: tuple, answer: str) -> None:
    if not _answer_cache_file:
        return
    try:
        existing: list = []
        if _answer_cache_file.exists():
            try:
                existing = json.loads(_answer_cache_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        serialized_key = [key[0], key[1], list(key[2])]
        for entry in existing:
            try:
                if entry["key"] == serialized_key:
                    entry["answer"] = answer
                    break
            except Exception:
                pass
        else:
            existing.append({"key": serialized_key, "answer": answer})
        _answer_cache_file.parent.mkdir(parents=True, exist_ok=True)
        _answer_cache_file.write_text(
            json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        log.debug("Could not save answer cache entry: %s", e)


# ── Location / salary helpers ─────────────────────────────────────────────────

LOCATION_ALIASES: dict[str, list[str]] = {
    "germany":        ["germany", "deutschland", "de"],
    "berlin":         ["berlin"],
    "united states":  ["united states", "usa", "us", "u.s.a.", "u.s."],
    "united kingdom": ["united kingdom", "uk", "u.k.", "great britain"],
    "austria":        ["austria", "österreich", "at"],
    "switzerland":    ["switzerland", "schweiz", "ch"],
}


def _normalize_location(value: str, options: list[str]) -> str:
    value_lower = value.lower()
    for opt in options:
        if value_lower in opt.lower() or opt.lower() in value_lower:
            return opt
    for canonical, aliases in LOCATION_ALIASES.items():
        if any(a in value_lower for a in aliases):
            for opt in options:
                if any(a in opt.lower() for a in aliases):
                    return opt
    return options[0]


def _get_salary_answer(field_type: str, label: str, salary: str, options: list[str]) -> str:
    if field_type in ("textarea", "text"):
        return "Open to discussion based on total compensation and scope of the role."
    if field_type == "number":
        return re.sub(r"[^\d]", "", salary.split(".")[0]) or salary
    if field_type in ("select", "radio") and options:
        try:
            target = int(re.sub(r"[^\d]", "", salary.split(".")[0]) or "0")
        except ValueError:
            return options[-1]
        best: str | None = None
        best_diff = float("inf")
        for opt in options:
            digits = re.findall(r"\d+", opt.replace(",", ""))
            if digits:
                val = int(digits[0])
                if val < 1000:
                    val *= 1000
                diff = abs(val - target)
                if diff < best_diff:
                    best_diff = diff
                    best = opt
        return best if best else options[-1]
    return salary


# ── Custom question answering ─────────────────────────────────────────────────

_FAST_PATH_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"salary|gehalt|compensation|expected.*pay|pay.*expectation", re.I), "salary_expectation"),
    (re.compile(r"notice period|k.ndigungsfrist|when.*start|earliest.*start|availability", re.I), "notice_period"),
    (re.compile(r"years.*(experience|erfahrung)|experience.*years", re.I), "years_of_experience"),
    (re.compile(r"work.*(permit|authorization|authorisation|visa)|right.to.work|eligible.*work", re.I), "work_permit"),
    (re.compile(
        r"visa|sponsor|arbeitserlaubnis|sponsorship|"
        r"require.*sponsor|need.*visa|authorization to work|"
        r"legally authorized|recht zu arbeiten",
        re.I
    ), "work_permit"),
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
    prior_answers: list[dict] | None = None,
) -> str:
    # Accommodations questions — use a safe deterministic answer so LinkedIn's
    # "Please enter a valid answer" validation never triggers on these.
    _ACCOMM_KW = re.compile(r"\baccommodat\w*\b", re.I)
    if _ACCOMM_KW.search(question_text) and field_type in ("text", "textarea"):
        return "I don't require any special accommodations to participate in the interview process."

    # Cover letter / motivation letter → dedicated Sonnet generator
    _COVER_KW = re.compile(
        r"cover\s*letter|motivation\s*letter|anschreiben|motivationsschreiben|"
        r"motivational\s*letter|letter\s+of\s+motivation|bewerbungsschreiben|"
        r"why\s+(do\s+you\s+want|are\s+you\s+applying|this\s+role|this\s+company|this\s+position)",
        re.I
    )
    if _COVER_KW.search(question_text) and field_type in ("text", "textarea"):
        result = _generate_cover_letter(question_text, profile, resume_text, job_desc)
        if result:
            return result

    _SALARY_KW = re.compile(r"\bsalary\b|\bcompensation\b|\bpay\b|\bwage\b|\bctc\b|\brate\b|\bgehalt\b|vergütung", re.I)
    if _SALARY_KW.search(question_text):
        _sal = str(profile.get("salary_expectation", "75000"))
        return _get_salary_answer(field_type, question_text, _sal, options)

    _LOCATION_KW = re.compile(r"\bcountry\b|\bcitizenship\b|\bregion\b|\bnation\b|land\b|herkunft", re.I)
    if _LOCATION_KW.search(question_text) and field_type in ("select", "radio") and options:
        _loc_val = profile.get("current_location", "Berlin, Germany")
        return _normalize_location(str(_loc_val), options)

    for pattern, key in _FAST_PATH_PATTERNS:
        if pattern.search(question_text):
            if key == "years_of_experience" and re.search(r"\b(with|using|in)\b.{1,40}$", question_text, re.I):
                continue
            val = profile.get(key)
            if val is None:
                continue
            if key == "work_permit":
                if field_type in ("radio", "select") and options:
                    if re.search(r"need|require|sponsor|need.*visa|require.*visa", question_text, re.I):
                        for opt in options:
                            if re.search(r"^no$|not required|not needed|don.t need|do not need", opt, re.I):
                                return opt
                        return options[-1] if options else "No"
                    else:
                        # Question asks "are you authorized?" → pick the affirmative option
                        for opt in options:
                            if re.search(r"^yes$|^true$|authorized|eligible|citizen|permitted", opt, re.I):
                                return opt
                        # Last-resort: if options are ['true','false'] style, pick 'true'
                        for opt in options:
                            if opt.lower() in ("true", "yes", "1", "ja", "oui"):
                                return opt
                        return options[0] if options else "Yes"
                # Text/textarea fallback — distinguish "are you eligible?" from "do you need sponsorship?"
                if re.search(r"need|require|sponsor", question_text, re.I):
                    return "No, I am a German citizen and do not require visa sponsorship."
                return "Yes"  # eligible / authorized to work
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
        _prior_ctx = ""
        if prior_answers and field_type != "number":
            _prior_ctx = (
                "Previous answers on this page:\n"
                + "\n".join(
                    f"  Q: {pa['question'][:80]}  A: {pa['answer'][:80]}"
                    for pa in prior_answers[-5:]
                )
                + "\nUse these as context when answering the current question.\n\n"
            )

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
                _prior_ctx
                + f"You ARE {profile.get('first_name', 'the candidate')} {profile.get('last_name', '')} — "
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
        _save_answer_cache_entry(_ck, answer)
        return answer
    except Exception as exc:
        log.warning("answer_custom_question Claude call failed: %s", exc)
        return options[0] if options else ""


# ── Language detection ────────────────────────────────────────────────────────

_GERMAN_KW = re.compile(
    r"\bwir suchen\b|\bgesucht\b|\bihre aufgaben\b|\banforderungen\b|"
    r"\bkenntnisse\b|\bberufserfahrung\b|\bstellenangebot\b|"
    r"\bdeutschkenntnisse\b|\baufgaben\b|\bbewerbung\b|\beinstellung\b|"
    r"\bab sofort\b|\bvollzeit\b|\bteilzeit\b|\bgehalt\b|\bfestanstellung\b",
    re.I
)


def _detect_job_language(job: dict) -> str:
    """Return 'de' if the job description/title look German, else 'en'."""
    text = " ".join([
        job.get("title", ""),
        (job.get("description") or "")[:800],
    ])
    return "de" if len(_GERMAN_KW.findall(text)) >= 3 else "en"


# ── File helpers ──────────────────────────────────────────────────────────────

def _resume_path(cfg: dict, lang: str = "en") -> Path | None:
    key = "resume_de" if lang == "de" else "resume_en"
    p = cfg.get("paths", {}).get(key)
    if p and Path(str(p)).exists():
        return Path(str(p))
    default = Path(__file__).resolve().parent.parent / "uploads" / f"resume_{lang}.pdf"
    if default.exists():
        return default
    # Fall back to English if the German file isn't uploaded yet
    if lang == "de":
        fallback = cfg.get("paths", {}).get("resume_en")
        if fallback and Path(str(fallback)).exists():
            return Path(str(fallback))
        en_default = Path(__file__).resolve().parent.parent / "uploads" / "resume_en.pdf"
        return en_default if en_default.exists() else None
    return None


def _support_files() -> list[Path]:
    if not _SUPPORT_DIR.exists():
        return []
    return [p for p in sorted(_SUPPORT_DIR.iterdir())
            if p.is_file() and p.suffix.lower() in {".pdf", ".docx"}]


# ── Typing helpers ────────────────────────────────────────────────────────────

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


# ── Label detection ────────────────────────────────────────────────────────────

async def _get_label(page: Page, element) -> str:
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


# ── Page error extraction ──────────────────────────────────────────────────────

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


async def _retry_invalid_fields(
    page: Page,
    resume_text: str,
    profile: dict,
    job_desc: str,
    prior_answers: list[dict],
    cfg: dict | None = None,
) -> int:
    cfg = cfg or {}
    """
    Walk the DOM to find fields paired with visible validation errors, then
    clear and re-fill each one.  Returns the number of fields re-filled.
    """
    try:
        errored_fields = await page.evaluate("""() => {
            const ERROR_SELS = [
                '.artdeco-inline-feedback--error',
                '[data-test-form-element-error-message]',
                '.fb-form-element__error-field',
            ];
            const CONTAINER_SELS = [
                '.jobs-easy-apply-form-element',
                '.fb-dash-form-element',
                '.jobs-easy-apply-form-section__group',
                '[data-test-form-element]',
            ];
            const results = [];
            for (const errSel of ERROR_SELS) {
                for (const errEl of document.querySelectorAll(errSel)) {
                    if (!errEl.offsetParent) continue;
                    let container = null;
                    for (const cSel of CONTAINER_SELS) {
                        container = errEl.closest(cSel);
                        if (container) break;
                    }
                    if (!container) container = errEl.parentElement;
                    if (!container) continue;
                    const field = container.querySelector('textarea, input:not([type="hidden"])');
                    if (!field || !field.offsetParent) continue;
                    const id = field.id || '';
                    const tag = field.tagName.toLowerCase();
                    let label = field.getAttribute('aria-label') || field.getAttribute('placeholder') || '';
                    if (!label && id) {
                        const lbl = document.querySelector('label[for="' + id + '"]');
                        if (lbl) label = lbl.textContent.trim();
                    }
                    if (!results.find(r => r.id === id && id !== '')) {
                        results.push({ id, tag, label });
                    }
                }
            }
            return results;
        }""")
    except Exception:
        return 0

    fixed = 0
    for fi in (errored_fields or [])[:5]:
        fid   = fi.get("id", "")
        ftag  = fi.get("tag", "textarea")
        label = (fi.get("label") or "").strip()
        try:
            if not fid:
                continue
            el = page.locator(f"#{fid}")
            if not await el.count() or not await el.first.is_visible():
                continue
            new_answer = answer_custom_question(
                label or "field",
                "textarea" if ftag == "textarea" else "text",
                [], resume_text, profile, job_desc,
                prior_answers=prior_answers,
            )
            if not new_answer:
                continue
            ok = await fill_field_smart(page, el.first, new_answer,
                                        label=label, cfg=cfg)
            if not ok:
                await el.first.click()
                await asyncio.sleep(0.1)
                await el.first.fill("")
                await asyncio.sleep(0.1)
                await _reliable_fill(page, el.first, new_answer)
            _emit("apply_answer", {"label": label[:60], "answer": new_answer[:80]})
            prior_answers.append({"question": label, "answer": new_answer})
            fixed += 1
            await asyncio.sleep(0.4)
        except Exception:
            pass

    # Fix errored selects + Claude vision fallback
    try:
        for el in await page.locator(".artdeco-inline-feedback--error").all():
            try:
                p = el.locator("xpath=ancestor::*[4]")
                s = p.locator("select").first
                if await s.count() and await s.is_visible():
                    opts = [o.strip() for o in await s.locator("option").all_text_contents() if o.strip().lower() not in {"","select","please select","select an option"}]
                    if opts:
                        ans = answer_custom_question("field","select",opts,resume_text,profile,job_desc,prior_answers=prior_answers)
                        if ans:
                            try: await s.select_option(label=ans)
                            except: pass
                            await s.dispatch_event("change"); fixed += 1
            except: pass
    except: pass
    if fixed == 0:
        try:
            from applier.smart_filler import _claude_decide, _execute_actions
            _acts = await _claude_decide(page, profile, resume_text, job_desc, task="fill")
            if _acts: fixed += await _execute_actions(page, _acts, {})
        except: pass
    return fixed




# ══════════════════════════════════════════════════════════════════════════════
# UNIVERSAL FIELD HANDLER — handles every LinkedIn/ATS input type
# ══════════════════════════════════════════════════════════════════════════════

async def _detect_field_type(el) -> str:
    """Detect the true type of a form element."""
    try:
        tag  = await el.evaluate("e => e.tagName.toLowerCase()")
        typ  = (await el.get_attribute("type") or "").lower()
        role = (await el.get_attribute("role") or "").lower()
        cls  = (await el.get_attribute("class") or "").lower()
        if tag == "select":                      return "select"
        if tag == "textarea":                    return "textarea"
        if typ == "checkbox":                    return "checkbox"
        if typ == "radio":                       return "radio"
        if typ == "file":                        return "file"
        if typ == "number":                      return "number"
        if typ in ("date", "month", "year"):     return "date"
        if role == "combobox" or "combobox" in cls: return "typeahead"
        if "artdeco-combobox" in cls:            return "typeahead"
        if "typeahead" in cls:                   return "typeahead"
        return "text"
    except Exception:
        return "text"


async def _get_field_label(page: Page, el) -> str:
    """Extract the best label for a form element."""
    try:
        fid = await el.get_attribute("id") or ""
        # 1. explicit label
        if fid:
            lbl = page.locator(f"label[for='{fid}']")
            if await lbl.count():
                return (await lbl.first.text_content() or "").strip()
        # 2. aria-label
        a = (await el.get_attribute("aria-label") or "").strip()
        if a: return a
        # 3. placeholder
        p = (await el.get_attribute("placeholder") or "").strip()
        if p: return p
        # 4. parent legend/label
        txt = await el.evaluate("""e => {
            let p = e.parentElement;
            for (let i=0; i<4 && p; i++, p=p.parentElement) {
                const lbl = p.querySelector('label,legend,span[class*=label]');
                if (lbl) return lbl.innerText.trim();
            }
            return '';
        }""")
        return (txt or "").strip()
    except Exception:
        return ""


async def _react_fill(page: Page, el, value: str) -> bool:
    """Fill a React/Vue controlled input using the native property setter."""
    try:
        await page.evaluate("""([el, val]) => {
            const nativeInputSetter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value'
            ).set;
            nativeInputSetter.call(el, val);
            el.dispatchEvent(new Event('input',  {bubbles:true}));
            el.dispatchEvent(new Event('change', {bubbles:true}));
            el.dispatchEvent(new Event('blur',   {bubbles:true}));
        }""", [el, value])
        return True
    except Exception:
        return False


async def _fill_typeahead(page: Page, el, value: str) -> bool:
    """Fill a typeahead/combobox: type value, wait for dropdown, click option."""
    try:
        await el.click()
        await asyncio.sleep(0.2)
        await el.fill("")
        await asyncio.sleep(0.1)
        # Type slowly to trigger search
        for chunk in [value[:3], value[3:]]:
            if chunk:
                await el.type(chunk, delay=60)
                await asyncio.sleep(0.4)
        # Wait for dropdown
        dropdown = page.locator(
            ".artdeco-combobox__option, [role=option], "
            "[class*=typeahead] li, [class*=dropdown] li, "
            "[class*=suggestions] li"
        )
        await page.wait_for_timeout(600)
        if await dropdown.count():
            # Click best matching option
            for i in range(min(await dropdown.count(), 8)):
                opt = dropdown.nth(i)
                opt_text = (await opt.text_content() or "").strip().lower()
                if value.lower() in opt_text or opt_text in value.lower():
                    await opt.click()
                    return True
            # Click first option as fallback
            await dropdown.first.click()
            return True
        return False
    except Exception:
        return False


async def _fill_date(page: Page, el, value: str) -> bool:
    """Fill a date input — handles both text-format and native date pickers."""
    try:
        typ = (await el.get_attribute("type") or "").lower()
        if typ == "date":
            # Native date input expects YYYY-MM-DD
            import re as _re
            nums = _re.findall(r'\d+', value)
            if len(nums) >= 3:
                # Try to interpret as DD/MM/YYYY or MM/DD/YYYY
                y = next((n for n in nums if len(n) == 4), "2020")
                rest = [n for n in nums if len(n) != 4]
                m, d = (rest + ["1","1"])[:2]
                formatted = f"{y}-{m.zfill(2)}-{d.zfill(2)}"
                await el.fill(formatted)
                return True
        # Text date input — just type the value
        await el.fill(value)
        return True
    except Exception:
        return False


async def _fill_select_smart(page: Page, el, value: str) -> bool:
    """Fill a <select> with fuzzy matching."""
    try:
        opts = await el.locator("option").all_text_contents()
        opts_clean = [o.strip() for o in opts if o.strip()]

        # 1. Exact match
        try:
            await el.select_option(label=value)
            await el.dispatch_event("change")
            return True
        except Exception:
            pass

        # 2. Fuzzy match
        v_lower = value.lower()
        for opt in opts_clean:
            if v_lower in opt.lower() or opt.lower() in v_lower:
                try:
                    await el.select_option(label=opt)
                    await el.dispatch_event("change")
                    return True
                except Exception:
                    pass

        # 3. Partial word match
        v_words = set(v_lower.split())
        best, best_score = None, 0
        for opt in opts_clean:
            o_words = set(opt.lower().split())
            score = len(v_words & o_words)
            if score > best_score:
                best, best_score = opt, score
        if best and best_score > 0:
            try:
                await el.select_option(label=best)
                await el.dispatch_event("change")
                return True
            except Exception:
                pass
        return False
    except Exception:
        return False


async def _fill_radio_smart(page: Page, el, value: str) -> bool:
    """Click the right radio option based on value text."""
    try:
        name = await el.get_attribute("name") or ""
        radios = page.locator(f"input[type='radio'][name='{name}']") if name else page.locator("input[type='radio']")
        v_lower = value.lower().strip()
        yes_vals = {"yes","ja","true","1","oui"}
        no_vals  = {"no","nein","false","0","non"}

        for i in range(await radios.count()):
            radio = radios.nth(i)
            if not await radio.is_visible():
                continue
            rid = await radio.get_attribute("id") or ""
            lbl_text = ""
            if rid:
                lbl = page.locator(f"label[for='{rid}']")
                if await lbl.count():
                    lbl_text = (await lbl.first.text_content() or "").strip().lower()
            val_attr = (await radio.get_attribute("value") or "").lower()

            # Match: yes/no type
            if v_lower in yes_vals and (lbl_text in yes_vals or val_attr in yes_vals):
                await radio.click(force=True)
                return True
            if v_lower in no_vals and (lbl_text in no_vals or val_attr in no_vals):
                await radio.click(force=True)
                return True
            # Match: text overlap
            if v_lower and (v_lower in lbl_text or lbl_text in v_lower):
                await radio.click(force=True)
                return True

        # Fallback: click first if "yes" or first option
        if v_lower in yes_vals and await radios.count():
            await radios.first.click(force=True)
            return True
        return False
    except Exception:
        return False


async def fill_field_smart(
    page: Page, el, value: str,
    label: str = "", cfg: dict | None = None
) -> bool:
    """
    Universal field filler — detects field type and uses the correct strategy.
    Handles: text, textarea, number, select, radio, checkbox, date,
             typeahead/combobox, file upload, React controlled inputs.
    Returns True if filled successfully.
    """
    if not value:
        return False
    cfg = cfg or {}
    field_type = await _detect_field_type(el)

    try:
        if field_type == "file":
            # Resume upload — honour the job's language (de/en), fall back to EN
            _lang = cfg.get("_resume_lang", "en")
            resume = _resume_path(cfg, _lang)
            if resume and resume.exists():
                await el.set_input_files(str(resume))
                _emit("apply_answer", {"label": label[:60] or "Resume", "answer": resume.name})
                return True
            return False

        if field_type == "checkbox":
            checked = await el.is_checked()
            # For consent/agreement boxes always check; for others match value
            should_check = value.lower() in ("yes","true","1","agree","accepted","i agree","ja")
            if should_check and not checked:
                await el.click()
                return True
            if not should_check and checked:
                await el.click()
                return True
            return True  # already correct

        if field_type == "radio":
            return await _fill_radio_smart(page, el, value)

        if field_type == "select":
            ok = await _fill_select_smart(page, el, value)
            if ok:
                _emit("apply_answer", {"label": label[:60], "answer": value[:80]})
            return ok

        if field_type == "typeahead":
            ok = await _fill_typeahead(page, el, value)
            if ok:
                _emit("apply_answer", {"label": label[:60], "answer": value[:80]})
            return ok

        if field_type == "date":
            ok = await _fill_date(page, el, value)
            if ok:
                _emit("apply_answer", {"label": label[:60], "answer": value[:80]})
            return ok

        if field_type == "number":
            import re as _re
            num_val = _re.sub(r"[^\d]", "", str(value).split(".")[0]) or "1"
            value = num_val

        # text / textarea / email / tel — try multiple strategies
        filled = False

        # Strategy 1: standard fill
        try:
            await el.click()
            await asyncio.sleep(0.05)
            await el.fill("")
            await asyncio.sleep(0.05)
            await el.fill(value)
            current = await el.input_value()
            if current.strip() == value.strip():
                filled = True
        except Exception:
            pass

        # Strategy 2: keyboard typing (for React inputs that ignore fill)
        if not filled:
            try:
                await el.click()
                await asyncio.sleep(0.1)
                await page.keyboard.press("Control+a")
                await page.keyboard.press("Delete")
                await asyncio.sleep(0.05)
                for char in value:
                    await page.keyboard.type(char)
                    await asyncio.sleep(0.02)
                filled = True
            except Exception:
                pass

        # Strategy 3: React native setter
        if not filled:
            filled = await _react_fill(page, el, value)

        if filled:
            # Trigger all validation events
            try:
                await el.dispatch_event("input")
                await el.dispatch_event("change")
                await el.dispatch_event("blur")
            except Exception:
                pass
            await asyncio.sleep(0.15)

            # Verify fill worked
            try:
                current = await el.input_value()
                if not current.strip():
                    # Last resort: JavaScript direct set
                    await _react_fill(page, el, value)
            except Exception:
                pass

            _emit("apply_answer", {"label": label[:60], "answer": value[:80]})
            return True

        return False

    except Exception as e:
        log.debug("fill_field_smart error: %s | label=%s type=%s", e, label, field_type)
        return False


async def _fill_all_visible_fields(
    page: Page,
    resume_text: str,
    profile: dict,
    job_desc: str,
    cfg: dict,
    prior_answers: list[dict] | None = None,
) -> int:
    """
    Comprehensive pass: find ALL visible unfilled fields, answer them,
    fill them using fill_field_smart(). Returns count filled.
    """
    if prior_answers is None:
        prior_answers = []
    filled_count = 0

    # Collect all visible interactive elements
    selectors = [
        "input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=reset]):not([disabled])",
        "textarea:not([disabled])",
        "select:not([disabled])",
        "[role=combobox]:not([disabled])",
        ".artdeco-combobox__input",
    ]
    for sel in selectors:
        try:
            locs = page.locator(sel)
            count = await locs.count()
            for i in range(min(count, 30)):
                try:
                    el = locs.nth(i)
                    if not await el.is_visible():
                        continue

                    field_type = await _detect_field_type(el)
                    label = await _get_field_label(page, el)

                    # Skip already-filled fields
                    if field_type not in ("radio", "checkbox", "file"):
                        try:
                            current_val = await el.input_value()
                            if current_val.strip() and current_val.strip().lower() not in {
                                "select", "please select", "select an option", "choose",
                            }:
                                continue
                        except Exception:
                            pass

                    # Check memory first
                    value = None
                    try:
                        from applier.memory import get_memory
                        value = get_memory().get_answer(label, platform="linkedin")
                    except Exception:
                        pass

                    # Generate answer if not in memory
                    if not value:
                        if field_type == "select":
                            try:
                                opts = [o.strip() for o in await el.locator("option").all_text_contents()
                                        if o.strip().lower() not in {"", "select", "please select",
                                                                      "select an option", "auswählen"}]
                                if opts:
                                    value = answer_custom_question(
                                        label or "select", "select", opts,
                                        resume_text, profile, job_desc,
                                        prior_answers=prior_answers
                                    )
                            except Exception:
                                pass
                        elif field_type == "radio":
                            value = answer_custom_question(
                                label or "radio", "radio", ["Yes", "No"],
                                resume_text, profile, job_desc,
                                prior_answers=prior_answers
                            )
                        elif field_type in ("checkbox",):
                            value = "yes"  # consent boxes
                        elif field_type == "file":
                            value = "resume"
                        elif field_type == "date":
                            value = profile.get("graduation_date", "01/2020")
                        else:
                            value = answer_custom_question(
                                label or "field",
                                "textarea" if field_type == "textarea" else "text",
                                [], resume_text, profile, job_desc,
                                prior_answers=prior_answers
                            )

                    if not value:
                        continue

                    ok = await fill_field_smart(page, el, value, label=label, cfg=cfg)
                    if ok:
                        prior_answers.append({"question": label, "answer": str(value)})
                        try:
                            from applier.memory import get_memory
                            get_memory().save_qa(label, str(value), platform="linkedin")
                        except Exception:
                            pass
                        filled_count += 1
                        await asyncio.sleep(0.2)

                except Exception:
                    pass
        except Exception:
            pass

    return filled_count


# ── Profile field filler ───────────────────────────────────────────────────────

def _local_phone(phone: str, country_code: str = "+49") -> str:
    if phone.startswith(country_code):
        return phone[len(country_code):]
    if phone.startswith("+"):
        return re.sub(r"^\+\d{1,3}", "", phone)
    return phone


async def _fill_profile_fields(page: Page, profile: dict) -> None:
    url = ""
    import re as _re

    async def _force_fill(label: str, value: str, locators: list):
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
                        return
                    except Exception:
                        pass
            except Exception:
                pass

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

    # Fill phone country-code SELECT first (LinkedIn renders it before the number field)
    await _fill_phone_country_code(page, profile)

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

    fill_map = [
        ("Full name",        "input[name='name'], input[aria-label*='full name' i]",                                                                                   profile["full_name"]),
        ("LinkedIn",         "input[aria-label*='linkedin' i], input[id*='linkedin' i]",                                                                               profile["linkedin_url"]),
        ("GitHub",           "input[aria-label*='github' i], input[id*='github' i]",                                                                                   profile["github_url"]),
        ("Portfolio",        "input[aria-label*='portfolio' i], input[aria-label*='website' i]",                                                                       profile["portfolio_url"]),
        ("Salary",           "input[aria-label*='salary' i], input[aria-label*='expected salary' i], input[id*='salary' i]",                                           profile["salary_expectation"]),
        ("Notice period",    "input[aria-label*='notice period' i], input[aria-label*='notice' i], input[id*='notice' i]",                                             profile["notice_period"]),
        ("Years experience", "input[aria-label*='years of experience' i], input[aria-label*='experience' i]",                                                          profile["years_of_experience"]),
        ("Work permit",      "input[aria-label*='work authorization' i], input[aria-label*='work permit' i], input[aria-label*='visa' i], input[aria-label*='right to work' i]", profile["work_permit"]),
        ("Location",         "input[aria-label*='current city' i], input[aria-label*='current location' i], input[aria-label*='location' i]",                          profile["current_location"]),
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


async def _handle_email_dropdown(page: Page, profile: dict, url: str) -> None:
    try:
        email_select = None

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
            return

        target_email = profile["email"].strip().lower()
        options = await email_select.locator("option").all_text_contents()
        options = [o.strip() for o in options if o.strip()]

        _emit("apply_step", {"url": url, "step": f"  📧 Email options: {', '.join(options)}"})

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


# ── Resume / attachment helpers ────────────────────────────────────────────────

async def _select_or_upload_resume(page: Page, cfg: dict) -> bool:
    try:
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


async def _upload_resume(page: Page, cfg: dict, lang: str = "en") -> bool:
    resume = _resume_path(cfg, lang)
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


# ── Numeric question detection ─────────────────────────────────────────────────

def is_numeric_question(label: str, inp_type: str) -> bool:
    if inp_type == "number":
        return True
    if inp_type == "text" and re.search(
        r"how many|years of|years with|number of|anzahl|wie viele",
        label, re.I
    ):
        return True
    return False


# ── Visible question scanner ───────────────────────────────────────────────────

async def _answer_visible_questions(page: Page, resume_text: str, profile: dict, job_desc: str) -> None:
    prior_answers: list[dict] = []
    try:
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
                try:
                    from applier.memory import get_memory
                    answer = get_memory().get_answer(label_text, platform='linkedin') or ''
                except Exception:
                    answer = ''
                if not answer:
                    answer = answer_custom_question(
                        label_text, effective_type, [], resume_text, profile, job_desc,
                        prior_answers=prior_answers
                    )
                    if answer:
                        try:
                            from applier.memory import get_memory
                            get_memory().save_qa(label_text, answer, platform='linkedin')
                        except Exception:
                            pass
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
                    prior_answers.append({"question": label_text, "answer": answer})
            except Exception:
                pass

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
                answer = answer_custom_question(
                    label_text, "textarea", [], resume_text, profile, job_desc,
                    prior_answers=prior_answers
                )
                if answer:
                    await _reliable_fill(page, ta, answer)
                    _emit("apply_answer", {"label": label_text[:60], "answer": answer[:120]})
                    prior_answers.append({"question": label_text, "answer": answer})
            except Exception:
                pass

        _CONSENT_WORDS = re.compile(
            r"\bagree\b|\bconfirm\b|\bauthorize\b|\bauthorise\b|\bconsent\b|\backnowledge\b",
            re.I
        )
        try:
            checkboxes = page.locator("input[type='checkbox']")
            cb_count = await checkboxes.count()
            for i in range(min(cb_count, 20)):
                try:
                    cb = checkboxes.nth(i)
                    if not await cb.is_visible():
                        continue
                    is_checked = await cb.is_checked()
                    cb_label = await _get_label(page, cb)
                    if not cb_label:
                        try:
                            par_lbl = cb.locator("xpath=ancestor::label[1]")
                            if await par_lbl.count():
                                cb_label = (await par_lbl.first.text_content() or "").strip()
                        except Exception:
                            pass
                    if not cb_label:
                        continue
                    if is_checked:
                        continue
                    if _CONSENT_WORDS.search(cb_label):
                        try:
                            cb_id = await cb.get_attribute("id")
                            if cb_id:
                                lbl = page.locator(f"label[for='{cb_id}']")
                                if await lbl.count():
                                    await lbl.first.click()
                                else:
                                    await cb.click()
                            else:
                                await cb.click()
                            await asyncio.sleep(0.3)
                            _emit("apply_answer", {"label": cb_label[:60], "answer": "checked (consent)"})
                            prior_answers.append({"question": cb_label, "answer": "Yes (checked)"})
                        except Exception:
                            pass
                    else:
                        ai_answer = answer_custom_question(
                            cb_label, "checkbox", ["Yes", "No"],
                            resume_text, profile, job_desc,
                            prior_answers=prior_answers
                        )
                        if ai_answer.lower() in ("yes", "true", "1", "check", "checked"):
                            try:
                                cb_id = await cb.get_attribute("id")
                                if cb_id:
                                    lbl = page.locator(f"label[for='{cb_id}']")
                                    if await lbl.count():
                                        await lbl.first.click()
                                    else:
                                        await cb.click()
                                else:
                                    await cb.click()
                                await asyncio.sleep(0.3)
                                _emit("apply_answer", {"label": cb_label[:60], "answer": "checked (AI)"})
                                prior_answers.append({"question": cb_label, "answer": "Yes (checked)"})
                            except Exception:
                                pass
                except Exception:
                    pass
        except Exception:
            pass

        try:
            fieldsets = page.locator("fieldset")
            fs_count = await fieldsets.count()
            for i in range(min(fs_count, 10)):
                try:
                    fs = fieldsets.nth(i)
                    if not await fs.is_visible():
                        continue
                    radios = fs.locator("input[type='radio']")
                    r_count = await radios.count()
                    if not r_count:
                        continue
                    already_answered = False
                    for j in range(r_count):
                        try:
                            if await radios.nth(j).is_checked():
                                already_answered = True
                                break
                        except Exception:
                            pass
                    legend = fs.locator("legend")
                    question = (await legend.first.text_content() or "").strip() if await legend.count() else ""
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
                        option_values, resume_text, profile, job_desc,
                        prior_answers=prior_answers
                    )
                    clicked = False
                    for j in range(r_count):
                        try:
                            val = await radios.nth(j).get_attribute("value") or ""
                            if not (answer.lower() in val.lower() or val.lower() in answer.lower()):
                                continue
                            radio_id = await radios.nth(j).get_attribute("id")

                            for _dt_val in [answer, val]:
                                _dt_lbl = page.locator(
                                    f"label[data-test-text-selectable-option__label='{_dt_val}']"
                                )
                                if await _dt_lbl.count():
                                    await _dt_lbl.first.scroll_into_view_if_needed()
                                    await _dt_lbl.first.click(force=True)
                                    await asyncio.sleep(0.4)
                                    try:
                                        if await radios.nth(j).is_checked():
                                            clicked = True
                                    except Exception:
                                        clicked = True
                                    if clicked:
                                        break

                            if not clicked and radio_id:
                                lbl = page.locator(f"label[for='{radio_id}']")
                                if await lbl.count():
                                    await lbl.first.scroll_into_view_if_needed()
                                    await lbl.first.click()
                                    await asyncio.sleep(0.4)
                                    if await radios.nth(j).is_checked():
                                        clicked = True

                            if not clicked:
                                await radios.nth(j).scroll_into_view_if_needed()
                                await radios.nth(j).click(force=True)
                                await asyncio.sleep(0.4)
                                if await radios.nth(j).is_checked():
                                    clicked = True

                            if not clicked:
                                try:
                                    handle = await radios.nth(j).element_handle()
                                    await page.evaluate("""(el) => {
                                        el.click();
                                        el.dispatchEvent(new MouseEvent('click',
                                            {bubbles: true, cancelable: true}));
                                        el.dispatchEvent(new Event('change',
                                            {bubbles: true}));
                                    }""", handle)
                                    await asyncio.sleep(0.4)
                                    clicked = True
                                except Exception:
                                    pass

                            if clicked:
                                is_checked = False
                                try:
                                    is_checked = await radios.nth(j).is_checked()
                                except Exception:
                                    pass
                                _emit("apply_answer", {"label": (question or "radio")[:60], "answer": answer[:80]})
                                _emit("apply_step", {"url": "", "step":
                                    f"  [radio] '{(question or '')[:40]}' → '{answer}' checked={is_checked}"})
                                prior_answers.append({"question": question or "radio", "answer": answer})
                                break
                        except Exception:
                            pass
                    if not clicked:
                        log.warning("Radio group '%s' — no option matched answer '%s'",
                                    (question or "?")[:60], answer)
                except Exception as _fs_err:
                    log.warning("Fieldset %d failed: %s", i, _fs_err)
                    continue
        except Exception:
            pass

        # Final pass — re-click any fieldset still unchecked
        try:
            fieldsets = page.locator("fieldset")
            fs_count = await fieldsets.count()
            for i in range(min(fs_count, 10)):
                try:
                    fs = fieldsets.nth(i)
                    if not await fs.is_visible():
                        continue
                    radios = fs.locator("input[type='radio']")
                    r_count = await radios.count()
                    if not r_count:
                        continue
                    any_checked = False
                    for j in range(r_count):
                        try:
                            if await radios.nth(j).is_checked():
                                any_checked = True
                                break
                        except Exception:
                            pass
                    if not any_checked:
                        legend = fs.locator("legend")
                        q_txt = (await legend.first.text_content() or "").strip() if await legend.count() else f"group {i}"
                        _emit("apply_step", {"url": "", "step":
                            f"  ⚠ Radio group {i} still unchecked — retrying: '{q_txt[:50]}'"})
                        try:
                            handle = await radios.first.element_handle()
                            await page.evaluate("""(el) => {
                                el.click();
                                el.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true}));
                                el.dispatchEvent(new Event('change', {bubbles:true}));
                            }""", handle)
                            await asyncio.sleep(0.4)
                        except Exception:
                            pass
                except Exception:
                    pass
        except Exception:
            pass

        # Retry invalid fieldsets
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

        # Selects
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
                try:
                    selected_text = (await sel.evaluate(
                        "el => (el.options[el.selectedIndex] || {}).text || ''"
                    )).strip().lower()
                except Exception:
                    selected_text = ""
                if selected_text and selected_text not in _PLACEHOLDER_OPTION_TEXTS:
                    continue
                answer = answer_custom_question(
                    label_text or "", "select", options, resume_text, profile, job_desc,
                    prior_answers=prior_answers
                )
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
                        prior_answers.append({"question": label_text or "select", "answer": answer})
            except Exception:
                pass

    except Exception:
        pass


# ── Wizard lifecycle helpers ───────────────────────────────────────────────────

async def _dismiss_unfinished_application_dialog(page: Page) -> bool:
    """Handle LinkedIn's 'unfinished application' dialog (Continue / Discard).

    Clicks 'Continue' to preserve any data LinkedIn already pre-filled
    (name, email, phone).  Returns True if the dialog was found and handled.

    Only call this ONCE per job, right after the Easy Apply modal opens —
    the dialog never appears mid-wizard, so no need to poll inside the step loop.
    """
    try:
        # Poll twice (1 s total) — covers the case where the dialog takes a moment to render
        for _ms in range(2):
            discard = page.locator(
                "button[aria-label*='Discard' i], button:has-text('Discard')"
            )
            if await discard.count() and await discard.first.is_visible():
                # Dialog confirmed — click Continue to keep LinkedIn's pre-fills
                for cont_sel in [
                    "button[aria-label*='Continue' i]",
                    "button:has-text('Continue')",
                ]:
                    btn = page.locator(cont_sel)
                    if await btn.count() and await btn.first.is_visible():
                        await btn.first.click()
                        await asyncio.sleep(1.0)
                        log.info("Unfinished-application dialog dismissed — clicked Continue")
                        _emit("apply_step", {"url": "", "step": "  ↩ Continuing previous application"})
                        return True
                # Continue button not found — click Discard to start fresh
                await discard.first.click()
                await asyncio.sleep(1.0)
                log.info("Unfinished-application dialog dismissed — clicked Discard (no Continue found)")
                _emit("apply_step", {"url": "", "step": "  ↩ Starting fresh application (discarded previous)"})
                return True
            await asyncio.sleep(0.5)
    except Exception:
        pass
    return False


async def _dismiss_post_submit_dialogs(page: Page) -> None:
    """Dismiss the post-submission dialogs LinkedIn shows after a successful submit.

    Handles:
    - "Your application was sent" confirmation modal → click Done
    - "Follow [Company]" auto-checked checkbox → uncheck it
    - Generic profile-update / upsell overlays → dismiss
    """
    await asyncio.sleep(1.2)

    # 1. Click the "Done" button that closes the "application sent" modal
    for sel in [
        "button[aria-label='Done']",
        "button:has-text('Done')",
        "button[data-control-name='submit_unify_apply_close_btn']",
    ]:
        try:
            btn = page.locator(sel)
            if await btn.count() and await btn.first.is_visible():
                await btn.first.click()
                await asyncio.sleep(0.5)
                _emit("apply_step", {"url": "", "step": "  ✓ Closed post-submit confirmation"})
                break
        except Exception:
            pass

    # 2. Uncheck "Follow [Company]" — LinkedIn auto-checks it, we don't want spam
    try:
        follow_chk = page.locator(
            "input[type='checkbox'][aria-label*='follow' i], "
            "input[type='checkbox'][id*='follow-company' i]"
        )
        if await follow_chk.count() and await follow_chk.first.is_visible():
            if await follow_chk.first.is_checked():
                await follow_chk.first.click()
                await asyncio.sleep(0.3)
                log.debug("Unchecked 'Follow company' post-submit")
    except Exception:
        pass

    # 3. Dismiss any remaining overlay (profile update prompt, upsell, etc.)
    for sel in [
        "button[aria-label='Dismiss']",
        ".artdeco-modal__dismiss",
        "button[data-test-modal-close-btn]",
    ]:
        try:
            btn = page.locator(sel)
            if await btn.count() and await btn.first.is_visible():
                await btn.first.click()
                await asyncio.sleep(0.4)
        except Exception:
            pass


async def _fill_phone_country_code(page: Page, profile: dict) -> None:
    """Fill the phone country-code <select> that LinkedIn renders before the phone field.

    Derives the target dial code from the profile phone number prefix (e.g. '+49').
    Falls back to '+49' (Germany) if the prefix cannot be parsed.
    """
    try:
        phone = profile.get("phone", "")
        if phone.startswith("+"):
            m = re.match(r"(\+\d{1,3})", phone)
            target_code = m.group(1) if m else "+49"
        else:
            target_code = "+49"  # default: Germany

        cc_sel = page.locator(
            "select[id*='phoneCountry' i], select[id*='phone-country' i], "
            "select[name*='phoneCountry' i], select[id*='countryCode' i], "
            "select[aria-label*='country code' i], select[aria-label*='phone country' i], "
            "select[data-test-phone-country-code]"
        )
        if not await cc_sel.count():
            return
        sel_el = cc_sel.first
        if not await sel_el.is_visible():
            return

        opts = await sel_el.locator("option").all_text_contents()
        for opt in opts:
            if target_code in opt:
                try:
                    await sel_el.select_option(label=opt)
                    await sel_el.dispatch_event("change")
                    _emit("apply_step", {"url": "", "step": f"  ✎ Phone country code: {opt.strip()[:30]}"})
                    return
                except Exception:
                    pass
    except Exception:
        pass


def _generate_cover_letter(
    question_text: str,
    profile: dict,
    resume_text: str,
    job_desc: str,
) -> str:
    """Generate a job-specific cover letter using claude-sonnet-4-6.

    Uses the answer cache so Sonnet is only called once per unique question label.
    """
    _ck = _cache_key(question_text, "cover_letter", [])
    if _ck in _answer_cache:
        log.debug("Cover letter cache hit for: %r", question_text[:50])
        return _answer_cache[_ck]

    try:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            return ""
        client = anthropic.Anthropic(api_key=api_key)
        system = (
            f"You ARE {profile.get('first_name', '')} {profile.get('last_name', '')} "
            "writing a genuine, concise job application cover letter. "
            "Write in first person. Exactly 3 short paragraphs:\n"
            "1. Why this specific role/company excites you, connecting to your background.\n"
            "2. 2-3 concrete skills or achievements from your experience that match the job.\n"
            "3. A brief professional closing (no 'I look forward to hearing from you' clichés).\n"
            "Maximum 220 words. No 'Dear Hiring Manager' salutation. No placeholder text. "
            "No meta-commentary about the letter itself."
        )
        user = (
            f"Question/label: {question_text}\n\n"
            f"Job description:\n{job_desc[:900]}\n\n"
            f"My profile:\n{_profile_summary(profile)}\n\n"
            f"My resume:\n{resume_text[:900]}"
        )
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=450,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        answer = resp.content[0].text.strip()
        _answer_cache[_ck] = answer
        _save_answer_cache_entry(_ck, answer)
        log.info("Cover letter generated (%d chars)", len(answer))
        return answer
    except Exception as exc:
        log.warning("Cover letter generation failed: %s", exc)
        return ""


# ── Easy Apply wizard ──────────────────────────────────────────────────────────

async def fill_easy_apply(
    page: Page,
    job: dict,
    profile: dict,
    resume_text: str,
    cfg: dict,
    *,
    apply_type: str = "Easy Apply",
) -> dict:
    """Drive the LinkedIn Easy Apply multi-step wizard to completion."""
    job_id = job.get("scraped_id", job.get("id", "unknown"))

    _full_page_apply = "/jobs/apply/" in page.url or "/apply?" in page.url
    _modal_el = page.locator(MODAL_SEL).first
    try:
        _modal_visible = await _modal_el.is_visible()
    except Exception:
        _modal_visible = False

    _ws = _modal_el if _modal_visible else page
    log.info("Wizard scope: %s | full_page=%s",
             "modal" if _modal_visible else "full-page", _full_page_apply)

    try:
        await page.wait_for_selector(
            "input[type='text']:not([disabled]), input[type='tel']:not([disabled]), "
            "input[type='email']:not([disabled]), select:not([disabled])",
            timeout=8000, state="visible",
        )
        await page.wait_for_timeout(500)
    except Exception:
        await page.wait_for_timeout(2000)

    # Handle "unfinished application" dialog that LinkedIn shows before the wizard starts
    await _dismiss_unfinished_application_dialog(page)

    await _handle_email_dropdown(page, profile, job["url"])
    await _fill_profile_fields(page, profile)

    _resume_lang = job.get("_resume_lang", "en")
    _prev_page_labels: list[str] = []
    _stuck_count = 0
    step_n = 0
    prior_answers: list[dict] = []
    _resume_selected = False  # cache: only scan for resume once it's confirmed selected
    for step_n in range(12):
        # Refresh wizard scope on every step — modal changes between pages
        try:
            _modal_visible = await _modal_el.is_visible()
        except Exception:
            _modal_visible = False
        _ws = _modal_el if _modal_visible else page

        await _maybe_attach_support_docs(page)
        if not _resume_selected:
            _resume_selected = await _select_or_upload_resume(page, cfg)
            if not _resume_selected:
                _resume_selected = await _upload_resume(page, cfg, _resume_lang)

        _BORING_LABELS = {"select language", "select an option", "upload resume",
                          "upload a resume", "change resume"}
        visible_labels = []
        try:
            for inp in await _ws.query_selector_all(
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

        if visible_labels and visible_labels == _prev_page_labels:
            _stuck_count += 1
            if _stuck_count == 1:
                # First time stuck — try Claude vision
                _emit("apply_step", {"url": job["url"],
                      "step": "  🧠 Stuck — activating Claude vision filler…"})
                try:
                    from applier.smart_filler import smart_fill_form
                    _smart_r = await smart_fill_form(
                        page, profile, resume_text,
                        job.get('description', ''), cfg, max_rounds=2
                    )
                    if _smart_r.get('success'):
                        return {"success": True, "manual": False,
                                "note": _smart_r['note'], "apply_type": apply_type}
                    _stuck_count = 0  # reset and try normal flow again
                except Exception as _sf_err:
                    log.warning("smart_fill_form error: %s", _sf_err)
            if _stuck_count >= 2:
                log.warning("Wizard stuck on same page (step %d) — giving up", step_n + 1)
                break
        else:
            _stuck_count = 0
        _prev_page_labels = visible_labels

        # Use comprehensive field handler (handles all types + validation)
        _n_filled = await _fill_all_visible_fields(
            page, resume_text, profile, job.get("description", ""), cfg,
            prior_answers=prior_answers
        )
        if _n_filled:
            _emit("apply_step", {"url": job["url"],
                  "step": f"  ✎ Filled {_n_filled} field(s) on page {step_n+1}"})
        # Also run legacy handler for any missed fields
        await _answer_visible_questions(page, resume_text, profile, job.get("description", ""))

        _sub_exact = _ws.get_by_label("Submit application", exact=True)
        submit = _sub_exact if await _sub_exact.count() else _ws.locator(
            "button:has-text('Submit application'), button:has-text('Submit')"
        )
        _nxt_exact = _ws.get_by_label("Continue to next step", exact=True)
        _rev_exact = _ws.get_by_label("Review your application", exact=True)
        if await _nxt_exact.count() and await _nxt_exact.first.is_visible():
            nxt = _nxt_exact
        elif await _rev_exact.count() and await _rev_exact.first.is_visible():
            nxt = _rev_exact
        else:
            nxt = _ws.locator(
                "button:has-text('Next'), button:has-text('Continue'), "
                "button:has-text('Review'), button:has-text('Weiter'), "
                "button:has-text('Fortfahren')"
            )

        # Trigger blur/change so LinkedIn shows validation errors BEFORE we click
        try:
            await page.evaluate("""
                () => { document.querySelectorAll(
                    'input:not([type=hidden]):not([type=file]):not([disabled]),'
                    +'textarea:not([disabled]),select:not([disabled])'
                ).forEach(f => { if (f.offsetParent) {
                    f.dispatchEvent(new Event('blur',   {bubbles:true}));
                    f.dispatchEvent(new Event('change', {bubbles:true}));
                    f.dispatchEvent(new Event('input',  {bubbles:true}));
                }})}
            """)
            await asyncio.sleep(0.4)
        except Exception:
            pass

        # Pre-click validation check
        _pre_errors = await _get_page_errors(page)
        if _pre_errors:
            _emit("apply_step", {"url": job["url"],
                  "step": f"  ⚠️ Validation: {'; '.join(_pre_errors[:2])[:120]}"})
            _fixed_pre = await _retry_invalid_fields(
                page, resume_text, profile, job.get("description", ""), prior_answers=prior_answers
            )
            if _fixed_pre:
                await asyncio.sleep(0.5)
                try:
                    await page.evaluate("""
                        () => { document.querySelectorAll('input,textarea,select')
                            .forEach(f => { if(f.offsetParent){
                                f.dispatchEvent(new Event('blur',{bubbles:true}));
                                f.dispatchEvent(new Event('change',{bubbles:true}));
                            }}); }
                    """)
                    await asyncio.sleep(0.3)
                except Exception:
                    pass

        if await submit.count():
            _emit("apply_step", {"url": job["url"], "step": "Submitting application…"})
            await submit.first.scroll_into_view_if_needed()
            await asyncio.sleep(0.5)
            await submit.first.click()
            await asyncio.sleep(random.uniform(0.8, 1.5))
            await _dismiss_post_submit_dialogs(page)
            try:
                from applier.memory import get_memory
                get_memory().save_application_result(
                    job.get("url",""), "linkedin", True, prior_answers
                )
            except Exception:
                pass
            return {"success": True, "manual": False, "note": "", "apply_type": apply_type}
        elif await nxt.count() or True:  # always enter; fallback to page search
            if not await nxt.count():
                nxt = page.locator(
                    "button[aria-label='Continue to next step'],"
                    "button[aria-label='Review your application'],"
                    "button:has-text('Next'),button:has-text('Continue'),"
                    "button:has-text('Review'),button:has-text('Weiter'),"
                    "button:has-text('Fortfahren'),button:has-text('Submit')"
                )
                if not await nxt.count():
                    # Claude vision fallback — find and click the right button
                    try:
                        from applier.smart_filler import _claude_decide, _execute_actions
                        _nav_acts = await _claude_decide(page, profile, resume_text,
                                                         job.get('description',''), task='submit')
                        if _nav_acts:
                            await _execute_actions(page, _nav_acts, cfg)
                            await asyncio.sleep(1.5)
                            continue
                    except Exception:
                        pass
                    log.warning("No Next button found at step %d", step_n + 1)
                    break
            nxt_text = (await nxt.first.text_content() or "Next").strip()
            _emit("apply_step", {"url": job["url"], "step": f"Page {step_n + 1} → clicking '{nxt_text}'"})
            await nxt.first.scroll_into_view_if_needed()
            await asyncio.sleep(0.5)
            await nxt.first.click()
            await asyncio.sleep(random.uniform(0.8, 1.5))
            _step_errs = await _get_page_errors(page)
            if _step_errs:
                for _se in _step_errs:
                    _emit("apply_step", {"url": job["url"], "step": f"  ⚠️ Validation: {_se[:100]}"})
                # Fix-and-retry: re-fill error fields and click Next again (up to 2 cycles)
                for _fix_n in range(2):
                    _fixed = await _retry_invalid_fields(
                        page, resume_text, profile, job.get("description", ""),
                        prior_answers=prior_answers, cfg=cfg
                    )
                    _emit("apply_step", {"url": job["url"],
                          "step": f"  🔧 Re-filled {_fixed} errored field(s)"})
                    if not _fixed:
                        break
                    await asyncio.sleep(0.8)
                    try:
                        _nxt_r = _ws.get_by_label("Continue to next step", exact=True)
                        if not (await _nxt_r.count() and await _nxt_r.first.is_visible()):
                            _nxt_r = _ws.locator(
                                "button:has-text('Next'), button:has-text('Continue'), "
                                "button:has-text('Weiter'), button:has-text('Fortfahren')"
                            )
                        if await _nxt_r.count() and await _nxt_r.first.is_visible():
                            await _nxt_r.first.click()
                            await asyncio.sleep(random.uniform(0.8, 1.5))
                    except Exception:
                        pass
                    _step_errs = await _get_page_errors(page)
                    if not _step_errs:
                        _emit("apply_step", {"url": job["url"],
                              "step": "  ✅ Validation errors resolved"})
                        _stuck_count = 0
                        break
                    for _se in _step_errs:
                        _emit("apply_step", {"url": job["url"],
                              "step": f"  ⚠️ Still: {_se[:80]}"})
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
