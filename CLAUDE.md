# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Install (two requirements files — README only mentions the first, but `core/main.py` needs both):
```bash
pip install -r requirements.txt -r core/requirements.txt
playwright install chromium
```

Run:
```bash
python core/main.py              # full pipeline: scrape -> dedup -> match -> apply
python dashboard/dashboard.py    # Flask UI at http://localhost:5000
python tracking/email_processor.py   # email triage only (also runs via Task Scheduler every 15 min)
python tracking/get_token.py     # one-time Microsoft OAuth2 device-code flow, writes MS_REFRESH_TOKEN to .env
```

Test:
```bash
pytest                                    # all tests
pytest tests/test_email_db.py             # single file
pytest tests/test_email_db.py::test_name  # single test
```

## Architecture

Pipeline: `scraper/` → `dedup/` → `matcher/` → `applier/` → `tracking/`, all sharing one SQLite DB at `uploads/jobhunt.db` (not under `data/`, despite `data/config.yaml` holding the user profile/thresholds). `core/main.py` orchestrates the full run; `core/config.py` loads `data/config.yaml` (or `$CONFIG_PATH`) and sets up logging. `matcher/matcher.py` scores each job 0-100 against the profile via Claude Haiku; jobs below `min_match_score` are skipped before reaching `applier/`.

A few functions are cross-cutting hubs touched from nearly every module — worth knowing before changing their signatures: `dedup/db.py`'s `_conn()`/`init_db()` (every module's DB access goes through these), `core/config.py`'s `load_config()` (called directly from main.py, dashboard, scraper, applier, matcher, and tests rather than threaded through as one object), and `applier/events.py`'s `_emit()` (the SSE bus every submission path — LinkedIn, external ATS, vision fallback — pushes progress through for the dashboard's live feed).

`applier/` has three submission paths, tried in order per job:
- `linkedin_applier.py` drives the multi-step Easy Apply wizard — all field types are routed through a single `_fill_wizard_step` handler so no element is processed twice and answers accumulate across steps.
- `external_applier.py` routes by domain to Greenhouse/Lever/Ashby-specific handlers, falling back to a generic CSS-based multi-step form loop.
- `smart_filler.py` is a Claude-vision fallback for ATS pages the generic loop can't parse.

`applier/memory.py` persists learned question→answer pairs across runs so Claude isn't re-asked the same custom application questions.

`tracking/` implements the reply side: `email_processor.py` classifies incoming mail with Claude and stages it in the DB, `email_executor.py` moves the message via the Microsoft Graph API and calls `dedup/db.py`'s `update_application_from_email()` to advance status (Pending Confirmation → In Review → Interview/Offer/Rejected). `dashboard/email_admin.py` is the human-in-the-loop approval UI for that staging queue.

`dashboard/dashboard.py` and `dashboard/email_admin.py` together expose a large REST surface behind `api_*` route handlers — job/agent control, interviews, email admin/staging/events, score cache stats, Excel import — grep for the specific `api_` function rather than reading either file top to bottom.

Tests patch `dedup.db._DB_PATH` (see `tests/conftest.py`) to isolate each test in its own SQLite file.

See README.md for the full wizard field-handling order, external-ATS routing tiers, and the email status state machine.

## Notes

- The ~20 top-level `apply_*.py` / `apply_atolls_*.py` / `debug_*.py` / `scout_*.py` scripts are one-off debugging scripts against specific ATS pages from earlier iteration — not part of the production pipeline (that's `applier/`). Ignore them unless explicitly asked.
- `data/config.yaml` was deliberately widened (as of 2026-08-23) to cast a broader net — treat these as intentional, not stale experiment state: `min_match_score` 65 (was 50), `max_applications_per_day` 499 (was 10, effectively uncapped), `posted_limit` `week` (was `24h`), `scrape_pool_size` 100 (was 5), `skip_cached_jobs` false (was true), `locations` now includes Hamburg/Germany/Zurich alongside Berlin, `roles` now includes "Cloud Native Developer"/"java developer" alongside Backend Java Engineer.

---

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.
