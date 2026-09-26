#!/usr/bin/env python3
"""Quote or explicitly buy ZQ statistics, preserving global settlement deduplication.

Default operation only gets cost estimates. Download requires --execute and a
positive --max-usd. The persistent ledger reserves 125% of each fresh quote BEFORE
requesting data, including failed/uncertain attempts. It is an estimate ceiling,
not a guarantee of a vendor invoice or a total account-wide spending cap.
"""
from __future__ import annotations
import argparse
from datetime import date, datetime, timezone
from decimal import Decimal
import fcntl
import hashlib
import json
import os
from pathlib import Path

from pipeline_config import DATA_ROOT


def month_windows(start, end):
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    if a.day != 1 or b.day != 1 or b <= a:
        raise ValueError('Use increasing YYYY-MM-01 boundaries; --end is exclusive.')
    while a < b:
        nxt = date(a.year + (a.month == 12), 1 if a.month == 12 else a.month + 1, 1)
        yield a.isoformat(), nxt.isoformat()
        a = nxt


def main(argv=None, client_factory=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--start', default='2022-01-01')
    p.add_argument('--end', default='2026-10-01', help='exclusive first day of month; fixed original research horizon')
    p.add_argument('--execute', action='store_true', help='explicit opt-in to paid historical requests')
    p.add_argument('--max-usd', type=Decimal, help='cumulative local ledger reserve ceiling; required to execute')
    a = p.parse_args(argv)
    if a.execute and (a.max_usd is None or not a.max_usd.is_finite() or a.max_usd <= 0):
        p.error('--execute requires a finite positive --max-usd')
    key = os.environ.get('DATABENTO_API_KEY')
    if not key:
        p.error('Set DATABENTO_API_KEY in the environment; never put a key in source or command arguments.')
    if client_factory is None:
        import databento as db
        client_factory = db.Historical
    client = client_factory(key)
    out = DATA_ROOT / 'raw/cme/databento'
    out.mkdir(parents=True, exist_ok=True)
    ledger_path = out / 'purchase_reserve_ledger.json'
    # One lock covers quote/re-check/reservation/download, so concurrent runs cannot
    # each spend against the same apparent local remainder.
    with (out / '.purchase.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else {'attempts': []}
        quotes = []
        for start, end in month_windows(a.start, a.end):
            req = dict(dataset='GLBX.MDP3', symbols=['ZQ.FUT'], stype_in='parent', schema='statistics', start=start, end=end)
            cache = out / '_monthly_statistics' / (start[:7] + '.parquet')
            if cache.exists():
                quotes.append({'start':start,'end':end,'cached':True,'quoted_usd':0.0})
                continue
            cost = Decimal(str(client.metadata.get_cost(**req)))
            if not cost.is_finite() or cost < 0:
                raise RuntimeError('Invalid vendor cost estimate; no download.')
            reserve = cost * Decimal('1.25')
            quotes.append({'start':start,'end':end,'cached':False,'quoted_usd':float(cost),'reserve_usd':str(reserve)})
            if not a.execute:
                continue
            used = sum((Decimal(x['reserve_usd']) for x in ledger['attempts']), Decimal(0))
            if used + reserve > a.max_usd:
                raise RuntimeError(f'Local reserve ceiling exceeded: {used}+{reserve}>{a.max_usd}; no request for {start}.')
            rec = dict(request=req, reserve_usd=str(reserve), quoted_usd=str(cost), status='reserved_before_request', timestamp_utc=datetime.now(timezone.utc).isoformat())
            ledger['attempts'].append(rec)
            ledger_path.write_text(json.dumps(ledger, indent=2)+'\n')
            try:
                result = client.timeseries.get_range(**req)
                frame = result.to_df()
                # Settlement stat_type 3. Retain the full monthly settlement subset
                # before global dedup; the final response can cross UTC chunks.
                frame = frame[frame.stat_type == 3].copy()
                import pandas as pd
                frame['trade_date'] = pd.to_datetime(frame.ts_ref).dt.date
                frame = frame.reset_index()[['trade_date','symbol','price','stat_flags','instrument_id','ts_recv']]
                cache.parent.mkdir(parents=True, exist_ok=True)
                tmp = cache.with_suffix('.parquet.tmp')
                frame.to_parquet(tmp, index=False)
                tmp.replace(cache)
                rec.update(status='complete', cache_sha256=hashlib.sha256(cache.read_bytes()).hexdigest())
            except Exception:
                rec['status'] = 'failed_or_uncertain_no_automatic_retry'
                raise
            finally:
                ledger_path.write_text(json.dumps(ledger, indent=2)+'\n')
        print(json.dumps({'mode':'execute' if a.execute else 'quote_only','requests':quotes,'quoted_new_usd':sum(x['quoted_usd'] for x in quotes),'ledger_is_not_vendor_invoice':True},indent=2))
        if a.execute:
            import pandas as pd
            frames = [pd.read_parquet(out / '_monthly_statistics' / (s[:7]+'.parquet')) for s, _ in month_windows(a.start,a.end)]
            if not frames:
                raise RuntimeError('No frames selected')
            allf = pd.concat(frames,ignore_index=True).sort_values('ts_recv').drop_duplicates(['trade_date','symbol'],keep='last').sort_values(['trade_date','symbol'],ignore_index=True)
            allf['delivery_month'] = allf.symbol.str.extract(r'ZQ([FGHJKMNQUVXZ]\d)$',expand=False)
            assert not allf.duplicated(['trade_date','symbol']).any()
            allf.to_parquet(out / 'zq_settlements.parquet',index=False)
            print(f'Wrote {len(allf):,} globally deduplicated settlement rows.')


if __name__ == '__main__':
    main()
