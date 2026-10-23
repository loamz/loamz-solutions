"""Stock alerts that say what to do: a watchlist of price levels, big moves and model signals.

    AAPL>200, AAPL<170, TSLA:move=3%, MSFT:signal

- `SYM>PRICE` / `SYM<PRICE`: the last price crossed a level you care about;
- `SYM:move=N%`: today's move against the previous close is at least N percent either way;
- `SYM:signal`: the signal model says buy or sell with enough confidence. The alert then answers "trade or not": what the
  trader would do (how many shares, at what size) and whether every risk rule would let it, with the rule that would stop it.

Nothing is traded here. Each alert is announced once a day per rule and direction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from loamz.solutions.kit.guard import Action, Decision
from loamz.solutions.kit.ledger import Ledger
from loamz.solutions.kit.notify import Alert, Notifier
from loamz.solutions.trading.broker import Broker, BrokerError
from loamz.solutions.trading.engine import Trader
from loamz.solutions.trading.signals import SignalModel

RULE = re.compile(
    r"^\s*([A-Za-z.]{1,10})\s*(?:(>|<)\s*([\d.]+)|:\s*(move)\s*=\s*([\d.]+)\s*%?|:\s*(signal))\s*$"
)


@dataclass(frozen=True)
class WatchRule:
    symbol: str
    kind: str  # "above", "below", "move", "signal"
    value: float = 0.0

    def __str__(self) -> str:
        return {
            "above": f"{self.symbol}>{self.value:g}",
            "below": f"{self.symbol}<{self.value:g}",
            "move": f"{self.symbol}:move={self.value:g}%",
            "signal": f"{self.symbol}:signal",
        }[self.kind]


def parse_rules(text: str) -> list[WatchRule]:
    rules = []
    for part in filter(None, (p.strip() for p in text.split(","))):
        found = RULE.match(part)
        if not found:
            raise ValueError(
                f"Cannot read the watch rule {part!r}. Use SYM>PRICE, SYM<PRICE, SYM:move=3% or SYM:signal"
            )
        symbol, sign, level, move, size, signal = found.groups()
        if sign:
            rules.append(
                WatchRule(symbol.upper(), "above" if sign == ">" else "below", float(level))
            )
        elif move:
            rules.append(WatchRule(symbol.upper(), "move", float(size)))
        else:
            rules.append(WatchRule(symbol.upper(), "signal"))
    return rules


class Watcher:
    def __init__(
        self,
        broker: Broker,
        rules: list[WatchRule],
        notifier: Notifier,
        ledger: Ledger,
        *,
        model: SignalModel | None = None,
        trader: Trader | None = None,
    ) -> None:
        self.broker, self.rules, self.notifier, self.ledger = broker, rules, notifier, ledger
        self.model, self.trader = model, trader

    def run_once(self) -> list[Alert]:
        sent: list[Alert] = []
        day = self.ledger.now().strftime("%Y-%m-%d")
        for rule in self.rules:
            try:
                alert = self.check(rule, day)
            except BrokerError as error:
                self.ledger.append("watch.error", rule.symbol, error=str(error))
                continue
            if alert:
                self.ledger.append(
                    "watch.triggered", rule.symbol, rule=str(rule), title=alert.title
                )
                self.notifier.send(alert)
                sent.append(alert)
        return sent

    def check(self, rule: WatchRule, day: str) -> Alert | None:
        key = f"watch:{rule}:{day}"
        if rule.kind in ("above", "below"):
            price = self.broker.price(rule.symbol)
            hit = price > rule.value if rule.kind == "above" else price < rule.value
            if not hit:
                return None
            word = "above" if rule.kind == "above" else "below"
            return Alert(
                key, f"{rule.symbol} is {word} {rule.value:g}", f"Last price {price:,.2f}.", "warn"
            )
        if rule.kind == "move":
            bars = self.broker.bars(rule.symbol, 3)
            if len(bars) < 2:
                return None
            previous, price = bars[-2].close, self.broker.price(rule.symbol)
            change = (price / previous - 1) * 100
            if abs(change) < rule.value:
                return None
            return Alert(
                f"{key}:{'up' if change > 0 else 'down'}",
                f"{rule.symbol} moved {change:+.1f}% today",
                f"{previous:,.2f} → {price:,.2f}",
                "warn",
            )
        return self.signal_alert(rule, key)

    def signal_alert(self, rule: WatchRule, key: str) -> Alert | None:
        if self.model is None:
            return None
        lookback = self.trader.config.lookback if self.trader else 60
        signal = self.model.predict(rule.symbol, self.broker.bars(rule.symbol, lookback))
        floor = self.trader.config.min_confidence if self.trader else 0.6
        if signal.side == "hold" or signal.confidence < floor:
            return None
        verdict = (
            self.would_trade(signal)
            if self.trader
            else "(no trader is configured to size or check it)"
        )
        return Alert(
            f"{key}:{signal.side}",
            f"Signal: {signal.side.upper()} {rule.symbol} ({signal.confidence:.0%})",
            f"{signal.rationale}\n{verdict}",
            "info",
        )

    def would_trade(self, signal) -> str:
        """What the trader would do with this signal, and whether every risk rule would allow it. Nothing is sent or recorded."""
        trader = self.trader
        account = trader.broker.account()
        held = {p.symbol: p for p in trader.broker.positions()}.get(signal.symbol)
        price = signal.price or trader.broker.price(signal.symbol)
        params, why = trader.plan(signal, account, held, price)
        if params is None:
            return f"No trade: {why}."
        action = Action("order", signal.symbol, {**params, "client_id": "dry-run"})
        for rule in trader.guard.rules:
            verdict = rule.check(action, trader.ledger)
            if verdict.decision is Decision.DENY:
                return (
                    f"No trade: {getattr(rule, 'name', 'a rule')} would stop it ({verdict.reason})."
                )
        needs = [
            r
            for r in trader.guard.rules
            if r.check(action, trader.ledger).decision is Decision.APPROVE
        ]
        suffix = " It would wait for your approval." if needs else ""
        return f"Would trade: {params['side']} {params['qty']:g} at about {params['price']:,.2f} ({params['notional']:,.0f}); every limit passes.{suffix}"
