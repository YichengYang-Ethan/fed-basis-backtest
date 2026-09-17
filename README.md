# fed-basis-backtest

Does the price gap between prediction-market FOMC contracts (Kalshi, Polymarket) and CBOT
30-Day Fed Funds futures (ZQ) survive a hedge, costs, and an honest look for lookahead?

**Answer: it did, and then it stopped.** The edge is real in the 2022-2025 "pinned" regime and
is not distinguishable from noise in the regime that began in September 2025. What changed is
not the strategy, it is the effective fed funds rate: it used to hold one value all month, and
now it moves inside the month, which is exactly what the hedge cannot absorb.

## Start here

- ### [results/PINNED_REGIME.md](results/PINNED_REGIME.md)
  The main result. The hedge error, computed rather than estimated, is **0.134 cents** per
  contract in the pinned regime and **3.554 cents** in the drifting one, a factor of 26.
  Twenty of twenty-five pinned meetings replicate the decision with literally no residual.
  After gating, the entry threshold is about 3 cents of basis: nine trades, nine winners,
  mean +2.34c. The same gate in the drifting regime gives two trades and a coin flip.

- ### [results/SYNTHESIS.md](results/SYNTHESIS.md)
  The earlier round, which could only see the drifting regime and concluded there was no
  arbitrage. Correct for what it could measure; its "structural bind" section is now resolved.

- ### [data/](data/) — four derived tables, documented in [data/README.md](data/README.md)
  No market data. Databento's licence makes republishing CME settlements an act of
  redistribution, so everything here is one row per meeting. Two of the four tables are built
  purely from published EFFR and carry no licence at all.

## What this repository is careful about

Three lookahead bugs were found by looking for them, and each one manufactured an edge that
does not exist:

1. **ZQ settles at 14:00 CT, an hour AFTER the 14:00 ET FOMC announcement.** Measured, not
   assumed: the 2024-09-18 settle implies -50.83bp and so do the next three sessions, while
   2024-09-17 implies -42.08bp. Comparing that settle against an earlier prediction-market
   snapshot invented a 62.7-cent edge.
2. **A frozen CLOB midpoint prints every minute exactly like a live one.** Three of the largest
   apparent edges were dead books, one quoting a 24.6% chance of a January 2024 hike. A
   leg-sum sanity band does not catch this; counting distinct midpoints over a trailing window,
   on in-play legs only, does.
3. **A continuous futures series is the wrong contract.** At a 19-day pre-meeting horizon the
   front contract has zero exposure to the meeting being priced in 73% of cases.

And one structural trap worth stating plainly: `KXFEDDECISION` is a **five-outcome mutually
exclusive ladder, not a binary**. A single-leg position against one ZQ contract is not a hedge.
It pays the same amount in two of five states and loses roughly nineteen times the edge in a
third, which the market itself prices at about 1.5%.

## Layout

```
prereg/     The plan, written before results existed but NEVER FROZEN. Read its status header.
harness/    Panel construction, cost model, the pinned-regime pipeline, the public-data builder
results/    Conclusions and the per-meeting tables behind them
data/       Derived, publishable tables (see data/README.md)
tests/      Regression tests
```

## Reproducing

The analysis reads a private DuckDB panel built by a companion repository, `fed-pricing-db`,
from a Databento subscription, an IBKR account, the Polymarket CLOB and FRED. With that panel
in place:

```bash
python3 harness/pinned_regime.py --csv results/
python3 harness/build_public_data.py
```

Without it, `data/` still stands on its own: `hedge_error.csv` and `effr_month_profile.csv` are
reproducible from FRED alone, and they carry the main result.

## Status and limits

Nine trades. t = 1.95 at the 3-cent gate is not significance, and two gate dimensions were
swept. The basis is measured midpoint to midpoint with an assumed 1-cent half-spread that was
never observed, because neither venue's historical order book is available. The regime that
made the strategy work ended in 2025-09. Nothing here is a recommendation to trade anything.

Author: Yicheng Yang
