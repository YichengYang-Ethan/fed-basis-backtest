> Historical exploratory study, retained for provenance. This is not the current account replay. See the [current overview](../README.md), [version history](../CHANGELOG.md) and [limitations](../docs/LIMITATIONS.md) before citing its conclusions.

# Measured Tracking Error of the ZQ Calendar-Spread Hedge

**Author:** Yicheng Yang
**Status:** MEASUREMENT. Computed before any P&L exists, as an input to the pre-registered
entry threshold. This document resolves BLOCKER B6 against `PREREGISTRATION.md` (WIP commit
`38483d6`, explicitly UNFROZEN).
**Date:** 2026-09-17
**Inputs:** `fed.duckdb` tables `fred_rates` (calendar-day EFFR, IORB, target range) and
`meetings` (FOMC dates, effective dates, realized target changes). No price data of any kind
is used, so nothing here can be contaminated by the basis it will be used to threshold.
**Outputs:** `results/tracking_error_by_pair.csv` (55 rows, one per adjacent delivery-month
pair).

---

## 1. What B6 said and what is wrong with the current reserve

Section 6.3 of the pre-registration charges

```
    C3 (base)   = 0.343 cents     = 4 x 0.085654bp
    C3 (stress) = 3.000 cents     = 4 x 0.75bp
```

sourced from a study of **target-to-EFFR pass-through**: how closely realized EFFR follows the
target range after a decision. That statistic is measured correctly and this document
reproduces it (Section 6: meeting-month level residual MAE **0.0963bp** over 37 meetings,
against the quoted 0.0857bp). The problem is not the arithmetic. The problem is that it is
the wrong statistic for the instrument the pre-registration actually trades, and it is
normalized with the wrong factor.

A Tier-1 instrument is a **calendar spread**, not a level. Section 4.2 shows that the
pre-meeting rate `R` cancels identically in a clean spread. That cancellation is exactly why
a *level* pass-through residual is not the spread's exposure: a level error common to both
delivery months cancels with `R`. What survives is the **difference in intra-month EFFR drift
between the two delivery months**. That quantity is measured nowhere in the
pre-registration. It is measured here.

Two further defects follow:

1. **No span scaling.** The conversion from a spread residual in bp to cents per Kalshi
   contract is `4/s`, not `4`. Section 6.3 uses the `s = 1` case for instruments whose `s`
   runs as low as 0.419.
2. **Wrong regime.** The 2022-2025 sample is dominated by months in which EFFR printed the
   identical value on every calendar day. **33 of the 56 delivery months** in 2022-01 ..
   2026-08 have exactly one distinct EFFR print, which makes the flat-rate model exactly
   right by construction. That regime ended in September 2025.

---

## 2. The quantity measured

### 2.1 Definitions and sign conventions

ZQ for delivery month `m` settles at `F(m) = 100 - mean_effr(m)`, where `mean_effr(m)` is the
arithmetic mean of EFFR over **all calendar days** of `m`, a non-publication day carrying
forward the last published rate (CME convention, Section 2.1).

For an adjacent pair with earlier month `E` and later month `L = E + 1`, the traded structure
is always long the later contract and short the earlier one (Section 4.1):

```
    X(E,L) = F(L) - F(E) = mean_effr(E) - mean_effr(L)
```

**Flat-rate model.** Anchor at the last published EFFR strictly before month `E` begins, call
it `r0`, and step the path by each meeting's *realized* target change on that meeting's
effective date:

```
    model_effr(d) = r0 + sum{ Delta_k : effective_date_k <= d, effective_date_k > anchor }
    model_mean(m) = mean of model_effr over the calendar days of m
    X_model(E,L)  = model_mean(E) - model_mean(L)
```

For a clean FRONT this reduces to `-Delta * (1 - w)` and for a clean BACK to `-Delta * w`,
i.e. `-Delta * s`, exactly as Section 4.2 derives.

**Residual.**

```
    resid_bp = 100 * ( X_realized - X_model )
             = drift(E) - drift(L)
    where drift(m) = 100 * ( mean_effr(m) - model_mean(m) )
```

This is the intra-month drift differential. A positive `resid_bp` means the realized spread
settled above the flat-rate prediction, which pays the long-`X` side and costs the short-`X`
side. Because Section 7.1 enters the mirror package when the edge is negative, the reserve is
charged on `|resid_bp|`.

**The anchor cancels.** `r0` enters `model_mean(E)` and `model_mean(L)` identically, so it
drops out of `X_model`. This is verified numerically: re-anchoring every pair 45 days earlier
leaves `resid_bp` unchanged to `1e-9` on all 55 pairs. The measurement therefore inherits the
G2 immunity of the instrument itself and does not depend on any choice of "pre-meeting EFFR".

On contaminated pairs the model includes the partner month's realized decision as well, so
`resid_bp` measures tracking error only. Contamination (`c/s`) remains a separate, already
pre-registered exposure and is not double counted here.

### 2.2 Normalization to cents, with the arithmetic

The pre-registration's unit is **cents per 25bp-equivalent Kalshi contract** (Section 2.4).
Derivation, once:

```
  ZQ:      1 price point   = M$ = $4,167 per contract = 100bp of rate
           so 1bp of rate  = $41.67 per contract
           one unit of X (long L, short E) moves $41.67 per bp of X

  Kalshi:  n_25 = M$ * s * 0.25 = 4167 * 0.25 * s = 1041.75 * s contracts   (Section 2.4)

  A residual of R bp in X's terminal value costs 41.67 * R dollars on the ZQ leg,
  borne across 1041.75 * s Kalshi contracts:

       41.67 * R / (1041.75 * s)  =  0.04 * R / s  dollars  =  4 * R / s  cents
```

**Convention used throughout this document:**

```
    C3(s) = 4 * R_bp / s        cents per 25bp-equivalent contract
```

Two independent cross-checks:

- At `s = 1` and `R = 0.085654bp` this gives **0.3426 cents**, which is the existing
  `C3 (base) = 0.343`. The current reserve is the `s = 1` special case of this formula applied
  to the level residual.
- `harness/panel.py` already carries `Instrument.anchor_sensitivity_cents_per_bp = 100 / h_bp`
  with `h_bp = 25 * s` (price move per +25bp decision, in bp of rate). `100 / (25 * s) = 4/s`.
  Same factor.

**Correction to the review brief.** The brief states "1bp of span residual = `100/s` cents".
That is the normalization per **1bp-equivalent** leg (`n_1 = M$ * s * 0.01 = 41.67 * s`
contracts, giving `41.67*R/(41.67*s) = 100*R/s` cents). The pre-registration's unit is the
**25bp-equivalent** contract, which is 25 times larger, so in the document's own unit the
factor is `100/(25*s) = 4/s`. Using `100/s` would overstate the reserve by 25x. Everything
below uses `4/s`.

---

## 3. Data, and the integrity checks that were run first

| Check | Result |
|---|---|
| `fred_rates` is a dense calendar grid, no missing dates | pass |
| My calendar-day forward fill vs the database's own `v_effr_calendar` ASOF join | max abs diff **0.0** |
| My forward-filled EFFR vs FRED `DFF`, 2022-01-01 onward | **0** mismatched days of 1,719 |
| `w(M) = days_post / days_in_month` reproduced independently for all 49 meetings | max diff **0.0** |
| Realized EFFR steps by exactly `Delta` on `effective_date` | 17 rate changes, **0** off by more than 0.51bp |
| Anchor invariance of `resid_bp` (re-anchor 45 days earlier) | pass on all 55 pairs |
| Section-4 selector reproduces the Section 3 instrument table | **0** mismatches on 16 spot-checked meetings |

EFFR is published through **2026-09-15**, so the last fully realized delivery month is
**2026-08**. The sweep covers every adjacent pair from **2022-01/2022-02 to 2026-07/2026-08**,
**n = 55**, no drops.

**An exact identity, discovered in the data.** On all 55 pairs, to 0.0000bp:

```
    resid_bp  ==  mean(EFFR - IORB)_E  -  mean(EFFR - IORB)_L
```

This holds whenever IORB steps by the same amount as the target on the same dates, which it
did throughout the sample. It is not a coincidence, it is the mechanism: **the calendar
spread's tracking error is the month-over-month change in where EFFR sits inside the target
range.** Two consequences. First, the reserve can be monitored forward from `fred_rates` alone
with no ZQ data. Second, the EFFR-IORB spread is a directly observable leading indicator of
the reserve going stale.

---

## 4. Worked example, end to end: the 2025-10-29 meeting

Selected instrument by Section 4: **Tier-1 FRONT, long ZQX25 (Nov-2025) / short ZQV25
(Oct-2025)**.

**Step 1 - calendar.** Meeting 2025-10-29, effective 2025-10-30. October has 31 days; days
carrying the new rate are Oct 30 and 31.

```
    w(Oct25) = 2/31 = 0.064516        s = 1 - w = 29/31 = 0.935484
```

November 2025 contains no FOMC meeting, so `c = 0` and the FRONT is clean.

**Step 2 - anchor.** Last published EFFR strictly before October: `EFFR(2025-09-30) = 4.09`.
Realized change `Delta = -0.25` effective 2025-10-30.

**Step 3 - model means.**

```
    model_mean(Oct25) = (29 * 4.09 + 2 * 3.84) / 31 = (118.61 + 7.68)/31 = 126.29/31 = 4.073871
    model_mean(Nov25) = 3.84                                    (all 30 days at the new rate)
    X_model = 4.073871 - 3.840000 = 0.233871 pp = +23.3871bp
```

Cross-check against `-Delta * s`: `0.25 * 0.935484 = 0.233871`. Identical.

**Step 4 - realized means.** October 2025 daily EFFR, calendar-day forward filled:

```
    4.09 on Oct 1-7   (7 days)     4.10 on Oct 8-15  (8 days)
    4.11 on Oct 16-26 (11 days)    4.12 on Oct 27-29 (3 days)
    3.87 on Oct 30    (1 day)      3.86 on Oct 31    (1 day)          total 31

    sum = 7(4.09) + 8(4.10) + 11(4.11) + 3(4.12) + 3.87 + 3.86
        = 28.63 + 32.80 + 45.21 + 12.36 + 3.87 + 3.86 = 126.73
    mean_effr(Oct25) = 126.73 / 31 = 4.088065
```

November 2025:

```
    3.86 on Nov 1-2 (weekend, carried from Oct 31)   3.87 on Nov 3-12 (10 days)
    3.88 on Nov 13-27 (15 days)                      3.89 on Nov 28-30 (3 days)   total 30

    sum = 2(3.86) + 10(3.87) + 15(3.88) + 3(3.89) = 7.72 + 38.70 + 58.20 + 11.67 = 116.29
    mean_effr(Nov25) = 116.29 / 30 = 3.876333
```

**Step 5 - residual.**

```
    X_realized = 4.088065 - 3.876333 = 0.211731 pp = +21.1731bp
    resid_bp   = 21.1731 - 23.3871   = -2.2140bp

    decomposition:  drift(Oct) = 100*(4.088065 - 4.073871) = +1.4194bp
                    drift(Nov) = 100*(3.876333 - 3.840000) = +3.6333bp
                    resid      = 1.4194 - 3.6333 = -2.2140bp            (checks)

    identity:  mean(EFFR-IORB)_Oct = -4.5806bp,  mean(EFFR-IORB)_Nov = -2.3667bp
               difference = -2.2140bp                                    (checks)
```

**Step 6 - cents.**

```
    C3 realized = 4 * |-2.2140| / 0.935484 = 8.8559 / 0.935484 = 9.4667 cents
```

**9.47 cents on the meeting whose pre-registered total threshold is 4.678 cents.** The hedge
error alone was twice the entire entry bar, and 27.6x the 0.343-cent reserve booked against it.
Nothing was mispriced: EFFR simply crept from 6bp below IORB at the start of October to 3bp
below by the end, and continued to 2bp below through November. The flat-rate model is what is
approximate.

---

## 5. (Task item 3) Full distribution of the drift differential

All 55 adjacent delivery-month pairs, 2022-01/2022-02 .. 2026-07/2026-08. `mean` and `sd` are
of the **signed** residual; `MAE`, `p50`, `p90`, `p95`, `max` are of `|resid|`.

| Sample | n | mean | MAE | sd | p50 | p90 | p95 | max |
|---|---|---|---|---|---|---|---|---|
| **All pairs** | 55 | -0.0921 | **0.1752** | 0.4912 | 0.0000 | 0.3467 | 1.2681 | **2.2140** |
| Pinned regime 2022-01 .. 2025-08 | 44 | -0.0083 | 0.0455 | 0.1137 | 0.0000 | 0.2055 | 0.3292 | 0.3667 |
| **Drifting regime 2025-09 .. 2026-07** | 11 | -0.4273 | **0.6939** | 1.0453 | 0.2409 | 2.1194 | 2.1667 | **2.2140** |

All figures in **bp of the spread**.

The pooled statistic is not a description of anything. It is a mixture of two regimes:

- **Pinned (2022-01 to 2025-08).** EFFR sat at a single value for an entire month in most
  months, so the flat-rate model is exactly right by construction. 33 of 56 delivery months in
  the full window have exactly one distinct EFFR print. MAE 0.046bp.
- **Drifting (2025-09 onward).** EFFR began migrating inside the target range as reserve
  conditions changed. MAE 0.694bp, **15x** the pinned regime.

**The drifting-regime residuals are one-signed within an episode, not independent noise:**

| pair | resid_bp | mean(EFFR-IORB) early | late |
|---|---|---|---|
| 2025-09/2025-10 | **-2.1194** | -6.70 | -4.58 |
| 2025-10/2025-11 | **-2.2140** | -4.58 | -2.37 |
| 2025-11/2025-12 | **-1.3667** | -2.37 | -1.00 |
| 2025-12/2026-01 | 0.0000 | -1.00 | -1.00 |
| 2026-01/2026-02 | 0.0000 | -1.00 | -1.00 |
| 2026-02/2026-03 | 0.0000 | -1.00 | -1.00 |
| 2026-03/2026-04 | 0.0000 | -1.00 | -1.00 |
| 2026-04/2026-05 | **+1.2258** | -1.00 | -2.23 |
| 2026-05/2026-06 | +0.2409 | -2.23 | -2.47 |
| 2026-06/2026-07 | -0.2731 | -2.47 | -2.19 |
| 2026-07/2026-08 | -0.1935 | -2.19 | -2.00 |

Seven nonzero, five negative and two positive, and the three largest share a sign and a cause.
Successive meetings are therefore **not independent draws** and the reserve must not be scaled
down by averaging across meetings. A persistently one-signed residual is also precisely the
signature that `harness/panel.py` already warns can masquerade as a persistent basis.

### 5.1 The worst five pairs, with causes

| rank | pair | resid_bp | drift E | drift L | EFFR-IORB E -> L | cents on the selected instrument | cause |
|---|---|---|---|---|---|---|---|
| 1 | **2025-10/2025-11** | -2.2140 | +1.42 | +3.63 | -4.58 -> -2.37 | 9.467c (F, s=0.935) | Money-market firming. EFFR stepped 4.09 -> 4.10 -> 4.11 -> 4.12 monotonically through October (6 distinct prints) and 3.87 -> 3.89 through November (4 prints), i.e. a secular climb through the target range as reserves drained. **Not** a month-end effect. |
| 2 | **2025-09/2025-10** | -2.1194 | +0.30 | +2.42 | -6.70 -> -4.58 | 14.960c (F, s=0.567) / 131.4c (B, s=0.065) | Onset of the same episode. September was pinned at -7/-6bp; October averaged -4.58bp. Both candidate structures are contaminated, so neither is the Tier-1 selection, but the BACK figure shows the `1/s` blow-up in the extreme: `s = w(Oct25) = 2/31`. |
| 3 | **2025-11/2025-12** | -1.3667 | +1.63 | +3.00 | -2.37 -> -1.00 | 8.070c (B, s=0.677) | Tail of the same episode; December pinned at -1bp. This is the selected instrument for the 2025-12-10 meeting. |
| 4 | **2026-04/2026-05** | +1.2258 | 0.00 | -1.23 | -1.00 -> -2.23 | 5.072c (F, s=0.967) | **Sign reversal.** EFFR eased back down through the range during May 2026: 3.64 until May 6, 3.63 from May 7, 3.62 from May 19 (neither date is an FOMC effective date). April printed a single value all month. This is the selected instrument for the 2026-04-29 meeting. |
| 5 | **2023-05/2023-06** | +0.3667 | 0.00 | -0.37 | -7.00 -> -7.37 | 2.750c (B, s=0.533, Tier 2) | A 1bp softening on scattered June 2023 days (5.07 against 5.08), including the Juneteenth carry-forward (Mon 2023-06-19 repeats 5.08 from Fri 06-16 while 06-20 printed 5.07) and 06-29. An order of magnitude below the 2025-26 episode. |

**Causes explicitly ruled out.**

- **Month-end / quarter-end spikes are not the driver.** Over the 39 rate-change-free months
  in 2022-01 .. 2026-08, the last-3-calendar-day mean minus the rest-of-month mean has mean
  **+0.032bp** and sd **0.325bp**, and quarter-end months (**+0.021bp**, n=9) are no worse than
  non-quarter-end months (**+0.032bp**, n=30). Largest single month-end effect in the sample:
  2025-11 at +1.52bp. EFFR is an administered-rate-anchored series and does not show the
  quarter-end behaviour of SOFR.
- **Holiday clustering is not the driver.** Calendar-day forward fill repeats the prior print
  on a holiday, which contributed at most about 0.03bp of month-mean effect in the observed
  sample (the 2023 Juneteenth case above).
- **IORB technical adjustments are not the driver** in this sample, because there were none.
  See Section 7.

The single mechanism behind every large residual is a **multi-month trend in the EFFR-IORB
spread**, which by construction puts adjacent months at different average distances from the
administered floor.

---

## 6. (Task item 4) By structure: FRONT vs BACK vs outright

The exposure genuinely differs by structure, because the same residual in bp is divided by a
different `s`.

### 6.1 In bp of the traded structure

| Structure | n | mean | MAE | sd | p50 | p90 | p95 | max |
|---|---|---|---|---|---|---|---|---|
| All adjacent pairs | 55 | -0.0921 | 0.1752 | 0.4912 | 0.0000 | 0.3467 | 1.2681 | 2.2140 |
| FRONT candidates (all) | 37 | -0.1012 | 0.2059 | 0.5549 | 0.0000 | 0.3596 | 1.4045 | 2.2140 |
| FRONT clean (`c = 0`) | 21 | -0.0824 | 0.1991 | 0.5674 | 0.0000 | 0.3548 | 1.2258 | 2.2140 |
| BACK candidates (all) | 36 | -0.0926 | 0.1515 | 0.4343 | 0.0000 | 0.3172 | 0.6167 | 2.1194 |
| BACK clean (`c = 0`) | 18 | -0.0733 | 0.1119 | 0.3370 | 0.0000 | 0.2586 | 0.4600 | 1.3667 |

In bp the two spread types are statistically indistinguishable, as the mechanism predicts:
the residual is a property of the month pair, not of which meeting is being hedged with it.

### 6.2 In cents per 25bp-equivalent contract (`4 * |resid| / s`)

| Structure | n | MAE | sd | p50 | p90 | p95 | max | mean `s` |
|---|---|---|---|---|---|---|---|---|
| FRONT candidates (all) | 37 | 2.2081 | 5.7145 | 0.0000 | 6.8300 | 14.9992 | 27.7419 | - |
| FRONT clean | 21 | **0.8701** | 2.2853 | 0.0000 | 2.0000 | 5.0723 | **9.4667** | 0.7578 |
| BACK candidates (all) | 34 | 5.2734 | 22.5510 | 0.0000 | 6.6727 | 11.3173 | 131.4000 | - |
| BACK clean | 18 | **0.7504** | 1.9970 | 0.0000 | 2.3871 | 3.5643 | **8.0698** | 0.6316 |

Clean FRONT and clean BACK are comparable once normalized. The "all candidates" rows are far
worse only because they include structures with tiny `s` that the Section 4 rule would never
select; they are shown to make the `1/s` amplification visible rather than to characterise a
tradable exposure.

### 6.3 Outrights (the Tier-3 fallback, `G2 EXPOSED`)

Here the level error does **not** cancel, which is the whole point.

| Structure | n | mean | MAE | sd | p50 | p90 | p95 | max |
|---|---|---|---|---|---|---|---|---|
| Meeting-month outright, bp | 37 | +0.0801 | **0.0963** | 0.2835 | 0.0000 | 0.3219 | 0.3867 | 1.5806 |
| Meeting-month outright, cents (`s = w`) | 35 | - | **3.7303** | 16.5663 | 0.0000 | 3.7846 | 7.0222 | **98.0000** |
| M+1 outright, bp | 37 | -0.0212 | 0.1439 | 0.4728 | 0.0000 | 0.3596 | 0.7518 | 2.4194 |
| M+1 outright, cents (`s = 1 - c`) | 37 | - | 0.6581 | 1.9453 | 0.0000 | 2.2133 | 3.4949 | 10.3448 |

Two meetings (2024-01-31, 2024-07-31) have `w = 0`, so the meeting-month outright is undefined
and is excluded from the cents row (35 not 37), exactly as limitation 11 of the
pre-registration anticipates.

**The meeting-month outright row is the reconciliation with the old number.** Its level
residual MAE of **0.0963bp** is within 12% of the 0.085654bp quoted in Section 2.6. The old
study measured that quantity correctly. It then (a) charged it against a *spread*, whose
exposure is the differential and not the level, and (b) converted at `4` rather than `4/s`,
which for the meeting-month outright itself would have given **3.73 cents**, not 0.343.

### 6.4 The operational view: the Section-4-selected instrument, per meeting

The number that should set the reserve is the residual on the instrument the pre-registered
rule actually picks. The selector used here is calendar-pure (contamination computed from the
published FOMC calendar, never from realized outcomes) and reproduces the Section 3 table on
all 16 spot-checked meetings.

| Sample | n | mean | MAE | sd | p50 | p90 | p95 | max |
|---|---|---|---|---|---|---|---|---|
| Selected Tier-1, bp | 33 | -0.0924 | 0.1878 | 0.5101 | 0.0000 | 0.3439 | 1.2822 | 2.2140 |
| Selected Tier-1, cents | 33 | - | 0.9630 | 2.2891 | 0.0000 | 2.6601 | 6.2713 | 9.4667 |
| Selected all tiers, cents | 37 | - | 0.9542 | 2.1906 | 0.0000 | 2.7577 | 5.6718 | 9.4667 |
| **Selected, 2025-09 onward, bp** | 8 | -0.3259 | **0.6926** | 1.0425 | 0.2704 | 1.6209 | 1.9174 | **2.2140** |
| **Selected, 2025-09 onward, cents** | 8 | - | **3.5536** | 3.6398 | 2.4963 | 8.4889 | 8.9778 | **9.4667** |

**Per-meeting detail over the ARM-2 window**, every one of which is a clean Tier-1 spread:

| Meeting | Instrument | `s` | `resid_bp` | `C3` realized (cents) | Old K3 (> 3.00c) |
|---|---|---|---|---|---|
| 2025-09-17 | B Aug/Sep25 | 0.433333 | -0.3000 | 2.769 | no |
| **2025-10-29** | F Oct/Nov25 | 0.935484 | **-2.2140** | **9.467** | **BREACH** |
| **2025-12-10** | B Nov/Dec25 | 0.677419 | **-1.3667** | **8.070** | **BREACH** |
| 2026-01-28 | F Jan/Feb26 | 0.903226 | 0.0000 | 0.000 | no |
| 2026-03-18 | B Feb/Mar26 | 0.419355 | 0.0000 | 0.000 | no |
| **2026-04-29** | F Apr/May26 | 0.966667 | **+1.2258** | **5.072** | **BREACH** |
| 2026-06-17 | B May/Jun26 | 0.433333 | +0.2409 | 2.223 | no |
| 2026-07-29 | F Jul/Aug26 | 0.935484 | -0.1935 | 0.828 | no |

**This confirms the reviewer's claim.** Kill condition K3 ("realized EFFR hedge error on any
meeting exceeds 3.00 cents: pass-through assumption broken, halt") is already breached on
**3 of the 7 decided meetings** in the ARM-2 window, before a single trade is placed. (The
brief says 3 of 8; the eighth ARM-2 meeting, 2026-09-16, has `realized_change_bp = NULL` in
`meetings` and no realized September mean yet, so it cannot be scored. The three breaching
meetings are the same three.) Over the full 2022-2026 sample the breach rate is 3 of 33
Tier-1 meetings, all three in the last eleven months.

---

## 7. (Task item 5) IORB moves without a target-range change

**Rule applied.** Scan every date in the IORB-covered window for `iorb` changing while
`tgt_upper` does not.

**Result: zero genuine occurrences.** The `IORB` series in this database runs **2021-07-29 to
2026-09-17**. Exactly one candidate date is returned, and it is an artifact:

| date | `d_iorb` | `tgt_upper` published that day? | EFFR response | verdict |
|---|---|---|---|---|
| 2026-09-17 | +25.0bp | **no** (NULL) | 0.00bp (EFFR not published either) | **ARTIFACT, not a technical adjustment** |

`fred_long` shows `IORB` loaded through **2026-09-17** while `DFEDTARU` / `DFEDTARL` are
loaded only through **2026-09-16** and `EFFR` through **2026-09-15**. The three series have
different release lags, so the last row of `fred_rates` always looks like a technical
adjustment. The +25bp IORB step on 2026-09-17 is the ordinary pass-through of the 2026-09-16
FOMC decision, whose row in `meetings` still carries `realized_change_bp = NULL` and must be
backfilled before that meeting can enter any sample.

**Implementation requirement for the K5 monitor.** Any detector must require `tgt_upper` to be
**non-null on the date being tested** before comparing it. A naive diff fires a false K5 on the
last row of the table every single day. This is a live trap: K5 says "close at market", so a
false positive is an unnecessary realized loss.

**What cannot be measured here, stated plainly.** The known IOER/IORB technical adjustments
(2018-06, 2018-12, 2019-05, 2019-09, 2021-06) all predate 2021-07-29, and FRED's `IOER`
series, the pre-2021 predecessor, **is not in this database**. The frequency and magnitude of
technical adjustments therefore cannot be estimated from this data and must be sourced
separately before any number is attached to K5. Section 6.3 must not carry an implied
estimate of zero merely because this window contains none.

**What one would do to a spread package, structurally.** An IORB adjustment of `x` bp
effective part-way through delivery month `L`, with month `E` already complete, moves
`mean_effr(L)` by roughly `x * (fraction of L at the new IORB)` and `mean_effr(E)` not at all.
The entire amount lands in `resid_bp`. A 5bp adjustment effective at mid-month is about 2.5bp
of spread residual:

```
    at s = 0.935:   4 * 2.5 / 0.935484 = 10.69 cents
    at s = 0.419:   4 * 2.5 / 0.419355 = 23.85 cents
```

Both exceed the entire pre-registered entry threshold for those meetings, and **no Kalshi
outcome resolves on an IORB adjustment**, so the loss is unhedged in full. This is not a tail
that the reserve covers; it is the K5 tail, and this measurement does not shrink it. Section
9 limitation 8 stands unchanged and is, on this evidence, the dominant risk in the design.

---

## 8. The number that replaces 0.343c / 3.00c

### 8.1 Recommendation

**Replace the scalar reserve with a span-scaled one.** A constant in cents is wrong on its
face, because the identical hedge error costs 2.2x more on the 2026-03-18 instrument
(`s = 0.419`) than on the 2026-04-29 instrument (`s = 0.967`). The primitive must be stated in
bp of the spread and converted per meeting.

```
    C3(s) = 4 * R_bp / s        cents per 25bp-equivalent contract

    R_bp (base)   = 0.70 bp     [drifting-regime MAE 0.6939bp, rounded up]
    R_bp (stress) = 2.25 bp     [drifting-regime max 2.2140bp, rounded up]
```

At the reference `s = 1` that is **2.80 cents base and 9.00 cents stress**, against the
current 0.343 and 3.00. The base reserve rises by a factor of **8.2x at `s = 1` and up to
19.5x at `s = 0.419`**.

**Reported for the record but not recommended as the base:** the full-sample values
`R_bp = 0.18` base (all-pairs MAE 0.1752bp) and `2.25` stress. The full-sample base is
rejected because 33 of 56 delivery months in that window had EFFR printing one value on every
calendar day, a condition that makes the flat-rate model exactly right by construction and that
has not held since September 2025. ARM 3 trades entirely inside the post-2025-09 regime.

`C3` by meeting:

| Meeting | Instrument | `s` | C3 old | **C3 base new** | multiple | **C3 stress new** |
|---|---|---|---|---|---|---|
| 2025-10-29 | F Oct/Nov25 | 0.935484 | 0.343 | **2.993** | 8.7x | 9.621 |
| 2025-12-10 | B Nov/Dec25 | 0.677419 | 0.343 | **4.133** | 12.1x | 13.286 |
| 2026-01-28 | F Jan/Feb26 | 0.903226 | 0.343 | **3.100** | 9.0x | 9.964 |
| 2026-03-18 | B Feb/Mar26 | 0.419355 | 0.343 | **6.677** | 19.5x | 21.462 |
| 2026-04-29 | F Apr/May26 | 0.966667 | 0.343 | **2.897** | 8.4x | 9.310 |
| 2026-06-17 | B May/Jun26 | 0.433333 | 0.343 | **6.462** | 18.8x | 20.769 |
| 2026-07-29 | F Jul/Aug26 | 0.935484 | 0.343 | **2.993** | 8.7x | 9.621 |
| 2026-09-16 | B Aug/Sep26 | 0.466667 | 0.343 | **6.000** | 17.5x | 19.286 |
| 2026-10-28 | F Oct/Nov26 | 0.903226 | 0.343 | **3.100** | 9.0x | 9.964 |
| 2026-12-09 | B Nov/Dec26 | 0.709677 | 0.343 | **3.945** | 11.5x | 12.682 |
| 2027-01-27 | F Jan/Feb27 | 0.870968 | 0.343 | **3.215** | 9.4x | 10.333 |
| 2027-03-17 | B Feb/Mar27 | 0.451613 | 0.343 | **6.200** | 18.1x | 19.929 |
| *(reference)* | *clean, `s` = 1* | 1.000000 | 0.343 | **2.800** | 8.2x | 9.000 |

(2027-03-17 selects Tier-1 BACK Feb/Mar27, `s = w = 14/31 = 0.451613`: February 2027 is
meeting-free, while the FRONT partner April 2027 contains the 2027-04-28 meeting.)

### 8.2 What this does to the pre-registered threshold

`T = C1 + C2 + C3 + C4` with the Section 6.5 base case unchanged otherwise (Variant A,
`n_cross = 2`, 1 tick, `p = 0.50`, EFFR 3.63%, `H = 90`). Note that `C2` and `C3` now both
scale as `1/s`, so they combine to `(2.00 + 4 * R_bp)/s`.

| Meeting | `s` | C1 | C2 | C3 new | C4 | **T old** | **T new (cents)** | **T new (bp)** |
|---|---|---|---|---|---|---|---|---|
| 2025-10-29 | 0.935484 | 1.750 | 2.1379 | 2.993 | 0.4475 | 4.678 | **7.329** | 1.832 |
| 2025-12-10 | 0.677419 | 1.750 | 2.9524 | 4.133 | 0.4475 | 5.493 | **9.283** | 2.321 |
| 2026-01-28 | 0.903226 | 1.750 | 2.2143 | 3.100 | 0.4475 | 4.755 | **7.512** | 1.878 |
| 2026-03-18 | 0.419355 | 1.750 | 4.7692 | 6.677 | 0.4475 | 7.310 | **13.644** | 3.411 |
| 2026-04-29 | 0.966667 | 1.750 | 2.0690 | 2.897 | 0.4475 | 4.609 | **7.163** | 1.791 |
| 2026-06-17 | 0.433333 | 1.750 | 4.6154 | 6.462 | 0.4475 | 7.156 | **13.274** | 3.319 |
| 2026-07-29 | 0.935484 | 1.750 | 2.1379 | 2.993 | 0.4475 | 4.678 | **7.329** | 1.832 |
| 2026-09-16 | 0.466667 | 1.750 | 4.2857 | 6.000 | 0.4475 | 6.826 | **12.483** | 3.121 |
| **2026-10-28** | 0.903226 | 1.750 | 2.2143 | 3.100 | 0.4475 | 4.755 | **7.512** | 1.878 |
| **2026-12-09** | 0.709677 | 1.750 | 2.8182 | 3.945 | 0.4475 | 5.359 | **8.961** | 2.240 |
| **2027-01-27** | 0.870968 | 1.750 | 2.2963 | 3.215 | 0.4475 | 4.837 | **7.709** | 1.927 |
| **2027-03-17** | 0.451613 | 1.750 | 4.4286 | 6.200 | 0.4475 | 6.969 | **12.826** | 3.207 |
| *(reference `s`=1)* | 1.000000 | 1.750 | 2.0000 | 2.800 | 0.4475 | 4.540 | **6.997** | 1.749 |

The entry bar rises from "roughly 4.5 to 7.3 cents" to **"roughly 7.0 to 13.6 cents"**, a
factor of 1.54x to 1.87x. In basis points of venue disagreement the requirement moves from
1.14-1.83bp to **1.75-3.41bp**.

This is worth reading against the pre-registration's own stated prior (Section 1.3): the prior
coarse pass produced an overall median day of **-1.97 cents** and a best meeting of **+7.69
cents** median. The best meeting in the biased-favourable coarse pass now barely clears the
threshold on the highest-`s` instruments and clears nothing on the low-`s` ones. Section 8.3's
prediction, "H1 rejected with very few or zero qualifying days", is strengthened, not
weakened, by this measurement. That is the correct direction for a pre-registration to move
before it is frozen.

### 8.3 K3 must be restated in bp

K3 currently reads "realized EFFR hedge error on any meeting exceeds **3.00 cents**". A cents
threshold is not comparable across instruments once the conversion is `4/s`, and as measured it
is already breached 3 times. Proposed replacement:

> **K3 (revised).** Realized drift differential `|resid_bp|` on a traded meeting's selected
> instrument exceeds **2.25bp**. Halt and re-estimate on the extended sample.

Honest caveat: 2.25bp is the in-sample maximum of the regime it was estimated on, so by
construction nothing in the sample breaches it. It is a forward tripwire, not a test that has
been passed. Its value is that it fires the first time the current regime worsens, and that it
is computable from `fred_rates` alone within one business day of month end.

### 8.4 Harness changes this requires

- `harness/costs.py`: `hedge_error_reserve(assumptions)` returns a constant and takes no `s`.
  It must take the instrument's span and return `4 * R_bp / s / 100` dollars. The
  `CostAssumptions` fields `hedge_error_mae_cents = 0.343` / `hedge_error_max_cents = 3.00`
  become `hedge_error_mae_bp = 0.70` / `hedge_error_max_bp = 2.25`.
- The accounting convention documented at `costs.py:35-40` is **unchanged and still correct**:
  the reserve sets the pre-registered threshold and is never charged against realized P&L,
  which already contains whatever hedge error actually occurred. Only the value and the
  `s`-dependence change.
- `harness/panel.py` already exposes `Instrument.anchor_sensitivity_cents_per_bp = 100/h_bp`,
  which is the same `4/s`. The reserve should be computed through it rather than duplicating
  the constant.
- Add a regression test asserting `C3(s=1, R=0.085654) == 0.3426` so that the relationship to
  the superseded number stays visible.

---

## 9. Output file

`results/tracking_error_by_pair.csv`, 55 rows x 35 columns, one row per adjacent
delivery-month pair.

| column | meaning |
|---|---|
| `pair`, `month_early`, `month_late` | the adjacent delivery-month pair, `E` and `L` |
| `regime` | `pinned` (E < 2025-09) or `drifting` |
| `n_days_early/late` | calendar days in each delivery month |
| `n_distinct_effr_early/late` | distinct EFFR prints in the month (1 = flat model exact by construction) |
| `anchor_date`, `anchor_effr` | last published EFFR strictly before `E` begins |
| `real_mean_early/late` | realized calendar-day mean EFFR, percentage points |
| `model_mean_early/late` | flat-rate model mean under realized target changes |
| `drift_early/late_bp` | `100 * (real - model)` per month |
| `spread_real_bp`, `spread_model_bp` | `X` realized and modelled, bp |
| `resid_bp`, `abs_resid_bp` | **the drift differential**, `drift_early - drift_late` |
| `effr_less_iorb_early/late_bp` | monthly mean EFFR minus IORB |
| `iorb_identity_bp`, `identity_err_bp` | the Section 3 identity and its error (0.0 everywhere) |
| `front_meeting`, `s_front`, `c_front`, `front_clean`, `resid_cents_front` | FRONT role for a meeting held in `E` |
| `back_meeting`, `s_back`, `c_back`, `back_clean`, `resid_cents_back` | BACK role for a meeting held in `L` |
| `quarter_boundary` | `E` ends a calendar quarter |

`s` and `c` are computed from the published FOMC calendar only, never from realized outcomes.

---

## 10. Limitations of this measurement

1. **The base reserve rests on 11 pairs, 7 of them nonzero.** The drifting-regime MAE of
   0.694bp is not a precisely estimated number, and three of its largest observations come
   from one continuous late-2025 episode.
2. **The regime split at 2025-09 is a judgement call**, made on the mechanism (EFFR ceased to
   print a single value per month) rather than on a test. It is stated here before any P&L so
   it cannot be re-chosen later, but it is a choice.
3. **The residual is autocorrelated and one-signed within an episode.** Treating it as a
   symmetric per-meeting reserve is conservative for a single trade and optimistic for a
   sequence of trades in the same direction during one episode.
4. **Nothing here bounds the IORB technical-adjustment tail** (Section 7). The database
   contains zero such events and no pre-2021 IOER series, so K5 remains unquantified.
5. **The measurement is ex post by construction.** It uses realized target changes to build
   the model path, which is correct for measuring realized tracking error but means the number
   is a historical average, not a forecast. The EFFR-IORB identity of Section 3 is the
   forward-monitoring handle.
6. **Only 2 months of 2026-09 onward data will be available before the first ARM-3 meeting**
   (2026-10-28), so the reserve cannot be re-estimated meaningfully before ARM 3 begins. It
   must be fixed now, at these values, and amended only with a dated entry.
7. **The `meetings` row for 2026-09-16 is stale** (`realized_change_bp = NULL` while IORB has
   already stepped +25bp on 2026-09-17). Every statistic in this document excludes that
   meeting. It must be backfilled and this measurement re-run before 2026-09 enters any sample.

---

## 11. Amendments required to `PREREGISTRATION.md` before freezing

1. **Section 2.6** - replace the pass-through paragraph. The 17-of-17 / MAE-0.0857bp result is
   retained as a statement about *level* pass-through and is explicitly labelled as not being
   the spread's exposure. Cross-reference this document.
2. **Section 6.3 (C3)** - replace with `C3(s) = 4 * R_bp / s`, `R_bp` base 0.70 / stress 2.25.
3. **Section 6.5** - replace the committed threshold table with Section 8.2 above, and
   recompute the Variant B and stress tables on the same basis.
4. **Section 7.4 (K3)** - restate in bp per Section 8.3.
5. **Section 6.6 (assumptions register)** - `C3` moves from a measured constant to a
   measured, regime-conditional, span-scaled quantity; add the regime choice as a declared
   judgement.
6. **Section 9 (limitations)** - amend item 14; add the regime-split and the IORB
   unquantifiability items from Section 10.

None of these are discretionary tuning. Every one is forced by a measurement made before any
P&L exists, which is the only time such a change is legitimate.
