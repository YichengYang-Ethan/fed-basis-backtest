"""Read-only CME instrument and execution-route choice study.

Only meeting-level / aggregate derived diagnostics leave work/. No network,
purchases, credentials, orders, or edits of either source repository.
"""
import os
from pathlib import Path
from pathlib import Path
import json
import numpy as np
import pandas as pd

W = Path(__file__).resolve().parent
O = W.parent / 'outputs'
D = Path(os.environ['FOMC_DATA_ROOT'])
A = Path(os.environ['FOMC_PROJECT_ROOT'])
R = D / 'raw/cme/databento/intraday_20260917'
EPS = 1e-10


def native(v):
    if isinstance(v, dict): return {str(k): native(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)): return [native(x) for x in v]
    if isinstance(v, (np.bool_,)): return bool(v)
    if isinstance(v, (np.integer,)): return int(v)
    if isinstance(v, (float, np.floating)): return float(v) if np.isfinite(v) else None
    return v


def quant(x, name):
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    return {f'{name}_{k}': float(f(x)) if len(x) else None for k, f in
            [('mean', np.mean), ('p10', lambda v: np.quantile(v, .1)),
             ('p50', np.median), ('p90', lambda v: np.quantile(v, .9)),
             ('p99', lambda v: np.quantile(v, .99))]}


def symbol(ym):
    return 'ZQ' + 'FGHJKMNQUVXZ'[ym.month - 1] + str(ym.year % 10)


def month_loading(month, effective):
    """Coefficient of a permanent policy step in this month's mean EFFR."""
    dates = pd.date_range(month.start_time.normalize(), month.end_time.normalize())
    return np.mean(dates >= effective)


def calendar_study(p):
    c = pd.read_csv(A / 'data/fomc_instruments.csv')
    c['md'] = pd.to_datetime(c.meeting_date)
    c['eff'] = pd.to_datetime(c.effective_date)
    jobs = json.loads((R / 'acquisition_manifest.json').read_text())['jobs']
    defs = pd.read_parquet(R / 'definitions/contract_definitions.parquet')
    rows = []
    for r in c.itertuples():
        month = r.md.to_period('M')
        own = p[p.meeting_date.eq(r.meeting_date)]
        for route, near, far in [('FRONT', month, month+1), ('BACK', month-1, month)]:
            loading = np.array([month_loading(far, e)-month_loading(near, e) for e in c.eff])
            target = c.meeting_date.eq(r.meeting_date).to_numpy()
            span = float(loading[target][0])
            others = c.loc[(abs(loading) > EPS) & ~target, 'meeting_date'].tolist()
            original = bool(getattr(r, route.lower()+'_eligible'))
            # Independently reproduce the repository's deliberately conservative month filter.
            month_others = c.loc[c.eff.dt.to_period('M').isin([near, far]) & ~target, 'meeting_date'].tolist()
            conservative = span > EPS and not month_others
            assert conservative == original, (r.meeting_date, route)
            clean = span > EPS and not others
            near_sym, far_sym = symbol(near), symbol(far)
            listed_sym = near_sym + '-' + far_sym
            start = pd.Timestamp(own.anchor_utc.min()) if len(own) else None
            expiries = defs[defs.symbol.eq(near_sym)]
            if start is not None:
                expiries = expiries[expiries.ts_recv.le(start.value)]
            expiry_ns = int(expiries.sort_values('ts_recv').iloc[-1].expiration) if len(expiries) else None
            expiry = pd.Timestamp(expiry_ns, tz='UTC') if expiry_ns else None
            still_live = own[own.anchor_ns.lt(expiry_ns)] if len(own) and expiry_ns else own.iloc[:0]
            target_jobs = [j for j in jobs if j['meeting_date'] == r.meeting_date]
            target_outrights = {s for j in target_jobs if j['instrument_kind'] == 'outright_pair' for s in j['request']['symbols'].split(',')}
            target_listed = {j['request']['symbols'] for j in target_jobs if j['instrument_kind'] == 'listed_calendar_spread'}
            # Full acquisition inventory is checked for any overlapping alternative coverage.
            overlap_symbols = set()
            if len(own):
                for j in jobs:
                    st = pd.Timestamp(j['request']['start'], tz='UTC')
                    en = pd.Timestamp(j['request']['end'], tz='UTC')
                    if st <= own.anchor_utc.max() and en > own.anchor_utc.min():
                        overlap_symbols.update(j['request']['symbols'].split(','))
            rows.append(dict(meeting_date=r.meeting_date, instrument=route,
                selected_in_repository=r.instrument == route, repo_calendar_eligible=original,
                exact_zero_other_meeting_loading=clean, calendar_boundary_unverified=near < c.md.min().to_period('M') or far > c.md.max().to_period('M'),
                span=span, near_month=str(near), far_month=str(far),
                digital_contracts_per_spread=1041.75*span,
                other_nonzero_loadings=';'.join(f'{m}:{loading[c.meeting_date.eq(m).to_numpy()][0]:.12g}' for m in others),
                conservatively_excluded_zero_loading_meetings=';'.join(m for m in month_others if m not in others),
                sampled_intraday=bool(len(own)), sampled_anchor_dates=int(own.date_et.nunique()),
                near_expiration_utc=expiry.isoformat() if expiry is not None else '',
                anchor_dates_before_near_expiry=int(still_live.date_et.nunique()),
                anchor_minutes_before_near_expiry=len(still_live),
                near_in_same_meeting_purchase=near_sym in target_outrights,
                far_in_same_meeting_purchase=far_sym in target_outrights,
                listed_in_same_meeting_purchase=listed_sym in target_listed,
                pair_in_any_overlapping_purchase=near_sym in overlap_symbols and far_sym in overlap_symbols,
                listed_in_any_overlapping_purchase=listed_sym in overlap_symbols))
    cal = pd.DataFrame(rows)
    cal.to_csv(O / 'cme_choice_calendar.csv', index=False)
    count = {}
    for label, ix in [('all_49', cal.index == cal.index), ('intraday_26', cal.sampled_intraday)]:
        cc = cal.loc[ix]
        count[label] = {}
        for rule in ['repo_calendar_eligible', 'exact_zero_other_meeting_loading']:
            t = cc.pivot(index='meeting_date', columns='instrument', values=rule)
            count[label][rule] = {'front':int(t.FRONT.sum()),'back':int(t.BACK.sum()),
                 'both':int((t.FRONT & t.BACK).sum()),'either':int(t.any(axis=1).sum()),'neither':int((~t.any(axis=1)).sum())}
    alternatives = cal[cal.exact_zero_other_meeting_loading & ~cal.selected_in_repository & cal.sampled_intraday]
    return cal, dict(counts=count, alternative_intraday_rows=alternatives.to_dict('records'),
                     conservative_false_but_exact_clean=cal[~cal.repo_calendar_eligible & cal.exact_zero_other_meeting_loading].to_dict('records'),
                     caveat='Exact exposures are conditional on the supplied 49 scheduled effective dates. Calendar boundary candidates are explicitly unverified outside this supplied horizon; unscheduled changes and EFFR drift are not made riskless by this check.')


def side_books(p, role, side):
    px = p[[f'{role}_{side}_px_{i:02d}_nano' for i in range(10)]].to_numpy(dtype=float, na_value=np.nan)/1e9
    if role == 'listed': px *= p.listed_to_index_price_factor.to_numpy()[:,None]
    sz = p[[f'{role}_{side}_sz_{i:02d}' for i in range(10)]].to_numpy(dtype=float, na_value=np.nan)
    valid = np.isfinite(px) & np.isfinite(sz) & (sz > 0)
    contiguous = ~np.any(valid[:,1:] & ~valid[:,:-1], axis=1)
    diffs = np.diff(px, axis=1)
    ordered = ~np.any((diffs > EPS if side == 'bid' else diffs < -EPS) & valid[:,1:] & valid[:,:-1], axis=1)
    good = contiguous & ordered & valid[:,0]
    sz = np.where(valid, sz, 0.)
    px = np.where(valid, px, 0.)
    return px, sz, good, dict(noncontiguous_rows=int((~contiguous).sum()), unordered_rows=int((~ordered).sum()))


def sweep(book, lots):
    px, sz, good, _ = book
    prior = np.cumsum(sz, axis=1)-sz
    taken = np.minimum(sz, np.maximum(lots-prior,0))
    fills = good & (taken.sum(axis=1) >= lots)
    return np.where(fills,(taken*px).sum(axis=1)/lots,np.nan)


def compare(p, quotes, scenario, lots, gates, mask=None):
    n=len(p)
    use=np.ones(n,dtype=bool) if mask is None else mask
    factor=400/p.span_exact.to_numpy()
    out=[]
    for direction in ['sell','buy']:
        side='bid' if direction=='sell' else 'ask'
        s=np.where(gates['synthetic'] & use,quotes['synthetic',side,lots],np.nan)
        l=np.where(gates['listed'] & use,quotes['listed',side,lots],np.nan)
        sv=np.isfinite(s);lv=np.isfinite(l);both=sv&lv; either=sv|lv
        listed_gain=(l-s if direction=='sell' else s-l)
        lw=both&(listed_gain>EPS);sw=both&(listed_gain < -EPS);tie=both&~lw&~sw
        best=np.fmax(s,l) if direction=='sell' else np.fmin(s,l)
        top_s=quotes['synthetic',side,1];top_l=quotes['listed',side,1]
        top_gain=top_l-top_s if direction=='sell' else top_s-top_l
        # A strict flip requires one route uniquely preferred at top of book,
        # then the other uniquely preferred when sweeping the target quantity.
        flips=both&(((top_gain>EPS)&sw)|((top_gain < -EPS)&lw))
        rec=dict(scenario=scenario,lots=lots,direction=direction,anchors=int(use.sum()),
            synthetic_available=int(sv.sum()),listed_available=int(lv.sum()),both=int(both.sum()),either=int(either.sum()),
            listed_strictly_better=int(lw.sum()),synthetic_strictly_better=int(sw.sum()),tied=int(tie.sum()),
            listed_only=int((lv&~sv).sum()),synthetic_only=int((sv&~lv).sum()),
            strict_preference_flip_from_one_lot=int(flips.sum()),
            listed_selected_including_ties=int((lv & (~sv | (listed_gain>=-EPS))).sum()),
            synthetic_selected=int((sv & (~lv | (listed_gain < -EPS))).sum()))
        equivalent=lots*p.digital_contracts_per_spread.to_numpy()[either]
        rec.update(quant(equivalent,'hedged_25bp_equivalent_contracts'))
        rec.update(quant(listed_gain[both]*factor[both],'listed_improvement_cents'))
        gain=(best-s if direction=='sell' else s-best)*factor
        rec.update(quant(gain[both],'best_vs_synthetic_cents'))
        for route,q in [('synthetic',s),('listed',l)]:
            top=quotes[route,side,1]
            slip=(top-q if direction=='sell' else q-top)*factor
            rec.update(quant(slip[np.isfinite(q)],route+'_sweep_slippage_cents'))
        out.append(rec)
    return out


def route_study(p):
    books={(r,s):side_books(p,r,s) for r in ['near','far','listed'] for s in ['bid','ask']}
    quotes={}
    for n in [1,2,5,10]:
        for side,opp in [('bid','ask'),('ask','bid')]:
            quotes['synthetic',side,n]=sweep(books['near',side],n)-sweep(books['far',opp],n)
            quotes['listed',side,n]=sweep(books['listed',side],n)
    # Independent top-of-book arithmetic validates the native-to-index conversion.
    for route in ['synthetic','listed']:
        for side in ['bid','ask']:
            expected=p[f'{route}_{side}_index_points'].to_numpy()
            valid=np.isfinite(quotes[route,side,1])
            assert np.allclose(quotes[route,side,1][valid],expected[valid],atol=1e-12,rtol=0)
    rows=[];meeting=[];cost=[];coverage={};depth=[]
    for state,age in [('carry',60),('strict',60),('strict',1),('strict',300),('strict',None)]:
        scenario=f'{state}_{age}s' if age else f'{state}_all_same_day_ages'
        gates={r:p[r+'_usable_'+state].to_numpy(dtype=bool)&(p[r+f'_age_le_{age}s'].to_numpy(dtype=bool) if age else True) for r in ['synthetic','listed']}
        coverage[scenario]={r:int(v.sum()) for r,v in gates.items()}
        for n in [1,2,5,10]:
            rows += compare(p,quotes,scenario,n,gates)
        both=gates['synthetic']&gates['listed']
        for route in ['synthetic','listed']:
            width=(quotes[route,'ask',1]-quotes[route,'bid',1])*400/p.span_exact.to_numpy()
            cost.append(dict(scenario=scenario,route=route,common_anchors=int(both.sum()),
                **quant(width[both],'full_width_cents_per25'),**quant(width[both]/2,'half_width_cents_per25')))
        if scenario=='carry_60s':
            for route in ['synthetic','listed']:
                for side in ['bid','ask']:
                    gate=gates[route]
                    size=p[route+'_'+side+'_size'].to_numpy(dtype=float,na_value=np.nan)
                    opp='ask' if side=='bid' else 'bid'
                    total=(books['listed',side][1].sum(axis=1) if route=='listed' else
                           np.minimum(books['near',side][1].sum(axis=1), books['far',opp][1].sum(axis=1)))
                    depth.append(dict(scenario=scenario,route=route,side=side,anchors=int(gate.sum()),
                        **quant(size[gate],'bbo_displayed_spreads'),
                        **quant(size[gate]*p.digital_contracts_per_spread.to_numpy()[gate],'bbo_cme_25bp_equivalent_capacity'),
                        **quant(total[gate],'ten_level_displayed_spreads')))
            for md in p.meeting_date.unique():
                mask=p.meeting_date.eq(md).to_numpy()
                for n in [1,10]:
                    meeting += [dict(meeting_date=md,**x) for x in compare(p,quotes,scenario,n,gates,mask)]
    pd.DataFrame(rows).to_csv(O/'cme_choice_route_summary.csv',index=False)
    pd.DataFrame(meeting).to_csv(O/'cme_choice_route_by_meeting.csv',index=False)
    pd.DataFrame(cost).to_csv(O/'cme_choice_crossing_costs.csv',index=False)
    pd.DataFrame(depth).to_csv(O/'cme_choice_displayed_depth.csv',index=False)
    # Local normalized quotes are useful to the parent's digital strategy study.
    local=p[['meeting_date','anchor_ns','span_exact','digital_contracts_per_spread']].copy()
    for (route,side,lots),values in quotes.items():
        gate=p[route+'_usable_carry'].to_numpy(dtype=bool)&p[route+'_age_le_60s'].to_numpy(dtype=bool)
        local[f'{route}_{side}_{lots}lot_D_bp']=np.where(gate,values*100/p.span_exact.to_numpy(),np.nan)
    local.to_parquet(W/'cme_choice_sweep_quotes.parquet',index=False)
    return dict(coverage=coverage,book_depth_checks={f'{r}_{s}':b[3] for (r,s),b in books.items()},
                baseline=[x for x in rows if x['scenario']=='carry_60s'],crossing_costs=cost,displayed_depth=depth,
                assumptions='Displayed same-anchor snapshots, age gates and reference/status checks; separate synthetic legs have execution/legging risk. Ten CME depth levels only. No claim that displayed quantities can be filled, and no prediction-market depth claim. Fees are not inferred: a per-spread dollar fee C adds 100*C/(1041.75*span) cents per 25bp equivalent, independent of lot count if proportional. Equal per-spread route fees cancel in quote comparisons; different route fees must be deducted before choosing.')


def daily_study(cal):
    z=pd.read_parquet(D/'raw/cme/databento/zq_outrights.parquet')
    assert not z.duplicated(['trade_date','dm']).any()
    panel=z.pivot(index='trade_date',columns='dm',values='price')
    common=cal.pivot(index='meeting_date',columns='instrument',values='exact_zero_other_meeting_loading').all(axis=1)
    rows=[]
    for md in common[common].index:
        g=cal[cal.meeting_date.eq(md)].set_index('instrument')
        front=g.loc['FRONT'];back=g.loc['BACK'];date=pd.Timestamp(md)
        use=panel.loc[(panel.index>=date-pd.Timedelta(days=21))&(panel.index<date)]
        cols=[back.near_month,front.near_month,front.far_month]
        if not all(x in use for x in cols):continue
        use=use[cols].dropna()
        # Expired previous-month futures are not an executable alternative.
        month_last=pd.Period(back.near_month).end_time.normalize()
        use=use[use.index<=month_last]
        if back.near_expiration_utc:
            exp=pd.Timestamp(back.near_expiration_utc).tz_convert('America/New_York').normalize().tz_localize(None)
            use=use[use.index<=exp]
        if not len(use): continue
        f=(use[front.near_month]-use[front.far_month])*100/front.span
        b=(use[back.near_month]-use[back.far_month])*100/back.span
        difference=f-b
        rows.append(dict(meeting_date=md,matched_preexpiry_daily_settlements=len(use),
            first_date=use.index.min().date().isoformat(),last_date=use.index.max().date().isoformat(),
            front_span=front.span,back_span=back.span,
            back_newly_admitted_by_exact_loading=not back.repo_calendar_eligible,
            **quant(difference,'front_minus_back_implied_bp'),
            **quant(abs(difference)*4,'absolute_front_back_difference_cents_per25'),
            limitation='Daily final settlements, not synchronous executable intraday quotes; no route-selection or profit conclusion.'))
    pd.DataFrame(rows).to_csv(O/'cme_choice_daily_alternative_diagnostics.csv',index=False)
    return rows


if __name__=='__main__':
    p=pd.read_parquet(W/'cme_minute_panel.parquet')
    assert len(p)==117390 and p.meeting_date.nunique()==26
    assert p.listed_to_index_price_factor.eq(.01).all()
    cal,calsummary=calendar_study(p)
    result=dict(rows=len(p),meetings=p.meeting_date.nunique(),calendar=calsummary,
        route=route_study(p),daily_alternatives=daily_study(cal),
        selection_rule='At each anchor, compute target-decision loading and all other-meeting loadings from the known calendar; admit only a nonzero target loading with zero other loadings, live unexpired definitions, usable statuses/books, freshness and required depth. Normalize dollars, bid/ask and costs by 1041.75*span. Rank the current net buy/sell edge across admissible constructions and routes for the chosen size. No subsequent decision, terminal drift or realized P&L enters selection. Cross-construction alternatives here lack intraday quotes and are not fabricated.')
    (W/'cme_choice_summary.json').write_text(json.dumps(native(result),indent=2))
    print(json.dumps(native({'calendar':calsummary,'baseline':result['route']['baseline'],'daily':result['daily_alternatives']}),indent=2))
