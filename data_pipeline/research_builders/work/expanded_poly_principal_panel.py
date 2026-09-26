"""Prepare observed principal pairs without suppressing partial-ladder risk.
Does not compare futures, normalize probabilities, or choose hindsight trades.
"""
from pathlib import Path
import json
import numpy as np,pandas as pd
W=Path(__file__).resolve().parent
P='expanded_poly_fullminute_'
e=pd.read_parquet(W/(P+'event_panel.parquet'))
l=pd.read_parquet(W/(P+'leg_panel.parquet'))
meta=json.loads((W/(P+'analysis_metadata.json')).read_text())
rows=[];audit=[]
markets=pd.read_parquet(W/'expanded_poly_market_metadata.parquet').set_index('yes_token_id')
calendar=pd.read_csv(W/'expanded_poly_meeting_inventory.csv')[['meeting_date','realized_change_bp']].drop_duplicates().set_index('meeting_date')
def result_for(token,meeting):
 r=markets.loc[token]
 try:
  raw=[float(x) for x in json.loads(r.final_outcome_prices)]
  if raw in ([0.,1.],[1.,0.]):return raw[0],'stored_resolved_binary_outcome_prices'
 except Exception:pass
 a=calendar.loc[meeting,'realized_change_bp']
 if pd.notna(a) and pd.notna(r.rule_lower_bp) and pd.notna(r.rule_upper_bp) and not r.rules_core_ambiguous:
  hit=(a>=r.rule_lower_bp if r.lower_inclusive else a>r.rule_lower_bp) and (a<=r.rule_upper_bp if r.upper_inclusive else a<r.rule_upper_bp)
  return float(hit),'calendar_realized_change_and_stored_rule_membership'
 return np.nan,'unknown'

for (meeting,eid),g in e.groupby(['meeting_date','event_id'],sort=True):
 if not g.historical_research_eligible.all():continue
 g=g.set_index('anchor_ts');lg=l[(l.meeting_date==meeting)&(l.event_id==eid)]
 market=lg.drop_duplicates('token_id').sort_values(['representative_bp','bin_label']);tokens=market.token_id.to_list()
 p=lg.pivot(index='anchor_ts',columns='token_id',values='mid_60s').reindex(index=g.index,columns=tokens)
 n=lg.pivot(index='anchor_ts',columns='token_id',values='no_mid_60s').reindex(index=g.index,columns=tokens)
 obs=p.gt(.01);counts=obs.sum(axis=1);two=counts.eq(2)
 source={col:lg.pivot(index='anchor_ts',columns='token_id',values=col).reindex(index=g.index,columns=tokens) for col in ['age_sec','no_age_sec','observed_ts','no_observed_ts','fidelity','no_fidelity']}

 exact_main=market.main_exact_25bp_contract.to_numpy();known_rule=market.contract_rule_unambiguous.to_numpy()
 for i in range(len(tokens)):
  for j in range(i+1,len(tokens)):
   ix=two & obs.iloc[:,i] & obs.iloc[:,j]
   if not ix.any():continue
   a,b=market.iloc[i],market.iloc[j]
   if pd.isna(a.representative_bp) or pd.isna(b.representative_bp):continue
   q=g.loc[ix].reset_index();q['lower_token_id']=a.token_id;q['upper_token_id']=b.token_id;q['lower_no_token_id']=a.no_token_id;q['upper_no_token_id']=b.no_token_id
   q['lower_label']=a.bin_label;q['upper_label']=b.bin_label;q['lower_rule_unambiguous']=a.contract_rule_unambiguous;q['upper_rule_unambiguous']=b.contract_rule_unambiguous
   q['d0_bp']=a.representative_bp;q['d1_bp']=b.representative_bp;q['gap_bp']=b.representative_bp-a.representative_bp
   q['main_states_exact_on_25bp_grid']=bool(a.contract_point_exact_on_25bp_grid and b.contract_point_exact_on_25bp_grid)
   q['both_principal_contracts_unambiguous']=bool(a.contract_rule_unambiguous and b.contract_rule_unambiguous)
   q['both_principal_are_HOLD_CUT25_HIKE25']=bool(a.main_exact_25bp_contract and b.main_exact_25bp_contract)
   q['p_lower']=p.loc[ix].iloc[:,i].to_numpy();q['p_upper']=p.loc[ix].iloc[:,j].to_numpy()
   extra=[]
   for label,k,token in [('lower',i,a.token_id),('upper',j,b.token_id)]:
    for side,px,pref in [('yes',p,''),('no',n,'no_')]:
     key=f'{label}_{side}_mid_60s';q[key]=px.loc[ix].iloc[:,k].to_numpy();extra.append(key)
     for col in ['age_sec','observed_ts','fidelity']:
      key=f'{label}_{side}_{col}';q[key]=source[pref+col].loc[ix].iloc[:,k].to_numpy();extra.append(key)
    win,win_source=result_for(token,meeting)
    for key,val in [(f'{label}_yes_winner_if_known',win),(f'{label}_no_winner_if_known',1-win),(f'{label}_winner_source',win_source)]:q[key]=val;extra.append(key)
   q['winner_fields_are_post_event_only']=True;extra.append('winner_fields_are_post_event_only')

   q['actual_no_lower_mid']=n.loc[ix].iloc[:,i].to_numpy();q['actual_no_upper_mid']=n.loc[ix].iloc[:,j].to_numpy()
   q['complement_no_lower_proxy']=1-q.p_lower;q['actual_no_lower_available']=q.actual_no_lower_mid.notna()
   q['actual_no_lower_minus_complement_cents']=100*(q.actual_no_lower_mid-q.complement_no_lower_proxy)
   q['observed_exactly_two_gt1pct']=True
   q['strict_complete_two_principal_gate']=q.two_principal_strict_60s
   q['partial_or_ambiguous_ladder']=~q.strict_complete_two_principal_gate
   q['all_other_states_below_or_equal_1pct_verified']=q.strict_complete_two_principal_gate
   q['unlisted_tail_probability_unknown']=~q.exhaustive_on_25bp_grid
   q['unobserved_or_rule_ambiguous_tail_probability_unknown']=~q.strict_complete_two_principal_gate
   q['observed_nonprincipal_weight']=p.loc[ix].sum(axis=1,min_count=1).to_numpy()-q.p_lower-q.p_upper
   q['executable_price_claim']=False
   keep=['meeting_date','event_id','canonical_event_id','anchor_ts','date_et','days_to_meeting','lower_token_id','upper_token_id','lower_no_token_id','upper_no_token_id','lower_label','upper_label','d0_bp','d1_bp','gap_bp','p_lower','p_upper','actual_no_lower_mid','actual_no_upper_mid','complement_no_lower_proxy','actual_no_lower_available','actual_no_lower_minus_complement_cents','lower_rule_unambiguous','upper_rule_unambiguous','main_states_exact_on_25bp_grid','both_principal_contracts_unambiguous','both_principal_are_HOLD_CUT25_HIKE25','observed_exactly_two_gt1pct','strict_complete_two_principal_gate','partial_or_ambiguous_ladder','all_other_states_below_or_equal_1pct_verified','unlisted_tail_probability_unknown','unobserved_or_rule_ambiguous_tail_probability_unknown','observed_nonprincipal_weight','n_valid_legs_60s','n_legs','all_legs_fresh_60s','all_legs_minute_60s','n_half_60s','n_constant_inplay_60s','max_age_sec_60s','executable_price_claim']
   rows.append(q[keep+extra])
 audit.append(dict(meeting_date=meeting,event_id=eid,all_candidate_anchors=len(g),any_fresh_yes_anchors=int(g.n_valid_legs_60s.gt(0).sum()),complete_strict_anchors=int(g.usable_strict_ladder_60s.sum()),complete_strict_two_gt1pct_anchors=int(g.two_principal_strict_60s.sum()),all_ladder_observed_two_gt1pct_anchors=int((two & g.all_legs_fresh_60s).sum()),partial_observed_two_gt1pct_anchors=int((two & ~g.two_principal_strict_60s).sum())))
out=pd.concat(rows,ignore_index=True);assert not out.duplicated(['meeting_date','event_id','anchor_ts']).any()
out.to_parquet(W/(P+'principal_panel.parquet'),index=False);pd.DataFrame(audit).to_csv(W/(P+'principal_coverage.csv'),index=False)
print(json.dumps(dict(pair_rows=len(out),strict_pair_rows=int(out.strict_complete_two_principal_gate.sum()),partial_pair_rows=int(out.partial_or_ambiguous_ladder.sum()),meetings=out.meeting_date.nunique(),unambiguous_main_pair_rows=int(out.both_principal_are_HOLD_CUT25_HIKE25.sum()))))
