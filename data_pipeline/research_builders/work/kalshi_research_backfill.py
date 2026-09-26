#!/usr/bin/env python3
"""Free, provenance-preserving Kalshi collection; no credentials or trading endpoints."""
import os
from pathlib import Path
import collections
import csv
import datetime as dt
import gzip
import hashlib
import json
import math
import pathlib
import time
from zoneinfo import ZoneInfo

import pandas as pd
import requests

WORK = pathlib.Path(__file__).resolve().parent
OUT = Path(os.environ['FOMC_DATA_ROOT']) / 'raw/kalshi/research_backfill_20260917'
BASE = 'https://api.elections.kalshi.com/trade-api/v2'
OUT.mkdir(parents=True, exist_ok=True)
(OUT / 'responses').mkdir(exist_ok=True)
(OUT / 'candles').mkdir(exist_ok=True)
STARTED = dt.datetime.now(dt.timezone.utc).isoformat()
SESSION = requests.Session()
SESSION.headers['User-Agent'] = 'Mozilla/5.0'
last_call = 0
requests_used = []


def iso(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat()


def epoch(value):
    return int(dt.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp())


def save_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n')


def request(path, params=None):
    """Cache exact successful responses. Retry transient failures twice, then record failure."""
    global last_call
    params = params or {}
    spec = {'method': 'GET', 'url': BASE + path, 'params': params}
    rid = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()[:24]
    meta_path = OUT / 'responses' / (rid + '.meta.json')
    body_path = OUT / 'responses' / (rid + '.json.gz')
    if meta_path.exists() and body_path.exists():
        meta = json.loads(meta_path.read_text())
        if meta['status'] == 200:
            raw = gzip.decompress(body_path.read_bytes())
            if hashlib.sha256(raw).hexdigest() != meta['response_sha256']:
                raise RuntimeError('Cached response hash mismatch ' + rid)
            requests_used.append(dict(meta, reused_cache=True))
            return json.loads(raw), rid, 200
    attempts = []
    raw = b''
    status = None
    for attempt in range(3):
        time.sleep(max(0, 0.52 - (time.monotonic() - last_call)))
        stamp = dt.datetime.now(dt.timezone.utc).isoformat()
        try:
            response = SESSION.get(spec['url'], params=params, timeout=45)
            status = response.status_code
            raw = response.content
            attempts.append({'attempt': attempt + 1, 'fetched_at': stamp, 'status': status})
        except requests.RequestException as error:
            status = -1
            raw = json.dumps({'error': str(error)}).encode()
            attempts.append({'attempt': attempt + 1, 'fetched_at': stamp, 'status': status, 'error': str(error)})
        last_call = time.monotonic()
        if status == 200 or (0 < status < 500 and status != 429):
            break
        if attempt < 2:
            time.sleep(2 ** (attempt + 1))
    body_path.write_bytes(gzip.compress(raw, mtime=0))
    meta = dict(spec, request_id=rid, status=status, attempts=attempts, fetched_at=attempts[-1]['fetched_at'],
                response_path=str(body_path), response_bytes=len(raw), compressed_bytes=body_path.stat().st_size,
                response_sha256=hashlib.sha256(raw).hexdigest(), compressed_sha256=hashlib.sha256(body_path.read_bytes()).hexdigest())
    save_json(meta_path, meta)
    requests_used.append(meta)
    with (OUT / 'requests.jsonl').open('a') as f:
        f.write(json.dumps(meta) + '\n')
    if status != 200:
        return None, rid, status
    return json.loads(raw), rid, status


def pages(path, list_key, params):
    values = []
    cursor = None
    while True:
        query = dict(params)
        if cursor:
            query['cursor'] = cursor
        body, rid, status = request(path, query)
        if status != 200:
            raise RuntimeError(f'Inventory request failed {path}: {status}, {rid}')
        values.extend(body.get(list_key, []))
        next_cursor = body.get('cursor')
        if not next_cursor:
            return values
        if next_cursor == cursor:
            raise RuntimeError('Nonadvancing inventory cursor')
        cursor = next_cursor


def number(value):
    if value in (None, ''):
        return None
    return float(value)


def normalize(c, historical):
    row = {'ts': int(c['end_period_ts']), 'interval_minutes': 1}
    for component in ('yes_bid', 'yes_ask', 'price'):
        obj = c.get(component) or {}
        for field in ('open', 'high', 'low', 'close', 'mean', 'previous'):
            # Historical API uses decimal-dollar strings without a _dollars suffix.
            # Current API uses _dollars; legacy integer fields, if present, are cents.
            if historical:
                value = number(obj.get(field))
            elif field + '_dollars' in obj:
                value = number(obj.get(field + '_dollars'))
            else:
                value = number(obj.get(field))
                value = value / 100 if value is not None else None
            row[component + '_' + field] = value
    row['volume'] = number(c.get('volume') if historical else c.get('volume_fp', c.get('volume')))
    row['open_interest'] = number(c.get('open_interest') if historical else c.get('open_interest_fp', c.get('open_interest')))
    return row


def main():
    manifest_rows = list(csv.DictReader((WORK.parent / 'outputs/data_acquisition_manifest.csv').open()))
    windows = sorted(set((r['start'], r['end']) for r in manifest_rows if r['stage'] == 'candidate_26'))
    cutoff, _, _ = request('/historical/cutoff')
    historical = pages('/historical/markets', 'markets', {'series_ticker': 'KXFEDDECISION', 'limit': 1000})
    current = pages('/markets', 'markets', {'series_ticker': 'KXFEDDECISION', 'limit': 1000})
    hist_ids = {m['ticker'] for m in historical}
    markets = {m['ticker']: m for m in historical}
    markets.update({m['ticker']: m for m in current})
    save_json(OUT / 'market_inventory.json', {'historical': historical, 'current': current})
    series = {}
    for ticker in ('KXFEDDECISION', 'FEDDECISION'):
        body, rid, status = request('/series/' + ticker)
        series[ticker] = {'status': status, 'request_id': rid, 'body': body}
    save_json(OUT / 'series_snapshots.json', series)
    local = pd.read_parquet(OUT.parent / 'markets.parquet')
    local_candles = pd.read_parquet(OUT.parent / 'candles.parquet', columns=['market_id', 'ts', 'interval'])
    local_files = []
    for name in ('markets.parquet', 'candles.parquet', 'trades.parquet', 'manifest.json'):
        f = OUT.parent / name
        local_files.append({'path': str(f), 'bytes': f.stat().st_size, 'sha256': hashlib.sha256(f.read_bytes()).hexdigest()})
    save_json(OUT / 'existing_input_inventory.json', {'files': local_files, 'market_rows': len(local),
            'candle_rows': len(local_candles), 'note': 'Read-only inventory. Fresh API responses retained for reproducibility; original files unchanged.'})
    by_event = collections.defaultdict(list)
    for market in markets.values():
        by_event[market['event_ticker']].append(market)
    mapping = []
    selected = []
    missing = []
    for start, meeting in windows:
        meeting_dt = dt.date.fromisoformat(meeting)
        month_title = meeting_dt.strftime('%B %Y').lower()
        matches = []
        for event, legs in by_event.items():
            exact = any((m.get('occurrence_datetime') or m.get('close_time', ''))[:10] == meeting for m in legs)
            title_month = any(month_title in m.get('title', '').lower() for m in legs)
            if exact or title_month:
                matches.append((event, legs, 'occurrence_or_close_exact_date' if exact else 'title_month_only_review_required'))
        if not matches:
            missing.append({'meeting': meeting, 'start': start, 'reason': 'No inventory event matches date or explicit month-year title. Not inferred from ticker.'})
        for event, legs, basis in sorted(matches):
            ev, rid, status = request('/events/' + event, {'with_nested_markets': 'true'})
            entry = {'meeting': meeting, 'window_start_utc': start + 'T00:00:00+00:00',
                     'core_window_end_utc_exclusive': meeting + 'T00:00:00+00:00',
                     'event_ticker': event, 'mapping_basis': basis, 'market_count': len(legs),
                     'market_tickers': sorted(m['ticker'] for m in legs), 'event_metadata_status': status,
                     'event_metadata_request_id': rid}
            mapping.append(entry)
            for m in sorted(legs, key=lambda x: x['ticker']):
                selected.append((entry, m))
    save_json(OUT / 'meeting_mapping.json', {'windows': windows, 'mappings': mapping, 'missing_meetings': missing})
    print(f'PLAN meetings={len(windows)} mapped={len(windows)-len(missing)} events={len(mapping)} markets={len(selected)}', flush=True)
    summaries = []
    for i, (mapping_row, market) in enumerate(selected):
        ticker = market['ticker']
        is_hist = ticker in hist_ids
        start = max(epoch(mapping_row['window_start_utc']), epoch(market['open_time']))
        end = epoch(market['close_time'])
        core_end = epoch(mapping_row['core_window_end_utc_exclusive'])
        rows = []
        failed = []
        request_ids = []
        cur = start
        while cur < end:
            chunk_end = min(cur + 3 * 86400, end)
            path = (f'/historical/markets/{ticker}/candlesticks' if is_hist else
                    f'/series/KXFEDDECISION/markets/{ticker}/candlesticks')
            body, rid, status = request(path, {'start_ts': cur, 'end_ts': chunk_end, 'period_interval': 1})
            request_ids.append(rid)
            if status == 200:
                for candle in body.get('candlesticks', []):
                    row = normalize(candle, is_hist)
                    row.update(meeting=mapping_row['meeting'], event_ticker=market['event_ticker'], ticker=ticker,
                               source='historical' if is_hist else 'current', request_id=rid,
                               in_core_window=epoch(mapping_row['window_start_utc']) <= row['ts'] < core_end,
                               before_or_at_close=row['ts'] <= end)
                    rows.append(row)
            else:
                failed.append({'start_ts': cur, 'end_ts': chunk_end, 'status': status, 'request_id': rid})
            cur = chunk_end
        df = pd.DataFrame(rows)
        duplicate_rows = 0
        conflicting_duplicates = 0
        outside_rows = 0
        null_bid = null_ask = invalid_price = crossed = 0
        missing_dates = []
        max_gap = None
        if len(df):
            duplicate_rows = int(df.duplicated(['ticker', 'ts']).sum())
            value_columns = [c for c in df if c not in ('request_id',)]
            for _, group in df[df.duplicated(['ticker', 'ts'], keep=False)].groupby(['ticker', 'ts']):
                if len(group[value_columns].drop_duplicates()) > 1:
                    conflicting_duplicates += 1
            df = df.sort_values('ts').drop_duplicates(['ticker', 'ts'], keep='last')
            outside_rows = int(((df.ts < start) | (df.ts > end)).sum())
            null_bid = int(df.yes_bid_close.isna().sum())
            null_ask = int(df.yes_ask_close.isna().sum())
            cols = [x for x in df if x.startswith(('yes_bid_', 'yes_ask_', 'price_'))]
            invalid_price = int((((df[cols] < 0) | (df[cols] > 1)).any(axis=1)).sum())
            crossed = int((df.yes_bid_close > df.yes_ask_close).sum())
            dates = set(pd.to_datetime(df.ts, unit='s', utc=True).dt.strftime('%Y-%m-%d'))
            expected_dates = [x.strftime('%Y-%m-%d') for x in pd.date_range(pd.to_datetime(start, unit='s', utc=True).normalize(), pd.to_datetime(end, unit='s', utc=True).normalize(), freq='D')]
            missing_dates = [x for x in expected_dates if x not in dates]
            max_gap = float(df.ts.diff().max()) if len(df) > 1 else None
            df.to_parquet(OUT / 'candles' / (ticker + '.parquet'), index=False)
        else:
            missing_dates = [x.strftime('%Y-%m-%d') for x in pd.date_range(pd.to_datetime(start, unit='s', utc=True).normalize(), pd.to_datetime(end, unit='s', utc=True).normalize(), freq='D')]
        prior = local_candles[(local_candles.market_id == ticker) & (local_candles.interval == 1) & local_candles.ts.between(start, end)]
        summary = {'meeting': mapping_row['meeting'], 'ticker': ticker, 'event_ticker': market['event_ticker'],
                   'mapping_basis': mapping_row['mapping_basis'], 'source': 'historical' if is_hist else 'current',
                   'start_ts': start, 'end_ts': end, 'start_utc': iso(start), 'end_utc': iso(end),
                   'close_time_et': dt.datetime.fromtimestamp(end, ZoneInfo('America/New_York')).isoformat(),
                   'fetched_rows_before_dedup': len(rows), 'rows': len(df), 'duplicate_rows': duplicate_rows,
                   'conflicting_duplicate_keys': conflicting_duplicates, 'rows_outside_requested_lifetime': outside_rows,
                   'null_bid_close': null_bid, 'null_ask_close': null_ask, 'invalid_price_rows': invalid_price,
                   'crossed_close_rows': crossed, 'max_internal_gap_seconds': max_gap,
                   'first_ts': int(df.ts.min()) if len(df) else None, 'last_ts': int(df.ts.max()) if len(df) else None,
                   'core_window_rows': int(df.in_core_window.sum()) if len(df) else 0,
                   'no_candle_utc_dates': missing_dates, 'request_ids': request_ids, 'failed_chunks': failed,
                   'existing_minute_rows_in_window': len(prior),
                   'existing_timestamps_missing_from_new': len(set(prior.ts) - set(df.ts if len(df) else [])),
                   'output': str(OUT / 'candles' / (ticker + '.parquet')) if len(df) else None}
        summaries.append(summary)
        save_json(OUT / 'qa_in_progress.json', summaries)
        print(f'[{i+1}/{len(selected)}] {ticker} rows={len(df)} failed={len(failed)} no_bar_days={len(missing_dates)}', flush=True)
    completed = dt.datetime.now(dt.timezone.utc).isoformat()
    qa = {'started_at': STARTED, 'completed_at': completed, 'cost_usd': 0, 'window_count': len(windows),
          'mapped_meeting_count': len(windows) - len(missing), 'mapped_event_count': len(mapping),
          'market_count': len(summaries), 'rows': sum(x['rows'] for x in summaries),
          'core_window_rows': sum(x['core_window_rows'] for x in summaries),
          'empty_market_count': sum(x['rows'] == 0 for x in summaries),
          'failed_chunk_count': sum(len(x['failed_chunks']) for x in summaries),
          'duplicate_rows_before_dedup': sum(x['duplicate_rows'] for x in summaries),
          'conflicting_duplicate_keys': sum(x['conflicting_duplicate_keys'] for x in summaries),
          'invalid_price_rows': sum(x['invalid_price_rows'] for x in summaries),
          'crossed_close_rows': sum(x['crossed_close_rows'] for x in summaries),
          'rows_outside_requested_lifetime': sum(x['rows_outside_requested_lifetime'] for x in summaries),
          'missing_meetings': missing, 'per_market': summaries,
          'limitations': ['API emits sparse candles; an absent minute or date is not proven collection loss and is not forward-filled.',
                         'Candles carry OHLC quotes and volume, not executable depth or quote-update timestamps.',
                         'Price units differ by historical/current endpoint; parser handles these explicitly.',
                         'Series fee metadata and rules are current snapshots, not verified historical fee vintages.',
                         'CME manifest ends at meeting UTC midnight; in_core_window excludes meeting-day candles, although close extension is retained.',
                         'A month-title match without exact occurrence/close date is flagged for review; alternate events are not silently merged.']}
    save_json(OUT / 'qa_report.json', qa)
    files = []
    for f in sorted(OUT.rglob('*')):
        if f.is_file() and f.name != 'manifest.json':
            files.append({'path': str(f.relative_to(OUT)), 'bytes': f.stat().st_size,
                          'sha256': hashlib.sha256(f.read_bytes()).hexdigest()})
    save_json(OUT / 'manifest.json', {'started_at': STARTED, 'completed_at': completed, 'cost_usd': 0,
              'script': str(pathlib.Path(__file__).resolve()), 'script_sha256': hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),
              'selection_manifest': str(WORK.parent / 'outputs/data_acquisition_manifest.csv'),
              'selection_manifest_sha256': hashlib.sha256((WORK.parent / 'outputs/data_acquisition_manifest.csv').read_bytes()).hexdigest(),
              'requests': requests_used, 'files': files, 'qa_path': str(OUT / 'qa_report.json')})
    print('DONE ' + json.dumps({k: v for k, v in qa.items() if k not in ('per_market', 'limitations')}), flush=True)


if __name__ == '__main__':
    main()
