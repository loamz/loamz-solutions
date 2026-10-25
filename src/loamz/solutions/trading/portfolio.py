"""How much of each name to own: sized by risk, capped by name and sector, scaled down when the book is losing.

    rules = PortfolioRules()
    weights = rules.size(["MU", "NVDA"], panel, t, equity=150_000, drawdown=0.04)
    weights["MU"]   # 0.11: 11% of equity

Sizing by risk: a position is sized so that being stopped out (entry minus `stop_atr` x ATR) costs `risk_per_position` of equity.
A volatile stock gets fewer dollars than a calm one for the same risk. Then the caps apply, in order: at most `max_positions`
names, `max_weight` per name, `max_sector` per sector, and `cash_reserve` left uninvested.

The drawdown breaker scales every position: down `halve_at` from the peak, positions are half size; down `flat_at`, the book
goes to cash and stays there until a person restarts it.
"""

from __future__ import annotations

from dataclasses import dataclass

from loamz.solutions.trading.panel import Panel, atr


@dataclass(frozen=True)
class PortfolioRules:
    risk_per_position: float = 0.01  # equity lost if a position hits its stop
    stop_atr: float = 2.5  # stop distance in ATRs
    atr_days: int = 20
    max_positions: int = 10
    max_weight: float = 0.15
    max_sector: float = 0.35
    cash_reserve: float = 0.05
    halve_at: float = 0.10  # drawdown from the peak that halves position sizes
    flat_at: float = 0.15  # drawdown from the peak that goes to cash; above 1, never
    idle_in: str | None = (
        None  # while the market trends up, what the picks leave uninvested sits here (e.g. "SPY")
    )

    def scale(self, drawdown: float) -> float:
        if drawdown >= self.flat_at:
            return 0.0
        return 0.5 if drawdown >= self.halve_at else 1.0

    def stop_distance(self, panel: Panel, symbol: str, t: int) -> float | None:
        a = atr(panel.high[symbol], panel.low[symbol], panel.close[symbol], t, self.atr_days)
        return self.stop_atr * a if a else None

    def size(
        self, names: list[str], panel: Panel, t: int, *, drawdown: float = 0.0
    ) -> dict[str, float]:
        """Target weights (fraction of equity) for `names`, best first; names that cannot be sized are left out."""
        factor = self.scale(drawdown)
        if factor == 0:
            return {}
        weights: dict[str, float] = {}
        for symbol in names[: self.max_positions]:
            price, distance = panel.close[symbol][t], self.stop_distance(panel, symbol, t)
            if not price or not distance:
                continue
            weights[symbol] = (
                min(self.risk_per_position * price / distance, self.max_weight) * factor
            )
        by_sector: dict[str, float] = {}
        for symbol, w in weights.items():
            by_sector[panel.sectors.get(symbol, "")] = (
                by_sector.get(panel.sectors.get(symbol, ""), 0.0) + w
            )
        for symbol in weights:
            total = by_sector[panel.sectors.get(symbol, "")]
            if total > self.max_sector:
                weights[symbol] *= self.max_sector / total
        invested = sum(weights.values())
        if invested > 1 - self.cash_reserve:
            weights = {s: w * (1 - self.cash_reserve) / invested for s, w in weights.items()}
        return weights

    def park(self, weights: dict[str, float], drawdown: float = 0.0) -> dict[str, float]:
        """`weights` plus the idle asset holding what they leave uninvested (scaled down with the book, never above it)."""
        if not self.idle_in or self.idle_in in weights:
            return weights
        room = (1 - self.cash_reserve) * self.scale(drawdown) - sum(weights.values())
        return {**weights, self.idle_in: room} if room > 0.01 else weights
