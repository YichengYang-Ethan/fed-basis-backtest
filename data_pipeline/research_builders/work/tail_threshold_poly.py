"""Local-only threshold panels rebuilt from unfiltered full-lifetime legs.
No strategy returns or threshold optimization. Old outputs are never changed.
"""
import os
from pathlib import Path
from pathlib import Path
import json,hashlib
import numpy as np,pandas as pd,duckdb
from expanded_poly_liveness import rolling_at_anchors

W=Path(__file__).resolve().parent;T=W/'tail_thresholds';T.mkdir(exist_ok=True)
O=W.parent/'outputs';D=Path(os.environ['FOMC_DATA_ROOT']) / 'raw/poly'
A=Path(os.environ['FOMC_PROJECT_ROOT']) / 'data/fomc_instruments.csv'
KEY=['meeting_date','event_id','anchor_ts'];PKEY=['threshold','mode']+KEY
TAUS=[.01,.03,.05];MODES=['exact_two_above_tau','omit_only_nested'];ET='America/New_York'

def winner(r,realized):
    try:
        prices=[float(x) for x in json.loads(r.final_outcome_prices)]
        tokens=json.loads(r.token_ids)
        if sorted(prices)==[0.,1.] and len(tokens)==2:
            return float(dict(zip(tokens,prices))[r.yes_token_id]),'stored_resolved_binary_outcome_prices'
    except (ValueError,TypeError,KeyError):pass
    a=realized.get(r.meeting_date,np.nan)
    if pd.notna(a) and pd.notna(r.rule_lower_bp) and pd.notna(r.rule_upper_bp) and not r.rules_core_ambiguous:
        hit=(a>=r.rule_lower_bp if r.lower_inclusive else a>r.rule_lower_bp) and (a<=r.rule_upper_bp if r.upper_inclusive else a<r.rule_upper_bp)
        return float(hit),'calendar_realized_change_and_stored_rule_membership'
    return np.nan,'unknown'

def make_panels():
    e=pd.read_parquet(W/'expanded_poly_fullminute_event_panel.parquet')
    e=e[e.historical_research_eligible].copy();assert not e.duplicated(KEY).any()
    cal=pd.read_csv(A).sort_values('meeting_date');assert not cal.meeting_date.duplicated().any()
    prev=dict(zip(cal.meeting_date,cal.meeting_date.shift()))
    e['previous_fomc_date']=e.meeting_date.map(prev)
    e['previous_fomc_calendar_known']=e.previous_fomc_date.notna()
    e['previous_fomc_14et_ts']=pd.to_datetime(e.previous_fomc_date+' 14:00').dt.tz_localize(ET).dt.tz_convert('UTC').astype('int64')//10**9
    e.loc[~e.previous_fomc_calendar_known,'previous_fomc_14et_ts']=np.nan
    e['meeting_14et_ts']=pd.to_datetime(e.meeting_date+' 14:00').dt.tz_localize(ET).dt.tz_convert('UTC').astype('int64')//10**9
    e['in_next_meeting_window']=e.previous_fomc_calendar_known&e.anchor_ts.ge(e.previous_fomc_14et_ts)&e.anchor_ts.lt(e.meeting_14et_ts)
    e['rule_complete_identified']=e.all_known_labels&e.exhaustive_on_25bp_grid&~e.rules_ambiguous
    lcols=KEY+['canonical_event_id','market_id','token_id','no_token_id','bin_label','bin_kind','representative_bp','tail_direction','rule_lower_bp','rule_upper_bp','lower_inclusive','upper_inclusive','contract_rule_unambiguous','contract_point_exact_on_25bp_grid','main_exact_25bp_contract','mid_60s','no_mid_60s','observed_ts','no_observed_ts','age_sec','no_age_sec','fidelity','no_fidelity']
    l=pd.read_parquet(W/'expanded_poly_fullminute_leg_panel.parquet',columns=lcols)
    m=pd.read_parquet(W/'expanded_poly_market_metadata.parquet');realized=cal.set_index('meeting_date').realized_change_bp.to_dict()
    win={r.yes_token_id:winner(r,realized) for r in m.itertuples()}
    panels=[];coverage=[];audit=[];top_records=[]
    for (meeting,eid),allg in e.groupby(['meeting_date','event_id'],sort=True):
        g=allg[allg.usable_strict_ladder_60s].copy().sort_values('anchor_ts').reset_index(drop=True)
        common_cov=dict(meeting_date=meeting,event_id=eid)
        for window in ['full_lifetime','next_meeting_window']:
            ag=allg if window=='full_lifetime' else allg[allg.in_next_meeting_window]
            for mode in MODES:
                for tau in TAUS:coverage.append(dict(**common_cov,threshold=tau,mode=mode,window=window,lifetime_anchors=len(ag),identified_complete_rule_anchors=int(ag.rule_complete_identified.sum()),strict_fresh_anchors=int(ag.usable_strict_ladder_60s.sum()),mode_gate_anchors=0,adjacent_point_anchors=0))
        if g.empty:continue
        lg=l[(l.meeting_date==meeting)&(l.event_id==eid)&l.anchor_ts.isin(g.anchor_ts)].copy()
        market=lg.drop_duplicates('token_id').sort_values(['representative_bp','bin_label','token_id']).reset_index(drop=True);tokens=market.token_id.tolist()
        values={col:lg.pivot(index='anchor_ts',columns='token_id',values=col).reindex(index=g.anchor_ts,columns=tokens).to_numpy() for col in ['mid_60s','no_mid_60s','observed_ts','no_observed_ts','age_sec','no_age_sec','fidelity','no_fidelity']}
        px=values['mid_60s'];assert np.isfinite(px).all() and ((px>=0)&(px<=1)).all()
        # Stable tie order fixed by representative bp, label, then token ID;
        # neither threshold nor future result is used for ranking.
        ranks=np.argsort(-px,axis=1,kind='stable');lo=np.minimum(ranks[:,0],ranks[:,1]);hi=np.maximum(ranks[:,0],ranks[:,1]);ii=np.arange(len(g))
        lower=market.iloc[lo].reset_index(drop=True);upper=market.iloc[hi].reset_index(drop=True)
        base=g.copy();base['lower_token_id']=lower.token_id;base['upper_token_id']=upper.token_id;base['lower_no_token_id']=lower.no_token_id;base['upper_no_token_id']=upper.no_token_id
        base['lower_label']=lower.bin_label;base['upper_label']=upper.bin_label;base['d0_bp']=lower.representative_bp;base['d1_bp']=upper.representative_bp;base['gap_bp']=base.d1_bp-base.d0_bp
        base['lower_rule_unambiguous']=lower.contract_rule_unambiguous;base['upper_rule_unambiguous']=upper.contract_rule_unambiguous
        base['both_principal_contracts_unambiguous']=lower.contract_rule_unambiguous&upper.contract_rule_unambiguous
        base['main_states_exact_on_25bp_grid']=lower.contract_point_exact_on_25bp_grid&upper.contract_point_exact_on_25bp_grid
        base['both_principal_are_HOLD_CUT25_HIKE25']=lower.main_exact_25bp_contract&upper.main_exact_25bp_contract
        base['principal_adjacent_point_pass']=base.gap_bp.eq(25)&base.both_principal_contracts_unambiguous&base.main_states_exact_on_25bp_grid
        base['p_lower']=px[ii,lo];base['p_upper']=px[ii,hi]
        base['main_pair_mid_sum']=base.p_lower+base.p_upper;base['all_outcome_yes_mid_sum']=px.sum(axis=1)
        base['main_pair_fraction_of_observed_mid_sum']=base.main_pair_mid_sum/base.all_outcome_yes_mid_sum
        omitted=px.copy();omitted[ii,lo]=np.nan;omitted[ii,hi]=np.nan
        base['omitted_total']=np.nansum(omitted,axis=1);base['omitted_max']=np.nanmax(omitted,axis=1);base['omitted_outcome_count']=len(tokens)-2
        base['omitted_count_gt_1pct']=np.sum(omitted>.01,axis=1);base['observed_nonprincipal_weight']=base.omitted_total
        base['all_other_states_below_or_equal_1pct_verified']=base.omitted_max.le(.01)
        base['baseline_one_pct_two_state_gate']=base.two_principal_strict_60s
        base['complete_ladder_rule_gate']=True;base['partial_or_ambiguous_ladder']=False;base['unlisted_tail_probability_unknown']=False;base['omitted_prices_complete']=True
        for label,indices in [('lower',lo),('upper',hi)]:
            for side,prefix in [('yes',''),('no','no_')]:
                base[f'{label}_{side}_mid_60s']=values[prefix+'mid_60s'][ii,indices]
                for col in ['observed_ts','age_sec','fidelity']:base[f'{label}_{side}_{col}']=values[prefix+col][ii,indices]
            tw=[win[t] for t in base[label+'_token_id']]
            base[label+'_yes_winner_if_known']=[x[0] for x in tw];base[label+'_no_winner_if_known']=1-base[label+'_yes_winner_if_known'];base[label+'_winner_source']=[x[1] for x in tw]
        base['winner_fields_are_post_event_only']=True;base['winner_known_at_historical_proven']=False
        top_records.append(base[KEY+['lower_token_id','upper_token_id','p_lower','p_upper','omitted_total','omitted_max','main_pair_mid_sum','principal_adjacent_point_pass','in_next_meeting_window']])
        used=np.zeros(len(base),dtype=bool)
        for tau in TAUS:
            ga=(px>tau).sum(axis=1)==2
            gb=(base.p_lower>.01)&(base.p_upper>.01)&base.omitted_max.le(tau)
            assert np.all(~ga|np.asarray(gb))
            if tau==.01:assert np.array_equal(ga,gb)
            for mode,gate in [(MODES[0],ga),(MODES[1],np.asarray(gb))]:
                eligible=np.asarray(gate)&base.principal_adjacent_point_pass.to_numpy();used|=eligible
                q=base.loc[eligible].copy();q['threshold']=tau;q['mode']=mode;q['mode_gate_pass']=True;q['all_other_states_below_or_equal_tau_verified']=True
                panels.append(q)
                for window in ['full_lifetime','next_meeting_window']:
                    wmask=np.ones(len(base),bool) if window=='full_lifetime' else base.in_next_meeting_window.to_numpy()
                    row=next(r for r in coverage if r['meeting_date']==meeting and r['event_id']==eid and r['threshold']==tau and r['mode']==mode and r['window']==window)
                    row['mode_gate_anchors']=int((np.asarray(gate)&wmask).sum());row['adjacent_point_anchors']=int((eligible&wmask).sum())
        audit.append(lg[lg.anchor_ts.isin(base.loc[used,'anchor_ts'])])
    p=pd.concat(panels,ignore_index=True).sort_values(PKEY).reset_index(drop=True);assert not p.duplicated(PKEY).any()
    # Every omitted outcome is retained once per event/token/anchor, not replaced by zero.
    outcomes=pd.concat(audit,ignore_index=True).sort_values(KEY+['token_id']);assert not outcomes.duplicated(KEY+['token_id']).any()
    outcomes['yes_winner_if_known']=outcomes.token_id.map({t:v[0] for t,v in win.items()});outcomes['no_winner_if_known']=1-outcomes.yes_winner_if_known;outcomes['winner_fields_are_post_event_only']=True
    outcomes.to_parquet(T/'poly_outcomes.parquet',index=False)
    pd.concat(top_records,ignore_index=True).to_parquet(T/'poly_top2_complete_fresh.parquet',index=False)
    m.to_parquet(T/'poly_market_rules.parquet',index=False)
    # Reconstruct the old 1% complete/adjacent sample exactly as a regression check.
    old=pd.read_parquet(W/'expanded_poly_fullminute_principal_panel.parquet')
    old=old[old.strict_complete_two_principal_gate&old.gap_bp.eq(25)&old.main_states_exact_on_25bp_grid&old.both_principal_contracts_unambiguous]
    baseline=p[p.threshold.eq(.01)&p['mode'].eq(MODES[0])]
    z=baseline.merge(old,on=KEY,suffixes=('_new','_old'),validate='one_to_one')
    assert len(z)==len(old)==len(baseline)==44450
    for col in ['lower_token_id','upper_token_id','lower_no_token_id','upper_no_token_id']:assert z[col+'_new'].eq(z[col+'_old']).all()
    for lab in ['lower','upper']:
        for side in ['yes','no']:
            for suffix in ['mid_60s','observed_ts','age_sec','winner_if_known']:
                col=f'{lab}_{side}_{suffix}';assert np.allclose(z[col+'_new'].to_numpy(float),z[col+'_old'].to_numpy(float),equal_nan=True)
    p.to_parquet(T/'poly_principals.parquet',index=False)
    pd.DataFrame(coverage).to_csv(T/'poly_coverage_before_liveness.csv',index=False)
    print(json.dumps({'stage':'principal_ready','rows':len(p),'unique_event_anchors':len(p.drop_duplicates(KEY)),'outcome_rows':len(outcomes),'baseline_exact_match':len(z)}),flush=True)
    return p,pd.DataFrame(coverage)

def live(p):
    # Deduplicate token/anchor work across all thresholds and modes.
    parts=[]
    for label in ['lower','upper']:
        for side in ['yes','no']:
            pref=f'{label}_{side}';token=f'{label}_token_id' if side=='yes' else f'{label}_no_token_id'
            q=p[['anchor_ts','date_et',token,pref+'_mid_60s',pref+'_age_sec',pref+'_observed_ts']].copy();q.columns=['anchor_ts','date_et','token_id','current_mid','current_age','current_observed_ts'];parts.append(q)
    a=pd.concat(parts,ignore_index=True).drop_duplicates();assert not a.duplicated(['token_id','anchor_ts']).any()
    ranges=a.groupby('token_id',as_index=False).agg(min_anchor=('anchor_ts','min'),max_anchor=('anchor_ts','max'));ranges['min_ts']=ranges.min_anchor-3600
    m=pd.read_parquet(W/'expanded_poly_market_metadata.parquet')
    life=pd.concat([m[[col,'lifetime_start','lifetime_end']].rename(columns={col:'token_id'}) for col in ['yes_token_id','no_token_id']]).drop_duplicates('token_id')
    life['life_start_ts']=life.lifetime_start.astype('int64')//10**9;life['life_end_ts']=life.lifetime_end.astype('int64')//10**9
    ranges=ranges.merge(life[['token_id','life_start_ts','life_end_ts']],on='token_id',validate='one_to_one')
    c=duckdb.connect();c.execute("SET memory_limit='1GB'");c.execute('SET threads=2');c.execute(f"SET temp_directory='{T/'duckdb_tmp'}'");c.register('needed',ranges)
    raw=T/'poly_liveness_observations.parquet'
    query=f"""SELECT x.token_id,x.ts,min(x.p) p,min(x.fidelity) fidelity FROM read_parquet(['{D}/prices.parquet','{W}/expanded_poly_fullminute_parts/*.parquet'],union_by_name=true) x JOIN needed n ON x.token_id=n.token_id WHERE x.ts>=greatest(n.min_ts,n.life_start_ts) AND x.ts<=n.max_anchor AND x.ts<n.life_end_ts AND x.p>=0 AND x.p<=1 AND CAST(timezone('{ET}',to_timestamp(x.ts)) AS TIME)>=TIME '09:00:00' AND CAST(timezone('{ET}',to_timestamp(x.ts)) AS TIME)<=TIME '15:00:00' GROUP BY x.token_id,x.ts ORDER BY x.token_id,x.ts"""
    c.execute(f"COPY ({query}) TO '{raw}' (FORMAT PARQUET,COMPRESSION ZSTD,ROW_GROUP_SIZE 250000)")
    out=[];checks=0
    for token,g in a.groupby('token_id',sort=True):
        g=g.sort_values('anchor_ts').copy();r=pd.read_parquet(raw,filters=[('token_id','==',token)],columns=['ts','p','fidelity'])
        ts=r.ts.to_numpy(np.int64);prices=r.p.to_numpy(float);fid=r.fidelity.to_numpy(np.int64);anchors=g.anchor_ts.to_numpy(np.int64)
        assert len(ts)==0 or (np.diff(ts)>0).all()
        midnight=pd.to_datetime(g.date_et).dt.tz_localize(ET).dt.tz_convert('UTC').astype('int64').to_numpy()//10**9;lo=np.maximum(anchors-3600,midnight)
        counts,extrema=rolling_at_anchors(ts,prices,fid,anchors,lo)
        g['trail60_n_observation_timestamps']=counts[:,0];g['trail60_n_distinct_prices']=counts[:,1];g['trail60_first_observed_ts']=extrema[:,0];g['trail60_last_observed_ts']=extrema[:,1]
        valid=g.current_mid.between(0,1)&g.current_age.between(0,60)&g.current_observed_ts.le(g.anchor_ts)
        valid&=pd.to_datetime(g.current_observed_ts,unit='s',utc=True).dt.tz_convert(ET).dt.strftime('%Y-%m-%d').eq(g.date_et)
        g['liveness_required']=valid&g.current_mid.between(.02,.98);g['current_quote_valid']=valid
        g['liveness_pass']=valid&(counts[:,0]>0)&(~g.liveness_required|((counts[:,0]>=20)&(counts[:,1]>=2)))
        g['liveness_status']=np.select([~valid,counts[:,0]==0,~g.liveness_required,counts[:,0]<20,counts[:,1]<2],['missing_current_quote','no_history','not_required_outside_inplay','insufficient_observations','insufficient_price_variation'],default='pass')
        for idx in np.unique(np.linspace(0,len(g)-1,min(11,len(g)),dtype=int)):
            mask=(ts>=lo[idx])&(ts<=anchors[idx]);assert int(mask.sum())==counts[idx,0] and len(np.unique(prices[mask]))==counts[idx,1];checks+=1
        active=counts[:,0]>0
        assert np.all(extrema[active,0]>=lo[active]) and np.all(extrema[active,1]<=anchors[active])
        for ix in [0,1]:assert pd.to_datetime(extrema[active,ix],unit='s',utc=True).tz_convert(ET).strftime('%Y-%m-%d').to_numpy().tolist()==g.loc[active,'date_et'].tolist()
        out.append(g)
    token=pd.concat(out,ignore_index=True);token.to_parquet(T/'poly_token_liveness.parquet',index=False)
    cols=['current_quote_valid','liveness_required','liveness_pass','liveness_status','trail60_n_observation_timestamps','trail60_n_distinct_prices','trail60_first_observed_ts','trail60_last_observed_ts']
    result=p[PKEY+['date_et','lower_token_id','upper_token_id','lower_no_token_id','upper_no_token_id']].copy()
    for label in ['lower','upper']:
        for side in ['yes','no']:
            pref=f'{label}_{side}';tok=f'{label}_token_id' if side=='yes' else f'{label}_no_token_id'
            mapping=token[['token_id','anchor_ts']+cols].rename(columns={'token_id':tok,**{col:pref+'_'+col for col in cols}})
            result=result.merge(mapping,on=[tok,'anchor_ts'],how='left',validate='many_to_one')
    result['both_main_yes_liveness_pass']=result.lower_yes_liveness_pass&result.upper_yes_liveness_pass
    result['principal_liveness_pass']=result.both_main_yes_liveness_pass&result.lower_no_liveness_pass&result.upper_no_liveness_pass
    assert not result.duplicated(PKEY).any();result.to_parquet(T/'poly_liveness.parquet',index=False)
    return result,dict(token_anchor_rows=len(token),unique_tokens=token.token_id.nunique(),independent_exact_window_checks=checks)

def finish(p,cov,liveness,lqa):
    p=p.merge(liveness[PKEY+['both_main_yes_liveness_pass','principal_liveness_pass']],on=PKEY,validate='one_to_one')
    for i,r in cov.iterrows():
        subset=p[p.meeting_date.eq(r.meeting_date)&p.threshold.eq(r.threshold)&p['mode'].eq(r['mode'])]
        baseline=p[p.meeting_date.eq(r.meeting_date)&p.threshold.eq(.01)&p['mode'].eq(r['mode'])]
        if r.window=='next_meeting_window':subset=subset[subset.in_next_meeting_window];baseline=baseline[baseline.in_next_meeting_window]
        now=set(subset.anchor_ts);old=set(baseline.anchor_ts)
        assert len(now)==r.adjacent_point_anchors
        cov.loc[i,'new_vs_1pct_anchors']=len(now-old);cov.loc[i,'lost_vs_1pct_anchors']=len(old-now);cov.loc[i,'common_vs_1pct_anchors']=len(now&old)
        cov.loc[i,'both_main_yes_liveness_anchors']=int(subset.both_main_yes_liveness_pass.sum());cov.loc[i,'all_four_liveness_anchors']=int(subset.principal_liveness_pass.sum())
        cov.loc[i,'omitted_total_mean']=subset.omitted_total.mean();cov.loc[i,'omitted_total_max']=subset.omitted_total.max();cov.loc[i,'omitted_single_max']=subset.omitted_max.max();cov.loc[i,'main_pair_mid_sum_min']=subset.main_pair_mid_sum.min()
        cov.loc[i,'qualified_meetings']=int(len(subset)>0)
    summaries=[];counts=[x for x in cov if x.endswith(('_anchors','_meetings'))]
    for (tau,mode,window),g in cov.groupby(['threshold','mode','window']):
        r=dict(threshold=tau,mode=mode,window=window,meeting_date='ALL',event_id='ALL');r.update({col:int(g[col].sum()) for col in counts})
        q=p[p.threshold.eq(tau)&p['mode'].eq(mode)]
        if window=='next_meeting_window':q=q[q.in_next_meeting_window]
        r.update(omitted_total_mean=q.omitted_total.mean(),omitted_total_max=q.omitted_total.max(),omitted_single_max=q.omitted_max.max(),main_pair_mid_sum_min=q.main_pair_mid_sum.min());summaries.append(r)
    result=pd.concat([pd.DataFrame(summaries),cov],ignore_index=True)
    for col in counts:result[col]=result[col].astype('int64')
    result.to_csv(O/'tail_threshold_poly_coverage.csv',index=False)
    for tau in TAUS[1:]:
        b=p[p.threshold.eq(tau)&p['mode'].eq(MODES[1])];prev=p[p.threshold.eq(TAUS[TAUS.index(tau)-1])&p['mode'].eq(MODES[1])]
        assert set(map(tuple,prev[KEY].to_numpy())).issubset(set(map(tuple,b[KEY].to_numpy())))
    # New 1% liveness must equal independently calculated legacy anchor-relative control.
    oldlive=pd.read_parquet(W/'expanded_poly_fullminute_liveness.parquet');newlive=liveness[liveness.threshold.eq(.01)&liveness['mode'].eq(MODES[0])]
    z=newlive.merge(oldlive,on=KEY,suffixes=('_new','_old'),validate='one_to_one');assert len(z)==44450
    for label in ['lower','upper']:
        for side in ['yes','no']:
            for suffix in ['liveness_pass','trail60_n_observation_timestamps','trail60_n_distinct_prices']:
                col=f'{label}_{side}_{suffix}';assert z[col+'_new'].eq(z[col+'_old']).all()
    qa=dict(rows=len(p),unique_event_anchor_rows=len(p.drop_duplicates(KEY)),thresholds=TAUS,modes=MODES,threshold_is_each_omitted_outcome_not_total=True,fully_fresh_ladder_before_ranking=True,top2_tie_order='representative_bp, bin_label, token_id; stable',baseline_one_percent_principal_rows_matched=44450,baseline_liveness_rows_matched=len(z),omit_only_nested_proven=True,actual_separate_NO_midpoints=True,all_actual_outcomes_saved='poly_outcomes.parquet',known_winners_ex_post_only=True,historical_resolution_known_at_proven=False,previous_fomc_calendar=str(A),calendar_sha256=hashlib.sha256(A.read_bytes()).hexdigest(),next_meeting_window='previous FOMC 14:00 ET <= anchor < target FOMC 14:00 ET; unknown predecessor fails',old_files_unchanged=True,local_only_no_network=True,liveness=lqa,all_summary=summaries)
    (T/'poly_qa.json').write_text(json.dumps(qa,indent=2,default=lambda x:x.item() if hasattr(x,'item') else str(x)))
    print(json.dumps(qa,indent=2,default=lambda x:x.item() if hasattr(x,'item') else str(x)),flush=True)

if __name__=='__main__':
    p,c=make_panels();lv,qa=live(p);finish(p,c,lv,qa)
