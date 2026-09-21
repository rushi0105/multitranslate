"""MultiTranslate CLI.

Examples
  python translate.py comments.xlsx --to hi,pa
  python translate.py D:\\data --to hi,pa,mr --mode combined --out D:\\data_translated
  python translate.py sheet.xlsx --to hi,pa --mode combined --sheet-mode sheets
  python translate.py big.csv --to hi --workers 8 --rps 4 --autosave 500
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

from mt.cache import DEFAULT_CACHE
from mt.engine import DEFAULT_AUTOSAVE, DEFAULT_RPS, DEFAULT_WORKERS
from mt.languages import LANGUAGES, parse_language_list
from mt.runner import JobOptions, JobStatus, run_job

BAR_WIDTH = 30


def _bar(status: JobStatus) -> str:
    total = max(status.strings_total, 1)
    filled = int(BAR_WIDTH * min(status.strings_done, total) / total)
    return (f"[{'#' * filled}{'-' * (BAR_WIDTH - filled)}] {status.strings_done}/{status.strings_total} strings "
            f"| file {status.files_done + 1}/{status.files_total} {status.current_file} ({status.current_lang})")


def _progress_loop(status: JobStatus, stop: threading.Event) -> None:
    last = ""
    while not stop.is_set():
        line = _bar(status)
        if line != last:
            sys.stdout.write("\r" + line[:150].ljust(150))
            sys.stdout.flush()
            last = line
        time.sleep(0.3)
    sys.stdout.write("\n")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Translate files / folders into many languages (free, local).")
    p.add_argument("input", nargs="?", default=".", help="file or folder (txt, md, csv, tsv, xlsx, docx, json, srt, vtt)")
    p.add_argument("--to", default="", help="target languages, e.g. hi,pa,mr,es")
    p.add_argument("--out", help="output folder (default: <input>_translated)")
    p.add_argument("--mode", choices=["separate", "combined"], default="separate",
                   help="separate = one file per language; combined = all languages in ONE file")
    p.add_argument("--sheet-mode", choices=["columns", "sheets"], default="columns",
                   help="combined xlsx: extra columns in the same sheet, or one sheet copy per language")
    p.add_argument("--source", default="auto", help="source language (default auto-detect)")
    p.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    p.add_argument("--rps", type=float, default=DEFAULT_RPS, help="max requests per second (Google ~5)")
    p.add_argument("--autosave", type=int, default=DEFAULT_AUTOSAVE, help="commit cache every N strings")
    p.add_argument("--cache", default=str(DEFAULT_CACHE), help="SQLite cache path")
    p.add_argument("--glossary", help="CSV (term,hi,pa,...) or JSON glossary; blank = keep term untranslated")
    p.add_argument("--no-protect", action="store_true", help="disable placeholder/URL/tag protection")
    p.add_argument("--no-hinglish", action="store_true",
                   help="do not convert romanised Hindi (Hinglish) to Devanagari before translating")
    p.add_argument("--force", action="store_true", help="redo outputs that already exist")
    p.add_argument("--quiet", action="store_true", help="no progress bar / log")
    p.add_argument("--list-languages", action="store_true")
    p.add_argument("--clear-cache", action="store_true", help="forget all cached translations, then exit")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.list_languages:
        for code, name in LANGUAGES.items():
            print(f"{code:6} {name}")
        return 0
    if args.clear_cache:
        from mt.cache import SqliteCache
        SqliteCache(Path(args.cache)).clear()
        print("cache cleared")
        return 0
    src = Path(args.input)
    if not src.exists():
        print(f"input not found: {src}", file=sys.stderr)
        return 2
    targets = parse_language_list(args.to)
    if not targets:
        print("--to needs at least one language code", file=sys.stderr)
        return 2
    out = Path(args.out) if args.out else src.parent / f"{src.stem}_translated"
    opts = JobOptions(input=src, output=out, targets=targets, mode=args.mode, sheet_mode=args.sheet_mode,
                      source=args.source, workers=args.workers, requests_per_second=args.rps,
                      autosave_every=args.autosave, cache_path=Path(args.cache), force=args.force,
                      protect=not args.no_protect, glossary=Path(args.glossary) if args.glossary else None,
                      hinglish=not args.no_hinglish)

    status = JobStatus()
    stop_bar = threading.Event()
    log_lines: list[str] = []
    if not args.quiet:
        threading.Thread(target=_progress_loop, args=(status, stop_bar), daemon=True).start()
    try:
        run_job(opts, status, log=log_lines.append)
    except KeyboardInterrupt:
        print("\nstopped by user - re-run the same command to resume", file=sys.stderr)
        return 130
    finally:
        stop_bar.set()
        time.sleep(0.35)
    if not args.quiet:
        print("\n".join(log_lines))
    print(f"\nOutput folder: {out}")
    for warn in status.warnings[:20]:
        print(f"WARNING: {warn}", file=sys.stderr)
    for err in status.errors:
        print(f"ERROR: {err}", file=sys.stderr)
    return 0 if status.state == "done" and not status.errors else 1


if __name__ == "__main__":
    sys.exit(main())
