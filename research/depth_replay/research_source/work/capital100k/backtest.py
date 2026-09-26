"""Frozen-rule, self-funded account replay. Local data only; no acquisition or orders.

Prediction fills and clearing clocks are explicitly modeled, never certified.
Selection has a strict feature allowlist; terminal labels enter only cash events.
"""
from pathlib import Path
from itertools import groupby
import json, hashlib, math
import numpy as np
import pandas as pd
from pandas.tseries.holiday import USFederalHolidayCalendar
from pandas.tseries.offsets import CustomBusinessDay

import sys as _snapshot_sys
_snapshot_sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from snapshot_paths import private_input, raw_input, output_path, expected_input

ROOT=Path(__file__).resolve().parents[2]; W=ROOT/'work'; OUT=output_path('outputs/capital100k')
ET='America/New_York'; CBD=CustomBusinessDay(calendar=USFederalHolidayCalendar())
PROTO=json.loads((ROOT / 'outputs/capital100k/protocol.json').read_text())
CUTOFF=pd.Timestamp(PROTO['cutoff_et']).timestamp()
FEATURES=['meeting_date','anchor_ts','event_id','date_et','instrument','leg_near','leg_far',
 'previous_fomc_14et_ts','meeting_14et_ts','in_next_meeting_window','liveness_pass',
 'persistence_key','edge_cents','N','quantity','cme_route','cme_direction_sign',
 'actual_token_id','actual_buy_side','actual_outcome','quote_mid_actual','cme_entry_D_bp',
 'sum_mid_60s','omitted_max','d0_bp','d1_bp','cash_perN']
KEY=['meeting_date','anchor_ts','persistence_key']

def timestamp(date,hour):
    return pd.Timestamp(date).tz_localize(ET).replace(hour=hour)

def release_clock(month):
    day=pd.Period(month,freq='M').end_time.normalize()+pd.Timedelta(days=7)
    return timestamp(CBD.rollforward(day),17).timestamp()

def payout_clock(meeting):
    return timestamp(pd.Timestamp(meeting)+2*CBD,17).timestamp()

def mark_clock(date,receipt):
    planned=timestamp(pd.Timestamp(date).normalize()+CBD,9)
    recv=pd.Timestamp(receipt).tz_convert(ET)
    day=recv.tz_localize(None).normalize()
    bank=CBD.rollforward(day)
    known=timestamp(bank,9)
    if known<recv: known=timestamp(bank+CBD,9)
    return max(planned,known).timestamp()

def load_features():
    return pd.read_parquet(private_input('work/tail_thresholds/poly_binary_tau5_omit_only_nested.parquet'),columns=FEATURES)

def select_signals(input_frame):
    # Ignore every non-allowlisted column, including poisoned labels.
    f=input_frame.loc[:,FEATURES].copy()
    f=f[f.liveness_pass.eq(True)&f.in_next_meeting_window.eq(True)&f.edge_cents.notna()]
    assert not f.duplicated(KEY).any()
    f=f.sort_values(['meeting_date','persistence_key','anchor_ts'])
    eligible=f.edge_cents.ge(PROTO['edge_threshold_cents_per_N'])
    groups=f.groupby(['meeting_date','persistence_key'],sort=False)
    for lag in [1,2]:
        prior=groups[['anchor_ts','date_et','edge_cents']].shift(lag)
        eligible &= prior.edge_cents.ge(PROTO['edge_threshold_cents_per_N'])
        eligible &= f.anchor_ts.sub(prior.anchor_ts).eq(60*lag)&f.date_et.eq(prior.date_et)
    q=f[eligible].sort_values(['meeting_date','anchor_ts','edge_cents','persistence_key'],ascending=[True,True,False,True])
    return q.drop_duplicates('meeting_date').sort_values('anchor_ts').reset_index(drop=True)

def execution_observations(signals,delay=60):
    anchors=(signals.anchor_ts+delay).astype(int).tolist()
    legs=pd.read_parquet(private_input('work/expanded_poly_fullminute_leg_panel.parquet'),filters=[('anchor_ts','in',anchors)])
    cm=pd.read_parquet(private_input('work/expanded_cme_minute_panel.parquet'),filters=[('anchor_ts','in',anchors)])
    output=[]
    for r in signals.to_dict('records'):
        t=int(r['anchor_ts']+delay);r.update(execution_ts=t,execution_status='READY')
        r['execution_et']=pd.to_datetime(t,unit='s',utc=True).tz_convert(ET).isoformat()
        pre='' if r['actual_buy_side']=='YES' else 'no_'
        lp=legs[(legs.meeting_date==r['meeting_date'])&(legs.event_id==r['event_id'])&
                (legs.anchor_ts==t)&(legs[pre+'token_id']==r['actual_token_id'])]
        cq=cm[(cm.meeting_date==r['meeting_date'])&(cm.instrument==r['instrument'])&
              (cm.leg_near==r['leg_near'])&(cm.leg_far==r['leg_far'])&(cm.anchor_ts==t)]
        if len(lp)!=1 or len(cq)!=1:
            r['execution_status']='NO_FILL_DATA_MISSING';output.append(r);continue
        l=lp.iloc[0];c=cq.iloc[0];obs=l[pre+'observed_ts'];p=l[pre+'mid_60s']
        ok=pd.notna(p) and 0<=p<=1 and pd.notna(obs) and obs<=t and 0<=t-obs<=60
        ok=ok and (obs>r['anchor_ts'] if delay else True) and bool(l[pre+'usable_60s']) and bool(l.in_lifetime)
        ok=ok and l.date_et==r['date_et'] and t<r['meeting_14et_ts']
        r.update(pm_observed_ts=obs,pm_age_sec=t-obs if pd.notna(obs) else np.nan,digital_price=p)
        if not ok:
            r['execution_status']='NO_FILL_DATA_PM';output.append(r);continue
        side='bid' if r['cme_direction_sign']==1 else 'ask';route=r['cme_route']
        ok=bool(c[f'{route}_{side}_one_spread_carry']) and bool(c[f'{route}_both_clocks_age_le_60s'])
        capacity=c[f'{route}_{side}_size'];d=c[f'{route}_{side}_D_bp']
        ok=ok and pd.notna(capacity) and capacity>=1 and pd.notna(d)
        roles=['listed'] if route=='listed' else ['near','far']
        for role in roles:
            ok=ok and c[f'{role}_ts_recv_ns']<=t*10**9 and c[f'{role}_ts_event_ns']<=t*10**9
        if not ok:
            r['execution_status']='NO_FILL_DATA_CME';output.append(r);continue
        span=float(c.span_exact);spread=float(d)*span/100
        # Allocation between listed legs is bookkeeping only; their difference is
        # exactly the observed executable spread. Aggregate VM is invariant to it.
        near=float(c.near_mid)
        if not np.isfinite(near): near=100.0
        r.update(cme_depth=int(capacity),span=span,entry_spread=spread,
                 initial_near_mark=near,initial_far_mark=near-spread,
                 near_symbol=c.near_symbol,far_symbol=c.far_symbol)
        assert np.isclose(r['N'],1041.75*span)
        for m in ['leg_near','leg_far']:
            assert pd.Timestamp(r[m]).to_period('M').end_time.date()>=pd.to_datetime(t,unit='s',utc=True).tz_convert(ET).date()
        output.append(r)
    return pd.DataFrame(output)

def evaluation_data(signals):
    # Label loading is deliberately separate and happens after signal selection.
    labels=pd.read_parquet(private_input('work/tail_thresholds/poly_binary_tau5_omit_only_nested.parquet'),columns=KEY+['actual_token_winner_ex_post'])
    z=signals[KEY].merge(labels,on=KEY,validate='one_to_one')
    winners=z.set_index('meeting_date').actual_token_winner_ex_post.to_dict()
    t=pd.read_parquet(private_input('work/expanded_terminal_cash_reference.parquet'));term={}
    for r in t.itertuples():
        for role in ['near','far']:
            price=getattr(r,'terminal_'+role+'_price');m=getattr(r,'leg_'+role)
            if pd.notna(price):
                if m in term:assert np.isclose(term[m],price)
                term[m]=float(price)
    marks=pd.read_parquet(private_input('work/capital100k/daily_marks_with_flags.parquet'))
    assert not marks.duplicated(['trade_date','dm']).any()
    marks['available_ts']=[mark_clock(d,r) for d,r in zip(marks.trade_date,marks.ts_recv)]
    assert (marks.available_ts>=marks.ts_recv.astype('int64')/1e9).all()
    return winners,term,marks

def size_packages(cost_per,reserve,cash,locked,budget,buffer,depth,one_package=False):
    available=max(0,min(budget,cash-locked-buffer))
    n=min(int(depth),max(0,math.floor((available+1e-9)/(cost_per+reserve))))
    return min(n,1) if one_package else n

def run_account(executions,marks,terminal,winners,budget_fraction=.25,one_package=False,
                cutoff=CUTOFF,extra_execution_cents=0):
    initial=float(PROTO['initial_cash_usd']);reserve=float(PROTO['reserve_per_spread_usd'])
    cash=initial;locked=0.;positions={};trade_records=[];history=[];skips=[];breaches=[]
    events=[]
    for r in executions.to_dict('records'):
        events.append((r['execution_ts'],3,'entry',r))
        events.append((payout_clock(r['meeting_date']),1,'payout',r['meeting_date']))
    for r in marks.itertuples():
        if r.trade_date.to_period('M')<=pd.Period(r.dm):
            events.append((r.available_ts,0,'mark',r))
    for month,price in terminal.items(): events.append((release_clock(month),2,'final',(month,price)))
    events.sort(key=lambda e:(e[0],e[1]))
    def record(t,k,delta,md=''):
        history.append(dict(timestamp=float(t),time_et=pd.to_datetime(t,unit='s',utc=True).tz_convert(ET).isoformat(),
          event=k,meeting_date=md,cash_change_usd=delta,cash_usd=cash,reserve_locked_usd=locked,
          free_cash_usd=cash-locked,open_packages=sum(v['n'] for v in positions.values() if not all(v['closed'].values())),
          open_digital_cost_usd=sum(v['principal'] for v in positions.values() if not v['paid'])))
    for t,group in groupby(events,key=lambda e:e[0]):
        if t>cutoff:break
        batch=list(group);cash_before_batch=cash
        for _,_,kind,r in batch:
            if kind=='entry':
                md=r['meeting_date']
                if r['execution_status']!='READY':
                    skips.append(dict(meeting_date=md,status=r['execution_status'],execution_ts=t));continue
                q=int(r['quantity']);p=float(r['digital_price'])
                fee=.05*q*p*(1-p);exc=(.02+extra_execution_cents*.01)*q
                per=q*p+fee+exc+5.
                n=size_packages(per,reserve,cash,locked,initial*budget_fraction,
                                PROTO['cash_buffer_usd'],r['cme_depth'],one_package)
                if n==0:
                    skips.append(dict(meeting_date=md,status='NO_FILL_CAPITAL',execution_ts=t));continue
                reserve_amt=n*reserve;cost=n*per;cash-=cost;locked+=reserve_amt
                v=dict(r);v.update(n=n,total_Q=n*q,principal=n*q*p,fees=n*fee,execution_cost=n*exc,
                 cash_outlay=cost,allocated=cost+reserve_amt,reserve=reserve_amt,cme_pnl=0.,payout=0.,paid=False,
                 marks={'near':r['initial_near_mark'],'far':r['initial_far_mark']},
                 mark_dates={'near':None,'far':None},closed={'near':False,'far':False},
                 cash_before_entry=cash+cost,locked_before_entry=locked-reserve_amt,prelim_mark_count=0,
                 stale_revision_skips=0,status='PENDING')
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
                        v['cme_pnl']+=delta;cash+=delta
                        # Record individual cash flows; assess liquidity only after
                        # all marks of the same modeled clearing timestamp net.
                        record(t,'VM_'+role.upper(),delta,md)
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
                    if all(v['closed'].values()) and not v.get('reserve_released',False):
                        locked-=v['reserve'];v['reserve_released']=True;record(t,'RESERVE_RELEASE',0.,md)
            for v in positions.values():
                if v['paid'] and all(v['closed'].values()) and v['status']!='CLOSED':
                    v.update(status='CLOSED',completion_ts=t)
        if positions:
            record(t,'BATCH_CLOSE',0.)
            history[-1]['batch_net_cash_change_usd']=cash-cash_before_batch
            if cash-locked < -1e-7:breaches.append(dict(timestamp=t,free_cash_usd=cash-locked))
    for md,v in positions.items():
        complete=v['status']=='CLOSED';pnl=v['cme_pnl']+v['payout']-v['cash_outlay'] if complete else np.nan
        if complete:
            direct=v['n']*v['cme_direction_sign']*4167*(v['entry_spread']-(terminal[v['leg_near']]-terminal[v['leg_far']]))+v['payout']-v['cash_outlay']
            assert abs(pnl-direct)<1e-7
        digital_days=(payout_clock(md)-v['execution_ts'])/86400
        reserve_days=(release_clock(v['leg_far'])-v['execution_ts'])/86400
        opp=.05*((v['principal']+v['fees']+v['execution_cost'])*digital_days+v['reserve']*reserve_days)/365
        trade_records.append(dict(meeting_date=md,signal_ts=v['anchor_ts'],execution_ts=v['execution_ts'],
          execution_et=v['execution_et'],near_symbol=v['near_symbol'],far_symbol=v['far_symbol'],
          cme_route=v['cme_route'],direction_sign=v['cme_direction_sign'],actual_outcome=v['actual_outcome'],
          actual_buy_side=v['actual_buy_side'],packages=v['n'],digital_quantity=v['total_Q'],
          observed_CME_capacity_packages=v['cme_depth'],cash_outlay_usd=v['cash_outlay'],reserve_usd=v['reserve'],
          allocated_usd=v['allocated'],model_profit_usd=pnl,status=v['status'],
          account_contribution_pct=100*pnl/initial,allocated_return_pct=100*pnl/v['allocated'],
          nonmargin_cash_return_pct=100*pnl/v['cash_outlay'],opportunity_cost_usd=opp,
          profit_after_opportunity_cost_usd=pnl-opp if complete else np.nan,
          completion_et=pd.to_datetime(v['completion_ts'],unit='s',utc=True).tz_convert(ET).isoformat() if complete else None,
          pm_age_sec=v['pm_age_sec'],entry_ladder_sum=v['sum_mid_60s'],
          initial_free_cash_usd=v['cash_before_entry']-v['locked_before_entry'],
          prelim_mark_count=v['prelim_mark_count'],stale_revision_skips=v['stale_revision_skips'],
          cme_cash_to_cutoff_usd=v['cme_pnl'],digital_payout_to_cutoff_usd=v['payout']))
    trades=pd.DataFrame(trade_records);hist=pd.DataFrame(history);skip=pd.DataFrame(skips)
    known=trades[trades.status.eq('CLOSED')] if len(trades) else trades
    closehist=hist[hist.event=='BATCH_CLOSE'] if len(hist) else hist
    summary=dict(initial_cash_usd=initial,event_budget_fraction=budget_fraction,one_package=one_package,
      extra_execution_cents=extra_execution_cents,filled_model_positions=len(trades),closed_positions=len(known),
      pending_positions=int(trades.status.eq('PENDING').sum()) if len(trades) else 0,
      data_skips=int(skip.status.str.startswith('NO_FILL_DATA').sum()) if len(skip) else 0,
      capital_skips=int(skip.status.eq('NO_FILL_CAPITAL').sum()) if len(skip) else 0,
      median_account_contribution_pct=known.account_contribution_pct.median() if len(known) else None,
      median_allocated_return_pct=known.allocated_return_pct.median() if len(known) else None,
      median_nonmargin_cash_return_pct=known.nonmargin_cash_return_pct.median() if len(known) else None,
      median_profit_usd=known.model_profit_usd.median() if len(known) else None,
      sum_closed_profit_usd=known.model_profit_usd.sum() if len(known) else 0,
      closed_positive_count=int(known.model_profit_usd.gt(0).sum()) if len(known) else 0,
      min_free_cash_after_clearing_usd=min(initial,float(closehist.free_cash_usd.min())) if len(closehist) else initial,
      peak_reserve_usd=float(closehist.reserve_locked_usd.max()) if len(closehist) else 0,
      end_cash_usd=cash,end_reserve_usd=locked,end_free_cash_usd=cash-locked,
      negative_free_cash_batches=len(breaches),true_OOS=False,verified_fill_count=0,
      verified_fill_median=None,complete_account_NAV=None,
      cash_conservation_error_usd=cash-(initial+sum(v['cme_pnl']+v['payout']-v['cash_outlay'] for v in positions.values())))
    assert abs(summary['cash_conservation_error_usd'])<1e-6
    return trades,hist,skip,summary

def main():
    for name,digest in PROTO['input_sha256'].items(): assert hashlib.sha256(expected_input(name).read_bytes()).hexdigest()==digest
    f=load_features();s=select_signals(f)
    old=pd.read_csv(ROOT/'outputs/execution_parameter_poly_entries.csv');old=old[old.rule_id=='T5_E4_P3']
    assert s[KEY].sort_values(KEY).reset_index(drop=True).equals(old[KEY].sort_values(KEY).reset_index(drop=True))
    s.to_parquet(private_input('work/capital100k/signals.parquet'),index=False)
    ex=execution_observations(s);ex.to_parquet(private_input('work/capital100k/executions.parquet'),index=False)
    winners,terminal,marks=evaluation_data(s);marks.to_parquet(private_input('work/capital100k/marks_prepared.parquet'),index=False)
    totals=[]
    scenarios=[('primary_25pct',.25,False,0),('one_package',.25,True,0),('budget_10pct',.10,False,0),('budget_50pct',.50,False,0),
               ('primary_plus1c',.25,False,1),('primary_plus2c',.25,False,2)]
    for name,budget,one,extra in scenarios:
        tr,hi,sk,sm=run_account(ex,marks,terminal,winners,budget,one,extra_execution_cents=extra)
        tr.to_csv(OUT/(name+'_trades.csv'),index=False);hi.to_csv(OUT/(name+'_cash_events.csv'),index=False)
        sk.to_csv(OUT/(name+'_skips.csv'),index=False);totals.append(dict(scenario=name,**sm))
    pd.DataFrame(totals).to_csv(OUT/'summary.csv',index=False)
    cov=pd.read_csv(ROOT/'outputs/poly_all_meetings_coverage_audit.csv');cov=cov[cov.calendar_status=='historical'][['meeting_date','primary_exclusion_code']]
    primary=pd.read_csv(OUT/'primary_25pct_trades.csv');sk=pd.read_csv(OUT/'primary_25pct_skips.csv')
    status={r.meeting_date:r.status for r in primary.itertuples()};status.update({r.meeting_date:r.status for r in sk.itertuples()})
    cov['account_status']=cov.meeting_date.map(status).fillna(cov.primary_exclusion_code.map(lambda x:'NO_SIGNAL' if x=='VALID_DATA_NO_4C_3MIN_SIGNAL' else 'NOT_EVALUABLE'))
    cov.to_csv(OUT/'all38_meetings.csv',index=False)
    (private_input('work/capital100k/evaluation.json')).write_text(json.dumps({'winners':winners,'terminal':terminal},indent=2,allow_nan=True))
    print(pd.DataFrame(totals)[['scenario','closed_positions','pending_positions','median_account_contribution_pct','median_allocated_return_pct','median_nonmargin_cash_return_pct','sum_closed_profit_usd','min_free_cash_after_clearing_usd']].to_string(index=False))

if __name__=='__main__':main()
