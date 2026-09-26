#!/usr/bin/env python3
"""Verify the public derived-result projection using only the standard library.

Checks aggregate P&L/ROI/CAGR, metadata, file hashes and precision conventions.
It does not reconstruct source quotes or reconcile omitted per-leg cash ledgers.
"""
from pathlib import Path
from datetime import datetime
from collections import defaultdict
import argparse
import csv
import hashlib
import json
import math
import statistics

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'


def read(name):
    with (DATA / name).open(newline='', encoding='utf-8') as stream:
        return list(csv.DictReader(stream))


def num(row, name):
    value = row[name]
    if value == '':
        return None
    result = float(value)
    assert math.isfinite(result), name
    return result


def close(actual, expected, label, tolerance=0.00001):
    if actual is None or expected is None or abs(actual - expected) > tolerance:
        raise AssertionError(f'{label}: computed={actual}, supplied={expected}')


def verify():
    manifest = json.loads((DATA / 'source_manifest.json').read_text())
    assert manifest['public_projection'] and not manifest['raw_inputs_bundled']
    assert not (DATA / 'main_cash_events.csv').exists()
    for item in manifest['files']:
        path = ROOT / item['file']
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item['sha256'], item['file']
        with path.open(newline='', encoding='utf-8') as stream:
            reader = csv.DictReader(stream)
            assert reader.fieldnames == item['columns'], item['file']
            assert sum(1 for _ in reader) == item['rows'], item['file']
    trades, summary = read('trade_results.csv'), read('scenario_summary.csv')
    candidates, skips = read('candidate_inventory.csv'), read('skipped_candidates.csv')
    assert len(summary) == 12 and len(trades) == 108 and len(candidates) == 11
    assert len({r['trade_id'] for r in trades}) == len(trades)
    assert len({r['run_id'] for r in summary}) == len(summary)
    forbidden = {'packages', 'digital_quantity', 'CME_cash_to_cutoff_usd', 'PM_payout_to_cutoff_usd',
                 'cash_outlay_usd', 'pm_principal_usd', 'pm_model_fee_usd', 'entry_spread', 'initial_near_mark', 'initial_far_mark'}
    assert not (forbidden & set(trades[0]))
    assert all(r['venue'] == 'Polymarket' for r in candidates + trades)
    lookup = {r['candidate_id']: r for r in candidates}
    assert len(lookup) == 11
    march = next(r for r in candidates if r['meeting_date'] == '2024-03-20')
    close(num(march, 'original_yes_ladder_sum'), 1.285, 'March 2024 original ladder', 1e-12)
    assert march['ladder_sum_quality_flag'] == 'NOT_NORMALIZED_OVER_1'
    assert len(read('coverage_historical38.csv')) == 38
    assert len(read('coverage_latest12.csv')) == 12
    kalshi = read('kalshi_inventory28.csv')
    assert len(kalshi) == 28 and all(r['venue'] == 'Kalshi' for r in kalshi)
    assert len(read('pm_cost_impact.csv')) == 5
    by_trade, by_skip = defaultdict(list), defaultdict(list)
    for row in trades:
        by_trade[row['run_id']].append(row)
    for row in skips:
        by_skip[row['run_id']].append(row)
    results = []
    for sm in summary:
        run = sm['run_id']
        positions = by_trade[run]
        closed = [r for r in positions if r['status'] == 'CLOSED']
        assert len(closed) == int(sm['closed_positions'])
        assert len(positions) - len(closed) == int(sm['pending_positions'])
        assert len(by_skip[run]) == int(sm['skips'])
        assert len({r['meeting_date'] for r in positions}) == len(positions)
        profits, rois, floors = [], [], []
        for row in positions:
            candidate = lookup[row['candidate_id']]
            close(num(row, 'execution_ts'), num(candidate, 'execution_ts'), run + ' fixed execution clock', 0)
            assert row['meeting_date'] == candidate['meeting_date']
            assert row['status'] == 'CLOSED', 'Published projection currently contains closed research positions only'
            profit = num(row, 'financed_profit_usd')
            committed = num(row, 'standalone_committed_usd')
            assert committed > 0
            # Dollars are rounded to cents. The exact underlying ratio is retained
            # to six decimal percentage points, so bound numerator/denominator error.
            rounding_roi_bound = 100 * (0.0051 / committed + abs(profit) * 0.0051 / (committed * (committed - 0.0051))) + 0.00000051
            close(100 * profit / committed, num(row, 'realized_full_funding_roi_pct'), run + ' funding ROI', rounding_roi_bound)
            close(num(row, 'standalone_return_pct'), num(row, 'realized_full_funding_roi_pct'), run + ' ROI aliases', 0)
            close(100 * profit / num(sm, 'initial_cash_usd'), num(row, 'account_contribution_pct'), run + ' account contribution', 0.000006)
            profits.append(profit)
            rois.append(num(row, 'realized_full_funding_roi_pct'))
            floors.append(num(row, 'entry_two_state_floor_roi_pct'))
        total = math.fsum(profits)
        dollar_bound = 0.0051 * (len(closed) + 1)
        close(total, num(sm, 'sum_closed_profit_usd'), run + ' aggregate profit', dollar_bound)
        close(statistics.median(rois), num(sm, 'median_standalone_return_pct'), run + ' median funding ROI', 0.0000011)
        close(statistics.median(floors), num(sm, 'median_entry_two_state_floor_roi_pct'), run + ' median conditional floor ROI', 0.0000011)
        assert sum(p > 0 for p in profits) == int(sm['positive_closed'])
        assert sm['financing_path_valid'].lower() == 'true'
        years = (datetime.fromisoformat(sm['annualization_end_utc']) - datetime.fromisoformat(sm['annualization_start_utc'])).total_seconds() / (365.25 * 86400)
        close(years, num(sm, 'calendar_years'), run + ' original annualization window', 1e-12)
        initial, terminal = num(sm, 'initial_cash_usd'), num(sm, 'end_cash_usd')
        close(initial + total, terminal, run + ' completed account P&L', dollar_bound)
        close(terminal, num(sm, 'complete_financed_NAV'), run + ' complete NAV', 0)
        roi = 100 * (terminal / initial - 1)
        cagr = 100 * ((terminal / initial) ** (1 / years) - 1)
        close(roi, num(sm, 'realized_full_account_return_pct'), run + ' account return', 0.000006)
        close(cagr, num(sm, 'calendar_cagr_pct'), run + ' calendar CAGR', 0.000006)
        results.append({'run_id': run, 'closed_trades': len(closed), 'aggregate_profit_from_rounded_trades_usd': round(total, 2),
                        'terminal_cash_usd': terminal, 'account_return_pct': roi, 'calendar_cagr_pct': cagr,
                        'median_full_funding_roi_pct': statistics.median(rois), 'median_conditional_floor_roi_pct': statistics.median(floors)})
    return {'all_checks_passed': True, 'scenarios': len(summary), 'trade_rows': len(trades), 'unique_filled_meetings': len({r['meeting_date'] for r in trades}),
            'candidate_count': len(candidates), 'public_projection': True, 'cash_ledger_reconciliation_performed': False,
            'raw_data_replay_performed': False, 'results': results,
            'limitations': ['Aggregated derived research outputs only; omitted feed components and per-leg ledgers cannot be verified here.',
                            'Twelve overlapping scenarios reuse nine meetings; no untouched out-of-sample result.',
                            'Public dollar and percentage rounding uses explicit tolerances; model decisions were not rerun or retuned.',
                            'Hypothetical historical fees and broker margins; selected five-book coverage; fills and intraday solvency unverified.',
                            'March 2024 original YES ladder sum is 128.5% and remains an explicit quality warning.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', type=Path, help='Optional JSON report output path')
    args = parser.parse_args()
    report = verify()
    text = json.dumps(report, indent=2, allow_nan=False) + '\n'
    if args.write:
        args.write.parent.mkdir(parents=True, exist_ok=True)
        args.write.write_text(text)
    print(text, end='')


if __name__ == '__main__':
    main()
