"""A simulated market for trying the Paper Trader (and its tool server) with nothing real connected."""

from __future__ import annotations

from pathlib import Path

from loamz.solutions.kit.guard import refuse
from loamz.solutions.kit.ledger import Ledger
from loamz.solutions.trading.backtest import synth_bars
from loamz.solutions.trading.broker import SimBroker
from loamz.solutions.trading.engine import Trader, TraderConfig
from loamz.solutions.trading.risk import RiskConfig
from loamz.solutions.trading.signals import MovingAverageCross


def demo_trader(state: Path, symbols: tuple[str, ...] = ("AAA", "BBB")) -> tuple[Trader, Ledger]:
    state.mkdir(parents=True, exist_ok=True)
    broker = SimBroker(100_000.0, slippage_bps=1.0)
    for i, symbol in enumerate(symbols):
        bars = synth_bars(120, seed=i + 1)
        broker.history[symbol] = bars
        broker.prices[symbol] = bars[-1].close
    ledger = Ledger(state / "trader.sqlite")
    config = TraderConfig(
        RiskConfig(set(symbols), kill_file=str(state / "KILL"), cooldown_minutes=0),
        min_confidence=0.6,
    )
    return Trader(broker, MovingAverageCross(5, 15), config, ledger, approver=refuse), ledger
