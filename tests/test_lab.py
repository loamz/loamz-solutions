"""The research lab: indicators use only the past, the strategy ranks and rates by its rules, sizing follows risk and caps,
and the simulator fills at the next open with costs, honours stops and the drawdown breaker, and judges out of sample."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from loamz.solutions.trading.lab import LabConfig, metrics, report, simulate
from loamz.solutions.trading.panel import Panel, atr, ret, sma, volatility
from loamz.solutions.trading.portfolio import PortfolioRules
from loamz.solutions.trading.strategy import MomentumTrend


def trading_days(n: int, start: date = date(2021, 1, 4)) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def path(n: int, daily: float, start: float = 100.0, wiggle: float = 0.0) -> list[float]:
    """A price path growing `daily` a day, with an alternating wiggle so volatility is never zero."""
    out, p = [], start
    for i in range(n):
        p *= 1 + daily + (wiggle if i % 2 else -wiggle)
        out.append(p)
    return out


def make_panel(paths: dict[str, list[float]], sectors: dict[str, str] | None = None) -> Panel:
    n = len(next(iter(paths.values())))
    dates = trading_days(n)
    rows = []
    for symbol, closes in paths.items():
        for i, c in enumerate(closes):
            o = closes[i - 1] if i else c
            rows.append((symbol, dates[i], o, max(o, c) * 1.01, min(o, c) * 0.99, c, 1e6))
    return Panel.from_rows(rows, sectors or {s: "Tech" for s in paths}, etfs={"SPY"})


# ── indicators ───────────────────────────────────────────────────────────────
def test_indicators_use_only_data_up_to_the_day_asked():
    series = [float(i) for i in range(1, 301)]
    assert sma(series, 9, 10) == pytest.approx(5.5)
    assert sma(series, 8, 10) is None  # not enough history yet
    assert ret(series, 299, 252, skip=21) == pytest.approx(series[278] / series[47] - 1)
    gappy = series[:100] + [None] + series[101:]
    assert sma(gappy, 105, 10) is None and volatility(gappy, 105, 20) is None
    assert atr([2.0] * 30, [1.0] * 30, [1.5] * 30, 29, 20) == pytest.approx(1.0)


# ── strategy ─────────────────────────────────────────────────────────────────
def test_the_strongest_uptrends_rank_first_and_downtrends_are_rated_sell():
    n = 320
    panel = make_panel(
        {
            "SPY": path(n, 0.0005, wiggle=0.002),
            "FAST": path(n, 0.002, wiggle=0.004),
            "SLOW": path(n, 0.0008, wiggle=0.004),
            "DOWN": path(n, -0.0015, wiggle=0.004),
        }
    )
    targets = MomentumTrend(top=1, keep=2).targets(panel, n - 1, held=set())
    assert targets.regime == "risk on"
    assert targets.hold == ["FAST"]
    assert targets.ratings["FAST"].level == "strong buy" and targets.ratings["FAST"].rank == 1
    assert targets.ratings["SLOW"].level == "buy"
    assert targets.ratings["DOWN"].level == "strong sell"
    assert "12-1 month" in targets.ratings["FAST"].reason


def test_a_held_name_is_kept_inside_the_buffer_and_nothing_new_is_bought_when_the_market_is_down():
    n = 320
    up = {
        "SPY": path(n, 0.0005, wiggle=0.002),
        "FAST": path(n, 0.002, wiggle=0.004),
        "SLOW": path(n, 0.0008, wiggle=0.004),
    }
    held = MomentumTrend(top=1, keep=2).targets(make_panel(up), n - 1, held={"SLOW"})
    assert held.hold == [
        "SLOW"
    ]  # kept (rank 2, inside keep); the one free slot is taken by it, top is 1
    down = dict(up, SPY=path(n, -0.001, wiggle=0.002))
    off = MomentumTrend(top=2, keep=2).targets(make_panel(down), n - 1, held={"SLOW"})
    assert off.regime == "risk off" and off.hold == ["SLOW"] and "no new positions" in off.note


# ── sizing ───────────────────────────────────────────────────────────────────
def test_positions_are_sized_by_risk_then_capped_by_name_sector_and_cash():
    n = 60
    panel = make_panel(
        {
            "A": path(n, 0.001, wiggle=0.01),
            "B": path(n, 0.001, wiggle=0.002),
            "C": path(n, 0.001, wiggle=0.002),
        },
        sectors={"A": "Tech", "B": "Tech", "C": "Health"},
    )
    rules = PortfolioRules(max_weight=0.5, max_sector=1.0, cash_reserve=0.0)
    w = rules.size(["A", "B"], panel, n - 1)
    distance_a = rules.stop_distance(panel, "A", n - 1)
    assert w["A"] == pytest.approx(0.01 * panel.close["A"][n - 1] / distance_a)
    assert w["A"] < w["B"]  # the volatile stock gets fewer dollars for the same risk
    capped = PortfolioRules(max_weight=0.10, max_sector=0.15, cash_reserve=0.05).size(
        ["A", "B", "C"], panel, n - 1
    )
    assert capped["B"] <= 0.10 + 1e-9 and capped["A"] + capped["B"] <= 0.15 + 1e-9
    assert sum(capped.values()) <= 0.95 + 1e-9
    assert PortfolioRules().scale(0.12) == 0.5 and PortfolioRules().scale(0.2) == 0.0


# ── simulator ────────────────────────────────────────────────────────────────
def test_orders_fill_at_the_next_open_with_costs_and_spy_is_the_benchmark():
    n = 330
    panel = make_panel({"SPY": path(n, 0.0005, wiggle=0.002), "FAST": path(n, 0.002, wiggle=0.004)})
    config = LabConfig(cost_bps=10, oos_days=40)
    result = simulate(panel, MomentumTrend(top=1, keep=1), PortfolioRules(), config)
    first = result.trades[0]
    entry_day = panel.dates.index(first.entry_date)
    assert panel.week_ends()[
        entry_day - 1
    ]  # decided on a week's last day, filled the next trading day
    assert first.entry == pytest.approx(panel.open["FAST"][entry_day] * 1.001)
    start = panel.dates.index(result.dates[0])
    bought = (
        config.cash * (1 - 0.001) / panel.open["SPY"][start]
    )  # SPY bought once at the first open, paying the cost
    assert result.benchmark[0] == pytest.approx(bought * panel.close["SPY"][start])
    assert result.equity[-1] > config.cash


def test_a_stop_fills_at_the_stop_or_at_the_open_when_the_price_gaps_through_it():
    n = 330
    fast = path(n, 0.002, wiggle=0.004)
    crash = fast[:300] + [fast[299] * 0.7 * (1.001**i) for i in range(30)]  # a 30% gap down
    panel = make_panel({"SPY": path(n, 0.0005, wiggle=0.002), "FAST": crash})
    panel.open["FAST"][300] = crash[300]  # the gap happens overnight: day 300 opens 30% lower
    result = simulate(
        panel, MomentumTrend(top=1, keep=1), PortfolioRules(), LabConfig(cost_bps=0, oos_days=20)
    )
    stopped = [t for t in result.trades if t.reason == "stop"]
    assert stopped
    gap_day = panel.dates[300]
    assert stopped[0].exit_date == gap_day
    assert stopped[0].exit == pytest.approx(
        panel.open["FAST"][300]
    )  # the open, below the stop: not the stop price
    assert stopped[0].r_multiple < -1  # a gap loses more than the planned risk


class AlwaysHold:
    name, warmup = "always-hold", 30

    def __init__(self, symbol: str):
        self.symbol = symbol

    def targets(self, panel, t, held):
        from loamz.solutions.trading.strategy import Targets

        return Targets([self.symbol])


def test_the_drawdown_breaker_goes_to_cash_and_restarts_later():
    n = 400
    fast = path(n, 0.002, wiggle=0.004)
    slide = fast[:300] + [
        fast[299] * (0.985**i) for i in range(1, 101)
    ]  # a long slide, no single gap
    panel = make_panel({"SPY": path(n, 0.0005, wiggle=0.002), "FAST": slide})
    # a stop too wide to trigger and a position big enough to matter, so the breaker must act
    rules = PortfolioRules(stop_atr=50.0, risk_per_position=1.0, max_weight=0.95, max_sector=1.0)
    result = simulate(
        panel,
        AlwaysHold("FAST"),  # never sells on its own, so only the breaker can take the book to cash
        rules,
        LabConfig(cost_bps=0, exit_below_sma=400, oos_days=20, restart_after=10),
    )
    events = [what for _, what in result.breaker]
    assert events and "to cash" in events[0] and "restarted" in events
    assert any(t.reason == "drawdown breaker" for t in result.trades)


def test_metrics_and_the_report_judge_out_of_sample_against_spy():
    days = [date(2021, 1, 1) + timedelta(days=i) for i in range(366)]  # CAGR is per calendar year
    doubling = [100 * 2 ** (i / 365) for i in range(366)]
    m = metrics(days, doubling)
    assert m.cagr == pytest.approx(1.0, rel=0.01) and m.max_drawdown == 0
    n = 330
    panel = make_panel({"SPY": path(n, 0.0005, wiggle=0.002), "FAST": path(n, 0.002, wiggle=0.004)})
    text = report(
        simulate(panel, MomentumTrend(top=1, keep=1), PortfolioRules(), LabConfig(oos_days=40))
    )
    assert "Out-of-sample" in text and "| CAGR |" in text and "Gate" in text


def test_idle_cash_parks_in_spy_scaled_with_the_book_and_never_counts_as_a_strategy_trade():
    rules = PortfolioRules(idle_in="SPY")
    assert rules.park({"A": 0.4}) == {"A": 0.4, "SPY": pytest.approx(0.55)}
    assert rules.park({"A": 0.4}, drawdown=0.12) == {"A": 0.4, "SPY": pytest.approx(0.075)}  # half size: 0.475 - 0.4
    assert rules.park({"A": 0.95}) == {"A": 0.95}  # nothing left to park
    n = 330
    panel = make_panel({"SPY": path(n, 0.0005, wiggle=0.002), "FAST": path(n, 0.002, wiggle=0.004)})
    parked = simulate(panel, MomentumTrend(top=1, keep=1), rules, LabConfig(oos_days=40))
    plain = simulate(panel, MomentumTrend(top=1, keep=1), PortfolioRules(), LabConfig(oos_days=40))
    assert sum(parked.exposure) / len(parked.exposure) > sum(plain.exposure) / len(plain.exposure)
    assert all(t.symbol != "SPY" for t in parked.trades)
