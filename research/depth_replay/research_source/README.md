# Real model source inspection snapshot

These are the actual source components used by the latest frozen depth replay, with file-location changes for portability. They are separate from the small synthetic payoff model and the bundled-derived-data verifier. Equations, fees, margin scenarios, historical signal choices, sizing objective and cash-event ordering were not retuned.

The call chain is `work/depth_replay/replay.py` → `work/spread_credit_backtest/engine.py` and `runner.py` → `work/capital_efficiency/backtest.py` → `work/capital100k/backtest.py`. The risk wrapper imports `work/portfolio_capital/risk_model.py`. The replay imports `work/depth_replay/prepare_books.py` for strict receipt-first book selection and cumulative ask sweeps. The older modules contain inherited routines as well as the functions called by the current adapter; their old standalone entrypoints are not the current baseline.

Inspect without any private data:

```bash
python research_source/verify_source_snapshot.py
```

This only checks hashes and Python syntax. It neither imports the engine nor claims that a full private rerun was performed for this shareable package.

## External private inputs

The package contains no market-feed DBN, Parquet, DuckDB, quote panels, full books or opaque token/asset/condition identifiers. Readable public event slugs and exchange tickers remain as provenance; they are not credentials or account identifiers. Full replay requires the already-prepared private research tree and the authorized raw tree listed in `required_private_inputs.json`. The environment variables specify directories, not credentials:

```bash
export FOMC_PRIVATE_RESEARCH_ROOT="$PWD/../private_research"
export FOMC_RAW_ROOT="$PWD/../private_raw"
export FOMC_REPLAY_OUTPUT_ROOT="$PWD/../private_replay_output"
```

The first tree must retain its `work/` and `outputs/` layout. The raw root starts at `poly/` (the parent of the Polymarket source files). The output root is separate from both inputs. The full source needs NumPy, pandas and PyArrow; these are optional for bundled reproduction. With authorized complete inputs, the latest entrypoint is `python research_source/work/depth_replay/replay.py`. It may be computationally expensive and produces private intermediate grids. Do not redistribute those grids or newly generated quote-level outputs.

The acquisition-manifest paths for the five book files are remapped to their filenames under the configured raw root. No acquisition, authenticated API call or order function is included. Existing raw hashes are still checked before use. The copy of each frozen protocol preserves its original input-hash map as provenance; portable source/configuration hashes are updated for path-only adaptations. Other private-input hashes remain unchanged.

The minimum rerun begins from frozen prepared executions, marks, rules and outcome evaluation inputs. It is not a full rebuild of the source feeds or a new search over all possible signals. Earlier signal/panel inputs are documented separately for inspection of upstream selection; their complete acquisition/panel-build toolchain is not claimed to be bundled. A missing input should fail visibly rather than invoke a download or fabricate replacement observations.

Book selection uses the latest collector receipt at or before the execution cutoff, then checks the event clock, both ages, identity, price ordering and positive depth. It fails on a bad latest state rather than searching backwards for an older good one. Quantities sweep one frozen book cumulatively, with fees accumulated per consumed level. The internal cost function does not scale an old VWAP linearly to a new quantity.

Private-data rerun limitations remain the same as the published analysis: selected five-book coverage, unverified historical fee and broker-margin assumptions, no fill/queue simulation, finite historical VM scenarios, conditional two-state entry objective, explicit out-of-state risks, and no untouched out-of-sample validation.
