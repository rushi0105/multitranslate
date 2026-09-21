"""Word documents via python-docx: paragraphs and table cells, formatting of the first run kept."""
from __future__ import annotations

from pathlib import Path

from docx import Document

from ..engine import Translator
from .base import section_title


def _paragraphs(doc):
    for p in doc.paragraphs:
        yield p
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    yield p


def _set_text(paragraph, text: str) -> None:
    """Put translated text in the first run (keeps its bold/size) and clear the rest."""
    runs = paragraph.runs
    if not runs:
        paragraph.add_run(text)
        return
    runs[0].text = text
    for run in runs[1:]:
        run.text = ""


def _translate_document(src: Path, translator: Translator, target: str):
    doc = Document(str(src))
    paragraphs = [p for p in _paragraphs(doc) if p.text.strip()]
    translated = translator.translate_many([p.text for p in paragraphs], target)
    for p, text in zip(paragraphs, translated):
        _set_text(p, text)
    return doc


class DocxHandler:
    extensions = (".docx",)

    def translate_separate(self, src: Path, dst: Path, translator: Translator, target: str) -> None:
        _translate_document(src, translator, target).save(str(dst))

    def translate_combined(self, src: Path, dst: Path, translator: Translator,
                           targets: list[str], sheet_mode: str) -> None:
        """Original document followed by a page break and the full text in each language."""
        out = Document(str(src))
        for target in targets:
            out.add_page_break()
            out.add_heading(section_title(target), level=1)
            translated = _translate_document(src, translator, target)
            for p in translated.paragraphs:
                out.add_paragraph(p.text, style=p.style.name if p.style.name in ("Normal", "Heading 1", "Heading 2", "Heading 3") else None)
        out.save(str(dst))
