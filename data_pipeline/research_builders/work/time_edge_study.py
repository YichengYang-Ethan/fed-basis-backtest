"""Finite, recorded time/edge grid; entry selector sees no outcome columns."""
from pathlib import Path
import json,hashlib,itertools,datetime
import numpy as np
import pandas as pd
W=Path(__file__).resolve().parent;O=W.parent/'outputs';ET='America/New_York'
P=json.loads((W/'time_edge_protocol.json').read_text());KEY=['meeting_date','anchor_ts']
FEATURES=['meeting_date','anchor_ts','date_et','instrument','leg_near','leg_far','days_to_meeting','N','quantity','actual_outcome','actual_buy_side','actual_token_id','persistence_key','cme_route','cme_entry_D_bp','cme_direction_sign','quote_mid_actual','quote_age_seconds','p_lower','p_upper','d0_bp','d1_bp','observed_nonprincipal_weight','edge_before_prediction_cost_cents','current_fee05_cents_perN','digital_funding_usd','complete_gate','direct_future_type','inplay_liveness_pass']
def select(features,threshold,global_best_first=False):
 x=features.copy()
 if global_best_first:
  x=x.sort_values(KEY+['edge_cents','persistence_key'],ascending=[True,True,False,True]).drop_duplicates(KEY)
 else:x=x.sort_values(['meeting_date','persistence_key','anchor_ts'])
 valid=x.edge_cents.ge(threshold)
 prev=x.groupby(['meeting_date','persistence_key'],sort=False).shift()
 contiguous=x.anchor_ts.sub(prev.anchor_ts).eq(60)&x.date_et.eq(prev.date_et)&valid&prev.edge_cents.ge(threshold)
 if global_best_first:
  contiguous=x.anchor_ts.diff().eq(60)&x.meeting_date.eq(x.meeting_date.shift())&x.persistence_key.eq(x.persistence_key.shift())&x.date_et.eq(x.date_et.shift())&valid&valid.shift(fill_value=False)
 x['run_id']=(~contiguous).cumsum();x['run_length']=x.groupby('run_id').cumcount()+1
 q=x[valid&x.run_length.ge(3)].sort_values(KEY+['edge_cents','persistence_key'],ascending=[True,True,False,True])
 return q.drop_duplicates('meeting_date').copy()

def attach(entries,outcomes):
 x=entries.merge(outcomes,on='row_id',validate='one_to_one')
 x['model_pnl_cents']=x.edge_cents+x.model_payoff_correction_cents
 x['model_pnl_usd']=x.model_pnl_cents*x.N/100
 x['stress_same_entry_pnl_cents']=x.model_pnl_cents-x.quantity/x.N
 x['stress_same_entry_pnl_usd']=x.stress_same_entry_pnl_cents*x.N/100
 x['entry_et']=pd.to_datetime(x.anchor_ts,unit='s',utc=True).dt.tz_convert(ET).astype(str)
 return x

def main():
 for name,h in P['input_sha256'].items():assert hashlib.sha256((W/name).read_bytes()).hexdigest()==h
 b=pd.read_parquet(W/'expanded_poly_binary_base.parquet');b['row_id']=np.arange(len(b))
 outcomes=b[['row_id','model_payoff_correction_cents','terminal_months_complete','realized_change_bp','actual_outcome_in_main_pair']].copy()
 f=b[['row_id']+FEATURES].copy();f['funding_cents']=100*f.digital_funding_usd/f.N*.05*(f.days_to_meeting+1)/365
 f['edge_cents']=f.edge_before_prediction_cost_cents-f.quantity/f.N-f.current_fee05_cents_perN-f.funding_cents
 f=f[f.complete_gate&f.inplay_liveness_pass&f.direct_future_type&f.edge_cents.notna()].copy()
 clock=pd.to_datetime(f.anchor_ts,unit='s',utc=True).dt.tz_convert(ET)
 f['minute_et']=clock.dt.hour*60+clock.dt.minute
 f=f[f.days_to_meeting.between(1,60)&f.minute_et.ge(600)&f.minute_et.lt(900)].copy()
 assert not f.duplicated(['meeting_date','persistence_key','anchor_ts']).any()
 assert all(x not in f.columns for x in ['model_pnl_cents','model_payoff_correction_cents','realized_change_bp','actual_outcome_in_main_pair'])
 meetings=sorted(f.meeting_date.unique());f.to_parquet(W/'time_edge_entry_features.parquet',index=False)
 # Toy regression: A remains continuously profitable although B temporarily wins.
 toy=pd.DataFrame([dict(meeting_date='m',date_et='d',anchor_ts=t,persistence_key=k,edge_cents=v,row_id=i) for i,(t,k,v) in enumerate([(0,'A',3),(0,'B',2.5),(60,'A',3),(60,'B',3.5),(120,'A',3),(120,'B',2.5)])])
 assert len(select(toy,2,True))==0 and len(select(toy,2,False))==1
 base=[]
 for mode,old in [('old_best_then_persistence',True),('independent_persistence_then_best',False)]:
  q=attach(select(f,2,old),outcomes);q['method']=mode;base.append(q)
 pd.concat(base).to_csv(O/'time_edge_selection_order_comparison.csv',index=False)
 entries=[];rules=[];coverage=[]
 for li,lead in enumerate(P['lead_day_windows']):
  for hi,hours in enumerate(P['intraday_ET_windows']):
   loh=sum(int(v)*m for v,m in zip(hours[0].split(':'),[60,1]));hih=sum(int(v)*m for v,m in zip(hours[1].split(':'),[60,1]))
   pool=f[f.days_to_meeting.between(*lead)&f.minute_et.ge(loh)&f.minute_et.lt(hih)]
   for th in P['net_edge_cents_thresholds']:
    rid=f'L{li+1}_H{hi+1}_E{th}';r=dict(rule_id=rid,lead_min=lead[0],lead_max=lead[1],hour_start=hours[0],hour_end=hours[1],threshold_cents=th)
    rules.append(r);q=attach(select(pool,th),outcomes)
    for k,v in r.items():q[k]=v
    entries.append(q)
    for md in meetings:
     g=pool[pool.meeting_date.eq(md)];coverage.append(dict(rule_id=rid,meeting_date=md,valid_observation_minutes=int(g.anchor_ts.nunique()),has_observation=not g.empty))
 e=pd.concat(entries,ignore_index=True);rule=pd.DataFrame(rules);cov=pd.DataFrame(coverage)
 e.to_csv(O/'time_edge_all_rule_entries.csv',index=False);cov.to_csv(O/'time_edge_rule_coverage.csv',index=False)
 # Outcome maturity is set for the common eligible universe, not for winners.
 univ=[]
 for md,g in f.groupby('meeting_date'):
  far=max(g.leg_far);avail=(pd.Period(far).end_time.normalize()+pd.Timedelta(days=7)).tz_localize(ET)
  allknown=bool(outcomes.loc[outcomes.row_id.isin(g.row_id),'model_payoff_correction_cents'].notna().all())
  univ.append(dict(meeting_date=md,latest_far_month=far,label_available_proxy_utc=avail.tz_convert('UTC').isoformat(),terminal_labels_known=allknown,n_instruments=int(g.instrument.nunique()),valid_minutes=int(g.anchor_ts.nunique())))
 u=pd.DataFrame(univ).sort_values('meeting_date');u.to_csv(O/'time_edge_meeting_universe.csv',index=False)
 known=list(u.loc[u.terminal_labels_known,'meeting_date']);pnl=pd.DataFrame(0.,index=meetings,columns=rule.rule_id);stress=pnl.copy();traded=pnl.astype(bool)
 for r in e.itertuples():pnl.loc[r.meeting_date,r.rule_id]=r.model_pnl_cents;stress.loc[r.meeting_date,r.rule_id]=r.stress_same_entry_pnl_cents;traded.loc[r.meeting_date,r.rule_id]=True
 # Do not make the pending meeting's trade outcome zero.
 for md in set(meetings)-set(known):pnl.loc[md,traded.loc[md]]=np.nan;stress.loc[md,traded.loc[md]]=np.nan
 summaries=[]
 for r in rule.itertuples():
  y=pnl.loc[known,r.rule_id];s=stress.loc[known,r.rule_id];ntrade=int(traded.loc[known,r.rule_id].sum());v=e[e.rule_id.eq(r.rule_id)]
  summaries.append(dict(**r._asdict(),known_common_meetings=len(known),meetings_with_window_observation=int(cov[cov.rule_id.eq(r.rule_id)].has_observation.sum()),candidate_meetings=len(v),known_trades=ntrade,positive_known_trades=int(v.model_pnl_cents.gt(0).sum()),mean_policy_cents=y.mean(),mean_per_trade_cents=v.model_pnl_cents.mean(),median_trade_cents=v.model_pnl_cents.median(),sum_model_usd=v.model_pnl_usd.sum(min_count=1),stress_same_entry_mean_policy_cents=s.mean(),stress_positive_known_trades=int(v.stress_same_entry_pnl_cents.gt(0).sum()),worst_trade_cents=v.model_pnl_cents.min(),unknown_trade_pnl=int(v.model_pnl_cents.isna().sum())))
 summary=pd.DataFrame(summaries).drop(columns='Index');summary.to_csv(O/'time_edge_grid_summary.csv',index=False)
 wf=[];scoretrace=[];testentries=[]
 for minimum in ['diagnostic_minimum','evidence_minimum']:
  for md in meetings:
   cut=(pd.Timestamp(md)-pd.Timedelta(days=60)+pd.Timedelta(hours=10)).tz_localize(ET)
   train=u[u.meeting_date.lt(md)&u.terminal_labels_known&pd.to_datetime(u.label_available_proxy_utc).lt(cut)]
   trainmd=list(train.meeting_date);nt=len(trainmd);qualified=[]
   for r in rule.itertuples():
    y=pnl.loc[trainmd,r.rule_id].to_numpy(float);ntrade=int(traded.loc[trainmd,r.rule_id].sum());mean=float(y.mean()) if nt else np.nan;se=float(y.std(ddof=1)/np.sqrt(nt)) if nt>1 else np.nan;score=mean-se;loomin=float(((y.sum()-y)/(nt-1)).min()) if nt>1 else np.nan
    ok=nt>=P[minimum]['mature_common_meetings'] and ntrade>=P[minimum]['training_trades'] and score>0 and loomin>0
    s=dict(layer=minimum,test_meeting=md,test_cutoff_utc=cut.tz_convert('UTC').isoformat(),rule_id=r.rule_id,training_meetings=nt,training_trades=ntrade,train_mean_cents=mean,train_one_se_cents=se,train_score=score,minimum_leave_one_out_mean_cents=loomin,qualified=bool(ok),training_meeting_list='|'.join(trainmd));scoretrace.append(s)
    if ok:qualified.append(s)
   selected=sorted(qualified,key=lambda x:(-x['train_score'],x['rule_id']))[0] if qualified else None
   row=dict(layer=minimum,test_meeting=md,test_cutoff_utc=cut.tz_convert('UTC').isoformat(),training_meetings=nt,qualified_rules=len(qualified),selected_rule=selected['rule_id'] if selected else '',decision='TRADE_RULE' if selected else 'ABSTAIN_INSUFFICIENT_TRAINING_OR_SCORE',test_has_entry=False,test_model_pnl_cents=0.,test_stress_pnl_cents=0.,test_model_pnl_usd=0.)
   if selected:
    rr=selected['rule_id'];v=e[e.meeting_date.eq(md)&e.rule_id.eq(rr)]
    row.update(selected_training_trades=selected['training_trades'],selected_train_mean=selected['train_mean_cents'],selected_train_score=selected['train_score'])
    if len(v):
     assert len(v)==1;q=v.iloc[0];assert pd.Timestamp(q.entry_et)>=cut
     row.update(test_has_entry=True,test_model_pnl_cents=q.model_pnl_cents,test_stress_pnl_cents=q.stress_same_entry_pnl_cents,test_model_pnl_usd=q.model_pnl_usd,test_entry_et=q.entry_et)
     qe=q.to_dict();qe.update(layer=minimum,test_cutoff_utc=cut.tz_convert('UTC').isoformat());testentries.append(qe)
    else:row['decision']='SELECTED_RULE_NO_OBSERVED_ENTRY'
   wf.append(row)
 pd.DataFrame(wf).to_csv(O/'time_edge_walkforward.csv',index=False);pd.DataFrame(scoretrace).to_csv(O/'time_edge_training_score_audit.csv',index=False);pd.DataFrame(testentries).to_csv(O/'time_edge_walkforward_entries.csv',index=False)
 # Common-meeting signs applied jointly to all grid cells. Symmetry is a
 # restrictive diagnostic assumption, especially for digital tail payoffs.
 y=pnl.loc[known].to_numpy(float);n=len(y);std=y.std(axis=0,ddof=1);obs=np.divide(y.mean(axis=0)*np.sqrt(n),std,out=np.zeros(len(std)),where=std>0)
 signs=np.array(list(itertools.product([-1.,1.],repeat=n))) if n<=15 else np.random.default_rng(9172026).choice([-1.,1.],size=(10000,n))
 means=signs@y/n;sumsq=(y*y).sum(axis=0);sds=np.sqrt(np.maximum((sumsq[None,:]-n*means**2)/(n-1),0));ts=np.divide(means*np.sqrt(n),sds,out=np.zeros_like(means),where=sds>1e-12)
 maxnull=ts.max(axis=1);p_adjusted=float((maxnull>=obs.max()-1e-12).mean());best_t=rule.iloc[int(obs.argmax())].rule_id
 bestmean=rule.iloc[int(y.mean(axis=0).argmax())].rule_id
 stability=[]
 for i,md in enumerate(known):
  z=np.delete(y,i,axis=0);winner=rule.iloc[int(z.mean(axis=0).argmax())].rule_id;stability.append(dict(omitted_meeting=md,winning_rule=winner,mean_policy_cents=float(z.mean(axis=0).max())))
 pd.DataFrame(stability).to_csv(O/'time_edge_leave_one_meeting_out.csv',index=False)
 result=dict(protocol_sha256=hashlib.sha256((W/'time_edge_protocol.json').read_bytes()).hexdigest(),rules=len(rule),common_meetings=len(meetings),known_common_meetings=len(known),main_instrument_choices_per_meeting=u.n_instruments.tolist(),candidate_rows=len(e),known_candidate_rows=int(e.model_pnl_cents.notna().sum()),hindsight_max_mean_rule=bestmean,max_t_rule=best_t,max_t=float(obs.max()),common_meeting_sign_flip_maxT_adjusted_p=p_adjusted,sign_patterns=len(signs),maxT_assumption='Conditional sign symmetry of whole meeting return vectors; exploratory stress only, not evidence of deployable alpha.',original_selection_order_candidates=len(base[0]),independent_path_selection_candidates=len(base[1]),label_availability_proxy_not_vintage_proof=True,genuine_untouched_out_of_sample=False,selection_outcome_columns_excluded=True,no_new_data_spending=True)
 (W/'time_edge_study_summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False,indent=2));print(pd.DataFrame(wf).to_string(index=False))
if __name__=='__main__':main()
