"""The vocabulary of trading: bars, signals, accounts, positions and orders."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

Side = Literal["buy", "sell"]


@dataclass(frozen=True)
class Bar:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass(frozen=True)
class Signal:
    """What a model thinks: act (`buy` or `sell`) or stay out (`hold`), how sure, and why."""

    symbol: str
    side: Literal["buy", "sell", "hold"]
    confidence: float  # 0..1
    rationale: str = ""
    at: datetime = field(default_factory=lambda: datetime.now(UTC))
    price: float | None = None  # the price the model saw


@dataclass(frozen=True)
class Position:
    symbol: str
    qty: float
    avg_price: float
    market_price: float

    @property
    def market_value(self) -> float:
        return self.qty * self.market_price


@dataclass(frozen=True)
class Account:
    equity: float
    cash: float
    buying_power: float
    last_equity: float  # equity at the previous close: the base for today's profit and loss

    @property
    def day_pnl(self) -> float:
        return self.equity - self.last_equity

    @property
    def day_pnl_pct(self) -> float:
        return self.day_pnl / self.last_equity if self.last_equity else 0.0


@dataclass(frozen=True)
class Order:
    id: str
    client_id: str
    symbol: str
    side: Side
    qty: float
    status: str  # "filled", "accepted", "rejected" ...
    filled_qty: float = 0.0
    filled_avg_price: float | None = None
