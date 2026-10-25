"""Factors: published, well-understood reasons one stock might do better than another, each a number per stock per day.

    values(panel, "momentum", t)              # {"NVDA": 0.48, ...}: larger is more attractive; data up to day t only
    blend(panel, {"momentum": 1, "low_vol": 1}, t)   # {"NVDA": (0.9, {"momentum": 1.6, "low_vol": 0.2}), ...}
    print(study(panel, FACTORS))              # does each factor's ranking predict the next week's and month's returns?

Each factor is turned into a z-score across the stocks that day (how many standard deviations above the average stock, capped
at 3), so factors measured in different units can be added. A blend is the weighted average of those z-scores, and it keeps
them, so every rank can be explained factor by factor.

The study is the first test of any idea, before it trades: on days `horizon` apart (so no two samples overlap), rank the stocks
by the factor and correlate that ranking with the return each stock went on to make, from the next open. That correlation is the
information coefficient (IC): 0 is noise; 0.03 to 0.05, held steadily over many years, is a real edge for stocks.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from loamz.solutions.trading.panel import Panel, ret, rsi, sma, volatility, window


@dataclass(frozen=True)
class Factor:
    name: str
    label: str
    idea: str  # the reason it should work, in plain words
    value: Callable[[Panel, str, int], float | None]  # larger is more attractive
    show: Callable[[float], str]  # the value as a person reads it


def _momentum(p: Panel, s: str, t: int) -> float | None:
    return ret(p.close[s], t, 252, skip=21)


def _trend(p: Panel, s: str, t: int) -> float | None:
    c, line = p.close[s][t], sma(p.close[s], t, 200)
    return c / line - 1 if c and line else None


def _high_52w(p: Panel, s: str, t: int) -> float | None:
    w = window(p.close[s], t, 252)
    return w[-1] / max(w) - 1 if w else None


def _low_vol(p: Panel, s: str, t: int) -> float | None:
    v = volatility(p.close[s], t, 126)
    return -v if v else None


def _reversal(p: Panel, s: str, t: int) -> float | None:
    r = ret(p.close[s], t, 21)
    return -r if r is not None else None


def _pullback(p: Panel, s: str, t: int) -> float | None:
    r = rsi(p.close[s], t, 2)
    return -r if r is not None else None


def _breakout(p: Panel, s: str, t: int) -> float | None:
    highs, c = window(p.high[s], t - 1, 55), p.close[s][t]
    return c / max(highs) - 1 if highs and c else None


def _risk_adjusted_momentum(p: Panel, s: str, t: int) -> float | None:
    m12, m6, v = (
        ret(p.close[s], t, 252, skip=21),
        ret(p.close[s], t, 126),
        volatility(p.close[s], t, 126),
    )
    return ((m12 + m6) / 2) / v if m12 is not None and m6 is not None and v else None


FACTORS: dict[str, Factor] = {
    f.name: f
    for f in (
        Factor(
            "momentum",
            "12-1 month momentum",
            "last year's winners keep winning for months (Jegadeesh and Titman, 1993); the latest month is left out",
            _momentum,
            lambda x: f"12-1 month {x:+.0%}",
        ),
        Factor(
            "trend",
            "Distance above the 200-day average",
            "prices trend: a stock well above its long average tends to stay strong",
            _trend,
            lambda x: f"{abs(x):.0%} {'above' if x >= 0 else 'below'} its 200-day average",
        ),
        Factor(
            "high_52w",
            "Near its 52-week high",
            "investors anchor on the 52-week high and under-react to news that pushes through it (George and Hwang, 2004)",
            _high_52w,
            lambda x: "at its 52-week high" if x > -0.005 else f"{-x:.0%} below its 52-week high",
        ),
        Factor(
            "low_vol",
            "Low volatility",
            "calm stocks have earned as much as wild ones with less risk (the low-volatility anomaly)",
            _low_vol,
            lambda x: f"volatility {-x:.0%}",
        ),
        Factor(
            "reversal",
            "Last month's losers",
            "over a few weeks, prices overshoot and partly come back (short-term reversal)",
            _reversal,
            lambda x: f"last month {-x:+.0%}",
        ),
        Factor(
            "pullback",
            "Oversold: RSI(2) low",
            "a sharp two-day dip often bounces, a classic swing-trading pattern (Connors)",
            _pullback,
            lambda x: f"RSI(2) {-x:.0f}",
        ),
        Factor(
            "breakout",
            "Above its 55-day high",
            "a close through the recent range starts a new leg (the Turtles' Donchian breakout)",
            _breakout,
            lambda x: f"{abs(x):.0%} {'above' if x >= 0 else 'below'} its 55-day high",
        ),
        Factor(
            "risk_adj_momentum",
            "Momentum per unit of volatility",
            "momentum-trend's own score: the average of 12-1 and 6 month return, divided by volatility",
            _risk_adjusted_momentum,
            lambda x: f"{x:+.2f}",
        ),
    )
}

# Fixed before any result was seen: the three best-documented long-only factors, equally weighted.
BLEND = {"momentum": 1.0, "high_52w": 1.0, "low_vol": 1.0}


def values(panel: Panel, name: str, t: int) -> dict[str, float]:
    """The factor for every stock with enough history on day t."""
    key = ("factor", name, t)
    if key not in panel.cache:
        f, out = FACTORS[name], {}
        for symbol in panel.stocks:
            if panel.close[symbol][t] is not None and (v := f.value(panel, symbol, t)) is not None:
                out[symbol] = v
        panel.cache[key] = out
    return panel.cache[key]


def zscores(raw: Mapping[str, float], cap: float = 3.0) -> dict[str, float]:
    if len(raw) < 2:
        return dict.fromkeys(raw, 0.0)
    mean = sum(raw.values()) / len(raw)
    sd = math.sqrt(sum((v - mean) ** 2 for v in raw.values()) / (len(raw) - 1))
    if not sd:
        return dict.fromkeys(raw, 0.0)
    return {s: max(-cap, min(cap, (v - mean) / sd)) for s, v in raw.items()}


def blend(
    panel: Panel, weights: Mapping[str, float], t: int
) -> dict[str, tuple[float, dict[str, float]]]:
    """Weighted average z-score, with each factor's z-score, for the stocks every factor covers on day t."""
    z = {name: zscores(values(panel, name, t)) for name in weights}
    covered = set.intersection(*(set(v) for v in z.values())) if z else set()
    total = sum(abs(w) for w in weights.values()) or 1.0
    return {
        s: (
            sum(w * z[name][s] for name, w in weights.items()) / total,
            {n: z[n][s] for n in weights},
        )
        for s in sorted(covered)  # a fixed order, so ties rank the same on every run
    }


def explain(panel: Panel, symbol: str, t: int, parts: Mapping[str, float]) -> str:
    """Each factor's z-score and its value in plain words: "momentum +1.6σ (12-1 month +48%)"."""
    out = []
    for name, z in parts.items():
        raw = values(panel, name, t).get(symbol)
        shown = f" ({FACTORS[name].show(raw)})" if raw is not None else ""
        out.append(f"{name} {z:+.1f}σ{shown}")
    return ", ".join(out)


# ── the study ────────────────────────────────────────────────────────────────
def spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 5:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    n = len(xs)
    mx = my = (n - 1) / 2
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    sx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    sy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return cov / (sx * sy) if sx and sy else None


def _ranks(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):  # ties share the average of their ranks
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2
        i = j + 1
    return ranks


@dataclass(frozen=True)
class Evidence:
    ic: float  # the average information coefficient
    t_stat: float  # how many standard errors the average IC is from zero; above 2 is unlikely to be luck
    samples: int
    spread: float  # top fifth minus bottom fifth, return a year (simple, before costs)


def evidence(
    panel: Panel, name: str, horizon: int, start: int, end: int, warmup: int = 253
) -> Evidence | None:
    """IC and the top-minus-bottom spread of one factor, on non-overlapping days in [start, end)."""
    ics, spreads = [], []
    for t in range(max(start, warmup), min(end, len(panel.dates) - 1 - horizon), horizon):
        xs, ys = [], []
        for symbol, x in values(panel, name, t).items():
            a, b = panel.open[symbol][t + 1], panel.open[symbol][t + 1 + horizon]
            if a and b:
                xs.append(x)
                ys.append(b / a - 1)
        ic = spearman(xs, ys)
        if ic is None:
            continue
        ics.append(ic)
        # by the factor alone: ties keep the stocks' fixed order, never the return they went on to make
        ordered = [y for _, y in sorted(zip(xs, ys, strict=True), key=lambda pair: pair[0])]
        fifth = max(1, len(ordered) // 5)
        spreads.append(sum(ordered[-fifth:]) / fifth - sum(ordered[:fifth]) / fifth)
    if len(ics) < 3:
        return None
    mean = sum(ics) / len(ics)
    sd = math.sqrt(sum((x - mean) ** 2 for x in ics) / (len(ics) - 1))
    return Evidence(
        mean,
        mean / (sd / math.sqrt(len(ics))) if sd else 0.0,
        len(ics),
        sum(spreads) / len(spreads) * 252 / horizon,
    )


def study(panel: Panel, names: list[str] | None = None, oos_days: int = 378) -> str:
    """For each factor: does its ranking predict the next week and month, over the whole history and the held-out recent part?"""
    split = len(panel.dates) - oos_days
    lines = [
        "| Factor | IC, next week | IC, next month | t | IC, next month, held out | Top fifth minus bottom fifth, a year |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name in names or list(FACTORS):
        week = evidence(panel, name, 5, 0, len(panel.dates))
        month = evidence(panel, name, 21, 0, len(panel.dates))
        held = evidence(panel, name, 21, split, len(panel.dates))
        if not (week and month):
            continue
        lines.append(
            f"| {FACTORS[name].label} | {week.ic:+.3f} | {month.ic:+.3f} | {month.t_stat:+.1f} | "
            f"{f'{held.ic:+.3f}' if held else '-'} | {month.spread:+.1%} |"
        )
    return "\n".join(lines)
