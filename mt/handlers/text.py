"""Plain text / markdown: translate line by line so blank lines and structure survive."""
from __future__ import annotations

from pathlib import Path

from ..engine import Translator
from .base import read_text_file, section_title


class TextHandler:
    extensions = (".txt", ".text", ".log")

    @staticmethod
    def _translate_lines(text: str, translator: Translator, target: str) -> str:
        lines = text.splitlines()
        return "\n".join(translator.translate_many(lines, target))

    def translate_separate(self, src: Path, dst: Path, translator: Translator, target: str) -> None:
        dst.write_text(self._translate_lines(read_text_file(src), translator, target), encoding="utf-8")

    def translate_combined(self, src: Path, dst: Path, translator: Translator,
                           targets: list[str], sheet_mode: str) -> None:
        original = read_text_file(src)
        blocks = [f"{section_title('original')}\n{original.rstrip()}"]
        for target in targets:
            blocks.append(f"{section_title(target)}\n{self._translate_lines(original, translator, target).rstrip()}")
        dst.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
