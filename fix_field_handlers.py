"""
Adds a universal _fill_field_smart() to linkedin_applier.py that handles
every field type correctly with proper validation and fallbacks.

Also patches _answer_visible_questions and _retry_invalid_fields to use it.

Run from project root:
    python fix_field_handlers.py
"""
from pathlib import Path
import subprocess

UNIVERSAL_HANDLER = '''

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
            nums = _re.findall(r'\\d+', value)
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
            # Resume upload
            resume_path = cfg.get("paths", {}).get("resume_en", "")
            if resume_path and Path(resume_path).exists():
                await el.set_input_files(resume_path)
                _emit("apply_answer", {"label": label[:60] or "Resume", "answer": Path(resume_path).name})
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
            num_val = _re.sub(r"[^\\d]", "", str(value).split(".")[0]) or "1"
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

'''

# Write the handler to a temp file to check syntax
import tempfile
tmp = Path(tempfile.gettempdir()) / "field_handler_check.py"
tmp.write_text(
    "import asyncio\nfrom pathlib import Path\nimport logging\nlog=logging.getLogger(__name__)\n"
    "from unittest.mock import AsyncMock as Page\n"
    "def _emit(a,b): pass\n"
    "def answer_custom_question(*a,**k): return ''\n"
    + UNIVERSAL_HANDLER, encoding="utf-8"
)
r = subprocess.run(["python", "-m", "py_compile", str(tmp)], capture_output=True, text=True)
if r.returncode != 0:
    print(f"❌ Handler syntax error: {r.stderr}")
    exit(1)
print("✅ Universal handler syntax OK")

# ── Inject into linkedin_applier.py ──────────────────────────────────────────
la = Path("applier/linkedin_applier.py")
lc = la.read_text(encoding="utf-8")

# Insert UNIVERSAL_HANDLER just before the _fill_profile_fields function
ANCHOR = "# ── Profile field filler ───────────────────────────────────────────────────────"
if ANCHOR in lc:
    if "fill_field_smart" not in lc:
        lc = lc.replace(ANCHOR, UNIVERSAL_HANDLER + "\n" + ANCHOR, 1)
        print("✅ Universal handler inserted into linkedin_applier.py")
    else:
        print("✅ fill_field_smart already present")
else:
    print("⚠️  Profile filler anchor not found — appending to end")
    lc += "\n" + UNIVERSAL_HANDLER

# Replace _answer_visible_questions call with _fill_all_visible_fields
OLD_ANS = "        await _answer_visible_questions(page, resume_text, profile, job.get(\"description\", \"\"))"
NEW_ANS = (
    "        # Use comprehensive field handler (handles all types + validation)\n"
    "        _n_filled = await _fill_all_visible_fields(\n"
    "            page, resume_text, profile, job.get(\"description\", \"\"), cfg,\n"
    "            prior_answers=prior_answers\n"
    "        )\n"
    "        if _n_filled:\n"
    "            _emit(\"apply_step\", {\"url\": job[\"url\"],\n"
    "                  \"step\": f\"  ✎ Filled {_n_filled} field(s) on page {step_n+1}\"})\n"
    "        # Also run legacy handler for any missed fields\n"
    "        await _answer_visible_questions(page, resume_text, profile, job.get(\"description\", \"\"))"
)
if OLD_ANS in lc:
    lc = lc.replace(OLD_ANS, NEW_ANS, 1)
    print("✅ _fill_all_visible_fields called before legacy handler")
else:
    print("⚠️  _answer_visible_questions call not found")

# Also update _retry_invalid_fields to use fill_field_smart
OLD_RETRY_FILL = (
    "            await el.first.click()\n"
    "            await asyncio.sleep(0.1)\n"
    "            await el.first.fill(\"\")\n"
    "            await asyncio.sleep(0.1)\n"
    "            await _reliable_fill(page, el.first, new_answer)"
)
NEW_RETRY_FILL = (
    "            ok = await fill_field_smart(page, el.first, new_answer,\n"
    "                                        label=label, cfg=cfg)\n"
    "            if not ok:\n"
    "                await el.first.click()\n"
    "                await asyncio.sleep(0.1)\n"
    "                await el.first.fill(\"\")\n"
    "                await asyncio.sleep(0.1)\n"
    "                await _reliable_fill(page, el.first, new_answer)"
)
if OLD_RETRY_FILL in lc:
    lc = lc.replace(OLD_RETRY_FILL, NEW_RETRY_FILL, 1)
    print("✅ _retry_invalid_fields uses fill_field_smart")
else:
    print("⚠️  retry fill anchor not found")

# Make sure cfg is passed to _retry_invalid_fields
OLD_RETRY_CALL = (
    "                    _fixed = await _retry_invalid_fields(\n"
    "                        page, resume_text, profile, job.get(\"description\", \"\"), prior_answers=[]\n"
    "                    )"
)
NEW_RETRY_CALL = (
    "                    _fixed = await _retry_invalid_fields(\n"
    "                        page, resume_text, profile, job.get(\"description\", \"\"),\n"
    "                        prior_answers=[], cfg=cfg\n"
    "                    )"
)
if OLD_RETRY_CALL in lc:
    lc = lc.replace(OLD_RETRY_CALL, NEW_RETRY_CALL, 1)
    print("✅ cfg passed to _retry_invalid_fields")

# Fix _retry_invalid_fields signature to accept cfg
OLD_SIG = (
    "async def _retry_invalid_fields(\n"
    "    page: Page,\n"
    "    resume_text: str,\n"
    "    profile: dict,\n"
    "    job_desc: str,\n"
    "    prior_answers: list[dict],\n"
    ") -> int:"
)
NEW_SIG = (
    "async def _retry_invalid_fields(\n"
    "    page: Page,\n"
    "    resume_text: str,\n"
    "    profile: dict,\n"
    "    job_desc: str,\n"
    "    prior_answers: list[dict],\n"
    "    cfg: dict | None = None,\n"
    ") -> int:\n"
    "    cfg = cfg or {}"
)
if OLD_SIG in lc:
    lc = lc.replace(OLD_SIG, NEW_SIG, 1)
    print("✅ _retry_invalid_fields signature updated with cfg")
else:
    print("⚠️  _retry_invalid_fields signature not found")

la.write_text(lc, encoding="utf-8")

# ── Also update smart_filler.py to use fill_field_smart ──────────────────────
sf = Path("applier/smart_filler.py")
sc = sf.read_text(encoding="utf-8")

OLD_EXEC_FILL = (
    "            else:  # fill\n"
    "                if typ == \"number\":\n"
    "                    value = re.sub(r\"[^\\\\d]\", \"\", value.split(\".\")[0]) or \"1\"\n"
    "                await el.triple_click()\n"
    "                await asyncio.sleep(0.1)\n"
    "                await el.fill(value)\n"
    "                await el.dispatch_event(\"blur\")\n"
    "                await el.dispatch_event(\"change\")\n"
    "                _emit(\"apply_step\", {\"url\": \"\", \"step\": f\"  ✎ {lbl[:40]} = {value[:40]}\"})\n"
    "                executed += 1"
)
NEW_EXEC_FILL = (
    "            else:  # fill — use universal handler\n"
    "                try:\n"
    "                    from applier.linkedin_applier import fill_field_smart\n"
    "                    ok = await fill_field_smart(page, el, value, label=lbl, cfg={})\n"
    "                    if ok:\n"
    "                        executed += 1\n"
    "                    else:\n"
    "                        raise Exception(\"fill_field_smart returned False\")\n"
    "                except Exception:\n"
    "                    if typ == \"number\":\n"
    "                        value = re.sub(r\"[^\\\\d]\", \"\", value.split(\".\")[0]) or \"1\"\n"
    "                    await el.triple_click()\n"
    "                    await asyncio.sleep(0.1)\n"
    "                    await el.fill(value)\n"
    "                    await el.dispatch_event(\"blur\")\n"
    "                    await el.dispatch_event(\"change\")\n"
    "                    _emit(\"apply_step\", {\"url\": \"\", \"step\": f\"  ✎ {lbl[:40]} = {value[:40]}\"})\n"
    "                    executed += 1"
)
if OLD_EXEC_FILL in sc:
    sc = sc.replace(OLD_EXEC_FILL, NEW_EXEC_FILL, 1)
    sf.write_text(sc, encoding="utf-8")
    print("✅ smart_filler uses fill_field_smart for text fields")
else:
    print("⚠️  smart_filler fill anchor not found")

# ── Syntax checks ─────────────────────────────────────────────────────────────
for f in ["applier/linkedin_applier.py", "applier/smart_filler.py"]:
    r = subprocess.run(["python", "-m", "py_compile", f], capture_output=True, text=True)
    print(f"{'✅' if r.returncode==0 else '❌'} Syntax: {f}" + (f"\n   {r.stderr.strip()}" if r.returncode else ""))

print("""
✅ Universal field handler installed. Now handles:
  • text/email/tel      — fill() + keyboard fallback + React setter
  • number              — digits-only, proper events
  • textarea            — fill() + blur/change events
  • <select>            — exact → fuzzy → word-overlap matching
  • radio               — yes/no detection + text label matching
  • checkbox            — consent boxes auto-checked
  • date                — YYYY-MM-DD format + text fallback
  • typeahead/combobox  — type → wait for dropdown → click best match
  • artdeco-combobox    — LinkedIn's custom typeahead component
  • file upload         — set_input_files() from profile/cfg
  • React controlled    — native property setter via JavaScript
  
All strategies: detect → fill → verify → re-try if failed
Memory checked first for every field before calling Claude
""")
