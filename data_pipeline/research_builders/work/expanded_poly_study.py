"""Expanded observed-state research with actual YES and NO midpoint histories.

One CME package; contemporary selection only. Digital history is not a filled
book. The complete ladder gate and incomplete-state sensitivity stay separate.
"""
from pathlib import Path
import json
import numpy as np
import pandas as pd
from analyze_fed_edges import best_cme,clean_json
from edge_stats import persistent_candidates,correlation_diagnostics
W=Path(__file__).resolve().parent;O=W.parent/'outputs';KEY=['meeting_date','anchor_ts']

def summarize(x,labels):
 # Max current edge chooses the route; never choose a future winning meeting.
 x=x[x.edge_cents.notna()].sort_values(KEY+['edge_cents','persistence_key'],ascending=[True,True,False,True]).drop_duplicates(KEY)
 first,ep=persistent_candidates(x,direction='persistence_key')
 first=first.copy();first['entry_time_et']=pd.to_datetime(first.anchor_ts,unit='s',utc=True).dt.tz_convert('America/New_York').dt.strftime('%Y-%m-%d %H:%M:%S %Z')
 for k,v in labels.items():first[k]=v
 r=dict(**labels,valid_minutes=len(x),meetings_with_valid_minutes=int(x.meeting_date.nunique()),positive_minutes=int(x.edge_cents.gt(0).sum()),over2_minutes=int(x.edge_cents.ge(2).sum()),median_edge_cents=x.edge_cents.median(),p95_edge_cents=x.edge_cents.quantile(.95),persistent_episodes=len(ep),candidate_meetings=len(first),candidate_model_pnl_known=int(first.model_pnl_cents.notna().sum()),candidate_positive_model_pnl=int(first.model_pnl_cents.gt(0).sum()),candidate_model_pnl_usd=first.model_pnl_usd.sum(min_count=1),candidate_pre21days=int(first.days_to_meeting.gt(21).sum()),candidate_meeting_day=int(first.days_to_meeting.eq(0).sum()))
 return r,first,ep,x

def main(cme_file=None):
 c=pd.read_parquet(cme_file or W/'expanded_cme_minute_panel.parquet')
 keep=KEY+['instrument','span_exact','digital_contracts_per_spread','selected_in_repository','leg_near','leg_far']+[x for x in c if x.startswith(('synthetic_','listed_')) and (x.endswith('_D_bp') or '_one_spread_' in x or '_age_le_' in x)]
 c=c[keep].copy();bid,ask,br,ar=best_cme(c)
 c['cme_bid_D']=bid;c['cme_ask_D']=ask;c['cme_bid_route']=br;c['cme_ask_route']=ar
 term=pd.read_parquet(W/'expanded_terminal_cash_reference.parquet');c=c.merge(term[['meeting_date','instrument','terminal_implied_change_bp','realized_change_bp','terminal_months_complete','regime']],on=['meeting_date','instrument'],validate='many_to_one')
 p=pd.read_parquet(W/'expanded_poly_fullminute_principal_panel.parquet')
 live=pd.read_parquet(W/'expanded_poly_fullminute_liveness.parquet')
 livecols=['lower_yes_liveness_pass','upper_yes_liveness_pass','lower_no_liveness_pass','upper_no_liveness_pass']
 p=p.merge(live[KEY+['event_id']+livecols],on=KEY+['event_id'],validate='one_to_one')
 assert not p.duplicated(KEY).any();assert not c.duplicated(KEY+['instrument']).any()
 z=c.merge(p,on=KEY,validate='many_to_one')
 z=z[z.gap_bp.eq(25)&z.main_states_exact_on_25bp_grid&z.both_principal_contracts_unambiguous].copy()
 # A complete screen knows every listed state. Incomplete ladders never prove
 # the unlisted tail has <1% weight, even when observed states have two >1%.
 z['complete_gate']=z.strict_complete_two_principal_gate
 z['observed_only_gate']=z.all_legs_fresh_60s&z.both_principal_are_HOLD_CUT25_HIKE25
 z['direct_future_type']=z.both_principal_are_HOLD_CUT25_HIKE25
 if z.empty:raise RuntimeError('No admissible historical principal pairs')
 n=z.digital_contracts_per_spread.to_numpy(float);q=np.floor(n+.5).astype(int);u=q/n
 d0=z.d0_bp.to_numpy(float);terminal=z.terminal_implied_change_bp.to_numpy(float)
 base=[]
 for implementation,label,longside in [('YES_upper','upper','yes'),('NO_lower','lower','no')]:
  for sign in [1,-1]:
   buy_side=longside if sign==1 else ('no' if longside=='yes' else 'yes')
   buy_px=z[f'{label}_{buy_side}_mid_60s'].to_numpy(float)
   winner=z[f'{label}_{buy_side}_winner_if_known'].to_numpy(float)
   observed=z[f'{label}_{buy_side}_observed_ts'].to_numpy(float);age=z[f'{label}_{buy_side}_age_sec'].to_numpy(float)
   valid=np.isfinite(buy_px)&(buy_px>=0)&(buy_px<=1)&(observed<=z.anchor_ts)&(age>=0)&(age<=60)
   # Cash + bought digital reproduces +/- base(d0 + 25 * indicator).
   cash=d0/25 if sign==1 else -d0/25-u
   entry_value=cash+u*buy_px;payout_value=cash+u*winner
   cme=z.cme_bid_D.to_numpy(float) if sign==1 else z.cme_ask_D.to_numpy(float)
   before=sign*4*cme-100*entry_value-500/n
   correction=100*payout_value-sign*4*terminal
   out=z[KEY+['instrument','date_et','days_to_meeting','regime','selected_in_repository','leg_near','leg_far','complete_gate','observed_only_gate','direct_future_type','lower_label','upper_label','lower_token_id','upper_token_id','lower_no_token_id','upper_no_token_id','d0_bp','d1_bp','p_lower','p_upper','observed_nonprincipal_weight','all_legs_minute_60s','n_constant_inplay_60s','n_half_60s','terminal_months_complete','realized_change_bp']].copy()
   out['construction']=implementation;out['direction']=np.where(sign==1,'short_CME_long_binary','long_CME_short_binary')
   out['actual_buy_side']=buy_side.upper();out['actual_outcome']=z[label+'_label'];out['actual_token_id']=z[label+('_token_id' if buy_side=='yes' else '_no_token_id')]
   out['cme_route']=z.cme_bid_route if sign==1 else z.cme_ask_route
   out['cme_entry_D_bp']=cme;out['cme_direction_sign']=sign
   out['cme_quote_inside_principal_interval']=(cme>=d0)&(cme<=z.d1_bp.to_numpy(float))
   out['inplay_liveness_pass']=(z.lower_yes_liveness_pass&z.upper_yes_liveness_pass&z[f'{label}_{buy_side}_liveness_pass']).fillna(False).to_numpy(bool)
   out['N']=n;out['quantity']=q;out['quote_mid_actual']=buy_px;out['quote_age_seconds']=age
   out['constant_cash_perN']=cash;out['cash_plus_digital_entry_perN']=entry_value
   out['edge_before_prediction_cost_cents']=np.where(valid,before,np.nan)
   out['model_payoff_correction_cents']=correction;out['digital_funding_usd']=q*buy_px
   out['current_fee05_cents_perN']=5*u*buy_px*(1-buy_px)
   out['persistence_key']=out.instrument+'|'+out.direction+'|'+implementation+'|'+out.lower_token_id+'|'+out.upper_token_id+'|'+out.actual_token_id
   out['actual_outcome_in_main_pair']=pd.array(np.isclose(z.realized_change_bp,d0)|np.isclose(z.realized_change_bp,z.d1_bp),dtype='boolean')
   out.loc[z.realized_change_bp.isna().to_numpy(),'actual_outcome_in_main_pair']=pd.NA
   # Conditional two-state matching with integer quantities must be bounded.
   out['principal_rounding_error_bound_cents']=50/n
   rep=np.array([-100.,100.]);which_d=z.d1_bp.to_numpy() if label=='upper' else d0
   hit=rep[None,:]==which_d[:,None]
   if buy_side=='no':hit=~hit
   residual=100*(cash[:,None]+u[:,None]*hit)-sign*4*rep
   out['pm100bp_stress_worst_residual_cents']=residual.min(axis=1)
   base.append(out)
 b=pd.concat(base,ignore_index=True)
 b.to_parquet(W/'expanded_poly_binary_base.parquet',index=False)
 summaries=[];candidates=[];bymeeting=[];episodes=[]
 scenarios=[('historical_1c_scenario',1.,0.,0.),('future_fee05_plus1c',1.,.05,0.),('future_fee05_plus2c',2.,.05,0.),('future_fee05_plus1c_funding05',1.,.05,.05)]
 for name,cost,feerate,funding_rate in scenarios:
  x=b.copy();x['edge_cents']=x.edge_before_prediction_cost_cents-cost*x.quantity/x.N-(x.current_fee05_cents_perN if feerate else 0)
  x['assumed_digital_funding_cents']=100*x.digital_funding_usd/x.N*funding_rate*(x.days_to_meeting+1)/365
  x['edge_cents']-=x.assumed_digital_funding_cents
  x['model_pnl_cents']=x.edge_cents+x.model_payoff_correction_cents;x['model_pnl_usd']=x.model_pnl_cents*x.N/100
  for gate in ['complete_gate','observed_only_gate']:
   for window in ['full_lifetime','last21days_predecision']:
    s=x[x[gate]].copy()
    if window=='last21days_predecision':s=s[s.days_to_meeting.between(1,21)]
    label=dict(cost_scenario=name,state_gate=gate,window=window)
    r,first,ep,best=summarize(s,label);summaries.append(r);candidates.append(first)
    for k,v in label.items():ep[k]=v
    episodes.append(ep)
    if name=='historical_1c_scenario' and window=='full_lifetime':
     for md,g in best.groupby('meeting_date'):
      rr,_,_,_=summarize(g,dict(**label,meeting_date=md));bymeeting.append(rr)
     best.to_parquet(W/f'expanded_poly_best_{gate}.parquet',index=False)
  # Secondary controls preserve fail minutes as missing: same exact run rule.
  for control,ok in [('future_contract_types',x.direct_future_type),('exclude_flat_inplay',x.n_constant_inplay_60s.eq(0)&x.n_half_60s.eq(0)),('all_minute_sources',x.all_legs_minute_60s),('cme_inside_principal_interval',x.cme_quote_inside_principal_interval),('inplay_liveness_20obs_2prices',x.inplay_liveness_pass)]:
   s=x[x.complete_gate].copy();s.loc[~ok.reindex(s.index),'edge_cents']=np.nan
   label=dict(cost_scenario=name,state_gate='complete_gate',window=control)
   r,first,ep,_=summarize(s,label);summaries.append(r);candidates.append(first)
 pd.DataFrame(summaries).to_csv(O/'expanded_poly_scenarios.csv',index=False)
 cand=pd.concat(candidates,ignore_index=True);cand.to_csv(O/'expanded_poly_first_candidates.csv',index=False)
 pd.DataFrame(bymeeting).to_csv(O/'expanded_poly_by_meeting.csv',index=False)
 pd.concat(episodes,ignore_index=True).to_csv(O/'expanded_poly_episodes.csv',index=False)
 # Nested bridge isolates longer coverage from changing the NO-price convention
 # or adding alternative CME pairs. The old study had 26 pre-September meetings.
 old_meetings=set(pd.read_parquet(W/'terminal_cash_reference.parquet').meeting_date)
 oldmask=b.meeting_date.isin(old_meetings)&b.selected_in_repository&b.days_to_meeting.between(1,21)&b.complete_gate
 bridge=[];bridge_candidates=[]
 for stage,mask,proxy in [('A_old26_21days_complement_NO',oldmask,True),('B_old26_21days_actual_NO',oldmask,False),('C_all27_full_lifetime_actual_NO',b.complete_gate,False)]:
  x=b[mask].copy()
  if proxy:
   principal=np.where(x.construction.eq('YES_upper'),x.p_upper,x.p_lower)
   px=np.where(x.actual_buy_side.eq('YES'),principal,1-principal)
   x['edge_cents']=x.cme_direction_sign*4*x.cme_entry_D_bp-100*(x.constant_cash_perN+x.quantity/x.N*px)-500/x.N-x.quantity/x.N
  else:x['edge_cents']=x.edge_before_prediction_cost_cents-x.quantity/x.N
  x['model_pnl_cents']=x.edge_cents+x.model_payoff_correction_cents;x['model_pnl_usd']=x.model_pnl_cents*x.N/100
  r,first,_,_=summarize(x,dict(stage=stage));bridge.append(r);bridge_candidates.append(first)
 pd.DataFrame(bridge).to_csv(O/'expanded_poly_comparison_bridge.csv',index=False)
 pd.concat(bridge_candidates,ignore_index=True).to_csv(O/'expanded_poly_bridge_candidates.csv',index=False)
 # Compare all available strict expected moves; do not treat serial minutes as
 # independent events. Correlations include within-meeting and changes.
 events=pd.read_parquet(W/'expanded_poly_fullminute_event_panel.parquet');events=events[events.historical_research_eligible]
 corr=c[c.selected_in_repository].merge(events[KEY+['date_et','usable_strict_ladder_60s','raw_weighted_move_bp_60s']],on=KEY,validate='one_to_one')
 corr['CME']=(corr.cme_bid_D+corr.cme_ask_D)/2;corr['Poly']=corr.raw_weighted_move_bp_60s.where(corr.usable_strict_ladder_60s)
 stats,local=correlation_diagnostics(corr,'CME','Poly','Expanded CME–Poly strict ladder')
 pd.DataFrame(stats).to_csv(O/'expanded_poly_correlation_summary.csv',index=False);local.to_csv(O/'expanded_poly_correlations_by_meeting.csv',index=False)
 out=dict(source_meetings=int(p.meeting_date.nunique()),cme_meetings=int(c.meeting_date.nunique()),principal_pair_rows_after_cme_join=len(z),complete_gate_rows=int(z.complete_gate.sum()),observed_only_gate_rows=int(z.observed_only_gate.sum()),scenarios=summaries,checks=dict(principal_pair_key_unique=True,cme_pair_key_unique=True,backward_actual_side_price_only=True,synthetic_NO_used=False,executable_profit_claim=False,meeting_outcomes_used_for_entry=False),notes=['Poly prices-history does not contain historical executable spread/depth. 1c is an assumed total historical digital cost; future_fee05 adds current economic taker rate plus 1c or 2c execution allowance.','Funding05 sensitivity charges 5% annual simple carry on purchased digital cash to decision date plus one day; it is an explicit scenario, not a measured financing rate, and omits CME margin cash flows and settlement delays.','Complete observed ladder does not make a two-state digital hedge exact outside its two principal states. Conditional tails remain.','Observed-only screen needs all listed outcomes fresh and unambiguous principal HOLD/CUT25/HIKE25, but incomplete/unlisted state risk remains unknown.','All scenarios are exploratory after reviewing the sample; no new holdout is claimed.','Model terminal uses current-FRED mean EFFR and independent monthly settlement rounding; September 2026 terminal incomplete, PnL unknown.'])
 (W/'expanded_poly_study_summary.json').write_text(json.dumps(clean_json(out),indent=2,ensure_ascii=False,allow_nan=False))
 print(pd.DataFrame(summaries).to_string(index=False))

if __name__=='__main__':main()
