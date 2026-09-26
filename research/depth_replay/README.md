# Depth-aware replay: public research release

This directory contains frozen historical research results, a small analytical payoff model, and an inspection snapshot of the actual replay engine. It is a baseline for further research, not an optimized or production trading strategy. No parameters or historical entries were changed to prepare this public release.

The empirical account results are **Polymarket plus ZQ futures**. The 28-meeting Kalshi inventory is a separate data-coverage layer and is not the source of these nine trades.

## Run the offline checks

From the repository root, using Python 3.10 or newer:

```sh
python research/depth_replay/model/verify_results.py
python research/depth_replay/model/payoff_model.py --self-test
python research/depth_replay/research_source/verify_source_snapshot.py
```

These commands use only the standard library. They require no credentials, network, paid data or private files. The first checks public derived-result arithmetic and provenance; the second checks exact synthetic payoff invariants; the third checks eight source hashes and syntax without importing the private-data-dependent engine.

## Primary frozen comparison

Use `book=all_signals_asof` and `maintenance950`. The simulated account starts with $100,000. Its annualization window is February 1, 2024 17:05 UTC through September 1, 2026 21:00 UTC. Results were frozen on September 20, 2026; this public projection was prepared on September 25.

| Cost scenario | Terminal cash | Account return | Calendar CAGR | Median realized trade funding ROI |
|---|---:|---:|---:|---:|
| Inherited midpoint + 2-cent proxy | $126,399.40 | 26.399403% | 9.496905% | 3.201660% |
| Five observed books replace the 2-cent proxy | $126,510.76 | 26.510756% | 9.534251% | 3.201660% |
| Five observed books plus an additional 2 cents | $123,765.82 | 23.765825% | 8.607702% | 3.201660% |

Each scenario contains the same nine completed meeting events, although modeled quantities can differ. The 12 configurations combine two evaluation books, three cost modes and two maintenance assumptions; they are **not 12 independent tests**. The other evaluation book ends September 18, 2026 03:59:59 UTC, so its CAGR differs even where terminal cash matches.

The updated primary scenario's median **entry two-state floor ROI** is 1.927572%, versus a 3.201660% median realized modeled trade ROI. The former assumes the retained two-state payoff and zero EFFR residual; it is not an unconditional expected return. These ratios divide by standalone committed capital, including digital cost, transaction costs and modeled support funding. Margin is a capital reservation, not a P&L expense. Account return divides total gain by the initial $100,000; CAGR uses elapsed calendar time, not repeated compounding of per-trade ROIs.

## Public data tables

| File | Rows | Meaning |
|---|---:|---|
| `data/scenario_summary.csv` | 12 | Cost/margin/sample scenarios and aggregate results |
| `data/trade_results.csv` | 108 | Nine completed positions repeated across 12 configurations; net research P&L, funding denominator and ROI |
| `data/skipped_candidates.csv` | 18 | Original non-fill decisions across configurations |
| `data/candidate_inventory.csv` | 11 | Frozen candidate clocks, calendar exposure, contract state, hedge ratio and quality metadata |
| `data/pm_cost_impact.csv` | 5 | Normalized full-size PM book slippage diagnostics; no raw book or exact position-size/cost decomposition |
| `data/coverage_historical38.csv` | 38 | Historical meeting eligibility and coverage inventory |
| `data/coverage_latest12.csv` | 12 | Later rolling-window coverage summaries |
| `data/kalshi_inventory28.csv` | 28 | Separate Kalshi contracts and candle-collection inventory |
| `data/data_dictionary.csv` | Field-level | Units, definitions and missing-value semantics |
| `data/source_manifest.json` | File-level | Source hashes, row counts, transformations and public hashes |

The 38-, 12- and 28-meeting inventories have different vintages and purposes. They are not a single filtering funnel. `candidate_id` joins a historical meeting across scenarios; `trade_id` identifies a position within one run. Empty values mean unavailable or inapplicable, never zero. See [the repository data catalog](../../docs/DATA_CATALOG.md).

## Public release boundary

This is a stricter projection than the private research package. It excludes raw feeds, DBN/Parquet/DuckDB files, order-book levels, CME quote/settlement/implied-price columns, exact per-trade package quantities, and per-leg cash components. The detailed cash-event ledger is omitted: variation-margin cash combined with position size could recover licensed settlement changes, and daily aggregation would still expose days with a single position.

Public trade and scenario dollar fields are rounded to cents; percentage returns are rounded to six decimal percentage points. The source manifest records transformations, not a license to redistribute underlying feeds. The verifier allows the resulting small rounding differences, including a few cents between the sum of rounded positions and a rounded account total. It checks aggregate P&L, funding ROI, account return, calendar CAGR, fixed execution metadata and file integrity. It **cannot independently reconcile the omitted cash ledger, reconstruct historical signals, certify fills or validate source feeds**.

Calendar-derived hedge quantities per one spread are retained because they are determined by contract mechanics and the FOMC calendar. Actual historical position sizes and their cash decomposition are omitted. Public event slugs and exchange tickers are provenance identifiers, not account data. Credentials, live account information and trading functions are absent.

## Model and assumptions

`model/payoff_model.py` uses invented inputs and exact rational arithmetic to demonstrate calendar exposure, integer hedge rounding, omitted-tail losses and contamination from another decision. Its economic futures sign `+1` means buy the near month and sell the far month. The archived empirical engine uses `cme_direction_sign=-1` for that economic direction. The synthetic model is separate from the actual replay engine in `research_source/`.

The real source snapshot preserves eight components, five protocols, external input schemas and source hashes. It documents the prepared inputs needed for a private-data replay. It does not promise a raw-feed-to-result rebuild from this directory alone. Read [research_source/README.md](research_source/README.md) before attempting a separately entitled rerun.

The baseline retains modeled PM fees of `0.05 * size * p * (1-p)`, $5 per CME spread package, $1,100 outright initial/$950 maintenance, and hypothetical complete paired requirements of $330/$300. The $904.153 maintenance case is a distinct reference sensitivity. None constitutes a verified historical broker invoice, account margin preview or SPAN calculation. Only five previously filled entries have full PM book replacement; four retain proxy costs. Static displayed depth does not establish queue priority, market impact or simultaneous cross-venue execution.

The March 20, 2024 candidate has an original YES-ladder sum of 1.285 (128.5%). The quality flag is retained, not repaired after observing results. A finite sample with nine positive modeled results does not establish a future 100% win probability. No untouched holdout, full tail hedge or intraday-solvency guarantee is supplied. The research opportunity is to test those assumptions and alternative structures under a controlled, forward protocol.
