# Offline model and public-result verification

Run from the repository root with Python 3.10 or newer:

```sh
python research/depth_replay/model/verify_results.py
python research/depth_replay/model/payoff_model.py --self-test
python research/depth_replay/research_source/verify_source_snapshot.py
```

These standard-library commands make no network, account or trading requests.

`verify_results.py` checks the public CSV hashes and row counts, candidate identity and execution clocks, 108 trade-result rows, funding ROI, medians, aggregate account P&L, account return and calendar CAGR. Dollar fields are rounded to cents and percentage returns to six decimal percentage points; checks use explicit bounds for rounding. `verification_report.json` records the release-time result. Use `--write PATH` to save another result.

The public projection deliberately omits exact position sizes, per-leg cash components and the detailed cash-event ledger. The verifier does not reconcile that private ledger, reconstruct quoted signals or independently reproduce model fills. Twelve configurations reuse nine historical events; repeated rows do not increase the independent sample.

`payoff_model.py` is an exact-arithmetic teaching model with invented inputs in `synthetic_examples.json`. It illustrates calendar exposure, signed futures P&L, digital payouts, integer hedge rounding and tail states. All prices and dates in its examples are synthetic. Its economic spread sign `+1` is long near/short far; the empirical archive uses `cme_direction_sign=-1` for that direction.

```sh
python research/depth_replay/model/payoff_model.py --example matched_two_state
python research/depth_replay/model/payoff_model.py --examples-file my_scenarios.json
```

The actual replay engine is provided separately in `../research_source/`. Its syntax/hash check runs offline. A full private-data replay requires separately entitled inputs and optional packages listed in `requirements.txt`; it was not rerun as part of this public projection. See the [research release guide](../README.md) for fees, margins, coverage and sample limitations.
