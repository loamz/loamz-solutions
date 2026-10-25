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
        spy = panel.close.get(self.benchmark)
        spy_now, spy_trend = (spy[t], sma(spy, t, 200)) if spy else (None, None)
        risk_on = spy_now is None or spy_trend is None or spy_now > spy_trend

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

        kept = [s for s in ranked if s in held and rank[s] <= self.keep]
        if risk_on:
            new = [s for s in ranked if s not in kept][: max(0, self.top - len(kept))]
            hold = sorted(kept + new, key=lambda s: rank[s])
            note = ""
        else:
            hold = kept
            note = "market below its 200-day average: no new positions, only held names still in an uptrend"
        return Targets(hold, ratings, "risk on" if risk_on else "risk off", note)


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
