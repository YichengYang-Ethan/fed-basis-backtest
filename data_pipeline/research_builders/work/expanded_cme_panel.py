"""Expand local CME as-of research coverage; never acquires data or places orders.

Reuse paid MBP-10 selection caches and decode new MBP-1 at identical UTC
minute anchors. A bad latest record is retained, not replaced by an older good
quote. Definitions, status, expiry, units, size and both source clocks gate use.
"""
import os
from pathlib import Path
from pathlib import Path
import json, hashlib, argparse
import numpy as np
import pandas as pd
import databento as db
import build_cme_minute_panel as old

W=Path(__file__).resolve().parent; O=W.parent/'outputs'
BASE=Path(os.environ['FOMC_DATA_ROOT']) / 'raw/cme/databento'
RAW=BASE/'expanded_20260917'; OLD=BASE/'intraday_20260917'
ET='America/New_York'
NUM=['ts_recv','ts_event','publisher_id','instrument_id','flags','sequence','ts_in_delta','depth','bid_px_00','ask_px_00','bid_sz_00','ask_sz_00','bid_ct_00','ask_ct_00']
COLS=['anchor_ns','symbol','present','source_job','source_file','ts_recv_ns','ts_event_ns','publisher_id','instrument_id','flags','sequence','ts_in_delta','depth','bid_px_00_nano','ask_px_00_nano','bid_sz_00','ask_sz_00','bid_ct_00','ask_ct_00','source_row','action','side']

def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(8*1024*1024),b''):h.update(b)
 return h.hexdigest()

def anchors(start,end):
 a=pd.Timestamp(start);b=pd.Timestamp(end)
 if a.tzinfo is None:a=a.tz_localize('UTC')
 if b.tzinfo is None:b=b.tz_localize('UTC')
 days=pd.date_range(a.tz_convert(ET).date(),b.tz_convert(ET).date(),freq='B')
 if not len(days):return pd.DatetimeIndex([],tz='UTC')
 x=pd.DatetimeIndex(np.concatenate([pd.date_range(str(d.date())+' 10:00',str(d.date())+' 15:00',freq='min',tz=ET).tz_convert('UTC').values for d in days])).tz_localize('UTC')
 return x[(x>=a)&(x<b)]

def scan(j,chunk=500000):
 cache=W/'expanded_cme_selection_cache';cache.mkdir(exist_ok=True)
 p=cache/(j['id']+'.parquet');mp=cache/(j['id']+'.json')
 if p.exists() and mp.exists():
  m=json.loads(mp.read_text());assert m['sha256']==j['sha256'];return p,m
 a=anchors(j['request']['start'],j['request']['end']);ans=a.asi8;n=len(a)
 midnight=a.tz_convert(ET).normalize().tz_convert('UTC').asi8
 symbols=j['request']['symbols'].split(',')
 state={s:{c:np.full(n,-1,dtype=np.int64) for c in NUM+['source_row']} for s in symbols}
 texts={s:{c:np.full(n,'',dtype=object) for c in ['action','side']} for s in symbols}
 checks={};ci=np.unique(np.linspace(0,n-1,min(n,9),dtype=int)) if n else []
 rows=0;prior=-1;chunks=0
 store=db.DBNStore.from_file(j['path']);assert str(store.schema) in ['mbp-1','mbp-10']
 for frame in store.to_df(price_type='fixed',pretty_ts=False,map_symbols=True,count=chunk):
  f=frame.reset_index();chunks+=1;r=f.ts_recv.to_numpy(dtype='int64')
  assert len(r) and r[0]>=prior and np.all(r[1:]>=r[:-1]);prior=int(r[-1])
  assert set(f.symbol.unique())<=set(symbols)
  for symbol,g in f.groupby('symbol',sort=False):
   receive=g.ts_recv.to_numpy(dtype='int64');idx=np.searchsorted(receive,ans,side='right')-1
   ok=idx>=0;ok[ok]&=receive[idx[ok]]>=midnight[ok];dest=np.flatnonzero(ok);take=idx[ok]
   if len(dest):
    pick=g.iloc[take]
    for c in NUM:state[symbol][c][dest]=pick[c].to_numpy(dtype='int64')
    state[symbol]['source_row'][dest]=rows+g.index.to_numpy(dtype='int64')[take]
    for c in texts[symbol]:texts[symbol][c][dest]=pick[c].to_numpy()
   for i in ci:
    q=g[(g.ts_recv>=midnight[i])&(g.ts_recv<=ans[i])]
    if len(q):checks[symbol,int(i)]={c:int(q.iloc[-1][c]) for c in NUM}
  rows+=len(f)
 assert rows==j['expected_records'],(j['id'],rows,j['expected_records'])
 frames=[];nchecks=0
 for symbol in symbols:
  st=state[symbol];present=st['ts_recv']>=0
  for i in ci:
   q=checks.get((symbol,int(i)));assert bool(present[i])==(q is not None)
   if q:assert all(st[c][i]==v for c,v in q.items());nchecks+=1
  out={'anchor_ns':ans,'symbol':symbol,'present':present,'source_job':j['id'],'source_file':Path(j['path']).name}
  for c,v in st.items():
   k=c+'_ns' if c in ['ts_recv','ts_event'] else c+'_nano' if '_px_' in c else c
   x=pd.array(v,dtype='Int64');x[~present]=pd.NA
   if '_px_' in c:x[x==old.UNDEF]=pd.NA
   if '_sz_' in c:x[x==old.USIZE]=pd.NA
   out[k]=x
  for c,v in texts[symbol].items():out[c]=pd.array(np.where(present,v,None),dtype='string')
  frames.append(pd.DataFrame(out))
 result=pd.concat(frames,ignore_index=True);result.to_parquet(p,index=False,compression='zstd')
 m=dict(id=j['id'],sha256=j['sha256'],source_rows=rows,anchor_symbol_rows=len(result),present=int(result.present.sum()),independent_checks=nchecks,chunks=chunks)
 mp.write_text(json.dumps(m,indent=2));print(json.dumps(m),flush=True);return p,m

def safe_asof(table,ns,timecol):
 if table.empty:
  raise ValueError('Empty reference table must be replaced with explicit unknown state')
 assert table[timecol].is_monotonic_increasing,'Reference table is not time sorted'
 idx=np.searchsorted(table[timecol].to_numpy(dtype='int64'),ns,side='right')-1
 valid=idx>=0;out=table.iloc[np.maximum(idx,0)].reset_index(drop=True).copy()
 if (~valid).any():
  for c in out:
   if c in ['definition_conflict']:out.loc[~valid,c]=True
   elif c in ['legs_one_to_one','status_nochange_carried']:out.loc[~valid,c]=False
   elif c in ['security_update_action']:out.loc[~valid,c]='D'
   elif c=='leg_mapping':out.loc[~valid,c]='{}'
   elif 'is_trading' in c or 'is_quoting' in c or c=='status_prior_explicit':out.loc[~valid,c]='~'
   elif pd.api.types.is_numeric_dtype(out[c]):out.loc[~valid,c]=-1
   else:out.loc[~valid,c]=''
 return out

def unknown_definition():
 return pd.DataFrame([dict(definition_ts_recv_ns=-1,definition_ts_event_ns=-1,
  expiration_ns=-1,activation_ns=-1,definition_conflict=True,
  security_update_action='D',legs_one_to_one=False,leg_mapping='{}',
  display_factor_nano=-1,min_price_increment_nano=-1)])

def unknown_status():
 return pd.DataFrame([dict(status_ts_recv_ns=-1,status_ts_event_ns=-1,
  status_is_trading='~',status_is_quoting='~',status_action=-1,
  status_reason=-1,status_trading_event=-1,status_is_trading_carry='~',
  status_nochange_carried=False,status_prior_explicit='~',status_prior_explicit_recv_ns=-1)])

def reference(jobs):
 audit=[]
 ref=W/'expanded_cme_reference';(ref/'definitions').mkdir(parents=True,exist_ok=True);(ref/'status').mkdir(exist_ok=True)
 for schema,folder,name in [('definition','definitions','contract_definitions.parquet'),('status','status','contract_status.parquet')]:
  fs=[pd.read_parquet(OLD/folder/name)]
  for j in jobs:
   if j['request']['schema']==schema and j['status']=='complete':
    assert sha(j['path'])==j['sha256'],('Reference file changed',j['id'])
    store=db.DBNStore.from_file(j['path'])
    df=store.to_df(price_type='fixed',pretty_ts=False,map_symbols=True).reset_index()
    physical=sum(1 for _ in store)
    assert len(df)==physical,('Reference decode count',j['id'],len(df),physical)
    exact=len(df)==j['expected_records']
    # Databento explicitly documents definition metadata counts as exact only
    # for discrete 24-hour ranges. ET-midnight requests are partial UTC days.
    assert len(df)<=j['expected_records'],('Unexpected excess reference count',j['id'],len(df),j['expected_records'])
    # A metadata mismatch is retained as a source-completeness caveat; it is
    # never silently changed into a verified expected count. Physical DBN
    # iteration and dataframe decode must still agree exactly. Missing status
    # or definitions fail closed in the independent as-of eligibility gates.
    assert not store.metadata.partial and not store.metadata.not_found
    audit.append(dict(job_id=j['id'],schema=schema,metadata_estimated_records=j['expected_records'],physical_records=physical,decoded_records=len(df),metadata_count_exact=exact,sha256_verified=True,metadata_partial_symbols=False,metadata_not_found_symbols=False))
    fs.append(df)
  merged=pd.concat(fs,ignore_index=True);merged.to_parquet(ref/folder/name,index=False)
 pd.DataFrame(audit).to_csv(W/'expanded_cme_reference_count_audit.csv',index=False)
 old.RAW=ref;old.ref_asof=safe_asof
 return old.reference_tables()

def build():
 manifest_paths=[RAW/'acquisition_manifest.json',BASE/'expanded_delta_20260917/acquisition_manifest.json',BASE/'expanded_delta2_20260917/acquisition_manifest.json']
 assert all(p.exists() for p in manifest_paths),'Expected base and source-date delta manifests'
 jobs=[j for p in manifest_paths for j in json.loads(p.read_text())['jobs']]
 assert len({j['id'] for j in jobs})==len(jobs),'Duplicate acquisition job IDs'
 assert all(j['status']=='complete' for j in jobs),'Incomplete acquisition'
 new=[];stats=[]
 for j in jobs:
  if j['request']['schema'] in ['mbp-1','mbp-10']:
   p,m=scan(j);new.append(p);stats.append(m)
 cached=sorted((W/'cme_minute_selection_cache').glob('*.parquet'))
 allobs=pd.concat([pd.read_parquet(p,columns=COLS) for p in cached+new],ignore_index=True)
 allobs=allobs[allobs.present].sort_values(['symbol','anchor_ns','ts_recv_ns','source_row'],kind='stable').drop_duplicates(['symbol','anchor_ns'],keep='last')
 assert (allobs.ts_recv_ns<=allobs.anchor_ns).all()
 defs,statuses=reference(jobs)
 choices=pd.read_csv(O/'cme_choice_calendar.csv');choices=choices[choices.exact_zero_other_meeting_loading & choices.span.gt(0) & ~choices.calendar_boundary_unverified]
 poly_days=W/'expanded_poly_fullminute_cme_candidate_days.csv'
 if not poly_days.exists():poly_days=W/'expanded_poly_cme_candidate_days_1min.csv'
 pp=pd.read_csv(poly_days);pp=pp[~pp.early_resolution_anomaly]
 kk=pd.read_csv(W/'expanded_kalshi_source_dates.csv')
 dates=pd.concat([pp[['meeting_date','date_et']],kk[['meeting_date','date_et']]],ignore_index=True).drop_duplicates()
 obsby={s:g for s,g in allobs.groupby('symbol',sort=False)};frames=[];coverage=[]
 for q in choices.itertuples():
  ds=dates.loc[dates.meeting_date.eq(q.meeting_date),'date_et'].sort_values()
  if ds.empty:continue
  aa=anchors(ds.min(),str(pd.Timestamp(q.meeting_date)+pd.Timedelta(days=1)))
  aa=aa[aa.tz_convert(ET).strftime('%Y-%m-%d').isin(ds)]
  aa=aa[aa<pd.Timestamp(q.meeting_date+' 14:00',tz=ET)]
  near=old.sym_month(q.near_month);far=old.sym_month(q.far_month);spread=near+'-'+far
  # A BACK leg that has expired can never be a new executable package.
  if q.instrument=='BACK':
   if pd.isna(q.near_expiration_utc):
    coverage.append(dict(meeting_date=q.meeting_date,instrument=q.instrument,anchors=len(aa),missing_reference=True,reason='BACK has no verified near expiry'));continue
   # Calendar eligibility prefilter; each role still checks its own PIT definition.
   # Never use the maximum expiry from future-received definition records here.
   aa=aa[aa<pd.Timestamp(q.near_expiration_utc)]
  if not len(aa):continue
  missing_roles=[s for s in [near,far,spread] if s not in defs or s not in statuses or defs[s].definition_ts_recv_ns.max()<0 or statuses[s].status_ts_recv_ns.max()<0]
  for s in [near,far,spread]:
   # Missing listed reference must not erase independently eligible synthetic legs.
   # Unknown reference fails closed for the affected role and every dependent route.
   if s not in defs:defs[s]=unknown_definition()
   if s not in statuses:statuses[s]=unknown_status()
  span=float(q.span);wide=pd.DataFrame(dict(meeting_date=q.meeting_date,instrument=q.instrument,anchor_ns=aa.asi8,anchor_utc=aa,anchor_ts=aa.asi8//10**9,date_et=aa.tz_convert(ET).strftime('%Y-%m-%d'),time_et=aa.tz_convert(ET).strftime('%H:%M:%S'),span_exact=span,digital_contracts_per_spread=1041.75*span,leg_near=q.near_month,leg_far=q.far_month,selected_in_repository=q.selected_in_repository))
  for symbol,role in [(near,'near'),(far,'far'),(spread,'listed')]:
   g=pd.DataFrame({'anchor_ns':aa.asi8}).merge(obsby.get(symbol,allobs.iloc[:0]),on='anchor_ns',how='left',validate='one_to_one')
   g['symbol']=symbol;g['present']=g.present.fillna(False).astype(bool)
   wide=old.attach_role(wide,g,symbol,role,aa,defs,statuses)
   # Undefined unsigned event timestamps can decode to signed -1; <=anchor
   # alone must not turn that unknown clock into a historically usable quote.
   wide[f'{role}_source_clocks_known']=wide[f'{role}_ts_recv_ns'].gt(0).fillna(False)&wide[f'{role}_ts_event_ns'].gt(0).fillna(False)
   wide[f'{role}_book_basic']&=wide[f'{role}_source_clocks_known']
   for age in [1,60,300]:
    recv_gate=wide[f'{role}_age_le_{age}s']
    event_age=wide[f'{role}_event_age_ns']
    wide[f'{role}_event_age_le_{age}s']=wide[f'{role}_present']&event_age.ge(0).fillna(False)&event_age.le(age*10**9).fillna(False)
    wide[f'{role}_both_clocks_age_le_{age}s']=recv_gate&wide[f'{role}_event_age_le_{age}s']
  wide['package_definition_admits']=wide.near_definition_admits&wide.far_definition_admits
  wide['listed_definition_direction_one_to_one']=wide.listed_legs_one_to_one&wide.listed_leg_mapping.eq(json.dumps({near:'B',far:'A'},sort_keys=True))
  wide['synthetic_book_basic']=wide.near_book_basic&wide.far_book_basic
  wide['synthetic_bid']=wide.near_bid-wide.far_ask;wide['synthetic_ask']=wide.near_ask-wide.far_bid
  wide['synthetic_mid']=(wide.synthetic_bid+wide.synthetic_ask)/2
  wide['synthetic_bid_size']=np.minimum(wide.near_bid_sz_00,wide.far_ask_sz_00);wide['synthetic_ask_size']=np.minimum(wide.near_ask_sz_00,wide.far_bid_sz_00)
  wide['listed_bid_size']=wide.listed_bid_sz_00;wide['listed_ask_size']=wide.listed_ask_sz_00
  units=wide.near_display_factor_nano.eq(10000000)&wide.far_display_factor_nano.eq(10000000)&wide.listed_display_factor_nano.eq(1000000000)
  wide['unit_definition_verified']=units
  for route in ['synthetic','listed']:
   base=wide[route+'_book_basic']&wide.package_definition_admits
   if route=='listed':base&=wide.listed_definition_admits&wide.listed_definition_direction_one_to_one&units
   else:base&=wide.near_display_factor_nano.eq(10000000)&wide.far_display_factor_nano.eq(10000000)
   for side in ['bid','ask','mid']:wide[f'{route}_{side}_D_bp']=wide[f'{route}_{side}']*(1 if route=='listed' else 100)/span
   for state in ['strict','carry']:
    status=wide['listed_status_'+state+'_open'] if route=='listed' else wide['near_status_'+state+'_open']&wide['far_status_'+state+'_open']
    wide[f'{route}_usable_{state}']=base&status
    for side in ['bid','ask']:wide[f'{route}_{side}_one_spread_{state}']=base&status&wide[f'{route}_{side}_size'].ge(1).fillna(False)
   for age in [1,60,300]:
    wide[f'{route}_age_le_{age}s']=wide[f'listed_age_le_{age}s'] if route=='listed' else wide[f'near_age_le_{age}s']&wide[f'far_age_le_{age}s']
    wide[f'{route}_both_clocks_age_le_{age}s']=wide[f'listed_both_clocks_age_le_{age}s'] if route=='listed' else wide[f'near_both_clocks_age_le_{age}s']&wide[f'far_both_clocks_age_le_{age}s']
  for role in ['near','far','listed']:
   assert ((wide[role+'_ts_recv_ns']<=wide.anchor_ns)|~wide[role+'_present']).all()
   for typ in ['definition','status']:
    assert (wide[f'{role}_{typ}_ts_recv_ns']<=wide.anchor_ns).all()
    assert (wide[f'{role}_{typ}_ts_event_ns']<=wide.anchor_ns).all()
  valid=(wide.synthetic_bid_one_spread_carry&wide.synthetic_age_le_60s)|(wide.listed_bid_one_spread_carry&wide.listed_age_le_60s)
  coverage.append(dict(meeting_date=q.meeting_date,instrument=q.instrument,anchors=len(wide),missing_reference=bool(missing_roles),missing_reference_symbols=','.join(missing_roles),valid_sell_minutes=int(valid.sum()),first_date=wide.date_et.min(),last_date=wide.date_et.max()))
  frames.append(wide);print(json.dumps(coverage[-1]),flush=True)
 panel=pd.concat(frames,ignore_index=True);assert not panel.duplicated(['meeting_date','instrument','anchor_ts']).any()
 assert panel.columns.is_unique,'Duplicate output column names'
 panel.to_parquet(W/'expanded_cme_minute_panel.parquet',index=False,compression='zstd')
 pd.DataFrame(coverage).to_csv(O/'expanded_cme_coverage.csv',index=False)
 (W/'expanded_cme_qa.json').write_text(json.dumps(dict(rows=len(panel),meetings=int(panel.meeting_date.nunique()),pair_count=len(coverage),new_scans=stats,source_observations_selected=len(allobs),old_cache_files=len(cached),key_duplicates=0,future_quote_or_reference_rows=0,quote_age_gate_seconds=60,quote_age_clock_policy='Primary: ts_recv age and ts_event<=anchor. Sensitivity: both_clocks_age additionally bounds ts_event age.',unknown_reference_policy='Unknown per role; affected route fails closed; independent route retained.',poly_candidate_days_source=str(poly_days),acquisition_manifest_hashes={str(p):sha(p) for p in manifest_paths},book_levels_used=1),indent=2))

if __name__=='__main__':build()
