"""`loamz-solutions markets ...`: fill and refresh the market tables on Loamz Data.

    loamz-solutions markets universe            the 50 most liquid candidates today (prints, writes nothing)
    loamz-solutions markets load [--years 5]     store the universe and its daily history
    loamz-solutions markets update              append the days not stored yet (the nightly job does this)

Needs ALPACA_KEY_ID / ALPACA_SECRET_KEY (market data), and LOAMZ_ENDPOINT with a service account (LOAMZ_CLIENT_ID,
LOAMZ_CLIENT_SECRET, LOAMZ_ISSUER) allowed to write main.markets.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

from loamz.solutions.markets.alpaca_data import MarketData, last_settled_day
from loamz.solutions.markets.universe import candidates, select_universe
from loamz.solutions.markets.warehouse import MarketWarehouse


def configured(env: Mapping[str, str]) -> bool:
    return all(
        env.get(k)
        for k in ("ALPACA_KEY_ID", "ALPACA_SECRET_KEY", "LOAMZ_ENDPOINT", "LOAMZ_CLIENT_ID")
    )


def build(env: Mapping[str, str]) -> tuple[MarketData, MarketWarehouse]:
    from loamz.data import DataClient
    from loamz.identity import DefaultLoamzCredential

    data = MarketData(
        env["ALPACA_KEY_ID"], env["ALPACA_SECRET_KEY"], feed=env.get("MARKET_FEED", "sip")
    )
    client = DataClient(env["LOAMZ_ENDPOINT"], DefaultLoamzCredential())
    return data, MarketWarehouse(client)


def pick(data: MarketData, today, top: int):
    recent = data.daily_bars(candidates(), start=today - timedelta(days=100), end=today)
    return select_universe(recent, today, n=top)


def add_parser(solutions) -> None:
    parser = solutions.add_parser(
        "markets", help="market data on Loamz Data: the universe and daily bars"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    universe = commands.add_parser("universe", help="print the most liquid candidates today")
    universe.add_argument("--top", type=int, default=50)
    load = commands.add_parser("load", help="store the universe and its daily history")
    load.add_argument("--years", type=int, default=5)
    load.add_argument("--top", type=int, default=50)
    commands.add_parser("update", help="append the days not stored yet")


def run(args: argparse.Namespace) -> None:
    env = dict(os.environ)
    if not configured(env):
        sys.exit(__doc__)
    data, warehouse = build(env)
    today = last_settled_day(datetime.now(UTC))
    if args.command == "universe":
        for m in pick(data, today, args.top):
            label = f"#{m.rank:<3}" if m.kind == "stock" else "ETF "
            print(f"{label} {m.symbol:<6} {m.sector:<24} ${m.dollar_volume / 1e9:6.2f}B a day")
    elif args.command == "load":
        members = pick(data, today, args.top)
        written = warehouse.load(data, members, years=args.years, today=today)
        print(f"universe of {len(members)} as of {today}; {written} daily bars stored")
    elif args.command == "update":
        print(f"{warehouse.update(data, today=today)} new daily bars up to {today}")
