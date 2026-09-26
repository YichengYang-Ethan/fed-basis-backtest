import os
from pathlib import Path
from pathlib import Path
import pandas as pd, duckdb
W=Path(__file__).resolve().parent; D=Path(os.environ['FOMC_DATA_ROOT']) / 'raw/poly';ET='America/New_York'
m=pd.read_parquet(W/'expanded_poly_market_metadata.parquet')
m=m[m.predecision_rows.fillna(0)>0].copy()
m['start_ts']=m.lifetime_start.astype('int64')/1e9;m['end_ts']=m.lifetime_end.astype('int64')/1e9
m['early_resolution_anomaly']=(m.decision_utc-m.lifetime_end)>pd.Timedelta(days=1)
c=duckdb.connect();c.register('markets',m)
x=c.execute(f'''with candidates as (
 select m.meeting_date,m.event_id,m.yes_token_id,m.early_resolution_anomaly,p.fidelity,
 cast(ceil(p.ts/60.0)*60+60*r.range as bigint) anchor_ts,p.ts,m.start_ts,m.end_ts
 from markets m join read_parquet(['{D}/prices.parquet','{W}/expanded_poly_fullminute_parts/*.parquet'], union_by_name=true) p
 on m.market_id=p.market_id and m.event_id=p.event_id and m.yes_token_id=p.token_id
 cross join range(2) r
 where p.p between 0 and 1), local as (
 select *,timezone('America/New_York',to_timestamp(anchor_ts)) anchor_et from candidates
 where anchor_ts-ts between 0 and 60 and anchor_ts>=start_ts and anchor_ts<end_ts)
 select meeting_date,event_id,cast(cast(anchor_et as date) as varchar) date_et,early_resolution_anomaly,
 count(distinct anchor_ts) possible_fresh_anchor_count,min(anchor_ts) first_anchor_ts,max(anchor_ts) last_anchor_ts,
 count(distinct yes_token_id) n_legs_ever_fresh,
 count(distinct anchor_ts) filter(where fidelity=1) minute_source_possible_anchors,
 count(distinct anchor_ts) filter(where fidelity=60) hourly_source_possible_anchors
 from local where isodow(anchor_et)<=5 and cast(anchor_et as time)>='10:00:00' and cast(anchor_et as time)<='15:00:00'
 group by all order by meeting_date,date_et,event_id''').fetchdf()
x.to_csv(W/'expanded_poly_fullminute_cme_candidate_days.csv',index=False)
y=x[~x.early_resolution_anomaly].groupby('meeting_date').agg(start=('date_et','min'),end=('date_et','max'),n_dates=('date_et','nunique'),possible_fresh_anchor_count=('possible_fresh_anchor_count','sum')).reset_index()
y.to_csv(W/'expanded_poly_fullminute_cme_date_ranges.csv',index=False)
print(y.to_string(index=False));print('actual_possible',x.possible_fresh_anchor_count.sum(),'days',len(x))
