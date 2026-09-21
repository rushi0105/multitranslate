"""Translation engine: dedup -> skip filter -> SQLite cache -> batched, multi-threaded provider calls.

Google (free gtx endpoint) is primary; Bing is best-effort; MyMemory is the reliable last resort.
Everything translated is written to the cache every `autosave_every` strings, so a crash loses at most
that many network results and a re-run resumes instantly from disk.
"""
from __future__ import annotations

import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

from .cache import DEFAULT_CACHE, SqliteCache
from .hinglish import LATIN_RUN, Transliterator, is_hinglish
from .protect import _TOKEN_RE, Glossary, Protector
from .providers import (BingProvider, FakeProvider, GoogleProvider, MyMemoryProvider, ProviderError,
                        RateLimiter)

MAX_RETRIES = 3
DEFAULT_WORKERS = 6
DEFAULT_RPS = 4.0            # Google's documented free limit is 5 req/s per IP
DEFAULT_AUTOSAVE = 200       # strings

_SKIP_PATTERNS = (
    re.compile(r"^\s*$"),                                    # blank
    re.compile(r"^[\d\s.,:;/%+\-()#$€₹|*_=~>\[\]]+$"),   # numbers / punctuation only
    re.compile(r"^https?://\S+$", re.I),                     # URL
    re.compile(r"^[\w.+-]+@[\w-]+\.[\w.-]+$"),               # email
    re.compile(r"^=\S"),                                      # spreadsheet formula
)

ProgressFn = Callable[[int, int], None]     # (done, total) strings for the current call


def should_skip(text: str) -> bool:
    """True for strings that must be copied through untouched (numbers, URLs, formulas ...)."""
    return any(p.match(text) for p in _SKIP_PATTERNS)


class TranslationError(RuntimeError):
    pass


class Translator:
    def __init__(self, source: str = "auto", cache_path: Path = DEFAULT_CACHE, workers: int = DEFAULT_WORKERS,
                 requests_per_second: float = DEFAULT_RPS, autosave_every: int = DEFAULT_AUTOSAVE,
                 log: Callable[[str], None] = print, progress: ProgressFn | None = None,
                 providers: list | None = None, protect: bool = True, glossary: Glossary | None = None,
                 hinglish: bool = True):
        self.source = source
        self.hinglish = hinglish
        self.protector = Protector(enabled=protect, glossary=glossary)
        self.warnings: list[str] = []
        self.workers = max(1, workers)
        self.autosave_every = autosave_every
        self.log = log
        self.progress = progress or (lambda done, total: None)
        self.cache = SqliteCache(cache_path)
        limiter = RateLimiter(requests_per_second)
        if providers is None:
            providers = [FakeProvider(limiter)] if os.environ.get("MT_FAKE_PROVIDER") else [
                GoogleProvider(limiter), BingProvider(limiter), MyMemoryProvider(limiter)]
        self.providers = providers
        self.transliterator = Transliterator(self.cache, limiter, log=log)
        self._disabled: set[str] = set()
        self._lock = threading.Lock()
        self.requests_made = 0
        self.stop_event = threading.Event()        # set by the user (cancel)
        self._abort = threading.Event()            # set internally when a batch fails

    # ------------------------------------------------------------------ public
    def translate_many(self, texts: list[str], target: str) -> list[str]:
        """Translate a list of strings; order preserved; skipped/cached strings cost no request.

        Pipeline per string: skip filter -> placeholder/glossary protection -> dedup -> cache -> provider
        -> restore placeholders. The cache key is the *protected* text, so "Hi {name}" and "Hi {user}"
        share one entry and glossary changes never require re-translation.
        """
        results: list[str | None] = [None] * len(texts)
        protected = [None] * len(texts)
        pending: dict[str, list[int]] = {}
        for i, text in enumerate(texts):
            if should_skip(text):
                results[i] = text
                continue
            prot = self.protector.protect(text)
            if should_skip(_TOKEN_RE.sub("", prot.text)):     # nothing translatable outside placeholders
                results[i] = text
                continue
            protected[i] = prot
            pending.setdefault(prot.text, []).append(i)

        unique = list(pending)
        cached = self.cache.get_many(self.source, target, unique)
        todo = [t for t in unique if t not in cached]
        translated_map = dict(cached)
        # Hinglish pivot: romanised Hindi -> Devanagari (Input Tools) -> translate as real Hindi.
        pivot = [t for t in todo if self.hinglish and self.source == "auto" and is_hinglish(t)]
        direct = [t for t in todo if t not in set(pivot)]
        self.log(f"  {target}: {len(texts)} cells, {len(unique)} unique, {len(cached)} cached, "
                 f"{len(direct)} to translate, {len(pivot)} hinglish")
        if pivot:
            translated_map.update(zip(pivot, self._translate_hinglish(pivot, target)))
        if direct:
            translated_map.update(zip(direct, self._translate_unique(direct, target)))
        if todo:
            self.cache.commit()
        for text, indexes in pending.items():
            for i in indexes:
                restored, warnings = self.protector.restore(translated_map[text], protected[i], target)
                results[i] = restored
                self.warnings.extend(warnings)
        return [r if r is not None else t for r, t in zip(results, texts)]

    def _translate_hinglish(self, texts: list[str], target: str) -> list[str]:
        devanagari = self.transliterator.transliterate_many(texts)
        if target == "hi":
            out = self._translate_english_runs(devanagari)
        else:
            unique = list(dict.fromkeys(devanagari))
            cached = self.cache.get_many("hi", target, unique)
            todo = [d for d in unique if d not in cached]
            done = dict(cached)
            if todo:
                done.update(zip(todo, self._translate_unique(todo, target, source="hi")))
            out = [done[d] for d in devanagari]
        self.cache.put_many(self.source, target, list(zip(texts, out)))   # next run: direct cache hit
        return out

    def _translate_english_runs(self, texts: list[str]) -> list[str]:
        """Hindi text with English phrases still in Latin ("... example बन गया keep it up"):
        translate each English phrase en->hi so the Hindi column reads as real Hindi."""
        runs = sorted({m.group(0) for t in texts for m in LATIN_RUN.finditer(t) if len(m.group(0)) > 1})
        if not runs:
            return texts
        cached = self.cache.get_many("en", "hi", runs)
        todo = [r for r in runs if r not in cached]
        mapping = dict(cached)
        if todo:
            mapping.update(zip(todo, self._translate_unique(todo, "hi", source="en")))
        return [LATIN_RUN.sub(lambda m: mapping.get(m.group(0), m.group(0)), t) for t in texts]

    def translate_text(self, text: str, target: str) -> str:
        return self.translate_many([text], target)[0]

    def close(self) -> None:
        self.cache.close()

    # ---------------------------------------------------------------- batching
    def _make_batches(self, texts: list[str], max_chars: int) -> list[list[int]]:
        """Group single-line strings into newline-joined batches; multi-line strings get their own batch.

        Google detects the source language once per request, so a batch that mixes scripts (English
        lines next to Devanagari ones) gets the majority language and the rest come back untouched.
        Strings are therefore batched per script."""
        batches: list[list[int]] = []
        groups: dict[str, list[int]] = {}
        for i, text in enumerate(texts):
            if "\n" in text or len(text) > max_chars:
                batches.append([i])
            else:
                groups.setdefault(_script_key(text), []).append(i)
        for indexes in groups.values():
            current: list[int] = []
            size = 0
            for i in indexes:
                if size + len(texts[i]) + 1 > max_chars and current:
                    batches.append(current)
                    current, size = [], 0
                current.append(i)
                size += len(texts[i]) + 1
            if current:
                batches.append(current)
        return batches

    def _translate_unique(self, texts: list[str], target: str, source: str | None = None) -> list[str]:
        source = source or self.source
        out: list[str] = [""] * len(texts)
        batches = self._make_batches(texts, self.providers[0].max_chars)
        total, done, since_save = len(texts), 0, 0
        self.progress(0, total)

        self._abort.clear()

        def work(batch: list[int]) -> tuple[list[int], list[str]]:
            if self.stop_event.is_set() or self._abort.is_set():
                raise TranslationError("stopped")
            return batch, self._translate_batch([texts[i] for i in batch], target, source)

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = [pool.submit(work, b) for b in batches]
            try:
                for future in as_completed(futures):
                    batch, translated = future.result()
                    for i, value in zip(batch, translated):
                        out[i] = value
                    # an unchanged result usually means the provider saw source == target; never cache it
                    self.cache.put_many(source, target, [(texts[i], out[i]) for i in batch if out[i] != texts[i]])
                    done += len(batch)
                    since_save += len(batch)
                    if since_save >= self.autosave_every:
                        self.cache.commit()
                        since_save = 0
                    self.progress(done, total)
            except BaseException:
                self._abort.set()
                for f in futures:
                    f.cancel()
                self.cache.commit()
                raise
        return out

    def _translate_batch(self, texts: list[str], target: str, source: str) -> list[str]:
        """Translate a batch of strings (joined by newline). Falls back to one-by-one if lines get merged.

        A line that comes back unchanged inside a multi-line batch was most likely mis-detected
        (the batch majority decided the source language), so it is retried on its own. If a Latin
        line is *still* unchanged when the target is Hindi, Google considers it Hindi already -
        i.e. it is Hinglish the detector missed - and it is transliterated instead."""
        if len(texts) == 1:
            out = [self._translate_block(texts[0], target, source)]
        else:
            parts = self._translate_block("\n".join(texts), target, source).split("\n")
            if len(parts) == len(texts):
                out = [p.strip() for p in parts]
                for i, (src, res) in enumerate(zip(texts, out)):
                    if res == src.strip():
                        out[i] = self._translate_block(src, target, source)
            else:
                out = [self._translate_block(t, target, source) for t in texts]
        if target == "hi" and source == "auto" and self.hinglish:
            stuck = [i for i, (src, res) in enumerate(zip(texts, out))
                     if res.strip() == src.strip() and _script_key(src) == "latin"]
            if stuck:
                fixed = self._translate_english_runs(self.transliterator.transliterate_many([texts[i] for i in stuck]))
                for i, dev in zip(stuck, fixed):
                    out[i] = dev
        return out

    def _translate_block(self, text: str, target: str, source: str) -> str:
        """Split long text into paragraph chunks that fit the current provider, translate each."""
        max_chars = self._active_providers()[0].max_chars
        if len(text) <= max_chars:
            return self._request(text, target, source)
        chunks, current = [], ""
        for para in text.split("\n"):
            if current and len(current) + len(para) + 1 > max_chars:
                chunks.append(current)
                current = ""
            current = f"{current}\n{para}" if current else para
        if current:
            chunks.append(current)
        return "\n".join(self._request(c, target, source) for c in chunks)

    # ---------------------------------------------------------------- providers
    def _active_providers(self) -> list:
        active = [p for p in self.providers if p.name not in self._disabled]
        return active or self.providers[:1]

    def _request(self, text: str, target: str, source: str) -> str:
        last_error: Exception | None = None
        for provider in self._active_providers():
            if len(text) > provider.max_chars:       # fallback provider with a smaller limit
                return "\n".join(self._request(c, target, source) for c in _split(text, provider.max_chars))
            for attempt in range(MAX_RETRIES):
                if self.stop_event.is_set() or self._abort.is_set():
                    raise TranslationError("stopped")
                try:
                    with self._lock:
                        self.requests_made += 1
                    return provider.translate(text, source, target)
                except (ProviderError, OSError, ValueError) as exc:
                    last_error = exc
                    if provider.name == "bing":
                        break                           # best effort - don't retry Bing
                    self.log(f"  {provider.name} retry {attempt + 1}/{MAX_RETRIES}: {exc}")
                    time.sleep(1.5 * (attempt + 1))
            if provider.name in ("bing",):
                self._disabled.add(provider.name)
            self.log(f"  {provider.name} failed ({last_error}) - trying next provider")
        raise TranslationError(f"all providers failed for '{target}': {last_error}")


def _script_key(text: str) -> str:
    """'latin' for ASCII-only letters, otherwise the Unicode block (rounded to 128) of the first non-ASCII letter."""
    for ch in text:
        if ch.isalpha() and not ch.isascii():
            return str(ord(ch) // 128)
    return "latin"


def _split(text: str, max_chars: int) -> list[str]:
    chunks, current = [], ""
    for para in text.split("\n"):
        while len(para) > max_chars:                    # a single huge line: hard split at a space
            cut = para.rfind(" ", 0, max_chars) or max_chars
            chunks.append(para[:cut])
            para = para[cut:].lstrip()
        if current and len(current) + len(para) + 1 > max_chars:
            chunks.append(current)
            current = ""
        current = f"{current}\n{para}" if current else para
    if current:
        chunks.append(current)
    return chunks
