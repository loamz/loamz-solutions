"""Ask the ledger why: a plain-language account of what the trader saw, decided and did for one symbol."""

from __future__ import annotations

from loamz.solutions.kit.ledger import Entry, Ledger
from loamz.solutions.kit.llm import LLM, LLMError

SYSTEM = (
    "You explain an automated paper-trading system's decisions to its owner. Use only the log lines given. "
    "Say what the model signalled, whether an order was sent and what limited it. Be brief and concrete. "
    "Do not give investment advice and do not predict prices."
)


def describe(entry: Entry) -> str:
    p = entry.payload
    when = f"{entry.at:%Y-%m-%d %H:%M}"
    if entry.kind == "signal":
        return f"{when} model signal {p['side']} at {p['confidence']:.0%} confidence, price {p['price']}: {p['rationale']}"
    if entry.kind == "no.trade":
        return f"{when} no trade: {p['reason']}"
    if entry.kind == "action.proposed":
        return f"{when} proposed {p['side']} {p['qty']:g} at {p['price']} (notional {p['notional']}, position would be {p['position_pct']:.0%} of equity)"
    if entry.kind == "action.denied":
        return f"{when} DENIED by {p['rule']}: {p['reason']}"
    if entry.kind == "action.refused":
        return f"{when} not approved by a person: {p['reason']}"
    if entry.kind == "action.approved":
        return f"{when} approved by a person: {p['reason']}"
    if entry.kind == "action.done":
        return f"{when} order {p['status']}: {p['side']} {p['qty']:g} at {p['price']} ({p['mode']})"
    if entry.kind == "action.failed":
        return f"{when} order failed: {p['error']}"
    return f"{when} {entry.kind}"


def history(ledger: Ledger, symbol: str, limit: int = 30) -> list[str]:
    entries = [
        e
        for e in ledger.entries(limit=2000, newest_first=True)
        if e.subject in (symbol, f"order:{symbol}") and e.kind != "equity"
    ][:limit]
    return [describe(e) for e in reversed(entries)]


def explain(ledger: Ledger, symbol: str, llm: LLM | None = None) -> str:
    lines = history(ledger, symbol)
    if not lines:
        return f"Nothing has been recorded for {symbol} yet."
    log = "\n".join(lines)
    if llm is None:
        return log
    try:
        return llm.text(
            SYSTEM, f"Symbol: {symbol}\nLog (oldest first):\n{log}\n\nWhat happened and why?"
        )
    except LLMError:
        return log
