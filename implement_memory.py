"""
Implements application memory/learning system.

Creates applier/memory.py — a SQLite-backed memory that:
  1. Remembers Q&A pairs (reuses answers to similar questions)
  2. Learns platform-specific patterns (what works on Greenhouse vs Ashby)
  3. Records field→value mappings that succeeded
  4. Extracts lessons from failures via Claude
  5. Injects relevant memory into Claude prompts before each application

Patches:
  - applier/linkedin_applier.py  → check memory before answering, save after
  - applier/external_applier.py  → inject memory into _ai_decide_form_actions
  - applier/smart_filler.py      → inject memory into _claude_decide
  - applier/applier.py           → save result memory after each job

Run from project root:
    python implement_memory.py
"""

from pathlib import Path
import subprocess

# ══════════════════════════════════════════════════════════════════════════════
# CREATE: applier/memory.py
# ══════════════════════════════════════════════════════════════════════════════
MEMORY_PY = r'''"""
applier/memory.py — Persistent application memory and learning system.

The agent remembers:
  - Answers it gave to custom questions (reuses them on similar questions)
  - Which approaches worked on which ATS platforms
  - Field→value mappings that led to successful submissions
  - Lessons extracted by Claude from failures

Usage:
    from applier.memory import AppMemory
    mem = AppMemory()

    # Get context to inject into a prompt
    hints = mem.get_hints(platform="greenhouse", question="years of experience")

    # Save a Q&A pair after answering
    mem.save_qa("Years of Java experience?", "5", platform="linkedin", success=True)

    # After application completes
    mem.save_application_result(platform, qa_pairs, success, failure_note)
"""

import json
import logging
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "agent_memory.db"


@contextmanager
def _conn():
    con = sqlite3.connect(str(_DB_PATH))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def _norm(text: str) -> str:
    """Normalize a question/label for fuzzy matching."""
    return re.sub(r"\s+", " ", (text or "").lower().strip().
                  replace("?", "").replace(":", "").replace("*", ""))


def _init():
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _conn() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS qa_memory (
            id            INTEGER PRIMARY KEY,
            question_norm TEXT NOT NULL,
            question_raw  TEXT,
            answer        TEXT NOT NULL,
            platform      TEXT DEFAULT '',
            field_type    TEXT DEFAULT 'text',
            success_count INTEGER DEFAULT 1,
            fail_count    INTEGER DEFAULT 0,
            last_used     TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_qa_q ON qa_memory(question_norm);

        CREATE TABLE IF NOT EXISTS platform_lessons (
            id          INTEGER PRIMARY KEY,
            platform    TEXT NOT NULL,
            lesson      TEXT NOT NULL,
            lesson_type TEXT DEFAULT 'general',
            created_at  TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_pl ON platform_lessons(platform);

        CREATE TABLE IF NOT EXISTS field_memory (
            id            INTEGER PRIMARY KEY,
            label_norm    TEXT NOT NULL,
            answer        TEXT NOT NULL,
            field_type    TEXT DEFAULT 'text',
            platform      TEXT DEFAULT '',
            success_count INTEGER DEFAULT 1,
            fail_count    INTEGER DEFAULT 0,
            last_used     TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_fm ON field_memory(label_norm);

        CREATE TABLE IF NOT EXISTS application_log (
            id           INTEGER PRIMARY KEY,
            job_url      TEXT,
            platform     TEXT,
            success      INTEGER,
            failure_note TEXT,
            qa_json      TEXT,
            created_at   TEXT
        );
        """)

_init()


class AppMemory:
    """Main interface to the application memory system."""

    # ── Q&A memory ──────────────────────────────────────────────────────────

    def save_qa(self, question: str, answer: str, *,
                platform: str = "", field_type: str = "text",
                success: bool = True) -> None:
        """Remember an answer to a question."""
        if not question or not answer:
            return
        q_norm = _norm(question)
        now = datetime.now(timezone.utc).isoformat()
        with _conn() as db:
            row = db.execute(
                "SELECT id, success_count, fail_count FROM qa_memory "
                "WHERE question_norm=? AND platform=?", (q_norm, platform)
            ).fetchone()
            if row:
                if success:
                    db.execute("UPDATE qa_memory SET answer=?, success_count=success_count+1, last_used=? WHERE id=?",
                               (answer, now, row["id"]))
                else:
                    db.execute("UPDATE qa_memory SET fail_count=fail_count+1, last_used=? WHERE id=?",
                               (now, row["id"]))
            else:
                db.execute(
                    "INSERT INTO qa_memory (question_norm,question_raw,answer,platform,field_type,success_count,fail_count,last_used) VALUES (?,?,?,?,?,?,?,?)",
                    (q_norm, question, answer, platform, field_type,
                     1 if success else 0, 0 if success else 1, now)
                )

    def get_answer(self, question: str, platform: str = "") -> str | None:
        """Look up a remembered answer for a question. Returns best match or None."""
        q_norm = _norm(question)
        with _conn() as db:
            # Exact platform match first
            row = db.execute(
                "SELECT answer FROM qa_memory WHERE question_norm=? AND platform=? "
                "AND success_count > fail_count ORDER BY success_count DESC LIMIT 1",
                (q_norm, platform)
            ).fetchone()
            if row:
                return row["answer"]
            # Cross-platform match
            row = db.execute(
                "SELECT answer FROM qa_memory WHERE question_norm=? "
                "AND success_count > fail_count ORDER BY success_count DESC LIMIT 1",
                (q_norm,)
            ).fetchone()
            if row:
                return row["answer"]
            # Fuzzy: any word overlap > 60%
            words = set(q_norm.split())
            if len(words) < 2:
                return None
            rows = db.execute(
                "SELECT question_norm, answer FROM qa_memory "
                "WHERE success_count > fail_count ORDER BY success_count DESC LIMIT 50"
            ).fetchall()
            for r in rows:
                r_words = set(r["question_norm"].split())
                overlap = len(words & r_words) / max(len(words), len(r_words), 1)
                if overlap >= 0.6:
                    return r["answer"]
        return None

    def save_field(self, label: str, answer: str, *,
                   field_type: str = "text", platform: str = "",
                   success: bool = True) -> None:
        """Remember a field label → value mapping."""
        if not label or not answer:
            return
        l_norm = _norm(label)
        now = datetime.now(timezone.utc).isoformat()
        with _conn() as db:
            row = db.execute(
                "SELECT id FROM field_memory WHERE label_norm=? AND platform=?",
                (l_norm, platform)
            ).fetchone()
            if row:
                if success:
                    db.execute("UPDATE field_memory SET answer=?, success_count=success_count+1, last_used=? WHERE id=?",
                               (answer, now, row["id"]))
                else:
                    db.execute("UPDATE field_memory SET fail_count=fail_count+1, last_used=? WHERE id=?",
                               (now, row["id"]))
            else:
                db.execute(
                    "INSERT INTO field_memory (label_norm,answer,field_type,platform,success_count,fail_count,last_used) VALUES (?,?,?,?,?,?,?)",
                    (l_norm, answer, field_type, platform,
                     1 if success else 0, 0 if success else 1, now)
                )

    def get_field_hint(self, label: str, platform: str = "") -> str | None:
        """Get a remembered value for a field label."""
        l_norm = _norm(label)
        with _conn() as db:
            for plat in [platform, ""]:
                row = db.execute(
                    "SELECT answer FROM field_memory WHERE label_norm=? AND platform=? "
                    "AND success_count > fail_count ORDER BY success_count DESC LIMIT 1",
                    (l_norm, plat)
                ).fetchone()
                if row:
                    return row["answer"]
        return None

    # ── Platform lessons ─────────────────────────────────────────────────────

    def save_lesson(self, platform: str, lesson: str,
                    lesson_type: str = "general") -> None:
        """Save a learned lesson about a platform."""
        if not platform or not lesson:
            return
        now = datetime.now(timezone.utc).isoformat()
        with _conn() as db:
            # Don't duplicate very similar lessons
            existing = db.execute(
                "SELECT id FROM platform_lessons WHERE platform=? AND lesson=?",
                (platform, lesson)
            ).fetchone()
            if not existing:
                db.execute(
                    "INSERT INTO platform_lessons (platform,lesson,lesson_type,created_at) VALUES (?,?,?,?)",
                    (platform, lesson, lesson_type, now)
                )

    def get_lessons(self, platform: str) -> list[str]:
        """Get all lessons for a platform."""
        with _conn() as db:
            rows = db.execute(
                "SELECT lesson FROM platform_lessons WHERE platform=? ORDER BY id DESC LIMIT 20",
                (platform,)
            ).fetchall()
            return [r["lesson"] for r in rows]

    # ── Application log ──────────────────────────────────────────────────────

    def save_application_result(self, job_url: str, platform: str,
                                 success: bool, qa_pairs: list[dict],
                                 failure_note: str = "") -> None:
        """Log a completed application and update Q&A success flags."""
        now = datetime.now(timezone.utc).isoformat()
        with _conn() as db:
            db.execute(
                "INSERT INTO application_log (job_url,platform,success,failure_note,qa_json,created_at) VALUES (?,?,?,?,?,?)",
                (job_url, platform, 1 if success else 0,
                 failure_note, json.dumps(qa_pairs), now)
            )
        # Update Q&A success flags
        for qa in qa_pairs:
            self.save_qa(qa.get("question", ""), qa.get("answer", ""),
                         platform=platform, success=success)
        # Extract lessons on failure
        if not success and failure_note:
            self._extract_lessons_from_failure(platform, failure_note, qa_pairs)

    def _extract_lessons_from_failure(self, platform: str,
                                       failure_note: str,
                                       qa_pairs: list[dict]) -> None:
        """Use Claude to extract reusable lessons from a failure."""
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
            qa_text = "\n".join(f"Q: {q['question']} A: {q['answer']}"
                                 for q in qa_pairs[:10]) if qa_pairs else "No Q&A recorded"
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=400,
                messages=[{"role": "user", "content":
                    f"A job application on {platform} failed: {failure_note}\n"
                    f"Q&A pairs attempted:\n{qa_text}\n\n"
                    "Extract 1-3 SHORT lessons for future attempts on this platform. "
                    "Return as JSON array of strings. Example: "
                    '["Always select country before state", "Phone must include +49 prefix"]\n'
                    "Return ONLY the JSON array."}]
            )
            raw = resp.content[0].text.strip()
            start, end = raw.find("["), raw.rfind("]") + 1
            if start >= 0 and end > start:
                lessons = json.loads(raw[start:end])
                for lesson in lessons[:3]:
                    if isinstance(lesson, str) and lesson.strip():
                        self.save_lesson(platform, lesson.strip(), "failure")
                        log.info("Memory: saved lesson for %s: %s", platform, lesson)
        except Exception as e:
            log.debug("lesson extraction failed: %s", e)

    # ── Context builder for prompts ──────────────────────────────────────────

    def build_prompt_context(self, platform: str = "",
                              questions: list[str] | None = None) -> str:
        """
        Build a memory context block to prepend to Claude prompts.
        Includes relevant Q&A hints and platform lessons.
        """
        lines = []

        # Platform lessons
        lessons = self.get_lessons(platform) if platform else []
        if lessons:
            lines.append(f"LEARNED LESSONS for {platform.upper()}:")
            for l in lessons[:5]:
                lines.append(f"  • {l}")

        # Q&A hints for specific questions
        if questions:
            hints = []
            for q in questions[:10]:
                ans = self.get_answer(q, platform)
                if ans:
                    hints.append(f"  Q: '{q}' → previously answered: '{ans}'")
            if hints:
                lines.append("REMEMBERED ANSWERS (reuse if still applicable):")
                lines.extend(hints)

        # Top field memories for this platform
        with _conn() as db:
            rows = db.execute(
                "SELECT label_norm, answer FROM field_memory WHERE platform=? "
                "AND success_count > fail_count ORDER BY success_count DESC LIMIT 8",
                (platform,)
            ).fetchall()
        if rows:
            lines.append(f"KNOWN FIELD VALUES for {platform}:")
            for r in rows:
                lines.append(f"  '{r['label_norm']}' → '{r['answer']}'")

        if not lines:
            return ""
        return "\n".join(["=== AGENT MEMORY (from past applications) ===",
                           *lines,
                           "=== END MEMORY ==="])


# Singleton
_mem: AppMemory | None = None

def get_memory() -> AppMemory:
    global _mem
    if _mem is None:
        _mem = AppMemory()
    return _mem
'''

Path("applier/memory.py").write_text(MEMORY_PY, encoding="utf-8")
print("✅ Created applier/memory.py")

# ══════════════════════════════════════════════════════════════════════════════
# PATCH: applier/linkedin_applier.py — inject memory into answer_custom_question
# ══════════════════════════════════════════════════════════════════════════════
la = Path("applier/linkedin_applier.py")
lc = la.read_text(encoding="utf-8")

# Before calling answer_custom_question, check memory first
# Find the answer_custom_question function definition
OLD_AQ = 'def answer_custom_question(\n    question: str,'
NEW_AQ = '''def answer_custom_question(
    question: str,'''

# Patch the _answer_visible_questions to use memory
OLD_HINT = (
    '            answer = answer_custom_question(\n'
    '                label_text or "", "text", [], resume_text, profile, job_desc,\n'
    '                prior_answers=prior_answers\n'
    '            )'
)
NEW_HINT = (
    '            # Check memory first\n'
    '            try:\n'
    '                from applier.memory import get_memory\n'
    '                _mem_hint = get_memory().get_answer(label_text or "", platform="linkedin")\n'
    '                if _mem_hint:\n'
    '                    answer = _mem_hint\n'
    '                else:\n'
    '                    answer = answer_custom_question(\n'
    '                        label_text or "", "text", [], resume_text, profile, job_desc,\n'
    '                        prior_answers=prior_answers\n'
    '                    )\n'
    '                    if answer:\n'
    '                        get_memory().save_qa(label_text or "", answer, platform="linkedin")\n'
    '            except Exception:\n'
    '                answer = answer_custom_question(\n'
    '                    label_text or "", "text", [], resume_text, profile, job_desc,\n'
    '                    prior_answers=prior_answers\n'
    '                )'
)

if OLD_HINT in lc:
    lc = lc.replace(OLD_HINT, NEW_HINT, 1)
    print("✅ LA1: memory check before answering text questions")
else:
    print("⚠️  LA1: text question anchor not found")

# Save result to memory after application completes in fill_easy_apply
OLD_SUCCESS = (
    '            return {"success": True, "manual": False, "note": "", "apply_type": apply_type}'
)
NEW_SUCCESS = (
    '            try:\n'
    '                from applier.memory import get_memory\n'
    '                get_memory().save_application_result(\n'
    '                    job.get("url",""), "linkedin", True, prior_answers\n'
    '                )\n'
    '            except Exception:\n'
    '                pass\n'
    '            return {"success": True, "manual": False, "note": "", "apply_type": apply_type}'
)
if OLD_SUCCESS in lc:
    lc = lc.replace(OLD_SUCCESS, NEW_SUCCESS, 1)
    print("✅ LA2: save successful Q&A to memory after submit")
else:
    print("⚠️  LA2: success return anchor not found")

la.write_text(lc, encoding="utf-8")

# ══════════════════════════════════════════════════════════════════════════════
# PATCH: applier/external_applier.py — inject memory into AI prompt
# ══════════════════════════════════════════════════════════════════════════════
ea = Path("applier/external_applier.py")
ec = ea.read_text(encoding="utf-8")

OLD_SYSPROMPT = (
    '        system = (\n'
    '            "You are filling a job application form on behalf of the candidate. "'
)
NEW_SYSPROMPT = (
    '        # Inject memory context\n'
    '        _mem_ctx = ""\n'
    '        try:\n'
    '            from applier.memory import get_memory\n'
    '            _detected_platform = _detect_platform_by_url(page_snapshot[:200]) if page_snapshot else ""\n'
    '            _mem_ctx = get_memory().build_prompt_context(platform=_detected_platform)\n'
    '        except Exception:\n'
    '            pass\n'
    '        system = (\n'
    '            "You are filling a job application form on behalf of the candidate. "\n'
    '            + (_mem_ctx + "\\n\\n" if _mem_ctx else "")\n'
    '            + "'
)

# This patch is complex, let's do a simpler targeted replacement
OLD_SYS2 = (
    '        system = (\n'
    '            "You are filling a job application form on behalf of the candidate. "\n'
    '            "Analyse the form fields and return a JSON array of actions to take. "\n'
)
NEW_SYS2 = (
    '        # Inject memory context into system prompt\n'
    '        _mem_context = ""\n'
    '        try:\n'
    '            from applier.memory import get_memory\n'
    '            _mem_context = get_memory().build_prompt_context()\n'
    '        except Exception:\n'
    '            pass\n'
    '        system = (\n'
    '            "You are filling a job application form on behalf of the candidate. "\n'
    '            + (_mem_context + "\\n\\n" if _mem_context else "")\n'
    '            + "Analyse the form fields and return a JSON array of actions to take. "\n'
)
if OLD_SYS2 in ec:
    ec = ec.replace(OLD_SYS2, NEW_SYS2, 1)
    print("✅ EA1: memory injected into _ai_decide_form_actions prompt")
else:
    print("⚠️  EA1: system prompt anchor not found")

# Save result after external apply
OLD_EXT_SUCCESS = (
    '        return {"success": True, "manual": False,\n'
    '                "note": result.final_result() or "", "apply_type": "External (browser-use)"}'
)
NEW_EXT_SUCCESS = (
    '        try:\n'
    '            from applier.memory import get_memory\n'
    '            get_memory().save_application_result(url, platform, True, [])\n'
    '        except Exception:\n'
    '            pass\n'
    '        return {"success": True, "manual": False,\n'
    '                "note": result.final_result() or "", "apply_type": "External (browser-use)"}'
)
if OLD_EXT_SUCCESS in ec:
    ec = ec.replace(OLD_EXT_SUCCESS, NEW_EXT_SUCCESS, 1)
    print("✅ EA2: save successful external apply to memory")
else:
    print("⚠️  EA2: browser-use success anchor not found")

ea.write_text(ec, encoding="utf-8")

# ══════════════════════════════════════════════════════════════════════════════
# PATCH: applier/smart_filler.py — inject memory into _claude_decide
# ══════════════════════════════════════════════════════════════════════════════
sf = Path("applier/smart_filler.py")
sc = sf.read_text(encoding="utf-8")

OLD_DECIDE = (
    '    if task == "fill":\n'
    '        instruction = """Look at this job application form screenshot'
)
NEW_DECIDE = (
    '    # Load memory context\n'
    '    mem_context = ""\n'
    '    try:\n'
    '        from applier.memory import get_memory\n'
    '        _url = page.url\n'
    '        _plat = _url.split("/")[2].replace("www.","").split(".")[0] if _url else ""\n'
    '        mem_context = get_memory().build_prompt_context(platform=_plat)\n'
    '    except Exception:\n'
    '        pass\n'
    '\n'
    '    if task == "fill":\n'
    '        instruction = """Look at this job application form screenshot'
)
if OLD_DECIDE in sc:
    sc = sc.replace(OLD_DECIDE, NEW_DECIDE, 1)
    print("✅ SF1: memory context injected into _claude_decide")
else:
    print("⚠️  SF1: _claude_decide anchor not found")

# Add mem_context to the prompt
OLD_PROMPT = (
    '                    {"type": "text", "text":\n'
    '                        f"{instruction}\\n\\nCandidate: {prof_summary}\\n\\n"'
)
NEW_PROMPT = (
    '                    {"type": "text", "text":\n'
    '                        f"{instruction}\\n\\n"'
    '                        f"{mem_context + chr(10) if mem_context else ""}"'
    '                        f"Candidate: {prof_summary}\\n\\n"'
)
if OLD_PROMPT in sc:
    sc = sc.replace(OLD_PROMPT, NEW_PROMPT, 1)
    print("✅ SF2: memory added to Claude vision prompt")
else:
    print("⚠️  SF2: prompt text anchor not found")

# Save Q&A to memory after smart_fill_form succeeds
OLD_SF_SUCCESS = (
    '        _emit("apply_step", {"url": url, "step": "  ✅ Smart apply: submission confirmed"})\n'
    '                return {"success": True, "note": "Submitted via smart fill"}'
)
NEW_SF_SUCCESS = (
    '        _emit("apply_step", {"url": url, "step": "  ✅ Smart apply: submission confirmed"})\n'
    '                try:\n'
    '                    from applier.memory import get_memory\n'
    '                    _plat2 = page.url.split("/")[2].replace("www.","").split(".")[0]\n'
    '                    get_memory().save_application_result(url, _plat2, True, [])\n'
    '                except Exception:\n'
    '                    pass\n'
    '                return {"success": True, "note": "Submitted via smart fill"}'
)
if OLD_SF_SUCCESS in sc:
    sc = sc.replace(OLD_SF_SUCCESS, NEW_SF_SUCCESS, 1)
    print("✅ SF3: save successful smart apply to memory")

sf.write_text(sc, encoding="utf-8")

# ══════════════════════════════════════════════════════════════════════════════
# PATCH: applier/applier.py — save failure lessons after each job
# ══════════════════════════════════════════════════════════════════════════════
ap = Path("applier/applier.py")
ac = ap.read_text(encoding="utf-8")

# After _apply_linkedin returns manual=True, save failure
OLD_MANUAL_RET = (
    '                    _emit("session_done", {"success": 0, "manual": 0, "failed": len(jobs),\n'
    '                                           "error": str(exc)})'
)
NEW_MANUAL_RET = (
    '                    try:\n'
    '                        from applier.memory import get_memory\n'
    '                        get_memory().save_application_result(\n'
    '                            job.get("url",""), "linkedin", False, [],\n'
    '                            failure_note=str(exc)\n'
    '                        )\n'
    '                    except Exception:\n'
    '                        pass\n'
    '                    _emit("session_done", {"success": 0, "manual": 0, "failed": len(jobs),\n'
    '                                           "error": str(exc)})'
)
if OLD_MANUAL_RET in ac:
    ac = ac.replace(OLD_MANUAL_RET, NEW_MANUAL_RET, 1)
    print("✅ AP1: save crash failure to memory")
else:
    print("⚠️  AP1: crash handler anchor not found")

ap.write_text(ac, encoding="utf-8")

# ── Syntax checks ─────────────────────────────────────────────────────────────
for f in ["applier/memory.py", "applier/linkedin_applier.py",
          "applier/external_applier.py", "applier/smart_filler.py",
          "applier/applier.py"]:
    r = subprocess.run(["python", "-m", "py_compile", f], capture_output=True, text=True)
    status = "✅" if r.returncode == 0 else "❌"
    print(f"{status} Syntax: {f}" + (f"\n   {r.stderr.strip()}" if r.returncode else ""))

print("\n✅ Memory system installed. The agent will now:")
print("  • Remember answers to custom questions across applications")
print("  • Reuse successful field values on similar forms")
print("  • Learn platform-specific lessons from failures (via Claude Haiku)")
print("  • Inject all relevant memory into Claude prompts before each application")
print("  • Memory stored in: data/agent_memory.db")
