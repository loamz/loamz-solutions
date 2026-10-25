# Paper Trader

An automated trading loop with the safety around it built in, starting (and staying, for a long time) on a **paper**
account. It is software for running experiments with your own signal model. It is not investment advice, and nothing here
predicts anything: a simulation or a backtest says nothing about the future.

## Try it now (no account)
```bash
uv sync
uv run loamz-solutions trade demo                 # replay synthetic prices through the full engine and limits
uv run loamz-solutions trade demo --symbols AAA,BBB,CCC --days 600
```

## Run it on a paper account
1. Create a free **Alpaca paper** account and an API key pair (Paper Trading → API keys).
2. Put `ALPACA_KEY_ID`, `ALPACA_SECRET_KEY` and `TRADE_SYMBOLS` in `.env`. The base URL defaults to paper.
3. ```bash
   uv run loamz-solutions trade status                  # account, positions, kill switch, ledger check
   uv run loamz-solutions trade backtest                # your model over two years of daily bars, same limits
   uv run loamz-solutions trade run-once                # one pass: signals, orders, a snapshot of equity
   uv run loamz-solutions trade watch --every 300       # keep going (the market-hours rule keeps it quiet at night)
   uv run loamz-solutions trade report                  # profit and loss, win rate, drawdown, from the ledger
   uv run loamz-solutions trade explain AAPL            # why did it do that? (plain words; needs LLM_API_KEY)
   uv run loamz-solutions trade kill                    # stop everything now; `trade resume` to continue
   ```

## The desk: a strategy on the whole account

With market data on Loamz Data ([markets.md](markets.md)), the desk runs a researched strategy ([research.md](research.md))
on the whole paper account. The default is momentum-trend with idle cash in SPY. Chat with the Paper trader agent, or use
`loamz-solutions serve` tools directly:

| Tool | What it does |
|---|---|
| `trade_ratings` | Each stock's rating (strong buy to strong sell) and the factors behind it, from the last close. |
| `trade_plan` | The orders for the next open as a table: limit, stop, dollars at risk and reason. Records the plan; sends nothing. |
| `trade_approve_plan` | Sends that plan exactly as shown. Needs a person's approval in chat. |
| `trade_follow_up` | Records fills at their real prices, and sends buys that were waiting for the sells' cash. |
| `trade_performance` | The account next to SPY. |

**How a plan becomes orders:**
- Every order is a day limit order at the last close ±0.5%. A gap is never chased, and an unfilled order expires at the close.
- Sent while the market is closed, orders wait at Alpaca for the open.
- Sells go first. Buys go only while settled cash covers them, so the account never borrows.
- Each new position gets a stop 2.5 ATRs below the close.

**The routine** (`loamz-solutions run`, New York time):

| Time | What runs |
|---|---|
| 18:00 | The day's prices are stored. |
| 18:15, weekdays | Risk exits are sent without approval: a close below the stop, a close below the 100-day average, or the drawdown breaker. On Fridays, the week's plan is also proposed and emailed. |
| 09:45 and 11:00 | Follow-up. |

What to watch: Alpaca's paper dashboard shows the orders and fills. `.state/trader/desk.sqlite` keeps the plans, approvals
and fills, hash-chained.

## Bring your own model
A model is anything with `predict(symbol, bars) -> Signal`. The simplest way to plug in a trained model is to serve it over
HTTP and set `SIGNAL_URL` (and `SIGNAL_TOKEN`):

```
POST SIGNAL_URL    {"symbol": "AAPL", "bars": [{"t": "...", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 100}, ...]}
→ 200              {"side": "buy" | "sell" | "hold", "confidence": 0.0-1.0, "rationale": "why, in a sentence"}
```

If the model errors, times out or answers something unusable, the trader treats it as **hold**: it never guesses. Without
`SIGNAL_URL` a moving-average crossover baseline runs, only so the machinery has something to exercise.

## What stands between a signal and an order
Every order is proposed to the guard, which asks these rules in order. Any "no" is final and written to the ledger.

| Rule | Default | Meaning |
| --- | --- | --- |
| Kill switch | off | a file (`.state/KILL`) or `LOAMZ_SOLUTIONS_KILL` stops everything |
| Trading hours | paper and live | no orders while the market is closed |
| Allowlist | `TRADE_SYMBOLS` | only these symbols, ever |
| No shorting | on | a sell may not exceed what is held |
| Max order | 5,000 | notional per order |
| Max position | 20% of equity | per symbol; buys are trimmed to fit, then re-checked |
| Daily loss | 2% | after losing this much today, no new buys (sells still allowed) |
| Orders per day | 10 | across all symbols |
| Already sent | on | one order per symbol, side and day (a repeat is refused with the reason, not silently ignored by the broker) |
| Cooldown | 30 min | between different orders on one symbol |
| Approval | live only | a person must type `y` for every live order |

Sizing: a buy commits `risk_per_trade` (1% of equity) times the model's confidence, rounded down to whole shares. Signals
under `min_confidence` (60%) do nothing. One order per symbol, side and day is sent by construction (the order id is derived
from them), so running twice cannot trade twice.

## Alerts that say "trade or not"
A watchlist checks levels, big moves and the model's signals and tells you what each means, without trading:

```bash
uv run loamz-solutions trade watchlist --rules "AAPL>200,AAPL<170,TSLA:move=3%,MSFT:signal"
# in .env, WATCH_RULES=… makes `loamz-solutions run` check it every WATCH_EVERY seconds (300) while the market is open
```

| Rule | Fires when |
| --- | --- |
| `SYM>PRICE`, `SYM<PRICE` | the last price crosses the level |
| `SYM:move=N%` | today's move against the previous close is at least N percent, either way |
| `SYM:signal` | the model says buy or sell with at least `TRADE_MIN_CONFIDENCE` |

A signal alert is the classification you asked for: it quotes the model's reason, then answers **Would trade** (side, shares,
size, and that every limit passes) or **No trade** with the rule that would stop it (kill switch, daily loss, position
limit, market closed...). Alerts go where `NOTIFY_*` points (see [operations.md](operations.md)), once per rule and day.

## The ledger
`.state/trader/ledger.sqlite` is an append-only, hash-chained record: every signal, every "no trade" with its reason, every
proposal, denial, approval, fill and failure, and an equity snapshot after each pass. `trade verify` recomputes the chain and
reports the first altered entry. Profit and loss, win rate and drawdown are computed from it alone.

## Going live
Not recommended, and refused by default. It needs both `ALPACA_BASE_URL=https://api.alpaca.markets` and
`TRADE_ALLOW_LIVE=I_UNDERSTAND_THE_RISK`, and then every order waits for you at the terminal. Live trading has not been
tested here. Run paper for months, read the ledger weekly, and keep the limits small.
