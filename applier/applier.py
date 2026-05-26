"""
applier.py — session loop and thin per-job dispatcher.

Platform detection → route to linkedin or external handler.
All form-fill logic lives in linkedin_applier.py and external_applier.py.
SSE events emitted on _event_queue (re-exported from apply_events).
"""

import asyncio
import hashlib
import json
import logging
import os
import random
import re
import threading
import traceback
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from playwright.async_api import async_playwright, Page, TimeoutError as PWTimeout

try:
    from jobspy import scrape_jobs as _jobspy_scrape
    _JOBSPY_OK = True
except ImportError:
    _jobspy_scrape = None  # type: ignore
    _JOBSPY_OK = False

import anthropic
from dedup import db as _db
from core.config import load_config
from applier.events import _event_queue, _emit
from applier.linkedin_clicker import click_apply_button, dismiss_overlays
from applier.linkedin_applier import fill_easy_apply, init_answer_cache, _resume_path, _detect_job_language
from applier.external_applier import detect_platform, follow_external_apply, PLATFORM_HANDLERS

load_dotenv()
log = logging.getLogger(__name__)

_MAX_PER_SESSION = 10
_DELAY_MIN       = 1.0
_DELAY_MAX       = 3.5
_SESSION_FILE    = Path(__file__).resolve().parent.parent / "uploads" / "linkedin_session.json"

# ── Job-match score cache (keyed by MD5 of description; in-memory per process) ─
_score_cache: dict[str, dict] = {}

_LOG_FILE = Path(__file__).resolve().parent.parent / "uploads" / "applied_jobs_log.json"


def _log_to_json(job: dict, apply_type: str, resume_used: str) -> None:
    """Append a successful application to applied_jobs_log.json."""
    try:
        data: dict = {"applied_jobs": []}
        if _LOG_FILE.exists():
            try:
                data = json.loads(_LOG_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        data.setdefault("applied_jobs", [])
        data["applied_jobs"].append({
            "company":    job.get("company", ""),
            "title":      job.get("title", ""),
            "url":        job.get("url", ""),
            "apply_type": apply_type,
            "resume_used": Path(resume_used).name if resume_used else "",
            "timestamp":  datetime.utcnow().isoformat() + "Z",
            "status":     "applied",
        })
        _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        _LOG_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        log.warning("_log_to_json failed: %s", exc)


# ── Utilities ─────────────────────────────────────────────────────────────────

async def _delay(extra: float = 0.0) -> None:
    await asyncio.sleep(random.uniform(_DELAY_MIN, _DELAY_MAX) + extra)


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


# ── LinkedIn thin wrapper ─────────────────────────────────────────────────────

async def _apply_linkedin(page: Page, job: dict, cfg: dict, resume_text: str, profile: dict) -> dict:
    job_id = job.get("scraped_id", job.get("id", "unknown"))
    try:
        await page.goto(job["url"], wait_until="domcontentloaded", timeout=30_000)

        if "linkedin.com/login" in page.url or "linkedin.com/authwall" in page.url:
            log.error("LinkedIn session expired — need to re-login (URL: %s)", page.url)
            return {"success": False, "manual": False, "note": "LinkedIn session expired — re-login required"}

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
            _emit("apply_step", {"url": job.get("url", ""), "step": "️ Going to manual: CAPTCHA on LinkedIn"})
            return {"success": False, "manual": True, "note": "CAPTCHA on LinkedIn"}

        try:
            _ss_path = str(Path(__file__).resolve().parent.parent / "uploads" / f"debug_pw_view_{job_id}.png")
            await page.screenshot(path=_ss_path, full_page=False)
            log.info("📸 Debug screenshot saved: %s", _ss_path)
        except Exception as _ss_exc:
            log.debug("Screenshot failed: %s", _ss_exc)

        await page.wait_for_timeout(3000)
        await page.evaluate("window.scrollTo(0, 0)")
        await page.wait_for_timeout(1000)

        n_dismissed = await dismiss_overlays(page)
        if n_dismissed:
            await page.wait_for_timeout(500)

        click_result = await click_apply_button(page)
        outcome = click_result
        log.info("click_apply_button result: %s", outcome)

        if outcome == "already":
            _emit("apply_step", {"url": job.get("url", ""), "step": "Already applied — skipping"})
            return {"success": True, "manual": False, "note": "Already applied", "apply_type": "Already"}

        if outcome == "not_found":
            try:
                _ss = Path(__file__).resolve().parent.parent / "uploads" / f"debug_apply_{job_id}.png"
                _ss.write_bytes(await page.screenshot(full_page=True))
            except Exception:
                pass
            _emit("apply_step", {"url": job.get("url", ""), "step": "️ Going to manual: No Apply button found"})
            return {"success": False, "manual": True, "note": "No Apply button found"}

        if outcome == "modal_failed":
            _emit("apply_step", {"url": job.get("url", ""),
                "step": "️ Going to manual: Easy Apply button clicked but modal never opened"})
            return {"success": False, "manual": True, "note": "Easy Apply modal never opened after click"}

        try:
            await page.screenshot(path=str(Path(__file__).resolve().parent.parent / "uploads" / f"debug_after_click_{job_id}.png"), full_page=False)
        except Exception:
            pass

        if outcome == "external":
            return await follow_external_apply(page, job, profile, resume_text, cfg)

        return await fill_easy_apply(page, job, profile, resume_text, cfg)

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


# ── Pipeline helpers ──────────────────────────────────────────────────────────

async def _scrape_jobs_jobspy(cfg: dict) -> list[dict]:
    """Scrape jobs via JobSpy (LinkedIn + Indeed + Glassdoor). Returns job dicts."""
    if not _JOBSPY_OK:
        log.warning("JobSpy not installed — skipping (pip install python-jobspy)")
        return []

    roles    = cfg.get("roles", ["Backend Java Engineer"])
    location = (cfg.get("locations") or ["Berlin"])[0]
    hours    = str(cfg.get("posted_limit", "24h")).replace("h", "")
    companies: list[str] = cfg.get("target_companies") or []

    search_terms: list[str] = list(roles)
    for company in companies:
        for role in roles:
            search_terms.append(f"{role} {company}")

    all_jobs: list[dict] = []

    def _run_scrape(term: str) -> list[dict]:
        try:
            df = _jobspy_scrape(
                site_name=["linkedin", "indeed", "glassdoor"],
                search_term=term,
                location=location,
                results_wanted=50,
                hours_old=int(hours),
                is_remote=False,
                country_indeed="Germany",
                linkedin_fetch_description=True,
                verbose=0,
            )
            if df is None or df.empty:
                return []
            out = []
            for _, row in df.iterrows():
                out.append({
                    "id":          str(row.get("id", "")),
                    "title":       str(row.get("title", "")),
                    "company":     str(row.get("company", "")),
                    "location":    str(row.get("location", "")),
                    "url":         str(row.get("job_url", "")),
                    "description": str(row.get("description", "")),
                    "source":      str(row.get("site", "jobspy")),
                    "is_remote":   bool(row.get("is_remote", False)),
                    "salary_min":  row.get("min_amount"),
                    "salary_max":  row.get("max_amount"),
                    "date_posted": str(row.get("date_posted", "")),
                    "easy_apply":  False,
                })
            log.info("✅ JobSpy found %d jobs for '%s'", len(out), term)
            return out
        except Exception as e:
            log.warning("JobSpy error for '%s': %s", term, e)
            return []

    for term in search_terms:
        log.info("🔍 JobSpy scraping: '%s' in %s", term, location)
        jobs = await asyncio.to_thread(_run_scrape, term)
        all_jobs.extend(jobs)
        await asyncio.sleep(2)

    seen: set[str] = set()
    unique: list[dict] = []
    for j in all_jobs:
        key = j.get("url", "")
        if key and key not in seen:
            seen.add(key)
            unique.append(j)

    log.info("📊 JobSpy total unique jobs: %d", len(unique))
    return unique


def _score_job_match_sync(job: dict, cfg: dict) -> dict:
    jd = (job.get("description") or "")[:3000]
    if not jd:
        return {"overall": 50, "reason": "No description available",
                "missing_skills": [], "matching_skills": [], "language": 100}

    cache_key = hashlib.md5(jd.encode()).hexdigest()
    if cache_key in _score_cache:
        return _score_cache[cache_key]

    profile_summary = """
Felix Behboudi — Backend Java Engineer, Berlin, Germany
- 5 years experience: Java, Spring Boot, Kafka, microservices, REST APIs
- Also knows: Python, PostgreSQL, Redis, Docker, Kubernetes basics
- German: B1 (conversational), English: Fluent
- Location: Berlin, open to hybrid/remote
- Immediately available, salary expectation: 75,000 EUR
- Work permit: German citizen, no sponsorship needed
"""
    prompt = (
        f"You are a job-resume match evaluator. Score how well this job fits "
        f"the candidate profile using Chain-of-Thought reasoning.\n\n"
        f"CANDIDATE:\n{profile_summary}\n\n"
        f"JOB TITLE: {job.get('title', '')}\n"
        f"COMPANY: {job.get('company', '')}\n"
        f"JOB DESCRIPTION:\n{jd}\n\n"
        "Score each dimension 0-100, then give overall score.\n"
        'Respond in this EXACT JSON format:\n'
        '{\n'
        '  "technical": <0-100>,\n'
        '  "seniority": <0-100>,\n'
        '  "location": <0-100>,\n'
        '  "language": <0-100>,\n'
        '  "overall": <0-100>,\n'
        '  "missing_skills": ["skill1"],\n'
        '  "matching_skills": ["skill1"],\n'
        '  "reason": "one sentence",\n'
        '  "should_apply": true\n'
        '}'
    )
    try:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            return {"overall": 50, "reason": "No API key", "missing_skills": [], "matching_skills": [], "language": 100}
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            result = json.loads(m.group())
            _score_cache[cache_key] = result
            return result
    except Exception as e:
        log.warning("Score LLM error: %s", e)
    return {"overall": 50, "reason": "Score failed", "missing_skills": [], "matching_skills": [], "language": 100}


async def _score_job_match(job: dict, cfg: dict) -> dict:
    return await asyncio.to_thread(_score_job_match_sync, job, cfg)


def _is_already_applied(job: dict, cfg: dict) -> bool:
    tracker = cfg.get("tracker_file", "")
    if not tracker or not os.path.exists(tracker):
        return False
    try:
        import openpyxl
        wb = openpyxl.load_workbook(tracker, read_only=True)
        ws = wb.active
        job_url     = (job.get("url") or "").strip()
        job_company = (job.get("company") or "").lower()
        job_title   = (job.get("title") or "").lower()
        for row in ws.iter_rows(min_row=2, values_only=True):
            row_company = str(row[1] if len(row) > 1 else "").lower()
            row_title   = str(row[2] if len(row) > 2 else "").lower()
            row_url     = str(row[3] if len(row) > 3 else "")
            if job_url and row_url and job_url in row_url:
                wb.close()
                return True
            if job_company and job_title and job_company in row_company and job_title in row_title:
                wb.close()
                return True
        wb.close()
    except Exception as e:
        log.warning("Dedup check failed: %s", e)
    return False


def _save_skills_gap(gap: dict[str, int], cfg: dict) -> None:
    log_file = cfg.get("log_file", "agent/applied_jobs_log.json")
    gap_file = Path(str(log_file).replace(".json", "_skills_gap.json"))
    if not gap_file.is_absolute():
        gap_file = Path(__file__).resolve().parent / gap_file
    try:
        existing: dict[str, int] = {}
        if gap_file.exists():
            try:
                existing = json.loads(gap_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        for skill, count in gap.items():
            existing[skill] = existing.get(skill, 0) + count
        gap_file.parent.mkdir(parents=True, exist_ok=True)
        gap_file.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
        top5 = sorted(existing.items(), key=lambda x: -x[1])[:5]
        log.info("📊 Top missing skills: %s", top5)
    except Exception as e:
        log.warning("Could not save skills gap: %s", e)


# ── Main apply loop ───────────────────────────────────────────────────────────

async def _run_apply(jobs: list[dict], cfg: dict, stop_flag: threading.Event) -> None:
    import pdfplumber

    init_answer_cache(cfg)

    # Load both resume texts once so per-job language selection is instant
    _resume_texts: dict[str, str] = {}
    _resume_paths: dict[str, str] = {}
    for _lang in ("en", "de"):
        _rp = _resume_path(cfg, _lang)
        if _rp:
            _resume_paths[_lang] = str(_rp)
            try:
                with pdfplumber.open(_rp) as pdf:
                    _resume_texts[_lang] = "\n".join(p.extract_text() or "" for p in pdf.pages)
            except Exception:
                pass
    # Keep backward-compat variable for code paths that don't get per-job lang
    resume_path = _resume_path(cfg, "en")
    resume_text = _resume_texts.get("en", "")

    prof = _profile(cfg)

    # ── Scraping phase: use JobSpy if no jobs were passed ──────────────────
    if not jobs:
        _emit("apply_step", {"url": "", "step": "🔍 No jobs provided — scraping via JobSpy"})
        jobs = await _scrape_jobs_jobspy(cfg)

    # ── Dedup + LLM scoring phase (runs before browser opens) ─────────────
    # Jobs that already have match_score (passed from matcher upstream) or
    # have no description (direct URL apply from dashboard) skip LLM scoring.
    skills_gap: dict[str, int] = {}
    if jobs:
        needs_scoring = [j for j in jobs
                         if not j.get("match_score") and j.get("description", "").strip()]
        pre_scored    = [j for j in jobs
                         if j.get("match_score") or not j.get("description", "").strip()]

        if needs_scoring:
            _emit("apply_step", {"url": "", "step": f"🧮 Scoring {len(needs_scoring)} jobs…"})
            scored: list[dict] = []
            for job in needs_scoring:
                # Dedup against tracker spreadsheet
                if _is_already_applied(job, cfg):
                    log.info("⏭️ Already applied: %s @ %s", job.get("title"), job.get("company"))
                    continue
                # LLM scoring
                score_data = await _score_job_match(job, cfg)
                for sk in score_data.get("missing_skills") or []:
                    skills_gap[sk] = skills_gap.get(sk, 0) + 1
                job["match_score"]     = score_data.get("overall", 50)
                job["missing_skills"]  = score_data.get("missing_skills", [])
                job["matching_skills"] = score_data.get("matching_skills", [])
                job["score_reason"]    = score_data.get("reason", "")
                # Language filter
                if score_data.get("language", 100) < 40:
                    log.info("🇩🇪 Skipping — German requirement too high: %s", job.get("title"))
                    continue
                # Overall score filter
                min_score = cfg.get("min_match_score", 70)
                if job["match_score"] < min_score:
                    log.info("⏭️ Skipping '%s' @ %s — score %d: %s",
                             job.get("title"), job.get("company"),
                             job["match_score"], score_data.get("reason", ""))
                    continue
                scored.append(job)
            _save_skills_gap(skills_gap, cfg)
            # Dedup pre_scored jobs too
            pre_scored = [j for j in pre_scored if not _is_already_applied(j, cfg)]
            jobs = scored + pre_scored
            _emit("apply_step", {"url": "", "step": f"✅ {len(jobs)} jobs ready to apply"})
        else:
            # All jobs are pre-matched (from matcher.py) or direct URL from dashboard
            jobs = [j for j in pre_scored if not _is_already_applied(j, cfg)]
            _emit("apply_step", {"url": "", "step": f"✅ {len(jobs)} jobs ready (pre-matched, skipping re-score)"})

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

            # Visit feed first to activate session properly (warmup — non-critical)
            try:
                await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=30_000)
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
            except Exception as _warmup_exc:
                log.warning("Feed warmup timed out (%s) — continuing anyway", _warmup_exc)

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

                # Detect job language → select resume
                _lang = _detect_job_language(job)
                job["_resume_lang"] = _lang
                resume_text = _resume_texts.get(_lang) or _resume_texts.get("en", "")
                _used_resume = _resume_paths.get(_lang) or _resume_paths.get("en", "")
                _emit("apply_step", {"url": url,
                    "step": f"📄 Resume: {'DE' if _lang == 'de' else 'EN'} ({Path(_used_resume).name if _used_resume else 'none'})"})

                # Detect platform
                platform = detect_platform(url)
                _emit("platform_detected", {"url": url, "platform": platform})
                log.info("Apply [%s/%s] %s @ %s", platform, _lang.upper(), title, company)

                MAX_RETRIES = 2
                handler = PLATFORM_HANDLERS.get(platform, _apply_linkedin)
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
                        _db.log_application(job, status="Applied ✓", applied_by="Agent",
                                            apply_type=result.get("apply_type", ""))
                        _log_to_json(job, result.get("apply_type", ""), _used_resume)
                    else:
                        log.info("DEBUG job — skipping DB write (Applied ✓)")
                    log.info("Applied: %s @ %s", title, company)
                else:
                    # ALL non-success outcomes (manual=True or outright failures) go to manual queue
                    manual_count += 1
                    note = result.get("note", "Unknown failure")
                    if not is_debug:
                        _db.log_manual_apply(url, title, company, platform, note,
                                             session_id=str(session_id))
                        _emit("apply_step", {"url": url, "step": f"  ⚠ Added to manual queue: {note[:80]}"})
                    else:
                        log.info("DEBUG job — skipping DB write (manual/failed: %s)", note)
                    log.info("Manual queue: %s @ %s — %s", title, company, note)

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
    try:
        asyncio.run(_run_apply(jobs, cfg, stop_flag))
    except Exception as exc:
        log.error("_run_apply crashed: %s", exc, exc_info=True)
        _emit("apply_step", {"url": "", "step": f"❌ Crash: {exc}"})
        _emit("session_done", {"success": 0, "manual": 0, "failed": len(jobs)})


# ── Legacy single-job wrapper (used by main.py) ──────────────────────────────

def apply(job: dict, archive_path: Path, cfg: dict | None = None) -> bool:
    """Single-job synchronous wrapper kept for backward compatibility."""
    if cfg is None:
        cfg = load_config()
    results: list[bool] = []

    async def _one():
        _lang      = _detect_job_language(job)
        resume_path = _resume_path(cfg, _lang)
        resume_text = ""
        if resume_path:
            try:
                import pdfplumber
                with pdfplumber.open(resume_path) as pdf:
                    resume_text = "\n".join(p.extract_text() or "" for p in pdf.pages)
            except Exception:
                pass
        job["_resume_lang"] = _lang
        prof     = _profile(cfg)
        platform = detect_platform(job.get("url", ""))
        handler  = PLATFORM_HANDLERS.get(platform, _apply_linkedin)
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


# ── Spec-defined public API (used by main.py orchestration) ──────────────────

async def apply_to_job(page: "Page", job: dict, config: dict) -> dict:
    """Apply to a single job on an already-open browser page.

    Returns: {success, apply_type, error_msg, timestamp}
    """
    import pdfplumber as _pdfplumber
    _lang = _detect_job_language(job)
    job["_resume_lang"] = _lang
    _rp   = _resume_path(config, _lang)
    _rt   = ""
    if _rp:
        try:
            with _pdfplumber.open(_rp) as _pdf:
                _rt = "\n".join(p.extract_text() or "" for p in _pdf.pages)
        except Exception:
            pass
    prof     = _profile(config)
    platform = detect_platform(job.get("url", ""))
    handler  = PLATFORM_HANDLERS.get(platform, _apply_linkedin)
    result   = await handler(page, job, config, _rt, prof)
    ts = datetime.utcnow().isoformat() + "Z"
    if result.get("success"):
        _log_to_json(job, result.get("apply_type", ""), str(_rp) if _rp else "")
    return {
        "success":    result.get("success", False),
        "apply_type": result.get("apply_type", ""),
        "error_msg":  result.get("note", ""),
        "timestamp":  ts,
    }


async def upload_resume(page: "Page", field_selector: str, resume_path: str) -> dict:
    """Upload a resume PDF to a file input. field_selector may be empty for auto-detect.

    Returns: {success, error_msg}
    """
    try:
        rp = Path(resume_path)
        if not rp.exists():
            return {"success": False, "error_msg": f"File not found: {resume_path}"}
        loc = page.locator(field_selector) if field_selector else page.locator("input[type='file']")
        if not await loc.count():
            return {"success": False, "error_msg": "No file input found"}
        await loc.first.set_input_files(str(rp))
        await asyncio.sleep(1.0)
        return {"success": True, "error_msg": ""}
    except Exception as exc:
        return {"success": False, "error_msg": str(exc)}


if __name__ == "__main__":
    from core.config import setup_logging
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
