"""Strategies decide what the book should hold; the portfolio rules decide how much; the guard decides whether an order may go.

    strategy = MomentumTrend()
    targets = strategy.targets(panel, t, held={"NVDA"})   # uses only data up to and including day t
    targets.hold          # the names to own after this rebalance, best first
    targets.ratings["MU"] # Rating("strong buy", score, "12-1 month +48%, 6 month +21%, ...")

`MomentumTrend` is trend-filtered momentum, the best-documented edge for a long-only book that trades weekly:
- regime: the market (SPY) above its 200-day average lets the book buy; below it, it buys nothing and keeps only names still
  in their own uptrend;
- eligible: a stock above its 200-day average whose 50-day average is above its 200-day one;
- score: the average of 12-1 month and 6 month return, divided by the stock's volatility (risk-adjusted momentum);
- the book: the top `top` eligible names; a name already held is kept while it ranks within `keep` (a buffer that cuts
  trading back and forth across the line).

Ratings, for every stock, every day, so a person (or an agent) can ask "any buy ratings?":
    strong sell  below its 200-day average and 12-1 month momentum negative
    sell         below its 100-day average, or held and fallen out of the top `keep`
    strong buy   eligible and in the top `top`
    buy          eligible and in the top `keep`
    hold         anything else
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from loamz.solutions.trading.factors import blend, explain
from loamz.solutions.trading.panel import Panel, ret, sma, volatility

LEVELS = ("strong sell", "sell", "hold", "buy", "strong buy")


@dataclass(frozen=True)
class Rating:
    level: str  # one of LEVELS
    rank: int | None  # 1 = best eligible name; None when not eligible
    score: float | None
    reason: str


@dataclass
class Targets:
    hold: list[str]  # what the book should own after this rebalance, best first
    ratings: dict[str, Rating] = field(default_factory=dict)
    regime: str = "risk on"  # "risk on" or "risk off" (market below its 200-day average)
    note: str = ""
    weights: dict[str, float] | None = (
        None  # set by a baseline: exact weights, held without risk sizing, stops or trend exits
    )


class Strategy(Protocol):
    name: str
    warmup: int  # days of history needed before the first decision

    def targets(self, panel: Panel, t: int, held: set[str]) -> Targets: ...


@dataclass
class MomentumTrend:
    top: int = 8
    keep: int = 15
    long_lookback: int = 252  # 12 months
    skip: int = 21  # the most recent month is left out of the long return (short-term reversal)
    short_lookback: int = 126  # 6 months
    vol_lookback: int = 126
    benchmark: str = "SPY"
    name: str = "momentum-trend"

    @property
    def warmup(self) -> int:
        return max(self.long_lookback, 200) + 1

    def targets(self, panel: Panel, t: int, held: set[str]) -> Targets:
        risk_on = market_risk_on(panel, t, self.benchmark)

        facts: dict[str, dict] = {}
        for symbol in panel.stocks:
            close = panel.close[symbol]
            c = close[t] if t < len(close) else None
            s50, s100, s200 = sma(close, t, 50), sma(close, t, 100), sma(close, t, 200)
            m12 = ret(close, t, self.long_lookback, self.skip)
            m6 = ret(close, t, self.short_lookback)
            vol = volatility(close, t, self.vol_lookback)
            if None in (c, s50, s100, s200, m12, m6, vol) or not vol:
                continue
            eligible = c > s200 and s50 > s200
            score = ((m12 + m6) / 2) / vol
            facts[symbol] = dict(
                c=c, s100=s100, s200=s200, m12=m12, m6=m6, vol=vol, eligible=eligible, score=score
            )

        ranked = sorted(
            (s for s, f in facts.items() if f["eligible"]), key=lambda s: -facts[s]["score"]
        )
        rank = {s: i + 1 for i, s in enumerate(ranked)}

        ratings: dict[str, Rating] = {}
        for symbol, f in facts.items():
            r = rank.get(symbol)
            if f["c"] < f["s200"] and f["m12"] < 0:
                level = "strong sell"
            elif f["c"] < f["s100"] or (symbol in held and (r is None or r > self.keep)):
                level = "sell"
            elif r is not None and r <= self.top:
                level = "strong buy"
            elif r is not None and r <= self.keep:
                level = "buy"
            else:
                level = "hold"
            ratings[symbol] = Rating(level, r, f["score"], describe(f, r))

        return book(ranked, rank, held, self.top, self.keep, risk_on, ratings)


def market_risk_on(panel: Panel, t: int, benchmark: str = "SPY") -> bool:
    """The market (its benchmark) closed above its 200-day average; unknown counts as yes."""
    series = panel.close.get(benchmark)
    now, trend = (series[t], sma(series, t, 200)) if series else (None, None)
    return now is None or trend is None or now > trend


def in_uptrend(panel: Panel, symbol: str, t: int) -> bool:
    """Above its 200-day average, with its 50-day average above the 200-day one."""
    close = panel.close[symbol]
    c, s50, s200 = close[t], sma(close, t, 50), sma(close, t, 200)
    return None not in (c, s50, s200) and c > s200 and s50 > s200


def book(
    ranked: list[str],
    rank: dict[str, int],
    held: set[str],
    top: int,
    keep: int,
    risk_on: bool,
    ratings: dict[str, Rating],
) -> Targets:
    """The top names, keeping held ones while they rank within `keep`; with the market down, only those kept."""
    kept = [s for s in ranked if s in held and rank[s] <= keep]
    if not risk_on:
        note = "market below its 200-day average: no new positions, only held names still in an uptrend"
        return Targets(kept, ratings, "risk off", note)
    new = [s for s in ranked if s not in kept][: max(0, top - len(kept))]
    return Targets(sorted(kept + new, key=lambda s: rank[s]), ratings, "risk on")


@dataclass
class FactorRank:
    """Rank the stocks in an uptrend by a blend of factors (`factors.BLEND`, or one factor alone) and hold the best, inside
    the same frame as `MomentumTrend` (market regime, uptrend eligibility, the keep buffer), so the lab compares only what
    each ranks by. Every rating explains itself factor by factor."""

    factors: dict[str, float]
    top: int = 8
    keep: int = 15
    benchmark: str = "SPY"
    name: str = ""
    warmup: int = 253

    def __post_init__(self) -> None:
        self.name = self.name or "factor rank: " + ", ".join(self.factors)

    def targets(self, panel: Panel, t: int, held: set[str]) -> Targets:
        scores = blend(panel, self.factors, t)
        ranked = sorted((s for s in scores if in_uptrend(panel, s, t)), key=lambda s: -scores[s][0])
        rank = {s: i + 1 for i, s in enumerate(ranked)}
        ratings: dict[str, Rating] = {}
        for symbol, (score, parts) in scores.items():
            r = rank.get(symbol)
            if r is None:
                level = "sell" if symbol in held else "hold"
            elif symbol in held and r > self.keep:
                level = "sell"
            elif r <= self.top:
                level = "strong buy"
            else:
                level = "buy" if r <= self.keep else "hold"
            where = f"rank {r}" if r else "not in an uptrend"
            ratings[symbol] = Rating(
                level, r, score, f"{explain(panel, symbol, t, parts)}; blend {score:+.2f}, {where}"
            )
        risk_on = market_risk_on(panel, t, self.benchmark)
        return book(ranked, rank, held, self.top, self.keep, risk_on, ratings)


@dataclass
class EqualWeight:
    """The naive baseline: every stock in the universe, equal weights, rebalanced weekly. Any strategy that picks stocks
    from this universe has to beat simply owning all of them."""

    name: str = "equal weight, every stock"
    warmup: int = 253
    invested: float = 0.98

    def targets(self, panel: Panel, t: int, held: set[str]) -> Targets:
        names = [s for s in panel.stocks if panel.close[s][t] is not None]
        return Targets(
            names, weights={s: self.invested / len(names) for s in names} if names else {}
        )


@dataclass
class MarketTrend:
    """The timing baseline: the market (SPY) while it is above its 200-day average, cash below it. Anything that adds stock
    picking to a trend filter has to beat the trend filter alone."""

    benchmark: str = "SPY"
    name: str = "SPY above its 200-day average, else cash"
    warmup: int = 253
    invested: float = 0.98

    def targets(self, panel: Panel, t: int, held: set[str]) -> Targets:
        if market_risk_on(panel, t, self.benchmark):
            return Targets([self.benchmark], weights={self.benchmark: self.invested})
        return Targets([], regime="risk off", weights={})


def describe(f: dict, rank: int | None) -> str:
    above = f["c"] / f["s200"] - 1
    parts = [
        f"12-1 month {f['m12']:+.0%}",
        f"6 month {f['m6']:+.0%}",
        f"{abs(above):.0%} {'above' if above >= 0 else 'below'} its 200-day average",
        f"volatility {f['vol']:.0%}",
    ]
    parts.append(f"rank {rank}" if rank else "not in an uptrend")
    return ", ".join(parts)
