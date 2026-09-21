"""Excel workbooks via openpyxl.

separate         -> same layout, text cells translated. Small sheets keep formatting (in-place edit);
                    sheets above BIG_SHEET_ROWS are rebuilt with the streaming writer (values only) so
                    100k+ row files stay fast and memory-safe.
combined/columns -> each sheet rebuilt as: Col | Col (Hindi) | Col (Punjabi) | ...   (ONE sheet, all languages)
combined/sheets  -> original sheets kept + a translated copy per language ("Sheet1 (hi)", formatting kept).
"""
from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from ..engine import Translator
from .table import Grid, combined_grid, translate_grid

MAX_COLUMN_WIDTH = 60
BIG_SHEET_ROWS = 20_000


def read_grids(path: Path) -> list[tuple[str, Grid]]:
    """[(sheet title, grid)] using read-only streaming mode (fast for huge files)."""
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        return [(ws.title, [list(row) for row in ws.iter_rows(values_only=True)]) for ws in wb.worksheets]
    finally:
        wb.close()


def _write_streaming(dst: Path, sheets: list[tuple[str, Grid]]) -> None:
    wb = Workbook(write_only=True)
    bold = Font(bold=True)
    for title, grid in sheets:
        ws = wb.create_sheet(title)
        widths: dict[int, int] = {}
        for row in grid[:500]:                                       # sample widths from the first rows
            for c, v in enumerate(row, start=1):
                if v is not None:
                    widths[c] = min(MAX_COLUMN_WIDTH, max(widths.get(c, 8), len(str(v)) + 2))
        for c, w in widths.items():                                  # must be set before rows are appended
            ws.column_dimensions[get_column_letter(c)].width = w
        for r, row in enumerate(grid):
            if r == 0:
                cells = []
                for v in row:
                    cell = WriteOnlyCell(ws, value=v)
                    cell.font = bold
                    cells.append(cell)
                ws.append(cells)
            else:
                ws.append(row)
    wb.save(dst)


def _translate_in_place(ws, translator: Translator, target: str) -> None:
    grid = translate_grid([list(r) for r in ws.iter_rows(values_only=True)], translator, target)
    for r, row in enumerate(grid, start=1):
        for c, value in enumerate(row, start=1):
            cell = ws.cell(r, c)
            if isinstance(value, str) and cell.value != value:
                cell.value = value


class XlsxHandler:
    extensions = (".xlsx", ".xlsm")

    def translate_separate(self, src: Path, dst: Path, translator: Translator, target: str) -> None:
        sheets = read_grids(src)
        if max((len(g) for _, g in sheets), default=0) > BIG_SHEET_ROWS:
            _write_streaming(dst, [(t, translate_grid(g, translator, target)) for t, g in sheets])
            return
        wb = load_workbook(src)
        for ws in wb.worksheets:
            _translate_in_place(ws, translator, target)
        wb.save(dst)

    def translate_combined(self, src: Path, dst: Path, translator: Translator,
                           targets: list[str], sheet_mode: str) -> None:
        if sheet_mode == "sheets":
            self._combined_sheets(src, dst, translator, targets)
        else:
            _write_streaming(dst, [(t, combined_grid(g, translator, targets)) for t, g in read_grids(src)])

    @staticmethod
    def _combined_sheets(src: Path, dst: Path, translator: Translator, targets: list[str]) -> None:
        wb = load_workbook(src)
        for ws in list(wb.worksheets):
            for target in targets:
                copy = wb.copy_worksheet(ws)
                copy.title = f"{ws.title} ({target})"[:31]     # Excel sheet-name limit
                _translate_in_place(copy, translator, target)
        wb.save(dst)
