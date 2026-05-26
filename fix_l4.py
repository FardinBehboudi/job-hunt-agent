from pathlib import Path
dash = Path("applier/linkedin_applier.py")
content = dash.read_text(encoding="utf-8")

OLD = (
    "            fixed += 1\n"
    "            await asyncio.sleep(0.4)\n"
    "        except Exception:\n"
    "            pass\n"
    "    return fixed\n"
    "# ── Profile field filler"
)
NEW = (
    "            fixed += 1\n"
    "            await asyncio.sleep(0.4)\n"
    "            try:\n"
    "                await el.first.dispatch_event('blur')\n"
    "                await el.first.dispatch_event('change')\n"
    "            except Exception:\n"
    "                pass\n"
    "        except Exception:\n"
    "            pass\n"
    "\n"
    "    # Fix errored <select> dropdowns\n"
    "    try:\n"
    "        for err_loc in await page.locator('.artdeco-inline-feedback--error').all():\n"
    "            try:\n"
    "                sel = page.locator('select').filter(has=err_loc.locator('xpath=ancestor::*')).first\n"
    "                if not await sel.count():\n"
    "                    # Try sibling select\n"
    "                    parent = err_loc.locator('xpath=ancestor::*[3]')\n"
    "                    sel = parent.locator('select').first\n"
    "                if await sel.count() and await sel.is_visible():\n"
    "                    opts = [o.strip() for o in await sel.locator('option').all_text_contents()\n"
    "                            if o.strip().lower() not in {'','select','please select','select an option'}]\n"
    "                    if opts:\n"
    "                        lbl_t = (await err_loc.text_content() or '').strip()\n"
    "                        ans = answer_custom_question(lbl_t or 'field','select',opts,\n"
    "                                                    resume_text,profile,job_desc,\n"
    "                                                    prior_answers=prior_answers)\n"
    "                        if ans:\n"
    "                            for _fn in [lambda: sel.select_option(label=ans),\n"
    "                                        lambda: sel.select_option(value=ans)]:\n"
    "                                try: await _fn(); break\n"
    "                                except Exception: pass\n"
    "                            await sel.dispatch_event('change')\n"
    "                            fixed += 1\n"
    "            except Exception: pass\n"
    "    except Exception: pass\n"
    "\n"
    "    # Claude vision fallback if nothing fixed\n"
    "    if fixed == 0:\n"
    "        try:\n"
    "            from applier.smart_filler import _claude_decide, _execute_actions\n"
    "            _acts = await _claude_decide(page, profile, resume_text, job_desc, task='fill')\n"
    "            if _acts:\n"
    "                fixed += await _execute_actions(page, _acts, {})\n"
    "        except Exception:\n"
    "            pass\n"
    "\n"
    "    return fixed\n"
    "# ── Profile field filler"
)

if OLD in content:
    content = content.replace(OLD, NEW, 1)
    dash.write_text(content, encoding="utf-8")
    print("✅ L4: select retry + blur + Claude vision added")
else:
    print("⚠️  Still not matching — checking context")
    idx = content.find("    return fixed\n# ── Profile field filler")
    if idx >= 0:
        print(f"  Found at char {idx}")
        print(repr(content[idx-100:idx+50]))
