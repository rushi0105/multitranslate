"""Shared handler contract. Each handler translates one file type in two modes:

- separate: one output file per target language (same layout as the source).
- combined: ONE output file that contains every target language
  (extra columns in sheets, sections in text/docx, keyed objects in json).
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..engine import Translator
from ..languages import language_name


class Handler(Protocol):
    extensions: tuple[str, ...]

    def translate_separate(self, src: Path, dst: Path, translator: Translator, target: str) -> None: ...

    def translate_combined(self, src: Path, dst: Path, translator: Translator,
                           targets: list[str], sheet_mode: str) -> None: ...


def section_title(code: str) -> str:
    if code == "original":
        return "===== Original ====="
    return f"===== {language_name(code)} ({code}) ====="


def read_text_file(path: Path) -> str:
    """UTF-8 first (with BOM), then Windows-1252 so old Excel/Notepad exports still open.
    Line endings are normalised to \\n so outputs never get doubled blank lines on Windows."""
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")
