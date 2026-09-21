"""JSON (i18n files, API dumps): every string VALUE is translated, keys and structure untouched.

separate -> same structure, translated.
combined -> {"original": <doc>, "hi": <doc>, "pa": <doc>, ...}
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..engine import Translator
from .base import read_text_file


def _collect(node: Any, out: list[str]) -> None:
    if isinstance(node, str):
        out.append(node)
    elif isinstance(node, dict):
        for v in node.values():
            _collect(v, out)
    elif isinstance(node, list):
        for v in node:
            _collect(v, out)


def _rebuild(node: Any, values: list[str]) -> Any:
    """Return a new structure with strings taken from `values` in traversal order (never mutates input)."""
    if isinstance(node, str):
        return values.pop(0)
    if isinstance(node, dict):
        return {k: _rebuild(v, values) for k, v in node.items()}
    if isinstance(node, list):
        return [_rebuild(v, values) for v in node]
    return node


def translate_json(data: Any, translator: Translator, target: str) -> Any:
    strings: list[str] = []
    _collect(data, strings)
    return _rebuild(data, translator.translate_many(strings, target))


def _dump(dst: Path, data: Any) -> None:
    dst.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class JsonHandler:
    extensions = (".json",)

    def translate_separate(self, src: Path, dst: Path, translator: Translator, target: str) -> None:
        _dump(dst, translate_json(json.loads(read_text_file(src)), translator, target))

    def translate_combined(self, src: Path, dst: Path, translator: Translator,
                           targets: list[str], sheet_mode: str) -> None:
        data = json.loads(read_text_file(src))
        _dump(dst, {"original": data, **{t: translate_json(data, translator, t) for t in targets}})
