"""Market data: paged multi-symbol bars, a liquidity-ranked universe, and a warehouse that only appends what is missing."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from loamz.core.exceptions import NotFoundError
from loamz.solutions.markets import (
    BENCHMARKS,
    CANDIDATES,
    DailyBar,
    MarketData,
    MarketDataError,
    MarketWarehouse,
    last_settled_day,
    select_universe,
)


def bar(symbol: str, day: date, close: float = 100.0, volume: float = 1e6) -> DailyBar:
    return DailyBar(symbol, day, close, close, close, close, volume, close, 10)


def days(n: int, end: date = date(2026, 10, 23)) -> list[date]:
    return [end - timedelta(days=n - 1 - i) for i in range(n)]


# ── Alpaca client ────────────────────────────────────────────────────────────
def test_bars_follow_every_page_and_come_back_oldest_first():
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(dict(request.url.params))
        if "page_token" not in request.url.params:
            body = {
                "bars": {
                    "AAPL": [
                        {
                            "t": "2026-10-21T04:00:00Z",
                            "o": 1,
                            "h": 2,
                            "l": 1,
                            "c": 2,
                            "v": 10,
                            "vw": 1.5,
                            "n": 3,
                        }
                    ]
                },
                "next_page_token": "p2",
            }
        else:
            body = {
                "bars": {
                    "AAPL": [{"t": "2026-10-20T04:00:00Z", "o": 1, "h": 1, "l": 1, "c": 1, "v": 5}]
                },
                "next_page_token": None,
            }
        return httpx.Response(200, json=body)

    data = MarketData("k", "s", transport=httpx.MockTransport(handler))
    got = data.daily_bars(["aapl"], start=date(2026, 10, 1))
    assert [b.date for b in got["AAPL"]] == [date(2026, 10, 20), date(2026, 10, 21)]
    assert got["AAPL"][1].vwap == 1.5 and got["AAPL"][0].vwap is None
    assert (
        calls[0]["feed"] == "sip"
        and calls[0]["adjustment"] == "all"
        and calls[1]["page_token"] == "p2"
    )


def test_many_symbols_are_asked_for_in_batches():
    asked: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(len(request.url.params["symbols"].split(",")))
        return httpx.Response(200, json={"bars": {}})

    MarketData("k", "s", transport=httpx.MockTransport(handler)).daily_bars(
        [f"S{i}" for i in range(250)], start=date(2026, 1, 1)
    )
    assert asked == [100, 100, 50]


def test_an_error_names_the_status_and_not_the_keys():
    data = MarketData(
        "key-id",
        "secret",
        transport=httpx.MockTransport(lambda r: httpx.Response(403, text="forbidden")),
    )
    with pytest.raises(MarketDataError, match="403") as caught:
        data.daily_bars(["AAPL"], start=date(2026, 1, 1))
    assert "secret" not in str(caught.value)


def test_an_unfinished_session_is_never_the_last_settled_day():
    # 15:00 New York time on 2026-10-23 (19:00 UTC, daylight time): the bar is not final yet.
    assert last_settled_day(datetime(2026, 10, 23, 19, 0, tzinfo=UTC)) == date(2026, 10, 22)
    assert last_settled_day(datetime(2026, 10, 23, 21, 0, tzinfo=UTC)) == date(2026, 10, 23)


# ── universe ─────────────────────────────────────────────────────────────────
def test_the_universe_is_the_most_liquid_stocks_above_the_price_floor_plus_every_benchmark():
    stocks = sorted(CANDIDATES)[:6]
    bars = {s: [bar(s, d, volume=1e6 * (i + 1)) for d in days(60)] for i, s in enumerate(stocks)}
    bars[stocks[5]] = [
        bar(stocks[5], d, close=5.0, volume=1e9) for d in days(60)
    ]  # most traded but a penny stock
    bars["SPY"] = [bar("SPY", d) for d in days(60)]
    picked = select_universe(bars, date(2026, 10, 23), n=3)
    names = [m.symbol for m in picked if m.kind == "stock"]
    assert names == [stocks[4], stocks[3], stocks[2]]  # by dollar volume, the penny stock left out
    assert [m.rank for m in picked if m.kind == "stock"] == [1, 2, 3]
    assert [m.symbol for m in picked if m.kind == "etf"] == ["SPY"]
    assert all(m.sector == CANDIDATES[m.symbol] for m in picked if m.kind == "stock")


def test_the_universe_never_uses_bars_after_its_date():
    s = sorted(CANDIDATES)[0]
    bars = {
        s: [bar(s, d) for d in days(60, end=date(2026, 10, 1))]
        + [bar(s, date(2026, 10, 20), volume=1e12)]
    }
    early = select_universe(bars, date(2026, 10, 1), n=1)
    assert early[0].dollar_volume == pytest.approx(100.0 * 1e6)


def test_benchmarks_cover_the_market_and_every_sector():
    assert {"SPY", "QQQ"} <= set(BENCHMARKS)
    assert set(CANDIDATES.values()) <= set(BENCHMARKS.values())


# ── warehouse ────────────────────────────────────────────────────────────────
class FakeClient:
    """Stands in for loamz.data's DataClient: tables are lists of row dicts read back from the uploaded Parquet."""

    def __init__(self):
        self.tables: dict[str, list[dict]] = {}
        outer = self

        class Catalog:
            def table(self, name):
                if name not in outer.tables:
                    raise NotFoundError(404, {"detail": "no such table"})
                return {"name": name}

        class Sql:
            def rows(self, query):
                table = next(t for t in outer.tables if t in query)
                rows = outer.tables[table]
                if "max(date)" in query:
                    last: dict[str, date] = {}
                    for r in rows:
                        last[r["symbol"]] = max(last.get(r["symbol"], r["date"]), r["date"])
                    return type("R", (), {"rows": [[s, d] for s, d in last.items()]})()
                newest = max(r["as_of"] for r in rows)
                return type(
                    "R", (), {"rows": sorted([[r["symbol"]] for r in rows if r["as_of"] == newest])}
                )()

        self.catalog, self.sql = Catalog(), Sql()
        self.uploads: list[tuple[str, str]] = []

    def upload(self, table, files, *, mode, file_format):
        import pyarrow.parquet as pq

        assert file_format == "parquet"
        self.uploads.append((table, mode))
        rows = pq.read_table(files[0]).to_pylist()
        if mode == "create":
            assert table not in self.tables
            self.tables[table] = []
        self.tables[table].extend(rows)


class FakeData:
    def __init__(self, end: date):
        self.end, self.asked = end, []

    def daily_bars(self, symbols, start, end=None):
        self.asked.append((tuple(sorted(symbols)), start))
        out = {}
        for s in symbols:
            out[s] = [bar(s, d) for d in days(10, end=self.end) if d >= start]
        return out


def member(symbol: str):
    from loamz.solutions.markets import UniverseMember

    return UniverseMember(date(2026, 10, 23), symbol, "stock", "X", 1.0, 1)


def test_a_load_then_an_update_on_the_same_day_adds_nothing_twice():
    client, data = FakeClient(), FakeData(date(2026, 10, 23))
    warehouse = MarketWarehouse(client)
    written = warehouse.load(
        data, [member("AAA"), member("BBB")], years=1, today=date(2026, 10, 23)
    )
    assert written == 20
    assert warehouse.update(data, today=date(2026, 10, 23)) == 0
    assert ("main.markets.daily_bars", "create") in client.uploads


def test_an_update_appends_only_the_new_days_and_backfills_a_new_symbol():
    client = FakeClient()
    warehouse = MarketWarehouse(client)
    warehouse.load(FakeData(date(2026, 10, 21)), [member("AAA")], years=1, today=date(2026, 10, 21))
    warehouse.write_universe([member("AAA"), member("NEW")])
    data = FakeData(date(2026, 10, 23))
    added = warehouse.update(data, years=1, today=date(2026, 10, 23))
    stored = client.tables["main.markets.daily_bars"]
    assert len([r for r in stored if r["symbol"] == "AAA"]) == 12  # 10 loaded + the 2 new days
    assert len([r for r in stored if r["symbol"] == "NEW"]) == 10  # its whole history
    assert added == 12
    assert (("AAA",), date(2026, 10, 22)) in data.asked


def test_the_nightly_job_keeps_new_york_time_whatever_the_machine_clock():
    from zoneinfo import ZoneInfo

    from loamz.solutions.runner import Job

    job = Job("markets-update", lambda: None, daily_at=(18, 0), tz=ZoneInfo("America/New_York"))
    assert not job.due(datetime(2026, 10, 23, 21, 59, tzinfo=UTC))  # 17:59 in New York
    assert job.due(datetime(2026, 10, 23, 22, 1, tzinfo=UTC))  # 18:01
    job.last = datetime(2026, 10, 23, 22, 1, tzinfo=UTC)
    assert not job.due(
        datetime(2026, 10, 24, 3, 0, tzinfo=UTC)
    )  # later that New York evening: already ran
    assert job.due(datetime(2026, 10, 24, 22, 5, tzinfo=UTC))  # the next evening
