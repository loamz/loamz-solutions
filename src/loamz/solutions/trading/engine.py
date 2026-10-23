"""The trading loop: read prices, ask the model, size an order, let the guard decide, send it, write it all down.

    trader = Trader(broker, model, config, ledger, approver=ask_a_person)
    report = trader.run_once()          # one pass over every symbol
    print(report.render())

Per symbol: the model gives a `Signal`. A buy is sized as `risk_per_trade x confidence x equity` and trimmed to fit the
position limit; a sell closes the position; `hold` and low-confidence signals do nothing. Whatever is planned goes through
the `Guard` (kill switch, allowlist, notional, position and loss limits, trading hours, order count, cooldown, and a person's
approval on a live account). One order per symbol and side per day by construction: the order id is derived from them, so a
repeated run cannot trade twice.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from typing import Any

from loamz.solutions.kit.guard import Action, Approver, Guard, Outcome, refuse
from loamz.solutions.kit.ledger import Ledger
from loamz.solutions.trading.broker import Broker, BrokerError
from loamz.solutions.trading.models import Account, Position, Signal
from loamz.solutions.trading.risk import RiskConfig, standard_rules
from loamz.solutions.trading.signals import SignalModel


@dataclass
class TraderConfig:
    risk: RiskConfig
    min_confidence: float = 0.6
    risk_per_trade: float = 0.01  # of equity committed by a buy at confidence 1.0
    lookback: int = 60  # bars given to the model


@dataclass
class SymbolResult:
    symbol: str
    signal: Signal | None
    status: str  # "traded", "no trade", "denied", "refused", "failed", "error"
    detail: str = ""


@dataclass
class RunReport:
    results: list[SymbolResult] = field(default_factory=list)
    equity: float = 0.0
    day_pnl: float = 0.0

    def render(self) -> str:
        lines = [f"equity {self.equity:,.2f}  today {self.day_pnl:+,.2f}"]
        for r in self.results:
            side = r.signal.side if r.signal else "-"
            conf = f" {r.signal.confidence:.0%}" if r.signal and r.signal.side != "hold" else ""
            lines.append(f"{r.symbol:6} {side}{conf:5} {r.status:9} {r.detail}")
        return "\n".join(lines)


def order_id(symbol: str, side: str, day: str) -> str:
    """One order per symbol, side and day: the same pass run twice sends the same id, which the broker ignores."""
    return hashlib.sha1(f"{symbol}|{side}|{day}".encode()).hexdigest()[:40]


class Trader:
    def __init__(
        self,
        broker: Broker,
        model: SignalModel,
        config: TraderConfig,
        ledger: Ledger,
        approver: Approver = refuse,
    ) -> None:
        self.broker, self.model, self.config, self.ledger = broker, model, config, ledger
        self.guard = Guard(
            ledger, standard_rules(config.risk, broker, hours=broker.mode != "sim"), approver
        )

    # ------------------------------------------------------------------ planning
    def plan(
        self, signal: Signal, account: Account, held: Position | None, price: float
    ) -> tuple[dict[str, Any] | None, str]:
        """(order parameters, "") or (None, why nothing is done)."""
        c = self.config
        if signal.side == "hold":
            return None, signal.rationale or "the model says hold"
        if signal.confidence < c.min_confidence:
            return None, f"confidence {signal.confidence:.0%} is below {c.min_confidence:.0%}"
        qty_held = held.qty if held else 0.0
        if signal.side == "sell":
            if qty_held <= 0:
                return None, "nothing to sell (no shorting)"
            qty = qty_held
        else:
            target = account.equity * c.risk_per_trade * signal.confidence
            room = account.equity * c.risk.max_position_pct - qty_held * price
            qty = math.floor(min(target, room, account.buying_power) / price)
            if qty < 1:
                return None, "too small or at the position limit"
        value_after = (qty_held + (qty if signal.side == "buy" else -qty)) * price
        return {
            "side": signal.side,
            "qty": qty,
            "price": round(price, 4),
            "notional": round(qty * price, 2),
            "position_pct": round(value_after / account.equity, 4) if account.equity else 0.0,
            "position_qty": qty_held,
            "day_pnl_pct": round(account.day_pnl_pct, 5),
            "confidence": round(signal.confidence, 3),
            "why": signal.rationale[:300],
        }, ""

    # ------------------------------------------------------------------ one pass
    def run_once(self) -> RunReport:
        report = RunReport()
        try:
            account = self.broker.account()
            held = {p.symbol: p for p in self.broker.positions()}
        except BrokerError as error:
            self.ledger.append("run.error", "broker", error=str(error))
            report.results.append(SymbolResult("*", None, "error", str(error)))
            return report
        report.equity, report.day_pnl = account.equity, account.day_pnl
        day = self.ledger.now().strftime("%Y-%m-%d")
        for symbol in sorted(self.config.risk.symbols):
            report.results.append(self.step(symbol, account, held.get(symbol), day))
        self.snapshot()
        return report

    def step(self, symbol: str, account: Account, held: Position | None, day: str) -> SymbolResult:
        try:
            bars = self.broker.bars(symbol, self.config.lookback)
            signal = self.model.predict(symbol, bars)
            price = signal.price or self.broker.price(symbol)
        except BrokerError as error:
            self.ledger.append("run.error", symbol, error=str(error))
            return SymbolResult(symbol, None, "error", str(error))
        self.ledger.append(
            "signal",
            symbol,
            side=signal.side,
            confidence=round(signal.confidence, 3),
            price=price,
            rationale=signal.rationale[:300],
        )
        params, why = self.plan(signal, account, held, price)
        if params is None:
            self.ledger.append("no.trade", symbol, reason=why)
            return SymbolResult(symbol, signal, "no trade", why)
        client_id = order_id(symbol, params["side"], day)
        outcome = self.guard.run(
            Action("order", symbol, {**params, "client_id": client_id}), self.send
        )
        return SymbolResult(symbol, signal, self.status(outcome), self.detail(outcome, params))

    def send(self, action: Action) -> dict[str, Any]:
        p = action.params
        order = self.broker.submit(action.subject, p["side"], p["qty"], p["client_id"])
        return {
            "symbol": action.subject,
            "side": order.side,
            "qty": order.qty,
            "price": order.filled_avg_price or p["price"],
            "status": order.status,
            "order_id": order.id,
            "mode": self.broker.mode,
        }

    @staticmethod
    def status(outcome: Outcome) -> str:
        return "traded" if outcome.status == "done" else outcome.status

    @staticmethod
    def detail(outcome: Outcome, params: dict[str, Any]) -> str:
        if outcome.status == "done":
            r = outcome.result
            return f"{r['side']} {r['qty']:g} at {r['price']:,.2f} ({r['status']})"
        return outcome.reason

    def snapshot(self) -> None:
        account = self.broker.account()
        positions = {p.symbol: p.qty for p in self.broker.positions()}
        self.ledger.append(
            "equity",
            "account",
            equity=round(account.equity, 2),
            cash=round(account.cash, 2),
            positions=positions,
            mode=self.broker.mode,
        )
