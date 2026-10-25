"""Factors and baselines: every factor uses only the past, the study measures prediction without peeking, a blend explains
itself factor by factor, and the naive baselines hold their weights without stops or trend exits."""

from __future__ import annotations

import pytest
from test_lab import make_panel, path

from loamz.solutions.trading.factors import FACTORS, blend, evidence, spearman, values
from loamz.solutions.trading.lab import LabConfig, simulate
from loamz.solutions.trading.panel import rsi
from loamz.solutions.trading.portfolio import PortfolioRules
from loamz.solutions.trading.strategy import EqualWeight, FactorRank, MarketTrend

N = 400
GROWTH = {"A": 0.0030, "B": 0.0024, "C": 0.0018, "D": 0.0012, "E": 0.0006, "F": 0.0001}


def growth_panel(wiggle: float = 0.004):
    """Six stocks that each grow at their own steady rate: the faster grower is ahead in the past and in the future."""
    return make_panel(
        {"SPY": path(N, 0.0005, wiggle=0.002)}
        | {s: path(N, g, wiggle=wiggle) for s, g in GROWTH.items()}
    )


def test_rsi_reads_the_last_days_moves():
    assert rsi([1.0, 2.0, 3.0], 2, 2) == 100.0
    assert rsi([3.0, 2.0, 1.0], 2, 2) == 0.0
    assert rsi([1.0, 2.0, 1.5], 2, 2) == pytest.approx(100 - 100 / (1 + 1 / 0.5))


@pytest.mark.parametrize("name", list(FACTORS))
def test_a_factor_on_day_t_ignores_every_later_price(name):
    panel, t = growth_panel(), 300
    before = values(panel, name, t)
    for series in (panel.open, panel.high, panel.low, panel.close):
        for symbol in series:
            series[symbol][t + 1 :] = [v * 3 for v in series[symbol][t + 1 :]]
    panel.cache.clear()
    assert values(panel, name, t) == before and before


def test_the_study_finds_a_factor_that_predicts_and_one_that_predicts_backwards():
    panel = growth_panel()
    momentum = evidence(panel, "momentum", 21, 0, N)
    reversal = evidence(panel, "reversal", 21, 0, N)
    assert momentum.ic == pytest.approx(1.0) and momentum.spread > 0
    assert reversal.ic == pytest.approx(-1.0) and reversal.spread < 0
    assert spearman([1, 2, 2, 3, 4], [1, 2, 2, 3, 4]) == pytest.approx(1.0)


def test_tied_factor_values_are_never_ordered_by_the_return_they_went_on_to_make():
    # A to E rise every day, so their RSI(2) is 100 and the pullback factor ties them; F alone wiggles and ranks top.
    # The bottom fifth must be the first tied name in the fixed order (A, the best future return), not the tie's worst
    panel = make_panel(
        {"SPY": path(N, 0.0005, wiggle=0.002)}
        | {s: path(N, g, wiggle=0.004 if s == "F" else 0.0) for s, g in GROWTH.items()}
    )
    tied = evidence(panel, "pullback", 21, 0, N)
    days = range(253, N - 1 - 21, 21)
    expected = [
        panel.open["F"][t + 22] / panel.open["F"][t + 1]
        - panel.open["A"][t + 22] / panel.open["A"][t + 1]
        for t in days
    ]
    assert tied.spread == pytest.approx(sum(expected) / len(expected) * 252 / 21)


def test_a_blend_ranks_by_its_factors_and_explains_each():
    panel, t = growth_panel(), N - 1
    scores = blend(panel, {"momentum": 1.0, "trend": 1.0}, t)
    assert max(scores, key=lambda s: scores[s][0]) == "A"
    targets = FactorRank({"momentum": 1.0, "trend": 1.0}, top=2, keep=3).targets(
        panel, t, held=set()
    )
    assert targets.hold == ["A", "B"]
    assert targets.ratings["A"].level == "strong buy"
    assert (
        "momentum +" in targets.ratings["A"].reason
        and "200-day average" in targets.ratings["A"].reason
    )


def test_equal_weight_owns_every_stock_without_stops_or_trend_exits():
    falling = path(N, -0.002, wiggle=0.004)
    panel = make_panel(
        {"SPY": path(N, 0.0005, wiggle=0.002), "UP": path(N, 0.002, wiggle=0.004), "DOWN": falling}
    )
    result = simulate(
        panel,
        EqualWeight(),
        PortfolioRules(halve_at=1.01, flat_at=1.01),
        LabConfig(cost_bps=0, oos_days=20),
    )
    assert {t.symbol for t in result.trades} == {"UP", "DOWN"}
    assert all(
        t.reason == "open at the end" for t in result.trades
    )  # never stopped, never sold for its trend
    assert min(result.exposure[5:]) > 0.9


def test_the_market_timing_baseline_is_in_cash_while_the_market_is_below_its_trend():
    spy = path(300, 0.001, wiggle=0.002) + path(
        100, -0.004, start=path(300, 0.001)[-1], wiggle=0.002
    )
    panel = make_panel({"SPY": spy, "X": path(N, 0.001, wiggle=0.004)})
    result = simulate(
        panel,
        MarketTrend(),
        PortfolioRules(halve_at=1.01, flat_at=1.01),
        LabConfig(cost_bps=0, oos_days=20),
    )
    assert result.exposure[10] > 0.9 and result.exposure[-1] == 0.0
    assert [t.reason for t in result.trades] == ["left the book"]
