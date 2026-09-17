# Data Boundaries

**Companion to `PREREGISTRATION.md`. Frozen at the same git tag.**
**Author:** Yicheng Yang
**Verified as of:** 2026-09-17

Purpose: record every data-availability fact and every gotcha that shapes what the harness
can and cannot test, so a reviewer can see what was **not** tested and why, without having
to re-run the queries. Every "VERIFIED" item below was checked directly against the files on
this machine on 2026-09-17. Every "ASSUMPTION" item is something the data does not answer.

The data itself is **not** in this repository. `.gitignore` excludes `data/`, `*.parquet`,
`*.duckdb` and `*.jsonl*`: Kalshi's terms prohibit redistribution, and the Polymarket and CME
pulls are made under the researcher's own account entitlements. Only code, the
pre-registration, and derived aggregate results are committed.

---

## 1. Source locations

| Source | Path | Size / rows |
|---|---|---|
| Main database (DuckDB) | `/Users/ethanyang/Developer/fed-pricing-db/fed.duckdb` | 105,132,032 bytes |
| IBKR per-contract ZQ | `/Users/ethanyang/Developer/fed-pricing-db/raw/cme/ibkr/zq_contracts_ibkr.parquet` | 135,514 bytes, 8,777 rows |
| IBKR pull report | `/Users/ethanyang/Developer/fed-pricing-db/raw/cme/ibkr/pull_report.json` | 18 contracts |
| Build report | `/Users/ethanyang/Developer/fed-pricing-db/build_report.json` | n/a |
| DB README / tests | `/Users/ethanyang/Developer/fed-pricing-db/README.md`, `TESTS.md` | n/a |

---

## 2. What exists, per table (VERIFIED)

| Table | Rows | Range | Notes |
|---|---|---|---|
| `meetings` | 49 | 2022-01-26 to 2028-01-26 | includes `effective_date`, `days_in_month`, `days_post`, `delta` (= `w`) |
| `kalshi_markets` | 147 | n/a | 52 columns incl. `fee_type`, `tick_size`, `open_ts_utc`, `close_ts_utc` |
| `kalshi_candles` | 624,766 | see §4 | `interval` in {1, 60, 1440}; `ts` is **bar END** |
| `kalshi_trades` | 155,004 | **2026-07-10 19:03:29 CDT** to 2026-09-17 00:24:44 CDT | 65 markets; has `taker_side`, `is_block_trade` |
| `poly_markets` | 249 | n/a | has `enable_order_book`, `has_prices`, `yes_token_id`, `neg_risk` |
| `poly_prices` | 23,943,238 | see §5 | `fidelity` in {1, 60} |
| `poly_event_meeting` | 53 | n/a | `is_primary` flag maps events to meetings |
| `cme_zq` | 23,955 | 2000-09-01 to 2026-09-17 | yfinance `ZQ=F`, **continuous only** |
| `fred_rates` | 26,377 | 1954-07-01 to 2026-09-17 | 6,579 non-null `effr` (series starts 2000-07-03) |

Views present: `v_prob_kalshi_daily`, `v_prob_poly_daily`, `v_zq_meeting`, `v_prob_cme_daily`,
`v_basis_daily`.

---

## 3. IBKR per-contract ZQ (VERIFIED)

### 3.1 Retention boundary: the hard limit on the historical sample

IBKR retains expired ZQ contracts only back to **last-trade-date 2025-09-30**, and lists them
on exchange `CBOT`. The earliest delivery month obtainable is therefore **2025-09**. This is
the single most binding constraint in the whole project: it is why no meeting before
**2025-10-29** can be tested with a two-leg calendar spread, and why the 2025-09-17 meeting
fails by exactly one contract (its selected instrument is the Aug/Sep-2025 back spread and
the August 2025 contract is gone).

### 3.2 Pull gotcha

For an **expired** contract, requesting history with `endDateTime=""` returns roughly **33
stale bars**. Anchoring the request at the contract's own last trade date returns the full
two years. Any re-pull must use the per-contract anchor.

### 3.3 Coverage (18 contracts, 8,777 daily rows)

| Delivery month | Bars | First | Last |
|---|---|---|---|
| 2025-09 | 512 | 2023-10-03 | 2025-10-01 |
| 2025-10 | 512 | 2023-11-02 | 2025-10-31 |
| 2025-11 | 511 | 2023-11-30 | 2025-11-28 |
| 2025-12 | 512 | 2024-01-02 | 2025-12-31 |
| 2026-01 | 510 | 2024-02-01 | 2026-01-30 |
| 2026-02 | 509 | 2024-02-29 | 2026-02-27 |
| 2026-03 | 510 | 2024-04-02 | 2026-04-01 |
| 2026-04 | 510 | 2024-05-02 | 2026-05-01 |
| 2026-05 | 509 | 2024-05-30 | 2026-05-29 |
| 2026-06 | 508 | 2024-07-02 | 2026-07-01 |
| 2026-07 | 507 | 2024-08-01 | 2026-07-31 |
| 2026-08 | 507 | 2024-09-02 | 2026-09-01 |
| 2026-09 | 496 | 2024-10-02 | 2026-09-17 |
| 2026-10 | 475 | 2024-10-31 | 2026-09-17 |
| 2026-11 | 453 | 2024-12-02 | 2026-09-17 |
| 2026-12 | 432 | 2025-01-02 | 2026-09-17 |
| 2027-01 | 412 | 2025-01-30 | 2026-09-17 |
| 2027-02 | 392 | 2025-02-27 | 2026-09-17 |

Columns: `date, open, high, low, settle(=close), volume, average, barCount, contract,
delivery_month, ib_local_symbol, ib_con_id, source`.

**Contracts NOT held that the pre-registration will need:** 2027-03 onward. The 2027-03-17
meeting's selected instrument is the Feb/Mar-2027 back spread; Mar-2027 must be pulled before
its entry window closes on **2027-02-26**.

### 3.4 GOTCHA: trailing zero-volume stub bar

Several expired contracts carry **one extra bar dated the business day after the last trade
date**, with `volume = 0`, `barCount = 0`, and `open = high = low = settle` equal to the
final settle. Verified examples:

| Contract | Last real bar | Stub bar |
|---|---|---|
| 2025-09 | 2025-09-30, settle 95.7750, vol 24,113 | 2025-10-01, settle 95.7750, vol 0, barCount 0 |
| 2026-04 | 2026-04-30, settle 96.3600, vol 732 | 2026-05-01, settle 96.3600, vol 0, barCount 0 |
| 2026-08 | 2026-08-31, settle 96.3700, vol 1,531 | 2026-09-01, settle 96.3700, vol 0, barCount 0 |

Not every contract has one (2025-10, 2025-11, 2025-12, 2026-01, 2026-02, 2026-05, 2026-07
end on their last trade date). The harness must drop bars with `volume == 0 and
barCount == 0` rather than assume a uniform offset. Gate test `T-G1d`.

### 3.5 GOTCHA: historical ZQ bid-ask is UNOBTAINABLE

IBKR **does** serve intraday bars for expired ZQ contracts: verified 1-minute `TRADES`
(2,700 bars over 2 days) and 5-minute bars. But `whatToShow="BID_ASK"` is **degenerate for
ZQ**: `open == close` on every bar, so there is no usable historical spread from this source
and none is known from any other.

**Consequence:** ZQ execution cost is an ASSUMPTION for the entire study (assumption A1 in
the pre-registration: one minimum tick = 0.0025 price points = 0.25bp = $10.4175 per
contract per crossing), reported with a {1 tick, 2 ticks} sensitivity. The ZQ
calendar-spread tick size is likewise unverified and is assumed equal to the outright tick
(A2), covered by the 2-tick case.

### 3.6 Depth context (VERIFIED, observational)

Front-month ZQ daily volume in this data set is of order 10^5 contracts (e.g. ZQQ26 on
2026-07-24: 272,720; on 2026-07-29, the announcement day: 638,878). ZQ depth is therefore not
the binding constraint at the one-to-ten lot scale implied by the Kalshi volume cap. Deferred
months and the near leg in its delivery month are not separately characterised.

### 3.7 Daily settle cross-check for the 2026-07-29 verification

ZQQ26 (Aug-2026) daily settles around the July FOMC:

| Date | Open | High | Low | Settle | Volume |
|---|---|---|---|---|---|
| 2026-07-27 | 96.2950 | 96.3000 | 96.2700 | 96.2800 | 281,540 |
| 2026-07-28 | 96.2750 | 96.3050 | 96.2750 | 96.2950 | 289,379 |
| 2026-07-29 | 96.2900 | 96.3700 | 96.2750 | 96.3650 | 638,878 |
| 2026-07-30 | 96.3650 | 96.3700 | 96.3650 | 96.3650 | 89,830 |

The verified announcement-jump figure (96.3000 to 96.3625 at 13:00 CT, +6.25bp) is an
**intraday** measurement and is bracketed by, but not equal to, these settles. The harness
must not substitute settle-to-settle for the intraday jump (gate test `T-G1c`).

---

## 4. Kalshi (VERIFIED)

### 4.1 The purge: what history exists and what does not

Kalshi's public API serves candle history only for markets that had not yet closed at pull
time. Of the 38 FOMC meetings in the 2022-01 to 2026-09-17 window, **exactly two** have any
Kalshi price data:

| Event | Interval | Rows | First bar end | Last bar end |
|---|---|---|---|---|
| `KXFEDDECISION-26JUL` | 1440 | 1,427 | 2025-09-29 23:00 CDT | 2026-07-29 23:00 CDT |
| `KXFEDDECISION-26JUL` | 60 | 25,698 | 2025-09-29 12:00 CDT | 2026-07-29 13:00 CDT |
| `KXFEDDECISION-26JUL` | 1 | 62,879 | **2026-06-14 13:00 CDT** | 2026-07-29 13:00 CDT |
| `KXFEDDECISION-26SEP` | 1440 | 1,607 | 2025-09-29 23:00 CDT | 2026-09-16 23:00 CDT |
| `KXFEDDECISION-26SEP` | 60 | 27,985 | 2025-09-29 12:00 CDT | 2026-09-16 13:00 CDT |
| `KXFEDDECISION-26SEP` | 1 | 69,721 | **2026-08-02 12:59 CDT** | 2026-09-16 13:00 CDT |

**Two important clarifications to the loose phrase "purged before 2026-07-29":**

1. The purge is **per market, by close status**, not by date. For the markets that survived,
   hourly and daily candles reach back to **2025-09-29** (or 2025-10-06 for events listed
   later), giving roughly 300 to 350 pre-decision **days** per meeting.
2. Many **days** does not mean many **observations**. The unit of inference is the meeting.
   ARM 1's effective sample size is **2**, not 600. Within-meeting days are heavily
   autocorrelated and must be clustered.

**Minute-candle window:** minute bars exist only for roughly the **45 days before close**
(verified: `26JUL` minute data starts 2026-06-14, 45 days before the 2026-07-29 close). Any
analysis requiring minute resolution more than 45 days out is impossible on both venues.

Events with data for **future** meetings (not yet resolved, therefore usable only forward):
`KXFEDDECISION-26OCT`, `-26DEC`, `-27JAN`, `-27MAR`, `-27APR`, `-27JUN`, `-27JUL`, `-27SEP`,
`-27OCT`, `-27DEC`, `-28JAN`, plus the separate `KXFED-*` range series and single-market
`KXFEDHIKE-2`, `KXRATECUT-26DEC31` series.

### 4.2 Bar-end convention (VERIFIED: this is the G1 anchor)

`kalshi_candles.ts` is the **bar END**, confirmed by the `bar_end_utc` column:

- `interval = 1440` bars end at **04:00 UTC = 00:00 America/New_York**.
- `interval = 60` bars end on the hour.
- `interval = 1` bars end on the minute.
- The last minute bar of `KXFEDDECISION-26JUL` ends 2026-07-29 18:00 UTC = **14:00 ET =
  13:00 CT**, the FOMC release time.

**The G1 defect, quantified.** Kalshi's daily bar for trade date `D` ends at 00:00 ET on
`D+1`. The CME daily settlement for `D` is in the afternoon of `D`. Pairing a Kalshi daily
close with a same-day ZQ settle therefore gives the Kalshi leg roughly **eight to nine
hours** of extra information about the same day. This is the exact defect in the prior coarse
pass, and it biased that pass **in favour** of finding edge.

### 4.3 Field completeness (VERIFIED, over the two ARM 1 events)

| Interval | Rows | `bid_close` non-null | `ask_close` non-null | `price_close` non-null | rows with `vol > 0` |
|---|---|---|---|---|---|
| 1 | 132,600 | 132,600 | 132,600 | 66,284 | 66,284 |
| 60 | 53,683 | 53,683 | 53,683 | 10,594 | 10,594 |
| 1440 | 3,034 | 3,034 | 3,034 | 1,215 | 1,215 |

**Quotes are complete; trade prints are not.** `price_close` is null on exactly the bars with
zero volume. The harness must execute against `ask_close` (long) and `bid_close` (short) and
must never fall back to `price_close` or `mid` (gate test `T-G7b`). The `mid` and `spread`
convenience columns exist in the table and must not be used on any entry path.

### 4.4 Outcome buckets: GOTCHA, the tails are OPEN-ENDED

`KXFEDDECISION-26SEP` has exactly 5 markets forming an exhaustive, mutually exclusive
partition. The DB's `outcome` labels are misleading about the tails:

| DB `outcome` | Ticker suffix | Kalshi subtitle | True definition |
|---|---|---|---|
| `CUT50P` | `-C26` | "Cut >25bps" | **cut of MORE than 25bp: no lower bound** |
| `CUT25` | `-C25` | "Cut 25bps" | exactly -25bp |
| `HOLD` | `-H0` | "Hike 0bps" | exactly 0 |
| `HIKE25` | `-H25` | "Hike 25bps" | exactly +25bp |
| `HIKE50P` | `-H26` | "Hike >25bps" | **hike of MORE than 25bp: no upper bound** |

The label `CUT50P` / `HIKE50P` implies 50bp. The contract says ">25bps". Treating them as
exactly -/+0.50 in the replication (assumption A8) is therefore a **lower bound** on the
replication weight, and a 75bp or 100bp move would leave that leg under-hedged. The
pre-registration requires a mandatory 75bp stress row and a mandatory check that removing the
tail legs does not flip the result.

### 4.5 Fee metadata (VERIFIED: single snapshot only)

| `series_ticker` | `fee_type` | `fee_multiplier` | `tick_size` | markets |
|---|---|---|---|---|
| `KXFED` | `quadratic_with_maker_fees` | 1 | 0.010 | 76 |
| `KXFEDDECISION` | `quadratic_with_maker_fees` | 1 | 0.010 | 65 |
| `KXFEDHIKE` | `quadratic` | 1 | 0.010 | 5 |
| `KXRATECUT` | `quadratic` | 1 | 0.001 | 1 |

**This is a snapshot taken at the 2026-09-17 build. The database contains no fee history.**
Whether `KXFEDDECISION` charged maker fees, or charged the same taker formula, at any earlier
date is **NOT ESTABLISHED**. The pre-registration handles this by (i) using taker-in /
hold-to-resolution in the base case so no maker rate is needed, (ii) declaring the taker
formula an assumption stamped onto the output, and (iii) confining the maker variant to a
sensitivity with an explicitly unknown rate (assumptions A3, A4).

Note `tick_size = 0.010` dollars: the Kalshi price grid is **1 cent**. Any edge below 1 cent
is not even expressible in the quote.

### 4.6 Trade tape

`kalshi_trades` (155,004 rows, 65 markets) begins **2026-07-10 19:03:29 CDT**. It cannot
characterise depth or taker flow for anything earlier, including most of the pre-decision
window of the 2026-07-29 meeting. It carries `taker_side`, `taker_book_side`, `yes_price`,
`no_price`, `is_block_trade` and microsecond timestamps.

**Depth is not observable on either venue.** Kalshi's candle API gives top-of-book close
prices without size. This is why the pre-registration uses a volume-fraction cap as a proxy
(assumption A9), and why that proxy has no empirical grounding in fill data.

---

## 5. Polymarket (VERIFIED)

### 5.1 The 2022 markets are NOT tradable data

`poly_event_meeting` maps a primary event to meetings from 2022-03-16 onward, but **every
Polymarket market for the seven 2022 meetings has `enable_order_book = false`,
`has_prices = false`, `yes_token_id_n_rows = 0`, `all_tokens_n_rows = 0`.** These are
pre-CLOB markets with no order book and no price series. The 2022-01-26 meeting has no mapped
event at all.

**Correction to the loose claim "Polymarket reaches back to 2022": tradable Polymarket price
history starts at the 2023-02-01 meeting**, whose earliest price row is 2022-12-15.

### 5.2 Coverage by meeting (primary event, joined on `yes_token_id`)

30 meetings from 2023-02-01 to 2026-09-16 have price rows, plus three live future meetings
(2026-10-28, 2026-12-09, 2027-01-27). Representative rows:

| Meeting | outcomes | hourly rows | minute rows | first | last |
|---|---|---|---|---|---|
| 2023-02-01 | 3 | 11,684 | 683,176 | 2022-12-15 | 2023-05-17 |
| 2024-11-07 | 5 | 12,853 | 388,348 | 2024-08-02 | 2024-11-07 |
| 2025-09-17 | 4 | 12,760 | 258,970 | 2025-05-07 | 2025-09-17 |
| 2026-07-29 | 5 | 14,531 | 322,390 | 2026-03-19 | 2026-07-29 |
| 2026-09-16 | 5 | 15,016 | 323,268 | 2026-05-13 | 2026-09-16 |
| 2027-01-27 | 5 | 5,886 | 323,525 | 2026-07-29 | 2026-09-17 |

The outcome count varies by meeting (2 to 5), so the state partition is **not** constant
across the Polymarket sample. Hourly data spans the full market life; minute data is again
roughly the last 45 days per token (approximately 64,800 rows per token).

### 5.3 GOTCHA: price rows extend past the meeting

Several early meetings have `last` well after the meeting date (2023-02-01, 2023-03-22 and
2023-05-03 all run to 2023-05-17), reflecting resolution lag rather than live trading. The
harness must truncate at the announcement timestamp, not at the last available row.

### 5.4 Why Polymarket is a secondary arm only

Different venue, different (assumed zero) fee schedule over the window, different resolution
source (UMA oracle rather than Kalshi's settlement sources), different and time-varying
bucket definitions, and prices in a different collateral asset. ARM 2 characterises the
**ZQ-side instrument** and the mechanics; it does not price the Kalshi side.

---

## 6. FRED / EFFR (VERIFIED)

- `fred_rates` covers 1954-07-01 to 2026-09-17 as **calendar** rows, with `effr` non-null on
  publication days only (6,579 rows; the EFFR series begins 2000-07-03).
- **Non-publication days are NULL, not carried forward in the table.** Verified example
  around the June 2026 FOMC:

| Date | Day | `effr` | `tgt_lower` | `tgt_upper` |
|---|---|---|---|---|
| 2026-06-18 | Thursday | 3.63 | 3.50 | 3.75 |
| 2026-06-19 | Friday (Juneteenth) | **NULL** | 3.50 | 3.75 |
| 2026-06-20 | Saturday | **NULL** | 3.50 | 3.75 |
| 2026-06-21 | Sunday | **NULL** | 3.50 | 3.75 |
| 2026-06-22 | Monday | 3.63 | 3.50 | 3.75 |

  ZQ settles on an arithmetic mean over **all calendar days**, so the harness must carry the
  most recent published rate forward across weekends and holidays. This is not optional
  bookkeeping: it changes `w`.

- **Verified `w` convention in the `meetings` table.** The 2025-06-18 decision was effective
  2025-06-19, but 2025-06-19 was Juneteenth with no EFFR publication, so the first June 2025
  day carrying the new rate was 2025-06-20. The table records `effective_date = 2025-06-20`,
  `days_post = 11`, `delta = 0.366667 = 11/30`. The harness must reproduce this independently
  and assert equality (test `T-W1`).

- **Publication lag:** EFFR for day `D` is published around 09:00 ET on `D+1`. Reading
  `EFFR(D)` at any time on `D` is lookahead. This is gate G2, and the clean-spread design
  removes it structurally because the unknown pre-meeting level cancels.

- **EFFR level as of the last published value in this data set: 3.63%, target range
  3.50-3.75%.** Used as the illustrative financing rate in the cost model.

---

## 7. `cme_zq` (continuous): why it cannot substitute (VERIFIED)

`cme_zq` holds 23,955 rows of yfinance `ZQ=F`, 2000-09-01 to 2026-09-17, with an
`is_continuous` flag and a `front_contract_est` guess. It is a **single roll-stitched
series**. A calendar spread requires two simultaneously-quoted delivery months, which this
source cannot provide at any date. It therefore supports only a meeting-month outright, which
(i) requires estimating the pre-meeting EFFR level `R` and so violates G2, and (ii) suffers
the `1/w` cost blow-up.

This is the reason the 30 meetings before 2025-10-29 cannot be tested under the
pre-registered design, even though a ZQ price exists for all of them.

The `meetings.zq_source` column reads `continuous_in_month` for meetings up to 2026-07-29 and
`contract` from 2026-09-16 onward. That column reflects the state of the build **before** the
IBKR pull and must not be used by the harness to decide data availability; the harness reads
the parquet directly.

---

## 8. The `w = 0` degeneracy (VERIFIED)

Two meetings in the 2022-01 to 2026-09 window fall on the last day of their month, so the
meeting-month contract has **zero** exposure to the decision and the meeting-month hedge
ratio is undefined:

| Meeting | Meeting month | `days_post` | `w` |
|---|---|---|---|
| 2024-01-31 | 2024-01 | 0 | **0.000000** |
| 2024-07-31 | 2024-07 | 0 | **0.000000** |

For both, the `M+1` front spread has `s = 1.000000` exactly. This is the cleanest available
demonstration that the meeting-month contract is the wrong instrument.

A related and useful consequence: a meeting with `w = 0` in month `M+1` contributes **nothing**
to month `M+1`'s average, so a front spread whose partner month contains such a meeting is
still **clean**. This is why 2024-06-12 (partner July 2024, meeting on Jul 31) is a Tier-1
clean spread rather than a contaminated one.

---

## 9. Structural facts about the FOMC calendar in this window (VERIFIED)

- **No calendar month from 2022-01 to 2028-01 contains two FOMC meetings.** The `w`
  arithmetic in the pre-registration relies on this; gate test `T-G4c` asserts it.
- Meeting-free months by year (the eligible spread partners):
  - 2022: Feb, Apr, Aug, Oct
  - 2023: Jan, Apr, Aug, Oct
  - 2024: Feb, Apr, Aug, Oct
  - 2025: Feb, Apr, Aug, Nov
  - 2026: Feb, May, Aug, Nov
  - 2027: Feb, May, Aug, Nov
- **Four of the 38 in-window meetings admit no clean two-leg spread**: 2022-06-15,
  2023-06-14, 2024-12-18, 2025-06-18. The recurring pattern is a mid-June meeting sandwiched
  between May and July meetings. Roughly one meeting in ten is structurally un-hedgeable with
  a clean adjacent-month pair.
- The 2027 and 2028 meeting dates in the `meetings` table are database entries whose
  provenance is the build, not an independently re-fetched Federal Reserve publication. They
  should be re-verified against the Fed's published calendar before any live entry.
- **Unscheduled meetings are not represented anywhere in the data.** The selector assumes the
  published calendar is complete.

---

## 10. What could NOT be tested, and why: the reviewer's summary

| Question | Testable? | Blocking fact |
|---|---|---|
| Kalshi vs ZQ basis, 2022-2025 | **No** | Kalshi API purges closed-market candles (§4.1) |
| Kalshi vs ZQ basis, meetings before 2026-07-29 | **No** | same |
| Any clean calendar spread before 2025-10-29 | **No** | IBKR expired-contract retention starts LTD 2025-09-30 (§3.1) |
| 2025-09-17 meeting with a clean spread | **No** | needs the Aug-2025 contract, one month before retention |
| Polymarket vs ZQ in 2022 | **No** | 2022 Polymarket markets have no order book and no prices (§5.1) |
| Realistic ZQ execution cost | **No** | `BID_ASK` degenerate for ZQ; cost is assumption A1 (§3.5) |
| Depth / fillable size on Kalshi | **No** | candle API gives no size; trade tape starts 2026-07-10 (§4.6) |
| Depth on Polymarket | **No** | same class of limitation |
| Kalshi fee regime before 2026-09 | **No** | single fee-type snapshot, no history (§4.5) |
| Polymarket fee regime | **No** | not in data; assumption A5 |
| Behaviour under a >50bp move | **No** | tail buckets are open-ended ">25bps" (§4.4) |
| A walk-forward threshold on Kalshi | **Not yet** | `n = 2` past meetings; deferred to `n >= 6` (2027-03-17) |
| Mechanics end to end on Kalshi | **Yes** | ARM 1, 2 meetings, descriptive only |
| ZQ-side spread instrument behaviour | **Yes** | ARM 2, 8 meetings 2025-10-29 to 2026-09-16 |
| H1 itself | **Forward only** | ARM 3: 2026-10-28, 2026-12-09, 2027-01-27 |

---

## 11. Re-pull checklist (for whoever runs this next)

1. **Mar-2027 ZQ contract** before 2027-02-26, or the 2027-03-17 meeting drops out of ARM 3.
   Anchor the request at the contract's own last trade date (§3.2).
2. **Intraday 1-minute `TRADES` bars** for both legs of every ARM 1 and ARM 3 spread, on
   every candidate entry date and on announcement day. The current parquet holds **daily bars
   only**; the pre-registered G1 rule cannot run without the intraday pull. Do not pull
   `BID_ASK` (§3.5).
3. **Kalshi candles for each ARM 3 event before it closes.** After close the history is gone
   permanently. This is a standing, time-critical obligation: `KXFEDDECISION-26OCT` must be
   captured before 2026-10-28, `-26DEC` before 2026-12-09, `-27JAN` before 2027-01-27.
4. **Kalshi `fee_type` / `fee_multiplier` snapshot at every entry date**, stamped into the
   result, so the G3 anachronism never recurs.
5. **FOMC calendar re-verified** against the Federal Reserve's published calendar for 2027
   and 2028 (§9).
