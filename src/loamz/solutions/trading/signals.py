"""Signal models: anything that turns recent prices into a `Signal`. Bring your own.

    class MyModel:
        def predict(self, symbol: str, bars: list[Bar]) -> Signal: ...

`HttpSignalModel` calls a model you serve yourself (a trained network, a notebook behind an API): it posts the bars and
reads back `{"side": "buy"|"sell"|"hold", "confidence": 0..1, "rationale": "..."}`. A model that fails answers `hold`, never
a guess. `MovingAverageCross` is a plain baseline for testing the machinery, not a recommendation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

import httpx

from loamz.solutions.trading.models import Bar, Signal


class SignalModel(Protocol):
    def predict(self, symbol: str, bars: list[Bar]) -> Signal: ...


def seen_at(bars: list[Bar]) -> datetime:
    return bars[-1].ts if bars else datetime.now(UTC)


def hold(symbol: str, why: str, bars: list[Bar] | None = None) -> Signal:
    return Signal(symbol, "hold", 0.0, why, seen_at(bars or []), bars[-1].close if bars else None)


class MovingAverageCross:
    """Buy when the fast average crosses above the slow one, sell when it crosses below. A crossover starts at 70% confidence and grows with the gap."""

    def __init__(self, fast: int = 10, slow: int = 30) -> None:
        if fast >= slow:
            raise ValueError("fast must be shorter than slow")
        self.fast, self.slow = fast, slow

    def predict(self, symbol: str, bars: list[Bar]) -> Signal:
        if len(bars) < self.slow + 1:
            return hold(symbol, f"needs {self.slow + 1} bars, has {len(bars)}", bars)
        closes = [b.close for b in bars]

        def mean(values: list[float]) -> float:
            return sum(values) / len(values)

        fast_now, slow_now = mean(closes[-self.fast :]), mean(closes[-self.slow :])
        fast_before, slow_before = (
            mean(closes[-self.fast - 1 : -1]),
            mean(closes[-self.slow - 1 : -1]),
        )
        gap = (fast_now - slow_now) / slow_now
        confidence = min(1.0, 0.7 + abs(gap) * 10)
        last = bars[-1]
        if fast_before <= slow_before and fast_now > slow_now:
            return Signal(
                symbol,
                "buy",
                confidence,
                f"the {self.fast}-bar average crossed above the {self.slow}-bar ({gap:+.2%})",
                last.ts,
                last.close,
            )
        if fast_before >= slow_before and fast_now < slow_now:
            return Signal(
                symbol,
                "sell",
                confidence,
                f"the {self.fast}-bar average crossed below the {self.slow}-bar ({gap:+.2%})",
                last.ts,
                last.close,
            )
        return Signal(symbol, "hold", 0.0, f"no crossover ({gap:+.2%})", last.ts, last.close)


class HttpSignalModel:
    def __init__(
        self,
        url: str,
        token: str = "",
        *,
        timeout: float = 20,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.url = url
        self.http = httpx.Client(headers=headers, timeout=timeout, transport=transport)

    def predict(self, symbol: str, bars: list[Bar]) -> Signal:
        body = {
            "symbol": symbol,
            "bars": [
                {
                    "t": b.ts.isoformat(),
                    "o": b.open,
                    "h": b.high,
                    "l": b.low,
                    "c": b.close,
                    "v": b.volume,
                }
                for b in bars
            ],
        }
        try:
            response = self.http.post(self.url, json=body)
            response.raise_for_status()
            answer = response.json()
            side = answer["side"]
            confidence = float(answer.get("confidence", 0))
            if side not in ("buy", "sell", "hold") or not 0 <= confidence <= 1:
                raise ValueError(f"unusable answer: side={side!r}, confidence={confidence}")
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
            return hold(symbol, f"the model failed ({type(error).__name__}); staying out", bars)
        price = bars[-1].close if bars else None
        return Signal(
            symbol, side, confidence, str(answer.get("rationale", ""))[:500], seen_at(bars), price
        )
