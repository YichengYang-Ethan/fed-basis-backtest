"""Run a frozen collateral sensitivity, with both all-signal and mature-cohort books."""
from pathlib import Path
from datetime import datetime,timezone
import importlib.util,json,hashlib
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

import sys as _snapshot_sys
_snapshot_sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from snapshot_paths import private_input, raw_input, output_path, expected_input

ROOT=Path(__file__).resolve().parents[2];OUT = output_path('outputs/spread_credit_backtest')
PROTOCOL=json.loads((ROOT / 'outputs/spread_credit_backtest/protocol.json').read_text())
spec=importlib.util.spec_from_file_location('spread_credit_engine',Path(__file__).with_name('engine.py'))
engine=importlib.util.module_from_spec(spec);spec.loader.exec_module(engine)

def check_hashes():
    for name,digest in PROTOCOL['input_sha256'].items():
        if hashlib.sha256(expected_input(name).read_bytes()).hexdigest()!=digest:raise RuntimeError('Changed source '+name)

def pm_values(executions,cutoff):
    # Valuation only, not an entry feature or cash credit. Older samples fail closed.
    source=raw_input('poly/prices.parquet')
    rows=[]
    for r in executions.to_dict('records'):
        if r['execution_status']!='READY' or r['execution_ts']>cutoff or engine.base.payout_clock(r['meeting_date'])<=cutoff:continue
        token=str(r['actual_token_id'])
        f=pq.read_table(source,filters=[('token_id','=',token),('ts','>=',int(cutoff)-60),('ts','<=',int(cutoff)),('fidelity','=',1)],columns=['ts','p','fidelity']).to_pandas()
        f=f.drop_duplicates().sort_values('ts')
        if f.empty:rows.append(dict(meeting_date=r['meeting_date'],token_id=token,p=np.nan,age_seconds=np.nan,observed_ts=np.nan));continue
        latest=f[f.ts.eq(f.ts.max())]
        if latest.p.nunique()!=1:raise RuntimeError('Conflicting PM valuation prices')
        row=latest.iloc[0]
        rows.append(dict(meeting_date=r['meeting_date'],token_id=token,p=float(row.p),age_seconds=float(cutoff-row.ts),observed_ts=float(row.ts)))
    return pd.DataFrame(rows,columns=['meeting_date','token_id','p','age_seconds','observed_ts'])

def augment(sm,tr,hi,executions,cutoff,valuation,book,cfg):
    sm=dict(sm);sm.update(book=book,scenario=cfg['name'],cutoff_et=pd.to_datetime(cutoff,unit='s',utc=True).tz_convert(engine.ET).isoformat(),
        verified_broker_margins=False,verified_PM_execution_capacity=False,complete_holding_solvency_certified=False,
        pair_rate_status='HYPOTHETICAL_COMPLETE_PAIR_REQUIREMENT_NOT_SPAN')
    batches=hi[hi.event.eq('BATCH_CLOSE')].sort_values('timestamp')
    duration=(batches.timestamp.shift(-1,fill_value=cutoff)-batches.timestamp)/86400
    sm['net_initial_margin_dollar_days']=float((batches.net_margin_usd*duration).sum())
    digital_days=0.;locked=0.;pm_value=0.;valuation_ok=True;unpaid=[]
    for r in tr.to_dict('records'):
        er=executions[executions.meeting_date.eq(r['meeting_date'])].iloc[0]
        principal=r['digital_quantity']*er.digital_price
        end=min(cutoff,engine.base.payout_clock(r['meeting_date']))
        digital_days+=principal*max(0,end-r['execution_ts'])/86400
        if engine.base.payout_clock(r['meeting_date'])>cutoff:
            locked+=principal;unpaid.append(r['meeting_date'])
            v=valuation[valuation.meeting_date.eq(r['meeting_date'])]
            if len(v)!=1 or not np.isfinite(v.iloc[0].p):valuation_ok=False
            else:pm_value+=r['digital_quantity']*v.iloc[0].p
    sm.update(PM_principal_dollar_days=digital_days,PM_principal_still_locked_usd=locked,
        net_initial_plus_PM_principal_dollar_days=sm['net_initial_margin_dollar_days']+digital_days,
        unpaid_PM_meetings='|'.join(unpaid),PM_mark_value_usd=pm_value if valuation_ok else None,
        mark_NAV_method='Cash after available daily CME marks plus <=60sec old PM midpoint for unpaid tokens; not liquidation value or final return')
    if valuation_ok and sm['financing_path_valid']:
        sm['model_mark_NAV_usd']=sm['end_cash_usd']+pm_value
        sm['model_mark_account_return_pct']=100*(sm['model_mark_NAV_usd']/sm['initial_cash_usd']-1)
    else:sm['model_mark_NAV_usd']=None;sm['model_mark_account_return_pct']=None
    sm['realized_full_account_return_pct']=100*(sm['complete_financed_NAV']/sm['initial_cash_usd']-1) if sm['complete_financed_NAV'] is not None else None
    if len(tr):
        sm['minimum_closed_trade_profit_usd']=float(tr.loc[tr.status.eq('CLOSED'),'shadow_hold_profit_usd'].min())
        sm['maximum_closed_trade_profit_usd']=float(tr.loc[tr.status.eq('CLOSED'),'shadow_hold_profit_usd'].max())
    return sm

def main():
    check_hashes();e,m,terminal,winners,h=engine.load_inputs()
    shared=engine.model_module.ScenarioSource(h)
    old_release=engine.base.release_clock
    all_summaries=[];all_trades=[];all_skips=[];all_certs=[]
    for book in PROTOCOL['books']:
        cutoff=pd.Timestamp(book['cutoff_et']).timestamp()
        selected=e.copy()
        if book['meeting_calendar_max'] is not None:selected=selected[selected.meeting_date.le(book['meeting_calendar_max'])].copy()
        valuation=pm_values(selected,cutoff)
        valuation.to_csv(OUT/(book['name']+'_PM_valuation.csv'),index=False)
        selected.drop(columns=[x for x in ['initial_near_mark','initial_far_mark','entry_spread'] if x in selected]).to_csv(OUT/(book['name']+'_candidate_inventory.csv'),index=False)
        for cfg in PROTOCOL['scenarios']:
            if cfg['clock']=='old_plus7':engine.base.release_clock=old_release
            else:
                def next_business(month):
                    d=pd.Period(month,freq='M').end_time.normalize()+engine.base.CBD
                    return engine.base.timestamp(d,17).timestamp()
                engine.base.release_clock=next_business
            tr,hi,ce,sk,sf,sm=engine.run_account(selected,m,terminal,winners,h,method='portfolio',margin_per_leg=1100,maintenance_per_leg=950,
                extra_cents=cfg['extra_cents'],cutoff=cutoff,pair_initial=cfg['pair_initial'],pair_maintenance=cfg['pair_maintenance'],scenario_source=shared)
            sm=augment(sm,tr,hi,selected,cutoff,valuation,book['name'],cfg)
            prefix=book['name']+'__'+cfg['name']
            for kind,frame in [('trades',tr),('cash_events',hi),('sizing_certificates',ce),('skips',sk),('margin_shortfalls',sf)]:
                frame.to_csv(OUT/(prefix+'__'+kind+'.csv'),index=False)
            for frame,target in [(tr,all_trades),(sk,all_skips),(ce,all_certs)]:
                f=frame.copy();f.insert(0,'scenario',cfg['name']);f.insert(0,'book',book['name']);target.append(f)
            all_summaries.append(sm)
            print(json.dumps({k:sm[k] for k in ['book','scenario','closed_positions','pending_positions','financing_path_valid','sum_closed_profit_usd','median_account_contribution_pct','median_standalone_return_pct','model_mark_account_return_pct','min_maintenance_excess_cash_usd']},default=str),flush=True)
    engine.base.release_clock=old_release;check_hashes()
    pd.DataFrame(all_summaries).to_csv(OUT/'summary.csv',index=False)
    pd.concat(all_trades,ignore_index=True).to_csv(OUT/'trades.csv',index=False)
    pd.concat(all_skips,ignore_index=True).to_csv(OUT/'skips.csv',index=False)
    pd.concat(all_certs,ignore_index=True).to_csv(OUT/'sizing_certificates.csv',index=False)
    manifest=dict(finished_utc=datetime.now(timezone.utc).isoformat(),original_sources_unchanged=True,
        protocol_sha256=hashlib.sha256((ROOT / 'outputs/spread_credit_backtest/protocol.json').read_bytes()).hexdigest(),
        scripts_sha256={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob('*.py')},
        pm_valuation_source=str(raw_input('poly/prices.parquet')),
        paid_data_spend_usd=0,account_API_calls=0,orders=0)
    (OUT/'run_manifest.json').write_text(json.dumps(manifest,indent=2))

if __name__=='__main__':main()
