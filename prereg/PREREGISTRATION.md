# Pre-Registration: Kalshi FOMC Contracts vs CBOT 30-Day Fed Funds Futures (ZQ)

**Author:** Yicheng Yang
**Status:** DESIGN DOCUMENT, written with pre-registration intent but **never frozen**.
**Date written:** 2026-09-17
**Data as of:** 2026-09-17 (see `DATA_BOUNDARIES.md` for the verified inventory)

> **Read this before citing anything below as pre-registered.** The plan here was genuinely
> written before any profit-and-loss number existed. It was never tagged, and results landed
> in this repository (`results/SYNTHESIS.md`, `results/PINNED_REGIME.md`) before any freeze
> took place. An earlier draft of this header claimed the document was frozen at a tag
> `prereg-v1`; no such tag was ever created, and the claim is withdrawn rather than
> backdated. Treat this as a design document that records what was intended in advance, and
> treat the results as what they are: exploratory, with the lookahead gates in §G applied
> after the fact rather than enforced before the first run. The gates caught two real
> lookahead bugs even used this way, which is the argument for writing them down; it is not
> an argument that the study was pre-registered.

The body below is unchanged from the version written before results existed. Any later
change appears as a dated amendment in the amendment log, with the original text intact.

---

## 0. One-paragraph summary

CBOT 30-Day Fed Funds futures (ZQ) settle on the arithmetic mean of daily EFFR over a
calendar month. A ZQ position is therefore a linear claim on FOMC decisions with a known,
calendar-determined coefficient. Kalshi's `KXFEDDECISION` series is a set of digital claims
on the same decisions. If the two venues price the same decision differently by more than the
cost of crossing both, a duration-matched package of the two is a locked-in profit in every
state of the world. This document pre-registers the exact package, the exact instrument
selection rule, the exact cost model, the exact entry threshold arithmetic, and the exact
conditions under which we will declare the idea dead. It also states, before any result is
computed, that the historical Kalshi sample is **two past meetings** and that no inference
can be drawn from it; the actual test is forward.

---

## 1. Hypothesis

### 1.1 The claim, stated so it can fail

> **H1.** Consider a package consisting of (a) Kalshi `KXFEDDECISION` legs for a single FOMC
> meeting, sized by the state-replication ratio in Section 2, entered at the **observable
> ask** (for long legs) or **observable bid** (for short legs) at a fixed daily decision
> timestamp, and (b) a duration-matched CBOT ZQ calendar spread selected by the rule in
> Section 4, entered at an observable intraday traded price plus one tick of assumed
> crossing cost. Held to resolution, this package earns a **strictly positive mean net
> profit per entered unit**, after the Kalshi taker fee, the assumed ZQ crossing cost, the
> financing cost of the Kalshi premium, and a reserve for the measured EFFR pass-through
> hedge error.

H1 is false if the mean net profit per entered unit over the pre-registered forward sample
is less than or equal to zero, or if no day in the forward sample clears the pre-derived
entry threshold at all.

### 1.2 What H1 is explicitly NOT

H1 is **not** "the two venues sometimes disagree." Venue disagreement is already
established and is uninteresting: two markets with different fee schedules, different
collateral requirements, different clienteles and different resolution sources will
disagree at all times. The question is exclusively whether the disagreement, measured at
prices a taker can actually hit, exceeds the round-trip cost of exploiting it.

H1 is also **not** "the basis has predictive content." We are not forecasting. The package
has the same payoff in every state, so there is nothing to forecast. Either the entry edge
exceeds the costs or it does not.

### 1.3 Pre-registered prior

The prior coarse pass (next-month outright, post-previous-effective-date window, daily
settles on both legs, Polymarket as the prediction-market leg) found 2 of 9 meetings with a
positive median net daily edge, an overall median day of **-1.97 cents**, and 37.5% of days
net positive. Two meetings were positive: 2026-07-29 at +7.69 cents and 2026-09-16 at
+4.55 cents median. That pass paired a Kalshi/Polymarket close that is eight to nine hours
*later* than the ZQ settle it was compared against, which is the exact defect Gate G1 is
built to remove. The honest prior is therefore: **the coarse result is uninformative in
both directions**, and the pre-derived threshold of roughly 4.5 to 6.8 cents (Section 6)
sits above the coarse median day and below the coarse best-meeting number. We expect the
answer to be close to zero and we have written the kill conditions accordingly.

---

## 2. State-replication mathematics

Reproduced in full, not cited.

### 2.1 ZQ settlement and units

ZQ for delivery month `M` settles at

```
    F_settle(M) = 100 - mean( EFFR(d) : d in all calendar days of M )
```

where the mean is arithmetic over **calendar** days and a non-publication day (weekend, or
a banking holiday such as Juneteenth) carries forward the most recent published EFFR.

Contract economics:

```
    1 basis point of rate   = $41.67 per contract
    1 price point (=100bp)  = $4,167 per contract      (denote M$ = 4167)
    minimum tick            = 0.0025 price points = 0.25bp = $10.4175 per contract
```

### 2.2 Decision weight `w`

For a meeting on date `D` in month `M`, with the new target effective from the first EFFR
publication date at the new rate, define

```
    w(M) = (number of calendar days of M whose applicable EFFR reflects the new target)
           / (number of calendar days in M)
```

The numerator counts a weekend or holiday as carrying the previous publication day's rate.
Worked: the 2025-06-18 decision was effective 2025-06-19, but 2025-06-19 was Juneteenth and
no EFFR was published, so the first day of June 2025 carrying the new rate was 2025-06-20,
giving 11 of 30 days and `w = 0.366667`. This is the convention already encoded in the
`meetings` table (`days_post / days_in_month`), and the harness must reproduce it
independently and assert equality (test T-W1).

### 2.3 Two-state prices and the given hedge ratio

Let `R` be the EFFR prevailing into the meeting. For the **meeting-month** contract:

```
    S_0 = 100 - R                (hold)
    S_1 = 100 - R - 0.25 * w     (hike 25bp)
    h   = S_0 - S_1 = 0.25 * w   (the two-state span, in price points)
```

CME's normalized state price for a tradable ZQ price `F`:

```
    q_CME = (S_0 - F) / (S_0 - S_1)
```

Hedge ratio: `N = M$ * h = 4167 * h` Kalshi contracts per 1 ZQ. Buy `N` Kalshi HIKE25 YES
and buy 1 ZQ (a hike makes ZQ fall).

**Payoff identity, derived.** Let `a` be the executed Kalshi ask (dollars).

```
  HOLD state:  M$*(S_0 - F) - N*a
  HIKE state:  M$*(S_1 - F) + N*(1 - a) = M$*(S_1 - F) + N - N*a
  Difference:  M$*(S_0 - S_1) - N = M$*h - M$*h = 0
```

The two states pay the same. Substituting `S_0 - F = q_CME * h`:

```
  Gross P&L (both states) = M$*h*q_CME - N*a = N*(q_CME - a)
```

which is the identity asserted in the brief. The package is a pure basis trade: it pays
the difference between the ZQ-implied state price and the Kalshi executed price, times the
number of Kalshi contracts, in every state.

### 2.4 Generalisation the harness actually implements

The two-state form above is a special case and is not sufficient in a regime where cuts,
holds and hikes are all live simultaneously. The harness implements the general form.

Let the traded ZQ structure be `X` with fair value

```
    X = A - s * Delta
```

where `Delta` is the decision's change in the target (percentage points, e.g. +0.25 for a
25bp hike), `s` is the **span** in [0,1], and `A` is a constant independent of `Delta`.
Section 4 gives `A` and `s` for each admissible structure. For the meeting-month outright,
`A = 100 - R` and `s = w`, so `h = 0.25 * s` reduces to `0.25 * w` exactly as in 2.3.

Kalshi's `KXFEDDECISION` event partitions the decision into five mutually exclusive,
exhaustive outcomes with rate changes `Delta_j`:

| DB outcome | Kalshi subtitle | `Delta_j` used | exact? |
|---|---|---|---|
| `CUT50P`  | "Cut >25bps"  | -0.50 | **NO: open-ended lower bound** |
| `CUT25`   | "Cut 25bps"   | -0.25 | yes |
| `HOLD`    | "Hike 0bps"   |  0.00 | yes |
| `HIKE25`  | "Hike 25bps"  | +0.25 | yes |
| `HIKE50P` | "Hike >25bps" | +0.50 | **NO: open-ended upper bound** |

Hold `n_j` Kalshi YES contracts on outcome `j` and one unit of `X`. In state `j` the
package is worth `n_j + M$*(A - s*Delta_j)`. For this to be state-independent:

```
    n_j = M$ * s * (Delta_j - Delta_ref)         for any chosen reference state
```

A positive `n_j` is a long YES position executed at that leg's **ask**; a negative `n_j` is
a short, executed on Kalshi by buying NO at the NO ask, which is economically a short of
YES at `yes_bid` (Kalshi quotes `no_ask = 100c - yes_bid`). Both prices are observable in
`kalshi_candles` (`ask_close`, `bid_close`).

**Pre-registered choice of `Delta_ref` and of which legs to trade** (fixed now, no fitting):

1. A leg is **live** on a given day if its `ask_close` is **>= 2 cents**. Justification from
   first principles: the Kalshi tick is 1 cent, so a leg quoted at 1 cent sits at the
   minimum expressible price and is not distinguishable from zero; 2 cents is the smallest
   strictly-above-tick price. This is not a tuned parameter.
2. `Delta_ref` is the `Delta` of the live leg with the **highest ask**. That leg gets
   `n_j = 0` and is not traded, which minimises both contracts traded and fees paid.
3. Dead legs (ask < 2 cents) are not traded. Their unhedged exposure is reported in a
   mandatory stress row, not netted away.

When exactly two legs are live and they are HOLD and HIKE25, this reduces **exactly** to
Section 2.3.

**Profit of the general package.** With `x` the executed price of `X`:

```
  Profit = M$*(A - x) - sum_j n_j * a_j
         = M$ * s * [ (A - x)/s  -  sum_j Delta_j * a_j ]
         = M$ * s * [ E_ZQ[Delta] - E_Kalshi[Delta] ]
```

So the edge is the **ZQ-implied expected move minus the Kalshi ask-weighted expected
move**. Define the reference unit as `n_25 = M$ * s * 0.25 = 1041.75 * s` contracts (the
size of a 25bp leg). Then, with `e_bp` the venue disagreement in basis points of expected
move,

```
  Profit per 25bp-equivalent contract = 4 * e_bp  cents
```

i.e. **one basis point of expected-move disagreement is worth four cents per contract.**
This is the unit every number in Sections 6 and 7 is quoted in. Cross-check against 2.3:
in the two-state case `E_ZQ - E_K = 0.25*(q_CME - a)`, so `e_bp = 25*(q_CME - a)` and
`4*e_bp = 100*(q_CME - a)` cents, identical.

### 2.5 Announcement jump is the surprise, with the 2026-07-29 verification

Before the announcement, `X` trades at `A - s*E_pre[Delta]`. Immediately after, in realised
state `j`, it is `A - s*Delta_j`. So

```
    dX_j = s * ( E_pre[Delta] - Delta_j )
```

For the two-state hold-vs-hike case with `k = 25bp * s` and pre-announcement hike
probability `p_pre`:

```
    dF_hold = + k * p_pre
    dF_hike = - k * (1 - p_pre)
```

**Verification, 2026-07-29 (a hold).** The Kalshi-implied pre-meeting expected move on
2026-07-28 was **6.158bp**. The instrument was the August 2026 ZQ outright, for which
`s = 1` (August 2026 contains no FOMC meeting and the July decision is effective
2026-07-30, before August starts), so `k = 25bp` and the predicted hold jump is
`+6.158bp`. Observed at 13:00 CT: **96.3000 -> 96.3625 = +6.25bp**. Residual **+0.09bp**,
which is `p_pre = 6.158/25 = 0.2463`. Note the sign: the market was pricing a *hike* with
roughly 25% probability into that meeting, the Fed held, and the contract *rose*.

Context from the daily settles of ZQQ26 (IBKR, verified): 2026-07-28 settle 96.2950,
2026-07-29 settle 96.3650. The daily settles bracket the intraday 13:00 CT print; the
verification number above is the intraday move, not the settle-to-settle move, and the
harness must not substitute one for the other (test T-G1c).

The harness must reproduce this verification as a regression test (test T-S1) and must
additionally check the spread analogue: for the pre-registered Jul/Aug front spread
(`s = 29/31 = 0.935484`), the predicted hold jump is `0.935484 * 6.158 = 5.761bp` of
spread.

### 2.6 EFFR pass-through, and why a hedge-error reserve is needed

The Kalshi leg resolves on the *target range*. ZQ settles on *realised EFFR*. The link
between them is an empirical regularity, not an identity.

Established, reproduced from FRED over 32 meetings 2022-2025:

- 17 of 17 policy changes passed through 1:1 at 1bp publication precision.
- Post-meeting mean EFFR residual: **MAE 0.085654bp, max 0.75bp**.
- Normalised to a $1-per-25bp digital: **MAE 0.343 cents, max 3.00 cents**.

The pre-2022 regime is different (2015-2021 one-day pass-through MAE about 0.93bp; the
September 2019 repo episode reached 10bp), so 2022 onward is the only sample used.

This residual is a real, unavoidable cost of the hedge and is carried as an explicit
reserve in Section 6. It also means the package is **not** a true arbitrage: it is a basis
trade with a small, measured, bounded residual risk.

---

## 3. Sample universe

Every FOMC meeting from 2022-01 through 2026-09-17. **38 meetings. No silent drops.**
The `Instrument` column is computed by the Section 4 rule, which is a pure function of the
FOMC calendar and is therefore computable for every row regardless of whether data exists.

Legend for data columns:
`K` = Kalshi price data in `kalshi_candles`.
`P` = Polymarket tradable price data (order book enabled and price rows present).
`Z` = both legs of the selected spread present in the IBKR per-contract ZQ parquet.
Instrument codes: `F` = front spread (long M+1, short M), `B` = back spread (long M, short
M-1), `F*`/`B*` = contaminated (partner month contains a meeting with non-zero weight).

| # | Meeting | `w(M)` | Instrument | `s` | K | P | Z | Arm / exclusion |
|---|---|---|---|---|---|---|---|---|
| 1 | 2022-01-26 | 0.161290 | F Jan/Feb22 | 0.838710 | no | no | no | E1,E2,E3 |
| 2 | 2022-03-16 | 0.483871 | F Mar/Apr22 | 0.516129 | no | no | no | E1,E2,E3 |
| 3 | 2022-05-04 | 0.870968 | B Apr/May22 | 0.870968 | no | no | no | E1,E2,E3 |
| 4 | 2022-06-15 | 0.500000 | F* Jun/Jul22 | 0.500000 | no | no | no | E1,E2,E3,E5 (c/s=0.258065) |
| 5 | 2022-07-27 | 0.129032 | F Jul/Aug22 | 0.870968 | no | no | no | E1,E2,E3 |
| 6 | 2022-09-21 | 0.300000 | F Sep/Oct22 | 0.700000 | no | no | no | E1,E2,E3 |
| 7 | 2022-11-02 | 0.933333 | B Oct/Nov22 | 0.933333 | no | no | no | E1,E2,E3 |
| 8 | 2022-12-14 | 0.548387 | F Dec22/Jan23 | 0.451613 | no | no | no | E1,E2,E3 |
| 9 | 2023-02-01 | 0.964286 | B Jan/Feb23 | 0.964286 | no | yes | no | E1,E3 |
| 10 | 2023-03-22 | 0.290323 | F Mar/Apr23 | 0.709677 | no | yes | no | E1,E3 |
| 11 | 2023-05-03 | 0.903226 | B Apr/May23 | 0.903226 | no | yes | no | E1,E3 |
| 12 | 2023-06-14 | 0.533333 | B* May/Jun23 | 0.533333 | no | yes | no | E1,E3,E5 (c/s=0.181452) |
| 13 | 2023-07-26 | 0.161290 | F Jul/Aug23 | 0.838710 | no | yes | no | E1,E3 |
| 14 | 2023-09-20 | 0.333333 | F Sep/Oct23 | 0.666667 | no | yes | no | E1,E3 |
| 15 | 2023-11-01 | 0.966667 | B Oct/Nov23 | 0.966667 | no | yes | no | E1,E3 |
| 16 | 2023-12-13 | 0.580645 | F Dec23/Jan24 | 0.419355 | no | yes | no | E1,E3 |
| 17 | 2024-01-31 | 0.000000 | F Jan/Feb24 | 1.000000 | no | yes | no | E1,E3 |
| 18 | 2024-03-20 | 0.354839 | F Mar/Apr24 | 0.645161 | no | yes | no | E1,E3 |
| 19 | 2024-05-01 | 0.967742 | B Apr/May24 | 0.967742 | no | yes | no | E1,E3 |
| 20 | 2024-06-12 | 0.600000 | F Jun/Jul24 | 0.400000 | no | yes | no | E1,E3 |
| 21 | 2024-07-31 | 0.000000 | F Jul/Aug24 | 1.000000 | no | yes | no | E1,E3 |
| 22 | 2024-09-18 | 0.400000 | F Sep/Oct24 | 0.600000 | no | yes | no | E1,E3 |
| 23 | 2024-11-07 | 0.766667 | B Oct/Nov24 | 0.766667 | no | yes | no | E1,E3 |
| 24 | 2024-12-18 | 0.419355 | F* Dec24/Jan25 | 0.580645 | no | yes | no | E1,E3,E5 (c/s=0.111111) |
| 25 | 2025-01-29 | 0.064516 | F Jan/Feb25 | 0.935484 | no | yes | no | E1,E3 |
| 26 | 2025-03-19 | 0.387097 | F Mar/Apr25 | 0.612903 | no | yes | no | E1,E3 |
| 27 | 2025-05-07 | 0.774194 | B Apr/May25 | 0.774194 | no | yes | no | E1,E3 |
| 28 | 2025-06-18 | 0.366667 | F* Jun/Jul25 | 0.633333 | no | yes | no | E1,E3,E5 (c/s=0.050934) |
| 29 | 2025-07-30 | 0.032258 | F Jul/Aug25 | 0.967742 | no | yes | no | E1,E3 |
| 30 | 2025-09-17 | 0.433333 | B Aug/Sep25 | 0.433333 | no | yes | no | E1,E3 (Aug-2025 contract not retained) |
| 31 | 2025-10-29 | 0.064516 | F Oct/Nov25 | 0.935484 | no | yes | **yes** | **ARM 2** |
| 32 | 2025-12-10 | 0.677419 | B Nov/Dec25 | 0.677419 | no | yes | **yes** | **ARM 2** |
| 33 | 2026-01-28 | 0.096774 | F Jan/Feb26 | 0.903226 | no | yes | **yes** | **ARM 2** |
| 34 | 2026-03-18 | 0.419355 | B Feb/Mar26 | 0.419355 | no | yes | **yes** | **ARM 2** |
| 35 | 2026-04-29 | 0.033333 | F Apr/May26 | 0.966667 | no | yes | **yes** | **ARM 2** |
| 36 | 2026-06-17 | 0.433333 | B May/Jun26 | 0.433333 | no | yes | **yes** | **ARM 2** |
| 37 | 2026-07-29 | 0.064516 | F Jul/Aug26 | 0.935484 | **yes** | yes | **yes** | **ARM 1 + ARM 2** |
| 38 | 2026-09-16 | 0.466667 | B Aug/Sep26 | 0.466667 | **yes** | yes | **yes** | **ARM 1 + ARM 2** |

### 3.1 Exclusion codes, stated once, with counts

- **E1: No Kalshi price data (API purge).** 36 of 38. Kalshi's public API serves candle
  history only for markets that had not yet closed at pull time. Of the 38 in-window
  meetings, only `KXFEDDECISION-26JUL` and `KXFEDDECISION-26SEP` were still open when the
  database was built (2026-09-17). For those two, hourly and daily candles reach back to
  **2025-09-29** and minute candles to 45 days before close. For every earlier meeting
  there is **zero** Kalshi price data and no way to recover it from this source.
- **E2: No Polymarket tradable prices.** 8 of 38. The 2022-01-26 meeting has no mapped
  Polymarket event at all. The seven 2022 meetings from 2022-03-16 to 2022-12-14 have
  mapped events, but every one of their markets has `enable_order_book = false`,
  `has_prices = false` and zero price rows: these were pre-CLOB markets. Polymarket
  tradable history therefore starts at the **2023-02-01** meeting, not 2022.
- **E3: No per-contract ZQ for both spread legs.** 30 of 38. IBKR retains expired ZQ only
  back to last-trade-date **2025-09-30**; the earliest delivery month in the parquet is
  2025-09. The first meeting whose selected spread has both legs retained is **2025-10-29**
  (needs Oct-2025 and Nov-2025). The 2025-09-17 meeting fails by one contract: its selected
  instrument is the Aug/Sep-2025 back spread and the August 2025 contract is gone.
- **E4: Continuous ZQ=F cannot substitute.** The `cme_zq` table (yfinance `ZQ=F`,
  2000-09-01 to 2026-09-17) is a single roll-stitched series. A calendar spread requires two
  simultaneously-quoted delivery months. The continuous series therefore supports only a
  meeting-month outright, which (i) requires estimating `R` and so violates G2, and (ii)
  suffers the 1/`w` cost blow-up of Section 4.4. Meetings 1 to 30 are consequently reported,
  if at all, as a **G2-EXPOSED descriptive arm** and are never evidence for or against H1.
- **E5: Contaminated spread.** 4 of 38 (rows 4, 12, 24, 28). No adjacent month is free of
  a non-zero-weight meeting, so the selected spread carries residual exposure to a second
  decision. The contamination ratio `c/s` is reported per row. These are analysed separately
  and never pooled with clean-spread meetings.

### 3.2 The three arms, declared now

- **ARM 1: primary (Kalshi x IBKR spread): n = 2.** Meetings 2026-07-29 and 2026-09-16.
  **Pre-registered as descriptive only.** Two meetings cannot support an inference about a
  mean. Any per-day statistics within these two meetings are severely clustered: ARM 1 has
  hundreds of pre-decision days but an effective sample size of two. The harness must report
  meeting-clustered standard errors and must print the sentence "n = 2; not evidence" on
  every ARM 1 summary.
- **ARM 2: secondary (Polymarket x IBKR spread): n = 8.** Meetings 2025-10-29 through
  2026-09-16. Used to characterise the basis process, the size of the spread structure's
  tracking error, and the feasibility of the mechanics. **Not** evidence about Kalshi:
  different venue, different (assumed zero) fee schedule, different resolution source,
  different bucket definitions.
- **ARM 3: forward, committed now.** 2026-10-28, 2026-12-09, 2027-01-27 are committed as
  the out-of-sample test with the instruments and thresholds already fixed below. This is
  the arm H1 will actually be judged on. 2027-03-17 requires an additional IBKR pull
  (the Mar-2027 contract is not yet in the parquet) and is committed conditional on that
  pull being completed before 2027-02-26.
  **Resolution of the ARM 3 count, fixed now so it cannot be chosen later:** ARM 3 is
  three meetings if the Mar-2027 pull does not happen in time, and four if it does. The
  "3 of the ARM 3 meetings" requirement in Sections 7.4 (K7) and 8.2 therefore means 3 of 3
  in the first case and 3 of 4 in the second. The decision on the pull must be recorded,
  dated, in the amendment log **before** the 2026-10-28 meeting, not after results exist.

---

## 4. Instrument selection rule

### 4.1 The rule (pure function of the published FOMC calendar)

The hedge instrument `X` is **always long the later contract and short the earlier contract
of an adjacent delivery-month pair**, so that `X = A - s*Delta` with `s > 0`. Two candidates
exist for a meeting in month `M`:

```
  FRONT:  X = F(M+1) - F(M)      s = 1 - w(M)     c = w(M+1)
  BACK:   X = F(M)   - F(M-1)    s = w(M)         c = 1 - w(M-1)
```

`c` is the contamination coefficient: the unhedged loading on the adjacent month's decision.
`c = 0` when the adjacent month contains no FOMC meeting, and also when it contains a
meeting whose own weight is zero or one (a meeting on the final day of a month contributes
nothing to that month's average).

**Selection, lexicographic, evaluated on the FOMC calendar as published at the entry date:**

1. **Tier 1.** Among candidates with `c = 0`, take the one with the larger `s`. Ties go to
   FRONT (it has no entry-window constraint, see 4.3).
2. **Tier 2.** If no candidate is clean, take the candidate with the smaller `c/s` and label
   the meeting `CONTAMINATED`, reporting `c/s`. Tier-2 meetings are never pooled with Tier-1.
3. **Tier 3.** If neither spread's legs are available in the data, fall back to an outright
   and print `G2 EXPOSED` on that meeting's row. Prefer the `M+1` outright (`s = 1 - w(M+1)`
   contribution aside, `s = 1` when M+1 is clean) over the `M` outright (`s = w(M)`).

Spreads are preferred over outrights **unconditionally**, even when the outright has a
larger `s`, because in a clean spread the unknown pre-meeting EFFR level `R` cancels
identically (4.2) and G2 disappears.

Nothing in this rule looks at prices, volumes, or which contract "worked."

### 4.2 Why `R` cancels in a clean spread (derivation)

Let `r0` be the rate applying to the earlier of the two months throughout, and let `Delta`
be the meeting's change.

*Clean FRONT (`M+1` meeting-free):* every day of `M+1` is at `r0 + Delta`, so
`F(M+1) = 100 - r0 - Delta`. Month `M` averages `r0 + Delta*w`, so
`F(M) = 100 - r0 - Delta*w`. Then

```
  X = F(M+1) - F(M) = -Delta*(1 - w)      =>  A = 0,  s = 1 - w
```

*Clean BACK (`M-1` meeting-free):* every day of `M-1` is at `r0`, so
`F(M-1) = 100 - r0`. Then

```
  X = F(M) - F(M-1) = -Delta*w            =>  A = 0,  s = w
```

`r0` cancels in both. Crucially this holds **regardless of when the position is opened**,
including before the *previous* FOMC meeting, because any earlier decision is effective
before both delivery months begin and is therefore fully baked into `r0` for both legs. The
prior pass's restriction of the window to "after the previous meeting's effective date" is
therefore unnecessary under the spread design and is dropped. Sanity check on sign: a hike
pushes both contracts down but pushes the later one down more, so `X` falls on a hike,
consistent with `X = A - s*Delta`.

*Outright:* `A = 100 - R`, so `q_CME` cannot be computed without an estimate of `R` at the
decision timestamp, and the only estimate available in real time uses EFFR published with a
one-day lag. That is G2.

### 4.3 Entry-window constraint on BACK spreads

The `M-1` leg of a back spread stops trading at the end of month `M-1`, which is always
before the meeting. Therefore **a back spread can only be opened on or before the last trade
date of the `M-1` contract.** Once that leg expires it cash-settles at `100 - r0`, which
contains no `Delta` exposure, so the *position* survives intact: the remaining short `M` leg
plus the banked settlement still delivers `-Delta*w` plus a known constant. Only the
*opening* is constrained.

Front spreads have both legs live through the announcement and have no entry-window
constraint, but the near (`M`) leg is within days of expiry at the announcement when `w` is
small, so liquidity in that leg should be assumed to be thinner than the deferred leg.

### 4.4 Why the meeting-month contract is the wrong instrument

Per-Kalshi-contract ZQ crossing cost, derived. Crossing `X` once costs `t` price points, so
`M$ * t` dollars per unit of `X`. One unit of `X` hedges `n_25 = M$ * 0.25 * s` Kalshi
contracts. So

```
  ZQ cost per Kalshi contract = (M$ * t) / (M$ * 0.25 * s) = t / (0.25*s) dollars
                              = 400 * t / s  cents
                              = 1.00 / s     cents   when t = one tick = 0.0025
```

The cost scales as `1/s`. With the meeting-month outright, `s = w`, and `w` is frequently
tiny. Two meetings in this universe have `w = 0` exactly (2024-01-31 and 2024-07-31): the
meeting-month contract has **zero** exposure to the decision and the hedge ratio is
undefined. For 2026-10-28 the meeting-month outright has `w = 3/31 = 0.096774`, giving
**10.33 cents** of ZQ crossing cost per Kalshi contract, against **1.11 cents** for the
Oct/Nov front spread.

### 4.5 Worked example: 2026-10-28

FOMC 2026 meetings: Jan 28, Mar 18, Apr 29, Jun 17, Jul 29, Sep 16, Oct 28, Dec 9.

- `M` = October 2026, 31 days. Effective 2026-10-29. Days at the new rate: Oct 29, 30, 31 =
  3. `w = 3/31 = 0.096774`.
- FRONT candidate: `M+1` = November 2026. November 2026 contains no FOMC meeting, so
  `c = 0`. `s = 1 - 3/31 = 28/31 = 0.903226`.
- BACK candidate: `M-1` = September 2026, which contains the 2026-09-16 meeting with
  `w(Sep26) = 14/30`, so `c = 1 - 14/30 = 0.533333 != 0`. Not clean.
- **Selected: Tier 1 FRONT, long ZQX26 (Nov-2026) / short ZQV26 (Oct-2026), `s = 0.903226`.**

Fair value: `X = F(Nov26) - F(Oct26) = -Delta * (1 - 3/31) = -Delta * 28/31`. The unknown
post-September EFFR level cancels entirely, exactly as asserted in the brief.

Hedge ratio: `n_25 = 1041.75 * 0.903226 = 940.94`, i.e. **941 Kalshi contracts per one
spread unit** on a 25bp leg.

### 4.6 Worked example: 2026-12-09

- `M` = December 2026, 31 days. Effective 2026-12-10. Days at the new rate: Dec 10 to Dec 31
  = 22. `w = 22/31 = 0.709677`.
- FRONT candidate: `M+1` = January 2027, which contains the 2027-01-27 meeting with
  `w(Jan27) = 4/31 = 0.129032`, so `c = 0.129032 != 0`. Not clean. (Explicitly: the
  Dec/Jan spread would equal `Delta_Dec*(9/31) + Delta_Jan*(4/31)`: a smaller span *and* a
  second unknown. It is strictly worse than both alternatives.)
- BACK candidate: `M-1` = November 2026, meeting-free, so `c = 0`. `s = 22/31 = 0.709677`.
- **Selected: Tier 1 BACK, long ZQZ26 (Dec-2026) / short ZQX26 (Nov-2026), `s = 0.709677`.**
- **Entry window closes at the last trade date of ZQX26, 2026-11-30.** This is 9 days before
  the meeting. After that date the meeting is not enterable under this design and every
  later day must be recorded as `WINDOW CLOSED`, not as a zero-edge day.

Fair value: `X = F(Dec26) - F(Nov26) = -Delta * 22/31`. `R` cancels.

Hedge ratio: `n_25 = 1041.75 * 0.709677 = 739.31`, i.e. **739 Kalshi contracts per spread
unit**.

---

## 5. The seven lookahead gates

Each gate is stated as (a) the rule the harness implements and (b) the test that proves it.
Every test named here must exist in `tests/` and must be run in CI before any result is
published. A failing gate test invalidates the run.

### G1: Timestamp alignment

**Rule.**

1. A single **decision clock** is defined: `t* = 14:00:00 America/New_York` on every ZQ
   trading day. It is inside regular trading hours for both venues and precedes the CME
   daily settlement.
2. The ZQ leg is priced from **1-minute TRADES bars**: for each delivery month, the last bar
   with `bar_end <= t*`. The spread price is the difference of the two legs' prices taken at
   the same `t*`. If intraday bars are unavailable for a date, **that date is dropped and
   counted in the exclusion ledger**: it is never back-filled with the daily settle.
3. The Kalshi leg is priced from the candle whose `bar_end_utc <= t*`: the 60-minute bar
   ending 14:00 ET, or the 1-minute bar ending 14:00 ET where minute data exists.
4. Bar-labelling conventions are reconciled explicitly and asserted, not assumed. Verified
   facts: Kalshi `ts` is the **bar end** (the `kalshi_candles.bar_end_utc` column confirms
   daily bars end at 04:00 UTC = 00:00 ET); IBKR daily bars carry the trade date and an
   expired contract receives one trailing zero-volume, zero-`barCount` stub bar repeating the
   final settle, which must be dropped.
5. **Mandatory robustness variant:** rerun with one additional bar of lag on the Kalshi leg
   (`bar_end <= t* - 60min`). If the sign of the headline result differs between the base and
   the lagged variant, the result is declared **NOT ROBUST** and reported as such.

**Why this matters, quantified.** Kalshi's daily bar for trade date `D` ends at 00:00 ET on
`D+1`. The CME settle for `D` is in the afternoon of `D`. Pairing the two hands the Kalshi
leg roughly eight to nine hours of extra information about the same day. That is the defect
in the prior coarse pass.

**Tests.**
- `T-G1a`: assert `kalshi_candles.bar_end_utc` equals `to_timestamp(ts)` for all rows and
  that every `interval=1440` bar ends at 00:00 America/New_York.
- `T-G1b`: inject a synthetic Kalshi price jump at 20:00 ET on day `D`; assert the harness's
  day-`D` panel value is unchanged and the day-`D+1` value moves.
- `T-G1c`: assert the harness never reads a ZQ daily `settle` field on any path used for
  entry pricing (static check plus a runtime guard that raises on access).
- `T-G1d`: assert every trailing bar with `volume == 0 and barCount == 0` is dropped, and
  assert the dropped count equals the count of contracts whose max date exceeds their last
  trade date.

### G2: EFFR publication lag

**Rule.** EFFR for day `D` is published around 09:00 ET on `D+1`. No quantity computed at
time `t` on day `D` may read `EFFR(D)`. The **primary defence is structural**: Tier-1 and
Tier-2 instruments are spreads in which `R` cancels identically (4.2), so no EFFR level is
ever needed at entry. EFFR is used only (i) to compute realised settlement in the
hold-to-settlement exit variant, which is a post-hoc valuation, and (ii) as the financing
rate in the cost model, where the harness must use the most recently **published** EFFR, i.e.
`EFFR(D-1)` at the earliest, with weekends and holidays carried forward.

Whenever a Tier-3 outright is used, the harness **must print `G2 EXPOSED`** on that meeting's
row in the output, and that meeting is excluded from the headline.

**Tests.**
- `T-G2a`: a runtime guard on the `fred_rates` accessor that raises if a caller requests
  `effr` for a date `>= ` the caller's as-of date.
- `T-G2b`: assert that for every Tier-1/Tier-2 meeting the entry edge is numerically
  invariant to adding an arbitrary constant to every EFFR value in the sample (the `R`
  cancellation, tested by perturbation).
- `T-G2c`: assert the string `G2 EXPOSED` appears in the output for exactly the set of
  Tier-3 meetings.

### G3: Fee-schedule anachronism

**Rule.** The fee regime is applied **as of the trade date** or is declared an assumption on
the face of the output. What is verified: as of the 2026-09-17 snapshot, series `KXFED` and
`KXFEDDECISION` carry `fee_type = 'quadratic_with_maker_fees'`, `fee_multiplier = 1`,
`tick_size = 0.01`. That is a **single snapshot**; the database contains no fee history.

Consequences, pre-registered:

- The base case uses **taker on entry and hold-to-resolution on the Kalshi leg**, so the
  maker fee schedule is not used at all in the base case and the anachronism risk is
  confined to the taker formula.
- The taker formula `ceil(0.07 * C * p * (1-p))` is applied to both ARM 1 meetings. Both are
  within three months of the snapshot, so the anachronism risk is small but **not zero**, and
  the output must carry the line "Kalshi taker fee schedule assumed constant from the
  2026-09-17 snapshot back to 2026-06-01; not independently verified."
- Any extension of ARM 1 to a meeting before 2026-06-01 requires re-deriving the fee regime
  from a dated source first.
- The maker variant (Section 6.6) is a **sensitivity only** and its maker rate is an
  explicit assumption with a stated range, because the rate is not in the data.
- The Polymarket arm (ARM 2) is charged **zero taker fee**, which is an assumption about
  Polymarket's fee history over 2025-10 to 2026-09 and is flagged as such. A mirror run
  charging the Kalshi-equivalent fee is mandatory.

**Tests.**
- `T-G3a`: assert the harness refuses to run on any meeting whose trade date falls outside
  the declared fee-assumption window unless an explicit override flag is passed, and that the
  override stamps a warning into the output.
- `T-G3b`: assert `fee(C, p) == ceil(0.07*C*p*(1-p)*100)/100` in dollars for a table of
  hand-computed cases, including `p = 0.5` giving 1.75 cents per contract.

### G4: Contract selection is a pure function of the calendar

**Rule.** The instrument selector takes exactly two inputs: the list of FOMC meeting dates
(and their effective dates) as published at the entry date, and the delivery-month calendar.
It takes **no** price, volume, open-interest or P&L input. It is implemented as a pure
function with no access to the price tables.

**Tests.**
- `T-G4a`: the selector module is imported in a restricted namespace with the price tables
  monkey-patched to raise on access; the full universe selection must complete.
- `T-G4b`: assert the selector's output for all 38 rows of the Section 3 table matches the
  table byte-for-byte (the table is the fixture).
- `T-G4c`: assert no calendar month in 2022-01 to 2028-01 contains two FOMC meetings (an
  assumption the `w` arithmetic relies on). If this ever fails, the `w` definition must be
  generalised before the harness runs.
- `T-G4d`: assert the selector produces identical output when fed the calendar truncated to
  information available one year before each meeting (the Fed publishes the calendar roughly
  two years ahead).

### G5: Universe declaration

**Rule.** The harness emits an **exclusion ledger**: one row per (meeting, date) pair in the
declared window, with status in {`ENTERED`, `NO_EDGE`, `WINDOW_CLOSED`, `NO_ZQ_INTRADAY`,
`NO_KALSHI_QUOTE`, `LEG_ILLIQUID`, `EXCLUDED_E1..E5`}. The counts in the ledger must sum to
the full cross-product. A meeting that produces no trades appears in the output with
`n_entries = 0`, never by omission.

**Tests.**
- `T-G5a`: assert `sum(ledger.count) == n_meetings_in_window * n_dates_in_window` after the
  documented per-meeting date restriction is applied, and that every status value is one of
  the enumerated set.
- `T-G5b`: assert the 38 meetings of Section 3 all appear in the ledger.

### G6: The threshold is not fitted

**Rule.** The entry threshold is the arithmetic of Section 6, committed in this document
before any P&L is computed. The harness reports:

1. The headline result at the **pre-derived threshold only**. This is the only number that
   counts as evidence.
2. A full threshold-versus-P&L curve for transparency, printed under the heading
   "DESCRIPTIVE: NOT EVIDENCE."
3. A walk-forward variant (estimate the threshold on meetings 1..k, test on k+1).
   **Pre-registered as deferred**: with `n = 2` past Kalshi meetings a walk-forward is
   meaningless. It executes automatically once `n >= 6` Kalshi meetings are complete, which
   the published calendar puts at the **2027-03-17** meeting (26JUL, 26SEP, 26OCT, 26DEC,
   27JAN, 27MAR). Pretending to walk forward on n = 2 would be worse than not doing it.

**Tests.**
- `T-G6a`: the threshold function is pure in `(p, s, n_cross, tick, reserve, effr, H)` and
  is imported in a namespace where P&L is not computable.
- `T-G6b`: assert the threshold values the harness computes for `s` in
  {1, 29/31, 28/31, 27/31, 22/31, 14/31, 14/30} equal the hard-coded table in Section 6.5
  to within 0.001 cents.
- `T-G6c`: assert the walk-forward routine raises `NotEnoughMeetings` for `n < 6`.

### G7: Execution and depth

**Rule.**

- **Kalshi size cap.** Position size on any Kalshi leg is capped at **10% of that market's
  observed candle volume `vol` on that day**, at the chosen bar interval, aggregated over the
  day up to `t*`. If the cap is below `n_25` for the meeting, the day is recorded as
  `LEG_ILLIQUID` and produces **no** P&L; it is not silently scaled down. Sensitivity over
  {2%, 5%, 10%, 25%} is mandatory.
- **Kalshi price.** Long legs execute at `ask_close`, short legs at `bid_close`. No mid, ever.
  Depth at the quote is not observable, which is why the volume cap exists as a proxy.
- **ZQ cost.** The ZQ bid-ask is **not observable historically** (IBKR `BID_ASK` bars for ZQ
  are degenerate: open equals close on every bar). The crossing cost is therefore an
  **assumption**: one minimum tick, 0.25bp, $10.4175 per contract per crossing. Mandatory
  sensitivity over {1 tick, 2 ticks} per crossing, where the 2-tick case also covers legging
  a calendar spread rather than trading it as a listed instrument (the ZQ calendar-spread
  tick size is itself not verified here and is treated as equal to the outright tick).
- **ZQ depth.** Observed front-month ZQ daily volumes in this data set are of order 10^5
  contracts, so ZQ depth is not the binding constraint at the one-to-ten lot scale implied by
  the Kalshi cap. The harness still records ZQ volume per entry for the record.

**Tests.**
- `T-G7a`: assert no entry's Kalshi contract count exceeds the cap, for every cap setting.
- `T-G7b`: assert the harness never reads `price_close` or `mid` on an entry path (a runtime
  guard); `price_close` is null on every zero-volume bar and is a trade print, not a
  hittable quote.
- `T-G7c`: assert the reported number of `LEG_ILLIQUID` days is non-zero-checked and appears
  in the ledger.

### G8 (additional): Exit variants

Not in the original seven, but required by the brief and pre-registered here.

The Kalshi leg resolves on announcement day; the ZQ leg settles at month end. Both are
modelled:

- **Variant A: close ZQ at the announcement.** Exit both ZQ legs from 1-minute TRADES bars
  on announcement day, using the last bar ending at or before **13:05 CT** (five minutes
  after the 13:00 CT release, to allow the print to exist), plus one assumed tick per
  crossing. Two crossings total (entry plus exit). For a back spread the `M-1` leg has
  already cash-settled, so only the `M` leg is crossed at exit; the entry is still one
  spread crossing, so the crossing count is 2 in both cases.
- **Variant B: hold ZQ to final settlement.** No exit crossing. Each leg settles at
  `100 - mean(EFFR over its delivery month)` computed from `fred_rates` with carry-forward.
  One crossing total. Carries EFFR path risk for the remainder of the delivery months, which
  is precisely what the hedge-error reserve covers.

Both variants are reported side by side for every meeting. **Neither is the headline; both
must be positive for H1 to be supported.**

**Test.** `T-G8a`: assert Variant A and Variant B agree to within the measured hedge-error
max (3.00 cents) on every meeting where the announcement-day ZQ price and the realised
settlement are both available; a larger divergence indicates a bug in the `w` arithmetic or
in the EFFR carry-forward, not a trading result.

---

## 6. Cost model and the pre-derived entry threshold

All costs are quoted in **cents per 25bp-equivalent Kalshi contract**, the unit defined in
Section 2.4, so they are directly comparable to the gross edge `4 * e_bp`.

### 6.1 C1: Kalshi taker fee

Kalshi's quadratic taker fee, rounded up at the order level:

```
    fee($) = ceil( 0.07 * C * p * (1-p) )
```

Per contract that is `7 * p * (1-p)` cents, where `p` is the executed price in dollars
(`ask_close` for a long leg, `bid_close` for a short leg). Reference values:

| `p` | 0.50 | 0.40 | 0.25 | 0.10 | 0.05 |
|---|---|---|---|---|---|
| cents/contract | **1.7500** | 1.6800 | 1.3125 | 0.6300 | 0.3325 |

The order-level ceiling adds at most one cent spread over `n_25` contracts, which at
`n_25` of order 500 to 1000 is under 0.002 cents per contract and is immaterial. The harness
uses the unrounded per-contract figure and asserts the rounding difference is below 0.01
cents (test T-G3b).

Because the Kalshi leg is **held to resolution**, there is no exit fee and no exit spread.
That is the reason the design holds rather than round-trips.

**Headline value used below: `p = 0.50`, the maximum of the quadratic, i.e. 1.7500 cents.**
This is deliberately the worst case. The harness evaluates C1 at the observed `p` per
observation, which is fitting-free because `p` is observable at the decision timestamp.

### 6.2 C2: ZQ crossing cost (ASSUMPTION)

There is no historical ZQ bid-ask in any available source. The crossing cost is assumed to
be **one minimum tick per crossing**: 0.0025 price points = 0.25bp = **$10.4175 per
contract**. From Section 4.4, per Kalshi contract this is

```
    C2 = n_cross * (1.00 / s)  cents,    with n_cross = 2 (Variant A) or 1 (Variant B)
```

**Sensitivity to be reported: {1 tick, 2 ticks} per crossing.** The 2-tick case doubles C2
and also stands in for legging the spread instead of trading the listed calendar spread.

### 6.3 C3: EFFR hedge-error reserve

From the measured 2022-2025 pass-through study, normalised to a $1-per-25bp digital:

```
    C3 (base)   = 0.343 cents   (measured MAE)
    C3 (stress) = 3.000 cents   (measured max)
```

This is a reserve, not a fee: it is the expected absolute tracking error of the ZQ leg
against the Kalshi leg's resolution. It is charged as a cost so that a package whose edge is
smaller than its own tracking error is never entered.

### 6.4 C4: Financing on the Kalshi premium

Kalshi requires full collateral on a long YES position. The premium `p` is tied up from entry
until the announcement. Charged at the **most recently published** EFFR (G2-compliant):

```
    C4 = 100 * p * EFFR * H / 365  cents,   H = calendar days from entry to announcement
```

Illustration with the observed EFFR of **3.63%** (the published rate through June 2026, with
a target range of 3.50-3.75%):

| `p` | `H` = 30 | `H` = 90 | `H` = 180 | `H` = 300 |
|---|---|---|---|---|
| 0.50 | 0.1492 | **0.4475** | 0.8951 | 1.4918 |
| 0.25 | 0.0746 | 0.2238 | 0.4475 | 0.7459 |

This term is material and was absent from the prior coarse pass: a position opened 300 days
before the meeting bears 1.49 cents of financing on a 50-cent contract, which is a third of
the entire base threshold. **It is the single strongest argument for entering close to the
meeting rather than early.** ZQ margin financing is **not** charged (an assumption that is
favourable to the strategy and is flagged as such on the output).

**Headline value used below: `p = 0.50`, `H = 90`, EFFR 3.63% => 0.4475 cents.**

### 6.5 The pre-derived threshold, committed before any P&L

```
  T(p, s, n_cross, H) = 7*p*(1-p)            [C1, Kalshi taker fee]
                      + n_cross * (1.00/s)   [C2, ZQ crossing, 1 tick assumed]
                      + 0.343                [C3, measured EFFR hedge-error MAE]
                      + 100*p*EFFR*H/365     [C4, financing on Kalshi premium]
                      + 0                    [no discretionary margin of safety]
```

The discretionary margin of safety is **explicitly zero**. Any non-zero value would be a free
parameter and this document does not grant one.

**Committed threshold table. Base case: Variant A (`n_cross = 2`), 1 tick, `p = 0.50`,
EFFR 3.63%, `H = 90`. Cents per 25bp-equivalent contract; the bp column is the same number
divided by 4, i.e. the required venue disagreement in basis points of expected move.**

| Meeting | Instrument | `s` | C1 | C2 | C3 | C4 | **T (cents)** | **T (bp)** |
|---|---|---|---|---|---|---|---|---|
| 2025-10-29 | F Oct/Nov25 | 0.935484 | 1.750 | 2.1379 | 0.343 | 0.4475 | **4.678** | 1.170 |
| 2025-12-10 | B Nov/Dec25 | 0.677419 | 1.750 | 2.9524 | 0.343 | 0.4475 | **5.493** | 1.373 |
| 2026-01-28 | F Jan/Feb26 | 0.903226 | 1.750 | 2.2143 | 0.343 | 0.4475 | **4.755** | 1.189 |
| 2026-03-18 | B Feb/Mar26 | 0.419355 | 1.750 | 4.7692 | 0.343 | 0.4475 | **7.310** | 1.827 |
| 2026-04-29 | F Apr/May26 | 0.966667 | 1.750 | 2.0690 | 0.343 | 0.4475 | **4.609** | 1.152 |
| 2026-06-17 | B May/Jun26 | 0.433333 | 1.750 | 4.6154 | 0.343 | 0.4475 | **7.156** | 1.789 |
| **2026-07-29** | F Jul/Aug26 | 0.935484 | 1.750 | 2.1379 | 0.343 | 0.4475 | **4.678** | 1.170 |
| **2026-09-16** | B Aug/Sep26 | 0.466667 | 1.750 | 4.2857 | 0.343 | 0.4475 | **6.826** | 1.707 |
| **2026-10-28** | F Oct/Nov26 | 0.903226 | 1.750 | 2.2143 | 0.343 | 0.4475 | **4.755** | 1.189 |
| **2026-12-09** | B Nov/Dec26 | 0.709677 | 1.750 | 2.8182 | 0.343 | 0.4475 | **5.359** | 1.340 |
| **2027-01-27** | F Jan/Feb27 | 0.870968 | 1.750 | 2.2963 | 0.343 | 0.4475 | **4.837** | 1.209 |
| *(reference)* | *clean, `s`=1* | 1.000000 | 1.750 | 2.0000 | 0.343 | 0.4475 | **4.541** | 1.135 |

**Variant B (hold ZQ to settlement, `n_cross = 1`), same other parameters:**

| `s` | 1.000000 | 0.935484 | 0.903226 | 0.870968 | 0.709677 | 0.466667 |
|---|---|---|---|---|---|---|
| **T (cents)** | 3.541 | 3.609 | 3.648 | 3.689 | 3.950 | 4.683 |

**Stress case (2 ticks per crossing, C3 = 3.000 max, `H` = 300, Variant A):**

| `s` | 1.000000 | 0.935484 | 0.903226 | 0.870968 | 0.709677 | 0.466667 |
|---|---|---|---|---|---|---|
| **T (cents)** | 10.242 | 10.518 | 10.670 | 10.834 | 11.878 | 14.813 |

**These numbers are committed. They will not be changed after P&L is seen.**

Read them against the Kalshi tick: the base threshold of roughly 4.5 to 4.8 cents on a
high-`s` meeting is between four and five Kalshi ticks. The strategy needs the two venues to
disagree by a little over one basis point of expected policy move, sustained long enough to
hit an ask. That is a demanding but not absurd bar. The two `s < 0.5` meetings
(2026-03-18, 2026-06-17, 2026-09-16) need a disagreement of 1.7 to 1.8bp and should be
expected to qualify far less often.

### 6.6 Assumptions register for Section 6

| ID | Assumption | Value | Sensitivity to report |
|---|---|---|---|
| A1 | ZQ crossing cost | 1 tick = 0.0025 pts = $10.4175 | {1, 2} ticks per crossing |
| A2 | ZQ calendar spread tick equals the outright tick | 0.0025 pts | covered by A1's 2-tick case |
| A3 | Kalshi taker fee schedule constant back to 2026-06-01 | `0.07*C*p*(1-p)` | flagged; no numeric range available |
| A4 | Kalshi maker fee rate (maker variant only) | **not in data** | report at 0, 0.25c, 0.50c per contract |
| A5 | Polymarket taker fee over ARM 2 window | 0 | mirror run at Kalshi-equivalent fee |
| A6 | ZQ margin financing | not charged | qualitative flag only |
| A7 | Financing rate on Kalshi premium | published EFFR (3.63% illustrative) | {published EFFR, 0} |
| A8 | `CUT50P`/`HIKE50P` treated as exactly -/+0.50 | see 2.4 | mandatory 75bp stress row |
| A9 | Fill at `ask_close`/`bid_close` in the size permitted by the volume cap | 10% of daily volume | {2, 5, 10, 25}% |

---

## 7. Pre-registered decision rule

### 7.1 Entry condition

On each ZQ trading day `D` in a meeting's admissible window, at `t* = 14:00:00 ET`:

1. Select the instrument by Section 4. If Tier 3, record `G2 EXPOSED` and skip for the
   headline.
2. If `D` is after the back-spread entry window (4.3), record `WINDOW_CLOSED` and stop.
3. Read the ZQ leg prices from 1-minute TRADES bars with `bar_end <= t*`. If unavailable,
   record `NO_ZQ_INTRADAY` and stop.
4. Read the Kalshi candles with `bar_end_utc <= t*`. Determine live legs (ask >= 2 cents),
   set `Delta_ref` to the highest-ask live leg, compute `n_j`.
5. Compute `E_ZQ[Delta] = (A - x)/s` (with `A = 0` for a spread) and
   `E_Kalshi[Delta] = sum_j Delta_j * p_j` with `p_j` the executable price per leg.
6. Gross edge in cents: `e = 400 * (E_ZQ[Delta] - E_Kalshi[Delta])`.
7. Compute `T` from Section 6.5 using the observed `p` of the traded leg, `s`, `n_cross`,
   and `H` = days from `D` to the announcement.
8. **Enter if and only if `e > T`.** Direction: if `e > 0` the ZQ leg is cheap relative to
   Kalshi, so buy `X` and take the Kalshi side given by the signs of `n_j`; if `e < -T` the
   mirror package is entered with all signs flipped and the same threshold applied to `|e|`.
9. Apply the G7 size cap. If the cap is below `n_25`, record `LEG_ILLIQUID` and do not enter.

### 7.2 Sizing

One unit is one spread unit of `X` against `n_25 = 1041.75 * s` Kalshi contracts on a 25bp
leg (and `2 * n_25` on an open-ended tail leg, when live). At most **one unit per meeting
per day**, and at most **one open unit per meeting at a time**: re-entry on a later day is
permitted only if no unit is open. This prevents a single meeting's persistent basis from
being counted many times and masquerading as many independent observations.

### 7.3 Exit

Both variants are run for every entry and both are reported.

- **Variant A:** ZQ legs closed from 1-minute TRADES bars, last bar ending at or before
  13:05 CT on announcement day, plus one assumed tick per crossing. Kalshi resolves at the
  announcement.
- **Variant B:** ZQ legs held to final settlement at `100 - mean(EFFR over delivery month)`.
  Kalshi resolves at the announcement.

No discretionary early exit exists. There is no stop-loss: the package is state-independent
by construction, so a mark-to-market drawdown before resolution is not information.

### 7.4 Kill conditions, as numbers

| ID | Condition | Action |
|---|---|---|
| K1 | Aggregate net P&L per entered unit over the first three ARM 3 meetings (2026-10-28, 2026-12-09, 2027-01-27) is `<= 0` | H1 rejected; stop |
| K2 | Any single meeting's realised net P&L per unit is worse than `-3 * T` for that meeting (e.g. worse than **-14.26 cents** at `s = 0.903226`) | Halt; re-derive the hedge error before any further entry |
| K3 | Realised EFFR hedge error on any meeting exceeds **3.00 cents** (the measured 2022-2025 max) | Pass-through assumption broken; halt and re-estimate on the extended sample |
| K4 | Kalshi `fee_type != 'quadratic_with_maker_fees'` or `fee_multiplier != 1` at entry | Halt; re-derive the cost model |
| K5 | An unscheduled FOMC meeting, or an IORB / administered-rate technical adjustment that moves EFFR without a Kalshi-resolving event, occurs inside the delivery months of a live package | Close at market; record the meeting as excluded with reason; do not count the P&L |
| K6 | Fill-feasibility rate (10% of daily Kalshi volume `>= n_25`) below **50%** of otherwise-qualifying days | Declared capacity-infeasible regardless of paper P&L |
| K7 | Fewer than **3** of the ARM 3 meetings produce any qualifying day | Declared opportunity-infeasible: the basis does not exceed costs often enough to be a strategy |

K5 deserves emphasis. The 17-of-17 one-to-one pass-through result covers *policy* changes.
The Fed has historically adjusted IORB without changing the target range (2019 and 2021).
Such an adjustment moves EFFR, and therefore ZQ, with **no** corresponding Kalshi event. The
Kalshi leg cannot hedge it. This is a genuine, unhedgeable tail in the design and is the
most likely mechanism by which a "risk-free" package loses real money.

---

## 8. Falsification, and what does not count as support

### 8.1 What falsifies H1

- Mean net P&L per entered unit over ARM 3 `<= 0` under **either** exit variant (K1).
- Zero qualifying days across the ARM 3 meetings: the basis never exceeds the pre-derived
  threshold, so there is no trade. This is a **definitive negative**, not an inconclusive
  result, and will be reported as such.
- Any kill condition K2 through K7 triggering.

### 8.2 What will NOT be accepted as support, stated in advance

1. **Concentration in one meeting.** At least **3 of the ARM 3** meetings must be
   individually net positive. A single large winner with the rest negative is a right-tail
   draw, not an edge. This is the same failure mode already documented in this researcher's
   prior work on Seeking Alpha pick studies and on the World Cup arbitrage micro-test.
2. **Profit only below the pre-derived threshold.** If the headline at `T` from Section 6.5
   is not positive but some lower threshold is, that is curve-fitting and will be reported
   as a negative result with the curve attached under the "NOT EVIDENCE" heading.
3. **Sign flip under the one-extra-bar Kalshi lag** (G1 variant). Declared NOT ROBUST.
4. **Sign flip under the 2-tick ZQ assumption** (A1 sensitivity). The 1-tick assumption is
   the most favourable plausible value; a result that survives only there is not a result.
5. **Size that the volume cap cannot support** (K6). Paper edge at infeasible size is not an
   edge.
6. **Profit driven by the open-ended `CUT50P` / `HIKE50P` legs.** These legs are ">25bps",
   not "exactly 50bps" (verified from the Kalshi subtitles). Their replication weight is a
   lower bound. If removing them from the package flips the sign, the result is an artefact
   of mis-specified tail weights (A8) and must be reported as such.
7. **Anything from ARM 1 alone.** `n = 2`. Two meetings cannot establish a positive mean.
   ARM 1 exists to prove the mechanics run end to end and to catch bugs, not to answer H1.
8. **Anything from ARM 2 transplanted to Kalshi.** Polymarket is a different venue with a
   different fee assumption (A5), a different resolution source and different buckets. ARM 2
   characterises the ZQ-side instrument; it does not price the Kalshi side.
9. **Variant A positive and Variant B negative, or vice versa.** Both must hold. A design
   that only works under one exit convention has an unmodelled cost hiding in the other.

### 8.3 The most likely honest outcome, stated now

Given that the prior coarse pass produced a median day of -1.97 cents against a threshold
here of 4.5 to 6.8 cents, and given that the coarse pass was biased *in favour* of finding
edge by the G1 timestamp defect, the most probable result is **H1 rejected, with very few or
zero qualifying days**. Writing that down now is the point of pre-registering.

---

## 9. Known limitations

Stated plainly, before results, so that none of them can be presented later as a discovery.

1. **No historical ZQ bid-ask, anywhere.** IBKR `whatToShow="BID_ASK"` for ZQ returns
   degenerate bars (open equals close on every bar). The entire ZQ execution cost is
   assumption A1. If the true ZQ spread is habitually wider than one tick in the deferred
   leg of a calendar spread, every number in this document is optimistic.
2. **No depth on either leg.** Kalshi's API gives the top-of-book close, not size. Polymarket
   likewise. The 10%-of-daily-volume cap is a proxy with no empirical grounding in fill data.
3. **`n` is tiny.** ARM 1 has two past meetings. ARM 3 commits three forward meetings before
   2027-02. A positive result on five meetings total is a weak result, and will be labelled
   weak.
4. **Kalshi history is short and irrecoverable.** Kalshi's API purges candle history for
   closed markets. The 36 excluded meetings cannot be recovered from this source at any
   price, so the Kalshi arm cannot be extended backwards; it can only be extended forwards,
   one meeting every six to eight weeks.
5. **The pre-2026 fee regime is an assumption.** The database contains a single fee-type
   snapshot (2026-09-17). Nothing establishes what Kalshi charged in 2025.
6. **Polymarket fee history is an assumption** (A5) and Polymarket's bucket definitions and
   resolution source differ from Kalshi's.
7. **Open-ended tail buckets.** `>25bps` in both directions has no upper bound. The
   replication weight of +/-0.50 is a lower bound (A8) and a 75bp move would leave the
   package unhedged on that leg.
8. **Administered-rate risk is unhedgeable** (K5). An IORB technical adjustment moves ZQ
   without moving any Kalshi outcome.
9. **Unscheduled meetings are not in the calendar.** The selector assumes the published
   calendar is complete. March 2020 is the counterexample.
10. **Four meetings admit no clean spread** (E5). For them the design carries a second,
    unhedged decision, and the results are segregated.
11. **`w = 0` meetings break the outright entirely.** Two meetings in this universe
    (2024-01-31, 2024-07-31) have zero meeting-month weight. The Tier-3 fallback is undefined
    for them. This is documented rather than patched, because Tier 3 is never the headline.
12. **Liquidity of the near leg at expiry.** A front spread's near leg is within days of
    expiry at the announcement when `w` is small. Its quotes are assumed as good as the
    deferred leg's, which is unlikely to be true.
13. **Financing on ZQ margin is not charged** (A6), which favours the strategy.
14. **The EFFR pass-through study is a 2022-2025 in-sample statistic** applied to 2026
    forward. The 2015-2021 regime had roughly eleven times the one-day MAE. There is no
    guarantee the current regime persists, and K3 is the tripwire.
15. **The 2027 and 2028 FOMC dates in the `meetings` table are calendar entries whose
    provenance is the database build, not an independently fetched Federal Reserve
    publication.** Test T-G4d guards the selector's dependence on them; the dates themselves
    should be re-verified against the Fed's published calendar before any ARM 3 entry.

---

## 10. Amendment log

**2026-09-17** — Yicheng Yang. Status header rewritten. The original claimed the document was
frozen at a git tag `prereg-v1`. No such tag was ever created, and results were committed
before any freeze, so the claim was false as written and is withdrawn rather than backdated.
Nothing in the body (§1 through §9) was edited.
