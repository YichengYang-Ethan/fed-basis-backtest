# September 25 Kalshi comparison: an as-of diagnostic

This example uses the October 28 decision and its calendar-eligible October/November ZQ spread. It is separate from the nine-trade Polymarket account and does not extend that account's September 1 cutoff.

At the latest common major-state minute available in the local comparison, **September 25, 2026 at 16:52 UTC**, the midpoint discrepancy was approximately **1.82 cents per 25bp-equivalent digital**. Crossing the futures and prediction-market quotes reduced the gross advantage to approximately **0.21 cents**, before transaction fees.

Using the same full-stage funding method, zero EFFR residual and HOLD / HIKE25 endpoints, the better-direction conditional return was approximately **−0.82%**, versus **−2.77%** for the reverse direction. An earlier observation briefly exceeded the four-cent signal threshold, but the discrepancy did not sustain that threshold over three consecutive minutes. This window did not qualify under the reference rule.

The comparison used CME as-of BBO and Kalshi minute-close prices. Historical Kalshi depth and within-minute update time were not certified. The evening Kalshi book was a different timestamp; it was not combined with the midday CME record as a live execution claim. Local IBKR API ports were offline, and available historical CME data lagged the request by about eight hours.

The fee input used Kalshi's then-current 0.07 taker coefficient and the study's assumed futures package cost. Actual account margin and fee invoices were unavailable. NO(HOLD) and YES(HIKE25) agree over the two chosen states but have different tail payoffs.

This public note provides derived diagnostics only. Source prices, order-book records, exact position/cash decomposition and local provider receipts remain private. The lesson is to test the full contract/cost/capital chain at a common clock, rather than translate a midpoint probability difference directly into a capital return.

Sources: [Kalshi fee schedule](https://kalshi.com/docs/kalshi-fee-schedule.pdf), [Fed October calendar](https://www.federalreserve.gov/newsevents/2026-october.htm), and the local September 25 matched-quote audit. These sources and the snapshot date are not a live quote service.
