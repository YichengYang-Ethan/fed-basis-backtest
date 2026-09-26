"""Full local lifetime, 1-minute predecision panel; backward same-ET-day only."""
import os
from pathlib import Path
from pathlib import Path
import json,sys
import numpy as np,pandas as pd,duckdb
sys.path.insert(0,str(Path(__file__).resolve().parent))
from build_poly_analysis_panel import rolling_history,event_rules,json_clean
W=Path(__file__).resolve().parent;D=Path(os.environ['FOMC_DATA_ROOT']) / 'raw/poly';ET='America/New_York';AGES=(60,300)
m=pd.read_parquet(W/'expanded_poly_market_metadata.parquet')
m=m[m.predecision_rows.fillna(0)>0].sort_values(['meeting_date','event_id','representative_bp','bin_label']).copy()
m['early_resolution_anomaly']=(m.decision_utc-m.lifetime_end)>pd.Timedelta(days=1)
m['canonical_event_id']=m.event_id
m.loc[m.event_id.eq('fed-decision-in-september-568'),'canonical_event_id']='fed-decision-in-september-762'
m['historical_research_eligible']=~m.early_resolution_anomaly
c=duckdb.connect();events=[];legs=[];coverage=[];rule_rows=[]
for (meeting,eid),g in m.groupby(['meeting_date','event_id'],sort=True):
 start=g.lifetime_start.min();end=g.lifetime_end.max();days=pd.date_range(start.tz_convert(ET).date(),end.tz_convert(ET).date(),freq='B')
 anchors=pd.DatetimeIndex(np.concatenate([pd.date_range(str(d.date())+' 10:00',str(d.date())+' 15:00',freq='min',tz=ET).tz_convert('UTC').values for d in days])).tz_localize('UTC')
 anchors=anchors[(anchors>=start)&(anchors<end)]
 grid=pd.DataFrame({'anchor_utc':anchors});grid['anchor_ts']=grid.anchor_utc.astype('int64')//10**9;grid['date_et']=grid.anchor_utc.dt.tz_convert(ET).dt.strftime('%Y-%m-%d');grid['meeting_date']=meeting;grid['event_id']=eid
 grid['canonical_event_id']=g.canonical_event_id.iloc[0];grid['early_resolution_anomaly']=g.early_resolution_anomaly.any();grid['historical_research_eligible']=not g.early_resolution_anomaly.any()
 rules=event_rules(g);rule_rows.append(dict(meeting_date=meeting,event_id=eid,**rules))
 for k,v in rules.items():grid[k]=v
 grid['days_to_meeting']=(pd.Timestamp(meeting)-pd.to_datetime(grid.date_et)).dt.days
 grid['price_kind']='historical_midpoint_proxy';grid['executable_edge_supported']=False;grid['anchor_interval_sec']=60
 raw=c.execute(f"select token_id,ts,p,fidelity from read_parquet(['{D}/prices.parquet','{W}/expanded_poly_fullminute_parts/*.parquet'], union_by_name=true) where event_id=? and ts>=? and ts<? order by token_id,ts,fidelity,p",[eid,int(start.timestamp())-3600,int(end.timestamp())]).fetchdf()
 raw=raw.drop_duplicates(['token_id','ts'],keep='first');raw['observed_utc']=pd.to_datetime(raw.ts,unit='s',utc=True)
 local=raw.observed_utc.dt.tz_convert(ET);raw['date_et']=local.dt.strftime('%Y-%m-%d');raw['local_minute']=local.dt.hour*60+local.dt.minute
 raw=raw[raw.local_minute.between(540,900)];raw['valid_probability']=raw.p.between(0,1)&raw.p.notna();by={t:x for t,x in raw.groupby('token_id',sort=False)}
 this=[]
 for r in g.itertuples():
  l=grid[['anchor_utc','anchor_ts','date_et','meeting_date','event_id','canonical_event_id','early_resolution_anomaly','historical_research_eligible']].copy()
  for key,value in [('market_id',r.market_id),('token_id',r.yes_token_id),('yes_token_id',r.yes_token_id),('no_token_id',r.no_token_id),('bin_label',r.bin_label),('bin_kind',r.bin_kind),('representative_bp',r.representative_bp),('tail_direction',r.tail_direction),('rule_lower_bp',r.rule_lower_bp),('rule_upper_bp',r.rule_upper_bp),('lower_inclusive',r.lower_inclusive),('upper_inclusive',r.upper_inclusive),('lifetime_start',r.lifetime_start),('lifetime_end',r.lifetime_end),('lifetime_start_metadata_known',r.lifetime_start_metadata_known)]:l[key]=value
  obs=by.get(r.yes_token_id,raw.iloc[:0]).copy();obs=obs[(obs.observed_utc>=r.lifetime_start)&(obs.observed_utc<r.lifetime_end)]
  diag=[]
  for _,d in obs.groupby('date_et',sort=True):
   d=d.sort_values('ts').copy();d[['trail60_n_obs','trail60_unique_p','trail60_n_changes','trail60_n_half']]=rolling_history(d.ts.to_numpy(),d.p.to_numpy());diag.append(d)
  cols=['date_et','ts','p','fidelity','valid_probability','trail60_n_obs','trail60_unique_p','trail60_n_changes','trail60_n_half']
  if diag:l=pd.merge_asof(l.sort_values('anchor_ts'),pd.concat(diag,ignore_index=True)[cols].sort_values('ts'),left_on='anchor_ts',right_on='ts',by='date_et',direction='backward')
  else:
   for key in cols[1:]:l[key]=np.nan
  l=l.rename(columns={'ts':'observed_ts','p':'mid_raw'});l['age_sec']=l.anchor_ts-l.observed_ts;l['in_lifetime']=(l.anchor_utc>=r.lifetime_start)&(l.anchor_utc<r.lifetime_end);l['same_et_day']=l.observed_ts.notna()
  l['is_half']=l.mid_raw.eq(.5);l['inplay_now']=l.mid_raw.between(.02,.98);l['trail60_constant_inplay']=l.inplay_now&l.trail60_n_obs.ge(20)&l.trail60_unique_p.eq(1)
  l['source_resolution']=np.where(l.fidelity.eq(1),'minute',np.where(l.fidelity.eq(60),'hourly','none'))
  no=by.get(r.no_token_id,raw.iloc[:0]).copy();no=no[(no.observed_utc>=r.lifetime_start)&(no.observed_utc<r.lifetime_end)]
  nc=['date_et','ts','p','fidelity','valid_probability']
  no=no[nc].rename(columns={k:'no_'+k for k in nc if k!='date_et'})
  l=pd.merge_asof(l.sort_values('anchor_ts'),no.sort_values('no_ts'),left_on='anchor_ts',right_on='no_ts',by='date_et',direction='backward') if len(no) else l.assign(no_ts=np.nan,no_p=np.nan,no_fidelity=np.nan,no_valid_probability=False)
  l=l.rename(columns={'no_ts':'no_observed_ts','no_p':'no_mid_raw'});l['no_age_sec']=l.anchor_ts-l.no_observed_ts
  for age in AGES:
   ok=l.observed_ts.notna()&l.age_sec.between(0,age)&l.in_lifetime&l.valid_probability.fillna(False).astype(bool)
   l[f'usable_{age}s']=ok;l[f'mid_{age}s']=l.mid_raw.where(ok)
   l[f'drop_reason_{age}s']=np.select([~l.in_lifetime,l.observed_ts.isna(),~l.valid_probability.fillna(False).astype(bool),l.age_sec.gt(age)],['outside_lifetime','no_previous_same_day','invalid_probability','stale'],default='usable')
   nok=l.no_observed_ts.notna()&l.no_age_sec.between(0,age)&l.in_lifetime&l.no_valid_probability.fillna(False).astype(bool)
   l[f'no_usable_{age}s']=nok;l[f'no_mid_{age}s']=l.no_mid_raw.where(nok);l[f'no_complement_proxy_{age}s']=1-l[f'mid_{age}s']
  l['contract_rule_unambiguous']=(not r.rules_core_ambiguous) and (not r.rules_wording_inconsistency) and r.bin_kind not in ('unmapped','unverified_numeric_label');l['contract_point_exact_on_25bp_grid']=(r.tail_direction==0) and r.bin_kind in ('exact','rounded_interval');l['main_exact_25bp_contract']=l.contract_point_exact_on_25bp_grid & l.representative_bp.isin([-25.,0.,25.]) & l.contract_rule_unambiguous;l['main_exact_25bp_usable_60s']=l.main_exact_25bp_contract & l.usable_60s;l['event_exhaustive_on_25bp_grid']=rules['exhaustive_on_25bp_grid'];l['tail_probability_unknown']=not rules['exhaustive_on_25bp_grid'];l['single_contract_retained']=True
  this.append(l);legs.append(l)
 wide=pd.concat(this,ignore_index=True);n=len(g);nknown=int(g.representative_bp.notna().sum())
 for age in AGES:
  s=f'{age}s';piv=wide.pivot(index='anchor_ts',columns='token_id',values=f'mid_{s}').reindex(grid.anchor_ts)
  reps=g.set_index('yes_token_id').representative_bp.reindex(piv.columns);tails=g.set_index('yes_token_id').tail_direction.reindex(piv.columns)
  good=piv.notna().sum(axis=1).eq(n);ngood=piv.loc[:,reps.notna()].notna().sum(axis=1).eq(nknown)
  numeric=piv.mul(reps,axis=1).sum(axis=1,min_count=nknown).where(ngood);summ=piv.sum(axis=1,min_count=n).where(good);weighted=numeric.where(good&rules['all_known_labels'])
  vals={'n_valid_legs':piv.notna().sum(axis=1),'all_legs_fresh':good,'sum_mid_observed':piv.sum(axis=1,min_count=1),'sum_mid':summ,'known_labels_weighted_move_bp':numeric,'raw_weighted_move_bp':weighted,'normalized_move_bp':weighted/summ.where(summ.ne(0)),'tail_probability_signed':piv.mul(tails,axis=1).sum(axis=1,min_count=n).where(good),'cut_tail_probability':piv.loc[:,tails.lt(0)].sum(axis=1).where(good),'hike_tail_probability':piv.loc[:,tails.gt(0)].sum(axis=1).where(good),'usable_label_convention':good&rules['all_known_labels']&(not rules['rules_core_ambiguous']),'usable_strict_ladder':good&rules['all_known_labels']&rules['exhaustive_on_25bp_grid']&(not rules['rules_ambiguous'])}
  for key,value in vals.items():grid[f'{key}_{s}']=value.to_numpy()
  fresh=wide[wide[f'usable_{s}']];agg=fresh.groupby('anchor_ts').agg(max_age_sec=('age_sec','max'),n_half=('is_half','sum'),n_hourly=('fidelity',lambda x:int(x.eq(60).sum())),n_constant_inplay=('trail60_constant_inplay','sum'))
  for key in agg.columns:grid[f'{key}_{s}']=grid.anchor_ts.map(agg[key]).fillna(0 if key!='max_age_sec' else np.nan)
  grid[f'all_legs_minute_{s}']=grid[f'all_legs_fresh_{s}']&grid[f'n_hourly_{s}'].eq(0)
  grid[f'n_legs_gt1pct_{s}']=piv.gt(.01).sum(axis=1).to_numpy();grid[f'two_principal_strict_{s}']=grid[f'usable_strict_ladder_{s}']&grid[f'n_legs_gt1pct_{s}'].eq(2)&grid.historical_research_eligible
  grid[f'tail_probability_known_{s}']=good.to_numpy()&rules['exhaustive_on_25bp_grid']&(not rules['rules_ambiguous'])
  grid[f'partial_ladder_usable_{s}']=grid[f'n_valid_legs_{s}'].gt(0)&(~grid[f'usable_strict_ladder_{s}'])
  holds=wide[wide.bin_label.isin(['HOLD','HOLD_0BP'])].set_index('anchor_ts');grid[f'hold_mid_{s}']=grid.anchor_ts.map(holds[f'mid_{s}'])
 holds=wide[wide.bin_label.isin(['HOLD','HOLD_0BP'])].set_index('anchor_ts')
 for src,dst in [('age_sec','hold_age_sec'),('observed_ts','hold_observed_ts'),('token_id','hold_token_id'),('fidelity','hold_source_fidelity'),('trail60_unique_p','hold_trail60_unique_p'),('trail60_constant_inplay','hold_trail60_constant_inplay')]:grid[dst]=grid.anchor_ts.map(holds[src])
 grid['tail_probability_unknown']=~grid.tail_probability_known_60s
 events.append(grid);coverage.append(dict(meeting_date=meeting,event_id=eid,**rules,anchors=len(grid),any_fresh60=int(grid.n_valid_legs_60s.gt(0).sum()),all_fresh60=int(grid.all_legs_fresh_60s.sum()),strict60=int(grid.usable_strict_ladder_60s.sum()),two_principal_strict60=int(grid.two_principal_strict_60s.sum()),early_resolution_anomaly=bool(grid.early_resolution_anomaly.any()),first_anchor_utc=str(grid.anchor_utc.min()),last_anchor_utc=str(grid.anchor_utc.max())))
 print(json.dumps(coverage[-1]),flush=True)
e=pd.concat(events,ignore_index=True);l=pd.concat(legs,ignore_index=True)
l['event_nonexhaustive_on_25bp_grid']=~l.event_exhaustive_on_25bp_grid
l=l.drop(columns='tail_probability_unknown').merge(e[['event_id','anchor_ts','tail_probability_unknown']],on=['event_id','anchor_ts'],how='left',validate='many_to_one')
assert not e.duplicated(['meeting_date','event_id','anchor_ts']).any();assert not l.duplicated(['meeting_date','event_id','token_id','anchor_ts']).any()
for side in ['', 'no_']:
 ix=l[f'{side}observed_ts'].notna();assert (l.loc[ix,f'{side}observed_ts']<=l.loc[ix,'anchor_ts']).all()
 assert pd.to_datetime(l.loc[ix,f'{side}observed_ts'],unit='s',utc=True).dt.tz_convert(ET).dt.strftime('%Y-%m-%d').eq(l.loc[ix,'date_et']).all()
 for age in AGES:assert l.loc[l[f'{side}usable_{age}s'],f'{side}age_sec'].between(0,age).all()
assert (e.anchor_utc<pd.to_datetime(e.meeting_date+' 14:00').dt.tz_localize(ET).dt.tz_convert('UTC')).all()
e.to_parquet(W/'expanded_poly_fullminute_event_panel.parquet',index=False);l.to_parquet(W/'expanded_poly_fullminute_leg_panel.parquet',index=False);pd.DataFrame(coverage).to_csv(W/'expanded_poly_fullminute_event_coverage.csv',index=False)
mapcols=['meeting_date','event_id','canonical_event_id','market_id','yes_token_id','no_token_id','question','description','bin_label','bin_kind','representative_bp','tail_direction','rule_lower_bp','rule_upper_bp','lower_inclusive','upper_inclusive','rounded_to_25bp','rules_core_ambiguous','rules_wording_inconsistency','rule_notes','resolution_evidence','no_statement_resolution','lifetime_start','lifetime_end','lifetime_start_source','lifetime_end_source','lifetime_start_metadata_known','fetched_at','final_outcome_prices','uma_resolution_status','closed_time','early_resolution_anomaly','historical_research_eligible']
mapping=m[mapcols].to_dict('records')
for item in mapping:
 item['rule_notes']=list(item['rule_notes'])
 item['lower_unbounded']=item['rule_lower_bp']==-np.inf;item['upper_unbounded']=item['rule_upper_bp']==np.inf
meta=dict(scope='All local direct historical FOMC decision events with predecision prices, full metadata lifetime, current-date cap 2026-09-17.',grid='1 minute Monday-Friday ET 10:00-15:00; decision day included strictly before 14:00 ET; no holiday assumption.',timestamp_policy='Backward same ET day; ts<=anchor and explicit age<=60s baseline /300s sensitivity. Quotes represent API observation timestamps, not receive times.',lifetime_policy='gamma_start_date, start_date, then first observed conservative existence; end earlier of actual closed_time and 14ET decision. Scheduled gamma_end_date midnight ignored as not actual close.',midpoint_policy='Historical midpoint proxy, no executable bid/ask/depth. Actual NO token history retained separately; no_complement_proxy is explicitly derived 1-YES, not actual NO.',nonexhaustive_policy='Single YES/NO contracts retained; missing ladder outcomes remain unknown and must not be labeled <1%. Strict two-principal gate requires complete parsed 25bp ladder, all YES fresh, no rule ambiguity and exactly two observed YES midpoints >1%.',anomaly_policy='September568 resolved in May before September meeting; retained as early_resolution_anomaly, historical_research_eligible=false, maps canonical Sept762 but never merged as same history.',early_history='2022 metadata exists but no local predecision history. Earlier available lifetime was backfilled with free CLOB fidelity1 calls over identified historical markets, appended only before the locally stored first minute timestamp. Provenance: expanded_poly_fullminute_acquisition.json.',market_mapping=mapping,event_rule_flags=rule_rows,event_summaries=coverage,counts=dict(meetings=e.meeting_date.nunique(),events=e.event_id.nunique(),event_anchor_rows=len(e),leg_anchor_rows=len(l),any_fresh60_event_anchors=int(e.n_valid_legs_60s.gt(0).sum()),strict60_event_anchors=int(e.usable_strict_ladder_60s.sum()),two_principal_strict60_event_anchors=int(e.two_principal_strict_60s.sum())),validation=dict(future_joins=0,cross_et_day_joins=0,duplicate_event_anchors=0,decision_at_or_after1400ET=0),event_columns={k:str(v) for k,v in e.dtypes.items()},leg_columns={k:str(v) for k,v in l.dtypes.items()})
(W/'expanded_poly_fullminute_analysis_metadata.json').write_text(json.dumps(json_clean(meta),indent=2,allow_nan=False));print(json.dumps(meta['counts']),flush=True)
