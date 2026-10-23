"""Risk rules for orders. Each rule reads facts the engine puts in the action (`position_pct`, `day_pnl_pct`, `position_qty`)
or in the ledger (orders today, the last order on a symbol), so every limit is enforced in one place, whatever the model says.

The limits are deliberately simple and conservative. They are your last line of defence against a model that is wrong,
confident and fast: tune them to money you can afford to lose.
"""

from __future__ import annotations

from dataclasses import dataclass

from loamz.solutions.kit.guard import (
    Action,
    Allowlist,
    KillSwitch,
    MaxPerAction,
    RequireApproval,
    Rule,
    Verdict,
)
from loamz.solutions.kit.ledger import Ledger
from loamz.solutions.trading.broker import Broker


@dataclass
class RiskConfig:
    symbols: set[str]
    max_order_notional: float = 5_000.0
    max_position_pct: float = 0.20  # of equity, per symbol
    max_daily_loss_pct: float = 0.02  # stop buying after losing this much today
    max_orders_per_day: int = 10
    cooldown_minutes: int = 30  # between orders on one symbol
    kill_file: str = ".state/KILL"


def is_order(action: Action) -> bool:
    return action.kind == "order"


class TradingHours:
    name = "trading-hours"

    def __init__(self, broker: Broker) -> None:
        self.broker = broker

    def check(self, action: Action, ledger: Ledger) -> Verdict:
        return Verdict.allow() if self.broker.is_open() else Verdict.deny("the market is closed")


class MaxPositionPct:
    name = "max-position"

    def __init__(self, limit: float) -> None:
        self.limit = limit

    def check(self, action: Action, ledger: Ledger) -> Verdict:
        if action.params.get("side") != "buy":
            return Verdict.allow()
        after = float(action.params.get("position_pct", 0))
        if after > self.limit:
            return Verdict.deny(
                f"the position would be {after:.0%} of equity, above the {self.limit:.0%} limit"
            )
        return Verdict.allow()


class MaxDailyLoss:
    name = "max-daily-loss"

    def __init__(self, limit: float) -> None:
        self.limit = limit

    def check(self, action: Action, ledger: Ledger) -> Verdict:
        if action.params.get("side") != "buy":
            return Verdict.allow()  # selling reduces risk and stays possible
        loss = -float(action.params.get("day_pnl_pct", 0))
        if loss >= self.limit:
            return Verdict.deny(
                f"down {loss:.1%} today, at or past the {self.limit:.1%} daily loss limit: no new buys"
            )
        return Verdict.allow()


class NoShorting:
    name = "no-shorting"

    def check(self, action: Action, ledger: Ledger) -> Verdict:
        if action.params.get("side") == "sell" and float(action.params.get("qty", 0)) > float(
            action.params.get("position_qty", 0)
        ):
            return Verdict.deny("selling more than is held would be a short")
        return Verdict.allow()


def done_orders(ledger: Ledger, since):
    return [
        e for e in ledger.entries(kind="action.done", since=since) if e.subject.startswith("order:")
    ]


class MaxOrdersPerDay:
    name = "max-orders-per-day"

    def __init__(self, limit: int) -> None:
        self.limit = limit

    def check(self, action: Action, ledger: Ledger) -> Verdict:
        midnight = ledger.now().replace(hour=0, minute=0, second=0, microsecond=0)
        count = len(done_orders(ledger, midnight))
        if count >= self.limit:
            return Verdict.deny(f"{count} orders already today (limit {self.limit})")
        return Verdict.allow()


class AlreadySent:
    """An order id (symbol, side, day) is sent once. A second one would be ignored by the broker while looking like a trade."""

    name = "already-sent"

    def check(self, action: Action, ledger: Ledger) -> Verdict:
        client_id = action.params.get("client_id")
        midnight = ledger.now().replace(hour=0, minute=0, second=0, microsecond=0)
        if client_id and any(
            e.payload.get("client_id") == client_id for e in done_orders(ledger, midnight)
        ):
            return Verdict.deny(
                f"already sent a {action.params.get('side')} for {action.subject} today"
            )
        return Verdict.allow()


class Cooldown:
    name = "cooldown"

    def __init__(self, minutes: int) -> None:
        self.minutes = minutes

    def check(self, action: Action, ledger: Ledger) -> Verdict:
        from datetime import timedelta

        recent = [
            e
            for e in done_orders(ledger, ledger.now() - timedelta(minutes=self.minutes))
            if e.subject == f"order:{action.subject}"
        ]
        if recent:
            return Verdict.deny(f"{action.subject} traded {self.minutes} minutes ago or less")
        return Verdict.allow()


def standard_rules(config: RiskConfig, broker: Broker, *, hours: bool = True) -> list[Rule]:
    """The full set, cheapest and most absolute first. Live accounts additionally need a person to approve each order."""
    rules: list[Rule] = [
        KillSwitch(config.kill_file),
        Allowlist(config.symbols, kinds=("order",)),
        NoShorting(),
        MaxPerAction("notional", config.max_order_notional, kinds=("order",)),
        MaxPositionPct(config.max_position_pct),
        MaxDailyLoss(config.max_daily_loss_pct),
        MaxOrdersPerDay(config.max_orders_per_day),
        AlreadySent(),
        Cooldown(config.cooldown_minutes),
    ]
    if hours:
        rules.insert(1, TradingHours(broker))
    if broker.mode == "live":
        rules.append(RequireApproval(("order",), "live trading: a person must confirm every order"))
    return rules
