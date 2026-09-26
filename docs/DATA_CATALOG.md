# Data catalog and release boundary

The project separates calendar/rule metadata, derived research results, and separately licensed source feeds. The public repository is sufficient to inspect the financial model and verify bundled derived arithmetic. It does not contain the raw market database or grant rights to redistribute source feeds.

## Different samples answer different questions

| Layer | Count | Unit and purpose | Public location |
|---|---:|---|---|
| Calendar construction | See original instrument table | Scheduled FOMC meetings and calendar-only FRONT/BACK eligibility | `data/fomc_instruments.csv` |
| Historical coverage review | 38 | Meetings examined for history, rules and calendar eligibility | `research/depth_replay/data/coverage_historical38.csv` |
| Later rolling coverage | 12 | Meeting windows with later joint-coverage diagnostics; 32,510 valid minute anchors in that analysis | `research/depth_replay/data/coverage_latest12.csv` |
| Kalshi inventory | 28 | Separate meetings, 140 listed legs and 1,854,555 collected candle rows in the private source inventory | `research/depth_replay/data/kalshi_inventory28.csv` |
| Frozen account candidates | 11 | Original Polymarket candidate meetings, including non-fills | `research/depth_replay/data/candidate_inventory.csv` |
| Completed account events | 9 | Unique Polymarket meeting events with a ZQ hedge | `research/depth_replay/data/trade_results.csv` |
| Replay configurations | 12 | Two evaluation books × three cost modes × two maintenance assumptions; reuse the same nine fills | `research/depth_replay/data/scenario_summary.csv` |
| Additional PM depth review | 5 | Selected previously filled entries with historical static full-book checks | `research/depth_replay/data/pm_cost_impact.csv` |

The 38-, 12- and 28-meeting tables are not a single attrition funnel. They have distinct universes and vintages. The nine empirical fills are Polymarket positions, not Kalshi backtest results. Later rolling analyses, the legacy pinned-regime study, and the capital-account replay must not be pooled as independent observations.

Grouping the historical 38-meeting primary labels gives 12 eligible-data cases, 9 non-exhaustive ladders, 8 without local history, 7 ambiguous rule mappings, 1 rule conflict and 1 without a clean isolated calendar instrument. The 12 eligible-data cases comprise 10 known-terminal signals, one pending-terminal signal and one valid-data window without the 4-cent/3-minute signal. Rule and contract exclusions cannot be cured merely by downloading more price rows. Secondary flags can overlap primary labels.

## Field interpretation

`data/data_dictionary.csv` within the replay release defines every included column. The most important joins and units are:

- `run_id`: sample/cutoff plus cost/margin configuration; not an independent experiment.
- `candidate_id`: historical meeting identity across configurations; `trade_id` is run-specific.
- `meeting_date`: scheduled decision date. Epoch timestamps are UTC seconds; fields ending `_et` retain explicit UTC offsets.
- `leg_near`, `leg_far`: delivery months. `instrument` identifies the FRONT or BACK construction. `span` is the calendar coefficient, not estimated market duration.
- `N` and `quantity` in candidate metadata: calendar-derived theoretical and integer digital hedge quantity **per one spread**; not the historical account's position size.
- `d0_bp`, `d1_bp`: the two retained decision changes in basis points. Tail omission is recorded separately and does not prove a complete state hedge.
- `actual_outcome` and `actual_buy_side`: the selected digital contract label and YES/NO side, not a guarantee that the contract won.
- `original_yes_ladder_sum`, rule bounds, inclusivity and parse flags: integrity checks for mutually exclusive outcomes. The March 2024 sum is 1.285 and remains flagged.
- `entry_two_state_floor_roi_pct`: conditional two-state entry payoff floor relative to modeled committed funds; not an unconditional expected return.
- `realized_full_funding_roi_pct`: modeled settled P&L divided by standalone committed capital. Margin is reserved capital, not an expense.
- `realized_full_account_return_pct`: simulated account gain divided by initial $100,000; `calendar_cagr_pct` uses the specified elapsed-calendar window.

Missing fields mean unavailable or inapplicable, never zero. Public trade/scenario dollar values are rounded to cents and percentage returns to six decimal percentage points. Verifiers document and bound rounding effects.

## Source types and access

| Source | Research use | What is public | What is deliberately absent |
|---|---|---|---|
| Federal Reserve calendar and published rates | Meeting dates, policy outcomes, calendar exposures, EFFR terminal payoff checks | Calendar-derived metadata and original study's derived tables/code | No credential required for separately downloading public series |
| CME / Databento | ZQ settlement marks and selected intraday book data; SR1/SR3 cross-check inputs | Source schemas, acquisition/build code where included, derived strategy summaries | Raw settlements, quote/book levels, DBN/Parquet files, reconstructable per-leg VM ledger and `fed.duckdb` |
| Polymarket public APIs | Market definitions, outcome mapping and historical price observations | Public event slugs, coverage metadata, derived research outcomes | Full historical price panel, token-level execution panel and raw books |
| Telonex | Five historical Polymarket depth checks | Normalized slippage and validity diagnostics | Downloaded full books, raw level sizes/prices, signed URLs and API credentials |
| Kalshi public APIs | Separate contract definitions, rules and candle coverage | Event/market tickers and coverage counts | Full candle/trade panel, authenticated account data or private order information |
| Broker margin references | Capital sensitivities | Explicit model assumptions and limitations | Live account data, API sessions, authenticated margin previews or a claim of historically verified house margin |

The original source-data pipeline remains documented in the companion [fed-pricing-db repository](https://github.com/YichengYang-Ethan/fed-pricing-db). The local `research/depth_replay/research_source/required_private_inputs.json` is the detailed schema manifest for the later frozen replay; it names external prepared inputs but contains no observations or credentials. Raw replay requires the relevant entitlements and the complete prepared input tree, not only API keys.

## Why the public projection is narrower than the private research package

Per-leg variation-margin cash divided by signed position size can reveal licensed settlement changes. Omitting raw price columns alone would not remove that reconstruction. The public replay therefore drops the detailed cash-event ledger, exact per-trade position sizes and cash components. Daily aggregation is insufficient when only one position is open. The release retains derived net P&L, committed-fund denominators, return metrics, assumptions and metadata so the reported economics remain inspectable without publishing the source feed.

`research/depth_replay/data/source_manifest.json` gives the allowlist, source hashes, transformations, omitted fields and current CSV hashes. These hashes identify frozen inputs; they do not make absent source files publicly accessible. The release is an intentional derived-data projection, not a substitute for obtaining source-data rights. Repository code licensing does not extend to third-party market feeds.

## What the checks establish

Run from the repository root:

```sh
python research/depth_replay/model/verify_results.py
python research/depth_replay/model/payoff_model.py --self-test
python research/depth_replay/research_source/verify_source_snapshot.py
```

These offline checks establish integrity of the public CSV projection; aggregate P&L, ROI and CAGR arithmetic; exact synthetic payoff invariants; and syntax/hashes of the eight archived model components. They do not independently certify source quotes, fills, historical fee vintages, broker margin, intraday solvency, untouched out-of-sample performance, or omitted per-leg cash-ledger reconciliation.
