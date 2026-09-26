"""Frozen-entry nonlinear depth-cost replay using the unchanged cash ledger.

No network, orders, source-data modifications, or signal optimization.
"""
from pathlib import Path
from datetime import datetime, timezone
import importlib.util
import json
import hashlib
import sys

import numpy as np
import pandas as pd

import sys as _snapshot_sys
_snapshot_sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from snapshot_paths import private_input, raw_input, output_path, expected_input

ROOT = Path(__file__).resolve().parents[2]
OUT = output_path('outputs/depth_replay')
WORK = Path(__file__).parent
PROTO = json.loads((ROOT / 'outputs/depth_replay/protocol.json').read_text())


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


engine = module('depth_replay_ledger', ROOT / 'work/spread_credit_backtest/engine.py')
runner = module('depth_replay_old_runner', ROOT / 'work/spread_credit_backtest/runner.py')
books_module = module('depth_replay_books', WORK / 'prepare_books.py')


def hash_inputs():
    for p, digest in PROTO['input_sha256'].items():
        assert hashlib.sha256(expected_input(p).read_bytes()).hexdigest() == digest, p


def cost_arrays(r, ns, mode, books):
    shares = np.asarray(ns, dtype=float) * int(r['quantity'])
    covered = r['meeting_date'] in books
    if mode == 'old_proxy' or not covered:
        p = float(r['digital_price'])
        principal = shares * p
        fee = .05 * shares * p * (1-p)
        friction = .02 * shares
        complete = np.ones(len(shares), dtype=bool)
        source = 'old_midpoint_plus2c_proxy'
    else:
        x = books_module.cost_curve(books[r['meeting_date']], shares)
        principal, fee, complete = x['principal'], x['fee'], x['complete']
        friction = (.02 if mode == 'book_retain_2c' else 0.) * shares
        source = 'telonex_static_full_ask_book'
    cost = principal + fee + friction + 5*np.asarray(ns)
    return dict(cost=cost, principal=principal, fee=fee, friction=friction,
                complete=complete, cost_source=source, book_available=covered)


def make_sizer(mode, books, run_key, keep_grids=True):
    def size(r, positions, cash, margin_per_leg, maintenance_per_leg, method,
             oracle, extra_cents=0, blocked=False):
        assert method == 'portfolio' and extra_cents == 0
        ns = np.arange(int(r['cme_depth']) + 1, dtype=np.int64)
        costs = cost_arrays(r, ns, mode, books)
        net = engine.exposure(positions)
        oldM, oldB, _ = engine.obligations(positions, margin_per_leg, oracle)
        required, initial, maintenance, info = oracle.requirements(
            net, r['execution_ts'], r, ns, engine.live_months(positions))
        before = engine.required_book(positions, r['execution_ts'], method,
                                      oracle, margin_per_leg, maintenance_per_leg)
        gross = min(r['cme_direction_sign']*41.67*(100*r['entry_spread']-r['span']*d)
                    + int(r['quantity'])*engine.parent.token_payoff(r, d)
                    for d in (r['d0_bp'], r['d1_bp']))
        floor = gross*ns - costs['cost']
        slack = cash - costs['cost'] - required
        feasible = (slack >= -1e-8) & costs['complete'] & np.isfinite(costs['cost'])
        eligible = feasible & (floor > 1e-8) & (ns > 0) & (not blocked)
        if eligible.any():
            best = float(floor[eligible].max())
            n = int(ns[eligible & (floor >= best-1e-8)][0])
        else:
            n = 0
        optimality_gap = float(floor[eligible].max()-floor[n]) if eligible.any() else 0.
        assert optimality_gap <= 1.01e-8
        if n:
            assert feasible[n] and floor[n] > 0
        if blocked:
            reason = 'PRIOR_MARGIN_SHORTFALL'
        elif n == 0:
            positive_displayed = costs['complete'] & (ns > 0) & np.isfinite(floor) & (floor > 1e-8)
            if positive_displayed.any():
                reason = 'CASH_MARGIN_VM'
            elif not (costs['complete'] & (ns > 0)).any():
                reason = 'PM_DISPLAYED_DEPTH'
            else:
                reason = 'NONPOSITIVE_QUOTED_CORE_EDGE'
        elif n == int(r['cme_depth']):
            reason = 'CME_DEPTH'
        elif feasible[n+1] and costs['complete'][n+1]:
            reason = 'NONLINEAR_QUOTED_FLOOR_OPTIMUM'
        elif not costs['complete'][n+1]:
            reason = 'PM_DISPLAYED_DEPTH'
        else:
            reason = 'CASH_MARGIN_VM'
        alone, *_ = oracle.requirements({}, r['execution_ts'], r, [n])
        # The unchanged cash ledger consumes n*unit terms: return the chosen
        # aggregate cost divided by n, NOT the one-package top-of-book quote.
        j = n if n else 1
        u = dict(cost=float(costs['cost'][j]/j), principal=float(costs['principal'][j]/j),
                 fee=float(costs['fee'][j]/j), execution=float(costs['friction'][j]/j))
        b = books.get(r['meeting_date'], {})
        cert = dict(meeting_date=r['meeting_date'], execution_ts=r['execution_ts'], packages=n,
            cme_capacity_packages=int(r['cme_depth']), cash_before_usd=cash,
            margin_before_usd=oldM, existing_VM_usd=oldB,
            net_margin_after_usd=float(initial[n]), incremental_net_margin_usd=float(initial[n]-oldM),
            unit_cash_usd=u['cost'], unit_VM_usd=r['empirical_VM'], unit_margin_leg_usd=margin_per_leg,
            cash_slack_usd=float(slack[n]), next_package_cash_slack_usd=float(slack[n+1]) if n<len(ns)-1 else None,
            higher_feasible_quantities=int(feasible[n+1:].sum()),
            unit_quoted_core_floor_usd=float(floor[n]/n) if n else float(floor[1]),
            selected_quoted_core_floor_usd=float(floor[n]),
            objective_optimality_gap_usd=optimality_gap,
            required_cash_before_usd=before, required_cash_after_usd=float(required[n]),
            incremental_account_commitment_usd=float(costs['cost'][n]+required[n]-before),
            standalone_required_cash_usd=float(alone[0]),
            extra_cash_beyond_current_initial_usd=float(required[n]-initial[n]),
            method=method, rejection_reason=reason, net_contracts_before_json=json.dumps(net, sort_keys=True),
            history_observations=r['history_observations'], history_start=r['history_start'], history_end=r['history_end'],
            pair_VM_usd=r['pair_VM'], far_VM_usd=r['far_VM'], max_calendar_gap_days=r['max_calendar_gap_days'],
            max_5interval_calendar_days=r['max_5interval_calendar_days'],
            history_latest_available_ts=r['history_latest_available_ts'], joint_history_info_json=json.dumps(info),
            cost_source=costs['cost_source'], new_pm_book_available=costs['book_available'],
            book_source_age_sec=b.get('source_age_sec'), book_collector_age_sec=b.get('collector_age_sec'),
            pm_principal_usd=float(costs['principal'][n]), pm_model_fee_usd=float(costs['fee'][n]),
            extra_friction_usd=float(costs['friction'][n]), cme_model_cost_usd=5*n,
            chosen_pm_vwap=float(costs['principal'][n]/(n*int(r['quantity']))) if n else None,
            sum_cost_usd=float(costs['cost'][n]), is_new_oos=False,
            cme_mbp10_validated=r['meeting_date'] != '2026-09-16')
        if keep_grids:
            dest = output_path('work/depth_replay/grids') / run_key
            dest.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(dict(packages=ns, cash_cost_usd=costs['cost'], principal_usd=costs['principal'],
                fee_usd=costs['fee'], friction_usd=costs['friction'], required_cash_usd=required,
                cash_slack_usd=slack, floor_profit_usd=floor, depth_complete=costs['complete'],
                feasible=feasible, eligible=eligible, selected=(ns==n))).to_parquet(dest / (r['meeting_date']+'.parquet'), index=False)
        return n, u, r['empirical_VM'], cert
    return size


def enrich_trades(tr, ce, e, cfg):
    cols = ['meeting_date','cost_source','new_pm_book_available','pm_principal_usd','pm_model_fee_usd',
            'extra_friction_usd','chosen_pm_vwap','selected_quoted_core_floor_usd',
            'standalone_required_cash_usd','sum_cost_usd','cme_mbp10_validated']
    result = tr.merge(ce[cols], on='meeting_date', validate='one_to_one')
    conditional = []
    for _, r in result.iterrows():
        er = e[e.meeting_date.eq(r.meeting_date)].iloc[0]
        q = r.digital_quantity
        profits = [r.packages*er.cme_direction_sign*41.67*(100*er.entry_spread-er.span*d)
                   + q*engine.parent.token_payoff(er,d)-r.cash_outlay_usd for d in [er.d0_bp, er.d1_bp]]
        conditional.append(min(profits))
    result['entry_two_state_floor_profit_usd'] = conditional
    result['entry_two_state_floor_roi_pct'] = 100*result.entry_two_state_floor_profit_usd/result.standalone_committed_usd
    result['realized_full_funding_roi_pct'] = result.standalone_return_pct
    result['is_realized_profit'] = result.status.eq('CLOSED')
    return result


def summarize(sm, tr, hi, selected, cfg, book):
    cutoff=pd.Timestamp(book['cutoff_et']).timestamp()
    vals=runner.pm_values(selected,cutoff)
    sm=runner.augment(sm,tr,hi,selected,cutoff,vals,book['name'],cfg)
    # Old runner valued locked principal at original midpoint; replace cash
    # investment accounting with actual quantity-dependent entry principal.
    dollars_days=0.;locked=0.
    for r in tr.itertuples():
        end=min(cutoff,engine.base.payout_clock(r.meeting_date))
        dollars_days+=r.pm_principal_usd*max(0,end-r.execution_ts)/86400
        if engine.base.payout_clock(r.meeting_date)>cutoff:locked+=r.pm_principal_usd
    sm['PM_principal_dollar_days']=dollars_days
    sm['PM_principal_still_locked_usd']=locked
    sm['net_initial_plus_PM_principal_dollar_days']=sm['net_initial_margin_dollar_days']+dollars_days
    sm.update(cost_mode=cfg['cost_mode'], displayed_book_trades=int(tr.cost_source.eq('telonex_static_full_ask_book').sum()),
        proxy_cost_trades=int(tr.cost_source.eq('old_midpoint_plus2c_proxy').sum()),
        source_signal_count=len(selected), source_ready_count=int(selected.execution_status.eq('READY').sum()),
        packages_total=int(tr.packages.sum()), median_entry_two_state_floor_roi_pct=float(tr.entry_two_state_floor_roi_pct.median()),
        verified_PM_execution_capacity=False, static_book_coverage_incomplete=True)
    first=float(selected.execution_ts.min())
    sm['annualization_start_utc']=pd.to_datetime(first,unit='s',utc=True).isoformat()
    sm['annualization_end_utc']=pd.to_datetime(cutoff,unit='s',utc=True).isoformat()
    years=(cutoff-first)/(365.25*86400)
    sm['calendar_years']=years
    sm['calendar_cagr_pct']=100*((sm['complete_financed_NAV']/100000)**(1/years)-1) if sm['complete_financed_NAV'] is not None else None
    if sm['pending_positions']==0 and sm['complete_financed_NAV'] is not None:
        assert abs(sm['complete_financed_NAV']-100000-tr.shadow_hold_profit_usd.sum())<1e-6
    return sm


def main():
    hash_inputs()
    e,m,term,winners,h=engine.load_inputs()
    books=books_module.load_books(e)
    assert len(books)==5
    actual=pd.read_csv(private_input('outputs/data_supplement/cme_fixed_entry_depth.csv'))
    actual=actual[actual.original_route_selected]
    for r in actual.itertuples():
        er=e[e.meeting_date.eq(r.meeting_date)].iloc[0]
        assert abs(er.initial_near_mark-er.initial_far_mark-er.entry_spread)<1e-12
        assert er.cme_depth == r.displayed_top_capacity
        assert abs(r.extra_execution_cost_vs_original_entry_total_usd)<1e-8
    def next_business(month):
        d=pd.Period(month,freq='M').end_time.normalize()+engine.base.CBD
        return engine.base.timestamp(d,17).timestamp()
    engine.base.release_clock=next_business
    shared=engine.model_module.ScenarioSource(h)
    summaries=[];trades=[];skips=[]
    for book in PROTO['books']:
        selected=e if book['meeting_calendar_max'] is None else e[e.meeting_date.le(book['meeting_calendar_max'])].copy()
        # Keep every original first signal; no filtering on prior fills or winners.
        selected.drop(columns=['entry_spread','initial_near_mark','initial_far_mark']).to_csv(OUT/(book['name']+'_signal_inventory.csv'),index=False)
        for cfg in PROTO['scenarios']:
            key=book['name']+'__'+cfg['name']
            engine.size_packages=make_sizer(cfg['cost_mode'],books,key)
            tr,hi,ce,sk,sf,sm=engine.run_account(selected,m,term,winners,h,
                margin_per_leg=cfg['single_initial'], maintenance_per_leg=cfg['single_maintenance'],
                pair_initial=cfg['pair_initial'],pair_maintenance=cfg['pair_maintenance'],
                cutoff=pd.Timestamp(book['cutoff_et']).timestamp(),scenario_source=shared)
            tr=enrich_trades(tr,ce,selected,cfg)
            sm=summarize(sm,tr,hi,selected,cfg,book)
            for name,df in [('trades',tr),('cash_events',hi),('sizing_certificates',ce),('skips',sk),('margin_shortfalls',sf)]:
                df.to_csv(OUT/(key+'__'+name+'.csv'),index=False)
            for frame,target in [(tr,trades),(sk,skips)]:
                frame=frame.copy();frame.insert(0,'scenario',cfg['name']);frame.insert(0,'book',book['name']);target.append(frame)
            summaries.append(sm)
            print(json.dumps({k:sm[k] for k in ['book','scenario','closed_positions','pending_positions','packages_total',
                'financing_path_valid','complete_financed_NAV','model_mark_NAV_usd','median_standalone_return_pct','calendar_cagr_pct']},default=str),flush=True)
    hash_inputs()
    pd.DataFrame(summaries).to_csv(OUT/'summary.csv',index=False)
    pd.concat(trades,ignore_index=True).to_csv(OUT/'trades.csv',index=False)
    pd.concat(skips,ignore_index=True).to_csv(OUT/'skips.csv',index=False)
    manifest=dict(completed_at=datetime.now(timezone.utc).isoformat(),original_input_hashes_unchanged=True,
        paid_api_calls=0,orders=0,protocol_sha256=hashlib.sha256((ROOT / 'outputs/depth_replay/protocol.json').read_bytes()).hexdigest(),
        code_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in WORK.glob('*.py')},
        raw_data_redistributed=False,independent_audit_pending=True)
    (OUT/'run_manifest.json').write_text(json.dumps(manifest,indent=2))


if __name__=='__main__':
    main()
