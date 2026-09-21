"""Placeholder protection + glossary substitution.

Before a string goes to a provider, fragments that must survive untouched (i18n variables, HTML tags,
URLs, emails, inline code, hashtags, glossary terms ...) are swapped for numbered tokens `⟦N⟧`, which
Google/Bing/MyMemory leave alone (verified empirically for hi/pa/ja/ar). After translation the tokens
are swapped back - glossary terms become their per-language translation if the glossary provides one.
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

TOKEN_OPEN, TOKEN_CLOSE = "⟦", "⟧"        # ⟦ ⟧
_TOKEN_RE = re.compile(rf"{TOKEN_OPEN}\s*(\d+)\s*{TOKEN_CLOSE}")

PLACEHOLDER_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"```.*?```", re.S),                     # fenced code (single-line use)
    re.compile(r"`[^`\n]+`"),                            # inline code
    re.compile(r"\{\{\s*[^{}]+?\s*\}\}"),                # {{ mustache }}
    re.compile(r"\{[A-Za-z_][\w.]*\}|\{\d+\}"),          # {name} {0}
    re.compile(r"%\([A-Za-z_]\w*\)[sdifr]|%[sdif]"),     # %(name)s %s
    re.compile(r"\$\{[^}]+\}|\$[A-Za-z_]\w*"),           # ${var} $var
    re.compile(r"</?[A-Za-z][^<>]*>"),                   # <b> </a> <br/>
    re.compile(r"https?://[^\s<>\"']+"),                 # URLs
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),             # emails
    re.compile(r"(?<![\w&])[#@][A-Za-z_]\w{1,}"),        # #hashtag @mention
    re.compile(r"&[a-z]+;|&#\d+;"),                      # HTML entities
    re.compile(r"\\n|\\t"),                              # literal escape sequences in i18n files
)


@dataclass
class Glossary:
    """term -> {lang: translation}. An empty/missing translation means: keep the term as-is.

    CSV format:   term,hi,pa        (header row; blank cell = keep term)
    JSON format:  {"Brand": {"hi": "ब्रांड"}, "Veda": {}}
    """
    entries: dict[str, dict[str, str]] = field(default_factory=dict)
    _pattern: re.Pattern | None = None

    @classmethod
    def load(cls, path: Path) -> "Glossary":
        if path.suffix.lower() == ".json":
            raw = json.loads(path.read_text(encoding="utf-8"))
            entries = {k: {lang: v for lang, v in (val or {}).items() if v} for k, val in raw.items()}
            return cls(entries)
        with path.open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.reader(fh))
        if not rows:
            return cls()
        header = [h.strip() for h in rows[0]]
        entries = {}
        for row in rows[1:]:
            if not row or not row[0].strip():
                continue
            entries[row[0].strip()] = {header[i]: cell.strip() for i, cell in enumerate(row[1:], start=1)
                                       if i < len(header) and cell.strip()}
        return cls(entries)

    def pattern(self) -> re.Pattern | None:
        if not self.entries:
            return None
        if self._pattern is None:
            terms = sorted(self.entries, key=len, reverse=True)          # longest match first
            self._pattern = re.compile(r"(?<!\w)(" + "|".join(re.escape(t) for t in terms) + r")(?!\w)", re.I)
        return self._pattern

    def lookup(self, term: str) -> dict[str, str]:
        if term in self.entries:
            return self.entries[term]
        lowered = term.lower()
        for key, val in self.entries.items():
            if key.lower() == lowered:
                return val
        return {}


@dataclass
class Protected:
    text: str                                  # text with tokens
    slots: list[tuple[str, bool]]              # (original fragment, is_glossary_term)


class Protector:
    def __init__(self, enabled: bool = True, glossary: Glossary | None = None):
        self.enabled = enabled
        self.glossary = glossary or Glossary()

    # -------------------------------------------------------------- protect
    def protect(self, text: str) -> Protected:
        slots: list[tuple[str, bool]] = []
        if not self.enabled and not self.glossary.entries:
            return Protected(text, slots)
        spans: list[tuple[int, int, bool]] = []
        gpat = self.glossary.pattern()
        if gpat:
            spans += [(m.start(), m.end(), True) for m in gpat.finditer(text)]
        if self.enabled:
            for pat in PLACEHOLDER_PATTERNS:
                spans += [(m.start(), m.end(), False) for m in pat.finditer(text)]
        spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
        out, pos = [], 0
        for start, end, is_term in spans:
            if start < pos:                         # overlaps an earlier (longer) match
                continue
            out.append(text[pos:start])
            out.append(f"{TOKEN_OPEN}{len(slots)}{TOKEN_CLOSE}")
            slots.append((text[start:end], is_term))
            pos = end
        out.append(text[pos:])
        return Protected("".join(out), slots)

    # -------------------------------------------------------------- restore
    def restore(self, translated: str, protected: Protected, target: str) -> tuple[str, list[str]]:
        """Swap tokens back. Returns (text, warnings). Missing tokens are appended so nothing is lost."""
        if not protected.slots:
            return translated, []
        seen: set[int] = set()

        def repl(m: re.Match) -> str:
            idx = int(m.group(1))
            if idx >= len(protected.slots):
                return m.group(0)
            seen.add(idx)
            return self._value(protected.slots[idx], target)

        text = _TOKEN_RE.sub(repl, translated)
        warnings = []
        missing = [i for i in range(len(protected.slots)) if i not in seen]
        if missing:
            text = text.rstrip() + " " + " ".join(self._value(protected.slots[i], target) for i in missing)
            warnings.append(f"{len(missing)} placeholder(s) moved to end of: {protected.text[:60]!r}")
        return text, warnings

    def _value(self, slot: tuple[str, bool], target: str) -> str:
        original, is_term = slot
        if not is_term:
            return original
        return self.glossary.lookup(original).get(target) or original
