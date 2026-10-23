"""Read the files in a folder as `Record`s: text, Markdown, CSV, JSON, HTML and common source files.

Hidden folders, build output and anything over 1 MB are skipped; files this module cannot read as text (PDF, Office
documents, images) are counted and listed in the inventory as "needs an extractor" rather than silently ignored.
"""

from __future__ import annotations

import csv
import io
import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from loamz.solutions.concierge.extract import available, extract
from loamz.solutions.kit.records import Record
from loamz.solutions.mail.imap import html_to_text

TEXT = {
    ".txt",
    ".md",
    ".rst",
    ".log",
    ".ini",
    ".cfg",
    ".toml",
    ".yaml",
    ".yml",
    ".py",
    ".js",
    ".ts",
    ".sql",
    ".sh",
}
TABLES = {".csv", ".tsv"}
SKIP_DIRS = {"node_modules", "__pycache__", "venv", "build", "dist", "target", "site-packages"}
NEEDS_EXTRACTOR = {
    ".pdf",
    ".docx",
    ".xlsx",
    ".pptx",
    ".doc",
    ".xls",
    ".ppt",
    ".png",
    ".jpg",
    ".jpeg",
    ".heic",
    ".zip",
}
MAX_BYTES = 1_000_000
MAX_CHARS = 20_000
MAX_BINARY = 25_000_000  # PDF and Office files may be larger than text before they are skipped


def read_text(path: Path) -> str | None:
    """The text of a file this module understands, or None."""
    suffix = path.suffix.lower()
    try:
        raw = path.read_bytes()[:MAX_BYTES].decode("utf-8", errors="replace")
    except OSError:
        return None
    if suffix in TEXT:
        return raw[:MAX_CHARS]
    if suffix in (".html", ".htm"):
        return html_to_text(raw)[:MAX_CHARS]
    if suffix == ".json":
        try:
            return json.dumps(json.loads(raw), indent=1)[:MAX_CHARS]
        except ValueError:
            return raw[:MAX_CHARS]
    if suffix in TABLES:
        dialect = csv.excel_tab if suffix == ".tsv" else csv.excel
        rows = list(csv.reader(io.StringIO(raw), dialect))
        if not rows:
            return ""
        header, body = rows[0], rows[1:]
        sample = "\n".join(" | ".join(r) for r in body[:40])
        return f"Table with columns: {', '.join(header)}. {len(body)} rows (first {min(len(body), 40)} shown).\n{sample}"[
            :MAX_CHARS
        ]
    return None


class FolderConnector:
    def __init__(self, roots: list[str | Path], name: str = "") -> None:
        self.roots = [Path(r).expanduser().resolve() for r in roots]
        self.name = name or "files:" + ",".join(p.name or str(p) for p in self.roots)
        self.unreadable: dict[str, int] = {}

    def walk(self) -> Iterator[Path]:
        for root in self.roots:
            for folder, dirs, files in os.walk(root):
                dirs[:] = [d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS]
                for file in files:
                    if not file.startswith("."):
                        yield Path(folder) / file

    def fetch(self, since: datetime | None = None, limit: int = 5000) -> Iterator[Record]:
        count = 0
        readable = available()
        self.unreadable = {}
        for path in self.walk():
            suffix = path.suffix.lower()
            try:
                stat = path.stat()
            except OSError:
                continue
            at = datetime.fromtimestamp(stat.st_mtime, UTC)
            if since and at < since:
                continue
            if suffix in NEEDS_EXTRACTOR:
                text = extract(path) if suffix in readable and stat.st_size <= MAX_BINARY else None
                if text is None:  # no reader installed, too big, damaged or password protected
                    self.unreadable[suffix] = self.unreadable.get(suffix, 0) + 1
                    continue
            else:
                if stat.st_size > MAX_BYTES:
                    continue
                text = read_text(path)
                if text is None or not text.strip():
                    continue
            yield Record(
                id=f"{self.name}:{path}",
                source=self.name,
                kind="file",
                title=path.name,
                text=text,
                at=at,
                author="",
                metadata={
                    "path": str(path),
                    "size": stat.st_size,
                    "type": suffix.lstrip(".") or "none",
                },
            )
            count += 1
            if count >= limit:
                return
