"""Market data for research and trading: daily bars from Alpaca, a liquid universe, and the tables they live in on Loamz Data.

data = MarketData(key_id, secret)                         # Alpaca's market-data API (SIP daily history)
picked = select_universe(data.daily_bars(candidates(), start=...), as_of=today)
warehouse = MarketWarehouse(DataClient(endpoint, credential))
warehouse.load(data, picked, years=5)                      # first load
warehouse.update(data, today)                              # every evening: only the days not yet stored
"""

from loamz.solutions.markets.alpaca_data import (
    DailyBar,
    MarketData,
    MarketDataError,
    last_settled_day,
)
from loamz.solutions.markets.universe import (
    BENCHMARKS,
    CANDIDATES,
    UniverseMember,
    candidates,
    select_universe,
)
from loamz.solutions.markets.warehouse import MarketWarehouse

__all__ = [
    "BENCHMARKS",
    "CANDIDATES",
    "DailyBar",
    "MarketData",
    "MarketDataError",
    "MarketWarehouse",
    "UniverseMember",
    "candidates",
    "last_settled_day",
    "select_universe",
]
