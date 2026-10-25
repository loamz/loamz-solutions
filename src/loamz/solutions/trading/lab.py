"""The research lab: replay years of prices through a strategy and the portfolio rules, honestly, and compare with SPY.

    result = simulate(panel, MomentumTrend(), PortfolioRules(), LabConfig())
    print(report(result))

Honest by construction:
- a decision uses only prices up to day t's close; its orders fill at day t+1's open, with costs on every fill;
- a stop is checked against each day's low and fills at the stop, or at the open when the price gaps below it;
- the book is marked at each close; the drawdown breaker acts on that mark and the book stays in cash for `restart_after`
  trading days (standing in for the person who restarts it);
- results are split into in-sample and a held-out recent period (`oos_days`), so a strategy is judged on data it was not
  tuned on, and shown next to SPY bought once with the same costs.

What it does not model: commissions beyond `cost_bps` (Alpaca charges none), market impact, dividends paid in cash (prices are
total-return adjusted instead), borrowing, and survivorship: the universe is today's large caps, which flatters the past.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

from loamz.solutions.trading.panel import Panel, sma
from loamz.solutions.trading.portfolio import PortfolioRules
from loamz.solutions.trading.strategy import Strategy


@dataclass(frozen=True)
class LabConfig:
    cash: float = 100_000.0
    cost_bps: float = 7.0  # per side: about 5 bps slippage plus half a 4 bps spread for large caps
    rebalance_drift: float = 0.25  # resize a held position only when it is this far from its target
    exit_below_sma: int = 100  # a held name closing below this average is sold at the next open
    daily_loss_stop: float = 0.03  # a day this bad means no new names at that rebalance
    restart_after: int = 20  # trading days in cash after the drawdown breaker
    oos_days: int = 378  # the held-out recent period, about 18 months
    benchmark: str = "SPY"


DEFAULTS = LabConfig()


@dataclass
class Position:
    qty: float
    entry: float
    entry_t: int
    stop: float
    managed: bool = (
        True  # False: a baseline's or the idle asset's, held without stops or trend exits
    )


@dataclass
class Trade:
    symbol: str
    entry_date: date
    entry: float
    exit_date: date
    exit: float
    qty: float
    risk: float  # entry minus the stop at entry, per share
    reason: str

    @property
    def pnl(self) -> float:
        return (self.exit - self.entry) * self.qty

    @property
    def r_multiple(self) -> float:
        return (self.exit - self.entry) / self.risk if self.risk > 0 else 0.0

    @property
    def days(self) -> int:
        return (self.exit_date - self.entry_date).days


@dataclass
class LabResult:
    strategy: str
    config: LabConfig
    dates: list[date]
    equity: list[float]
    benchmark: list[float]
    exposure: list[float]
    trades: list[Trade] = field(
        default_factory=list
    )  # the strategy's own trades (the idle asset's are left out)
    breaker: list[tuple[date, str]] = field(default_factory=list)
    traded_value: float = 0.0


def simulate(
    panel: Panel, strategy: Strategy, rules: PortfolioRules, config: LabConfig = DEFAULTS
) -> LabResult:
    cost = config.cost_bps / 10_000
    week_end = panel.week_ends()
    start, T = strategy.warmup, len(panel.dates)
    if start >= T - 1:
        raise ValueError(
            f"{strategy.name} needs more than {start} days of history; the panel has {T}"
        )

    cash, positions = config.cash, {}
    pending: dict[
        str, tuple[float, str, float | None]
    ] = {}  # symbol -> (target qty, reason, stop distance; None: no stop, not managed)
    last_close: dict[str, float] = {}
    peak, flat_since, prev_equity = config.cash, None, config.cash
    result = LabResult(strategy.name, config, [], [], [], [])

    bench = panel.close.get(config.benchmark)
    bench_open = panel.open.get(config.benchmark)
    bench_qty = (
        config.cash * (1 - cost) / bench_open[start]
        if bench and bench_open and bench_open[start]
        else 0.0
    )

    idle = rules.idle_in

    def close_trade(symbol: str, t: int, price: float, reason: str) -> None:
        nonlocal cash
        pos = positions.pop(symbol)
        fill = price * (1 - cost)
        cash += pos.qty * fill
        result.traded_value += pos.qty * fill
        if symbol == idle:
            return
        result.trades.append(
            Trade(
                symbol,
                panel.dates[pos.entry_t],
                pos.entry,
                panel.dates[t],
                fill,
                pos.qty,
                pos.entry - pos.stop,
                reason,
            )
        )

    for t in range(start, T):
        # 1. yesterday's decisions fill at today's open; sales first, so their cash pays for the buys
        def buying(item: tuple[str, tuple[float, str, float | None]]) -> tuple[bool, str]:
            symbol, (target, _, _) = item
            held = positions.get(symbol)
            return (target > (held.qty if held else 0.0), symbol)

        for symbol, (target, reason, distance) in sorted(pending.items(), key=buying):
            price = panel.open[symbol][t]
            if price is None:
                continue
            held = positions.get(symbol)
            current = held.qty if held else 0.0
            if target <= 0 and held:
                close_trade(symbol, t, price, reason)
            elif target > current:
                fill = price * (1 + cost)
                qty = min(target - current, max(cash, 0.0) / fill)
                if qty <= 0:
                    continue
                cash -= qty * fill
                result.traded_value += qty * fill
                if held:
                    held.entry = (held.entry * held.qty + fill * qty) / (held.qty + qty)
                    held.qty += qty
                elif distance is None:
                    positions[symbol] = Position(qty, fill, t, 0.0, managed=False)
                else:
                    positions[symbol] = Position(qty, fill, t, fill - distance)
            elif held and target < current:
                fill = price * (1 - cost)
                cash += (current - target) * fill
                result.traded_value += (current - target) * fill
                held.qty = target
        pending.clear()

        # 2. stops, against today's range
        for symbol in sorted(positions):
            pos, low, opened = positions[symbol], panel.low[symbol][t], panel.open[symbol][t]
            if low is not None and low <= pos.stop:
                close_trade(
                    symbol, t, min(opened, pos.stop) if opened is not None else pos.stop, "stop"
                )

        # 3. mark at the close
        for symbol in positions:
            c = panel.close[symbol][t]
            if c is not None:
                last_close[symbol] = c
        invested = sum(p.qty * last_close.get(s, p.entry) for s, p in positions.items())
        equity = cash + invested
        result.dates.append(panel.dates[t])
        result.equity.append(equity)
        result.exposure.append(invested / equity if equity else 0.0)
        result.benchmark.append(
            bench_qty * bench[t] if bench and bench[t] is not None else config.cash
        )
        peak = max(peak, equity)
        drawdown = 1 - equity / peak
        day_return = equity / prev_equity - 1 if prev_equity else 0.0
        prev_equity = equity
        if t == T - 1:
            break

        # 4. the drawdown breaker
        if flat_since is None and drawdown >= rules.flat_at:
            flat_since = t
            result.breaker.append((panel.dates[t], f"down {drawdown:.0%} from the peak: to cash"))
            for symbol in positions:
                pending[symbol] = (0.0, "drawdown breaker", 0.0)
            continue
        if flat_since is not None:
            if t - flat_since < config.restart_after:
                continue
            flat_since, peak = None, equity  # restarted: the drawdown is measured afresh
            result.breaker.append((panel.dates[t], "restarted"))

        # 5. decisions at today's close, for tomorrow's open
        for symbol, pos in positions.items():
            if not pos.managed:
                continue  # the idle asset leaves only with the market regime; a baseline only by its own weights
            c, line = panel.close[symbol][t], sma(panel.close[symbol], t, config.exit_below_sma)
            if c is not None and line is not None and c < line:
                pending[symbol] = (
                    0.0,
                    f"closed below its {config.exit_below_sma}-day average",
                    0.0,
                )
        if not week_end[t]:
            continue
        targets = strategy.targets(panel, t, set(positions))
        managed = targets.weights is None
        if managed:
            names = [s for s in targets.hold if s not in pending]
            if day_return <= -config.daily_loss_stop:
                names = [s for s in names if s in positions]  # a bad day: no new names
            weights = rules.size(names, panel, t, drawdown=drawdown)
            if targets.regime == "risk on":
                weights = rules.park(weights, drawdown)
        else:
            weights = {s: w * rules.scale(drawdown) for s, w in targets.weights.items()}
        for symbol in positions:
            if symbol not in weights and symbol not in pending:
                pending[symbol] = (0.0, "left the book", 0.0)
        for symbol, w in weights.items():
            price, stopped = panel.close[symbol][t], managed and symbol != idle
            distance = rules.stop_distance(panel, symbol, t) if stopped else None
            if not price or (stopped and not distance):
                continue
            target = w * equity / price
            held = positions.get(symbol)
            if held and abs(target - held.qty) <= config.rebalance_drift * held.qty:
                continue  # close enough: no trade
            pending[symbol] = (target, "rebalance" if held else "entered the book", distance)

    for symbol in sorted(
        positions
    ):  # mark what is still open as if sold at the last close, so trade stats include it
        close_trade(
            symbol, T - 1, last_close.get(symbol, positions[symbol].entry), "open at the end"
        )
    return result


def sweep(
    panel: Panel,
    make,
    grid: dict[str, list],
    rules: PortfolioRules,
    config: LabConfig = DEFAULTS,
) -> str:
    """Run the strategy for every combination in `grid` (its constructor's arguments) and show in- and out-of-sample results
    side by side: a robust idea works across neighbouring settings; one that works for a single setting is curve-fitted."""
    from itertools import product

    keys = list(grid)
    lines = [
        "| "
        + " | ".join(keys)
        + " | In-sample Sharpe | Out-of-sample Sharpe | Out-of-sample CAGR | Out-of-sample max DD |",
        "|" + "---|" * len(keys) + "---:|---:|---:|---:|",
    ]
    for values in product(*(grid[k] for k in keys)):
        result = simulate(panel, make(**dict(zip(keys, values, strict=True))), rules, config)
        split = max(1, len(result.dates) - config.oos_days)
        ins, _ = segment(result, 0, split + 1)
        oos, _ = segment(result, split)
        lines.append(
            "| "
            + " | ".join(str(v) for v in values)
            + f" | {ins.sharpe:.2f} | {oos.sharpe:.2f} | {pct(oos.cagr)} | {pct(-oos.max_drawdown)} |"
        )
    return "\n".join(lines)


def compare(
    panel: Panel, variants: dict[str, tuple[Strategy, PortfolioRules]], config: LabConfig = DEFAULTS
) -> tuple[str, dict[str, LabResult]]:
    """Several variants on the same data: the whole period and the held-out one, each with its gate verdict."""
    results = {
        name: simulate(panel, strategy, rules, config)
        for name, (strategy, rules) in variants.items()
    }
    lines = [
        "| Variant | CAGR | Sharpe | Max DD | Out-of-sample CAGR | Out-of-sample Sharpe | Out-of-sample max DD | Gate |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    spy_full = spy_oos = None
    for name, result in results.items():
        full, spy_full = segment(result, 0)
        oos, spy_oos = segment(result, max(1, len(result.dates) - config.oos_days))
        passed = "passed" if verdict(oos, spy_oos).startswith("**Gate passed") else "not passed"
        lines.append(
            f"| {name} | {pct(full.cagr)} | {full.sharpe:.2f} | {pct(-full.max_drawdown)} | {pct(oos.cagr)} | "
            f"{oos.sharpe:.2f} | {pct(-oos.max_drawdown)} | {passed} |"
        )
    if spy_full and spy_oos:
        lines.append(
            f"| SPY, bought once | {pct(spy_full.cagr)} | {spy_full.sharpe:.2f} | {pct(-spy_full.max_drawdown)} | "
            f"{pct(spy_oos.cagr)} | {spy_oos.sharpe:.2f} | {pct(-spy_oos.max_drawdown)} | |"
        )
    return "\n".join(lines), results


# ── metrics ──────────────────────────────────────────────────────────────────
@dataclass
class Metrics:
    start: date
    end: date
    cagr: float
    volatility: float
    sharpe: float
    sortino: float
    max_drawdown: float
    longest_drawdown_days: int
    exposure: float | None = None
    turnover: float | None = None


def metrics(dates: list[date], equity: list[float], exposure: list[float] | None = None) -> Metrics:
    rets = [equity[i] / equity[i - 1] - 1 for i in range(1, len(equity)) if equity[i - 1]]
    years = max((dates[-1] - dates[0]).days / 365.25, 1 / 365.25)
    cagr = (equity[-1] / equity[0]) ** (1 / years) - 1 if equity[0] > 0 and equity[-1] > 0 else -1.0
    mean = sum(rets) / len(rets) if rets else 0.0
    sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)) if len(rets) > 1 else 0.0
    downside = [min(r, 0.0) for r in rets]
    dsd = math.sqrt(sum(r * r for r in downside) / len(downside)) if downside else 0.0
    peak, worst, longest, run = equity[0], 0.0, 0, 0
    for value in equity:
        if value >= peak:
            peak, run = value, 0
        else:
            run += 1
            worst = max(worst, 1 - value / peak)
            longest = max(longest, run)
    return Metrics(
        dates[0],
        dates[-1],
        cagr,
        sd * math.sqrt(252),
        mean / sd * math.sqrt(252) if sd else 0.0,
        mean / dsd * math.sqrt(252) if dsd else 0.0,
        worst,
        longest,
        sum(exposure) / len(exposure) if exposure else None,
    )


def segment(result: LabResult, start: int, end: int | None = None) -> tuple[Metrics, Metrics]:
    """The strategy's and the benchmark's metrics over result days [start, end)."""
    sl = slice(start, end)
    return (
        metrics(result.dates[sl], result.equity[sl], result.exposure[sl]),
        metrics(result.dates[sl], result.benchmark[sl]),
    )


def report(result: LabResult, universe_note: str = "") -> str:
    full, spy = segment(result, 0)
    years = (result.dates[-1] - result.dates[0]).days / 365.25
    full.turnover = result.traded_value / (sum(result.equity) / len(result.equity)) / years / 2
    split = max(1, len(result.dates) - result.config.oos_days)
    ins, ins_spy = segment(result, 0, split + 1)
    oos, oos_spy = segment(result, split)
    trades = result.trades
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    gross_win, gross_loss = sum(t.pnl for t in wins), -sum(t.pnl for t in losses)

    def row(label: str, a: str, b: str = "") -> str:
        return f"| {label} | {a} | {b} |"

    lines = [
        f"## {result.strategy}: {result.dates[0]} to {result.dates[-1]} ({years:.1f} years)",
        "",
        f"Costs {result.config.cost_bps:.0f} bps a side; decisions at the close, fills at the next open. {universe_note}".strip(),
        "",
        "| | Strategy | SPY |",
        "|---|---:|---:|",
        row("CAGR", pct(full.cagr), pct(spy.cagr)),
        row("Volatility", pct(full.volatility), pct(spy.volatility)),
        row("Sharpe", f"{full.sharpe:.2f}", f"{spy.sharpe:.2f}"),
        row("Sortino", f"{full.sortino:.2f}", f"{spy.sortino:.2f}"),
        row("Max drawdown", pct(-full.max_drawdown), pct(-spy.max_drawdown)),
        row(
            "Longest drawdown (trading days)",
            str(full.longest_drawdown_days),
            str(spy.longest_drawdown_days),
        ),
        row("Time invested", pct(full.exposure or 0), "100%"),
        row("Turnover a year", f"{full.turnover:.1f}x", ""),
        "",
        "| Trades | Win rate | Average win | Average loss | Profit factor | Average R | Average days held |",
        "|---:|---:|---:|---:|---:|---:|---:|",
        "| {} | {} | {} | {} | {} | {:+.2f} | {:.0f} |".format(
            len(trades),
            pct(len(wins) / len(trades)) if trades else "-",
            money(gross_win / len(wins)) if wins else "-",
            money(-gross_loss / len(losses)) if losses else "-",
            f"{gross_win / gross_loss:.2f}" if gross_loss else "-",
            sum(t.r_multiple for t in trades) / len(trades) if trades else 0.0,
            sum(t.days for t in trades) / len(trades) if trades else 0.0,
        ),
        "",
        "| Period | Strategy CAGR | SPY CAGR | Strategy Sharpe | SPY Sharpe | Strategy max DD | SPY max DD |",
        "|---|---:|---:|---:|---:|---:|---:|",
        period_row(f"In-sample ({ins.start} to {ins.end})", ins, ins_spy),
        period_row(f"Out-of-sample ({oos.start} to {oos.end})", oos, oos_spy),
    ]
    lines += [
        "",
        "| Year | Strategy return | SPY return | Strategy max DD | SPY max DD |",
        "|---|---:|---:|---:|---:|",
    ]
    for year in sorted({d.year for d in result.dates}):
        idx = [i for i, d in enumerate(result.dates) if d.year == year]
        start = max(idx[0] - 1, 0)  # from the previous year's last close
        a, b = segment(result, start, idx[-1] + 1)
        partial = " (part)" if len(idx) < 240 else ""
        lines.append(
            f"| {year}{partial} | {pct(result.equity[idx[-1]] / result.equity[start] - 1)} | "
            f"{pct(result.benchmark[idx[-1]] / result.benchmark[start] - 1)} | {pct(-a.max_drawdown)} | {pct(-b.max_drawdown)} |"
        )
    if result.breaker:
        lines += ["", "Drawdown breaker: " + "; ".join(f"{d} {what}" for d, what in result.breaker)]
    lines += ["", verdict(oos, oos_spy)]
    return "\n".join(lines)


def period_row(label: str, a: Metrics, b: Metrics) -> str:
    return (
        f"| {label} | {pct(a.cagr)} | {pct(b.cagr)} | {a.sharpe:.2f} | {b.sharpe:.2f} | "
        f"{pct(-a.max_drawdown)} | {pct(-b.max_drawdown)} |"
    )


def verdict(oos: Metrics, oos_spy: Metrics) -> str:
    better = oos.sharpe > oos_spy.sharpe
    shallower = oos.max_drawdown <= max(oos_spy.max_drawdown, 0.15)
    if better and shallower:
        return (
            "**Gate passed:** out of sample, the strategy beat SPY risk-adjusted (Sharpe "
            f"{oos.sharpe:.2f} vs {oos_spy.sharpe:.2f}) without a deeper drawdown. Next: paper trading."
        )
    why = []
    if not better:
        why.append(
            f"its out-of-sample Sharpe {oos.sharpe:.2f} is not above SPY's {oos_spy.sharpe:.2f}"
        )
    if not shallower:
        why.append(f"its out-of-sample drawdown {oos.max_drawdown:.0%} is deeper than allowed")
    return "**Gate not passed:** " + " and ".join(why) + ". Adjust and re-test before it trades."


def pct(x: float) -> str:
    return f"{x:+.1%}" if x < 0 else f"{x:.1%}"


def money(x: float) -> str:
    return f"-${-x:,.0f}" if x < 0 else f"${x:,.0f}"
