"""Brokers: where orders go and where account and price data come from.

`AlpacaBroker` talks to Alpaca's REST API. It is built for the paper account: a live account needs a different base URL
*and* `allow_live=True`, and the engine additionally asks a human to approve every live order. `SimBroker` fills orders
instantly at a price you set: it powers the backtester and the tests.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

import httpx

from loamz.solutions.trading.models import Account, Bar, Order, Position, Side

PAPER_URL = "https://paper-api.alpaca.markets"
LIVE_URL = "https://api.alpaca.markets"
DATA_URL = "https://data.alpaca.markets"


class BrokerError(RuntimeError):
    pass


class Broker(Protocol):
    mode: str  # "paper", "live" or "sim"

    def account(self) -> Account: ...
    def positions(self) -> list[Position]: ...
    def bars(self, symbol: str, limit: int = 60) -> list[Bar]: ...
    def price(self, symbol: str) -> float: ...
    def is_open(self) -> bool: ...
    def submit(self, symbol: str, side: Side, qty: float, client_id: str) -> Order: ...


def normalize(url: str) -> str:
    """Alpaca's dashboard shows the endpoint as https://paper-api.alpaca.markets/v2; the API paths here already include /v2."""
    url = url.strip().rstrip("/")
    return url.removesuffix("/v2").rstrip("/")


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class AlpacaBroker:
    def __init__(
        self,
        key_id: str,
        secret: str,
        *,
        base_url: str = PAPER_URL,
        data_url: str = DATA_URL,
        allow_live: bool = False,
        feed: str = "iex",
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        base_url = normalize(base_url)
        live = base_url != PAPER_URL
        if live and not allow_live:
            raise BrokerError("Refusing a non-paper Alpaca URL without allow_live=True")
        self.mode = "live" if live else "paper"
        self.feed = feed
        headers = {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret}
        self.http = httpx.Client(
            base_url=base_url.rstrip("/"), headers=headers, timeout=20, transport=transport
        )
        self.data = httpx.Client(
            base_url=data_url.rstrip("/"), headers=headers, timeout=20, transport=transport
        )

    def get(self, client: httpx.Client, path: str, **params):
        try:
            response = client.get(path, params={k: v for k, v in params.items() if v is not None})
        except httpx.TransportError as error:
            raise BrokerError(f"The broker is unreachable: {error}") from None
        if response.status_code >= 400:
            raise BrokerError(f"The broker answered {response.status_code}: {response.text[:200]}")
        return response.json()

    def account(self) -> Account:
        a = self.get(self.http, "/v2/account")
        return Account(
            float(a["equity"]), float(a["cash"]), float(a["buying_power"]), float(a["last_equity"])
        )

    def positions(self) -> list[Position]:
        return [
            Position(
                p["symbol"], float(p["qty"]), float(p["avg_entry_price"]), float(p["current_price"])
            )
            for p in self.get(self.http, "/v2/positions")
        ]

    def bars(self, symbol: str, limit: int = 60) -> list[Bar]:
        body = self.get(
            self.data,
            f"/v2/stocks/{symbol}/bars",
            timeframe="1Day",
            limit=limit,
            adjustment="all",
            feed=self.feed,
            sort="asc",
            start=(datetime.now(UTC).replace(year=datetime.now(UTC).year - 2)).strftime("%Y-%m-%d"),
        )
        rows = (body.get("bars") or [])[-limit:]
        return [
            Bar(parse_time(b["t"]), b["o"], b["h"], b["l"], b["c"], b.get("v", 0)) for b in rows
        ]

    def price(self, symbol: str) -> float:
        body = self.get(self.data, f"/v2/stocks/{symbol}/trades/latest", feed=self.feed)
        return float(body["trade"]["p"])

    def is_open(self) -> bool:
        return bool(self.get(self.http, "/v2/clock")["is_open"])

    def submit(self, symbol: str, side: Side, qty: float, client_id: str) -> Order:
        """A market order, day only. `client_id` makes it idempotent: the same id never trades twice."""
        body = {
            "symbol": symbol,
            "qty": str(qty),
            "side": side,
            "type": "market",
            "time_in_force": "day",
            "client_order_id": client_id,
        }
        try:
            response = self.http.post("/v2/orders", json=body)
        except httpx.TransportError as error:
            raise BrokerError(f"The broker is unreachable: {error}") from None
        if response.status_code == 422 and "client_order_id" in response.text:
            response = self.http.get(
                "/v2/orders:by_client_order_id", params={"client_order_id": client_id}
            )
        if response.status_code >= 400:
            raise BrokerError(
                f"The broker refused the order ({response.status_code}): {response.text[:200]}"
            )
        o = response.json()
        return Order(
            o["id"],
            o.get("client_order_id", client_id),
            o["symbol"],
            o["side"],
            float(o["qty"]),
            o["status"],
            float(o.get("filled_qty") or 0),
            float(o["filled_avg_price"]) if o.get("filled_avg_price") else None,
        )


class SimBroker:
    """An exchange in a box. Set `prices` (and `now`) before each decision; orders fill at that price plus slippage."""

    mode = "sim"

    def __init__(
        self, cash: float = 100_000.0, *, slippage_bps: float = 1.0, commission: float = 0.0
    ) -> None:
        self.cash = cash
        self.start_cash = cash
        self.slippage = slippage_bps / 10_000
        self.commission = commission
        self.prices: dict[str, float] = {}
        self.fills: dict[
            str, float
        ] = {}  # a fill price that overrides `prices` (the next bar's open, in a backtest)
        self.history: dict[str, list[Bar]] = {}
        self.holdings: dict[str, tuple[float, float]] = {}  # symbol -> (qty, average price)
        self.open = True
        self.last_equity = cash
        self.orders: dict[str, Order] = {}
        self.counter = 0

    def equity(self) -> float:
        return self.cash + sum(q * self.prices.get(s, p) for s, (q, p) in self.holdings.items())

    def account(self) -> Account:
        e = self.equity()
        return Account(e, self.cash, self.cash, self.last_equity)

    def positions(self) -> list[Position]:
        return [
            Position(s, q, avg, self.prices.get(s, avg))
            for s, (q, avg) in self.holdings.items()
            if q
        ]

    def bars(self, symbol: str, limit: int = 60) -> list[Bar]:
        return self.history.get(symbol, [])[-limit:]

    def price(self, symbol: str) -> float:
        if symbol not in self.prices:
            raise BrokerError(f"No price for {symbol}")
        return self.prices[symbol]

    def is_open(self) -> bool:
        return self.open

    def submit(self, symbol: str, side: Side, qty: float, client_id: str) -> Order:
        if client_id in self.orders:
            return self.orders[client_id]
        base = self.fills.get(symbol) or self.price(symbol)
        price = base * (1 + self.slippage if side == "buy" else 1 - self.slippage)
        held, avg = self.holdings.get(symbol, (0.0, 0.0))
        if side == "buy":
            cost = qty * price + self.commission
            if cost > self.cash:
                raise BrokerError("Insufficient buying power")
            self.cash -= cost
            total = held + qty
            self.holdings[symbol] = (total, (held * avg + qty * price) / total)
        else:
            if qty > held:
                raise BrokerError("Cannot sell more than is held")
            self.cash += qty * price - self.commission
            self.holdings[symbol] = (held - qty, avg)
        self.counter += 1
        order = Order(f"sim-{self.counter}", client_id, symbol, side, qty, "filled", qty, price)
        self.orders[client_id] = order
        return order
