"""The mail index: every message, its triage, and full-text search, in one SQLite file."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loamz.solutions.kit.records import Record
from loamz.solutions.mail.triage import Priority, Triage

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY, source TEXT NOT NULL, at TEXT NOT NULL, author TEXT NOT NULL, title TEXT NOT NULL,
    text TEXT NOT NULL, meta TEXT NOT NULL,
    priority INTEGER, category TEXT, action TEXT, reason TEXT, by TEXT, score INTEGER, deadline TEXT, triaged_at TEXT);
CREATE INDEX IF NOT EXISTS messages_rank ON messages(priority, score, at);
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(title, text, author, content='messages', content_rowid='rowid');
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, title, text, author) VALUES (new.rowid, new.title, new.text, new.author);
END;
"""


class MailStore:
    def __init__(self, path: str | Path = ":memory:") -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    def add(self, records: Iterable[Record]) -> list[Record]:
        """Store messages not seen before; returns just the new ones."""
        import json

        fresh = []
        with self.db:
            for r in records:
                done = self.db.execute(
                    "INSERT OR IGNORE INTO messages (id, source, at, author, title, text, meta) VALUES (?,?,?,?,?,?,?)",
                    (
                        r.id,
                        r.source,
                        r.at.astimezone(UTC).isoformat(),
                        r.author,
                        r.title,
                        r.text,
                        json.dumps(r.metadata),
                    ),
                )
                if done.rowcount:
                    fresh.append(r)
        return fresh

    def pending(self, limit: int = 500) -> list[Record]:
        rows = self.db.execute(
            "SELECT * FROM messages WHERE priority IS NULL ORDER BY at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self.record(r) for r in rows]

    def save(self, message_id: str, t: Triage) -> None:
        with self.db:
            self.db.execute(
                "UPDATE messages SET priority=?, category=?, action=?, reason=?, by=?, score=?, deadline=?, triaged_at=? WHERE id=?",
                (
                    int(t.priority),
                    t.category,
                    t.action,
                    t.reason,
                    t.by,
                    t.score,
                    t.deadline,
                    datetime.now(UTC).isoformat(),
                    message_id,
                ),
            )

    def top(
        self,
        *,
        since: datetime | None = None,
        at_least: Priority = Priority.IMPORTANT,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        clauses, params = ["priority >= ?"], [int(at_least)]
        if since:
            clauses.append("at >= ?")
            params.append(since.astimezone(UTC).isoformat())
        rows = self.db.execute(
            f"SELECT * FROM messages WHERE {' AND '.join(clauses)} ORDER BY priority DESC, score DESC, at DESC LIMIT ?",
            [*params, limit],
        ).fetchall()
        return [self.view(r) for r in rows]

    def counts(self, since: datetime | None = None) -> dict[str, int]:
        sql, params = "SELECT priority, count(*) AS n FROM messages WHERE priority IS NOT NULL", []
        if since:
            sql += " AND at >= ?"
            params.append(since.astimezone(UTC).isoformat())
        rows = self.db.execute(sql + " GROUP BY priority", params).fetchall()
        return {Priority(r["priority"]).name.lower(): r["n"] for r in rows}

    def search(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        words = [w for w in re.findall(r"[\w']{3,}", query.lower())]
        if not words:
            return []
        match = " OR ".join(f'"{w}"' for w in words)
        rows = self.db.execute(
            "SELECT m.* FROM messages_fts f JOIN messages m ON m.rowid = f.rowid WHERE messages_fts MATCH ? "
            "ORDER BY bm25(messages_fts) LIMIT ?",
            (match, limit),
        ).fetchall()
        return [self.view(r) for r in rows]

    def get(self, message_id: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
        return self.view(row) if row else None

    @staticmethod
    def view(row: sqlite3.Row) -> dict[str, Any]:
        out = {k: row[k] for k in row.keys() if k != "meta"}  # noqa: SIM118 - sqlite3.Row has no dict items
        out["priority_name"] = (
            Priority(row["priority"]).name.lower() if row["priority"] is not None else None
        )
        return out

    @staticmethod
    def record(row: sqlite3.Row) -> Record:
        import json

        return Record(
            row["id"],
            row["source"],
            "email",
            row["title"],
            row["text"],
            datetime.fromisoformat(row["at"]),
            row["author"],
            json.loads(row["meta"]),
        )
