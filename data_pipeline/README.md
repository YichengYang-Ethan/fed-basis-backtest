# Data acquisition and research preparation

This directory integrates the original `fed-pricing-db` acquisition/panel code and the selected upstream builders used before the latest account replay. **No raw market-data file or credential is included.** The default data location is the repository's ignored `local_data/` directory; set `FOMC_DATA_ROOT` to use another authorized location.

A frozen source/configuration snapshot identifies the release version; it is not evidence of prospective preregistration or untouched out-of-sample performance.

The two layers have different purposes:

| Layer | What is included | Reproduction boundary |
|---|---|---|
| `scripts/` | FRED, public prediction-market inventory/history collectors, ZQ settlement collector, optional IBKR bar collector, original DuckDB panel builder | Downloads require running each collector explicitly. The Databento collector defaults to a cost quote and requires a separate paid opt-in. |
| `research_builders/` | Actual metadata parsing, as-of minute joins, calendar exposure checks, liveness, tail sensitivity, delayed-entry preparation dependencies | Private source files, vendor job manifests, and earlier reference artifacts are required. Historical endpoints may no longer reproduce an old snapshot. |
| `config/` | Public event identifier seeds and column-only private-input schemas | A seed list is discovery metadata, not evidence that every market or rule was known at a past entry. |
| `tests/` | Mock-only tests of paid-request controls | No live vendor/API account is used. |

Start with [the pipeline map](../docs/PIPELINE_MAP.md), then [private reconstruction instructions](../docs/PRIVATE_REPRODUCTION.md). To check the shareable results without any vendor access, use the repository's public verification commands instead.

## Important implementation distinctions

- `build_db.py` is the original **daily exploratory panel**. Its CME table reads `raw/cme/ZQ*.parquet` produced by the legacy Yahoo collector. It does **not** silently substitute Databento settlements, nor does its daily basis view replace the later synchronized intraday replay. The authoritative settlement and latest intraday paths are separate and documented.
- `databento_zq_pull.py` preserves settlement filtering and global `(trade_date, symbol)` deduplication. A new command-line shell adds quote-before-download, explicit execution, persistent local budget reservations, atomic monthly caches, and no automatic retry after an uncertain request.
- `prepare_account_inputs.py` calls the existing frozen signal and delayed-execution functions. Its daily-mark extraction turns an originally ad-hoc, provenance-recorded step into a small explicit adapter. A read-only comparison matched all **7,930 rows and all five columns exactly** on the source snapshot; this does not verify current vendor data or a live brokerage path.
- `ibkr_zq_pull.py` requests contract details and historical bars with the API connection's read-only flag. Account identifiers are suppressed. It contains no order or margin-preview call.
- The optional requirements file is a dependency range, not a cross-platform lockfile. Original snapshots contain data-version and regression assertions; do not remove those assertions and call the resulting run an exact reproduction.

## Provenance

[source_manifest.json](source_manifest.json) records original and adapted source hashes. The upstream repository is [fed-pricing-db](https://github.com/YichengYang-Ethan/fed-pricing-db). The integrated builders preserve the historical equations and filters; packaging is not a new backtest or a claim that their limitations have been solved.
