"""Historical cross-venue diagnostics. No purchases, orders or source writes."""
from pathlib import Path
import json,hashlib
import numpy as np
import pandas as pd
from edge_stats import correlation_diagnostics,lag_diagnostics,persistent_candidates,algebra_checks

W=Path(__file__).resolve().parent;O=W.parent/'outputs'
KEY=['meeting_date','anchor_ts']

def clean_json(v):
    if isinstance(v,dict):return {str(k):clean_json(x) for k,x in v.items()}
    if isinstance(v,list):return [clean_json(x) for x in v]
    if isinstance(v,(np.integer,)):return int(v)
    if isinstance(v,(np.floating,float)):return float(v) if np.isfinite(v) else None
    if isinstance(v,np.bool_):return bool(v)
    return v

def dump(path,obj):path.write_text(json.dumps(clean_json(obj),indent=2,ensure_ascii=False))

def build_merged():
    c=pd.read_parquet(W/'cme_minute_panel.parquet')
    keep=[x for x in c if x in ['meeting_date','anchor_ns','anchor_utc','date_et','time_et','instrument','span_exact','digital_contracts_per_spread','dataset_degraded','near_expired','package_definition_admits']
          or x.startswith(('synthetic_','listed_')) and not any(y in x for y in ['_nano','_sz_','_ct_','_source_','_definition_','_status_','_publisher_','_instrument_','_sequence','_depth','_action','_side','_legs_','_leg_mapping','_ts_in_delta'])]
    c=c[keep].copy();c['anchor_ts']=(c.anchor_ns//10**9).astype('int64');c=c.drop(columns='anchor_ns')
    p=pd.read_parquet(W/'poly_event_panel.parquet');k=pd.read_parquet(W/'kalshi_event_panel.parquet')
    for d in [c,p,k]:assert len(d)==117390 and not d.duplicated(KEY).any()
    for d in [p,k]:
        d.rename(columns={x:'p_'+x if d is p else 'k_'+x for x in d if x not in KEY},inplace=True)
    z=c.merge(p,on=KEY,validate='one_to_one').merge(k,on=KEY,validate='one_to_one')
    term=pd.read_parquet(W/'terminal_cash_reference.parquet')[['meeting_date','regime','realized_change_bp','terminal_implied_change_bp','cash_hedge_error_cents']]
    z=z.merge(term,on='meeting_date',validate='many_to_one')
    assert np.allclose(z.span_exact,z.k_span,atol=1e-12)
    kl=pd.read_parquet(W/'kalshi_leg_panel.parquet',columns=KEY+['bid','ask','quote_numeric_valid','quote_age_s'])
    kl['width']=kl.ask-kl.bid
    widths=kl.groupby(KEY,sort=False).width.max().rename('k_max_leg_width').reset_index()
    z=z.merge(widths,on=KEY,how='left',validate='one_to_one')
    # Integer-lot Poly cash convention; rows stay private and are never exported as raw feed.
    pl=pd.read_parquet(W/'poly_leg_panel.parquet',columns=KEY+['token_id','representative_bp','mid_60s','mid_300s'])
    ns=z.groupby('meeting_date').digital_contracts_per_spread.first()
    pl['N']=pl.meeting_date.map(ns)
    pl['q']=np.sign(pl.representative_bp)*np.floor(pl.N*np.abs(pl.representative_bp)/25+.5)
    for age in [60,300]:pl[f'weighted_{age}']=pl.q/pl.N*pl[f'mid_{age}s']
    vals=pl.groupby(KEY).agg(p_lot_value_60=('weighted_60','sum'),p_lot_value_300=('weighted_300','sum')).reset_index()
    weights=pl.drop_duplicates(['meeting_date','token_id']).assign(absweight=lambda d:abs(d.q)/d.N).groupby('meeting_date').absweight.sum()
    z=z.merge(vals,on=KEY,validate='one_to_one');z['p_abs_contract_units']=z.meeting_date.map(weights)
    z['days_to_meeting']=(pd.to_datetime(z.meeting_date)-pd.to_datetime(z.date_et)).dt.days
    for age in [1,60,300]:
        for state in ['strict','carry']:
            for route in ['synthetic','listed']:
                z[f'{route}_ok_{state}_{age}']=z[f'{route}_usable_{state}']&z[f'{route}_age_le_{age}s']
    z['k_rules_ok']=z.k_all_rules_strict_ok.fillna(False)&~z.k_nonexhaustive_on_25bp_grid.fillna(True)&~z.k_overlapping_on_25bp_grid.fillna(True)
    z['p_rules_ok']=z.p_usable_label_convention_60s.fillna(False)
    z['cme_D']=z.synthetic_mid_D_bp.where(z.synthetic_ok_carry_60)
    z['listed_D']=z.listed_mid_D_bp.where(z.listed_ok_carry_60)
    z['poly_D']=z.p_raw_weighted_move_bp_60s.where(z.p_rules_ok)
    z['poly_strict_D']=z.p_raw_weighted_move_bp_60s.where(z.p_usable_strict_ladder_60s)
    z['poly_norm_D']=z.p_normalized_move_bp_60s.where(z.p_rules_ok)
    z['kalshi_D']=z.k_weighted_move_bp_0s.where(z.k_rules_ok&z.k_max_leg_width.le(.10))
    z['kalshi_D_unrestricted_width']=z.k_weighted_move_bp_0s.where(z.k_rules_ok)
    z['poly_hold']=z.p_hold_mid_60s
    z['kalshi_hold']=z.k_hold_mid.where(z.k_hold_quote_age_s.eq(0)&z.k_hold_quote_numeric_valid.fillna(False)&(z.k_hold_ask-z.k_hold_bid).le(.10))
    z.to_parquet(W/'fed_analysis_merged.parquet',index=False)
    return z

def best_cme(z,age=60,state='carry'):
    bid=[];ask=[]
    for route in ['synthetic','listed']:
        bid.append(z[f'{route}_bid_D_bp'].where(z[f'{route}_bid_one_spread_{state}']&z[f'{route}_age_le_{age}s']))
        ask.append(z[f'{route}_ask_D_bp'].where(z[f'{route}_ask_one_spread_{state}']&z[f'{route}_age_le_{age}s']))
    b=pd.concat(bid,axis=1);a=pd.concat(ask,axis=1)
    return b.max(axis=1),a.min(axis=1),np.where(b.iloc[:,0].ge(b.iloc[:,1])|b.iloc[:,1].isna(),'synthetic','listed'),np.where(a.iloc[:,0].le(a.iloc[:,1])|a.iloc[:,1].isna(),'synthetic','listed')

def kalshi_screen(z,kage=0,cage=60,fee='070',other_cost=5.,rules=True,financing=0.):
    bid,ask,br,ar=best_cme(z,cage)
    long=z[f'k_linear_long_value_rounded_lot_dollars_per25_{kage}s'];short=z[f'k_linear_short_proceeds_rounded_lot_dollars_per25_{kage}s']
    lf=z[f'k_long_fee_rate{fee}_cents_per25_{kage}s'] if fee!='000' else 0.
    sf=z[f'k_short_fee_rate{fee}_cents_per25_{kage}s'] if fee!='000' else 0.
    fc=other_cost*100/z.digital_contracts_per_spread
    lc=z[f'k_long_fullcash_rounded_lot_yes_no_cost_dollars_per25_{kage}s'];sc=z[f'k_short_fullcash_rounded_lot_yes_no_cost_dollars_per25_{kage}s']
    finance_long=lc*100*financing*(z.days_to_meeting+1)/365
    finance_short=sc*100*financing*(z.days_to_meeting+1)/365
    grosslong=4*bid-100*long;grossshort=100*short-4*ask
    el=grosslong-lf-fc-finance_long;es=grossshort-sf-fc-finance_short
    if rules:el=el.where(z.k_rules_ok);es=es.where(z.k_rules_ok)
    choose_long=el.ge(es)|es.isna()
    out=z[KEY+['date_et','regime','digital_contracts_per_spread','k_event_ticker','cash_hedge_error_cents','k_any_rules_conflict','k_cancellation_rule','k_cancel_all_no_risk','days_to_meeting']].copy()
    out['edge_cents']=pd.concat([el,es],axis=1).max(axis=1)
    out['direction']=np.where(choose_long,'short_CME_long_K','long_CME_short_K')
    out['route']=np.where(choose_long,br,ar)
    out['gross_edge_cents']=np.where(choose_long,grosslong,grossshort)
    out['kalshi_fee_cents']=np.where(choose_long,lf,sf) if fee!='000' else 0.
    out['futures_other_cost_cents']=fc
    out['kalshi_financing_cents']=np.where(choose_long,finance_long,finance_short)
    out['digital_funded_cash_usd']=np.where(choose_long,lc,sc)*z.digital_contracts_per_spread
    error=100*z.k_linear_proxy_payout_rounded_lot_dollars_per25_ex_post-4*z.terminal_implied_change_bp
    out['ex_post_payoff_correction_cents']=np.where(choose_long,error,-error)
    out['ex_post_model_pnl_cents']=out.edge_cents+out.ex_post_payoff_correction_cents
    out['ex_post_model_pnl_usd']=out.ex_post_model_pnl_cents/100*z.digital_contracts_per_spread
    return out

def poly_screen(z,age=60,cage=60,cost_per_contract_cents=1.,other_cost=5.,strict=False):
    bid,ask,br,ar=best_cme(z,cage)
    value=z[f'p_lot_value_{age}'];gate=z[f'p_usable_strict_ladder_{age}s'] if strict else z[f'p_usable_label_convention_{age}s']
    fc=100*other_cost/z.digital_contracts_per_spread
    el=4*bid-100*value-fc;es=100*value-4*ask-fc
    choose_long=el.ge(es)|es.isna()
    out=z[KEY+['date_et','regime','p_event_id','p_has_open_tails','p_exhaustive_on_25bp_grid','p_abs_contract_units','days_to_meeting']].copy()
    out['edge_before_poly_cost_cents']=pd.concat([el,es],axis=1).max(axis=1).where(gate)
    out['edge_cents']=out.edge_before_poly_cost_cents-cost_per_contract_cents*z.p_abs_contract_units
    out['poly_assumed_cost_cents']=cost_per_contract_cents*z.p_abs_contract_units
    out['direction']=np.where(choose_long,'short_CME_long_P','long_CME_short_P')
    out['route']=np.where(choose_long,br,ar)
    out['break_even_cost_per_digital_cents']=out.edge_before_poly_cost_cents/z.p_abs_contract_units
    # This is a midpoint-cost scenario, never an executable profit measurement.
    return out

def summarize_screen(s,name,**kwargs):
    v=s.edge_cents.dropna();chosen,episodes=persistent_candidates(s)
    return {'name':name,**kwargs,'valid_minutes':len(v),'meetings_with_data':s.loc[v.index,'meeting_date'].nunique(),
            'positive_minutes':int(v.gt(0).sum()),'positive_share':float(v.gt(0).mean()) if len(v) else None,
            'over2_minutes':int(v.ge(2).sum()),'median_cents':float(v.median()) if len(v) else None,
            'p95_cents':float(v.quantile(.95)) if len(v) else None,'max_cents':float(v.max()) if len(v) else None,
            'persistent_episodes':len(episodes),'first_candidates':len(chosen)}

def main():
    assert algebra_checks();z=build_merged()
    correlations=[];per=[];lags=[]
    for x,y,label in [('cme_D','poly_D','CME–Poly'),('cme_D','poly_strict_D','CME–Poly 严格规则'),('cme_D','kalshi_D','CME–Kalshi'),('poly_D','kalshi_D','Poly–Kalshi'),('poly_hold','kalshi_hold','Poly–Kalshi HOLD'),('cme_D','listed_D','CME 两种路径')]:
        r,local=correlation_diagnostics(z,x,y,label);correlations+=r;per.append(local)
        if label in ['CME–Poly','CME–Kalshi','Poly–Kalshi HOLD']:lags+=lag_diagnostics(z,x,y,label)
    pd.DataFrame(correlations).to_csv(O/'correlation_results.csv',index=False)
    pd.concat(per,ignore_index=True).to_csv(O/'correlation_by_meeting.csv',index=False)
    pd.DataFrame(lags).to_csv(O/'lead_lag_results.csv',index=False)
    coverage=[]
    for md,g in z.groupby('meeting_date',sort=True):
        coverage.append({'meeting_date':md,'planned_minutes':len(g),'cme_basic':int(g.synthetic_usable_carry.sum()),'cme_fresh60':int(g.synthetic_ok_carry_60.sum()),
            'poly_alllegs60':int(g.p_all_legs_fresh_60s.sum()),'poly_rules60':int(g.p_rules_ok.sum()),'poly_strict_rules60':int(g.p_usable_strict_ladder_60s.sum()),
            'kalshi_alllegs0':int(g.k_all_legs_mid_valid_0s.fillna(False).sum()),'kalshi_narrow_rules0':int(g.kalshi_D.notna().sum()),
            'cme_poly_common':int((g.cme_D.notna()&g.poly_D.notna()).sum()),'cme_kalshi_common':int((g.cme_D.notna()&g.kalshi_D.notna()).sum()),
            'hold_pair_common':int((g.poly_hold.notna()&g.kalshi_hold.notna()).sum()),'expired_minutes':int(g.near_expired.sum()),
            'regime':g.regime.iloc[0]})
    pd.DataFrame(coverage).to_csv(O/'analysis_coverage_by_meeting.csv',index=False)
    scenarios=[]
    for age,cage in [(0,60),(60,60),(300,300),(0,1)]:
        s=kalshi_screen(z,age,cage);scenarios.append(summarize_screen(s,'Kalshi',kalshi_age=age,cme_age=cage,fee_rate=.07,futures_usd=5,financing=0))
    for fee,cost,fin in [('000',0,0),('035',5,0),('070',0,0),('070',10,0),('070',5,.05),('070',5,.10)]:
        s=kalshi_screen(z,fee=fee,other_cost=cost,financing=fin);scenarios.append(summarize_screen(s,'Kalshi',kalshi_age=0,cme_age=60,fee_rate=int(fee)/1000,futures_usd=cost,financing=fin))
    ks=kalshi_screen(z);ks.to_parquet(W/'kalshi_edge_minutes.parquet',index=False)
    chosen,episodes=persistent_candidates(ks);chosen.to_csv(W/'kalshi_first_candidates.csv',index=False);episodes.to_csv(W/'kalshi_edge_episodes.csv',index=False)
    # Exact previous-minute candle feature availability; same historical ET day.
    old=z[[*KEY,'date_et']+[x for x in z if x.startswith('k_')]].copy();old.anchor_ts+=60
    lagz=z.drop(columns=[x for x in z if x.startswith('k_')]).merge(old,on=KEY+['date_et'],how='left',validate='one_to_one')
    lagz['k_rules_ok']=lagz.k_rules_ok.fillna(False)
    lagks=kalshi_screen(lagz);scenarios.append(summarize_screen(lagks,'Kalshi publication lag 60s',kalshi_age=0,cme_age=60,fee_rate=.07,futures_usd=5,financing=0))
    lagks.to_parquet(W/'kalshi_lag60_edge_minutes.parquet',index=False)
    for cost in [0,.5,1,2]:
        ps=poly_screen(z,cost_per_contract_cents=cost)
        scenarios.append(summarize_screen(ps,'Poly midpoint scenario',poly_age=60,cme_age=60,digital_cost_cents=cost,futures_usd=5,strict_rules=False))
    ps=poly_screen(z);ps.to_parquet(W/'poly_edge_minutes.parquet',index=False)
    for age,cage,strict in [(60,1,False),(300,300,False),(60,60,True)]:
        s=poly_screen(z,age,cage,strict=strict);scenarios.append(summarize_screen(s,'Poly midpoint scenario',poly_age=age,cme_age=cage,digital_cost_cents=1,futures_usd=5,strict_rules=strict))
    pd.DataFrame(scenarios).to_csv(O/'edge_cost_sensitivity.csv',index=False)
    meeting=[]
    for venue,s in [('Kalshi',ks),('Poly scenario',ps)]:
        for md,g in s.groupby('meeting_date'):
            v=g.edge_cents.dropna()
            meeting.append({'venue':venue,'meeting_date':md,'valid_minutes':len(v),'positive_minutes':int(v.gt(0).sum()),'over2_minutes':int(v.ge(2).sum()),
                'p10_cents':v.quantile(.1),'median_cents':v.median(),'p90_cents':v.quantile(.9),'p95_cents':v.quantile(.95),'max_cents':v.max()})
    pd.DataFrame(meeting).to_csv(O/'edge_by_meeting.csv',index=False)
    basis=[]
    for md,g in z.groupby('meeting_date'):
        for venue,col in [('Poly','poly_D'),('Kalshi','kalshi_D')]:
            b=4*(g.cme_D-g[col]);b=b.dropna()
            basis.append({'meeting_date':md,'venue':venue,'n':len(b),'median_basis_cents':b.median(),'p10':b.quantile(.1),'p90':b.quantile(.9),
                'median_abs_basis_cents':b.abs().median()})
    pd.DataFrame(basis).to_csv(O/'basis_by_meeting.csv',index=False)
    # Difference induced by the old probability-normalization convention.
    pair=z[z.cme_D.notna()&z.poly_D.notna()]
    normalization=(4*(pair.poly_norm_D-pair.poly_D)).abs()
    polytail=4*25*z.p_tail_probability_signed_60s.abs()
    summary={'rows':len(z),'meetings':z.meeting_date.nunique(),'coverage':coverage,'correlations':correlations,'scenarios':scenarios,
             'normalization_abs_shift_cents':{'median':normalization.median(),'p90':normalization.quantile(.9),'max':normalization.max()},
             'poly_tail_representative_plus25_abs_shift_cents':{'median':polytail.median(),'p90':polytail.quantile(.9),'max':polytail.max()},
             'kalshi_first_candidates':chosen.to_dict('records'),'scripts':['build_cme_minute_panel.py','build_kalshi_analysis_panel.py','build_poly_analysis_panel.py','build_terminal_cash.py','analyze_fed_edges.py','edge_stats.py'],
             'assumptions':'Exploratory historical diagnostics; no filled positions. Baseline CME age60/carry; K same-minute required-side closes, .07 fee scenario, $5 one-spread other costs. Poly 1c per weighted contract midpoint cost scenario. Missing prediction-market depth, fee vintages, first-release interest-rate vintages and financing.'}
    dump(W/'fed_edge_analysis_summary.json',summary)
    print(pd.DataFrame(correlations)[['pair','mode','n','meetings','r','ci_low','ci_high']].to_string(index=False))
    print(pd.DataFrame(scenarios).to_string(index=False))
    print('FIRST K CANDIDATES',len(chosen));print(chosen[['meeting_date','edge_cents','ex_post_model_pnl_cents','direction']].to_string(index=False))

if __name__=='__main__':main()
