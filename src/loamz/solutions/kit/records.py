"""What connectors read: one `Record` per message, document, row or event, whatever its source."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class Record:
    id: str
    source: str  # the connector's name, e.g. "imap:me@example.com"
    kind: str  # "email", "document", "row", "event" ...
    title: str
    text: str
    at: datetime
    author: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class Connector(Protocol):
    """Reads records from one place. `fetch` yields the records newer than `since` (None: everything, up to `limit`)."""

    name: str

    def fetch(self, since: datetime | None = None, limit: int = 200) -> Iterator[Record]: ...
