import os
from pathlib import Path
from pathlib import Path
import concurrent.futures,json,requests,time
import pandas as pd,duckdb
W=Path(__file__).resolve().parent;D=Path(os.environ['FOMC_DATA_ROOT']) / 'raw/poly';P=W/'expanded_poly_fullminute_parts'
m=pd.read_parquet(W/'expanded_poly_market_metadata.parquet');m=m[(m.predecision_rows.fillna(0)>0)&~m.event_id.eq('fed-decision-in-september-568')]
existing={x['token_id']:x for x in json.loads((W/'expanded_poly_fullminute_jobs.json').read_text())}
b=duckdb.sql(f"select token_id,min(ts) filter(where fidelity=1) minute_first_ts from read_parquet('{D}/prices.parquet') group by token_id").df().set_index('token_id')
jobs=[]
for r in m.itertuples():
 for label,token in [('Yes',r.yes_token_id),('No',r.no_token_id)]:
  if token not in b.index:continue
  until=existing[token]['start_ts'] if token in existing else b.loc[token,'minute_first_ts']
  if pd.isna(until):continue
  until=int(until);start=int(r.lifetime_start.timestamp())
  if until-start<=120:continue
  a=pd.date_range(r.lifetime_start.ceil('min'),pd.to_datetime(until,unit='s',utc=True),freq='min',inclusive='left').tz_convert('America/New_York')
  ok=(a.dayofweek<5)&((a.hour*60+a.minute)>=600)&((a.hour*60+a.minute)<=900)
  if not ok.any():continue
  jobs.append(dict(event_id=r.event_id,market_id=r.market_id,token_id=token,outcome=r.outcome,outcome_label=label,meeting_date=r.meeting_date,start_ts=start,until_ts=until))
def pull(j):
 out={**j,'requested_at_utc':str(pd.Timestamp.now(tz='UTC'))};pts={}
 try:
  res=requests.get('https://clob.polymarket.com/prices-history',params={'market':j['token_id'],'startTs':j['start_ts'],'fidelity':1},headers={'User-Agent':'Mozilla/5.0 (research read-only)'},timeout=75);out['status']=res.status_code;res.raise_for_status()
  h=res.json().get('history',[])
  for x in h:
   if j['start_ts']<=x['t']<j['until_ts']:pts[int(x['t'])]=float(x['p'])
 except Exception as ex:out['error']=str(ex)
 f=pd.DataFrame({'ts':sorted(pts)});f['p']=[pts[t] for t in f.ts]
 for k in ['event_id','market_id','token_id','outcome','outcome_label']:f[k]=j[k]
 f['venue']='polymarket';f['fidelity']=1
 if len(f):f.to_parquet(P/(j['token_id']+'.opening.parquet'),index=False,compression='zstd')
 out['rows']=len(f);return out
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:out=list(pool.map(pull,jobs))
(W/'expanded_poly_fullminute_opening_acquisition.json').write_text(json.dumps(out,indent=2));print(json.dumps(dict(jobs=len(jobs),rows=sum(x['rows'] for x in out),errors=[x for x in out if 'error' in x]),indent=2))
