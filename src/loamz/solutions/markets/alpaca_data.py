"""Daily bars from Alpaca's market-data API, many symbols at a time.

Prices are split- and dividend-adjusted (`adjustment=all`), so a return computed from them is a total return. The default feed is
SIP (every US exchange): its daily history is available to free accounts, and its volume is the whole market's, where IEX sees only
a few percent. Responses are paged; `daily_bars` follows every page.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx

from loamz.solutions.trading.broker import DATA_URL, parse_time

NEW_YORK = ZoneInfo("America/New_York")
SETTLED = time(16, 30)  # the daily bar is final a little after the 16:00 close
BATCH = 100  # symbols per request
PAGE = 10_000  # bars per page, Alpaca's maximum


def last_settled_day(now: datetime) -> date:
    """The latest day whose daily bar is final: today in New York once the close has settled, otherwise yesterday. Storing an
    unfinished bar would freeze a mid-session price into history."""
    local = now.astimezone(NEW_YORK)
    return local.date() if local.time() >= SETTLED else local.date() - timedelta(days=1)


class MarketDataError(RuntimeError):
    pass


@dataclass(frozen=True)
class DailyBar:
    symbol: str
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    vwap: float | None = None
    trades: int | None = None

    @property
    def dollar_volume(self) -> float:
        return (self.vwap or self.close) * self.volume


class MarketData:
    def __init__(
        self,
        key_id: str,
        secret: str,
        *,
        data_url: str = DATA_URL,
        feed: str = "sip",
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.feed = feed
        self.http = httpx.Client(
            base_url=data_url.rstrip("/"),
            headers={"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret},
            timeout=60,
            transport=transport,
        )

    def daily_bars(
        self, symbols: Iterable[str], start: date, end: date | None = None
    ) -> dict[str, list[DailyBar]]:
        """Every daily bar from `start` to `end` (inclusive) for each symbol, oldest first. A symbol with no data is left out."""
        wanted = sorted({s.upper() for s in symbols})
        out: dict[str, list[DailyBar]] = {}
        for i in range(0, len(wanted), BATCH):
            for symbol, bars in self._batch(wanted[i : i + BATCH], start, end).items():
                out.setdefault(symbol, []).extend(bars)
        for bars in out.values():
            bars.sort(key=lambda b: b.date)
        return out

    def _batch(
        self, symbols: list[str], start: date, end: date | None
    ) -> dict[str, list[DailyBar]]:
        params: dict[str, object] = {
            "symbols": ",".join(symbols),
            "timeframe": "1Day",
            "start": start.isoformat(),
            "adjustment": "all",
            "feed": self.feed,
            "limit": PAGE,
            "sort": "asc",
        }
        if end is not None:
            params["end"] = end.isoformat()
        found: dict[str, list[DailyBar]] = {}
        while True:
            body = self._get("/v2/stocks/bars", params)
            for symbol, rows in (body.get("bars") or {}).items():
                found.setdefault(symbol, []).extend(
                    DailyBar(
                        symbol,
                        parse_time(r["t"]).date(),
                        float(r["o"]),
                        float(r["h"]),
                        float(r["l"]),
                        float(r["c"]),
                        float(r.get("v", 0)),
                        float(r["vw"]) if r.get("vw") is not None else None,
                        int(r["n"]) if r.get("n") is not None else None,
                    )
                    for r in rows
                )
            token = body.get("next_page_token")
            if not token:
                return found
            params["page_token"] = token

    def _get(self, path: str, params: dict[str, object]) -> dict:
        try:
            response = self.http.get(path, params=params)
        except httpx.TransportError as error:
            raise MarketDataError(f"Alpaca market data is unreachable: {error}") from None
        if response.status_code >= 400:
            raise MarketDataError(
                f"Alpaca market data answered {response.status_code}: {response.text[:200]}"
            )
        return response.json()
