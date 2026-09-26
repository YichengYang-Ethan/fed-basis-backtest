"""Current cached observability calendar; no synthetic quote fill."""
import os
from pathlib import Path
from pathlib import Path
import pandas as pd,json
W=Path(__file__).resolve().parent;D=Path(os.environ['FOMC_DATA_ROOT']);O=W.parent/'outputs'
inv=json.load(open(W/'expanded_kalshi_inventory.json'));spec={s['ticker']:s for s in json.load(open(W/'expanded_kalshi_rules.json'))};frames=[]
orig=pd.read_parquet(D/'raw/kalshi/candles.parquet',columns=['market_id','interval','ts','bid_close','ask_close'])
orig=orig[orig.market_id.isin(spec)&orig['interval'].isin([1,60])].rename(columns={'market_id':'ticker','bid_close':'bid','ask_close':'ask'})
orig['source_cache']='original_raw';frames.append(orig)
for folder,name in [('research_backfill_20260917','prior_21d'),('expanded_20260917','expanded_lifetime')]:
 for p in (D/'raw/kalshi'/folder/'candles').glob('*.parquet'):
  if p.stem not in spec:continue
  q=pd.read_parquet(p,columns=['ts','yes_bid_close','yes_ask_close']).rename(columns={'yes_bid_close':'bid','yes_ask_close':'ask'})
  q['ticker']=p.stem;q['interval']=1;q['source_cache']=name;frames.append(q)
z=pd.concat(frames,ignore_index=True);z['meeting_date']=z.ticker.map({k:v['meeting_date'] for k,v in spec.items()});z['exact_primary']=z.ticker.map({k:v['mapping_strict_ok'] and v['move_exact_bp'] is not None for k,v in spec.items()})
ts=pd.to_datetime(z.ts,unit='s',utc=True).dt.tz_convert('America/New_York');z['date_et']=ts.dt.strftime('%Y-%m-%d');z['time_et']=ts.dt.strftime('%H:%M');z=z[(ts.dt.dayofweek<5)&ts.dt.hour.between(10,15)&((ts.dt.hour<15)|(ts.dt.minute==0))];z=z[z.date_et.le(z.meeting_date)]
z['valid']=z.bid.between(0,1)&z.ask.between(0,1)&z.bid.le(z.ask);z=z[z.valid&z.exact_primary];z=z[z.ts.le(z.ticker.map({k:int(pd.Timestamp(v['close_time']).timestamp()) for k,v in spec.items()}))]
z=z.drop_duplicates(['ticker','ts','interval'])
a=z.groupby(['meeting_date','date_et','interval']).agg(observed_main_leg_rows=('ts','size'),observed_main_legs=('ticker','nunique'),first_observed_ts=('ts','min'),last_observed_ts=('ts','max')).reset_index();a.to_parquet(W/'expanded_kalshi_source_dates.parquet',index=False);a.to_csv(W/'expanded_kalshi_source_dates.csv',index=False)
a.groupby(['meeting_date','interval']).agg(observed_dates=('date_et','nunique'),first_date=('date_et','min'),last_date=('date_et','max'),observed_rows=('observed_main_leg_rows','sum')).to_csv(O/'expanded_kalshi_source_date_coverage.csv')
print(a.groupby(['meeting_date','interval']).agg(dates=('date_et','nunique'),start=('date_et','min'),end=('date_et','max')).to_string())
