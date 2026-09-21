"""CSV files (comma / semicolon / tab auto-detected)."""
from __future__ import annotations

import csv
import io
from pathlib import Path

from ..engine import Translator
from .base import read_text_file
from .table import Grid, combined_grid, translate_grid


def _read(path: Path) -> tuple[Grid, str]:
    text = read_text_file(path)
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ","
    return list(csv.reader(io.StringIO(text), delimiter=delimiter)), delimiter


def _write(path: Path, grid: Grid, delimiter: str) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as fh:   # BOM so Excel opens Hindi etc. correctly
        csv.writer(fh, delimiter=delimiter).writerows(
            [["" if v is None else v for v in row] for row in grid])


class CsvHandler:
    extensions = (".csv", ".tsv")

    def translate_separate(self, src: Path, dst: Path, translator: Translator, target: str) -> None:
        grid, delimiter = _read(src)
        _write(dst, translate_grid(grid, translator, target), delimiter)

    def translate_combined(self, src: Path, dst: Path, translator: Translator,
                           targets: list[str], sheet_mode: str) -> None:
        grid, delimiter = _read(src)
        _write(dst, combined_grid(grid, translator, targets), delimiter)
