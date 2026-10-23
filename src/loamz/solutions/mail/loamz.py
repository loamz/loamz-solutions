"""Remember important mail in Loamz memory, so any agent on the platform can recall it under the same tenant rules."""

from __future__ import annotations

from typing import Any

from loamz.data import DataClient


class LoamzMemorySink:
    def __init__(self, client: DataClient, user_id: str) -> None:
        self.client, self.user_id = client, user_id

    def remember(self, item: dict[str, Any]) -> None:
        text = f"Email from {item['from']}: {item['subject']}. {item['reason']} (priority {item['priority']})"
        self.client.memory.save(
            {
                "id": f"mail-{item['id']}",
                "content": [{"type": "text", "text": text}],
                "namespace": {"user_id": self.user_id},
                "category": "semantic",
            }
        )
