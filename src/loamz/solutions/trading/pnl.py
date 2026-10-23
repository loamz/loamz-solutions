"""Profit and loss from the ledger alone: what was traded, and how the account changed."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime

from loamz.solutions.kit.ledger import Ledger


@dataclass
class Summary:
    start_equity: float = 0.0
    end_equity: float = 0.0
    orders: int = 0
    round_trips: int = 0
    wins: int = 0
    realized: float = 0.0
    max_drawdown_pct: float = 0.0
    by_symbol: dict[str, float] = field(default_factory=dict)
    curve: list[tuple[datetime, float]] = field(default_factory=list)

    @property
    def pnl(self) -> float:
        return self.end_equity - self.start_equity

    @property
    def pnl_pct(self) -> float:
        return self.pnl / self.start_equity if self.start_equity else 0.0

    @property
    def win_rate(self) -> float:
        return self.wins / self.round_trips if self.round_trips else 0.0

    def render(self) -> str:
        lines = [
            f"equity     {self.start_equity:,.2f} → {self.end_equity:,.2f}  ({self.pnl:+,.2f}, {self.pnl_pct:+.2%})",
            f"orders     {self.orders}   round trips {self.round_trips}   win rate {self.win_rate:.0%}   realized {self.realized:+,.2f}",
            f"drawdown   {self.max_drawdown_pct:.2%} (worst peak to trough)",
        ]
        if self.by_symbol:
            lines.append(
                "by symbol  "
                + "  ".join(f"{s} {v:+,.2f}" for s, v in sorted(self.by_symbol.items()))
            )
        return "\n".join(lines)


def summarize(ledger: Ledger, since: datetime | None = None) -> Summary:
    out = Summary()
    peak = 0.0
    for e in ledger.entries(kind="equity", since=since):
        equity = e.payload["equity"]
        if not out.curve:
            out.start_equity = equity
        out.end_equity = equity
        out.curve.append((e.at, equity))
        peak = max(peak, equity)
        if peak:
            out.max_drawdown_pct = max(out.max_drawdown_pct, (peak - equity) / peak)
    lots: dict[str, deque[list[float]]] = defaultdict(deque)  # symbol -> FIFO of [qty, price]
    by_symbol: dict[str, float] = defaultdict(float)
    for e in ledger.entries(kind="action.done", since=since):
        if not e.subject.startswith("order:"):
            continue
        symbol, side, qty, price = (
            e.payload["symbol"],
            e.payload["side"],
            e.payload["qty"],
            e.payload["price"],
        )
        out.orders += 1
        if side == "buy":
            lots[symbol].append([qty, price])
            continue
        remaining, gained = qty, 0.0
        while remaining > 1e-9 and lots[symbol]:
            lot = lots[symbol][0]
            used = min(lot[0], remaining)
            gained += used * (price - lot[1])
            lot[0] -= used
            remaining -= used
            if lot[0] <= 1e-9:
                lots[symbol].popleft()
        out.round_trips += 1
        out.wins += gained > 0
        out.realized += gained
        by_symbol[symbol] += gained
    out.by_symbol = dict(by_symbol)
    return out
