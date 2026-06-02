"""
linkedin_clicker.py
Click the Apply / Easy Apply button on a LinkedIn job page.

Returns: "easy_apply" | "external" | "already" | "not_found" | "modal_failed"

Dual-button routing:
  Easy Apply  → click_apply_button() returns "easy_apply"  → caller uses linkedin_applier.fill_easy_apply()
  External    → click_apply_button() returns "external"    → caller uses external_applier.follow_external_apply()

Standalone helpers (useful for testing / pre-flight checks):
  is_easy_apply_button(page)           → bool
  is_external_apply_button(page)       → bool
  get_target_url_for_apply_button(page)→ str | None
"""
import logging
import re
from pathlib import Path
from typing import Literal
from playwright.async_api import Page

log = logging.getLogger(__name__)


# ── Standalone detection helpers ──────────────────────────────────────────────

async def is_easy_apply_button(page: Page) -> bool:
    """Return True if the current page has a visible Easy Apply button."""
    try:
        ea = page.get_by_role("button", name=re.compile(r"easy apply", re.IGNORECASE))
        if await ea.count() and await ea.first.is_visible():
            return True
    except Exception:
        pass
    for sel in [
        "button[aria-label*='Easy Apply']",
        "button[aria-label*='easy apply']",
        "button.jobs-apply-button--top-card",
    ]:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                return True
        except Exception:
            pass
    return False


async def is_external_apply_button(page: Page) -> bool:
    """Return True if the page has a plain Apply button (not Easy Apply) in the top 800px."""
    try:
        result = await page.evaluate("""() => {
            const MAX_TOP = 800;
            for (const b of document.querySelectorAll('button')) {
                const rect = b.getBoundingClientRect();
                if (rect.top > MAX_TOP || !b.offsetParent) continue;
                const combined = ((b.textContent || '') + ' ' + (b.getAttribute('aria-label') || '')).toLowerCase();
                if (combined.includes('apply') && !combined.includes('easy apply')) return true;
            }
            return false;
        }""")
        return bool(result)
    except Exception:
        return False


async def get_target_url_for_apply_button(page: Page) -> "str | None":
    """Return the href of an external Apply link on the page, or None if only Easy Apply is present."""
    try:
        return await page.evaluate("""() => {
            for (const a of document.querySelectorAll('a[href]')) {
                const combined = ((a.textContent || '') + ' ' + (a.getAttribute('aria-label') || '')).toLowerCase();
                if (combined.includes('apply') && !combined.includes('easy apply')) return a.href || null;
            }
            return null;
        }""") or None
    except Exception:
        return None

_MODAL_SEL = (
    ".jobs-easy-apply-modal, "
    ".jobs-easy-apply-content, "
    "[data-test-modal], "
    ".artdeco-modal__content"
)
MODAL_SEL = _MODAL_SEL  # public alias for imports


async def _is_ea_modal(page: Page) -> bool:
    """Return True if the currently-visible modal is the Easy Apply wizard (not a Premium overlay)."""
    # Specific EA modal classes are definitive
    for sel in (".jobs-easy-apply-modal", ".jobs-easy-apply-content"):
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                return True
        except Exception:
            pass
    # Broader modals — check header text for EA context
    try:
        header = await page.query_selector(
            ".artdeco-modal__header h2, .artdeco-modal__headline, [data-test-modal-header]"
        )
        if header and await header.is_visible():
            text = (await header.text_content() or "").lower()
            if "easy apply" in text or "apply to" in text:
                return True
            # Premium / upsell modals won't contain apply-to context
            if any(kw in text for kw in ("premium", "stand out", "subscription", "upgrade", "trial")):
                return False
    except Exception:
        pass
    # Presence of a form input inside the modal is a strong signal
    try:
        form_el = await page.query_selector(
            ".artdeco-modal__content input:not([type='hidden']), "
            ".artdeco-modal__content select, "
            "[data-test-modal] input:not([type='hidden']), "
            "[data-test-modal] select"
        )
        if form_el and await form_el.is_visible():
            return True
    except Exception:
        pass
    # Navigation buttons present in the EA wizard
    try:
        for label in (r"Next", r"Submit application", r"Review your application"):
            btn = page.get_by_role("button", name=re.compile(label, re.IGNORECASE))
            if await btn.count() and await btn.first.is_visible():
                return True
    except Exception:
        pass
    return False

_EA_CSS = [
    "button[aria-label*='Easy Apply']",
    "button[aria-label*='easy apply']",
    "button.jobs-apply-button--top-card",
    # REMOVED ".jobs-apply-button"   - selects a container DIV, not the button
    #                                  clicking it navigates to similar-jobs list
    # REMOVED ".jobs-s-apply button" - too broad, catches external Apply button
    # REMOVED "button.artdeco-button--primary" - too broad
]


async def click_apply_button(
    page: Page,
    *,
    modal_timeout_ms: int = 8_000,
) -> Literal["easy_apply", "external", "already", "not_found", "modal_failed"]:

    print(f"[clicker] URL: {page.url}", flush=True)

    # FIX Issue 3: wait for job card content to actually render (blank-page race condition)
    try:
        await page.wait_for_selector(
            ".jobs-unified-top-card, .job-view-layout, .jobs-details, .jobs-apply-button",
            state="visible", timeout=10_000,
        )
    except Exception:
        pass  # proceed anyway — might still work on non-standard layouts

    await page.evaluate("window.scrollTo(0, 0)")
    await page.wait_for_timeout(400)

    # ── Already applied? ──────────────────────────────────────────────────────
    for sel in [
        "button[aria-label*='Applied']",
        "[class*='applied-badge']",
        "button:has-text('Applied')",  # fallback for different HTML structures
    ]:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                print("[clicker] Already applied", flush=True)
                return "already"
        except Exception:
            pass

    # Also check for "Already applied" text anywhere on the page
    try:
        has_applied_text = await page.locator("text='Already applied'").is_visible()
        if has_applied_text:
            print("[clicker] Already applied (text)", flush=True)
            return "already"
    except Exception:
        pass

    # ── Helpers (mirrors original _sel_hit / _text_hit / _do_click) ──────────
    async def _sel_hit():
        for sel in _EA_CSS:
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

    async def _do_click(el) -> bool:
        try:
            await el.scroll_into_view_if_needed()
            await page.wait_for_timeout(500)
            await el.click()
            return True
        except Exception:
            return False

    # ── Upfront: is this external-only (no Easy Apply)? ──────────────────────
    _has_easy = False
    _ext_el   = None
    try:
        ea_role = page.get_by_role("button", name=re.compile(r"easy apply", re.IGNORECASE))
        _has_easy = await ea_role.count() > 0 and await ea_role.first.is_visible()
    except Exception:
        pass

    if not _has_easy:
        # JS-based scan — immune to aria-label length, icon characters, CSS class changes,
        # and fixed/sticky containers (offsetParent is null for position:fixed elements,
        # so we use rect dimensions instead).
        _ext_js = await page.evaluate("""() => {
            const MAX_TOP = 1200;
            const all = [];
            // Query BOTH buttons AND anchor links — LinkedIn's external Apply is an <a>, not a <button>
            for (const b of document.querySelectorAll('button, a[href]')) {
                const rect  = b.getBoundingClientRect();
                if (rect.width === 0 || rect.height === 0) continue;
                if (rect.top > MAX_TOP || rect.bottom < 0)  continue;
                const text  = (b.textContent  || b.innerText || '').trim().toLowerCase();
                const label = (b.getAttribute('aria-label') || '').toLowerCase();
                const combined = text + ' ' + label;
                all.push({tag: b.tagName, text: text.substring(0,40), label: label.substring(0,40), top: Math.round(rect.top)});
                // Match "Apply" but not "Easy Apply"
                // Also exclude buttons that are navigation or messaging
                const isApply = (combined.includes('apply') && !combined.includes('easy apply'));
                const isNotNav = !combined.includes('home') && !combined.includes('messaging') && !combined.includes('skip to');
                if (isApply && isNotNav) {
                    b.setAttribute('data-clicker-ext', '1');
                    return {found: true, debug: all};
                }
            }
            return {found: false, debug: all};
        }""")
        if isinstance(_ext_js, dict):
            _debug_btns = _ext_js.get("debug", [])
            print(f"[clicker] JS scan found {len(_debug_btns)} visible buttons: {_debug_btns[:8]}", flush=True)
            if _ext_js.get("found"):
                # Could be either a <button> or <a> tag
                _ext_el = await page.query_selector("button[data-clicker-ext='1'], a[data-clicker-ext='1']")
                if _ext_el:
                    print("[clicker] External Apply found via JS scan", flush=True)

    # External-only: click and return immediately
    if _ext_el and not _has_easy:
        print("[clicker] External apply detected upfront — clicking", flush=True)
        try:
            await _do_click(_ext_el)
        except Exception:
            pass
        return "external"

    # ── Method 1: get_by_role ────────────────────────────────────────────────
    button_found = False
    try:
        ea_loc = page.get_by_role("button", name=re.compile(r"easy apply", re.IGNORECASE))
        if await ea_loc.count():
            btn = await ea_loc.first.evaluate("el => el.getBoundingClientRect().top")
            # Only click buttons in the upper part of the page (avoid bottom recommendations)
            if btn < 2000:  # reasonable threshold for top-of-page job card
                await ea_loc.first.scroll_into_view_if_needed()
                await page.wait_for_timeout(500)
                await ea_loc.first.click()
                print("[clicker] Clicked via get_by_role", flush=True)
                button_found = True
                await page.wait_for_timeout(1_500)  # wait to confirm we don't navigate away
    except Exception as e:
        log.debug("get_by_role failed: %s", e)

    # ── Method 2: locator filter has_text ────────────────────────────────────
    if not button_found:
        try:
            ea_loc = page.locator("button").filter(has_text="Easy Apply")
            if await ea_loc.count():
                btn = await ea_loc.first.evaluate("el => el.getBoundingClientRect().top")
                if btn < 2000:  # only click if in upper part of page
                    await ea_loc.first.scroll_into_view_if_needed()
                    await page.wait_for_timeout(500)
                    await ea_loc.first.click()
                    print("[clicker] Clicked via has_text filter", flush=True)
                    button_found = True
                    await page.wait_for_timeout(1_500)
        except Exception as e:
            log.debug("has_text filter failed: %s", e)

    # ── Method 3: CSS selectors ───────────────────────────────────────────────
    if not button_found:
        el = await _sel_hit()
        if el:
            # Validate button position (avoid bottom-of-page elements)
            try:
                btn_pos = await el.evaluate("el => el.getBoundingClientRect().top")
                if btn_pos > 2000:
                    el = None  # element too far down the page
            except Exception:
                pass  # continue anyway

            if el:
                button_found = await _do_click(el)
                if button_found:
                    print("[clicker] Clicked via CSS selector", flush=True)
                    await page.wait_for_timeout(1_500)

    # ── Method 4: full text scan ──────────────────────────────────────────────
    if not button_found:
        el = await _text_hit()
        if el:
            button_found = await _do_click(el)
            if button_found:
                print("[clicker] Clicked via text scan", flush=True)

    # ── Method 5: scroll 300px and retry ─────────────────────────────────────
    if not button_found:
        await page.evaluate("window.scrollTo(0, 300)")
        await page.wait_for_timeout(1_500)
        try:
            ea_loc = page.get_by_role("button", name=re.compile(r"easy apply", re.IGNORECASE))
            if await ea_loc.count():
                await ea_loc.first.scroll_into_view_if_needed()
                await ea_loc.first.click()
                print("[clicker] Clicked via get_by_role after scroll", flush=True)
                button_found = True
        except Exception:
            pass
        if not button_found:
            el = await _sel_hit() or await _text_hit()
            if el:
                button_found = await _do_click(el)
                if button_found:
                    print("[clicker] Clicked via selector/text after scroll", flush=True)

    # ── Method 6: JS deep search ─────────────────────────────────────────────
    if not button_found:
        clicked = await page.evaluate("""
            () => {
                const btns = Array.from(document.querySelectorAll('button'));
                // Filter to only visible buttons in upper part of page
                let btn = btns.find(b => {
                    const rect = b.getBoundingClientRect();
                    const text = (b.textContent || '').toLowerCase();
                    const label = (b.getAttribute('aria-label') || '').toLowerCase();
                    const isVisible = rect.top < 2000 && b.offsetParent !== null;
                    const isEasyApply = text.includes('easy apply') || label.includes('easy apply');
                    return isEasyApply && isVisible;
                });
                if (!btn) {
                    const anchors = Array.from(document.querySelectorAll('a'));
                    btn = anchors.find(a => {
                        const rect = a.getBoundingClientRect();
                        const text = (a.textContent || '').toLowerCase();
                        const label = (a.getAttribute('aria-label') || '').toLowerCase();
                        const isVisible = rect.top < 2000 && a.offsetParent !== null;
                        const isEasyApply = text.includes('easy apply') || label.includes('easy apply');
                        return isEasyApply && isVisible;
                    });
                }
                if (btn) { btn.click(); return (btn.textContent || '').trim().substring(0, 50); }
                return null;
            }
        """)
        if clicked:
            print(f"[clicker] Clicked via JS deep search: {clicked!r}", flush=True)
            button_found = True
            await page.wait_for_timeout(2_000)

    # ── External Apply fallback ───────────────────────────────────────────────
    # Reached only when button_found=False (no Easy Apply found by any method).
    # Same JS scan as upfront — fixed/sticky-safe, debug-logged.
    if not button_found:
        _ext_fb = await page.evaluate("""() => {
            const MAX_TOP = 1200;
            const all = [];
            // Query BOTH buttons AND anchor links — LinkedIn's external Apply is an <a>, not a <button>
            for (const b of document.querySelectorAll('button, a[href]')) {
                const rect  = b.getBoundingClientRect();
                if (rect.width === 0 || rect.height === 0) continue;
                if (rect.top > MAX_TOP || rect.bottom < 0)  continue;
                const text  = (b.textContent  || b.innerText || '').trim().toLowerCase();
                const label = (b.getAttribute('aria-label') || '').toLowerCase();
                const combined = text + ' ' + label;
                all.push({tag: b.tagName, text: text.substring(0,40), label: label.substring(0,40), top: Math.round(rect.top)});
                // Match "Apply" but not "Easy Apply"
                // Also exclude buttons that are navigation or messaging
                const isApply = (combined.includes('apply') && !combined.includes('easy apply'));
                const isNotNav = !combined.includes('home') && !combined.includes('messaging') && !combined.includes('skip to');
                if (isApply && isNotNav) {
                    b.setAttribute('data-clicker-fb', '1');
                    return {found: true, debug: all};
                }
            }
            return {found: false, debug: all};
        }""")
        if isinstance(_ext_fb, dict):
            _fb_btns = _ext_fb.get("debug", [])
            print(f"[clicker] Fallback JS scan — {len(_fb_btns)} visible buttons: {_fb_btns[:8]}", flush=True)
            if _ext_fb.get("found"):
                # Could be either a <button> or <a> tag
                ext_btn = await page.query_selector("button[data-clicker-fb='1'], a[data-clicker-fb='1']")
                if ext_btn and await ext_btn.is_visible():
                    ext_text = (await ext_btn.text_content() or "").strip()
                    print(f"[clicker] External Apply button (JS fallback): {ext_text!r}", flush=True)
                    await ext_btn.click()
                    return "external"

        print("[clicker] No apply button found", flush=True)
        return "not_found"

    # ── Wait for Easy Apply modal ─────────────────────────────────────────────
    # Strategy 1: broad selector wait (fast path — covers most sessions)
    _modal_opened = False
    _detect_via   = "none"
    try:
        await page.wait_for_selector(_MODAL_SEL, state="visible", timeout=modal_timeout_ms)
        _modal_opened = True
        _detect_via   = "selector"
    except Exception:
        pass

    # Strategy 2 + 3: role=dialog and body scroll-lock (progressive, 4 × 1.5 s)
    if not _modal_opened:
        for _n in range(4):
            if "/jobs/apply/" in page.url or "/apply?" in page.url:
                return "easy_apply"
            # role=dialog
            try:
                for _d in await page.query_selector_all("[role='dialog']"):
                    if await _d.is_visible():
                        _modal_opened = True
                        _detect_via   = "role=dialog"
                        break
            except Exception:
                pass
            if not _modal_opened:
                # body scroll-lock — LinkedIn locks <body> scroll when a modal is open
                try:
                    _ov = await page.evaluate("() => getComputedStyle(document.body).overflow")
                    if _ov == "hidden":
                        _modal_opened = True
                        _detect_via   = "scroll-lock"
                except Exception:
                    pass
            if _modal_opened:
                break
            await page.wait_for_timeout(1_500)

    if _modal_opened:
        await page.wait_for_timeout(500)
        # Verify the detected modal is actually the Easy Apply wizard
        if await _is_ea_modal(page):
            print(f"[clicker] Easy Apply modal open (via {_detect_via})", flush=True)
            return "easy_apply"
        # Premium / upsell overlay matched the broad selector — dismiss and retry
        print("[clicker] Non-EA overlay detected — dismissing and retrying", flush=True)
        await dismiss_overlays(page)
        await page.wait_for_timeout(1_500)
        try:
            await page.wait_for_selector(_MODAL_SEL, state="visible", timeout=8_000)
            if await _is_ea_modal(page):
                print("[clicker] Easy Apply modal open (after overlay dismiss)", flush=True)
                return "easy_apply"
        except Exception:
            pass

    # Check if we navigated to search results (wrong button click)
    if "/jobs/search-results/" in page.url and "currentJobId" in page.url:
        print(f"[clicker] ERROR: Navigated to search results instead of modal. URL: {page.url}", flush=True)
        print("[clicker] This suggests the wrong button was clicked (maybe a 'See Similar Jobs' or related element).", flush=True)
        return "modal_failed"

    if "/jobs/apply/" in page.url or "/apply?" in page.url:
        return "easy_apply"

    # One retry: click Easy Apply again in case the first click was swallowed
    await page.wait_for_timeout(1_500)
    try:
        ea_loc = page.get_by_role("button", name=re.compile(r"easy apply", re.IGNORECASE))
        if await ea_loc.count():
            await ea_loc.first.click()
        await page.wait_for_selector(_MODAL_SEL, state="visible", timeout=8_000)
        await page.wait_for_timeout(500)
        if await _is_ea_modal(page):
            return "easy_apply"
    except Exception:
        pass

    if "/jobs/apply/" in page.url:
        return "easy_apply"

    # External ATS navigation (clicked something and left linkedin.com)
    if "linkedin.com" not in page.url:
        print(f"[clicker] Navigated to external ATS: {page.url}", flush=True)
        return "external"

    # Debug screenshot on confirmed failure
    try:
        _slug = re.sub(r"[^a-zA-Z0-9]", "_", page.url.split("/")[-1])[:30]
        _dbg  = Path(__file__).resolve().parent.parent / "uploads" / f"debug_modal_{_slug}.png"
        await page.screenshot(path=str(_dbg))
        print(f"[clicker] Debug screenshot saved: {_dbg}", flush=True)
    except Exception:
        pass

    return "modal_failed"


async def dismiss_overlays(page: Page) -> int:
    """Dismiss LinkedIn Premium/upsell overlays that intercept clicks.

    Called BEFORE click_apply_button — the Easy Apply modal is never open at
    this point, so it is safe to close any visible Dismiss/Close button that
    is not inside the modal container selectors.
    """
    dismissed = 0

    # FIX Issue 1: expanded selector list covers the new "Job search smarter
    # with Premium" sidebar that LinkedIn started A/B-testing (mid-2026).
    _overlay_sels = [
        ".jobs-premium-applicant-insights button",
        ".artdeco-card__dismiss",
        "[data-test-premium-upsell-dismiss]",
        "[data-test-premium-upsell] button[aria-label]",
        ".msg-overlay-bubble-header__control[aria-label*='Close' i]",
        "button[data-tracking-control-name*='cookie']",
        # Premium sidebar close buttons
        ".artdeco-card button[aria-label*='Dismiss' i]",
        ".artdeco-card button[aria-label*='Close' i]",
        ".premium-upsell-modal__close",
        "[data-test-modal-close-btn]",
    ]
    for _sel in _overlay_sels:
        try:
            for _el in await page.query_selector_all(_sel):
                if await _el.is_visible():
                    await _el.click()
                    await page.wait_for_timeout(400)
                    dismissed += 1
                    log.info("🗑️ Dismissed overlay via: %s", _sel)
        except Exception:
            pass

    # FIX Issue 1: JS fallback now also catches aria-label="Dismiss/Close"
    # buttons, while explicitly skipping any button inside the Easy Apply modal
    # (belt-and-suspenders — the modal isn't open yet at this stage anyway).
    try:
        n = await page.evaluate("""() => {
            // Only protect the EA-specific modal classes — NOT the broad artdeco
            // classes that Premium/upsell overlays also use.
            const MODAL_SELS = [
                '.jobs-easy-apply-modal',
                '.jobs-easy-apply-content',
            ];
            function inModal(el) {
                return MODAL_SELS.some(s => el.closest(s) !== null);
            }
            let n = 0;
            for (const b of document.querySelectorAll('button')) {
                if (!b.offsetParent) continue;   // not visible
                if (inModal(b))     continue;   // never touch the Easy Apply modal
                const txt   = (b.innerText || '').trim();
                const label = (b.getAttribute('aria-label') || '').toLowerCase();
                const isClose =
                    txt === '×' || txt === '✕' || txt === '✖' ||
                    label === 'dismiss' || label.startsWith('dismiss ') ||
                    label === 'close'   || label.startsWith('close ');
                if (isClose) { b.click(); n++; }
            }
            return n;
        }""")
        if n:
            log.info("🗑️ Dismissed %d close/dismiss buttons via JS", n)
            dismissed += n
            await page.wait_for_timeout(500)
    except Exception:
        pass
    return dismissed
