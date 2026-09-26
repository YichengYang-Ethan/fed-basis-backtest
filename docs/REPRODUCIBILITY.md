# Reproducibility levels

There are three different tasks. Public verification and source inspection require no market-data entitlement. Private replay and acquisition require the applicable data access.

## A. Verify the public research outputs

Python 3.10+; no API keys or third-party Python packages are needed for the core checks:

```sh
python3 research/depth_replay/model/verify_results.py
python3 research/depth_replay/model/payoff_model.py --self-test
python3 research/depth_replay/research_source/verify_source_snapshot.py
make verify
```

The verifier reconciles the published aggregate trade/scenario outputs, return denominators and calendar annualization. The analytical model uses synthetic inputs to expose payoff and calendar assumptions. Source verification checks the portable inspection snapshot. Publication verification checks the selected repository files against the manifest and restricted-content policy.

This is **not** independent recovery of source prices, a historical fill simulation, or a reconstruction of the omitted per-leg cash ledger. Public projection deliberately removes quantities and cash components that could reconstruct licensed market prices. Exact scope is described in the [bundle README](../research/depth_replay/README.md).

## B. Inspect and replay with entitled prepared inputs

The actual replay source is included in [research_source](../research/depth_replay/research_source/README.md). Its external file schemas, source fingerprints, environment variables and path adaptations are documented there. Use a separate private working directory and output location; do not put a private database or feeds into tracked paths.

The reference entrypoint begins from prepared execution, mark, history, outcome and book inputs. Supplying only the public CSVs is insufficient. The source reproduces the selected historical baseline; it is not a complete plug-and-play trading application.

## C. Rebuild upstream source data and prepared panels

Start with the integrated [data layer](../data_pipeline/README.md), [pipeline map](PIPELINE_MAP.md) and [private reproduction instructions](PRIVATE_REPRODUCTION.md). These distinguish original pullers, newer minute/depth builders and the handoff to the replay. Some provider inventories and licensed historical snapshots are external inputs. Acquisition is optional and may require paid entitlements; no public verification command purchases data.

Original source hashes and adaptation notes identify the preserved research implementation. A full end-to-end private rebuild has not been rerun merely to publish this repository. Live APIs may return changed histories, changed schemas or different inventory coverage, so independently record retrieval times, raw hashes, rule versions and fees.

## Figures and report

Figure source tables are under `reports/figures/`. With optional plotting dependencies installed, run:

```sh
make figures
```

The public brief's source is `reports/build_brief.py`; its PDF and Markdown reflect the same result vintage. Install `requirements-public.txt` in a separate environment, run `make figures`, then `python3 reports/build_brief.py` to regenerate the PDF and Markdown. A PDF must be rendered and visually inspected after changing its content.

## Research controls for future work

Before new evaluation, define the calendar universe, contract-state map, timestamp/freshness rules, entry/exit family, fee assumptions, capital constraints and primary statistic. Preserve all rejections and failed executions. Keep test events chronological and untouched; do not tune a threshold on these nine outcomes and then call the resulting result out-of-sample.
