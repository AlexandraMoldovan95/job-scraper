"""CVDocument -> HTML (preview and PDF source), PDF and DOCX files."""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

from docx import Document
from docx.enum.text import WD_TAB_ALIGNMENT
from docx.shared import Cm, Pt, RGBColor
from jinja2 import Environment, FileSystemLoader

from jobscraper.config import PROJECT_ROOT
from jobscraper.cv.document import CVDocument, CVEntry
from jobscraper.models import JobPosting
from jobscraper.normalize import strip_diacritics

_TEMPLATES = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=True,
    trim_blocks=True,
    lstrip_blocks=True,
)
_PDF_TIMEOUT_SECONDS = 90
_ACCENT = RGBColor(0x2F, 0x3B, 0x52)


class CVExportError(RuntimeError):
    """Shown to the user as is, so the message is in Romanian."""


def render_html(document: CVDocument) -> str:
    return _TEMPLATES.get_template("cv.html.j2").render(cv=document)


def _slug(text: str | None) -> str:
    plain = strip_diacritics(text or "").lower()
    return re.sub(r"[^a-z0-9]+", "-", plain).strip("-")[:40].strip("-")


def file_stem(job: JobPosting, today: date) -> str:
    """"2026-10-19_firma-srl_python-developer" (ASCII, safe on Windows)."""
    parts = [today.isoformat(), _slug(job.company), _slug(job.title)]
    return "_".join(part for part in parts if part)


def focus_file_stem(focus: str, today: date) -> str:
    """"2026-10-19_cv_testare-qa" for a CV written for a type of job."""
    return "_".join(part for part in (today.isoformat(), "cv", _slug(focus)) if part)


def write_pdf(document: CVDocument, path: Path) -> Path:
    """Print the HTML with headless Chromium in a separate process.

    A subprocess keeps Playwright's own event loop away from Streamlit's
    (they clash on Windows) and a crash there cannot take the UI down.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        html_path = Path(tmp) / "cv.html"
        html_path.write_text(render_html(document), encoding="utf-8")
        try:
            result = subprocess.run(
                [sys.executable, "-m", "jobscraper.cv.pdf_worker", str(html_path), str(path)],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                timeout=_PDF_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise CVExportError("Generarea PDF-ului a durat prea mult.") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()[-1:] or ["eroare necunoscută"]
        hint = " Rulează: .venv\\Scripts\\python.exe -m playwright install chromium" if "playwright install" in result.stderr else ""
        raise CVExportError(f"PDF-ul nu a putut fi generat: {detail[0]}{hint}")
    return path


def write_docx(document: CVDocument, path: Path) -> Path:
    """Single column, real headings and bullet lists, no tables: what ATS parsers read best."""
    doc = Document()
    for section in doc.sections:
        section.top_margin = section.bottom_margin = Cm(1.6)
        section.left_margin = section.right_margin = Cm(1.8)
    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(10.5)
    normal.paragraph_format.space_after = Pt(2)

    name = doc.add_paragraph()
    run = name.add_run(document.full_name)
    run.bold = True
    run.font.size = Pt(20)
    if document.headline:
        headline = doc.add_paragraph().add_run(document.headline)
        headline.bold = True
        headline.font.size = Pt(12)
        headline.font.color.rgb = _ACCENT
    if document.contact:
        doc.add_paragraph(" · ".join(document.contact))

    def heading(text: str) -> None:
        paragraph = doc.add_paragraph()
        paragraph.paragraph_format.space_before = Pt(10)
        paragraph.paragraph_format.space_after = Pt(3)
        run = paragraph.add_run(text.upper())
        run.bold = True
        run.font.color.rgb = _ACCENT

    def entry(item: CVEntry) -> None:
        head = doc.add_paragraph()
        head.paragraph_format.space_before = Pt(4)
        head.paragraph_format.tab_stops.add_tab_stop(Cm(17.4), WD_TAB_ALIGNMENT.RIGHT)
        head.add_run(item.title + (f" — {item.organization}" if item.organization else "")).bold = True
        if item.period:
            head.add_run(f"\t{item.period}")
        sub = " · ".join(filter(None, (item.location, item.link)))
        if sub:
            doc.add_paragraph().add_run(sub).italic = True
        if item.text:
            doc.add_paragraph(item.text)
        for bullet in item.bullets:
            doc.add_paragraph(bullet, style="List Bullet")
        if item.technologies:
            doc.add_paragraph(f"{document.labels['technologies']}: {', '.join(item.technologies)}")

    if document.summary:
        heading(document.labels["summary"])
        doc.add_paragraph(document.summary)
    if document.skills:
        heading(document.labels["skills"])
        doc.add_paragraph(", ".join(document.skills))
    for key in ("experience", "projects", "education", "courses"):
        items: list[CVEntry] = getattr(document, key)
        if items:
            heading(document.labels[key])
            for item in items:
                entry(item)
    if document.languages:
        heading(document.labels["languages"])
        doc.add_paragraph(" · ".join(document.languages))

    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    return path
