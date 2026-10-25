# Market data

Daily prices for the US stocks the trader may trade, kept on Loamz Data so agents and the research lab read the same numbers.

| Table | What is in it |
|---|---|
| `main.markets.daily_bars` | `symbol, date, open, high, low, close, volume, vwap, trades`, one row per trading day. Split- and dividend-adjusted, so returns are total returns. |
| `main.markets.universe` | `as_of, symbol, kind (stock/etf), sector, dollar_volume, rank`: each selection of what to trade. |

## The universe

The 50 most liquid of about 100 US large caps (ranked by average dollar volume over 60 trading days, price above $10), plus
SPY, QQQ and the 11 sector ETFs (the benchmark and sector context). Liquid means cheap to trade and clean data.

The candidates are today's large caps, so a backtest over this universe is flattered (survivorship bias: companies that left the
index are missing). The research lab's report says so.

## Prices

From Alpaca's SIP feed (every exchange): its daily history is free, and its volume is the whole market's. `markets update`
appends, for each symbol, only the days not stored yet, and only days whose bar is final (after 16:30 New York time), so running
it twice adds nothing.

## Running it

```bash
loamz-solutions markets universe          # today's top 50 (prints only)
loamz-solutions markets load --years 5    # first time: the universe and its history
loamz-solutions markets update            # every evening; `run` does it at MARKETS_UPDATE_AT, New York time
```

Next to a cell (`docker compose up -d`), the solutions join its network and reach Loamz Data, Keycloak and storage by the cell's
own names; run the commands with `docker compose exec solutions loamz-solutions markets ...`.

The writer is a service account (`loamz-markets`) with rights on `main.markets` only (create, write, read) and on compute; your
organisation can read the schema, so agents query it as you, under your grants:

```python
import loamz.data as ld
ld.sql("SELECT date, close FROM main.markets.daily_bars WHERE symbol = 'SPY' ORDER BY date")
```
