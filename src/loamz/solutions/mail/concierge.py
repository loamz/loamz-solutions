"""The Mail Concierge: fetch, triage, remember, digest, answer.

    concierge = MailConcierge(connector, MailStore(".state/mail.sqlite"), triager, Ledger(".state/mail-ledger.sqlite"))
    concierge.sync(days=2)
    print(concierge.digest())
    print(concierge.ask("what did Priya say about the contract?", llm).text)

It only reads. The digest suggests what to do with each message (reply, file, archive, unsubscribe); it does not do it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from loamz.solutions.kit.ledger import Ledger
from loamz.solutions.kit.llm import LLM, LLMError
from loamz.solutions.kit.records import Connector
from loamz.solutions.mail import digest as digest_module
from loamz.solutions.mail.store import MailStore
from loamz.solutions.mail.triage import Priority, Triager


class Sink(Protocol):
    def remember(self, item: dict[str, Any]) -> None: ...


@dataclass
class SyncReport:
    fetched: int = 0
    new: int = 0
    triaged: int = 0
    by_priority: dict[str, int] = field(default_factory=dict)


@dataclass
class Answer:
    text: str
    sources: list[dict[str, Any]]


ASK_SYSTEM = (
    "You answer questions about the user's own email, using only the messages given. "
    "Quote dates and names exactly. If the messages do not contain the answer, say so plainly. "
    "End with the ids of the messages you used, like [id1, id2]."
)


class MailConcierge:
    def __init__(
        self,
        connector: Connector,
        store: MailStore,
        triager: Triager,
        ledger: Ledger,
        sink: Sink | None = None,
    ) -> None:
        self.connector, self.store, self.triager, self.ledger, self.sink = (
            connector,
            store,
            triager,
            ledger,
            sink,
        )

    def sync(self, days: int = 2, limit: int = 200) -> SyncReport:
        since = datetime.now(UTC) - timedelta(days=days)
        report = SyncReport()
        fetched = list(self.connector.fetch(since=since, limit=limit))
        report.fetched = len(fetched)
        report.new = len(self.store.add(fetched))
        for record in self.store.pending():
            result = self.triager.triage(record)
            self.store.save(record.id, result)
            self.ledger.append(
                "mail.triaged",
                record.id,
                priority=result.priority.name.lower(),
                category=result.category,
                by=result.by,
                action=result.action,
            )
            name = result.priority.name.lower()
            report.by_priority[name] = report.by_priority.get(name, 0) + 1
            report.triaged += 1
            if self.sink and result.priority >= Priority.IMPORTANT:
                self.sink.remember(
                    {
                        "id": record.id,
                        "from": record.author,
                        "subject": record.title,
                        "reason": result.reason,
                        "priority": name,
                    }
                )
        self.ledger.append(
            "mail.synced",
            self.connector.name,
            fetched=report.fetched,
            new=report.new,
            triaged=report.triaged,
        )
        return report

    def digest(self, hours: int = 24, now: datetime | None = None) -> str:
        now = now or datetime.now(UTC)
        since = now - timedelta(hours=hours)
        items = self.store.top(since=since, at_least=Priority.NORMAL, limit=60)
        return digest_module.render(
            items, self.store.counts(since), digest_module.noisy_senders(self.store, since), now
        )

    def ask(self, question: str, llm: LLM | None = None) -> Answer:
        hits = self.store.search(question, limit=8)
        if not hits:
            return Answer("I found no messages that match that.", [])
        if llm is None:
            return Answer("No model is configured; these are the closest messages.", hits)
        context = "\n\n".join(
            f"[{h['id']}] {h['at'][:10]} from {h['author']}: {h['title']}\n{h['text'][:1200]}"
            for h in hits
        )
        try:
            text = llm.text(ASK_SYSTEM, f"Messages:\n{context}\n\nQuestion: {question}")
        except LLMError as error:
            return Answer(
                f"The model could not answer ({error}); these are the closest messages.", hits
            )
        return Answer(text, hits)
