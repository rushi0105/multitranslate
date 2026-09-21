"""Markdown: line based like TXT, but fenced code blocks are copied verbatim and leading markers
(#, -, 1., >, |) are split off so headings/lists/quotes/tables keep their structure.
Inline code, links and URLs are protected by the engine's placeholder layer."""
from __future__ import annotations

import re
from pathlib import Path

from ..engine import Translator
from .base import read_text_file, section_title

_FENCE = re.compile(r"^\s*(```|~~~)")
_PREFIX = re.compile(r"^(\s*(?:#{1,6}\s+|[-*+]\s+(?:\[[ xX]\]\s+)?|\d+[.)]\s+|>\s*)+)")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def split_lines(text: str) -> tuple[list[str], list[int], list[str]]:
    """Return (all lines, indexes of translatable lines, their translatable payloads)."""
    lines = text.split("\n")
    idx, payload = [], []
    in_fence = False
    for i, line in enumerate(lines):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence or not line.strip() or _TABLE_SEP.match(line):
            continue
        idx.append(i)
        payload.append(line)
    return lines, idx, payload


def translate_markdown(text: str, translator: Translator, target: str) -> str:
    lines, idx, payload = split_lines(text)
    prefixes, bodies, cells = [], [], []
    for line in payload:
        m = _PREFIX.match(line)
        prefix = m.group(1) if m else ""
        body = line[len(prefix):]
        prefixes.append(prefix)
        if body.strip().startswith("|"):                          # table row: translate cell by cell
            cells.append([c.strip() for c in body.split("|")])
            bodies.append(None)
        else:
            cells.append(None)
            bodies.append(body)
    flat = [b for b in bodies if b is not None] + [c for row in cells if row for c in row]
    translated = iter(translator.translate_many(flat, target))
    body_out = [next(translated) if b is not None else None for b in bodies]
    out = list(lines)
    for k, i in enumerate(idx):
        if cells[k] is not None:
            row = [next(translated) for _ in cells[k]]
            out[i] = prefixes[k] + "|".join(f" {c} " if c else "" for c in row).strip()
        else:
            out[i] = prefixes[k] + body_out[k]
    return "\n".join(out)


class MarkdownHandler:
    extensions = (".md", ".markdown")

    def translate_separate(self, src: Path, dst: Path, translator: Translator, target: str) -> None:
        dst.write_text(translate_markdown(read_text_file(src), translator, target), encoding="utf-8")

    def translate_combined(self, src: Path, dst: Path, translator: Translator,
                           targets: list[str], sheet_mode: str) -> None:
        original = read_text_file(src)
        blocks = [f"{section_title('original')}\n\n{original.rstrip()}"]
        for t in targets:
            blocks.append(f"{section_title(t)}\n\n{translate_markdown(original, translator, t).rstrip()}")
        dst.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
