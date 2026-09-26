#!/usr/bin/env python3
"""Public Kalshi lifetime archive, immutable originals; resumable exact response cache."""
import os
from pathlib import Path
import concurrent.futures as cf
import datetime as dt
import gzip, hashlib, json, math, pathlib, threading, time
import pandas as pd
import requests
from kalshi_research_backfill import normalize
from build_kalshi_analysis_panel import parse_leg
W=pathlib.Path(__file__).resolve().parent
D=Path(os.environ['FOMC_DATA_ROOT'])
OLD=D/'raw/kalshi/research_backfill_20260917'
RAW=D/'raw/kalshi/expanded_20260917'
O=W.parent/'outputs'
for p in [RAW/'responses',RAW/'candles',O]:p.mkdir(parents=True,exist_ok=True)
BASE='https://api.elections.kalshi.com/trade-api/v2'
LOCK=threading.Lock(); LAST=0.; LOCAL=threading.local()
def save(p,v):p.write_text(json.dumps(v,indent=2,default=str,allow_nan=False)+'\n')
def epoch(x):return int(pd.Timestamp(x).timestamp())
def request(path,params=None):
 global LAST
 params=params or {};spec={'method':'GET','url':BASE+path,'params':params}
 rid=hashlib.sha256(json.dumps(spec,sort_keys=True).encode()).hexdigest()[:24]
 for directory in [RAW,OLD]:
  mp=directory/'responses'/(rid+'.meta.json');bp=directory/'responses'/(rid+'.json.gz')
  if mp.exists() and bp.exists():
   m=json.loads(mp.read_text())
   if m['status']==200:return json.loads(gzip.decompress(bp.read_bytes())),m
 if not hasattr(LOCAL,'s'):
  LOCAL.s=requests.Session();LOCAL.s.headers['User-Agent']='Mozilla/5.0'
 attempts=[];raw=b'';status=-1
 for attempt in range(5):
  with LOCK:
   time.sleep(max(0,.24-(time.monotonic()-LAST)));LAST=time.monotonic()
  stamp=dt.datetime.now(dt.timezone.utc).isoformat()
  try:r=LOCAL.s.get(BASE+path,params=params,timeout=40);status=r.status_code;raw=r.content
  except requests.RequestException as e:raw=json.dumps({'error':str(e)}).encode();status=-1
  attempts.append({'attempt':attempt+1,'status':status,'fetched_at':stamp})
  if status==200 or (0<status<500 and status!=429):break
  time.sleep(min(20,2**attempt))
 bp=RAW/'responses'/(rid+'.json.gz');bp.write_bytes(gzip.compress(raw,mtime=0))
 m=dict(spec,request_id=rid,status=status,attempts=attempts,fetched_at=stamp,response_path=str(bp),response_sha256=hashlib.sha256(raw).hexdigest(),response_bytes=len(raw))
 save(RAW/'responses'/(rid+'.meta.json'),m)
 return (json.loads(raw) if status==200 else None),m

def pages(path,key,params):
 out=[];cursor='';ids=[]
 while True:
  b,m=request(path,dict(params,**({'cursor':cursor} if cursor else {})));ids.append(m['request_id'])
  if m['status']!=200:raise RuntimeError(m)
  out.extend(b.get(key,[]));n=b.get('cursor','')
  if not n:return out,ids
  if n==cursor:raise RuntimeError('cursor did not advance')
  cursor=n

def inventory():
 inv=json.loads((OLD/'market_inventory.json').read_text());request_evidence=[]
 # Refresh/discover older renamed series via official endpoint, retaining exact empty responses.
 for series in ['FEDDECISION','KXFEDDECISION']:
  for path,label in [('/historical/markets','historical'),('/markets','current')]:
   v,ids=pages(path,'markets',{'series_ticker':series,'limit':1000})
   request_evidence.append({'path':path,'series':series,'rows':len(v),'request_ids':ids})
   old={x['ticker']:x for x in inv[label]};old.update({x['ticker']:x for x in v});inv[label]=list(old.values())
 probes=[]
 for ev in ['FEDDECISION-23FEB','FEDDECISION-23FEB01','FEDDECISION-23MAR','FEDDECISION-23MAR22']:
  b,m=request('/events/'+ev,{'with_nested_markets':'true'});probes.append({'event_ticker':ev,'status':m['status'],'request_id':m['request_id'],'response':b})
 hist={m['ticker'] for m in inv['historical']};markets={m['ticker']:m for v in inv.values() for m in v}
 inst=pd.read_csv(Path(os.environ['FOMC_PROJECT_ROOT']) / 'data/fomc_instruments.csv')
 dates=set(inst.loc[inst.meeting_date.between('2023-01-01','2026-09-17'),'meeting_date'])
 meetings=[];excluded=[];selected=[]
 for ev in sorted(set(m['event_ticker'] for m in markets.values())):
  legs=[m for m in markets.values() if m['event_ticker']==ev];close_dates={m['close_time'][:10] for m in legs}
  if len(close_dates)!=1 or next(iter(close_dates)) not in dates:
   if min(m['close_time'] for m in legs)<'2026-09-18':excluded.append({'event_ticker':ev,'close_dates':sorted(close_dates),'reason':'No exact FOMC close date; title month alone insufficient'})
   continue
  md=next(iter(close_dates));ii=inst[inst.meeting_date.eq(md)].iloc[0]
  row={'meeting_date':md,'event_ticker':ev,'open_time':min(m['open_time'] for m in legs),'close_time':max(m['close_time'] for m in legs),'leg_count':len(legs),'mapping_basis':'close_date_exact_matches_FOMC_calendar','clean_cme_calendar_instrument':bool(pd.notna(ii.instrument)),'instrument':str(ii.instrument),'source':'historical' if all(m['ticker'] in hist for m in legs) else 'current','market_tickers':'|'.join(sorted(m['ticker'] for m in legs))}
  meetings.append(row)
  for m in legs:selected.append(dict(m,meeting_date=md,source='historical' if m['ticker'] in hist else 'current'))
 meetings=sorted(meetings,key=lambda x:x['meeting_date']);missing=sorted(dates-{m['meeting_date'] for m in meetings})
 save(RAW/'market_inventory.json',inv);save(W/'expanded_kalshi_inventory.json',{'meetings':meetings,'missing_meetings':missing,'excluded_events':excluded,'request_evidence':request_evidence,'earlier_event_probes':probes,'selected_markets':selected})
 pd.DataFrame(meetings).to_csv(O/'expanded_kalshi_inventory.csv',index=False)
 specs=[dict(parse_leg(m),meeting_date=m['meeting_date'],source=m['source']) for m in selected]
 save(W/'expanded_kalshi_rules.json',specs)
 return selected

def merge_intervals(intervals):
 out=[]
 for a,b in sorted(intervals):
  if not out or a>out[-1][1]+60:out.append([a,b])
  else:out[-1][1]=max(out[-1][1],b)
 return out

def gaps(start,end,covered):
 out=[];cur=start
 for a,b in merge_intervals(covered):
  if b<cur:continue
  if a>cur:out.append((cur,min(a,end)))
  cur=max(cur,b)
  if cur>=end:break
 if cur<end:out.append((cur,end))
 return [(a,b) for a,b in out if b>a]

def main():
 selected=inventory();print(f'Inventory {len(selected)} markets / {len(set(m["meeting_date"] for m in selected))} meetings',flush=True)
 # Prior request success spans, including empty intervals, are authoritative download coverage.
 old_meta={}
 for p in (OLD/'responses').glob('*.meta.json'):
  m=json.loads(p.read_text());q=m.get('params',{})
  if m['status']==200 and m['url'].endswith('/candlesticks') and q.get('period_interval')==1:
   ticker=m['url'].split('/')[-2];old_meta.setdefault(ticker,[]).append((q['start_ts'],q['end_ts']))
 orig=pd.read_parquet(D/'raw/kalshi/candles.parquet');orig=orig[orig['interval'].eq(1)]
 # Original normalized quotes contain requested 45-day minute windows. Conservatively reuse only observed min/max.
 original_frames={t:g for t,g in orig.groupby('market_id')}
 def market_job(m):
  ticker=m['ticker'];start=epoch(m['open_time']);end=epoch(m['close_time']);covered=old_meta.get(ticker,[]).copy();base=[]
  p=OLD/'candles'/(ticker+'.parquet')
  if p.exists():base.append(pd.read_parquet(p))
  if ticker in original_frames:
   q=original_frames[ticker].rename(columns={'bid_close':'yes_bid_close','ask_close':'yes_ask_close','vol':'volume','oi':'open_interest'}).copy()
   q['request_id']='original_raw_candles_parquet';q['source']='original_raw';base.append(q)
   covered.append((int(q.ts.min()),int(q.ts.max())))
  tasks=[]
  for a,b in gaps(start,end,covered):
   cur=a
   while cur<b:
    nxt=min(cur+4990*60,b);tasks.append((cur,nxt));cur=nxt
  ids=[];failed=[];newrows=[]
  for a,b in tasks:
   path=f'/historical/markets/{ticker}/candlesticks' if m['source']=='historical' else f'/series/KXFEDDECISION/markets/{ticker}/candlesticks'
   body,meta=request(path,{'start_ts':a,'end_ts':b,'period_interval':1});ids.append(meta['request_id'])
   if meta['status']!=200:failed.append({'start':a,'end':b,'status':meta['status'],'request_id':meta['request_id']});continue
   for c in body.get('candlesticks',[]):
    r=normalize(c,m['source']=='historical');r['request_id']=meta['request_id'];r['source']=m['source'];newrows.append(r)
  if newrows:base.append(pd.DataFrame(newrows))
  if base:
   q=pd.concat(base,ignore_index=True);q=q[q.ts.ge(start)&q.ts.le(end)].copy();dup=int(q.duplicated('ts').sum());q=q.sort_values('ts').drop_duplicates('ts',keep='last')
   q['ticker']=ticker;q['meeting_date']=m['meeting_date'];q['event_ticker']=m['event_ticker'];q['open_ts']=start;q['close_ts']=end
   keep=['ticker','meeting_date','event_ticker','ts','yes_bid_close','yes_ask_close','price_close','volume','open_interest','request_id','source','open_ts','close_ts']
   q=q.reindex(columns=keep);q.to_parquet(RAW/'candles'/(ticker+'.parquet'),index=False,compression='zstd')
  else:q=pd.DataFrame();dup=0
  summary={'ticker':ticker,'meeting_date':m['meeting_date'],'source':m['source'],'open_ts':start,'close_ts':end,'new_request_chunks':len(tasks),'failed_chunks':failed,'rows':len(q),'new_response_rows':len(newrows),'duplicates_deduped':dup,'first_ts':int(q.ts.min()) if len(q) else None,'last_ts':int(q.ts.max()) if len(q) else None,'request_ids':ids,'prior_success_spans':merge_intervals(covered),'output':str(RAW/'candles'/(ticker+'.parquet')) if len(q) else None}
  save(RAW/'candles'/(ticker+'.qa.json'),summary)
  print(f'{ticker}: rows={len(q)} extra_requests={len(tasks)} failures={len(failed)}',flush=True)
  return summary
 with cf.ThreadPoolExecutor(max_workers=6) as pool:
  results=list(pool.map(market_job,sorted(selected,key=lambda m:(m['meeting_date'],m['ticker']))))
 save(W/'expanded_kalshi_collection_qa.json',{'completed_at':dt.datetime.now(dt.timezone.utc).isoformat(),'markets':results,'new_chunk_count':sum(m['new_request_chunks'] for m in results),'failure_count':sum(len(m['failed_chunks']) for m in results),'total_rows':sum(m['rows'] for m in results),'originals_unmodified':True,'download_policy':'Full market open through close, 4990-minute chunks; exact prior successful spans and original observed min/max reused; raw HTTP success including empty response cached; no post-close marker retained.'})
 pd.DataFrame([{k:v for k,v in m.items() if not isinstance(v,(list,dict))} for m in results]).to_csv(W/'expanded_kalshi_market_coverage.csv',index=False)
 pd.DataFrame(results).groupby('meeting_date').agg(markets=('ticker','size'),candle_rows=('rows','sum'),new_chunks=('new_request_chunks','sum'),first_ts=('first_ts','min'),last_ts=('last_ts','max')).to_csv(O/'expanded_kalshi_collection_coverage.csv')
 print('FINISHED',flush=True)
if __name__=='__main__':main()
