# Audit findings and remaining limitations

This file describes the current September 20 replay and the public project release. The original September 17 research is retained as dated historical context, not as a second source of current headline figures.

| Area | What was checked locally | What remains unresolved |
|---|---|---|
| Payoff arithmetic | Calendar signs and units, endpoint matching, integer quantities, cash/terminal identities | Open-ended outcome buckets, omitted states, unscheduled policy moves and EFFR residual risk |
| Timestamp discipline | Fixed entry attempts, delayed execution clocks, availability filters, future-book poisoning checks | Historical snapshots are not a full message/latency/fill record; prior parameter exploration remains |
| Entry execution | Nine original ZQ entry-day depth diagnostics and five Polymarket book-cost replacements | Four filled cases retain proxy PM costs; selected entry coverage does not certify exits or all rejected candidates |
| Funding | Local cash ledgers and expiry-stage support model audited under fixed margin scenarios | Actual account/FCM house rules, intraday margin calls, transfers and changing requirements |
| Public reproduction | Hashes, source syntax, synthetic payoff invariants and aggregate P&L/ROI/CAGR reconciliation | Raw-feed reconstruction and per-leg cash-ledger checks need separately entitled private inputs |
| Inference | Nine distinct filled events clearly distinguished from repeated scenarios | Stable future win rate, loss-payoff ratio, capacity and risk-adjusted alpha are not established |

## Known issues retained rather than hidden

- The March 20, 2024 selected YES ladder sums to **128.5%**. It remains flagged to reconcile the reference baseline. Its inclusion is not evidence that it was a clean opportunity.
- Five historical PM books were obtained for selected previously filled entries. They improve those entry cost estimates; they do not create a newly unbiased event sample.
- Hypothetical fee and margin assumptions are not account invoices or verified historical schedules. The inherited Polymarket research fee coefficient is 0.05; a current Kalshi diagnostic uses its separately checked 0.07 taker coefficient. Do not exchange those assumptions silently.
- All nine modeled terminal profits are positive. That fact is not an estimate of a 100% future win probability.
- Original research explored multiple parameters. The design document in `prereg/` was never frozen before results. This repository does not describe the historical sample as a pre-registered validation.
- Full-book quotes are static displayed liquidity. Shared implied futures liquidity and synthetic leg execution must not be double-counted as independent capacity.
- Some acquired dataset dates are marked degraded. Source conditions and partial coverage must be checked for the actual evaluation window.

## Version corrections

The original README asserted that the edge was real and then ended with EFFR drift. The current project treats those statements as exploratory conclusions from an earlier, narrower design. Calendar replication, state matching, executable prices and funding are distinct tests; no current headline claims a universal locked payoff or optimal implementation.

Earlier manuscript returns such as digital-cash-only percentages use different samples and capital denominators. They are not directly comparable to the current 3.2017% median realized standalone full-funding return. The current account cutoff and cost mode are always explicit.

The public release deliberately excludes source-feed prices and reconstructable per-leg VM ledgers. Passing public CI establishes a narrower claim than the original local audit. The exclusions and private input contract are part of the research result, not a claim that the missing data are immaterial.
