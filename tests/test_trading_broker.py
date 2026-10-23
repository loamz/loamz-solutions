import json

import httpx
import pytest

from loamz.solutions.trading.broker import LIVE_URL, AlpacaBroker, BrokerError, SimBroker


def alpaca(handler, **options):
    return AlpacaBroker("id", "secret", transport=httpx.MockTransport(handler), **options)


def test_a_live_url_is_refused_unless_explicitly_allowed():
    with pytest.raises(BrokerError, match="allow_live"):
        AlpacaBroker("i", "s", base_url=LIVE_URL)
    assert AlpacaBroker("i", "s", base_url=LIVE_URL, allow_live=True).mode == "live"
    assert AlpacaBroker("i", "s").mode == "paper"


def test_account_positions_clock_and_bars_are_parsed():
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.headers["APCA-API-KEY-ID"] == "id"
        path = request.url.path
        if path == "/v2/account":
            return httpx.Response(
                200,
                json={
                    "equity": "101000.5",
                    "cash": "50000",
                    "buying_power": "100000",
                    "last_equity": "100000",
                },
            )
        if path == "/v2/positions":
            return httpx.Response(
                200,
                json=[
                    {
                        "symbol": "AAPL",
                        "qty": "10",
                        "avg_entry_price": "180",
                        "current_price": "190",
                    }
                ],
            )
        if path == "/v2/clock":
            return httpx.Response(200, json={"is_open": True})
        if path == "/v2/stocks/AAPL/bars":
            return httpx.Response(
                200,
                json={
                    "bars": [
                        {"t": "2026-10-22T04:00:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 100}
                    ]
                },
            )
        if path == "/v2/stocks/AAPL/trades/latest":
            return httpx.Response(200, json={"trade": {"p": 191.25}})
        return httpx.Response(404)

    broker = alpaca(handle)
    account = broker.account()
    assert (
        account.equity == 101000.5
        and account.day_pnl == 1000.5
        and round(account.day_pnl_pct, 4) == 0.01
    )
    assert broker.positions()[0].market_value == 1900
    assert broker.is_open() and broker.price("AAPL") == 191.25
    bar = broker.bars("AAPL")[0]
    assert bar.close == 1.5 and bar.ts.tzinfo is not None


def test_an_order_is_a_day_market_order_with_a_client_id_and_a_duplicate_does_not_trade_twice():
    sent = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            sent.append(json.loads(request.content))
            if len(sent) > 1:
                return httpx.Response(422, text='{"message":"client_order_id must be unique"}')
            return httpx.Response(
                200,
                json={
                    "id": "o1",
                    "client_order_id": "c1",
                    "symbol": "AAPL",
                    "side": "buy",
                    "qty": "2",
                    "status": "accepted",
                },
            )
        assert request.url.path == "/v2/orders:by_client_order_id"
        return httpx.Response(
            200,
            json={
                "id": "o1",
                "client_order_id": "c1",
                "symbol": "AAPL",
                "side": "buy",
                "qty": "2",
                "status": "filled",
                "filled_qty": "2",
                "filled_avg_price": "190.1",
            },
        )

    broker = alpaca(handle)
    first = broker.submit("AAPL", "buy", 2, "c1")
    assert sent[0] == {
        "symbol": "AAPL",
        "qty": "2",
        "side": "buy",
        "type": "market",
        "time_in_force": "day",
        "client_order_id": "c1",
    }
    again = broker.submit("AAPL", "buy", 2, "c1")
    assert (
        first.id == again.id == "o1"
        and again.status == "filled"
        and again.filled_avg_price == 190.1
    )


def test_broker_errors_are_typed():
    broker = alpaca(lambda r: httpx.Response(403, text="forbidden"))
    with pytest.raises(BrokerError, match="403"):
        broker.account()
    with pytest.raises(BrokerError, match="refused"):
        broker.submit("AAPL", "buy", 1, "x")


def test_the_simulator_fills_updates_cash_and_is_idempotent():
    sim = SimBroker(cash=10_000, slippage_bps=10)
    sim.prices["AAPL"] = 100.0
    order = sim.submit("AAPL", "buy", 10, "a")
    assert order.status == "filled" and order.filled_avg_price == pytest.approx(100.1)
    assert sim.submit("AAPL", "buy", 10, "a") is order  # the same client id never trades twice
    assert sim.cash == pytest.approx(10_000 - 1001) and sim.positions()[0].qty == 10
    sim.prices["AAPL"] = 110.0
    assert sim.equity() == pytest.approx(10_000 - 1001 + 1100)
    sim.submit("AAPL", "sell", 10, "b")
    assert sim.positions() == [] or sim.positions()[0].qty == 0
    with pytest.raises(BrokerError):
        sim.submit("AAPL", "sell", 1, "c")
    with pytest.raises(BrokerError):
        sim.submit("AAPL", "buy", 10_000, "d")


def test_the_endpoint_as_the_alpaca_dashboard_shows_it_is_still_paper():
    assert AlpacaBroker("i", "s", base_url="https://paper-api.alpaca.markets/v2").mode == "paper"
    assert AlpacaBroker("i", "s", base_url="https://paper-api.alpaca.markets/v2/").mode == "paper"
    with pytest.raises(BrokerError):
        AlpacaBroker("i", "s", base_url="https://api.alpaca.markets/v2")


def test_bars_are_the_most_recent_ones_oldest_first():
    seen = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        newest_first = [
            {"t": f"2026-10-{day:02d}T04:00:00Z", "o": day, "h": day, "l": day, "c": day, "v": 1}
            for day in (23, 22, 21)
        ]
        return httpx.Response(200, json={"bars": newest_first})

    bars = alpaca(handle).bars("AAPL", 3)
    assert [b.close for b in bars] == [21, 22, 23]  # oldest first, ending at the newest
    params = seen[0]
    assert (
        params["sort"] == "desc" and params["limit"] == "3"
    )  # the newest three, not the oldest three in the window
    from datetime import UTC, datetime

    start = datetime.fromisoformat(params["start"]).replace(tzinfo=UTC)
    assert (
        0 < (datetime.now(UTC) - start).days < 30
    )  # a window sized to the request, not a fixed two years
