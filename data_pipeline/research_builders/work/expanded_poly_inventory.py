import os
from pathlib import Path
from pathlib import Path
import pandas as pd, numpy as np, duckdb,json,sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from build_poly_analysis_panel import parse_bin,event_rules,stamp,json_clean
W=Path(__file__).resolve().parent
D=Path(os.environ['FOMC_DATA_ROOT']) / 'raw/poly'
A=Path(os.environ['FOMC_PROJECT_ROOT']) / 'data'
ET='America/New_York'
cal=pd.read_csv(A/'fomc_instruments.csv');cal=cal[(cal.meeting_date>='2022-01-01')&(cal.meeting_date<'2026-09-17')]
m=pd.read_parquet(D/'markets.parquet');m=m[(m.fomc_date>='2022-01-01')&(m.fomc_date<'2026-09-17')].copy()
direct=m.outcome_detail.str.match(r'^(CUT|HIKE)\d+P?$|^HOLD(?:_0BP)?$|^THRESHOLD_ABOVE_')
ids=m.loc[direct,'event_id'].unique();m=m[m.event_id.isin(ids)].rename(columns={'fomc_date':'meeting_date'})
m['decision_utc']=pd.to_datetime(m.meeting_date+' 14:00').dt.tz_localize(ET).dt.tz_convert('UTC');m['decision_ts']=m.decision_utc.astype('int64')//10**9
c=duckdb.connect();c.register('markets',m)
b=c.execute(f'''select m.market_id,p.token_id,min(p.ts) raw_first_ts,max(p.ts) raw_last_ts,count(*) raw_rows,
 min(p.ts) filter(where p.ts<m.decision_ts) predecision_first_ts,max(p.ts) filter(where p.ts<m.decision_ts) predecision_last_ts,
 count(*) filter(where p.ts<m.decision_ts) predecision_rows,
 count(*) filter(where p.ts<m.decision_ts and p.fidelity=1) predecision_minute_rows
 from markets m join read_parquet('{D}/prices.parquet') p on m.market_id=p.market_id and m.event_id=p.event_id and m.yes_token_id=p.token_id
 group by m.market_id,p.token_id''').fetchdf();b=b.drop(columns='token_id');m=m.merge(b,on='market_id',how='left')
pd.DataFrame(b).to_csv(W/'expanded_poly_token_bounds.csv',index=False)
starts=[];ends=[];sources=[];known=[]
for r in m.itertuples():
 s=stamp(r.gamma_start_date);src='gamma_start_date'
 if pd.isna(s):s=stamp(r.start_date);src='start_date_fallback'
 ok=pd.notna(s)
 if pd.isna(s) and pd.notna(r.raw_first_ts):s=pd.to_datetime(r.raw_first_ts,unit='s',utc=True);src='first_observed_conservative_existence'
 e=r.decision_utc
 ce=stamp(r.closed_time)
 if pd.notna(ce):e=min(e,ce)
 starts.append(s);ends.append(e);sources.append(src);known.append(ok)
m['lifetime_start']=starts;m['lifetime_end']=ends;m['lifetime_start_source']=sources;m['lifetime_start_metadata_known']=known
m['lifetime_end_source']='earlier_actual_closed_time_or_decision_1400ET_exclusive'
parsed=pd.DataFrame([parse_bin(r) for r in m.itertuples()],index=m.index);m=pd.concat([m,parsed],axis=1)
rows=[]
for (meeting,eid),g in m.groupby(['meeting_date','event_id']):
 rules=event_rules(g);win=[]
 for r in g.itertuples():
  try:
   prices=[float(x) for x in json.loads(r.final_outcome_prices)]
   if prices[0]==1:win.append(r.bin_label)
  except Exception:pass
 start=g.lifetime_start.min();pstart=pd.to_datetime(g.predecision_first_ts.min(),unit='s',utc=True) if g.predecision_first_ts.notna().any() else pd.NaT
 pend=pd.to_datetime(g.predecision_last_ts.max(),unit='s',utc=True) if g.predecision_last_ts.notna().any() else pd.NaT
 valid_start=max(start,pstart) if pd.notna(start) and pd.notna(pstart) else pd.NaT
 end=g.lifetime_end.max()
 rows.append(dict(meeting_date=meeting,event_id=eid,canonical_event_id=eid,event_variant='multiple_events_same_meeting' if m[m.meeting_date==meeting].event_id.nunique()>1 else 'sole_direct_event',market_count=len(g),
 metadata_lifetime_start=start,metadata_common_ladder_start=g.lifetime_start.max(),interval_start=valid_start,interval_end_exclusive=end,
 raw_first_utc=pd.to_datetime(g.raw_first_ts.min(),unit='s',utc=True) if g.raw_first_ts.notna().any() else pd.NaT,raw_last_utc=pd.to_datetime(g.raw_last_ts.max(),unit='s',utc=True) if g.raw_last_ts.notna().any() else pd.NaT,
 available_predecision_first_utc=pstart,available_predecision_last_utc=pend,predecision_rows=int(g.predecision_rows.fillna(0).sum()),predecision_minute_rows=int(g.predecision_minute_rows.fillna(0).sum()),
 has_predecision_history=bool(g.predecision_rows.fillna(0).sum()),known_winner_labels='|'.join(win),winner_source='stored_final_outcome_prices_metadata',**rules))
i=pd.DataFrame(rows)
missing=cal[~cal.meeting_date.isin(i.meeting_date)]
for r in missing.itertuples():i=pd.concat([i,pd.DataFrame([dict(meeting_date=r.meeting_date,has_predecision_history=False,market_count=0,predecision_rows=0,predecision_minute_rows=0,event_variant='no_local_direct_metadata')])],ignore_index=True)
i=i.merge(cal,on='meeting_date',how='left').sort_values(['meeting_date','event_id'])
i['early_resolution_anomaly']=pd.to_datetime(i.interval_end_exclusive,utc=True)<pd.to_datetime(i.meeting_date,utc=True)-pd.Timedelta(days=1)
i['historical_research_eligible']=i.has_predecision_history & ~i.early_resolution_anomaly
i['reported_metadata_resolution_labels']=i.known_winner_labels
i.loc[i.early_resolution_anomaly,'known_winner_labels']=''
i.loc[i.early_resolution_anomaly,'winner_source']='excluded_early_resolution_anomaly'
i.loc[i.event_id.eq('fed-decision-in-september-568'),'canonical_event_id']='fed-decision-in-september-762'
# Feb 2023 uses near-terminal AMM values, so exact 1.0 alone does not identify winner.
for ix,row in i.iterrows():
 if row.has_predecision_history and not row.early_resolution_anomaly and not row.known_winner_labels and pd.notna(row.realized_change_bp):
  g=m[m.event_id.eq(row.event_id)]
  matched=[]
  for r in g.itertuples():
   if pd.notna(r.rule_lower_bp) and pd.notna(r.rule_upper_bp):
    a=row.realized_change_bp
    hit=(a>=r.rule_lower_bp if r.lower_inclusive else a>r.rule_lower_bp) and (a<=r.rule_upper_bp if r.upper_inclusive else a<r.rule_upper_bp)
    if hit:matched.append(r.bin_label)
  if len(matched)==1:i.at[ix,'known_winner_labels']=matched[0];i.at[ix,'winner_source']='calendar_realized_change_and_stored_rule_membership'
i.to_csv(W/'expanded_poly_meeting_inventory.csv',index=False)
m.to_parquet(W/'expanded_poly_market_metadata.parquet',index=False)
print(i[['meeting_date','event_id','interval_start','interval_end_exclusive','predecision_rows','known_winner_labels','instrument']].to_string(index=False))
print(json.dumps(dict(calendar_meetings=len(cal),metadata_meetings=m.meeting_date.nunique(),predecision_meetings=i[i.has_predecision_history].meeting_date.nunique(),predecision_events=int(i.has_predecision_history.sum())),indent=2))
