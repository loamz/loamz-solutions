"""The Data Concierge: where does my data live, what is in it, what can I do with it, and what does it say about X?

    concierge = DataConcierge(DataIndex(".state/data.sqlite"), FolderConnector(["~/Documents"]), mail=mail, llm=llm)
    concierge.scan()
    print(concierge.inventory())            # a map of everything it can see
    print(concierge.ask("what did we agree about the Northwind contract?").text)
    print(concierge.suggest())              # useful things to do with what you have

It reads, never writes or deletes: your files, mail and Loamz data are untouched.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from loamz.solutions.concierge.files import FolderConnector
from loamz.solutions.concierge.index import DataIndex
from loamz.solutions.concierge.loamz import LoamzSource
from loamz.solutions.kit.llm import LLM, LLMError
from loamz.solutions.mail.concierge import MailConcierge


@dataclass
class ScanReport:
    files_seen: int = 0
    files_new: int = 0
    unreadable: dict[str, int] = field(default_factory=dict)


@dataclass
class Answer:
    text: str
    sources: list[dict[str, Any]]


ASK_SYSTEM = (
    "You answer questions about the user's own data (files, mail, tables, documents) using only the excerpts given. "
    "Quote names, dates and numbers exactly. If the excerpts do not contain the answer, say so plainly and say where it "
    "might be. Cite the references you used in square brackets."
)
SUGGEST_SYSTEM = (
    "You advise the owner of the data described below. Suggest at most five specific, useful things to do with it, each one "
    "a sentence naming the data and the benefit. No generalities, no warnings, no investment or legal advice."
)


def ago(stamp: str) -> str:
    try:
        days = (datetime.now(UTC) - datetime.fromisoformat(stamp)).days
    except ValueError:
        return "?"
    return "today" if days < 1 else f"{days}d ago"


class DataConcierge:
    def __init__(
        self,
        index: DataIndex,
        folders: FolderConnector | None = None,
        *,
        mail: MailConcierge | None = None,
        loamz: LoamzSource | None = None,
        llm: LLM | None = None,
    ) -> None:
        self.index, self.folders, self.mail, self.loamz, self.llm = index, folders, mail, loamz, llm
        self.unreadable: dict[str, int] = {}

    # ------------------------------------------------------------------ scanning
    def scan(self) -> ScanReport:
        report = ScanReport()
        if self.folders:
            records = list(self.folders.fetch())
            report.files_seen = len(records)
            report.files_new = self.index.upsert(records)
            report.unreadable = dict(self.folders.unreadable)
            self.unreadable = report.unreadable
        return report

    # ------------------------------------------------------------------ the map
    def inventory(self) -> str:
        lines = ["# Where your data lives", ""]
        counts = self.index.counts()
        if self.folders:
            total = sum(c["n"] for c in counts if c["kind"] == "file")
            lines += [
                "## Files",
                f"Folders: {', '.join(str(r) for r in self.folders.roots)}",
                f"{total} readable files indexed.",
            ]
            by_type = self.index.types().most_common(6)
            if by_type:
                lines.append("By type: " + ", ".join(f"{n} {t}" for t, n in by_type))
            for item in self.index.newest(3):
                lines.append(f"- recently changed: {item['title']} ({ago(item['at'])})")
            if self.unreadable:
                lines.append(
                    "Not readable yet (needs an extractor): "
                    + ", ".join(
                        f"{n} {ext} files"
                        for ext, n in sorted(self.unreadable.items(), key=lambda kv: -kv[1])
                    )
                )
            lines.append(
                "You can: search by words, ask questions, find the latest version of something.\n"
            )
        if self.mail:
            stats = self.mail.store.counts()
            total = sum(stats.values())
            lines += [
                "## Mail",
                f"{total} messages indexed: "
                + (", ".join(f"{n} {k}" for k, n in stats.items()) or "none yet"),
            ]
            lines.append(
                "You can: get a prioritised digest, see what needs a reply, ask what someone said.\n"
            )
        if self.loamz:
            inv = self.loamz.inventory()
            lines += [
                "## Loamz",
                f"{len(inv['catalogs'])} catalogs, {len(inv['tables'])} tables, {len(inv['collections'])} document collections.",
            ]
            lines += [f"- table {t}" for t in inv["tables"][:8]] + [
                f"- documents {c}" for c in inv["collections"][:5]
            ]
            lines += [f"(unavailable: {e})" for e in inv["errors"]]
            lines.append(
                "You can: run governed SQL, search documents, keep long-term memory for agents.\n"
            )
        if not (self.folders or self.mail or self.loamz):
            lines.append(
                "No sources are configured. Set DATA_FOLDERS, MAIL_USER or LOAMZ_ENDPOINT."
            )
        return "\n".join(lines).rstrip() + "\n"

    # ------------------------------------------------------------------ finding and asking
    def search(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        """Matches from every source, each given a fair share of the results (files alone must not crowd out mail)."""
        sources: list[list[dict[str, Any]]] = []
        sources.append(
            [
                {
                    "where": f"files ({h['meta'].get('type', '?')})",
                    "title": h["title"],
                    "ref": h["meta"].get("path", h["id"]),
                    "snippet": h["snippet"],
                    "text": h["text"][:1200],
                    "at": h["at"],
                }
                for h in self.index.search(query, limit)
            ]
        )
        if self.mail:
            sources.append(
                [
                    {
                        "where": "mail",
                        "title": h["title"],
                        "ref": h["id"],
                        "snippet": f"from {h['author']}",
                        "text": h["text"][:1200],
                        "at": h["at"],
                    }
                    for h in self.mail.store.search(query, limit=limit)
                ]
            )
        if self.loamz:
            sources.append(
                [
                    {**h, "text": h["snippet"], "at": ""}
                    for h in self.loamz.search(query, limit=limit)
                ]
            )
        # round robin: the best of each source first, then the second best of each, until the limit
        merged: list[dict[str, Any]] = []
        for rank in range(limit):
            for hits in sources:
                if rank < len(hits) and len(merged) < limit:
                    merged.append(hits[rank])
        return merged

    def ask(self, question: str) -> Answer:
        hits = self.search(question, limit=8)
        if not hits:
            return Answer(
                "I found nothing about that in the sources I can see. Try different words, or check `inventory` for what is covered.",
                [],
            )
        if self.llm is None:
            return Answer("No model is configured; these are the closest matches.", hits)
        context = "\n\n".join(
            f"[{h['ref']}] ({h['where']}) {h['title']}\n{h['text']}" for h in hits
        )
        try:
            return Answer(
                self.llm.text(ASK_SYSTEM, f"Excerpts:\n{context}\n\nQuestion: {question}"), hits
            )
        except LLMError as error:
            return Answer(
                f"The model could not answer ({error}); these are the closest matches.", hits
            )

    # ------------------------------------------------------------------ what to do with it
    def suggest(self) -> str:
        ideas = self.heuristic_ideas()
        if self.llm is not None:
            try:
                return self.llm.text(SUGGEST_SYSTEM, self.inventory())
            except LLMError:
                pass
        return (
            "\n".join(f"- {i}" for i in ideas) or "Nothing to suggest yet: scan some sources first."
        )

    def heuristic_ideas(self) -> list[str]:
        ideas: list[str] = []
        types = self.index.types()
        tables = types.get("csv", 0) + types.get("tsv", 0)
        if tables:
            ideas.append(
                f"Load your {tables} CSV files into Loamz tables and query them with SQL instead of opening each one."
            )
        if self.mail:
            ideas.append(
                "Run the mail digest every morning and unsubscribe from the senders it lists as noise."
            )
        if sum(self.unreadable.values()):
            ideas.append(
                f"Add a PDF/Office extractor: {sum(self.unreadable.values())} files cannot be searched yet."
            )
        if types.get("md", 0) + types.get("txt", 0) >= 5:
            ideas.append(
                "Keep your notes searchable by asking questions across them instead of remembering which file holds what."
            )
        if self.loamz:
            ideas.append(
                "Give an agent the Loamz tools so it can answer questions across your tables and documents on a schedule."
            )
        return ideas
