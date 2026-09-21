"""Handler registry: pick a handler by file extension."""
from __future__ import annotations

from pathlib import Path

from .csv_ import CsvHandler
from .docx_ import DocxHandler
from .json_ import JsonHandler
from .markdown import MarkdownHandler
from .subtitle import SubtitleHandler
from .text import TextHandler
from .xlsx import XlsxHandler

HANDLERS = (TextHandler(), MarkdownHandler(), CsvHandler(), XlsxHandler(), DocxHandler(),
            JsonHandler(), SubtitleHandler())
SUPPORTED_EXTENSIONS = tuple(ext for h in HANDLERS for ext in h.extensions)


def handler_for(path: Path):
    ext = path.suffix.lower()
    for handler in HANDLERS:
        if ext in handler.extensions:
            return handler
    return None
