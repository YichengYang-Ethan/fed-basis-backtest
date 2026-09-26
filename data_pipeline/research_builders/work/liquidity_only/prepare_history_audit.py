"""Bounded audit extract only. No acquisition, strategy selection or account replay."""
import os
from pathlib import Path
from pathlib import Path
import importlib.util, json, hashlib
import pandas as pd

ROOT=Path(__file__).resolve().parents[2]
DEST=ROOT/'work/liquidity_only'
SRC=Path(os.environ['FOMC_DATA_ROOT']) / 'raw/cme/databento'
spec=importlib.util.spec_from_file_location('existing_capital100k_base',ROOT/'work/capital100k/backtest.py')
base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)
ex=pd.read_parquet(ROOT/'work/capital100k/executions.parquet')
months=sorted(set(ex.leg_near)|set(ex.leg_far))
a=pd.read_parquet(SRC/'zq_outrights.parquet');a=a[a.dm.isin(months)].copy()
b=pd.read_parquet(SRC/'zq_settlements.parquet',columns=['trade_date','instrument_id','ts_recv','price','stat_flags','delivery_month'])
b['trade_date']=pd.to_datetime(b.trade_date)
# Source delivery_month is a CME symbol suffix (e.g. G4), not YYYY-MM.
b=b[b.instrument_id.isin(a.instrument_id)].copy()
keys=['instrument_id','trade_date','ts_recv','price']
assert not a.duplicated(keys).any()
assert not b.duplicated(keys).any()
x=a.merge(b[keys+['stat_flags']],on=keys,how='left',validate='one_to_one',indicator=True)
assert x['_merge'].eq('both').all()
assert not x.duplicated(['trade_date','dm']).any()
x=x[['trade_date','dm','price','ts_recv','stat_flags','instrument_id']].sort_values(['trade_date','dm']).reset_index(drop=True)
x['available_ts']=[base.mark_clock(d,r) for d,r in zip(x.trade_date,x.ts_recv)]
assert (x.available_ts>=x.ts_recv.astype('int64')/1e9).all()
x.to_parquet(DEST/'history_marks.parquet',index=False)
prov={
 'purpose':'Full existing history for the same 18 fixed delivery months; separate audit extract, no old prepared marks changed',
 'source_files':{str(SRC/n):hashlib.sha256((SRC/n).read_bytes()).hexdigest() for n in ['zq_outrights.parquet','zq_settlements.parquet']},
 'executions_sha256':hashlib.sha256((ROOT/'work/capital100k/executions.parquet').read_bytes()).hexdigest(),
 'clock_implementation':'work/capital100k/backtest.py::mark_clock; max(next US federal business day 09 ET after trade_date, next such 09 ET not before ts_recv)',
 'clock_source_sha256':hashlib.sha256((ROOT/'work/capital100k/backtest.py').read_bytes()).hexdigest(),
 'strict_flag_join':keys,'join_unmatched':0,'duplicates_trade_date_dm':0,
 'rows':len(x),'delivery_months':months,'min_trade_date':str(x.trade_date.min().date()),'max_trade_date':str(x.trade_date.max().date()),
 'stat_flags_counts':{str(k):int(v) for k,v in x.stat_flags.value_counts().items()},
 'all_available_ge_receipt':True,
 'limitations':['Original source keeps latest available daily records; overwritten preliminary and prior revision versions cannot be reconstructed.',
 'Any pre-entry use MUST filter available_ts < execution_ts (this is also stricter than ts_recv < execution_ts). Final rows received after an entry cannot replace missing earlier versions.',
 'US federal business-day 09 ET is an explicit funding proxy, not a verified CME/FCM margin-call clock.',
 'stat_flags 2 rows are actual preliminary rather than certified final; these remain identified.',
 'Past maximum is a historical sample statistic, not a bound on future cash demand or a liquidation guarantee.',
 'No IBKR rows merged; no paid data; no changes to previous account marks.']}
(DEST/'provenance.json').write_text(json.dumps(prov,indent=2)+'\n')
print(json.dumps({k:prov[k] for k in ['rows','min_trade_date','max_trade_date','stat_flags_counts','join_unmatched','duplicates_trade_date_dm']},indent=2))
rows=[]
for r in ex.itertuples():
 z=x[x.available_ts.lt(r.execution_ts)]
 n=z[z.dm.eq(r.leg_near)].set_index('trade_date');f=z[z.dm.eq(r.leg_far)].set_index('trade_date')
 joined=n[['price','stat_flags','ts_recv','available_ts']].join(f[['price','stat_flags','ts_recv','available_ts']],lsuffix='_near',rsuffix='_far',how='inner').sort_index()
 q=joined.tail(60)
 zsp=q.price_near-q.price_far
 loss=lambda v:max(0.,float(-v.min())) if len(v.dropna()) else None
 spread5=loss(-r.cme_direction_sign*4167*zsp.diff(5))
 far5=loss(r.cme_direction_sign*4167*q.price_far.diff(5))
 spread15=max([loss(-r.cme_direction_sign*4167*zsp.diff(h)) for h in range(1,6)]) if len(q)>5 else None
 far15=max([loss(r.cme_direction_sign*4167*q.price_far.diff(h)) for h in range(1,6)]) if len(q)>5 else None
 union=n.index.union(f.index)
 within=union[(union>=q.index.min())&(union<=q.index.max())] if len(q) else union[:0]
 unmatched=len(within.difference(q.index))
 rows.append(dict(meeting=r.meeting_date,execution_status=r.execution_status,near=r.leg_near,far=r.leg_far,common_prior_dates=len(joined),window_rows=len(q),first=str(q.index.min().date()) if len(q) else None,last=str(q.index.max().date()) if len(q) else None,unmatched_known_leg_dates_in_window=unmatched,flagged_prelim_leg_values=int(q.stat_flags_near.eq(2).sum()+q.stat_flags_far.eq(2).sum()),spread_max_5session_loss=spread5,far_max_5session_loss=far5,spread_max_1to5session_loss=spread15,far_max_1to5session_loss=far15,max_of_spread_far_1to5=max(spread15,far15) if spread15 is not None else None))
out=pd.DataFrame(rows);out.to_csv(DEST/'fullhistory_preentry_60session_coverage.csv',index=False)
print(out.to_string(index=False))
