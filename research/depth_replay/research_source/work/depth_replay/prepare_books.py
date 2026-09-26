"""Local, frozen-entry Polymarket books and cumulative ask-side cost curves.

No network, credentials, orders or source mutations. Books returned in memory
contain private quotes; only derived verification metadata is written by main().
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, localcontext
import hashlib
import json
from pathlib import Path
from collections.abc import Mapping

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

import sys as _snapshot_sys
_snapshot_sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from snapshot_paths import private_input, raw_input, output_path, expected_input

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = raw_input('poly/telonex_trial_20260920')
MANIFEST = PRIVATE / 'acquisition_manifest.json'
AVAILABILITY = private_input('outputs/data_supplement/telonex_availability.json')
OUTPUT = output_path('outputs/depth_replay/book_input_checks.json')


def _sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _decimal(value):
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, (int, np.integer)) and not isinstance(value, (bool, np.bool_)):
        result = Decimal(int(value))
    elif isinstance(value, (float, np.floating)):
        result = Decimal(str(float(value)))
    elif isinstance(value, str):
        result = Decimal(value)
    else:
        raise ValueError('Quantity/coefficient must be numeric or a decimal string')
    if not result.is_finite():
        raise ValueError('Quantity/coefficient must be finite')
    return result


def _clock_ns(values):
    """Preserve the exact Arrow double's fractional microseconds, never truncate."""
    result = np.empty(len(values), dtype=np.int64)
    with localcontext() as ctx:
        ctx.prec = 60
        for i, value in enumerate(values):
            if isinstance(value, (float, np.floating)):
                if not np.isfinite(value) or abs(value) >= 2**53:
                    raise ValueError('Nonfinite or imprecise microsecond clock')
                n = Decimal.from_float(float(value)) * 1000
            elif isinstance(value, (int, np.integer)):
                n = Decimal(int(value)) * 1000
            else:
                raise ValueError('Unsupported/missing source clock')
            if n != n.to_integral_value() or not 0 < n <= np.iinfo(np.int64).max:
                raise ValueError('Source clock cannot be represented exactly as nanoseconds')
            result[i] = int(n)
    return result


def _levels(levels, side):
    if not isinstance(levels, list) or not levels:
        raise ValueError(f'Empty or non-list {side} book')
    parsed = []
    for level in levels:
        if not isinstance(level, dict) or not isinstance(level.get('price'), str) or not isinstance(level.get('size'), str):
            raise ValueError('Book prices and sizes must be source decimal strings')
        price, size = Decimal(level['price']), Decimal(level['size'])
        if not price.is_finite() or not size.is_finite() or not 0 < price < 1 or size <= 0:
            raise ValueError('Invalid book price/size')
        parsed.append((price, size))
    prices = [p for p, _ in parsed]
    if any(not (b > a if side == 'ask' else b < a) for a, b in zip(prices, prices[1:])):
        raise ValueError(f'{side} prices are not strictly ordered')
    return parsed


def _execution_records(executions):
    if isinstance(executions, pd.DataFrame):
        frame = executions.copy()
        if 'meeting_date' not in frame and frame.index.name == 'meeting_date':
            frame = frame.reset_index()
        records = frame.to_dict('records')
    elif isinstance(executions, Mapping):
        records = []
        for key, value in executions.items():
            row = dict(value)
            row.setdefault('meeting_date', key)
            records.append(row)
    else:
        records = list(executions)
    result = {}
    for row in records:
        key = str(row['meeting_date'])[:10]
        if key in result:
            raise ValueError(f'Duplicate execution meeting: {key}')
        result[key] = row
    return result


def load_books(executions):
    """Return the five manifest books keyed by meeting_date.

    executions is a DataFrame, records sequence, or meeting-keyed mapping with
    execution_ts, actual_token_id and actual_buy_side. All five frozen meetings
    must be present. Invalid latest states fail; an older good book is never used.
    No files are written by this function.
    """
    rows = _execution_records(executions)
    manifest = json.loads(MANIFEST.read_text())
    jobs = manifest['jobs']
    if len(jobs) != 5 or len({j['meeting_date'] for j in jobs}) != 5:
        raise ValueError('Expected exactly five distinct frozen acquisition jobs')
    availability = {r['meeting_date']: r for r in json.loads(AVAILABILITY.read_text())}
    output = {}
    for job in jobs:
        meeting = job['meeting_date']
        if meeting not in rows:
            raise ValueError(f'Missing frozen execution: {meeting}')
        execution = rows[meeting]
        expected_token = str(execution['actual_token_id'])
        side = str(execution['actual_buy_side']).upper()
        timestamp = _decimal(execution['execution_ts'])
        if timestamp != timestamp.to_integral_value() or int(timestamp) != int(job['execution_ts']):
            raise ValueError(f'Execution timestamp changed: {meeting}')
        if side != 'YES' or str(job['outcome']).upper() != side or expected_token != job['asset_id']:
            raise ValueError(f'Actual token/YES identity mismatch: {meeting}')
        if job['status'] != 'complete' or job['channel'] != 'book_snapshot_full':
            raise ValueError('Only completed full-book files may be used')
        path = (PRIVATE / Path(job['path']).name).resolve()
        if not path.is_relative_to(PRIVATE.resolve()) or path.suffix != '.parquet':
            raise ValueError('Unexpected raw file location/type')
        digest = _sha(path)
        if digest != job['sha256']:
            raise ValueError(f'Acquisition file SHA mismatch: {meeting}')
        table = pq.read_table(path)
        if table.num_rows != job['rows']:
            raise ValueError('Manifest/source row count differs')
        if not pa.types.is_int64(table.schema.field('timestamp_us').type):
            raise ValueError('Expected int64 event microseconds')
        local_type = table.schema.field('local_timestamp_us').type
        if not (pa.types.is_int64(local_type) or pa.types.is_float64(local_type)):
            raise ValueError('Unexpected receipt clock schema')
        av_record = availability[meeting]
        av = av_record['availability']
        if av_record['actual_token_id'] != expected_token or av['asset_id'] != expected_token or av['outcome_id'] != 0:
            raise ValueError('Stored availability does not identify this YES token')
        expected = {'exchange': 'polymarket', 'asset_id': expected_token, 'outcome': 'Yes',
                    'slug': job['slug'], 'market_id': av['market_id']}
        identity_ok = all(set(table[name].to_pylist()) == {value} for name, value in expected.items())
        if not identity_ok or av['slug'] != job['slug']:
            raise ValueError(f'File/market/token identity mismatch: {meeting}')
        event = _clock_ns(table['timestamp_us'].to_numpy())
        receipt_raw = table['local_timestamp_us'].to_numpy()
        receipt = _clock_ns(receipt_raw)
        cutoff = int(timestamp) * 1_000_000_000
        # First choose the latest state already received. A future/invalid event
        # clock on that state must fail validation, never reveal an older good book.
        visible = np.flatnonzero(receipt <= cutoff)
        if not len(visible):
            raise ValueError(f'No causally visible snapshot: {meeting}')
        newest = int(receipt[visible].max())
        tied = visible[receipt[visible] == newest]
        # Resolve identical duplicates only. Conflicting latest receipt ties fail.
        tied_rows = [table.slice(int(i), 1).to_pylist()[0] for i in tied]
        if len({json.dumps(row, sort_keys=True) for row in tied_rows}) != 1:
            raise ValueError(f'Conflicting latest-receipt tie: {meeting}')
        selected_index = int(tied[-1])
        row = tied_rows[-1]
        event_ns, receipt_ns = int(event[selected_index]), int(receipt[selected_index])
        if not 0 <= cutoff-event_ns <= 60_000_000_000 or not 0 <= cutoff-receipt_ns <= 60_000_000_000:
            raise ValueError(f'Latest snapshot exceeds dual-clock 60-second freshness: {meeting}')
        if receipt_ns < event_ns:
            raise ValueError(f'Latest snapshot has negative vendor latency: {meeting}')
        with localcontext() as ctx:
            ctx.prec = 60
            bids, asks = _levels(row['bids'], 'bid'), _levels(row['asks'], 'ask')
            if not bids[0][0] < asks[0][0]:
                raise ValueError(f'Latest book is locked/crossed: {meeting}')
            total = sum((size for _, size in asks), Decimal(0))
            midpoint = (bids[0][0]+asks[0][0])/2
        output[meeting] = dict(meeting_date=meeting, execution_ts=int(timestamp),
            actual_token_id=expected_token, actual_buy_side='YES', outcome='Yes',
            market_id=av['market_id'], slug=job['slug'],
            asks=[{'price':level['price'], 'size':level['size']} for level in row['asks']],
            total_ask_size=float(total), total_ask_size_decimal=str(total), observed_mid=float(midpoint),
            source_age_sec=(cutoff-event_ns)/1e9, collector_age_sec=(cutoff-receipt_ns)/1e9,
            source_timestamp_ns=event_ns, collector_timestamp_ns=receipt_ns,
            schema=str(table.schema), path=str(path), path_sha256=digest,
            manifest_sha256=_sha(MANIFEST), availability_sha256=_sha(AVAILABILITY),
            selected_row_index=selected_index, latest_receipt_tie_rows=len(tied),
            rows=table.num_rows, bid_levels=len(bids), ask_levels=len(asks), quality_valid=True,
            receipt_fractional_microsecond_rows=int(np.count_nonzero(receipt_raw != np.floor(receipt_raw))),
            file_negative_vendor_latency_rows=int(np.count_nonzero(receipt < event)),
            selection_rule='Latest receipt<=cutoff first, then require event<=cutoff and both clock ages<=60s; validate only after selection; no older-book fallback')
    return output


def cost_curve(book, total_share_quantities, fee_coefficient=.05):
    """Cumulative purchase costs for Q shares against one frozen ask book.

    Returns same-length 1-D numpy arrays principal, fee, complete, avg_price.
    Out-of-depth Q has inf principal/fee, False complete and NaN avg_price;
    Q=0 has zero costs, True complete and NaN avg_price. Negative/nonfinite Q
    raises ValueError. Fee is summed per filled level: k*q*p*(1-p), with no
    unstated rounding rule. Floats use their shortest decimal representation;
    Decimal/string Q can express an exact fractional depth boundary.
    """
    values = np.asarray(total_share_quantities, dtype=object)
    if values.ndim != 1:
        raise ValueError('total_share_quantities must be one-dimensional')
    with localcontext() as ctx:
        ctx.prec = 60
        amounts = [_decimal(v) for v in values]
        coefficient = _decimal(fee_coefficient)
        if coefficient < 0 or any(q < 0 for q in amounts):
            raise ValueError('Negative quantity/fee coefficient is invalid')
        levels = _levels(book['asks'], 'ask')
        cumulative_q = [Decimal(0)]
        cumulative_cash = [Decimal(0)]
        cumulative_fee = [Decimal(0)]
        for p, size in levels:
            cumulative_q.append(cumulative_q[-1]+size)
            cumulative_cash.append(cumulative_cash[-1]+size*p)
            cumulative_fee.append(cumulative_fee[-1]+coefficient*size*p*(1-p))
        complete = np.asarray([q <= cumulative_q[-1] for q in amounts], dtype=bool)
        qfloat = np.asarray([float(q) for q in amounts], dtype=float)
        if not np.all(np.isfinite(qfloat)):
            raise ValueError('Quantity exceeds numerical array range')
        principal = np.full(len(values), np.inf)
        fee = np.full(len(values), np.inf)
        average = np.full(len(values), np.nan)
        zero = qfloat == 0
        principal[zero] = fee[zero] = 0.0
        valid = complete & ~zero
        if np.any(valid):
            bounds = np.asarray([float(q) for q in cumulative_q])
            index = np.searchsorted(bounds[1:], qfloat[valid], side='left')
            index = np.minimum(index, len(levels)-1)
            remainder = np.maximum(0., qfloat[valid]-bounds[index])
            prices = np.asarray([float(p) for p, _ in levels])
            cash_prefix = np.asarray([float(v) for v in cumulative_cash])
            fee_prefix = np.asarray([float(v) for v in cumulative_fee])
            principal[valid] = cash_prefix[index] + remainder*prices[index]
            fee[valid] = fee_prefix[index] + float(coefficient)*remainder*prices[index]*(1-prices[index])
            average[valid] = principal[valid]/qfloat[valid]
        return {'principal':principal, 'fee':fee, 'complete':complete, 'avg_price':average}


def _decimal_walk(book, quantity, coefficient=Decimal('.05')):
    """Independent direct per-level reference; does not use prefix/searchsorted."""
    with localcontext() as ctx:
        ctx.prec = 60
        left = quantity
        cash = charge = Decimal(0)
        for level in book['asks']:
            p, size = Decimal(level['price']), Decimal(level['size'])
            taken = min(left, size)
            cash += taken*p
            charge += coefficient*taken*p*(1-p)
            left -= taken
            if not left:
                break
        return left == 0, cash, charge


def main():
    executions_path = private_input('work/capital100k/executions.parquet')
    economics_path = private_input('outputs/hf_pm_report/reviewed_trade_economics.csv')
    if _sha(economics_path) != json.loads(MANIFEST.read_text())['source_entries_sha256']:
        raise ValueError('Original reviewed economics changed since frozen acquisition')
    executions = pd.read_parquet(executions_path)
    economics = pd.read_csv(economics_path).set_index('meeting_date')
    books = load_books(executions)
    previous = pd.read_csv(private_input('outputs/data_supplement/telonex_fixed_entry_depth.csv')).set_index('meeting_date')
    checks = []
    for meeting, book in books.items():
        r = economics.loc[meeting]
        q = _decimal(r.quantity); old_size = q*_decimal(r.packages)
        depth = Decimal(book['total_ask_size_decimal'])
        first_depth = Decimal(book['asks'][0]['size'])
        epsilon = Decimal('0.000001')
        cases = [('zero',Decimal(0)), ('one_share',Decimal(1)), ('one_package',q),
                 ('old_size',old_size), ('first_level_boundary',first_depth),
                 ('depth_below',depth-epsilon), ('depth_boundary',depth), ('depth_above',depth+epsilon)]
        curve = cost_curve(book, [amount for _, amount in cases])
        spots = []
        for i, (name, amount) in enumerate(cases):
            complete, cash, charge = _decimal_walk(book, amount)
            assert bool(curve['complete'][i]) == complete
            if complete:
                cash_error = abs(curve['principal'][i]-float(cash))
                fee_error = abs(curve['fee'][i]-float(charge))
                assert cash_error <= 1e-8 and fee_error <= 1e-8
                if amount:
                    assert abs(curve['avg_price'][i]-float(cash/amount)) <= 1e-12
                else:
                    assert curve['principal'][i] == curve['fee'][i] == 0 and np.isnan(curve['avg_price'][i])
            else:
                cash_error = fee_error = None
                assert np.isinf(curve['principal'][i]) and np.isinf(curve['fee'][i]) and np.isnan(curve['avg_price'][i])
            spots.append(dict(case=name, complete=complete, passed=True,
                              principal_abs_error_usd=cash_error, fee_abs_error_usd=fee_error))
        old = cost_curve(book, [old_size])
        principal_diff = abs(old['principal'][0]-previous.loc[meeting,'original_size_notional'])
        fee_diff = abs(old['fee'][0]-previous.loc[meeting,'original_size_model_fee'])
        assert principal_diff < 1e-8 and fee_diff < 1e-8
        checks.append({key:book[key] for key in ['meeting_date','execution_ts','actual_token_id','actual_buy_side',
            'schema','path','path_sha256','manifest_sha256','availability_sha256','selected_row_index',
            'source_timestamp_ns','collector_timestamp_ns','source_age_sec','collector_age_sec','rows',
            'latest_receipt_tie_rows','bid_levels','ask_levels','quality_valid','receipt_fractional_microsecond_rows',
            'file_negative_vendor_latency_rows','selection_rule']} | dict(
                old_total_quantity=str(old_size), decimal_spotchecks=spots,
                old_total_principal_abs_difference_usd=float(principal_diff),
                old_total_fee_abs_difference_usd=float(fee_diff),
                actual_token_and_yes_identity_verified=True, strict_order_positive_sizes_and_uncrossed=True))
    report = dict(created_at=datetime.now(timezone.utc).isoformat(), meetings=len(books), all_passed=True,
        source_execution_sha256=_sha(executions_path), source_economics_sha256=_sha(economics_path),
        module_sha256=_sha(Path(__file__)), checks=checks,
        clock_policy='Exact Decimal.from_float(double microseconds)*1000; latest receipt<=cutoff first; then require event<=cutoff, nonnegative latency and both ages<=60 seconds; no older-good-book fallback.',
        cost_policy='Each input Q independently sweeps the same full ask book cumulatively; principal and 0.05*q*p*(1-p) fee summed over actual consumed levels, never old VWAP scaled linearly.',
        fee_policy='Inherited 0.05 scenario coefficient, no claim about historical fee schedule or undocumented rounding.',
        output_policy='No raw price levels, best quotes, midpoint, depth-boundary quantities or per-boundary cash are written to public outputs. Private quotes exist only in returned in-memory books.',
        scope='Frozen five-entry static depth input; no API calls, purchases, re-selection, real orders or source changes.')
    OUTPUT.parent.mkdir(parents=True,exist_ok=True)
    OUTPUT.write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    print(json.dumps({'meetings':len(books),'all_checks_passed':True,'output':str(OUTPUT)}))


if __name__ == '__main__':
    main()
