"""A complete solution in about sixty lines, built from the kit: pay supplier invoices, with limits, a person for big ones, and a record.

    uv run python examples/invoice_approver.py invoices.csv

invoices.csv has the columns: id, supplier, amount. Each invoice is read as a Record (Connector), proposed as an Action to the
Guard (rules: known supplier, a hard cap, a person approves anything over 1,000, nothing runs while the kill switch is on), and
every step lands in the ledger. `pay` here only prints; replace it with a call to your bank's API and nothing else changes.
"""

from __future__ import annotations

import csv
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from loamz.solutions.kit import (
    Action,
    Allowlist,
    Guard,
    KillSwitch,
    Ledger,
    MaxPerAction,
    Record,
    RequireApprovalAbove,
)
from loamz.solutions.kit.notify import Alert, Console, Notifier


class CsvInvoices:
    """A Connector: any source that can yield Records works the same way (an inbox, an API, a database)."""

    name = "invoices.csv"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def fetch(self, since: datetime | None = None, limit: int = 200) -> Iterator[Record]:
        with self.path.open(newline="") as handle:
            for row in csv.DictReader(handle):
                yield Record(
                    row["id"],
                    self.name,
                    "invoice",
                    f"{row['supplier']} {row['amount']}",
                    "",
                    datetime.now(UTC),
                    row["supplier"],
                    {"amount": float(row["amount"])},
                )


class Rules:
    """Which supplier to trust and how much to risk. Two lines of policy; the Guard does the enforcing and the recording."""

    suppliers = {"Acme Cloud", "Northwind LLC", "Initech"}
    hard_cap = 20_000
    needs_a_person_above = 1_000


def run(
    path: str | Path,
    ledger: Ledger,
    notifier: Notifier,
    approver=lambda action, reason: False,
    kill_file: str = ".state/KILL",
) -> dict[str, int]:
    guard = Guard(
        ledger,
        [
            KillSwitch(kill_file),
            Allowlist(Rules.suppliers, kinds=("pay",)),
            MaxPerAction("amount", Rules.hard_cap),
            RequireApprovalAbove("amount", Rules.needs_a_person_above, kinds=("pay",)),
        ],
        approver=approver,
    )
    paid = {"done": 0, "denied": 0, "refused": 0, "failed": 0}
    for invoice in CsvInvoices(path).fetch():
        action = Action(
            "pay", invoice.author, {"invoice": invoice.id, "amount": invoice.metadata["amount"]}
        )
        outcome = guard.run(
            action, execute=lambda a: {"paid": a.params["amount"], "supplier": a.subject}
        )
        paid[outcome.status] += 1
        if outcome.status != "done":
            notifier.send(
                Alert(
                    f"invoice:{invoice.id}",
                    f"Invoice {invoice.id} not paid",
                    f"{invoice.author}: {outcome.status}. {outcome.reason}",
                    "warn",
                )
            )
    return paid


if __name__ == "__main__":
    result = run(
        sys.argv[1],
        Ledger(".state/invoices.sqlite"),
        Console(),
        approver=lambda a, why: (
            input(f"Pay {a.subject} {a.params['amount']}? ({why}) [y/N] ").lower() == "y"
        ),
    )
    print(result)
