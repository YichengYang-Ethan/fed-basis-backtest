"""Fixed 36-rule exploratory execution stress. No purchase or order calls."""
from pathlib import Path
import json,hashlib,itertools
import numpy as np
import pandas as pd
W=Path(__file__).resolve().parent;O=W.parent/'outputs';T=W/'tail_thresholds';ET='America/New_York'
P=json.loads((O/'execution_parameter_protocol.json').read_text())

def select(f,edge,persist):
    x=f.sort_values(['meeting_date','persistence_key','anchor_ts']).copy()
    prev=x.groupby(['meeting_date','persistence_key'],sort=False).shift()
    good=x.edge_cents.ge(edge)
    cont=good&prev.edge_cents.ge(edge)&x.anchor_ts.sub(prev.anchor_ts).eq(60)&x.date_et.eq(prev.date_et)
    x['run']=(~cont).cumsum();x['run_length']=x.groupby('run').cumcount()+1
    q=x[good&x.run_length.ge(persist)].sort_values(['meeting_date','anchor_ts','edge_cents','persistence_key'],ascending=[True,True,False,True])
    return q.drop_duplicates('meeting_date')

def stats(values):
    x=pd.Series(values).dropna();w=x[x>0];l=x[x<0]
    return dict(known=len(x),wins=len(w),losses=len(l),zeros=int(x.eq(0).sum()),win_rate=len(w)/len(x) if len(x) else np.nan,
                mean_cents=x.mean(),median_cents=x.median(),worst_cents=x.min(),avg_win_cents=w.mean(),avg_loss_abs_cents=-l.mean(),
                payoff_ratio=w.mean()/(-l.mean()) if len(l) and len(w) else np.nan,
                profit_factor=w.sum()/(-l.sum()) if len(l) else np.nan)

def components(u):
    par={m:m for m in u.meeting_date}
    def root(m):
        while par[m]!=m:m=par[m]
        return m
    months={r.meeting_date:set(r.months.split('|')) for r in u.itertuples()}
    for a,b in itertools.combinations(par,2):
        if months[a]&months[b]:par[root(b)]=root(a)
    return {m:root(m) for m in par}

def delayed(entries):
    z=entries.copy();z['execution_anchor_ts']=z.anchor_ts+60;anchors=sorted(z.execution_anchor_ts.unique().tolist())
    cols=['meeting_date','event_id','anchor_ts','token_id','no_token_id','mid_60s','no_mid_60s','observed_ts','no_observed_ts','age_sec','no_age_sec','usable_60s','no_usable_60s','in_lifetime','date_et']
    leg=pd.read_parquet(W/'expanded_poly_fullminute_leg_panel.parquet',columns=cols,filters=[('anchor_ts','in',anchors)])
    pieces=[]
    for side in ['YES','NO']:
        pre='' if side=='YES' else 'no_';q=leg[['meeting_date','event_id','anchor_ts','date_et','in_lifetime',pre+'token_id',pre+'mid_60s',pre+'observed_ts',pre+'age_sec',pre+'usable_60s']].copy()
        q.columns=['meeting_date','event_id','execution_anchor_ts','delay_quote_date_et','delay_in_lifetime','actual_token_id','delay_price','delay_observed_ts','delay_age_sec','delay_token_usable'];pieces.append(q)
    quotes=pd.concat(pieces,ignore_index=True);keys=['meeting_date','event_id','execution_anchor_ts','actual_token_id'];assert not quotes.duplicated(keys).any()
    z=z.merge(quotes,on=keys,how='left',validate='many_to_one')
    cs=['meeting_date','instrument','anchor_ts','leg_near','leg_far']+[f'{r}_{s}' for r in ['synthetic','listed'] for s in ['bid_D_bp','ask_D_bp','bid_one_spread_carry','ask_one_spread_carry','age_le_60s']]
    c=pd.read_parquet(W/'expanded_cme_minute_panel.parquet',columns=cs,filters=[('anchor_ts','in',anchors)])
    c=c.rename(columns={'anchor_ts':'execution_anchor_ts'});keys=['meeting_date','instrument','execution_anchor_ts','leg_near','leg_far'];assert not c.duplicated(keys).any()
    z=z.merge(c,on=keys,how='left',validate='many_to_one')
    z['delay_cme_D_bp']=np.nan;z['delay_cme_usable']=False
    for route in ['synthetic','listed']:
        for sign,side in [(1,'bid'),(-1,'ask')]:
            mask=z.cme_route.eq(route)&z.cme_direction_sign.eq(sign)
            z.loc[mask,'delay_cme_D_bp']=z.loc[mask,f'{route}_{side}_D_bp']
            z.loc[mask,'delay_cme_usable']=(z.loc[mask,f'{route}_{side}_one_spread_carry'].eq(True)&z.loc[mask,f'{route}_age_le_60s'].eq(True)&z.loc[mask,f'{route}_{side}_D_bp'].notna())
    exdate=pd.to_datetime(z.execution_anchor_ts,unit='s',utc=True).dt.tz_convert(ET).dt.strftime('%Y-%m-%d')
    tokenok=z.delay_token_usable.eq(True)&z.delay_in_lifetime.eq(True)&z.delay_age_sec.between(0,60)&z.delay_observed_ts.le(z.execution_anchor_ts)&z.delay_quote_date_et.eq(exdate)&exdate.eq(z.date_et)
    deadline=z.execution_anchor_ts.lt(z.meeting_14et_ts)
    z['delay_observable']=tokenok&deadline&z.delay_cme_usable
    z['delay_status']=np.select([~deadline,~tokenok,~z.delay_cme_usable],['past_decision_deadline','token_quote_missing_or_stale','locked_CME_route_unavailable'],default='observable')
    u=z.quantity/z.N;p=z.delay_price
    z['delay_cost_cents']=u+5*u*p*(1-p)+100*u*p*.05*(z.days_to_meeting+1)/365+500/z.N
    z['delay_edge_cents']=(z.cme_direction_sign*4*z.delay_cme_D_bp-100*(z.cash_perN+u*p)-z.delay_cost_cents).where(z.delay_observable)
    z['delay_pnl_cents']=(z.delay_edge_cents+z.model_payoff_correction_cents).where(z.delay_observable)
    z['delay_pnl_usd']=z.delay_pnl_cents*z.N/100
    for extra in [1,2]:z[f'delay_plus{extra}c_pnl_cents']=z.delay_pnl_cents-extra*u
    # Keep a trace for unavailable rows, including signal price and original PnL.
    drop=[v for v in cs if v not in keys and v not in ['anchor_ts']]
    return z.drop(columns=[v for v in drop if v in z])

def main():
    frames=[];universes=[];eligible_counts=[];features_for_qa={}
    for tau in P['thresholds']:
        name=f'tail_thresholds/poly_binary_tau{int(tau*100)}_omit_only_nested.parquet'
        assert hashlib.sha256((W/name).read_bytes()).hexdigest()==P['input_sha256'][name]
        b=pd.read_parquet(W/name);b=b[b.liveness_pass&b.in_next_meeting_window&b.edge_cents.notna()].reset_index(drop=True);b['row_id']=np.arange(len(b))
        assert not b.duplicated(['meeting_date','anchor_ts','persistence_key']).any()
        f=b[['row_id','meeting_date','date_et','anchor_ts','persistence_key','edge_cents']].copy();features_for_qa[tau]=f
        eligible_counts.append(dict(tau=tau,valid_minutes=len(b[['meeting_date','anchor_ts']].drop_duplicates()),meetings=b.meeting_date.nunique()))
        if tau==.05:
            for md,g in b.groupby('meeting_date'):
                far=max(g.leg_far);available=pd.Timestamp(pd.Period(far).end_time.normalize()+pd.Timedelta(days=7)).tz_localize(ET)
                universes.append(dict(meeting_date=md,previous_fomc_14et_ts=int(g.previous_fomc_14et_ts.iloc[0]),label_available_proxy_utc=available.tz_convert('UTC').isoformat(),terminal_complete=bool(g.model_pnl_cents.notna().all()),max_far=far,months='|'.join(sorted(set(g.leg_near)|set(g.leg_far))),valid_minutes=g.anchor_ts.nunique()))
        for edge,persist in itertools.product(P['net_edge_cents'],P['persistence_minutes']):
            picked=select(f,edge,persist);x=b.set_index('row_id').loc[picked.row_id].reset_index();x['tau']=tau;x['net_edge_threshold']=edge;x['persistence_minutes']=persist;x['rule_id']=f'T{int(tau*100)}_E{edge}_P{persist}';x['entry_et']=pd.to_datetime(x.anchor_ts,unit='s',utc=True).dt.tz_convert(ET).astype(str)
            for extra in [1,2]:x[f'plus{extra}c_pnl_cents']=x.model_pnl_cents-extra*x.quantity/x.N
            frames.append(x)
    e=pd.concat(frames,ignore_index=True);e=delayed(e);e.to_csv(O/'execution_parameter_poly_entries.csv',index=False)
    u=pd.DataFrame(universes).sort_values('meeting_date');u['shared_month_component']=u.meeting_date.map(components(u));u['split']=np.where(np.arange(len(u))<len(u)//2,'early_half','late_half');u.to_csv(O/'execution_parameter_poly_universe.csv',index=False)
    rules=[dict(rule_id=f'T{int(tau*100)}_E{edge}_P{persist}',tau=tau,net_edge_threshold=edge,persistence_minutes=persist) for tau,edge,persist in itertools.product(P['thresholds'],P['net_edge_cents'],P['persistence_minutes'])]
    summary=[];split=[];deletes=[]
    for rule in rules:
        x=e[e.rule_id.eq(rule['rule_id'])];row=rule|dict(candidate_meetings=len(x),pending_terminal=int(x.model_pnl_cents.isna().sum()),sum_model_usd=x.model_pnl_usd.sum(min_count=1),model_tail_hits=int(x.realized_inside_main_pair.eq(False).sum()),mean_omitted_total=x.omitted_total.mean(),max_omitted_total=x.omitted_total.max(),first_hour_count=int(((x.anchor_ts-x.previous_fomc_14et_ts)<3600).sum()),ladder_sum_outside_95_105=int((x.sum_mid_60s.sub(1).abs()>.05).sum()))|stats(x.model_pnl_cents)
        for extra in [1,2]:row.update({f'plus{extra}c_{k}':v for k,v in stats(x[f'plus{extra}c_pnl_cents']).items()})
        row.update(delay_observable=int(x.delay_observable.sum()),delay_missing=int((~x.delay_observable).sum()),delay_edge_negative=int(x.delay_edge_cents.lt(0).sum()),delay_edge_below_rule=int(x.delay_edge_cents.lt(rule['net_edge_threshold']).sum()))
        row.update({f'delay_{k}':v for k,v in stats(x.delay_pnl_cents).items()});row.update({f'delay_plus2c_{k}':v for k,v in stats(x.delay_plus2c_pnl_cents).items()})
        paired=x[x.delay_observable&x.model_pnl_cents.notna()].copy()
        delta=paired.delay_pnl_cents-paired.model_pnl_cents
        row.update(paired_known=len(paired),paired_signal_mean_cents=paired.model_pnl_cents.mean(),paired_delay_mean_cents=paired.delay_pnl_cents.mean(),paired_delay_delta_mean_cents=delta.mean(),paired_delay_delta_worst_cents=delta.min(),paired_changed_prices=int(delta.abs().gt(1e-10).sum()))
        for name,uu in u.groupby('split'):
            xx=x[x.meeting_date.isin(uu.meeting_date)];split.append(rule|dict(split=name,observable_universe_meetings=len(uu),signals=len(xx),sum_model_usd=xx.model_pnl_usd.sum(min_count=1))|stats(xx.model_pnl_cents))
        y=x[x.model_pnl_cents.notna()].copy();y['block']=y.meeting_date.map(u.set_index('meeting_date').shared_month_component)
        for block in y.block.unique():
            yy=y[~y.block.eq(block)];deletes.append(rule|dict(omitted_shared_month_block=block,remaining_trades=len(yy),mean_cents=yy.model_pnl_cents.mean(),plus2c_mean_cents=yy.plus2c_pnl_cents.mean()))
        summary.append(row)
    s=pd.DataFrame(summary);s.to_csv(O/'execution_parameter_poly_summary.csv',index=False);pd.DataFrame(split).to_csv(O/'execution_parameter_poly_split.csv',index=False);pd.DataFrame(deletes).to_csv(O/'execution_parameter_poly_delete_block.csv',index=False)
    # Reconstructed chronological selection, not untouched OOS.
    ruleids=[r['rule_id'] for r in rules];pnl=pd.DataFrame(0.,index=u.meeting_date,columns=ruleids);trades=pnl.astype(bool)
    for r in e.itertuples():pnl.loc[r.meeting_date,r.rule_id]=r.model_pnl_cents;trades.loc[r.meeting_date,r.rule_id]=True
    wf=[];scores=[]
    for minmeet,mintr,layer in [(8,5,'main_min8_5'),(6,4,'diagnostic_min6_4')]:
        for r in u.itertuples():
            cut=pd.to_datetime(r.previous_fomc_14et_ts,unit='s',utc=True)
            train=u[u.terminal_complete&u.meeting_date.lt(r.meeting_date)&pd.to_datetime(u.label_available_proxy_utc).lt(cut)]
            qualified=[]
            for rule in rules:
                rid=rule['rule_id'];y=pnl.loc[train.meeting_date,rid];nt=int(trades.loc[train.meeting_date,rid].sum());se=y.std(ddof=1)/np.sqrt(len(y)) if len(y)>1 else np.nan;score=y.mean()-se;loo=(y.sum()-y)/(len(y)-1) if len(y)>1 else pd.Series(dtype=float)
                ok=len(y)>=minmeet and nt>=mintr and score>0 and len(loo)>0 and loo.min()>0
                scores.append(dict(layer=layer,test_meeting=r.meeting_date,rule_id=rid,cutoff_utc=cut.isoformat(),train_meetings='|'.join(train.meeting_date),n_train=len(y),n_trades=nt,mean=y.mean(),score=score,min_loo_mean=loo.min(),qualified=ok))
                if ok:qualified.append((float(score),rid))
            chosen=sorted(qualified,key=lambda v:(-v[0],v[1]))[0][1] if qualified else ''
            xx=e[e.rule_id.eq(chosen)&e.meeting_date.eq(r.meeting_date)]
            row=dict(layer=layer,test_meeting=r.meeting_date,cutoff_utc=cut.isoformat(),n_train=len(train),qualified_rules=len(qualified),selected_rule=chosen,decision='ABSTAIN_NO_RULE' if not chosen else ('SIGNAL' if len(xx) else 'SELECTED_RULE_NO_SIGNAL'),has_entry=bool(len(xx)),model_pnl_cents=0. if chosen and not len(xx) else np.nan,delay_pnl_cents=0. if chosen and not len(xx) else np.nan,plus2c_pnl_cents=0. if chosen and not len(xx) else np.nan,true_untouched_OOS=False)
            if len(xx):
                z=xx.iloc[0];assert z.anchor_ts>=r.previous_fomc_14et_ts
                row.update(entry_et=z.entry_et,model_pnl_cents=z.model_pnl_cents,plus2c_pnl_cents=z.plus2c_pnl_cents,delay_pnl_cents=z.delay_pnl_cents,delay_plus2c_pnl_cents=z.delay_plus2c_pnl_cents)
            wf.append(row)
    pd.DataFrame(wf).to_csv(O/'execution_parameter_poly_chronology.csv',index=False);pd.DataFrame(scores).to_csv(O/'execution_parameter_poly_training.csv',index=False)
    # Baseline and independent persistence checks.
    old=pd.read_csv(O/'tail_threshold_poly_candidates.csv');old=old[old['mode'].eq('omit_only_nested')&old.scope.eq('next_meeting_only')]
    for tau in P['thresholds']:
        a=old[old.threshold.eq(tau)].sort_values('meeting_date');b=e[e.rule_id.eq(f'T{int(tau*100)}_E2_P3')].sort_values('meeting_date')
        assert list(a.anchor_ts)==list(b.anchor_ts) and np.allclose(a.model_pnl_usd,b.model_pnl_usd,equal_nan=True)
    ncheck=0
    for r in e.itertuples():
        f=features_for_qa[r.tau];s=f[(f.meeting_date==r.meeting_date)&(f.persistence_key==r.persistence_key)&f.anchor_ts.between(r.anchor_ts-60*(r.persistence_minutes-1),r.anchor_ts)]
        assert len(s)==r.persistence_minutes and s.edge_cents.ge(r.net_edge_threshold).all() and s.date_et.nunique()==1;ncheck+=len(s)
    qa=dict(rules=36,signal_scenario_rows=len(e),unique_entries=len(e[['meeting_date','anchor_ts','persistence_key']].drop_duplicates()),common_meetings=len(u),known_common_meetings=int(u.terminal_complete.sum()),independent_original_price_persistence_observations=ncheck,baseline_three_tail_levels_exact=True,delay_missing_status_counts=e.delay_status.value_counts().to_dict(),fixed_delayed_original_route=True,delay_reapplied_signal_gate=False,labels_used_for_entry=False,new_spend_usd=0,true_untouched_OOS=False)
    (O/'execution_parameter_poly_qa.json').write_text(json.dumps(qa,indent=2));print(json.dumps(qa,indent=2));print(s if False else pd.DataFrame(summary)[['rule_id','candidate_meetings','known','wins','losses','mean_cents','payoff_ratio','plus2c_mean_cents','delay_known','delay_win_rate','delay_plus2c_mean_cents']].to_string(index=False))

if __name__=='__main__':main()
