"""Replay history through the same engine, guard and ledger the live loop uses.

    result = backtest(MovingAverageCross(), {"AAPL": bars}, TraderConfig(RiskConfig({"AAPL"})))
    print(result.summary.render()); print(f"buy and hold: {result.benchmark_pct:+.2%}")

Honest by construction: a decision is made on the close of bar *i* (the model sees bars up to and including *i*) and the order
fills at the open of bar *i+1* with slippage, never at the price that produced the signal. The risk rules run unchanged, with
the ledger's clock set to the bar's time. A good backtest is necessary and nowhere near sufficient: it says nothing about
the future, fees you did not model, or a regime change.
"""

from __future__ import annotations

import dataclasses
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from loamz.solutions.kit.ledger import Entry, Ledger
from loamz.solutions.trading.broker import SimBroker
from loamz.solutions.trading.engine import Trader, TraderConfig
from loamz.solutions.trading.models import Bar
from loamz.solutions.trading.pnl import Summary, summarize
from loamz.solutions.trading.signals import SignalModel


@dataclass
class BacktestResult:
    summary: Summary
    benchmark_pct: float  # equal-weight buy and hold over the same window
    trades: list[Entry]
    ledger: Ledger


def backtest(
    model: SignalModel,
    bars: dict[str, list[Bar]],
    config: TraderConfig,
    *,
    cash: float = 100_000.0,
    warmup: int = 35,
    slippage_bps: float = 1.0,
) -> BacktestResult:
    symbols = sorted(bars)
    length = min(len(bars[s]) for s in symbols)
    if length < warmup + 2:
        raise ValueError(f"Need at least {warmup + 2} bars per symbol, have {length}")
    now = [bars[symbols[0]][warmup].ts]
    ledger = Ledger(":memory:", clock=lambda: now[0])
    sim = SimBroker(cash, slippage_bps=slippage_bps)
    risk = dataclasses.replace(
        config.risk, symbols=set(symbols), kill_file="/nonexistent/loamz-kill"
    )
    trader = Trader(sim, model, dataclasses.replace(config, risk=risk), ledger)
    for i in range(warmup, length - 1):
        now[0] = bars[symbols[0]][i].ts
        sim.last_equity = sim.equity()
        for s in symbols:
            sim.history[s] = bars[s][: i + 1]
            sim.prices[s] = bars[s][i].close
            sim.fills[s] = bars[s][i + 1].open
        trader.run_once()
    # close the books at the last close
    for s in symbols:
        sim.prices[s] = bars[s][length - 1].close
        sim.fills.pop(s, None)
    now[0] = bars[symbols[0]][length - 1].ts
    trader.snapshot()
    held = [bars[s][length - 1].close / bars[s][warmup].close - 1 for s in symbols]
    return BacktestResult(
        summarize(ledger), sum(held) / len(held), ledger.entries(kind="action.done"), ledger
    )


def synth_bars(
    n: int = 400, seed: int = 1, start: float = 100.0, drift: float = 0.0006, vol: float = 0.012
) -> list[Bar]:
    """A random walk whose drift flips sign every few months, so trend-following has something to find (and to lose to)."""
    rng = random.Random(seed)
    price, day, bars = start, datetime(2024, 1, 2, 14, 30, tzinfo=UTC), []
    regime = 1.0
    for i in range(n):
        if i % 70 == 0:
            regime = rng.choice([-1.0, 1.0])
        open_ = price * (1 + rng.gauss(0, vol / 4))
        close = open_ * (1 + regime * drift + rng.gauss(0, vol))
        high, low = (
            max(open_, close) * (1 + abs(rng.gauss(0, vol / 3))),
            min(open_, close) * (1 - abs(rng.gauss(0, vol / 3))),
        )
        bars.append(
            Bar(day, round(open_, 4), round(high, 4), round(low, 4), round(close, 4), 1_000_000)
        )
        price = close
        day += timedelta(days=1)
        while day.weekday() >= 5:
            day += timedelta(days=1)
    return bars


def load_csv(path: str) -> list[Bar]:
    """Bars from a CSV with columns date (or timestamp), open, high, low, close and optionally volume."""
    import csv

    bars = []
    with open(path, newline="") as handle:
        for row in csv.DictReader(handle):
            raw = row.get("date") or row.get("timestamp") or row.get("t")
            ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            bars.append(
                Bar(
                    ts if ts.tzinfo else ts.replace(tzinfo=UTC),
                    float(row["open"]),
                    float(row["high"]),
                    float(row["low"]),
                    float(row["close"]),
                    float(row.get("volume") or 0),
                )
            )
    return sorted(bars, key=lambda b: b.ts)
