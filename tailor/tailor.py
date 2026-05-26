"""
tailor.py — generate a tailored resume + cover letter for each matched job.

Pipeline:
  1. Claude suggests changes (JSON) → applied with python-docx
  2. .docx → PDF via mammoth (docx→HTML) + weasyprint (HTML→PDF)
     Falls back to LibreOffice soffice if weasyprint is unavailable.

Base resume must be resume_en.docx in cv_root/agent/.
Cover letter template can be .docx or .pdf (text extracted via pdfplumber).
"""

import json
import logging
import os
import re
import shutil
from datetime import date
from pathlib import Path

import anthropic
import pdfplumber
from docx import Document
from dotenv import load_dotenv

from core.config import load_config

load_dotenv()
log = logging.getLogger(__name__)

_MODEL = "claude-sonnet-4-6"
_WML = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _slugify(text: str) -> str:
    return re.sub(r"[^\w-]", "_", text.strip())[:40]


def _archive_folder(job: dict, cfg: dict) -> Path:
    history: Path = cfg["paths"]["history_folder"]
    folder = history / f"{_slugify(job.get('company', 'Unknown'))}_{_slugify(job.get('title', 'Role'))}_{date.today().isoformat()}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


# ── PDF export ────────────────────────────────────────────────────────────────

def _export_docx_to_pdf(docx_path: Path, pdf_path: Path) -> None:
    """Convert .docx → PDF.  Primary: mammoth + weasyprint.  Fallback: LibreOffice."""
    try:
        import mammoth
        import weasyprint

        with open(docx_path, "rb") as f:
            result = mammoth.convert_to_html(f)
        html_body = result.value

        html = (
            "<!DOCTYPE html><html lang='en'><head>"
            "<meta charset='utf-8'>"
            "<style>"
            "@page{margin:2cm}"
            "body{font-family:Arial,sans-serif;font-size:10pt;line-height:1.4}"
            "h1{font-size:16pt;margin-bottom:4pt}"
            "h2{font-size:12pt;margin-top:12pt;margin-bottom:4pt;border-bottom:1px solid #ccc}"
            "p,li{margin:4pt 0}"
            "ul{padding-left:20pt}"
            "</style></head>"
            f"<body>{html_body}</body></html>"
        )
        weasyprint.HTML(string=html).write_pdf(str(pdf_path))
        log.info("PDF exported (weasyprint): %s", pdf_path.name)
        return

    except ImportError as exc:
        log.warning("mammoth/weasyprint not available (%s) — trying LibreOffice", exc)
    except Exception as exc:
        log.warning("weasyprint export failed (%s) — trying LibreOffice", exc)

    # Fallback: LibreOffice headless
    try:
        import subprocess
        result = subprocess.run(
            ["soffice", "--headless", "--convert-to", "pdf",
             "--outdir", str(pdf_path.parent), str(docx_path)],
            capture_output=True, timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.decode())
        generated = pdf_path.parent / (docx_path.stem + ".pdf")
        if generated.exists() and generated != pdf_path:
            generated.rename(pdf_path)
        log.info("PDF exported (LibreOffice): %s", pdf_path.name)
        return
    except FileNotFoundError:
        pass
    except Exception as exc:
        log.warning("LibreOffice export also failed (%s)", exc)

    # Last resort: keep the .docx for manual conversion
    shutil.copy2(docx_path, pdf_path.with_suffix(".docx"))
    log.warning("No PDF converter available — saved .docx instead: %s", pdf_path.with_suffix(".docx"))


# ── Section reordering ────────────────────────────────────────────────────────

def _reorder_sections(doc: Document, section_order: list[str]) -> None:
    """Reorder document body sections in-place according to section_order."""
    if not section_order:
        return

    body = doc.element.body

    def get_text(el) -> str:
        return "".join(t.text or "" for t in el.iter(f"{{{_WML}}}t"))

    def is_heading(el) -> bool:
        pStyle = el.find(f".//{{{_WML}}}pStyle")
        if pStyle is None:
            return False
        val = pStyle.get(f"{{{_WML}}}val", "")
        return "heading" in val.lower()

    # sectPr must stay last — pull it out
    sect_pr = body.find(f"{{{_WML}}}sectPr")
    all_children = [c for c in list(body) if c is not sect_pr]

    # Group children: header_block (pre-first-heading) + sections list
    header_block: list = []
    sections: list[tuple[str, object, list]] = []  # (heading_text, heading_el, content_els)
    current: list | None = None

    for child in all_children:
        if child.tag == f"{{{_WML}}}p" and is_heading(child):
            heading_text = get_text(child).strip()
            current = []
            sections.append((heading_text, child, current))
        elif current is None:
            header_block.append(child)
        else:
            current.append(child)

    # Match requested names to actual sections (case-insensitive substring)
    remaining = list(sections)
    ordered: list[tuple] = []
    for req in section_order:
        for sec in remaining:
            if req.lower() in sec[0].lower() or sec[0].lower() in req.lower():
                ordered.append(sec)
                remaining.remove(sec)
                break
    ordered.extend(remaining)  # append unmatched sections at end

    # Rebuild body
    for child in list(body):
        if child is not sect_pr:
            body.remove(child)
    for el in header_block:
        body.append(el)
    for _, h_el, content in ordered:
        body.append(h_el)
        for el in content:
            body.append(el)
    if sect_pr is not None:
        body.append(sect_pr)


# ── Resume tailoring ──────────────────────────────────────────────────────────

_RESUME_SYSTEM = """\
You are a professional resume coach helping a candidate tailor their resume.
IMPORTANT: Never invent experience or skills. Only reorder, re-emphasise, or surface
keywords that are genuinely present in the candidate's background.
Return ONLY valid JSON — no markdown fences, no prose.
"""

_RESUME_PROMPT = """\
## Job Description
{jd}

## Current Resume Text
{resume}

Return a JSON object:
{{
  "summary_tweak": "<revised 2-3 sentence professional summary, or null>",
  "keywords_to_add": ["keyword1", "keyword2"],
  "section_order": ["Experience", "Skills", "Education"],
  "bullet_replacements": [
    {{"original": "exact bullet text from resume", "replacement": "improved version"}}
  ]
}}

All changes must be factually accurate to the resume provided.
Only include bullet_replacements where you can quote the original text exactly.
"""

_CL_SYSTEM = """\
You are a professional cover letter writer.
Preserve the candidate's voice and style from the template.
Return ONLY the full cover letter body text — no JSON, no metadata, no subject line.
"""

_CL_PROMPT = """\
## Cover Letter Template
{template}

## Job Description
{jd}

## Company Name
{company}

Write a tailored cover letter for this specific role and company.
Preserve the original tone, structure, and personal voice exactly.
Do not fabricate achievements — draw only from what the template shows.
"""


def _read_docx_text(path: Path) -> str:
    doc = Document(path)
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


def _read_pdf_text(path: Path) -> str:
    parts: list[str] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                parts.append(t)
    return "\n".join(parts)


def _read_template_text(path: Path) -> str:
    return _read_pdf_text(path) if path.suffix.lower() == ".pdf" else _read_docx_text(path)


def _apply_resume_changes(base_docx: Path, out_docx: Path, changes: dict) -> None:
    doc = Document(base_docx)

    # 1. Reorder sections
    section_order = changes.get("section_order", [])
    if section_order:
        _reorder_sections(doc, section_order)

    # 2. Replace professional summary
    summary = changes.get("summary_tweak")
    if summary:
        for para in doc.paragraphs:
            if len(para.text) > 60 and para.style.name.startswith("Normal"):
                para.clear()
                para.add_run(summary)
                break

    # 3. Apply bullet replacements (match against exact original text)
    replacements = {r["original"]: r["replacement"]
                    for r in changes.get("bullet_replacements", [])
                    if r.get("original") and r.get("replacement")}
    for para in doc.paragraphs:
        for orig, repl in replacements.items():
            if orig in para.text:
                for run in para.runs:
                    if orig in run.text:
                        run.text = run.text.replace(orig, repl)
                        break

    # 4. Add keywords to Skills section
    keywords = changes.get("keywords_to_add", [])
    if keywords:
        in_skills = False
        for para in doc.paragraphs:
            text_lower = para.text.strip().lower()
            if "skill" in text_lower and para.style.name.lower().startswith("heading"):
                in_skills = True
                continue
            if in_skills and para.style.name.lower().startswith("heading"):
                break
            if in_skills and para.text.strip() and para.runs:
                new_kw = [k for k in keywords if k.lower() not in para.text.lower()]
                if new_kw:
                    para.runs[-1].text += ", " + ", ".join(new_kw)
                break

    doc.save(out_docx)


def _apply_cover_letter(template_docx: Path, out_docx: Path, body_text: str) -> None:
    doc = Document(template_docx)
    body_paragraphs = [p for p in doc.paragraphs if p.text.strip()]
    # Preserve first 2 paragraphs (name/address header), clear the rest
    for para in body_paragraphs[2:]:
        para.clear()
    # Append new body text
    for line in body_text.split("\n\n"):
        line = line.strip()
        if line:
            p = doc.add_paragraph(line)
            p.style = doc.styles["Normal"]
    doc.save(out_docx)


def _build_cl_docx_from_text(body_text: str, out_path: Path) -> None:
    """Build a cover letter .docx from scratch (used when template is a PDF)."""
    doc = Document()
    for para_text in body_text.split("\n\n"):
        para_text = para_text.strip()
        if para_text:
            doc.add_paragraph(para_text)
    doc.save(out_path)


# ── Public API ────────────────────────────────────────────────────────────────

def create_docs(job: dict, cfg: dict | None = None) -> Path:
    """
    Build tailored resume + cover letter for *job*.
    Returns the archive folder path.
    """
    if cfg is None:
        cfg = load_config()

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    archive = _archive_folder(job, cfg)

    # Save raw job data
    (archive / "job_description.txt").write_text(job.get("description", ""), encoding="utf-8")
    (archive / "match_score.json").write_text(
        json.dumps({
            "match_score":           job.get("match_score"),
            "interview_chance":      job.get("interview_chance"),
            "german_level_required": job.get("german_level_required"),
            "match_summary":         job.get("match_summary"),
        }, indent=2),
        encoding="utf-8",
    )

    cv_root: Path = cfg["paths"]["cv_root"]
    resume_docx  = cv_root / "agent" / "resume_en.docx"
    cl_template  = cfg["paths"]["cover_letter_template"]

    if not resume_docx.exists():
        log.warning("resume_en.docx not found at %s — skipping resume tailoring", resume_docx)
    if not cl_template.exists():
        log.warning("cover_letter_template not found at %s — skipping CL", cl_template)

    # ── Tailor resume ─────────────────────────────────────────────────────────
    if resume_docx.exists():
        resume_text = _read_docx_text(resume_docx)
        resp = client.messages.create(
            model=_MODEL,
            max_tokens=1024,
            system=_RESUME_SYSTEM,
            messages=[{"role": "user", "content": _RESUME_PROMPT.format(
                jd=job["description"][:5000],
                resume=resume_text[:4000],
            )}],
        )
        try:
            changes = json.loads(resp.content[0].text.strip())
        except json.JSONDecodeError:
            log.warning("Resume tailoring returned invalid JSON — using base resume unchanged")
            changes = {}

        out_resume_docx = archive / "resume_en.docx"
        _apply_resume_changes(resume_docx, out_resume_docx, changes)
        _export_docx_to_pdf(out_resume_docx, archive / "resume_en.pdf")
        log.info("Resume tailored → %s", archive.name)

    # ── Tailor cover letter ───────────────────────────────────────────────────
    if cl_template.exists():
        cl_text = _read_template_text(cl_template)
        resp = client.messages.create(
            model=_MODEL,
            max_tokens=1024,
            system=_CL_SYSTEM,
            messages=[{"role": "user", "content": _CL_PROMPT.format(
                template=cl_text,
                jd=job["description"][:4000],
                company=job.get("company", "the company"),
            )}],
        )
        cl_body = resp.content[0].text.strip()
        out_cl_docx = archive / "cover_letter.docx"
        if cl_template.suffix.lower() == ".docx":
            _apply_cover_letter(cl_template, out_cl_docx, cl_body)
        else:
            _build_cl_docx_from_text(cl_body, out_cl_docx)
        _export_docx_to_pdf(out_cl_docx, archive / "cover_letter.pdf")
        log.info("Cover letter tailored → %s", archive.name)

    log.info("Archive: %s", archive)
    return archive


if __name__ == "__main__":
    from config import setup_logging
    cfg = load_config()
    setup_logging(cfg)
    sample_job = {
        "title": "Data Engineer",
        "company": "TestCorp GmbH",
        "location": "Berlin",
        "url": "https://example.com/job/1",
        "description": "Looking for a Data Engineer with Python, Airflow, dbt, and SQL.",
        "source": "LinkedIn",
        "match_score": 85,
        "interview_chance": "high",
        "german_level_required": "B1",
        "match_summary": "Strong match on Python and data pipeline skills.",
    }
    path = create_docs(sample_job, cfg)
    print(f"Archive: {path}")
