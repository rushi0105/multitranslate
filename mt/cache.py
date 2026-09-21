"""SQLite translation cache. Thread-safe; every translated string is stored, so a re-run after a
crash replays from disk without touching the network (this is what makes "resume" free)."""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

DEFAULT_CACHE = Path.home() / ".multitranslate" / "cache.sqlite3"


class SqliteCache:
    def __init__(self, path: Path = DEFAULT_CACHE):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS translations ("
            " source TEXT NOT NULL, target TEXT NOT NULL, text TEXT NOT NULL, translated TEXT NOT NULL,"
            " PRIMARY KEY (source, target, text))")
        self._conn.commit()
        self._pending = 0

    def get_many(self, source: str, target: str, texts: list[str]) -> dict[str, str]:
        """Return {text: translated} for every text already cached."""
        found: dict[str, str] = {}
        with self._lock:
            for i in range(0, len(texts), 500):
                chunk = texts[i:i + 500]
                marks = ",".join("?" * len(chunk))
                rows = self._conn.execute(
                    f"SELECT text, translated FROM translations WHERE source=? AND target=? AND text IN ({marks})",
                    [source, target, *chunk]).fetchall()
                found.update(rows)
        return found

    def put_many(self, source: str, target: str, pairs: list[tuple[str, str]], commit_every: int = 500) -> None:
        with self._lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO translations (source, target, text, translated) VALUES (?,?,?,?)",
                [(source, target, t, tr) for t, tr in pairs])
            self._pending += len(pairs)
            if self._pending >= commit_every:
                self._conn.commit()
                self._pending = 0

    def commit(self) -> None:
        with self._lock:
            self._conn.commit()
            self._pending = 0

    def purge_identity(self) -> int:
        """Drop rows whose 'translation' equals the source text (no-op results from same-language detection)."""
        with self._lock:
            cur = self._conn.execute("DELETE FROM translations WHERE translated = text")
            self._conn.commit()
            return cur.rowcount

    def clear(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM translations")
            self._conn.commit()

    def count(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM translations").fetchone()[0]

    def close(self) -> None:
        self.commit()
        self._conn.close()
