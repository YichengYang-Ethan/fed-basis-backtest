"""Bounded free GET backfill of identified historical Fed market lifetimes only."""
import os
from pathlib import Path
from pathlib import Path
import concurrent.futures,time,json,requests
import pandas as pd,duckdb
W=Path(__file__).resolve().parent;D=Path(os.environ['FOMC_DATA_ROOT']) / 'raw/poly';PART=W/'expanded_poly_fullminute_parts';PART.mkdir(exist_ok=True)
m=pd.read_parquet(W/'expanded_poly_market_metadata.parquet');m=m[(m.predecision_rows.fillna(0)>0)&~m.event_id.eq('fed-decision-in-september-568')]
b=duckdb.sql(f"select token_id,min(ts) filter(where fidelity=1) minute_first_ts,min(ts) history_first_ts from read_parquet('{D}/prices.parquet') group by token_id").df().set_index('token_id')
jobs=[]
for r in m.itertuples():
 for label,token in [('Yes',r.yes_token_id),('No',r.no_token_id)]:
  if token not in b.index:continue
  first=b.loc[token,'minute_first_ts'];rawfirst=b.loc[token,'history_first_ts'];start=max(int(r.lifetime_start.timestamp()),int(rawfirst)-60)
  until=min(int(first) if pd.notna(first) else int(r.decision_ts),int(r.lifetime_end.timestamp()))
  if until-start<=86400:continue
  jobs.append(dict(event_id=r.event_id,market_id=r.market_id,token_id=token,outcome=r.outcome,outcome_label=label,meeting_date=r.meeting_date,start_ts=start,until_ts=until))
(W/'expanded_poly_fullminute_jobs.json').write_text(json.dumps(jobs,indent=2))

def pull(job):
 path=PART/(job['token_id']+'.parquet');rec={**job,'requested_at_utc':str(pd.Timestamp.now(tz='UTC')),'method':'free public GET prices-history; fidelity1; no endTs','calls':0,'errors':[]}
 if path.exists():
  p=pd.read_parquet(path);rec.update(status='resume_existing',rows=len(p),first_ts=int(p.ts.min()) if len(p) else None,last_ts=int(p.ts.max()) if len(p) else None);return rec
 cur=job['start_ts'];pts={}
 for turn in range(5):
  if cur>=job['until_ts']:break
  h=None
  for retry in range(3):
   try:
    res=requests.get('https://clob.polymarket.com/prices-history',params={'market':job['token_id'],'startTs':cur,'fidelity':1},headers={'User-Agent':'Mozilla/5.0 (Fed history research read-only)'},timeout=90);rec['calls']+=1
    if res.status_code!=200:raise RuntimeError(f'HTTP{res.status_code}: {res.text[:200]}')
    payload=res.json();h=payload.get('history',[]);break
   except Exception as ex:rec['errors'].append(str(ex));time.sleep(1+retry)
  if h is None:rec['status']='request_failed';break
  for x in h:
   if job['start_ts']<=x['t']<job['until_ts']:pts[int(x['t'])]=float(x['p'])
  if not h:rec['status']='empty_response';break
  last=max(x['t'] for x in h)
  if last>=job['until_ts']-61:rec['status']='covered_to_local_minute_start';break
  if last<cur:rec['status']='no_progress';break
  cur=last+1
 else:rec['status']='bounded_call_limit'
 p=pd.DataFrame({'ts':sorted(pts)});p['p']=[pts[t] for t in p.ts]
 for col in ['event_id','market_id','token_id','outcome','outcome_label']:p[col]=job[col]
 p['venue']='polymarket';p['fidelity']=1
 if len(p):p.to_parquet(path,index=False,compression='zstd')
 rec.update(rows=len(p),first_ts=int(p.ts.min()) if len(p) else None,last_ts=int(p.ts.max()) if len(p) else None,completed_at_utc=str(pd.Timestamp.now(tz='UTC')))
 print(json.dumps({k:rec[k] for k in ['meeting_date','outcome_label','status','rows','calls']}),flush=True)
 return rec
out=[]
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
 for rec in pool.map(pull,jobs):
  out.append(rec);(W/'expanded_poly_fullminute_acquisition.json').write_text(json.dumps(out,indent=2))
print(json.dumps(dict(jobs=len(jobs),rows=sum(r['rows'] for r in out),calls=sum(r['calls'] for r in out),statuses=pd.Series([r['status'] for r in out]).value_counts().to_dict())),flush=True)
