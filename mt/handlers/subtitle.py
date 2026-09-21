"""SRT / WebVTT subtitles: only cue text is translated; indexes, timecodes and headers stay byte-identical.

separate -> same cues, translated text.
combined -> each cue shows the original line(s) followed by one line per language.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..engine import Translator
from .base import read_text_file

_TIMECODE = re.compile(r"^\s*\d{1,2}:\d{2}:\d{2}[,.]\d{3}\s*-->|^\s*\d{2}:\d{2}[,.]\d{3}\s*-->")


@dataclass
class Cue:
    header: list[str]          # index line (srt) and/or timecode line, kept verbatim
    text: list[str]            # spoken lines


def parse(text: str) -> tuple[list[str], list[Cue]]:
    """Return (preamble lines e.g. 'WEBVTT', cues). Blocks are separated by blank lines."""
    lines = text.split("\n")
    preamble: list[str] = []
    cues: list[Cue] = []
    block: list[str] = []

    def flush() -> None:
        if not block:
            return
        tc = next((i for i, l in enumerate(block) if _TIMECODE.match(l)), None)
        if tc is None:                                   # not a cue (WEBVTT header, NOTE, STYLE ...)
            preamble.extend(block + [""])
        else:
            cues.append(Cue(header=block[:tc + 1], text=block[tc + 1:]))
        block.clear()

    for line in lines:
        if line.strip() == "":
            flush()
        else:
            block.append(line)
    flush()
    return preamble, cues


def render(preamble: list[str], cues: list[Cue]) -> str:
    blocks = ["\n".join(c.header + c.text) for c in cues]
    head = "\n".join(preamble)
    return (head + "\n" if head else "") + "\n\n".join(blocks) + "\n"


class SubtitleHandler:
    extensions = (".srt", ".vtt")

    def translate_separate(self, src: Path, dst: Path, translator: Translator, target: str) -> None:
        preamble, cues = parse(read_text_file(src))
        flat = [l for c in cues for l in c.text]
        translated = iter(translator.translate_many(flat, target))
        out = [Cue(c.header, [next(translated) for _ in c.text]) for c in cues]
        dst.write_text(render(preamble, out), encoding="utf-8")

    def translate_combined(self, src: Path, dst: Path, translator: Translator,
                           targets: list[str], sheet_mode: str) -> None:
        preamble, cues = parse(read_text_file(src))
        flat = [l for c in cues for l in c.text]
        per_lang = {t: iter(translator.translate_many(flat, t)) for t in targets}
        out = []
        for c in cues:
            lines = list(c.text)
            for t in targets:
                lines += [next(per_lang[t]) for _ in c.text]
            out.append(Cue(c.header, lines))
        dst.write_text(render(preamble, out), encoding="utf-8")
