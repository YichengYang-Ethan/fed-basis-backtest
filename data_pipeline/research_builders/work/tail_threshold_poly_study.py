"""User-requested1/3/5% omitted-quote sensitivity; no new strategy fit."""
from pathlib import Path
import json,numpy as np,pandas as pd
from analyze_fed_edges import best_cme
from time_edge_study import select
W=Path(__file__).resolve().parent;T=W/'tail_thresholds';O=W.parent/'outputs';KEY=['meeting_date','event_id','anchor_ts'];CK=['meeting_date','anchor_ts']
def main():
 p=pd.read_parquet(T/'poly_principals.parquet');live=pd.read_parquet(T/'poly_liveness.parquet');lk=['threshold','mode']+KEY
 assert not p.duplicated(lk).any();assert not live.duplicated(lk).any()
 lcols=[f'{a}_{b}_liveness_pass' for a in ['lower','upper'] for b in ['yes','no']]
 p=p.merge(live[lk+lcols],on=lk,validate='one_to_one')
 ccols=CK+['instrument','span_exact','digital_contracts_per_spread','leg_near','leg_far']+[f'{r}_{x}' for r in ['synthetic','listed'] for x in ['bid_D_bp','ask_D_bp','bid_one_spread_carry','ask_one_spread_carry','age_le_60s']]
 c=pd.read_parquet(W/'expanded_cme_minute_panel.parquet',columns=ccols);bid,ask,br,ar=best_cme(c)
 c['cme_bid_D']=bid;c['cme_ask_D']=ask;c['cme_bid_route']=br;c['cme_ask_route']=ar
 keep=CK+['instrument','span_exact','digital_contracts_per_spread','leg_near','leg_far','cme_bid_D','cme_ask_D','cme_bid_route','cme_ask_route']
 c=c[keep];term=pd.read_parquet(W/'expanded_terminal_cash_reference.parquet')
 c=c.merge(term[['meeting_date','instrument','terminal_implied_change_bp','terminal_months_complete']],on=['meeting_date','instrument'],validate='many_to_one')
 if 'raw_weighted_move_bp_60s' not in p:
  ev=pd.read_parquet(W/'expanded_poly_fullminute_event_panel.parquet',columns=KEY+['raw_weighted_move_bp_60s','sum_mid_60s'])
  p=p.merge(ev,on=KEY,validate='many_to_one')
 records=[];summaries=[];bymeeting=[];entry_labels=[];validkeysets={}
 for (tau,mode),pp in p.groupby(['threshold','mode'],sort=True):
  assert pp.gap_bp.eq(25).all() and pp.main_states_exact_on_25bp_grid.all() and pp.both_principal_contracts_unambiguous.all()
  z=c.merge(pp,on=CK,validate='many_to_one').reset_index(drop=True)
  frames=[];n=z.digital_contracts_per_spread.to_numpy(float);q=np.floor(n+.5).astype(int);u=q/n;d0=z.d0_bp.to_numpy(float)
  for implementation,label,longside in [('YES_upper','upper','yes'),('NO_lower','lower','no')]:
   for sign in [1,-1]:
    side=longside if sign==1 else ('no' if longside=='yes' else 'yes');px=z[f'{label}_{side}_mid_60s'].to_numpy(float);win=z[f'{label}_{side}_winner_if_known'].to_numpy(float)
    valid=np.isfinite(px)&(px>=0)&(px<=1)&z[f'{label}_{side}_age_sec'].between(0,60).to_numpy()&z[f'{label}_{side}_observed_ts'].le(z.anchor_ts).to_numpy()
    liveok=(z.lower_yes_liveness_pass&z.upper_yes_liveness_pass&z[f'{label}_{side}_liveness_pass']).fillna(False).to_numpy(bool)
    cme=z.cme_bid_D.to_numpy(float) if sign==1 else z.cme_ask_D.to_numpy(float)
    cash=d0/25 if sign==1 else -d0/25-u
    funding=100*q*px/n*.05*(z.days_to_meeting.to_numpy(float)+1)/365
    predcost=u+5*u*px*(1-px)+funding
    edge=sign*4*cme-100*(cash+u*px)-500/n-predcost
    corr=100*(cash+u*win)-sign*4*z.terminal_implied_change_bp.to_numpy(float)
    cols=CK+['event_id','date_et','instrument','leg_near','leg_far','days_to_meeting','lower_token_id','upper_token_id','lower_no_token_id','upper_no_token_id','lower_label','upper_label','d0_bp','d1_bp','p_lower','p_upper','omitted_total','omitted_max','main_pair_mid_sum','in_next_meeting_window','previous_fomc_14et_ts','meeting_14et_ts','terminal_months_complete','raw_weighted_move_bp_60s','sum_mid_60s']
    o=z[cols].copy();o['threshold']=tau;o['mode']=mode;o['construction']=implementation;o['cme_direction_sign']=sign;o['actual_buy_side']=side.upper();o['actual_outcome']=z[label+'_label'];o['actual_token_id']=z[label+('_token_id' if side=='yes' else '_no_token_id')];o['cme_route']=z.cme_bid_route if sign==1 else z.cme_ask_route
    o['N']=n;o['quantity']=q;o['quote_mid_actual']=px;o['cme_entry_D_bp']=cme;o['edge_cents']=np.where(valid&np.isfinite(cme),edge,np.nan);o['liveness_pass']=liveok;o['model_payoff_correction_cents']=corr;o['actual_token_winner_ex_post']=win
    knownmain=z.lower_yes_winner_if_known.notna()&z.upper_yes_winner_if_known.notna()
    o['realized_inside_main_pair']=pd.array((z.lower_yes_winner_if_known+z.upper_yes_winner_if_known).eq(1),dtype='boolean');o.loc[~knownmain,'realized_inside_main_pair']=pd.NA
    o['cash_perN']=cash;o['prediction_cost_cents']=predcost;o['funding_cents']=funding
    o['binary_mid_equivalent_D_bp']=sign*25*(cash+u*px)
    o['representative_grid_pricing_shift_cents']=sign*4*(o.raw_weighted_move_bp_60s-o.binary_mid_equivalent_D_bp)
    o['proxy_grid_basis_minus_same_costs_cents']=o.edge_cents-o.representative_grid_pricing_shift_cents
    states=np.arange(-100,101,25,dtype=float);y=states[None,:]==z[label.replace('lower','d0').replace('upper','d1')+'_bp'].to_numpy(float)[:,None]
    if side=='no':y=~y
    stresscorr=100*(cash[:,None]+u[:,None]*y)-sign*4*states
    o['pm100_grid_worst_net_cents']=edge+stresscorr.min(axis=1);o['pm100_grid_worst_net_usd']=o.pm100_grid_worst_net_cents*n/100
    o['persistence_key']=o.instrument+'|'+o.leg_near+'|'+o.leg_far+'|'+o.lower_token_id+'|'+o.upper_token_id+'|'+implementation+'|'+str(sign)+'|'+o.actual_token_id
    o=o[o.edge_cents.notna()].copy();frames.append(o)
  b=pd.concat(frames,ignore_index=True);b['row_id']=np.arange(len(b));b['model_pnl_cents']=b.edge_cents+b.model_payoff_correction_cents;b['model_pnl_usd']=b.model_pnl_cents*b.N/100;b['stress_same_entry_pnl_cents']=b.model_pnl_cents-b.quantity/b.N;b['stress_same_entry_pnl_usd']=b.stress_same_entry_pnl_cents*b.N/100
  b.to_parquet(T/f'poly_binary_tau{int(tau*100)}_{mode}.parquet',index=False)
  for scope in ['full_lifetime','next_meeting_only']:
   s=b[b.liveness_pass & (b.in_next_meeting_window if scope=='next_meeting_only' else True)].copy()
   features=s[['meeting_date','date_et','anchor_ts','persistence_key','edge_cents','row_id']].copy()
   chosen=select(features,2,False);x=s.set_index('row_id').loc[chosen.row_id].reset_index();x['scope']=scope;x['entry_et']=pd.to_datetime(x.anchor_ts,unit='s',utc=True).dt.tz_convert('America/New_York').astype(str)
   records.append(x);validkeysets[(tau,mode,scope)]=set(zip(s.meeting_date,s.anchor_ts))
   def nmin(g,mask=None):
    if mask is not None:g=g[mask]
    return len(g[['meeting_date','anchor_ts']].drop_duplicates())
   summaries.append(dict(venue='Polymarket',threshold=tau,mode=mode,scope=scope,valid_minutes=nmin(s),meetings_with_valid_minutes=int(s.meeting_date.nunique()),over2_minutes=nmin(s,s.edge_cents.ge(2)),candidate_meetings=len(x),known_terminal_candidates=int(x.model_pnl_cents.notna().sum()),positive_model_candidates=int(x.model_pnl_cents.gt(0).sum()),negative_model_candidates=int(x.model_pnl_cents.lt(0).sum()),outside_main_known_candidates=int(x.realized_inside_main_pair.eq(False).sum()),mean_entry_omitted_total=x.omitted_total.mean(),max_entry_omitted_total=x.omitted_total.max(),max_entry_omitted_single=x.omitted_max.max(),mean_pnl_per_trade_cents=x.model_pnl_cents.mean(),sum_model_usd=x.model_pnl_usd.sum(min_count=1),worst_model_usd=x.model_pnl_usd.min(),positive_same_entry_stress=int(x.stress_same_entry_pnl_cents.gt(0).sum()),proxy_grid_basis_nonpositive_at_entry=int(x.proxy_grid_basis_minus_same_costs_cents.le(0).sum())))
   for md,g in s.groupby('meeting_date'):
    bymeeting.append(dict(threshold=tau,mode=mode,scope=scope,meeting_date=md,valid_minutes=nmin(g),over2_minutes=nmin(g,g.edge_cents.ge(2)),max_omitted_total=g.omitted_total.max()))
 cand=pd.concat(records,ignore_index=True);summ=pd.DataFrame(summaries);diffs=[]
 for mode in p['mode'].unique():
  for scope in ['full_lifetime','next_meeting_only']:
   base=cand[cand.threshold.eq(.01)&cand['mode'].eq(mode)&cand.scope.eq(scope)].set_index('meeting_date');basemin=validkeysets[(.01,mode,scope)]
   for tau in [.01,.03,.05]:
    here=cand[cand.threshold.eq(tau)&cand['mode'].eq(mode)&cand.scope.eq(scope)].set_index('meeting_date');common=base.index.intersection(here.index);same=0;known_delta=[]
    for md in common:
     a,v=base.loc[md],here.loc[md];eq=a.anchor_ts==v.anchor_ts and a.persistence_key==v.persistence_key;same+=int(eq)
     delta=v.model_pnl_usd-a.model_pnl_usd
     if pd.notna(delta):known_delta.append(delta)
     diffs.append(dict(threshold=tau,mode=mode,scope=scope,meeting_date=md,status='same_entry' if eq else 'changed_entry',baseline_entry_et=a.entry_et,entry_et=v.entry_et,baseline_model_usd=a.model_pnl_usd,model_usd=v.model_pnl_usd,delta_model_usd=delta))
    for md in here.index.difference(base.index):
     v=here.loc[md];diffs.append(dict(threshold=tau,mode=mode,scope=scope,meeting_date=md,status='new_meeting',entry_et=v.entry_et,model_usd=v.model_pnl_usd))
    for md in base.index.difference(here.index):diffs.append(dict(threshold=tau,mode=mode,scope=scope,meeting_date=md,status='lost_meeting'))
    mask=summ.threshold.eq(tau)&summ['mode'].eq(mode)&summ.scope.eq(scope)
    vals=dict(new_valid_minutes=len(validkeysets[(tau,mode,scope)]-basemin),lost_valid_minutes=len(basemin-validkeysets[(tau,mode,scope)]),new_candidate_meetings=len(here.index.difference(base.index)),lost_candidate_meetings=len(base.index.difference(here.index)),same_entry_meetings=same,changed_entry_meetings=len(common)-same,common_meetings_pnl_delta_usd=sum(known_delta))
    for k,v in vals.items():summ.loc[mask,k]=v
    if mode=='omit_only_nested':assert not basemin-validkeysets[(tau,mode,scope)]
 cand.to_csv(O/'tail_threshold_poly_candidates.csv',index=False);summ.to_csv(O/'tail_threshold_poly_summary.csv',index=False);pd.DataFrame(bymeeting).to_csv(O/'tail_threshold_poly_by_meeting.csv',index=False);pd.DataFrame(diffs).to_csv(O/'tail_threshold_poly_candidate_changes.csv',index=False)
 # Independent actual purchase + signed CME cashflow, without fixed-cash algebra.
 checks=[]
 for r in cand.itertuples():
  ter=term[term.meeting_date.eq(r.meeting_date)&term.instrument.eq(r.instrument)].iloc[0].terminal_implied_change_bp
  cost=r.quantity*.01+.05*r.quantity*r.quote_mid_actual*(1-r.quote_mid_actual)+r.quantity*r.quote_mid_actual*.05*(r.days_to_meeting+1)/365+5
  cash=r.cme_direction_sign*(r.cme_entry_D_bp-ter)*r.N*.04+r.quantity*(r.actual_token_winner_ex_post-r.quote_mid_actual)-cost
  if pd.notna(cash):assert abs(cash-r.model_pnl_usd)<1e-9;checks.append(abs(cash-r.model_pnl_usd))
 old=pd.read_csv(O/'expanded_poly_first_candidates.csv');old=old[old.cost_scenario.eq('future_fee05_plus1c_funding05')&old.window.eq('inplay_liveness_20obs_2prices')&old.state_gate.eq('complete_gate')].sort_values('meeting_date')
 new=cand[cand.threshold.eq(.01)&cand['mode'].eq('omit_only_nested')&cand.scope.eq('full_lifetime')].sort_values('meeting_date')
 assert list(old.meeting_date)==list(new.meeting_date) and list(old.anchor_ts)==list(new.anchor_ts)
 assert np.allclose(old.model_pnl_usd,new.model_pnl_usd,equal_nan=True,atol=1e-8)
 qa=dict(one_percent_baseline_candidates=len(new),one_percent_entry_and_cash_baseline_reproduced=True,independent_cash_checks=len(checks),max_cash_error_usd=max(checks) if checks else None,primary_minute_sets_nested=True,outcomes_used_for_entry=False,main_cost='fee.05+1c+$5+funding.05',same_entry_extra_1c_stress=True,all_new_data_spending=0,omitted_price_weights_not_true_probabilities=True)
 (T/'poly_study_qa.json').write_text(json.dumps(qa,indent=2));print(json.dumps(qa,indent=2));print(summ.to_string(index=False))
if __name__=='__main__':main()
