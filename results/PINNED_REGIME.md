# The pinned regime: the answer to the question the tracking-error work left open

`SYNTHESIS.md` ended on a bind. Every meeting we could test had a hedge we could not rely
on, and every meeting with a hedge we could rely on was one we had no ZQ history for. The
reason was a regime change: EFFR sat at a single value all month long from 2022-01 through
2025-08, and started drifting inside the month from 2025-09. Our ZQ history only reached
back to 2023-10 and, in practice, only covered the drifting meetings with prediction-market
data alongside.

Databento closed the data gap (ZQ daily settlements back to 2021-12-31, cross-validated
against IBKR at 8,795 overlapping observations with **zero** disagreement, exact to the
last decimal). So the question is now answerable.

**The answer: the absence of edge is a property of the current regime, not of the strategy.**

## 1. The hedge was essentially exact, and then it was not

A ZQ contract settles at 100 minus the arithmetic mean of daily EFFR over its delivery
month. Once a month is past, the contract's terminal price is a known function of published
EFFR, so the hedge's error is not estimated here, it is computed.

| Regime | Meetings | MAE | Median | p90 | Max | Exactly zero |
| --- | --- | --- | --- | --- | --- | --- |
| pinned, 2022-01 to 2025-08 | 25 | **0.134c** | 0.000c | 0.390c | 2.000c | 20 of 25 |
| drifting, 2025-09 onward | 8 | **3.554c** | 2.496c | 8.489c | 9.467c | 2 of 8 |

Units are cents per 25bp-equivalent binary contract. The hedge got **26x worse**. In the
pinned regime 20 of 25 meetings replicated the decision with literally no residual, because
a constant intra-month EFFR makes the calendar spread an exact instrument. That is the
regime in which this trade is arbitrage-shaped. The current one is not.

Two qualifications on that label, both found by a later audit and both real. "Pinned" means
pinned to within 1bp, not exactly: six month-segments (2022-01, 2022-07, 2023-02, 2023-03,
2023-06, 2023-07) carry a 1bp wobble, and 2023-06-14 is the only one of 20 HOLD meetings
where EFFR moved across the effective date at all (5.08 to 5.07). And the drift did not
persist. The genuine drift episode was 2025-09 through 2025-12; EFFR then re-pinned, printing
3.63 unchanged every day from 2026-08-01 through 2026-09-15. So "the hedge is 26x worse" is a
statement about the tail this regime can produce, not about how EFFR is behaving this month.
A max-based risk buffer still has to respect that tail, and the 2016-2019 floor-system era,
where clean-month drift ran to 9bp at p90, is the better prior for how bad it can get.

## 2. The instrument, and why it is the right one

FRONT = (M, M+1) with span 1-w, BACK = (M-1, M) with span w, where w is the fraction of the
meeting month spent at the new rate. Implied decision D = (F_near - F_far)/span. The unknown
post-decision EFFR level cancels, which is what removes the EFFR publication lag as a gate.

A month carrying any *other* meeting's rate change disqualifies the construction that uses
it. Because w is large exactly when the meeting falls early in its month, FRONT and BACK
are complementary: between them they cover 18 of the 21 Polymarket-era meetings at a median
span of 0.806, where the naive FRONT-only rule covers 10.

The instrument validates against outcomes it never saw: on the last pre-decision day its
implied decision correlates **0.9916** with the realized change, median absolute error
0.67bp.

## 3. Two lookahead bugs this exercise caught

**ZQ settles after the FOMC announcement.** ZQ settles 14:00 CT, which is 15:00 ET, an hour
after the 14:00 ET announcement. This is measured, not assumed: the 2024-09-18 settle implies
-50.83bp and so do the next three sessions, while 2024-09-17 implies -42.08bp. Comparing a
settle against a Polymarket snapshot taken earlier in the day hands ZQ an hour of free
information. On decision day it is catastrophic: it manufactured a 62.7c "edge" that does
not exist. Fix: snap Polymarket at 15:00 ET and drop decision day outright.

**Frozen CLOB midpoints look exactly like live ones.** `poly_prices.p` is a midpoint, and a
dead book prints it every minute just as a live book does. The `sum_raw` band of [0.95, 1.10]
does not catch this. Three of the largest apparent edges were dead books:

| Meeting | Snapshot | What the book was doing |
| --- | --- | --- |
| 2024-01-31 | 2024-01-04 | HIKE25 quoted 0.2455, a 24.6% chance of a January 2024 *hike*; HOLD took 3 distinct values across 10,248 prints in 7 days |
| 2024-03-20 | 2024-02-06 | CUT50P took **one** distinct value across 10,248 prints in 7 days |
| 2023-02-01 | 2022-12-19 | HIKE25 took **one** distinct value across 5,686 prints in 7 days |

All three passed the `sum_raw` gate. The fix is a liveness count: distinct midpoints over a
trailing 3 days, measured only on legs sitting in [0.05, 0.95], since a deep out-of-the-money
leg is legitimately still. Applying it, the apparent edge in the frozen bucket (median 7.67c,
p90 36c) collapses toward what live books show (median 3.88c).

## 4. The threshold

P&L identity. Entering at basis b and holding both legs to settlement locks in |b|; only the
hedge error e and the entry cost take it away:

```
net_cents = |b| - sign(b)*e - 100*ZQ_dollars/(1041.75*span) - poly_half_spread
```

One trade per meeting, taken the first day both gates are met, held to settlement. Costs are
ZQ $16 per calendar spread round turn and a 1.0c Polymarket half-spread.

| Regime | Basis gate | Trades | Win | Mean | Median | Worst | t |
| --- | --- | --- | --- | --- | --- | --- | --- |
| pinned | 0c | 10 | 50% | +1.01c | +0.27c | -2.12c | 0.78 |
| pinned | **3c** | **9** | **100%** | **+2.34c** | +1.12c | +0.64c | 1.95 |
| pinned | 5c | 4 | 100% | +6.66c | +7.08c | +0.64c | 2.25 |
| drifting | 0c | 4 | 25% | +0.40c | -0.50c | -3.75c | 0.19 |
| drifting | 3c | 2 | 50% | +1.30c | +1.30c | -3.75c | n/a |

**The threshold is roughly 3c of basis.** Below it the ZQ fixed cost plus the Polymarket
half-spread eat the trade, and the win rate falls to a coin flip. Above it, in the pinned
regime, 9 of 9 trades cleared. The same gate in the drifting regime produces 2 trades and a
coin flip, because the hedge error is now the same size as the edge.

The gate sweep is reported across both dimensions deliberately. Nothing here is tuned to a
single number, and the full surface is in the table the script prints.

## 5. What this is worth

One ZQ calendar spread hedges 1041.75 x span binary contracts, roughly 625 to 1042, about
$415 of Polymarket notional at a 50c price.

| | |
| --- | --- |
| Total, 9 meetings, 1 spread each | **$154** |
| Mean per meeting | $17 |
| Best / worst | $74 / $5 |
| Excluding the single 2024-09 trade | $80 over 8 meetings |
| Contracts needed on one leg to run 10 spreads | ~8,070 |

ZQ is not the binding constraint; it is one of the deepest contracts listed. Polymarket depth
is. The honest read is that this is a low-thousands-per-year strategy at realistic
Polymarket size, which is consistent with a research-scale book and not with deploying
meaningful capital.

## 6. What would break this

- **Nine trades.** t = 1.95 at the 3c gate is not significance, and two gate dimensions were
  swept. The result is suggestive, not established.
- **The basis is measured mid to mid.** The Polymarket half-spread is assumed at 1.0c, never
  observed. We have no historical Polymarket book depth. At a 2.0c half-spread the median
  pinned trade is close to flat.
- **The regime is over.** Every number in section 1 says the instrument that made this work
  stopped working in 2025-09. Nothing here is a claim about trading it today.
- **One trade carries an unpriceable tail.** Polymarket's `CUT50P` is "50+ bps", not a 50bp
  binary. At the 2024-09-18 entry, 25.5% of the book's mass sat in that open bucket. Had the
  Fed cut 75bp, the Polymarket leg would have paid 50 while ZQ paid 75, a 25bp mismatch worth
  100c per contract. That single trade contributes $74 of the $154, roughly half. The other eight carry at
  most 2.6c of the same exposure.
- **Entries cluster 17 to 21 days out.** Inside three weeks is where the book is live enough
  to trust, and that is also where the basis is smallest. The large early-cycle bases are the
  dead books of section 3.

## Reproducing

```bash
python3 harness/pinned_regime.py --csv results/
```

Reads `~/Developer/fed-pricing-db/fed.duckdb` and the Databento ZQ settlements. Writes
`pinned_hedge_error.csv` (per-meeting exact hedge error) and `pinned_trades.csv` (the nine
trades with their entry basis, hedge error, net cents, and dollars per spread).
