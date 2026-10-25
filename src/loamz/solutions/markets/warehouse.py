"""The market tables on Loamz Data, written through the governed client like any other data.

    main.markets.daily_bars    symbol, date, open, high, low, close, volume, vwap, trades   (adjusted daily bars)
    main.markets.universe      as_of, symbol, kind, sector, dollar_volume, rank             (one snapshot per selection)

`load` writes the history once; `update` appends only what is missing: for each symbol, the days after the last one stored, and the
whole history for a symbol that is new to the universe. Running it twice in an evening adds nothing the second time.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterable
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Protocol

from loamz.core.exceptions import NotFoundError
from loamz.solutions.markets.alpaca_data import DailyBar
from loamz.solutions.markets.universe import UniverseMember

SCHEMA = "main.markets"


class Bars(Protocol):
    def daily_bars(
        self, symbols: Iterable[str], start: date, end: date | None = None
    ) -> dict[str, list[DailyBar]]: ...


class MarketWarehouse:
    def __init__(self, client: Any, schema: str = SCHEMA) -> None:
        self.client, self.schema = client, schema

    def name(self, table: str) -> str:
        return f"{self.schema}.{table}"

    def exists(self, table: str) -> bool:
        try:
            self.client.catalog.table(self.name(table))
        except NotFoundError:
            return False
        return True

    # ── reads ────────────────────────────────────────────────────────────────
    def last_dates(self) -> dict[str, date]:
        """The last stored day per symbol; empty before the first load."""
        if not self.exists("daily_bars"):
            return {}
        result = self.client.sql.rows(
            f"SELECT symbol, max(date) AS last FROM {self.name('daily_bars')} GROUP BY symbol"
        )
        return {row[0]: as_date(row[1]) for row in result.rows}

    def universe(self) -> list[str]:
        """The symbols of the latest universe snapshot."""
        if not self.exists("universe"):
            return []
        table = self.name("universe")
        result = self.client.sql.rows(
            f"SELECT DISTINCT symbol FROM {table} WHERE as_of = (SELECT max(as_of) FROM {table}) ORDER BY symbol"
        )
        return [row[0] for row in result.rows]

    # ── writes ───────────────────────────────────────────────────────────────
    def write_bars(self, bars: Iterable[DailyBar]) -> int:
        rows = list(bars)
        if not rows:
            return 0
        columns = {
            "symbol": [b.symbol for b in rows],
            "date": [b.date for b in rows],
            "open": [b.open for b in rows],
            "high": [b.high for b in rows],
            "low": [b.low for b in rows],
            "close": [b.close for b in rows],
            "volume": [b.volume for b in rows],
            "vwap": [b.vwap for b in rows],
            "trades": [b.trades for b in rows],
        }
        self._append("daily_bars", columns)
        return len(rows)

    def write_universe(self, members: Iterable[UniverseMember]) -> int:
        rows = list(members)
        if not rows:
            return 0
        columns = {
            "as_of": [m.as_of for m in rows],
            "symbol": [m.symbol for m in rows],
            "kind": [m.kind for m in rows],
            "sector": [m.sector for m in rows],
            "dollar_volume": [m.dollar_volume for m in rows],
            "rank": [m.rank for m in rows],
        }
        self._append("universe", columns)
        return len(rows)

    def _append(self, table: str, columns: dict[str, list]) -> None:
        import pyarrow as pa
        import pyarrow.parquet as pq

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / f"{table}.parquet"
            pq.write_table(pa.table(columns), path)
            mode = "append" if self.exists(table) else "create"
            self.client.upload(self.name(table), [path], mode=mode, file_format="parquet")

    # ── jobs ─────────────────────────────────────────────────────────────────
    def load(
        self, data: Bars, universe: list[UniverseMember], *, years: int = 5, today: date
    ) -> int:
        """Store the universe snapshot and every symbol's history not yet stored."""
        self.write_universe(universe)
        return self._fill(
            data, [m.symbol for m in universe], today - timedelta(days=365 * years + 7), today
        )

    def update(self, data: Bars, *, years: int = 5, today: date) -> int:
        """Append the days each universe symbol is missing (all of them, for a symbol new to the universe)."""
        return self._fill(data, self.universe(), today - timedelta(days=365 * years + 7), today)

    def _fill(self, data: Bars, symbols: list[str], history_start: date, today: date) -> int:
        last = self.last_dates()
        by_start: dict[date, list[str]] = {}
        for symbol in sorted(set(symbols)):  # a universe saved twice in a day lists a symbol twice
            start = last[symbol] + timedelta(days=1) if symbol in last else history_start
            if start <= today:
                by_start.setdefault(start, []).append(symbol)
        written = 0
        for start, group in sorted(by_start.items()):
            fetched = data.daily_bars(group, start=start, end=today)
            written += self.write_bars(
                b for bars in fetched.values() for b in bars if b.date >= start
            )
        return written


def as_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])
