> Historical exploratory study, retained for provenance. This is not the current account replay. See the [current overview](../README.md), [version history](../CHANGELOG.md) and [limitations](../docs/LIMITATIONS.md) before citing its conclusions.

# Synthesis: the hedge and the edge do not coexist

This file combines the measured spread tracking error (`prereg/TRACKING_ERROR.md`) with the coarse
backtest run before the harness was written. Every number below was independently recomputed from
FRED daily EFFR and the IBKR contract bars.

## The measurement that settles it

A ZQ calendar spread does not hedge a Kalshi Fed contract against "the policy decision". It hedges
against the *realized mean EFFR of two delivery months*. The residual is the intra-month EFFR drift
DIFFERENTIAL between those two months, and it splits cleanly into two regimes:

| regime | months | MAE (bp of spread) | MAE in cents/contract at s=0.9 | p90 | months with EFFR flat all month |
| --- | --- | --- | --- | --- | --- |
| Pinned 2022-01 .. 2025-08 | 44 | **0.046** | 0.20c | 0.21bp | 28/44 |
| Drifting 2025-09 .. 2026-07 | 11 | **0.694** | **3.08c** | 2.12bp (**9.42c**) | 4/11 |

The unit conversion is `4 * resid_bp / s` cents per 25bp-equivalent contract, verified: at s=1 and
the 0.0857bp pass-through figure it reproduces the 0.343c reserve exactly. (The review brief's
`100/s` overstates by 25x.)

Worst pair, recomputed end to end: 2025-10 mean EFFR 4.0881%, 2025-11 mean 3.8763%, realized spread
+21.17bp against a flat-rate prediction of +23.39bp, residual **-2.2140bp = 9.47 cents** on an
instrument whose entire pre-registered entry threshold was 4.678 cents. EFFR climbed monotonically
through October (4.09 -> 4.12, six distinct prints) as reserves drained. Not a month-end artifact.

## Why that kills the trade

The coarse backtest found 2 of 9 meetings with a positive median net edge: 2026-07-29 at +7.69c and
2026-09-16 at +4.55c. Both sit inside the drifting regime.

- Ex post, 2026-07-29's own pair (2026-07/2026-08) had a residual of only -0.194bp = **0.83c**, so
  that meeting's edge survived its realized hedge error. It was a good outcome.
- Ex ante, the position carried a risk whose MAE in that regime is 3.08c and whose p90 is 9.42c,
  against an edge of 7.69c. The edge-to-risk ratio is roughly 1:1.

A package whose hedge error has a p90 larger than its edge is not an arbitrage. It is a risky
relative-value trade that happened to win once.

## The structural bind

> **Resolved, 2026-09-17. See [PINNED_REGIME.md](PINNED_REGIME.md).** Databento supplies ZQ
> settlements back to 2021-12-31, so the pinned regime is now testable and the bind below no
> longer holds. Two numbers in this section are superseded: the pinned-regime hedge error is
> **0.134c** MAE (not 0.20c), exactly zero on 20 of 25 meetings, and the drifting-regime error
> measured on the same FRONT/BACK instrument is **3.554c** MAE with p90 8.489c. The conclusion
> of this document stands for the current regime and is now known to be specific to it.

The two regimes are mutually exclusive in exactly the wrong way:

- **Pinned regime**: the hedge is excellent (0.20c). But there is no ZQ contract data before
  2025-09-30 (IBKR's expired-contract retention floor), so the edge cannot be measured there at all.
- **Drifting regime**: the edge is measurable, and appears on 2 of 9 meetings. But the hedge error
  in that same regime has MAE 3.08c and p90 9.42c.

Every meeting we can test has a hedge we cannot rely on; every meeting with a hedge we could rely on
is one we cannot test.

## What this does not say

It does not say the venues are efficiently linked. The basis was positive on 61% of days and the two
profitable meetings are real observations. It says that the instrument proposed to harvest that basis
does not hold the position still enough, in the current monetary-operations regime, for the result to
be called arbitrage. A different hedge (meeting-dated OIS, if it were retail-accessible) would not
carry this residual, because it settles on the meeting outcome rather than on a monthly EFFR average.
