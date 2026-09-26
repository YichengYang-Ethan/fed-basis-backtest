"""Causal local maximum sizing under a frozen finite stress envelope.

No acquisition, orders or parameter search. Margin/VM are cash reservations,
never expenses. The legacy execution ledger is retained with new entry sizing.
"""
from pathlib import Path
from itertools import groupby
import importlib.util
import json, math, hashlib
import numpy as np
import pandas as pd

import sys as _snapshot_sys
_snapshot_sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from snapshot_paths import private_input, raw_input, output_path, expected_input

ROOT=Path(__file__).resolve().parents[2]
OUT = output_path('outputs/capital_efficiency')
PROTO=json.loads((ROOT / 'outputs/capital_efficiency/protocol.json').read_text())
spec=importlib.util.spec_from_file_location('legacy_cash_engine',ROOT/'work/capital100k/backtest.py')
base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)
ET=base.ET

RULE_FIELDS=['rule_lower_bp','rule_upper_bp','lower_inclusive','upper_inclusive',
             'rules_core_ambiguous','rules_wording_inconsistency','bin_kind','rule_parse_source']

def attach_rules(executions):
    # Only rule fields enter risk sizing. Outcome/volume/final-price fields are not read.
    meta=pd.read_parquet(private_input('work/expanded_poly_market_metadata.parquet'),
                        columns=['event_id','yes_token_id','no_token_id']+RULE_FIELDS)
    records=[]
    for r in executions.to_dict('records'):
        col='yes_token_id' if r['actual_buy_side']=='YES' else 'no_token_id'
        z=meta[(meta.event_id==r['event_id'])&(meta[col]==r['actual_token_id'])]
        assert len(z)==1, (r['meeting_date'],r['event_id'],len(z))
        r.update(z[RULE_FIELDS].iloc[0].to_dict());records.append(r)
    return pd.DataFrame(records)

def token_payoff(r,d):
    if r['rules_core_ambiguous'] or r['rules_wording_inconsistency']:
        return 0.0  # Conservative lower bound on the purchased token, including NO.
    lo,hi=r['rule_lower_bp'],r['rule_upper_bp']
    yes=(d>=lo if r['lower_inclusive'] else d>lo) and (d<=hi if r['upper_inclusive'] else d<hi)
    return float(yes if r['actual_buy_side']=='YES' else not yes)

def stress_states(r,policy_bp):
    """Exact endpoints and one-sided limits of a piecewise linear payoff.

    Evaluate futures at the boundary, but payout on its adjacent open interval.
    Thus no epsilon-dependent underestimate of a discontinuity's loss supremum.
    """
    bounds={-float(policy_bp),float(policy_bp),0.}
    bounds.update(float(x) for x in np.arange(-policy_bp,policy_bp+1,25))
    bounds.update(float(r[k]) for k in ['rule_lower_bp','rule_upper_bp'] if np.isfinite(r[k]) and abs(r[k])<=policy_bp)
    result=[]
    for d in sorted(bounds):
        result.append((d,token_payoff(r,d),'exact'))
        if d>-policy_bp:result.append((d,token_payoff(r,np.nextafter(d,-np.inf)),'left_limit'))
        if d<policy_bp:result.append((d,token_payoff(r,np.nextafter(d,np.inf)),'right_limit'))
    return result

def package_terms(r,policy_bp=100,extra_cents=0):
    q=int(r['quantity']);p=float(r['digital_price'])
    principal=q*p;fee=.05*q*p*(1-p);execution=(.02+.01*extra_cents)*q
    cost=principal+fee+execution+5.
    states=[]
    for d,y,side in stress_states(r,policy_bp):
        for eps in [-PROTO['native_spread_residual_bp'],PROTO['native_spread_residual_bp']]:
            fut=r['cme_direction_sign']*41.67*(r['entry_spread']*100-r['span']*d-eps)
            states.append(dict(decision_bp=d,limit=side,residual_native_bp=eps,
                               token_payout=y,futures_pnl_usd=fut,total_pnl_usd=fut+q*y-cost))
    worst=min(states,key=lambda s:s['total_pnl_usd'])
    fut_debit=max(0.,-min(s['futures_pnl_usd'] for s in states))
    vm=fut_debit+41.67*PROTO['additional_far_leg_temporary_shock_bp']
    return dict(cost=cost,principal=principal,fee=fee,execution=execution,
                risk=max(0.,-worst['total_pnl_usd']),vm=vm,
                terminal_futures_debit=fut_debit,worst=worst,states=states)

def remaining_vm(v):
    return 0. if all(v['closed'].values()) else max(0.,v['n']*v['unit_vm']+v['cme_pnl'])

def obligations(positions):
    margin=sum(v['reserve'] for v in positions.values() if not all(v['closed'].values()))
    vm=sum(remaining_vm(v) for v in positions.values())
    risk=sum(v['n']*v['unit_risk'] for v in positions.values() if v['status']!='CLOSED')
    return margin,vm,risk

def update_stress_breaches(positions):
    for v in positions.values():
        if not all(v['closed'].values()) and v['cme_pnl'] < -v['n']*v['unit_vm']-1e-7:
            v['stress_breached']=True

def floor_nonnegative(a,b):
    if a < -1e-8:return 0
    return max(0,math.floor((a+1e-8)/b)) if b>0 else 10**12

def size_terms(unit,cash,margin,vm,risk,risk_budget,depth,blocked=False):
    per_cash=unit['cost']+PROTO['margin_reserve_usd_per_package']+unit['vm']
    bounds={'cash':floor_nonnegative(cash-margin-vm,per_cash),
            'risk':floor_nonnegative(risk_budget-risk,unit['risk']),
            'depth':max(0,int(depth))}
    n=0 if blocked else min(bounds.values())
    slacks={'cash_slack_usd':cash-margin-vm-n*per_cash,
            'risk_slack_usd':risk_budget-risk-n*unit['risk'],'depth_slack_packages':int(depth)-n}
    nextfails=[]
    if cash-margin-vm-(n+1)*per_cash < -1e-7:nextfails.append('CASH')
    if risk_budget-risk-(n+1)*unit['risk'] < -1e-7:nextfails.append('RISK')
    if n+1>depth:nextfails.append('CME_DEPTH')
    if blocked:nextfails.append('PRIOR_STRESS_ENVELOPE_BREACH')
    assert nextfails, (bounds,n)
    if n:assert min(slacks.values())>=-1e-7
    return n,dict(max_cash_packages=bounds['cash'],max_risk_packages=bounds['risk'],
                  max_depth_packages=bounds['depth'],next_package_rejected_by='|'.join(nextfails),**slacks)

def run_account(executions,marks,terminal,winners,risk_budget=20000,policy_bp=100,extra_cents=0,cutoff=base.CUTOFF):
    initial=PROTO['initial_cash_usd'];cash=float(initial);positions={};history=[];skips=[];certificates=[];stress_rows=[]
    events=[]
    for r in executions.to_dict('records'):
        events.extend([(r['execution_ts'],3,'entry',r),(base.payout_clock(r['meeting_date']),1,'payout',r['meeting_date'])])
    for r in marks.itertuples():
        if r.trade_date.to_period('M')<=pd.Period(r.dm):events.append((r.available_ts,0,'mark',r))
    for month,price in terminal.items():events.append((base.release_clock(month),2,'final',(month,price)))
    events.sort(key=lambda e:(e[0],e[1]))
    def record(t,kind,delta,md=''):
        margin,vm,risk=obligations(positions)
        history.append(dict(timestamp=t,time_et=pd.to_datetime(t,unit='s',utc=True).tz_convert(ET).isoformat(),
            event=kind,meeting_date=md,cash_change_usd=delta,cash_usd=cash,margin_reserve_usd=margin,
            remaining_VM_reserve_usd=vm,stress_risk_reserved_usd=risk,
            free_cash_after_margin_usd=cash-margin,uncommitted_cash_usd=cash-margin-vm,
            open_digital_cost_usd=sum(v['principal'] for v in positions.values() if not v['paid'])))
    for t,group in groupby(events,key=lambda e:e[0]):
        if t>cutoff:break
        before=cash
        for _,_,kind,r in list(group):
            if kind=='entry':
                md=r['meeting_date']
                if r['execution_status']!='READY':
                    skips.append(dict(meeting_date=md,status=r['execution_status'],execution_ts=t));continue
                # All same-timestamp marks precede entry. Update sticky flags now,
                # so a newly breached path cannot fund an entry in this batch.
                update_stress_breaches(positions)
                u=package_terms(r,policy_bp,extra_cents);margin,vm,risk=obligations(positions)
                blocked=any(v.get('stress_breached',False) and not all(v['closed'].values()) for v in positions.values())
                n,cert=size_terms(u,cash,margin,vm,risk,risk_budget,r['cme_depth'],blocked)
                cert.update(meeting_date=md,execution_ts=t,packages=n,cash_before_usd=cash,
                    existing_margin_usd=margin,existing_VM_usd=vm,existing_risk_usd=risk,
                    unit_cash_usd=u['cost'],unit_margin_usd=PROTO['margin_reserve_usd_per_package'],
                    unit_VM_usd=u['vm'],unit_risk_usd=u['risk'],risk_budget_usd=risk_budget,
                    policy_stress_bp=policy_bp,worst_decision_bp=u['worst']['decision_bp'])
                certificates.append(cert)
                stress_rows.extend(dict(meeting_date=md,**s) for s in u['states'])
                if n==0:
                    skips.append(dict(meeting_date=md,status='NO_FILL_RISK_OR_CASH',execution_ts=t));continue
                cost=n*u['cost'];cash-=cost
                v=dict(r);v.update(n=n,total_Q=n*int(r['quantity']),cash_outlay=cost,principal=n*u['principal'],
                    fees=n*u['fee'],execution_cost=n*u['execution'],reserve=n*PROTO['margin_reserve_usd_per_package'],
                    unit_risk=u['risk'],unit_vm=u['vm'],unit_cost=u['cost'],cme_pnl=0.,payout=0.,paid=False,
                    marks={'near':r['initial_near_mark'],'far':r['initial_far_mark']},
                    mark_dates={'near':None,'far':None},closed={'near':False,'far':False},
                    status='PENDING',prelim_mark_count=0,stale_revision_skips=0,stress_breached=False)
                positions[md]=v;record(t,'ENTRY',-cost,md)
            elif kind=='mark':
                for md,v in positions.items():
                    if t<=v['execution_ts']:continue
                    for role in ['near','far']:
                        if r.dm!=v['leg_'+role] or v['closed'][role]:continue
                        if r.trade_date.date()<pd.to_datetime(v['execution_ts'],unit='s',utc=True).tz_convert(ET).date():continue
                        last=v['mark_dates'][role]
                        if last is not None and r.trade_date<last:
                            v['stale_revision_skips']+=1;continue
                        direction=-v['cme_direction_sign'] if role=='near' else v['cme_direction_sign']
                        delta=v['n']*direction*4167*(r.price-v['marks'][role])
                        v['marks'][role]=r.price;v['mark_dates'][role]=r.trade_date
                        v['prelim_mark_count']+=int((int(r.stat_flags)&1)==0)
                        v['cme_pnl']+=delta;cash+=delta;record(t,'VM_'+role.upper(),delta,md)
            elif kind=='payout':
                if r not in positions:continue
                v=positions[r];y=winners.get(r,np.nan)
                if not np.isfinite(y):continue
                assert y in [0,1] and not v['paid']
                delta=v['total_Q']*y;cash+=delta;v.update(payout=delta,paid=True)
                record(t,'DIGITAL_PAYOUT',delta,r)
            elif kind=='final':
                month,price=r
                for md,v in positions.items():
                    for role in ['near','far']:
                        if month!=v['leg_'+role] or v['closed'][role]:continue
                        direction=-v['cme_direction_sign'] if role=='near' else v['cme_direction_sign']
                        delta=v['n']*direction*4167*(price-v['marks'][role]);cash+=delta
                        v['cme_pnl']+=delta;v['marks'][role]=price;v['closed'][role]=True
                        record(t,'FINAL_'+role.upper(),delta,md)
            for v in positions.values():
                if v['paid'] and all(v['closed'].values()) and v['status']!='CLOSED':
                    v.update(status='CLOSED',completion_ts=t)
        if positions:
            # Net the same clearing timestamp before checking stress or cash.
            update_stress_breaches(positions)
            record(t,'BATCH_CLOSE',0.)
            history[-1]['batch_net_cash_change_usd']=cash-before
    records=[]
    for md,v in positions.items():
        done=v['status']=='CLOSED';pnl=v['cme_pnl']+v['payout']-v['cash_outlay'] if done else np.nan
        if done:
            direct=v['n']*v['cme_direction_sign']*4167*(v['entry_spread']-(terminal[v['leg_near']]-terminal[v['leg_far']]))+v['payout']-v['cash_outlay']
            assert abs(pnl-direct)<1e-7
        allocated=v['cash_outlay']+v['reserve']+v['n']*v['unit_vm']
        records.append(dict(meeting_date=md,execution_et=v['execution_et'],execution_ts=v['execution_ts'],
            near_symbol=v['near_symbol'],far_symbol=v['far_symbol'],cme_route=v['cme_route'],direction_sign=v['cme_direction_sign'],
            actual_outcome=v['actual_outcome'],actual_buy_side=v['actual_buy_side'],packages=v['n'],digital_quantity=v['total_Q'],
            status=v['status'],cash_outlay_usd=v['cash_outlay'],margin_reserve_usd=v['reserve'],
            initial_VM_reserve_usd=v['n']*v['unit_vm'],initial_capital_committed_usd=allocated,
            terminal_stress_loss_usd=v['n']*v['unit_risk'],model_profit_usd=pnl,
            account_contribution_pct=100*pnl/initial,committed_capital_return_pct=100*pnl/allocated,
            cash_plus_margin_return_pct=100*pnl/(v['cash_outlay']+v['reserve']),
            nonmargin_cash_return_pct=100*pnl/v['cash_outlay'],
            observed_CME_capacity_packages=v['cme_depth'],CME_cash_to_cutoff_usd=v['cme_pnl'],digital_payout_to_cutoff_usd=v['payout'],
            stress_envelope_breached=v['stress_breached'],prelim_mark_count=v['prelim_mark_count'],
            stale_revision_skips=v['stale_revision_skips'],completion_et=pd.to_datetime(v['completion_ts'],unit='s',utc=True).tz_convert(ET).isoformat() if done else None))
    tr=pd.DataFrame(records);hi=pd.DataFrame(history);sk=pd.DataFrame(skips);ce=pd.DataFrame(certificates)
    c=tr[tr.status.eq('CLOSED')] if len(tr) else tr
    bh=hi[hi.event.eq('BATCH_CLOSE')] if len(hi) else hi
    margin,vm,risk=obligations(positions)
    sm=dict(risk_budget_usd=risk_budget,policy_envelope_bp=policy_bp,extra_execution_cents=extra_cents,
        closed_positions=len(c),pending_positions=int(tr.status.eq('PENDING').sum()) if len(tr) else 0,
        data_skips=int(sk.status.str.startswith('NO_FILL_DATA').sum()) if len(sk) else 0,
        funding_skips=int(sk.status.eq('NO_FILL_RISK_OR_CASH').sum()) if len(sk) else 0,
        median_account_contribution_pct=c.account_contribution_pct.median() if len(c) else None,
        median_committed_capital_return_pct=c.committed_capital_return_pct.median() if len(c) else None,
        median_cash_plus_margin_return_pct=c.cash_plus_margin_return_pct.median() if len(c) else None,
        median_nonmargin_cash_return_pct=c.nonmargin_cash_return_pct.median() if len(c) else None,
        median_profit_usd=c.model_profit_usd.median() if len(c) else None,
        sum_closed_profit_usd=c.model_profit_usd.sum() if len(c) else 0.,closed_positive_count=int(c.model_profit_usd.gt(0).sum()) if len(c) else 0,
        min_free_cash_after_margin_usd=min(initial,bh.free_cash_after_margin_usd.min()) if len(bh) else initial,
        min_uncommitted_cash_usd=min(initial,bh.uncommitted_cash_usd.min()) if len(bh) else initial,
        peak_terminal_stress_risk_usd=bh.stress_risk_reserved_usd.max() if len(bh) else 0.,
        negative_free_cash_batches=int(bh.free_cash_after_margin_usd.lt(-1e-7).sum()) if len(bh) else 0,
        negative_uncommitted_cash_batches=int(bh.uncommitted_cash_usd.lt(-1e-7).sum()) if len(bh) else 0,
        stress_envelope_breach_positions=int(tr.stress_envelope_breached.sum()) if len(tr) else 0,
        end_cash_usd=cash,end_margin_reserve_usd=margin,end_VM_reserve_usd=vm,end_uncommitted_cash_usd=cash-margin-vm,
        verified_fill_count=0,verified_fill_median=None,
        complete_account_NAV=cash if all(v['status']=='CLOSED' for v in positions.values()) else None,true_OOS=False,
        cash_conservation_error_usd=cash-initial-sum(v['cme_pnl']+v['payout']-v['cash_outlay'] for v in positions.values()))
    assert abs(sm['cash_conservation_error_usd'])<1e-6
    assert sm['peak_terminal_stress_risk_usd']<=risk_budget+1e-7
    return tr,hi,sk,ce,pd.DataFrame(stress_rows),sm

def load_inputs():
    e=attach_rules(pd.read_parquet(private_input('work/capital100k/executions.parquet')))
    m=pd.read_parquet(private_input('work/capital100k/marks_prepared.parquet'))
    z=json.loads((private_input('work/capital100k/evaluation.json')).read_text())
    return e,m,z['terminal'],z['winners']

def main():
    for name,digest in PROTO['input_sha256'].items():
        assert hashlib.sha256(expected_input(name).read_bytes()).hexdigest()==digest,name
    e,m,t,w=load_inputs();summaries=[]
    for config in PROTO['scenarios']:
        name=config['name'];tr,hi,sk,ce,st,sm=run_account(e,m,t,w,**{k:v for k,v in config.items() if k!='name'})
        for label,frame in [('trades',tr),('cash_events',hi),('skips',sk),('sizing_certificates',ce),('stress_payoffs',st)]:
            frame.to_csv(OUT/f'{name}_{label}.csv',index=False)
        summaries.append(dict(scenario=name,**sm))
    pd.DataFrame(summaries).to_csv(OUT/'summary.csv',index=False)
    print(pd.DataFrame(summaries).to_string(index=False))

if __name__=='__main__':main()
