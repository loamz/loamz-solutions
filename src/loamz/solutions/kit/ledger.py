"""The ledger: an append-only record of what a solution decided and did, and a place for its small state.

Every entry commits to the one before it (a hash chain), so an entry that is changed or removed afterwards is detected by
`verify()`. SQLite in WAL mode: one writer at a time, safe across processes.

    ledger = Ledger(".state/trader.sqlite")
    ledger.append("order.proposed", "AAPL", side="buy", qty=3)
    ledger.entries(kind="order.proposed")
    ledger.set("cursor", "2026-10-23T00:00:00Z"); ledger.get("cursor")
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, kind TEXT NOT NULL, subject TEXT NOT NULL,
    payload TEXT NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS entries_kind ON entries(kind, id);
CREATE INDEX IF NOT EXISTS entries_subject ON entries(subject, id);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


@dataclass(frozen=True)
class Entry:
    id: int
    at: datetime
    kind: str
    subject: str
    payload: dict[str, Any]
    hash: str


def digest(previous: str, at: str, kind: str, subject: str, payload: str) -> str:
    return hashlib.sha256("|".join((previous, at, kind, subject, payload)).encode()).hexdigest()


class Ledger:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        if self.path != ":memory:":
            self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield self.db
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
            self.db.execute("COMMIT")

    # ------------------------------------------------------------------ entries
    def append(self, kind: str, subject: str = "", **payload: Any) -> Entry:
        at = datetime.now(UTC)
        body = json.dumps(payload, sort_keys=True, default=str)
        with self.transaction() as db:
            last = db.execute("SELECT hash FROM entries ORDER BY id DESC LIMIT 1").fetchone()
            previous = last["hash"] if last else ""
            stamp = at.isoformat()
            current = digest(previous, stamp, kind, subject, body)
            cursor = db.execute(
                "INSERT INTO entries (at, kind, subject, payload, prev_hash, hash) VALUES (?,?,?,?,?,?)",
                (stamp, kind, subject, body, previous, current),
            )
        return Entry(cursor.lastrowid, at, kind, subject, json.loads(body), current)

    def entries(
        self,
        *,
        kind: str | None = None,
        subject: str | None = None,
        since: datetime | None = None,
        limit: int = 1000,
        newest_first: bool = False,
    ) -> list[Entry]:
        clauses, params = ["1=1"], []
        if kind:
            clauses.append("kind LIKE ?")
            params.append(kind.replace("*", "%"))
        if subject:
            clauses.append("subject=?")
            params.append(subject)
        if since:
            clauses.append("at>=?")
            params.append(since.astimezone(UTC).isoformat())
        order = "DESC" if newest_first else "ASC"
        with self.lock:
            rows = self.db.execute(
                f"SELECT * FROM entries WHERE {' AND '.join(clauses)} ORDER BY id {order} LIMIT ?",
                [*params, limit],
            ).fetchall()
        return [
            Entry(
                r["id"],
                datetime.fromisoformat(r["at"]),
                r["kind"],
                r["subject"],
                json.loads(r["payload"]),
                r["hash"],
            )
            for r in rows
        ]

    def verify(self) -> dict[str, Any]:
        """Recompute the chain. `ok` is false at the first entry that was altered or whose predecessor is missing."""
        previous, checked = "", 0
        with self.lock:
            rows = self.db.execute("SELECT * FROM entries ORDER BY id").fetchall()
        for r in rows:
            expected = digest(previous, r["at"], r["kind"], r["subject"], r["payload"])
            if r["prev_hash"] != previous or r["hash"] != expected:
                return {"ok": False, "checked": checked, "first_bad_id": r["id"]}
            previous, checked = r["hash"], checked + 1
        return {"ok": True, "checked": checked, "first_bad_id": None}

    # ------------------------------------------------------------------ small state
    def get(self, key: str, default: str | None = None) -> str | None:
        with self.lock:
            row = self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set(self, key: str, value: str) -> None:
        with self.transaction() as db:
            db.execute(
                "INSERT INTO state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
