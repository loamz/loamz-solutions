# Research lab

Before a strategy trades, even on paper, it is replayed over the stored market history ([markets.md](markets.md)) and has to beat
simpler things. The lab is honest by construction:

- decisions use prices up to the close; orders fill at the next open, at 7 bps a side;
- stops fill at the stop, or at the open when the price gaps through it;
- the last 18 months are held out, and every result is shown next to SPY.

```bash
loamz-solutions trade lab                  # momentum-trend: full report against SPY
loamz-solutions trade lab --sweep          # the same, at neighbouring settings (robust, or curve-fitted?)
loamz-solutions trade lab --experiments    # variants: idle cash in SPY, no go-to-cash breaker
loamz-solutions trade lab --baselines      # the ladder below
```

## The baseline ladder

If a lower rung does as well as a higher one, the simpler thing wins.

1. **Naive baselines.** SPY bought once; every stock at equal weight; SPY only while it is above its 200-day average.
2. **Evidence.** For each factor, does its ranking of the stocks predict the next week's and month's returns? This is
   measured on days a horizon apart, so samples never overlap.
   - **The information coefficient (IC)** is the rank correlation between the factor and the return that followed. 0 is
     noise; +0.03 to +0.05, held for years, is a real edge.
   - **t** says whether the average IC could be luck (above 2: unlikely).
3. **Each factor alone, traded.** Every factor trades inside momentum-trend's frame: market regime, uptrend only, top 8,
   risk sizing, stops and the breaker. So the rows differ only in what they rank by.
4. **The blend:** 12-1 month momentum, nearness to the 52-week high and low volatility, equally weighted. These are fixed
   in `factors.BLEND`, chosen before any result was seen. Each factor becomes a z-score across the stocks that day, and
   the blend keeps them, so every rating explains itself ("momentum +1.6σ (12-1 month +48%), ...").
5. **Momentum-trend**, the first strategy, for reference.

| Factor | The idea |
|---|---|
| 12-1 month momentum | Last year's winners keep winning for months (Jegadeesh and Titman); the latest month is skipped. |
| Distance above the 200-day average | Prices trend. |
| Near its 52-week high | Investors anchor on the high and under-react to news that breaks through it (George and Hwang). |
| Low volatility | Calm stocks have earned as much as wild ones, with less risk. |
| Last month's losers | Over a few weeks, prices overshoot and partly come back. |
| Oversold: RSI(2) low | A sharp two-day dip often bounces: a swing-trading pattern. |
| Above its 55-day high | A breakout through the recent range (the Turtles' Donchian rule). |

To add a factor, write a function `(panel, symbol, t) -> value or None` in `trading/factors.py` that uses only data up to day
`t`, and add it to `FACTORS`. A test checks that changing any later price leaves every factor unchanged.

## Reading the results

The universe is today's 50 most liquid large caps, so the past is flattered: the decade's winners are owned from the first day,
and the losers that left the index are missing. This flatters equal weight most, and it makes high-volatility stocks look
better than they were. Read the rows against each other, not as returns to expect.
