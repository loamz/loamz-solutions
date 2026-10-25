"""Which stocks to trade: the most liquid US large caps, plus the ETFs that serve as benchmark and sector context.

Liquid means cheap to trade (tight spreads, deep books) and clean data. `select_universe` ranks the candidates by their average
dollar volume over the last 60 trading days and keeps the top `n` above a minimum price. Benchmarks are always included.

The candidates are today's large caps (roughly the S&P 100), with their GICS sector. Ranking history by *today's* leaders
flatters a backtest (survivorship bias: the companies that fell out of the index are not in it), so a backtest over this
universe says so in its report.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from loamz.solutions.markets.alpaca_data import DailyBar

CANDIDATES: dict[str, str] = {
    # Information Technology
    **dict.fromkeys(
        [
            "AAPL",
            "MSFT",
            "NVDA",
            "AVGO",
            "ORCL",
            "CRM",
            "ADBE",
            "AMD",
            "INTC",
            "CSCO",
            "QCOM",
            "TXN",
            "IBM",
            "ACN",
            "NOW",
            "INTU",
            "AMAT",
            "MU",
            "LRCX",
            "PLTR",
        ],
        "Information Technology",
    ),
    # Communication Services
    **dict.fromkeys(
        ["GOOGL", "META", "NFLX", "DIS", "CMCSA", "T", "VZ", "TMUS"], "Communication Services"
    ),
    # Consumer Discretionary
    **dict.fromkeys(
        ["AMZN", "TSLA", "HD", "MCD", "NKE", "LOW", "SBUX", "BKNG", "TJX", "UBER"],
        "Consumer Discretionary",
    ),
    # Consumer Staples
    **dict.fromkeys(
        ["WMT", "COST", "PG", "KO", "PEP", "PM", "MO", "MDLZ", "CL"], "Consumer Staples"
    ),
    # Financials
    **dict.fromkeys(
        [
            "BRK.B",
            "JPM",
            "BAC",
            "WFC",
            "GS",
            "MS",
            "C",
            "SCHW",
            "BLK",
            "AXP",
            "V",
            "MA",
            "PYPL",
            "SPGI",
            "COF",
        ],
        "Financials",
    ),
    # Health Care
    **dict.fromkeys(
        [
            "LLY",
            "UNH",
            "JNJ",
            "MRK",
            "ABBV",
            "PFE",
            "TMO",
            "ABT",
            "DHR",
            "AMGN",
            "BMY",
            "GILD",
            "MDT",
            "CVS",
            "ISRG",
        ],
        "Health Care",
    ),
    # Energy
    **dict.fromkeys(["XOM", "CVX", "COP"], "Energy"),
    # Industrials
    **dict.fromkeys(
        ["GE", "CAT", "BA", "HON", "UPS", "RTX", "LMT", "DE", "UNP", "MMM"], "Industrials"
    ),
    # Utilities, Materials, Real Estate
    **dict.fromkeys(["NEE", "DUK", "SO"], "Utilities"),
    **dict.fromkeys(["LIN"], "Materials"),
    **dict.fromkeys(["AMT", "PLD"], "Real Estate"),
}

BENCHMARKS: dict[str, str] = {
    "SPY": "S&P 500",
    "QQQ": "Nasdaq 100",
    "XLK": "Information Technology",
    "XLC": "Communication Services",
    "XLY": "Consumer Discretionary",
    "XLP": "Consumer Staples",
    "XLF": "Financials",
    "XLV": "Health Care",
    "XLE": "Energy",
    "XLI": "Industrials",
    "XLB": "Materials",
    "XLU": "Utilities",
    "XLRE": "Real Estate",
}


@dataclass(frozen=True)
class UniverseMember:
    as_of: date
    symbol: str
    kind: str  # "stock" or "etf"
    sector: str  # a stock's GICS sector; for an ETF, what it tracks
    dollar_volume: float  # average daily dollar volume over the lookback
    rank: int  # 1 = most liquid stock; ETFs are 0


def candidates() -> list[str]:
    return sorted({*CANDIDATES, *BENCHMARKS})


def select_universe(
    bars: Mapping[str, list[DailyBar]],
    as_of: date,
    *,
    n: int = 50,
    lookback: int = 60,
    min_price: float = 10.0,
) -> list[UniverseMember]:
    """The `n` most liquid candidate stocks as of `as_of` (only bars on or before it count), then every benchmark ETF with data."""
    ranked: list[tuple[float, str]] = []
    for symbol in CANDIDATES:
        recent = [b for b in bars.get(symbol, []) if b.date <= as_of][-lookback:]
        if len(recent) < lookback // 2 or recent[-1].close < min_price:
            continue  # too new to judge, or a penny stock
        ranked.append((sum(b.dollar_volume for b in recent) / len(recent), symbol))
    ranked.sort(reverse=True)
    members = [
        UniverseMember(as_of, symbol, "stock", CANDIDATES[symbol], volume, rank)
        for rank, (volume, symbol) in enumerate(ranked[:n], start=1)
    ]
    for symbol, tracks in BENCHMARKS.items():
        recent = [b for b in bars.get(symbol, []) if b.date <= as_of][-lookback:]
        if recent:
            volume = sum(b.dollar_volume for b in recent) / len(recent)
            members.append(UniverseMember(as_of, symbol, "etf", tracks, volume, 0))
    return members
