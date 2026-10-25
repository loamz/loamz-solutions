"""The ladder every strategy must climb before it trades: if a rung below does as well, the simpler thing wins.

    print(ladder(panel))

1. Naive baselines: SPY bought once, every stock at equal weight, and SPY only while it is above its 200-day average.
2. Evidence: does each factor's ranking predict returns at all (`factors.study`)?
3. Each factor alone, traded in the same frame as momentum-trend (regime, uptrend, top 8, risk sizing, stops).
4. The blend of the three best-documented factors, fixed before any result was seen, with and without idle cash in SPY.
5. Momentum-trend, the strategy built first, for reference.
"""

from __future__ import annotations

from dataclasses import replace

from loamz.solutions.trading.factors import BLEND, FACTORS, study
from loamz.solutions.trading.lab import DEFAULTS, LabConfig, compare
from loamz.solutions.trading.panel import Panel
from loamz.solutions.trading.portfolio import PortfolioRules
from loamz.solutions.trading.strategy import EqualWeight, FactorRank, MarketTrend, MomentumTrend

UNBROKEN = PortfolioRules(halve_at=1.01, flat_at=1.01)  # a baseline never trips the breaker


def variants() -> dict[str, tuple]:
    rules = PortfolioRules()
    out: dict[str, tuple] = {
        "Equal weight, every stock": (EqualWeight(), UNBROKEN),
        "SPY above its 200-day average, else cash": (MarketTrend(), UNBROKEN),
    }
    for name, factor in FACTORS.items():
        if name != "risk_adj_momentum":  # that is momentum-trend's score; it has its own row
            out[f"{factor.label}, top 8"] = (FactorRank({name: 1.0}), rules)
    label = ", ".join(FACTORS[n].label.lower() for n in BLEND)
    out[f"Blend: {label}"] = (FactorRank(dict(BLEND)), rules)
    out["Blend, idle cash in SPY"] = (FactorRank(dict(BLEND)), replace(rules, idle_in="SPY"))
    out["Momentum-trend"] = (MomentumTrend(), rules)
    out["Momentum-trend, idle cash in SPY"] = (MomentumTrend(), replace(rules, idle_in="SPY"))
    return out


def ladder(panel: Panel, note: str = "", config: LabConfig = DEFAULTS) -> str:
    table, results = compare(panel, variants(), config)
    any_result = next(iter(results.values()))
    t = len(panel.dates) - 1
    today = FactorRank(dict(BLEND)).targets(panel, t, held=set())
    ranked = sorted((r for r in today.ratings.items() if r[1].rank), key=lambda r: r[1].rank)[:10]
    lines = [
        f"## The baseline ladder, {any_result.dates[0]} to {any_result.dates[-1]}",
        "",
        note,
        "",
        "### 1. Does each factor predict returns?",
        "",
        "Rank the stocks by the factor, then see how they did from the next open. The information coefficient (IC) is the "
        "correlation of the two rankings: 0 is noise, +0.03 to +0.05 held for years is a real edge. A t above 2 is unlikely "
        "to be luck. Held out: the last 18 months, which no choice here was tuned on.",
        "",
        study(panel, oos_days=config.oos_days),
        "",
        "### 2. Traded, after costs, against the rungs below",
        "",
        "Each factor trades in momentum-trend's frame (market regime, uptrend only, top 8, risk sizing, stops, breaker), "
        "so the rows differ only in what they rank by. The baselines hold their weights with no stops or breaker. "
        "Gate: out-of-sample Sharpe above SPY's, drawdown within limits. Equal weight is the most flattered row: the "
        "universe was chosen from today's largest stocks, so it owns the decade's winners from the first day.",
        "",
        table,
        "",
        f"### 3. Today's blend ({panel.dates[t]}), market {today.regime}",
        "",
        "| Rank | Stock | Rating | Why |",
        "|---:|---|---|---|",
        *(f"| {r.rank} | {s} | {r.level} | {r.reason} |" for s, r in ranked),
    ]
    return "\n".join(lines)
