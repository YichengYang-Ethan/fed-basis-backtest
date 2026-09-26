"""Isolated replay adapter: original ledger with a hypothetical margin function.
Original production research files are unchanged. See protocol.json and diff.
+"""
"""Portfolio cash allocation using initial-equity cushion and synchronized VM paths.
Entry objective uses only the two-main-state contemporaneous quoted-payoff floor.
No account-level expected-return or all-state solvency guarantee is asserted.
"""
from pathlib import Path
from itertools import groupby
from collections import defaultdict
import importlib.util, json, hashlib
import pandas as pd
import numpy as np

import sys as _snapshot_sys
_snapshot_sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from snapshot_paths import private_input, raw_input, output_path, expected_input

ROOT=Path(__file__).resolve().parents[2];OUT = output_path('outputs/spread_credit_backtest')
PROTO=json.loads((ROOT/'outputs/portfolio_capital/protocol.json').read_text())
spec=importlib.util.spec_from_file_location('parent_efficiency',ROOT/'work/capital_efficiency/backtest.py')
parent=importlib.util.module_from_spec(spec);spec.loader.exec_module(parent)
base=parent.base;ET=base.ET
spec2=importlib.util.spec_from_file_location('joint_cash_risk_model',Path(__file__).with_name('risk_model.py'))
model_module=importlib.util.module_from_spec(spec2);spec2.loader.exec_module(model_module)
PortfolioModel=model_module.PortfolioModel;InsufficientHistory=model_module.InsufficientHistory

def estimate_buffers(executions,history):
    output=[]
    for r in executions.to_dict('records'):
        if r['execution_status']!='READY':output.append(r);continue
        entryday=pd.to_datetime(r['execution_ts'],unit='s',utc=True).tz_convert(ET).tz_localize(None).normalize()
        h=history[history.dm.isin([r['leg_near'],r['leg_far']]) &
                  history.available_ts.lt(r['execution_ts']) &
                  history.ts_recv.lt(pd.to_datetime(r['execution_ts'],unit='s',utc=True)) & history.trade_date.lt(entryday)]
        z=h.pivot(index='trade_date',columns='dm',values='price').reindex(
            columns=[r['leg_near'],r['leg_far']]).dropna().sort_index().tail(60)
        r['history_observations']=len(z)
        if len(z)<60:
            r['execution_status']='NO_FILL_INSUFFICIENT_VM_HISTORY';output.append(r);continue
        near=z[r['leg_near']].to_numpy();far=z[r['leg_far']].to_numpy();sigma=r['cme_direction_sign']
        pair_deficit=0.;far_deficit=0.
        for lag in range(1,6):
            pair=4167*sigma*(-(near[lag:]-near[:-lag])+(far[lag:]-far[:-lag]))
            alone=4167*sigma*(far[lag:]-far[:-lag])
            pair_deficit=max(pair_deficit,float(-pair.min()));far_deficit=max(far_deficit,float(-alone.min()))
        dates=z.index;known=h[h.trade_date.isin(dates)]
        r.update(empirical_VM=max(pair_deficit,far_deficit),pair_VM=pair_deficit,far_VM=far_deficit,
                 history_start=str(dates.min().date()),history_end=str(dates.max().date()),
                 max_calendar_gap_days=int(dates.to_series().diff().dt.days.max()),
                 max_5interval_calendar_days=int((dates[5:]-dates[:-5]).days.max()),
                 history_latest_available_ts=float(known.available_ts.max()),
                 history_latest_receipt_ts=float(known.ts_recv.astype('int64').max()/1e9))
        output.append(r)
    return pd.DataFrame(output)

def cost_terms(r,extra_cents=0):
    q=int(r['quantity']);p=float(r['digital_price'])
    principal=q*p;fee=.05*q*p*(1-p);execution=(.02+.01*extra_cents)*q
    return dict(cost=principal+fee+execution+5,principal=principal,fee=fee,execution=execution)

def exposure(positions):
    by_month=defaultdict(int)
    for v in positions.values():
        for role,sign in [('near',-v['cme_direction_sign']),('far',v['cme_direction_sign'])]:
            if not v['closed'][role]:by_month[v['leg_'+role]]+=int(v['n']*sign)
    return dict(by_month)

def obligations(positions,margin_per_leg,oracle):
    net=exposure(positions)
    margin=oracle.margin(net,level="initial")
    vm=sum(v['n']*v['unit_vm'] for v in positions.values() if not all(v['closed'].values()))
    gross=margin_per_leg*sum(v['n']*sum(not closed for closed in v['closed'].values()) for v in positions.values())
    return margin,vm,gross

def live_months(positions):
    return tuple(sorted({v['leg_'+role] for v in positions.values() for role in ['near','far'] if not v['closed'][role]}))

def quoted_core_floor(r,extra_cents=0):
    cost=cost_terms(r,extra_cents)['cost'];q=int(r['quantity'])
    return min(r['cme_direction_sign']*41.67*(r['entry_spread']*100-r['span']*d)+q*parent.token_payoff(r,d)-cost
               for d in [r['d0_bp'],r['d1_bp']])

def required_book(positions,now,method,oracle,margin_per_leg,maintenance_per_leg):
    M,B,gross=obligations(positions,margin_per_leg,oracle)
    if method=='baseline':return M+B
    if method=='cushion':return max(M,M*maintenance_per_leg/margin_per_leg+B)
    req,*_=oracle.requirements(exposure(positions),now,universe_months=live_months(positions))
    return float(req[0])

def size_packages(r,positions,cash,margin_per_leg,maintenance_per_leg,method,oracle,extra_cents=0,blocked=False):
    u=cost_terms(r,extra_cents);B=r['empirical_VM'];edge=quoted_core_floor(r,extra_cents)
    oldM,oldB,gross=obligations(positions,margin_per_leg,oracle);net=exposure(positions)
    ngrid=np.arange(int(r['cme_depth'])+1,dtype=np.int64)
    near=r['leg_near'];far=r['leg_far'];sigma=int(r['cme_direction_sign'])
    unchanged=sum(abs(v) for k,v in net.items() if k not in [near,far])
    newM=margin_per_leg*(unchanged+np.abs(net.get(near,0)-sigma*ngrid)+np.abs(net.get(far,0)+sigma*ngrid))
    if method=='baseline':required=newM+oldB+B*ngrid;info={}
    elif method=='cushion':required=np.maximum(newM,newM*maintenance_per_leg/margin_per_leg+oldB+B*ngrid);info={}
    else:
        required,newM,newK,info=oracle.requirements(net,r['execution_ts'],r,ngrid,live_months(positions))
    before_required=required_book(positions,r['execution_ts'],method,oracle,margin_per_leg,maintenance_per_leg)
    slack=cash-u['cost']*ngrid-required
    feasible=np.flatnonzero(slack>=-1e-8)
    n=int(feasible[-1]) if len(feasible) and not blocked and edge>0 else 0
    reason='PRIOR_MARGIN_SHORTFALL' if blocked else ('NONPOSITIVE_QUOTED_CORE_EDGE' if edge<=0 else ('CME_DEPTH' if n==int(r['cme_depth']) else 'CASH_MARGIN_VM'))
    if not blocked and edge>0:assert not (slack[n+1:]>=-1e-8).any()
    if n:assert slack[n]>=-1e-8
    if method=='baseline':standalone_required=n*(2*margin_per_leg+B)
    elif method=='cushion':standalone_required=n*max(2*margin_per_leg,2*maintenance_per_leg+B)
    else:
        alone,*_=oracle.requirements({},r['execution_ts'],r,[n])
        standalone_required=float(alone[0])
    cert=dict(meeting_date=r['meeting_date'],execution_ts=r['execution_ts'],packages=n,
        cme_capacity_packages=int(r['cme_depth']),cash_before_usd=cash,margin_before_usd=oldM,
        existing_VM_usd=oldB,net_margin_after_usd=float(newM[n]),incremental_net_margin_usd=float(newM[n]-oldM),
        unit_cash_usd=u['cost'],unit_VM_usd=B,unit_margin_leg_usd=margin_per_leg,
        cash_slack_usd=float(slack[n]),next_package_cash_slack_usd=float(slack[n+1]) if n<int(r['cme_depth']) else None,
        higher_feasible_quantities=int((slack[n+1:]>=-1e-8).sum()) if not blocked else None,
        unit_quoted_core_floor_usd=edge,selected_quoted_core_floor_usd=n*edge,
        required_cash_before_usd=before_required,required_cash_after_usd=float(required[n]),
        incremental_account_commitment_usd=n*u['cost']+float(required[n])-before_required,
        standalone_required_cash_usd=standalone_required,
        extra_cash_beyond_current_initial_usd=float(required[n]-newM[n]),method=method,
        rejection_reason=reason,net_contracts_before_json=json.dumps(net,sort_keys=True),
        history_observations=r['history_observations'],history_start=r['history_start'],history_end=r['history_end'],
        pair_VM_usd=r['pair_VM'],far_VM_usd=r['far_VM'],max_calendar_gap_days=r['max_calendar_gap_days'],
        max_5interval_calendar_days=r['max_5interval_calendar_days'],
        history_latest_available_ts=r['history_latest_available_ts'],joint_history_info_json=json.dumps(info))
    return n,u,B,cert

def run_account(executions,marks,terminal,winners,history,method='portfolio',margin_per_leg=1100,maintenance_per_leg=950,extra_cents=0,cutoff=base.CUTOFF,pair_initial=2200,pair_maintenance=1900,scenario_source=None):
    assert 0<maintenance_per_leg<=margin_per_leg
    initial=PROTO['initial_cash_usd'];cash=float(initial);positions={};hist=[];certs=[];skips=[];shortfalls=[];invalid=False
    oracle=PortfolioModel(history,margin_per_leg,maintenance_per_leg,pair_initial,pair_maintenance,scenario_source)
    events=[]
    for r in executions.to_dict('records'):
        events.extend([(r['execution_ts'],3,'entry',r),(base.payout_clock(r['meeting_date']),1,'payout',r['meeting_date'])])
    for r in marks.itertuples():
        if r.trade_date.to_period('M')<=pd.Period(r.dm):events.append((r.available_ts,0,'mark',r))
    for m,p in terminal.items():events.append((base.release_clock(m),2,'final',(m,p)))
    events.sort(key=lambda x:(x[0],x[1]))
    def check_margin(t):
        nonlocal invalid
        M,B,gross=obligations(positions,margin_per_leg,oracle)
        maintenance=oracle.margin(exposure(positions),level="maintenance")
        if cash<maintenance-1e-7:
            invalid=True
            if not shortfalls or shortfalls[-1]['timestamp']!=t:
                shortfalls.append(dict(timestamp=t,cash_usd=cash,required_maintenance_usd=maintenance,shortfall_usd=maintenance-cash))
    def record(t,kind,delta,md=''):
        M,B,gross=obligations(positions,margin_per_leg,oracle)
        try:required=required_book(positions,t,method,oracle,margin_per_leg,maintenance_per_leg) if kind in ['ENTRY','BATCH_CLOSE'] else np.nan
        except InsufficientHistory:required=np.nan
        hist.append(dict(timestamp=t,time_et=pd.to_datetime(t,unit='s',utc=True).tz_convert(ET).isoformat(),
            event=kind,meeting_date=md,cash_change_usd=delta,cash_usd=cash,net_margin_usd=M,
            gross_margin_without_netting_usd=gross,margin_excess_cash_usd=cash-M,
            maintenance_margin_usd=oracle.margin(exposure(positions),level="maintenance"),
            maintenance_excess_cash_usd=cash-oracle.margin(exposure(positions),level="maintenance"),
            legacy_gross_VM_usd=B,required_cash_usd=required,
            extra_liquidity_above_initial_usd=max(0.,required-M) if np.isfinite(required) else np.nan,
            uncommitted_cash_usd=cash-required,financing_path_invalid=invalid))
    for t,g in groupby(events,key=lambda e:e[0]):
        if t>cutoff:break
        startcash=cash
        for _,_,kind,r in list(g):
            if kind=='entry':
                check_margin(t)
                md=r['meeting_date']
                if r['execution_status']!='READY':
                    skips.append(dict(meeting_date=md,status=r['execution_status'],execution_ts=t));continue
                try:n,u,B,cert=size_packages(r,positions,cash,margin_per_leg,maintenance_per_leg,method,oracle,extra_cents,invalid)
                except InsufficientHistory:
                    skips.append(dict(meeting_date=md,status='NO_FILL_INSUFFICIENT_PORTFOLIO_HISTORY',execution_ts=t));continue
                certs.append(cert)
                if not n:
                    skips.append(dict(meeting_date=md,status='NO_FILL_'+cert['rejection_reason'],execution_ts=t));continue
                cost=n*u['cost'];cash-=cost
                v=dict(r);v.update(n=n,total_Q=n*int(r['quantity']),cash_outlay=cost,principal=n*u['principal'],
                    unit_vm=B,cme_pnl=0.,payout=0.,paid=False,closed={'near':False,'far':False},status='PENDING',
                    marks={'near':r['initial_near_mark'],'far':r['initial_far_mark']},mark_dates={'near':None,'far':None},
                    marginal_commitment=cert['incremental_account_commitment_usd'],
                    standalone_commitment=cost+cert['standalone_required_cash_usd'],
                    legacy_standalone_commitment=cost+2*n*margin_per_leg+n*B,
                    entry_extra_liquidity=cert['extra_cash_beyond_current_initial_usd'],
                    incremental_margin=cert['incremental_net_margin_usd'])
                positions[md]=v;record(t,'ENTRY',-cost,md)
            elif kind=='mark':
                for md,v in positions.items():
                    if t<=v['execution_ts']:continue
                    for role in ['near','far']:
                        if r.dm!=v['leg_'+role] or v['closed'][role]:continue
                        if r.trade_date.date()<pd.to_datetime(v['execution_ts'],unit='s',utc=True).tz_convert(ET).date():continue
                        last=v['mark_dates'][role]
                        if last is not None and r.trade_date<last:continue
                        direction=-v['cme_direction_sign'] if role=='near' else v['cme_direction_sign']
                        delta=v['n']*direction*4167*(r.price-v['marks'][role]);cash+=delta;v['cme_pnl']+=delta
                        v['marks'][role]=r.price;v['mark_dates'][role]=r.trade_date;record(t,'VM_'+role.upper(),delta,md)
            elif kind=='payout':
                if r not in positions:continue
                v=positions[r];y=winners.get(r,np.nan)
                if not np.isfinite(y):continue
                assert y in [0,1] and not v['paid']
                delta=v['total_Q']*y;cash+=delta;v.update(payout=delta,paid=True);record(t,'PM_PAYOUT',delta,r)
            elif kind=='final':
                month,price=r
                for md,v in positions.items():
                    for role in ['near','far']:
                        if month!=v['leg_'+role] or v['closed'][role]:continue
                        direction=-v['cme_direction_sign'] if role=='near' else v['cme_direction_sign']
                        delta=v['n']*direction*4167*(price-v['marks'][role]);cash+=delta;v['cme_pnl']+=delta
                        v['marks'][role]=price;v['closed'][role]=True;record(t,'FINAL_'+role.upper(),delta,md)
            for v in positions.values():
                if v['paid'] and all(v['closed'].values()) and v['status']!='CLOSED':v.update(status='CLOSED',completion_ts=t)
        if positions:
            check_margin(t);record(t,'BATCH_CLOSE',0.)
            hist[-1]['batch_net_cash_change_usd']=cash-startcash
    rows=[]
    for md,v in positions.items():
        closed=v['status']=='CLOSED';profit=v['cme_pnl']+v['payout']-v['cash_outlay'] if closed else np.nan
        if closed:
            direct=v['n']*v['cme_direction_sign']*4167*(v['entry_spread']-terminal[v['leg_near']]+terminal[v['leg_far']])+v['payout']-v['cash_outlay']
            assert abs(profit-direct)<1e-6
        rows.append(dict(meeting_date=md,execution_et=v['execution_et'],execution_ts=v['execution_ts'],
            status=v['status'],packages=v['n'],digital_quantity=v['total_Q'],near_symbol=v['near_symbol'],far_symbol=v['far_symbol'],
            actual_outcome=v['actual_outcome'],actual_buy_side=v['actual_buy_side'],cash_outlay_usd=v['cash_outlay'],
            legacy_unit_empirical_VM_usd=v['unit_vm'],legacy_entry_VM_reserve_usd=v['unit_vm']*v['n'],
            entry_extra_liquidity_above_initial_usd=v['entry_extra_liquidity'],
            standalone_committed_usd=v['standalone_commitment'],marginal_portfolio_committed_usd=v['marginal_commitment'],
            legacy_standalone_committed_usd=v['legacy_standalone_commitment'],
            incremental_net_margin_usd=v['incremental_margin'],shadow_hold_profit_usd=profit,
            financed_profit_usd=profit if not invalid else np.nan,
            account_contribution_pct=100*profit/initial if not invalid else np.nan,
            shadow_account_contribution_pct=100*profit/initial,
            standalone_return_pct=100*profit/v['standalone_commitment'] if not invalid else np.nan,
            marginal_commitment_return_pct=100*profit/v['marginal_commitment'] if not invalid and v['marginal_commitment']>0 else np.nan,
            nonmargin_cash_return_pct=100*profit/v['cash_outlay'] if not invalid else np.nan,
            CME_cash_to_cutoff_usd=v['cme_pnl'],PM_payout_to_cutoff_usd=v['payout'],
            completion_et=pd.to_datetime(v['completion_ts'],unit='s',utc=True).tz_convert(ET).isoformat() if closed else None))
    tr=pd.DataFrame(rows);hi=pd.DataFrame(hist);ce=pd.DataFrame(certs)
    sk=pd.DataFrame(skips,columns=['meeting_date','status','execution_ts'])
    sf=pd.DataFrame(shortfalls,columns=['timestamp','cash_usd','required_maintenance_usd','shortfall_usd'])
    c=tr[tr.status.eq('CLOSED')] if len(tr) else tr
    batches=hi[hi.event.eq('BATCH_CLOSE')] if len(hi) else hi
    M,B,gross=obligations(positions,margin_per_leg,oracle)
    endR=required_book(positions,min(cutoff,t),method,oracle,margin_per_leg,maintenance_per_leg)
    sm=dict(initial_cash_usd=initial,method=method,margin_per_leg_usd=margin_per_leg,maintenance_per_leg_usd=maintenance_per_leg,extra_cents=extra_cents,pair_initial_usd=pair_initial,pair_maintenance_usd=pair_maintenance,
        financing_path_valid=not invalid,closed_positions=len(c),pending_positions=int(tr.status.eq('PENDING').sum()) if len(tr) else 0,
        skips=len(sk),margin_shortfall_batches=len(sf),min_margin_excess_cash_usd=batches.margin_excess_cash_usd.min() if len(hi) else initial,
        min_maintenance_excess_cash_usd=batches.maintenance_excess_cash_usd.min() if len(hi) else initial,
        initial_margin_reserve_gap_batches=int(batches.margin_excess_cash_usd.lt(-1e-7).sum()) if len(hi) else 0,
        min_uncommitted_cash_usd=batches.uncommitted_cash_usd.min() if len(hi) else initial,
        short_window_reserve_gap_batches=int(batches.uncommitted_cash_usd.lt(-1e-7).sum()) if len(hi) else 0,
        median_profit_usd=c.financed_profit_usd.median() if len(c) and not invalid else None,
        median_account_contribution_pct=c.account_contribution_pct.median() if len(c) and not invalid else None,
        median_standalone_return_pct=c.standalone_return_pct.median() if len(c) and not invalid else None,
        median_marginal_commitment_return_pct=c.marginal_commitment_return_pct.median() if len(c) and not invalid and c.marginal_commitment_return_pct.notna().any() else None,
        median_nonmargin_cash_return_pct=c.nonmargin_cash_return_pct.median() if len(c) and not invalid else None,
        sum_closed_profit_usd=c.financed_profit_usd.sum() if len(c) and not invalid else None,
        shadow_sum_closed_profit_usd=c.shadow_hold_profit_usd.sum() if len(c) else 0.,
        positive_closed=int(c.shadow_hold_profit_usd.gt(0).sum()) if len(c) else 0,
        end_cash_usd=cash,end_margin_usd=M,end_required_cash_usd=endR,end_legacy_gross_VM_usd=B,end_uncommitted_cash_usd=cash-endR,
        missing_joint_risk_history_batches=int(batches.required_cash_usd.isna().sum()) if len(hi) else 0,
        complete_financed_NAV=cash if not invalid and all(v['status']=='CLOSED' for v in positions.values()) else None,
        cash_conservation_error_usd=cash-initial-sum(v['cme_pnl']+v['payout']-v['cash_outlay'] for v in positions.values()),
        max_margin_netting_benefit_usd=(batches.gross_margin_without_netting_usd-batches.net_margin_usd).max() if len(hi) else 0,
        true_OOS=False,verified_execution_capacity=False)
    assert abs(sm['cash_conservation_error_usd'])<1e-6
    return tr,hi,ce,sk,sf,sm

def load_inputs():
    e=pd.read_parquet(private_input('work/capital100k/executions.parquet'))
    h=pd.read_parquet(private_input('work/liquidity_only/history_marks.parquet'))
    e=parent.attach_rules(estimate_buffers(e,h))
    m=pd.read_parquet(private_input('work/capital100k/marks_prepared.parquet'))
    z=json.loads((private_input('work/capital100k/evaluation.json')).read_text())
    return e,m,z['terminal'],z['winners'],h

def main():
    for name,digest in PROTO['input_sha256'].items():assert hashlib.sha256(expected_input(name).read_bytes()).hexdigest()==digest,name
    e,m,t,w,h=load_inputs()
    fields=['meeting_date','execution_status','history_observations','history_start','history_end','pair_VM','far_VM','empirical_VM',
        'history_latest_available_ts','history_latest_receipt_ts','max_calendar_gap_days','max_5interval_calendar_days']
    e[fields].to_csv(OUT/'buffer_estimates.csv',index=False)
    sums=[]
    for cfg in PROTO['scenarios']:
        name=cfg['name'];tr,hi,ce,sk,sf,sm=run_account(e,m,t,w,h,**{k:v for k,v in cfg.items() if k!='name'})
        for label,frame in [('trades',tr),('cash_events',hi),('sizing_certificates',ce),('skips',sk),('margin_shortfalls',sf)]:
            frame.to_csv(OUT/f'{name}_{label}.csv',index=False)
        sums.append(dict(scenario=name,**sm))
    pd.DataFrame(sums).to_csv(OUT/'summary.csv',index=False)
    print(pd.DataFrame(sums).to_string(index=False))

if __name__=='__main__':raise RuntimeError('Use runner.py with the frozen scenario protocol')
