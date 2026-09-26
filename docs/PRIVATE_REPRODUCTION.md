# Private reconstruction guide

This guide is for a researcher with their own data entitlements. It does not grant redistribution rights, contain API credentials, or place an order. The public CSV verification route needs none of these inputs; see the repository README first.

“Frozen” source/configuration refers to this release snapshot, not to parameters preregistered before the historical sample was observed.

There are three different operations:

1. **Verify the bundled results.** Available from the included derived data and independent arithmetic/payoff checks.
2. **Replay the fixed source snapshot with an existing authorized private dataset.** The published model source and exact private-input schema identify the dependencies.
3. **Acquire a new snapshot and rebuild all upstream tables.** The integrated collectors/builders provide the source chain, but historical metadata vintages, full vendor tapes and acquisition-specific reference artifacts must be supplied. This operation has not been certified as a one-command fresh download with identical results.

## Workspace and environment

Run setup from the repository root. A local virtual environment and `local_data/` are ignored by Git.

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r data_pipeline/requirements.txt
export FOMC_PROJECT_ROOT="$(pwd)"
export FOMC_DATA_ROOT="$FOMC_PROJECT_ROOT/local_data"
export FOMC_RESEARCH_ROOT="$FOMC_DATA_ROOT/research"
export FOMC_PRIVATE_RESEARCH_ROOT="$FOMC_RESEARCH_ROOT"
export FOMC_RAW_ROOT="$FOMC_DATA_ROOT/raw"
export FOMC_REPLAY_OUTPUT_ROOT="$FOMC_DATA_ROOT/replay_output"
python3 data_pipeline/scripts/stage_research_workspace.py
```

The staging tool copies an explicit source/configuration allowlist, never data or credentials. It refuses to overwrite a file with different content. The upstream builders preserve the original `work/` and `outputs/` layout inside the private workspace. The latest replay source remains under `research/depth_replay/research_source/` in the public checkout and reads private inputs through the environment variables above.

The requirements file defines compatible ranges, not a fully tested lock across all platforms. Record the resolved package versions and platform in a new run manifest. No paid collector runs during installation or staging.

## A. Original source data and daily panel

Inspect [inventory_seeds.json](../data_pipeline/config/inventory_seeds.json) first. It contains public event identifiers from the research universe, not a historical membership database. `make_inventories.py` refreshes metadata using public API endpoints and reports inaccessible/expired events instead of inventing empty histories.

A logical order is:

```bash
python3 data_pipeline/scripts/make_inventories.py
python3 data_pipeline/scripts/fetch_fred.py
python3 data_pipeline/scripts/build_meetings.py
python3 data_pipeline/scripts/poly_pull.py
python3 data_pipeline/scripts/poly_post.py
python3 data_pipeline/scripts/kalshi_pull.py
```

The prediction-market collectors make public network requests. They may take hours and can encounter changed/removed endpoints. Preserve responses, fetch times and failure reports. A current API response does not prove the metadata or resolved winner was available before an old entry.

Optional read-only IBKR collection is separate:

```bash
python3 data_pipeline/scripts/ibkr_zq_pull.py --probe
```

This expects a local Gateway/TWS session. It only requests contract details and historical bars. No account-specific guarantee of margin or execution follows from a successful bar request.

### Databento settlement quotes and explicit paid requests

Provide `DATABENTO_API_KEY` through your local environment/secret manager. Do not commit it, paste it into source, or pass it as a command-line argument.

The command below asks for cost estimates only:

```bash
python3 data_pipeline/scripts/databento_zq_pull.py --start 2022-01-01 --end 2026-10-01
```

Downloading requires **both** `--execute` and a finite positive `--max-usd` selected by the researcher after reviewing the quote. The maximum is cumulative against this collector's persistent local ledger, not a fresh allowance on every invocation. A 125% reserve of the fresh estimate is written before the vendor request. Failed or uncertain requests remain reserved, and the script does not automatically retry them. A local file lock prevents two instances from simultaneously using the same remainder. This is an estimate-based control; it cannot enforce a vendor invoice amount, purchases from other tools, taxes or an account-wide credit limit.

Do not remove the ledger to bypass its ceiling. Compare its estimates with the vendor account before reconciling an uncertain charge. Completed monthly caches are reused. After acquisition:

```bash
python3 data_pipeline/scripts/databento_zq_outrights.py
```

The settlement collector keeps the latest received record globally by `(trade_date, symbol)`, including preliminary/final pairs that cross UTC request boundaries. The outright converter resolves the single-digit contract year using the listed-month horizon. An IBKR overlap check runs only if the optional private IBKR table exists.

### Optional legacy DuckDB exploration

The original daily database builder additionally requires `raw/cme/manifest.json`, `raw/cme/ZQ*.parquet`, and `raw/cme/sofr/*.parquet`, historically generated by `fetch_cme_yf.py`:

```bash
python3 data_pipeline/scripts/fetch_cme_yf.py
python3 data_pipeline/scripts/build_db.py
```

`build_db.py` deletes/recreates **its selected local database output**, writes `build_report.json`, `TESTS.md` and `PANEL_COVERAGE.md` under `FOMC_DATA_ROOT`, and returns a nonzero status on failed integrity checks. Inspect the report rather than using `--skip-tests` to hide anomalies.

This legacy CME table is not the authoritative Databento table. Do not treat its continuous series or daily basis alignment as a replacement for the synchronized latest account replay. The original Yahoo symbol list and calendar horizon are fixed to the archived project date; refreshing them is a new source version.

## B. Reconstruct the intraday research inputs

The selected actual producers are staged in `$FOMC_RESEARCH_ROOT/work/`; [PIPELINE_MAP.md](PIPELINE_MAP.md) gives their order and inputs. For example, after authorized `raw/poly/{markets,prices}.parquet` exist:

```bash
python3 "$FOMC_RESEARCH_ROOT/work/expanded_poly_inventory.py"
python3 "$FOMC_RESEARCH_ROOT/work/expanded_poly_fetch_fullminute.py"
python3 "$FOMC_RESEARCH_ROOT/work/expanded_poly_fill_opening.py"
python3 "$FOMC_RESEARCH_ROOT/work/expanded_poly_build_fullminute_panel.py"
python3 "$FOMC_RESEARCH_ROOT/work/expanded_poly_principal_panel.py"
python3 "$FOMC_RESEARCH_ROOT/work/expanded_poly_liveness.py"
python3 "$FOMC_RESEARCH_ROOT/work/expanded_poly_fullminute_candidate_dates.py"
```

CME intraday preparation requires authorized DBN files and manifests for the original `intraday_20260917` set and the `expanded_20260917`, `expanded_delta_20260917`, and `expanded_delta2_20260917` additions. The exact saved snapshot also contains decoded definition/status references, dataset-condition metadata, and original selection caches. These are not outputs of the settlement-only collector.

The original job manifest schema includes `jobs[]` with `id`, `request` (`dataset`, `schema`, `symbols`, `start`, `end`, symbology), `path`, `sha256`, `expected_records`, and `status`; some original jobs also record meeting and instrument kind. Relocate `path` to your authorized file while preserving and checking its content hash. Do not include signed download URLs or keys in a shareable manifest. The decoder verifies source hashes/counts and retains bad latest quotes instead of silently replacing them with older good quotes.

The original calendar-choice producer depends on the original minute panel and definitions. It is followed by the expanded panel producer. The original sources intentionally retain baseline references and exact-count assertions. Additional private references include:

- `outputs/data_acquisition_manifest.csv`, the original 26-meeting request plan.
- `work/cme_final_asof_quality.csv`, an original last-as-of comparison.
- `work/terminal_cash_reference.parquet`, the earlier terminal-price reference.
- `work/effr_fresh.csv`, saved FRED CSV, and the expanded calendar/terminal outputs.
- Kalshi discovery/rule metadata and cached responses used to generate `expanded_kalshi_source_dates.csv`.

Those files must be supplied from the same snapshot or regenerated under a **new version with explicit differences**. The repository does not ship source prices disguised as reference fixtures. It also does not relabel unknown observations as valid history.

Then follow the map for `expanded_poly_study.py`, `tail_threshold_poly.py`, and `tail_threshold_poly_study.py`. The 1%/3%/5% tables are historical sensitivity outputs; they do not turn exploratory parameter selection into untouched out-of-sample evidence. The latest replay keeps T5_E4_P3 fixed rather than selecting whichever newly rebuilt rule performs best.

## C. Materialize the account inputs without running a parameter search

Once the candidate, delayed-quote, rule metadata and terminal files exist:

```bash
python3 data_pipeline/scripts/prepare_account_inputs.py
python3 "$FOMC_RESEARCH_ROOT/work/liquidity_only/prepare_history_audit.py"
```

The first command calls the frozen `select_signals`, `execution_observations` and `evaluation_data` functions from the published account source. It creates:

- `work/capital100k/signals.parquet`: feature-only deterministic entry selection.
- `work/capital100k/executions.parquet`: fixed route/token and 60-second delayed observations, with explicit missing-fill reasons.
- `work/capital100k/daily_marks_with_flags.parquet`: authoritative settlement rows for the selected months in the recorded date range.
- `work/capital100k/marks_prepared.parquet`: those marks plus the original availability proxy.
- `work/capital100k/evaluation.json`: terminal prices and resolved labels, loaded after selection.

The daily-mark adapter joins flags by exact `(trade_date, instrument_id, ts_recv, price)` and refuses duplicates or unmatched source rows. Its 7,930-row equality check applies to the saved research snapshot, not future acquisitions. Earlier preliminary versions already discarded by the source collector cannot be reconstructed. The second command creates a wider pre-entry history extract for the same named months, preserving strict `available_ts` cutoffs.

## D. Replay with private depth inputs

Use the field-level contract in [required_private_inputs.json](../research/depth_replay/research_source/required_private_inputs.json) and the source README. In addition to prepared inputs, the depth replay expects five authorized Polymarket order-book files, their acquisition/availability manifests, and CME fixed-entry depth checks. None is supplied here. A midpoint is not a replacement for a missing book.

The published source path is:

```bash
python3 research/depth_replay/research_source/work/depth_replay/replay.py
```

This command is meaningful only after **all** required private inputs and the original protocol hashes have been checked. It makes no data purchases and submits no orders. New files or a fresh vendor snapshot will not automatically reproduce historical hashes. Record a new run version and an explicit difference analysis; do not remove mismatches to claim original-result reproduction.

The replay includes modeled fee coefficients, staged support funds and delayed settlement clocks. These are not a broker's historical account margin record or a guarantee against liquidation. Historical all-positive model trades do not establish a future win probability.

## Validation performed for this publication

- All integrated Python source files compiled without executing collectors.
- Four fake-client tests checked quote-only default behavior, budget rejection, explicit execution opt-in, and retained reservations after an uncertain request.
- A private source-workspace staging smoke test passed.
- Daily settlement preparation matched the existing five-column, 7,930-row table exactly.
- No new paid data request, account order, or full private backtest was run for publication.

Public checks, source compilation and private input compatibility are separate validation claims. The repository does not claim that a new end-to-end live download was completed or that all third-party historical endpoints remain unchanged.
