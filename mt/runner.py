"""Job runner: walks a file or folder, translates every supported file into the chosen languages.

Output layout
  separate : <out>/<lang>/<relative path>/<stem>_<lang>.<ext>      one file per language
  combined : <out>/<relative path>/<stem>_multi.<ext>              one file, all languages inside

Resume: <out>/.progress.json records finished outputs; a re-run skips them (use force=True to redo).
Every translated string is also in the SQLite cache, so even a re-done file costs no network calls.
"""
from __future__ import annotations

import json
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .cache import DEFAULT_CACHE
from .engine import DEFAULT_AUTOSAVE, DEFAULT_RPS, DEFAULT_WORKERS, Translator
from .handlers import SUPPORTED_EXTENSIONS, handler_for
from .protect import Glossary

PROGRESS_FILE = ".progress.json"


@dataclass
class JobOptions:
    input: Path
    output: Path
    targets: list[str]
    mode: str = "separate"          # separate | combined
    sheet_mode: str = "columns"     # columns | sheets   (combined mode, xlsx only)
    source: str = "auto"
    workers: int = DEFAULT_WORKERS
    requests_per_second: float = DEFAULT_RPS
    autosave_every: int = DEFAULT_AUTOSAVE
    cache_path: Path = DEFAULT_CACHE
    force: bool = False
    protect: bool = True            # placeholder / URL / tag protection
    glossary: Path | None = None    # CSV or JSON glossary file
    hinglish: bool = True           # romanised Hindi -> Devanagari pivot before translating


@dataclass
class JobStatus:
    state: str = "idle"             # idle | running | done | error | stopped
    files_total: int = 0
    files_done: int = 0
    current_file: str = ""
    current_lang: str = ""
    strings_done: int = 0
    strings_total: int = 0
    outputs: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    log: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    started_at: float = 0.0
    finished_at: float = 0.0
    requests_made: int = 0

    def as_dict(self) -> dict:
        d = self.__dict__.copy()
        d["log"] = self.log[-200:]
        d["elapsed"] = round((self.finished_at or time.time()) - self.started_at, 1) if self.started_at else 0
        return d


def collect_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root] if root.suffix.lower() in SUPPORTED_EXTENSIONS else []
    return sorted(p for p in root.rglob("*")
                  if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
                  and not p.name.startswith("~$"))          # Excel lock files


def _output_path(opts: JobOptions, src: Path, lang: str | None) -> Path:
    base = opts.input if opts.input.is_dir() else opts.input.parent
    rel = src.relative_to(base)
    if lang is None:
        return opts.output / rel.parent / f"{src.stem}_multi{src.suffix}"
    return opts.output / lang / rel.parent / f"{src.stem}_{lang}{src.suffix}"


def _load_progress(out: Path) -> set[str]:
    path = out / PROGRESS_FILE
    if not path.exists():
        return set()
    try:
        return set(json.loads(path.read_text(encoding="utf-8")).get("done", []))
    except (OSError, json.JSONDecodeError):
        return set()


def _save_progress(out: Path, done: set[str]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / PROGRESS_FILE).write_text(json.dumps({"done": sorted(done)}, indent=1), encoding="utf-8")


def run_job(opts: JobOptions, status: JobStatus | None = None,
            log: Callable[[str], None] | None = None,
            stop_event: threading.Event | None = None) -> JobStatus:
    status = status or JobStatus()
    stop_event = stop_event or threading.Event()

    def _log(msg: str) -> None:
        status.log.append(msg)
        if log:
            log(msg)

    def _progress(done: int, total: int) -> None:
        status.strings_done, status.strings_total = done, total

    files = collect_files(opts.input)
    tasks: list[tuple[Path, str | None]] = [
        (f, None) for f in files] if opts.mode == "combined" else [(f, t) for f in files for t in opts.targets]
    status.state, status.started_at = "running", time.time()
    status.files_total = len(tasks)
    if not files:
        status.state = "error"
        status.errors.append(f"No supported files under {opts.input} (supported: {', '.join(SUPPORTED_EXTENSIONS)})")
        status.finished_at = time.time()
        return status

    done = set() if opts.force else _load_progress(opts.output)
    glossary = None
    if opts.glossary:
        try:
            glossary = Glossary.load(Path(opts.glossary))
            _log(f"glossary: {len(glossary.entries)} term(s) from {opts.glossary}")
        except (OSError, ValueError) as exc:
            status.state = "error"
            status.errors.append(f"glossary could not be loaded: {exc}")
            status.finished_at = time.time()
            return status
    translator = Translator(source=opts.source, cache_path=opts.cache_path, workers=opts.workers,
                            requests_per_second=opts.requests_per_second, autosave_every=opts.autosave_every,
                            log=_log, progress=_progress, protect=opts.protect, glossary=glossary,
                            hinglish=opts.hinglish)
    translator.stop_event = stop_event
    _log(f"{len(files)} file(s), languages: {', '.join(opts.targets)}, mode: {opts.mode}, workers: {opts.workers}")

    try:
        for src, lang in tasks:
            if stop_event.is_set():
                status.state = "stopped"
                break
            dst = _output_path(opts, src, lang)
            key = str(dst)
            status.current_file, status.current_lang = src.name, lang or "all"
            if key in done and dst.exists():
                _log(f"skip (already done): {dst.name}")
                status.files_done += 1
                status.outputs.append(key)
                continue
            _log(f"-> {src.name}  [{lang or ', '.join(opts.targets)}]")
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                handler = handler_for(src)
                if lang is None:
                    handler.translate_combined(src, dst, translator, opts.targets, opts.sheet_mode)
                else:
                    handler.translate_separate(src, dst, translator, lang)
            except Exception as exc:                        # one bad file must not kill a 500-file batch
                if stop_event.is_set():
                    status.state = "stopped"
                    break
                status.errors.append(f"{src.name} [{lang or 'all'}]: {exc}")
                _log(f"ERROR {src.name}: {exc}\n{traceback.format_exc(limit=2)}")
                continue
            done.add(key)
            _save_progress(opts.output, done)
            status.outputs.append(key)
            status.files_done += 1
            _log(f"saved {dst}")
        else:
            status.state = "done"
    finally:
        translator.close()
        status.warnings = translator.warnings[:200]
        status.requests_made = translator.requests_made + translator.transliterator.requests_made
        status.finished_at = time.time()
        if status.state == "running":
            status.state = "error"
    _log(f"finished: {status.files_done}/{status.files_total} outputs, {status.requests_made} requests, "
         f"{len(status.errors)} error(s), {round(status.finished_at - status.started_at, 1)}s")
    return status
