"""Grid logic shared by CSV and XLSX: which cells to translate and how to lay out combined output.

A grid is a list of rows; each row is a list of cell values (str, number, None ...).
Only str cells that pass the engine's skip filter are translated. Row 0 is treated as the header.
"""
from __future__ import annotations

from typing import Any

from ..engine import Translator, should_skip
from ..languages import language_name

Grid = list[list[Any]]


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and not should_skip(value)


def _pad(grid: Grid) -> Grid:
    width = max((len(r) for r in grid), default=0)
    return [list(r) + [None] * (width - len(r)) for r in grid]


def translate_grid(grid: Grid, translator: Translator, target: str, translate_header: bool = True) -> Grid:
    """Return a new grid with every text cell translated (same shape as the input)."""
    grid = _pad(grid)
    positions = [(r, c) for r, row in enumerate(grid) for c, v in enumerate(row)
                 if _is_text(v) and (translate_header or r > 0)]
    translated = translator.translate_many([grid[r][c] for r, c in positions], target)
    out = [list(row) for row in grid]
    for (r, c), value in zip(positions, translated):
        out[r][c] = value
    return out


def text_columns(grid: Grid) -> list[int]:
    """Indexes of columns that contain at least one translatable cell below the header."""
    grid = _pad(grid)
    width = len(grid[0]) if grid else 0
    return [c for c in range(width) if any(_is_text(row[c]) for row in grid[1:])]


def combined_grid(grid: Grid, translator: Translator, targets: list[str]) -> Grid:
    """One sheet: every original column, immediately followed by its translation per language.

        Name | Name (Hindi) | Name (Marathi) | Price | Description | Description (Hindi) | ...
    """
    grid = _pad(grid)
    if not grid:
        return []
    cols = text_columns(grid)
    per_lang: dict[str, Grid] = {t: translate_grid(grid, translator, t, translate_header=False) for t in targets}
    header = grid[0]
    out: Grid = []
    for r, row in enumerate(grid):
        new_row: list[Any] = []
        for c, value in enumerate(row):
            new_row.append(value)
            if c not in cols:
                continue
            for t in targets:
                if r == 0:
                    base = header[c] if header[c] not in (None, "") else f"Column {c + 1}"
                    new_row.append(f"{base} ({language_name(t)})")
                else:
                    new_row.append(per_lang[t][r][c])
        out.append(new_row)
    return out
