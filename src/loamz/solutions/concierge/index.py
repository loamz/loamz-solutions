"""A searchable index over everything the Concierge has scanned, in one SQLite file with full-text search."""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loamz.solutions.kit.records import Record

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY, source TEXT NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL, text TEXT NOT NULL,
    at TEXT NOT NULL, meta TEXT NOT NULL);
CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(title, text, content='items', content_rowid='rowid');
CREATE TRIGGER IF NOT EXISTS items_ai AFTER INSERT ON items BEGIN
    INSERT INTO items_fts(rowid, title, text) VALUES (new.rowid, new.title, new.text);
END;
CREATE TRIGGER IF NOT EXISTS items_ad AFTER DELETE ON items BEGIN
    INSERT INTO items_fts(items_fts, rowid, title, text) VALUES ('delete', old.rowid, old.title, old.text);
END;
"""


class DataIndex:
    def __init__(self, path: str | Path = ":memory:") -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    def upsert(self, records: Iterable[Record]) -> int:
        """Add or refresh items; returns how many were new or changed."""
        changed = 0
        with self.db:
            for r in records:
                at = r.at.astimezone(UTC).isoformat()
                old = self.db.execute("SELECT at FROM items WHERE id=?", (r.id,)).fetchone()
                if old and old["at"] == at:
                    continue
                if old:
                    self.db.execute("DELETE FROM items WHERE id=?", (r.id,))
                self.db.execute(
                    "INSERT INTO items (id, source, kind, title, text, at, meta) VALUES (?,?,?,?,?,?,?)",
                    (
                        r.id,
                        r.source,
                        r.kind,
                        r.title,
                        r.text,
                        at,
                        json.dumps(r.metadata, default=str),
                    ),
                )
                changed += 1
        return changed

    def search(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        words = re.findall(r"[\w']{3,}", query.lower())
        if not words:
            return []
        rows = self.db.execute(
            "SELECT i.*, snippet(items_fts, 1, '[', ']', '…', 18) AS snippet FROM items_fts f JOIN items i ON i.rowid=f.rowid "
            "WHERE items_fts MATCH ? ORDER BY bm25(items_fts) LIMIT ?",
            (" OR ".join(f'"{w}"' for w in words), limit),
        ).fetchall()
        return [
            {**{k: r[k] for k in r.keys() if k != "meta"}, "meta": json.loads(r["meta"])}
            for r in rows
        ]  # noqa: SIM118

    def counts(self) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT source, kind, count(*) AS n, max(at) AS newest, sum(length(text)) AS chars FROM items GROUP BY source, kind ORDER BY n DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def types(self) -> Counter[str]:
        out: Counter[str] = Counter()
        for r in self.db.execute("SELECT meta FROM items WHERE kind='file'"):
            out[json.loads(r["meta"]).get("type", "?")] += 1
        return out

    def newest(self, limit: int = 5) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT id, source, title, at FROM items ORDER BY at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def since(self, when: datetime) -> int:
        return self.db.execute(
            "SELECT count(*) AS n FROM items WHERE at >= ?", (when.astimezone(UTC).isoformat(),)
        ).fetchone()["n"]
