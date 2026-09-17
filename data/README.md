# Derived data

Four tables. None of them is market data.

## Why there are no prices here

Publishing CME settlement prices would be **redistribution** under Databento's licence, which
turns a non-professional subscriber into a redistributor and requires an information licence
agreement with CME plus redistribution fees. Databento permits derived data on the condition
that it cannot be reverse-engineered back to the feed. Every CME- or Polymarket-derived table
below is therefore **one row per FOMC meeting, never a daily series**: 33 summary rows cannot
reconstruct 70,579 settlements or 9.2 million order-book midpoints.

Two of the four tables contain no exchange data at all. They are built from the Fed's own
meeting calendar and from published EFFR, so they carry no licence encumbrance.

Anyone with their own Databento key can rebuild the full panel from `harness/`. The pull and
build scripts are published in the companion repository [fed-pricing-db](https://github.com/YichengYang-Ethan/fed-pricing-db).

## Tables

### `fomc_instruments.csv` — 49 rows, no market data

The calendar arithmetic that makes this strategy possible. A ZQ contract settles at 100 minus
the arithmetic mean of daily EFFR over its delivery month, so for a meeting in month M with
`w = days_post / days_in_month` of the month spent at the new rate:

```
FRONT spread  F_M     - F_{M+1} = (1 - w) * D
BACK  spread  F_{M-1} - F_M     =      w  * D
```

`D` is the decision in percentage points. The unknown post-decision EFFR level cancels in both,
which is what removes the EFFR publication lag as a constraint. A month carrying any *other*
meeting's rate change disqualifies the construction using it, hence `front_eligible` and
`back_eligible`. Because `w` is large exactly when a meeting falls early in its month, the two
are complementary: together they cover 37 of 49 meetings where FRONT alone covers 23.

`contracts_per_zq` is how many 25bp-equivalent binary contracts one ZQ unit hedges, which is
`1041.75 * span` (ZQ is $41.67 per basis point, and a 25bp digital pays $1).

### `hedge_error.csv` — 33 rows, built from published EFFR only

**The main result.** Once a delivery month is past, the contract's terminal price is a known
function of EFFR, so the hedge's error is not estimated, it is computed. `resid_bp` is the
spread's terminal value minus `span * D`; `err_cents` expresses it as cents per
25bp-equivalent contract.

| regime | meetings | MAE | median | p90 | max | exactly zero |
| --- | --- | --- | --- | --- | --- | --- |
| pinned, 2022-01 to 2025-08 | 25 | **0.134c** | 0.000c | 0.390c | 2.000c | 20 of 25 |
| drifting, 2025-09 onward | 8 | **3.554c** | 2.496c | 8.489c | 9.467c | 2 of 8 |

`is_exact_zero` uses a tolerance of 1e-3 cents. EFFR is published to 1bp, so a residual below
that is floating-point residue from summing about thirty daily rates rather than a hedge error;
without the tolerance two pinned meetings show up at 2.5e-8 bp.

### `effr_month_profile.csv` — 141 rows, built from published EFFR only

What EFFR actually did each month back to 2015, which is the evidence for the regime split.
`distinct` is how many different values it printed, `range_bp` its intra-month range, and
`drift_bp` how far the month's average sat from the rate it opened at. The 2016-2019 rows are
the floor-system era and are the better prior for how large the drift tail can get: the
2022-2025 zeros are a property of abundant reserves, not of the instrument.

### `venue_snapshot.csv` — 49 rows

One row per meeting: each venue's implied expected move at the entry horizon the backtest
actually uses (19 days before the decision), plus coverage counts. Two probabilities per
meeting reconstruct nothing. 27 meetings carry both a CME and a Polymarket reading.

`basis_cents_t19` is `4 * (cme_implied_bp - poly_implied_bp)`, the quantity the strategy trades.
Note 2024-01-31 at -25.9c: that is not an opportunity, it is a frozen Polymarket book quoting a
24.6% chance of a January 2024 *hike*. See §3 of `results/PINNED_REGIME.md`.

## Coverage of the underlying panel

For reference, what the private panel holds and this directory summarises:

| source | rows | span | detail |
| --- | --- | --- | --- |
| CME ZQ, Databento GLBX.MDP3 | 70,579 | 2021-12-31 to 2026-08-31 | 116 delivery months, 2022-01 to 2031-08 |
| CME ZQ, IBKR | 8,777 | 2023-10-03 to 2026-09-17 | 18 delivery months; the only source covering Sept 2026 |
| CME ZQ listed calendar spreads | 823,140 | same | 2,714 spread symbols |
| Polymarket | 9.25M minute + 300k hourly | 2022-12-15 to 2026-09-17 | 33 meetings, 2 to 5 legs each |
| Kalshi `KXFEDDECISION` | 624,766 candles + 155,004 trades | quotes from 2025-09-29 | 13 meetings, 5 legs each, 2 settled |
| FRED | EFFR from 2000, DFF from 1954 | through 2026-09-17 | EFFR, IORB, SOFR, target range |

The two CME sources were cross-validated on 8,795 overlapping daily settlements: **100% exact
agreement, zero disagreement to the last decimal.**

## Known gaps

- **No order-book depth on either venue.** Kalshi's `liquidity_dollars` is zero on all 65
  markets. Polymarket's published price is a CLOB **midpoint, not a tradeable quote**, so
  historical execution cost can only be assumed, never measured.
- **No intraday CME data.** One settlement per day, struck 14:00 CT. Kalshi stops trading at
  13:59 ET and the FOMC announces at 14:00 ET, so the two venues cannot be aligned to a common
  instant without intraday ZQ. This is the single biggest remaining gap.
- **SOFR futures were not pulled**, so the SOFR strip is unavailable as a cross-check.
- `realized_change_bp` is null for 2026-09-16 because it is computed from FRED's DFEDTARU,
  which stops at 2026-09-16 while the decision took effect 2026-09-17. It fills in on the next
  FRED refresh. The hike itself is corroborated by Kalshi settlement and by IORB stepping
  3.65 to 3.90.

## Rebuilding

```bash
python3 harness/build_public_data.py
```

Reads `~/Developer/fed-pricing-db/fed.duckdb` and the ZQ parquets; writes this directory.
