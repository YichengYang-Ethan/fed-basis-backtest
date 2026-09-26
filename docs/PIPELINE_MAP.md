# Pipeline map and dependency boundaries

“Frozen” in source descriptions means a release-time code/configuration snapshot. The parameters were studied historically; this is not a prospective preregistration or untouched out-of-sample test.

The public project has a complete **research inspection path** and a runnable **bundled-result verification path**. Private raw-data reconstruction is documented, but is not certified as a fresh-clone, one-command reproduction. Keeping these claims separate matters because original quote tapes and old API metadata vintages are not redistributable here.

## 1. Source collection and the legacy daily database

```text
identifier seed lists -> make_inventories.py -> raw/*_fed_inventory.json
FRED CSV             -> fetch_fred.py       -> raw/fred/*.parquet
inventories + FRED    -> build_meetings.py   -> raw/meetings.parquet
Polymarket metadata  -> poly_pull.py + poly_post.py -> raw/poly/{markets,prices}.parquet
Kalshi metadata      -> kalshi_pull.py      -> raw/kalshi/{markets,candles,trades}.parquet
Databento statistics -> databento_zq_pull.py -> raw/cme/databento/zq_settlements.parquet
                     -> databento_zq_outrights.py -> zq_outrights.parquet
optional IBKR bars   -> ibkr_zq_pull.py      -> raw/cme/ibkr/zq_contracts_ibkr.parquet
legacy Yahoo bars    -> fetch_cme_yf.py      -> raw/cme/ZQ*.parquet + sofr/ + manifest.json
all original tables -> build_db.py         -> fed.duckdb + build_report.json
```

The DuckDB builder retains the older daily exploratory table definitions. Its CME table comes from the Yahoo-pattern inputs, not the Databento subdirectory. The latest account replay bypasses that daily table and uses the named-contract intraday and settlement inputs below. Continuous futures are not accepted as a substitute for those named delivery months.

## 2. Expanded prediction-market and CME minute panels

All names below are staged under `FOMC_RESEARCH_ROOT/work/`. Their actual source is in `data_pipeline/research_builders/work/`; helper-only modules are not steps to execute independently.

| Order | Producer / helper | Principal output | External dependency or limitation |
|---|---|---|---|
| 1 | `expanded_poly_inventory.py`; helpers in `build_poly_analysis_panel.py` | `expanded_poly_market_metadata.parquet`, meeting inventory | Stored Gamma metadata, historical prices, `data/fomc_instruments.csv`; some final outcome fields are evaluation labels. |
| 2 | `expanded_poly_fetch_fullminute.py`, then `expanded_poly_fill_opening.py` | `expanded_poly_fullminute_parts/*.parquet` | Public history requests. Old observations and API behavior may have changed. No payment code. |
| 3 | `expanded_poly_build_fullminute_panel.py` | Full-minute event and leg panels, metadata/rule audit | Backward same-ET-day observations and explicit age checks; midpoint histories do not establish executable prices. |
| 4 | `expanded_poly_principal_panel.py`, then `expanded_poly_liveness.py` | Principal-state and trailing-liveness tables | Actual YES and NO token histories retained; omitted outcomes remain observed and explicit. |
| 5 | `expanded_poly_fullminute_candidate_dates.py` | Candidate CME day inventory | Determines the dates on which the prediction leg has observable history; this is not an entry selection by realized profit. |
| parallel | `kalshi_research_backfill.py`, `expanded_kalshi_collect.py`, `expanded_kalshi_source_dates.py` | Kalshi inventory/rules and observable-date inventory | Legacy inventory/cache and actual API endpoints are dependencies; current endpoints can omit expired events. `build_kalshi_analysis_panel.py` supplies rule parsing. |
| parallel | `build_cme_minute_panel.py` | Original 26-meeting as-of caches and panel | Privately acquired MBP-10, definition/status tables, original acquisition plan and dataset conditions. |
| next | `cme_choice_study.py` | `outputs/cme_choice_calendar.csv` | Named-contract definitions and original minute panel; calendar loadings are independent of price. |
| next | `expanded_cme_panel.py` | `expanded_cme_minute_panel.parquet` | Original caches plus expanded/delta/delta2 DBN jobs, metadata and source-day inventories. Both event and receive timestamps are retained; unknown reference state fails closed. |
| evaluation only | `expanded_terminal_cash.py` | `expanded_terminal_cash_reference.parquet` | Saved FRED EFFR CSV, calendar choices; independently rounded delivery-month terminal prices. This is an ex-post label, not an entry feature. |
| next | `expanded_poly_study.py` | Baseline candidate/economics references | Original terminal reference and earlier regression outputs required by the historical source. |
| next | `tail_threshold_poly.py`, then `tail_threshold_poly_study.py` | `tail_thresholds/poly_binary_tau{1,3,5}_*.parquet` | Dated 1%/3%/5% exploratory sensitivity; original source retains exact baseline assertions. Helper imports include `analyze_fed_edges.py`, `edge_stats.py`, `time_edge_study.py`. |
| optional prior-grid reproduction | `execution_parameter_poly.py` | `outputs/execution_parameter_poly_entries.csv` | Original 36-rule exploratory comparisons. This is prior research, not a fresh parameter optimization. |

The source-preserving builders retain references to old baseline diagnostics such as `work/terminal_cash_reference.parquet`, `work/cme_final_asof_quality.csv`, and `outputs/data_acquisition_manifest.csv`. These are **required private snapshot inputs** when running their original `main()` functions; they are not generated by `build_db.py`. They contain market data or acquisition-specific context and are not fabricated or silently skipped in this repository. The included source and schema map make the boundary inspectable. Reconstructing a new snapshot requires a newly versioned acquisition manifest and regression baseline, not changing old input hashes to conceal a mismatch.

## 3. Prepared account inputs and latest depth replay

```text
fixed T5_E4_P3 candidate features + next-minute source panels
    -> existing capital100k.select_signals / execution_observations
    -> work/capital100k/{signals,executions}.parquet

named-month daily ZQ settlements + original flags + fixed source date range
    -> prepare_account_inputs.py::build_daily_marks
    -> work/capital100k/daily_marks_with_flags.parquet
    -> existing evaluation_data -> marks_prepared.parquet + evaluation.json

same named months + full prior settlement history
    -> liquidity_only/prepare_history_audit.py
    -> work/liquidity_only/history_marks.parquet

prepared inputs + actual rule metadata + five private Telonex books
    -> research/depth_replay/research_source/work/depth_replay/{prepare_books,replay}.py
    -> account ledgers, sizing certificates, trade/scenario summaries
```

`prepare_account_inputs.py` is an adapter around the already published signal and execution functions. It does not run the old account allocation policy or choose a new parameter. The original daily-marks extraction has now been made explicit; all 7,930 source rows were compared exactly before packaging.

The complete **minimum latest-replay field list** is in [required_private_inputs.json](../research/depth_replay/research_source/required_private_inputs.json). Additional upstream column schemas are in [private_input_schemas.json](../data_pipeline/config/private_input_schemas.json). That latter file contains column names/types only, no market-data observations.

## 4. What is and is not reproduced publicly

| Claim | Public verification status |
|---|---|
| Bundled trade profits, account return denominators and summary arithmetic | Runnable from included derived CSVs. |
| Analytical payoff identities on synthetic examples | Runnable without market data. |
| Historical source-code chain and source/configuration hashes | Inspectable and hash-verifiable. |
| Paid-request controls | Tested with a fake client; no account contact. |
| Original private daily-mark extraction | Compared exactly on the author's original inputs; original raw rows are excluded. |
| Full latest replay from licensed raw quote tapes | Requires the private inputs and point-in-time manifests; not asserted by public CI. |
| Current live prices, live fills, broker-specific margins, or historical metadata revisions | Not certified by this repository. |
