"""Prices for many symbols on one shared calendar, and the indicators the strategy reads.

    panel = Panel.from_rows(rows, sectors)        # rows: (symbol, date, open, high, low, close, volume)
    panel.close["SPY"][t]                         # day t's close; t indexes panel.dates
    sma(panel.close["SPY"], t, 200)               # every indicator uses only data up to and including day t

A symbol missing on a day carries `None` there; indicators over a window that contains one return `None`, so a strategy treats
that symbol as not yet tradeable instead of guessing.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date

Series = list[float | None]


@dataclass
class Panel:
    dates: list[date]
    open: dict[str, Series] = field(default_factory=dict)
    high: dict[str, Series] = field(default_factory=dict)
    low: dict[str, Series] = field(default_factory=dict)
    close: dict[str, Series] = field(default_factory=dict)
    volume: dict[str, Series] = field(default_factory=dict)
    sectors: dict[str, str] = field(
        default_factory=dict
    )  # symbol -> sector; ETFs map to what they track
    etfs: set[str] = field(default_factory=set)
    cache: dict = field(
        default_factory=dict, repr=False, compare=False
    )  # computed values (factors by day), shared by every strategy run on this panel

    @property
    def stocks(self) -> list[str]:
        return sorted(s for s in self.close if s not in self.etfs)

    @classmethod
    def from_rows(
        cls,
        rows: Iterable[tuple[str, date, float, float, float, float, float]],
        sectors: Mapping[str, str] | None = None,
        etfs: Iterable[str] = (),
    ) -> Panel:
        by_symbol: dict[str, dict[date, tuple[float, float, float, float, float]]] = {}
        for symbol, day, o, h, low, c, v in rows:
            by_symbol.setdefault(symbol, {})[day] = (o, h, low, c, v)
        dates = sorted({d for days in by_symbol.values() for d in days})
        panel = cls(dates, sectors=dict(sectors or {}), etfs=set(etfs))
        for symbol, days in by_symbol.items():
            cols: list[Series] = [[], [], [], [], []]
            for d in dates:
                bar = days.get(d)
                for i in range(5):
                    cols[i].append(bar[i] if bar else None)
            (
                panel.open[symbol],
                panel.high[symbol],
                panel.low[symbol],
                panel.close[symbol],
                panel.volume[symbol],
            ) = cols
        return panel

    def week_ends(self) -> list[bool]:
        """Whether each day is the last trading day of its week (the weekly rebalance day)."""
        out = []
        for i, d in enumerate(self.dates):
            nxt = self.dates[i + 1] if i + 1 < len(self.dates) else None
            out.append(nxt is None or nxt.isocalendar()[:2] != d.isocalendar()[:2])
        return out


def window(series: Series, t: int, n: int) -> list[float] | None:
    """The n values ending at t, or None if there are not n of them or one is missing."""
    if t + 1 < n or t >= len(series):
        return None
    values = series[t + 1 - n : t + 1]
    return None if any(v is None for v in values) else values  # type: ignore[return-value]


def sma(series: Series, t: int, n: int) -> float | None:
    values = window(series, t, n)
    return sum(values) / n if values else None


def ret(series: Series, t: int, back: int, skip: int = 0) -> float | None:
    """Return from `back` days ago to `skip` days ago (skip=21 gives classic 12-1 momentum)."""
    end, start = t - skip, t - back
    if start < 0 or end < 0 or series[start] is None or series[end] is None or not series[start]:
        return None
    return series[end] / series[start] - 1  # type: ignore[operator]


def volatility(series: Series, t: int, n: int = 126) -> float | None:
    """Annualised standard deviation of daily returns over n days."""
    values = window(series, t, n + 1)
    if not values:
        return None
    rets = [values[i] / values[i - 1] - 1 for i in range(1, len(values)) if values[i - 1]]
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(252)


def rsi(series: Series, t: int, n: int = 14) -> float | None:
    """Relative strength index over n days, 0 to 100: low means the recent days were mostly down (oversold)."""
    values = window(series, t, n + 1)
    if not values:
        return None
    moves = [values[i] - values[i - 1] for i in range(1, len(values))]
    gain, loss = sum(m for m in moves if m > 0), -sum(m for m in moves if m < 0)
    if not loss:
        return 100.0 if gain else 50.0
    return 100 - 100 / (1 + gain / loss)


def atr(high: Series, low: Series, close: Series, t: int, n: int = 20) -> float | None:
    """Average true range over n days: the typical daily move, used to place stops and size positions."""
    if t < n:
        return None
    ranges = []
    for i in range(t - n + 1, t + 1):
        h, lo, prev = high[i], low[i], close[i - 1]
        if h is None or lo is None or prev is None:
            return None
        ranges.append(max(h - lo, abs(h - prev), abs(lo - prev)))
    return sum(ranges) / n
