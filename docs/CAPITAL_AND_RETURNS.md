# Capital, payoff and return definitions

The project reports three distinct quantities: conditional entry payoff, realized modeled profit, and capital required to carry the modeled path. They must not be collapsed into one “expected return.”

## Conditional entry return

For the two selected decision states `d0` and `d1`, and zero EFFR residual:

```text
entry_cash = digital premium + modeled fees and friction on both venues
conditional_profit_floor = min(net terminal payoff at d0, net terminal payoff at d1)
standalone_support = max(current initial margin,
                        maintenance at each modeled stage - prior scenario cash flows)
committed_capital = entry_cash + standalone_support
conditional_entry_ROI = conditional_profit_floor / committed_capital
```

The maximum covers all modeled historical shock scenarios and expiry stages. It is not a lower payoff bound across all policy states or a guarantee against liquidation. The small integer hedge residual remains in the two endpoint payoffs.

The current margin scenario assumes $330 initial / $300 maintenance for an eligible complete adjacent pair, and $1,100 / $950 for an unpaired contract. Historical one-to-five-observation cash shocks use the previous 60 common, available observations. A separate $904.153 maintenance reference is a sensitivity, not a verified historical account margin schedule.

Margin is reserved capital, not an expense. Removing it from the denominator gives a different return-on-cash metric; it does not improve the package's dollars of profit. Initial spread margin alone is insufficient when the near leg expires before the far leg.

## Realized and account returns

```text
realized_trade_ROI = modeled settled net profit / standalone committed capital
account_return = (completed ending assets / initial account cash) - 1
calendar_CAGR = (completed ending assets / initial account cash)^(1 / elapsed years) - 1
```

Calendar CAGR includes idle time. Do not compound a median trade return a presumed six or eight times per year. Meetings have different holding periods, overlapping cash needs, missing entries and rejected signals.

## Current reference values

For `all_signals_asof / book_replace_2c__maintenance950`:

| Statistic | Conditional entry ROI | Realized full-funding ROI |
|---|---:|---:|
| Minimum | 0.631874% | 0.637993% |
| Median | 1.927572% | 3.201660% |
| Maximum | 9.800921% | 9.417995% |

The middle half of conditional entry returns is 1.029990%–3.201660%. These are nine previously examined Polymarket positions, including a flagged March 2024 ladder anomaly, not a prospective return distribution.

The main $100,000 account ends at $126,510.76: cumulative 26.5108% and calendar CAGR 9.5343% from February 1, 2024 through September 1, 2026. Retaining an additional two-cent allowance on the five newly covered books changes the result to $123,765.82, or 8.6077% calendar CAGR. The choice of cost convention is a sensitivity and must not be selected by which outcome looks better.

## What would be needed for unconditional expected value?

A probability-weighted expectation requires a justified joint distribution for policy outcomes, EFFR residuals, execution failures, funding shocks and cash-release timing. Futures-implied means and digital quotes are not established physical probabilities. Favorable realized EFFR drift is an attribution component, not an entry-time promise.

Public aggregate verification confirms arithmetic in the delivered outputs. Detailed per-leg cash ledgers remain private because they can disclose licensed market-price changes. Their public omission reduces the scope of independent verification from the bundle; see [reproduction levels](REPRODUCIBILITY.md).
