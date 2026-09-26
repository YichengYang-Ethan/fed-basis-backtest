"""Read-only Polymarket event panel for the fixed candidate_26 research universe.

Outputs only into this workspace's work/ directory.  No network, credentials,
repository mutations, normalization as a trading price, or executable-price claim.
"""
from __future__ import annotations
import os
from pathlib import Path

from collections import Counter, deque
from pathlib import Path
import hashlib
import json
import math
import re

import duckdb
import numpy as np
import pandas as pd

W = Path(__file__).resolve().parent
ROOT = W.parent
RAW = Path(os.environ['FOMC_DATA_ROOT']) / 'raw/poly'
ET = 'America/New_York'
AGES = (60, 300)


def stamp(value):
    if value is None or pd.isna(value) or not str(value).strip():
        return pd.NaT
    return pd.to_datetime(value, utc=True, errors='coerce', format='mixed')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def serial(value):
    if isinstance(value, (pd.Timestamp, Path)):
        return str(value)
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(type(value).__name__)


def json_clean(value):
    """Strict JSON: missing values are null, never NaN/Infinity tokens."""
    if isinstance(value, dict):
        return {str(k):json_clean(v) for k,v in value.items()}
    if isinstance(value, (list,tuple)):
        return [json_clean(v) for v in value]
    if value is None or value is pd.NaT:
        return None
    if isinstance(value, (float,np.floating)) and not np.isfinite(value):
        return None
    if isinstance(value, (pd.Timestamp,Path)):
        return str(value)
    if isinstance(value,np.generic):
        return value.item()
    return value


def parse_bin(row):
    """Preserve original labels; independently recover rule intervals where known.

    rule bounds describe actual moves; representative_bp describes the displayed
    25bp-grid convention.  They intentionally differ for rounded/range contracts.
    """
    detail = str(row.outcome_detail)
    text = str(row.description or '').replace('\\n', '\n')
    low = text.lower()
    item = {
        'bin_label': detail, 'bin_kind': 'unmapped',
        'representative_bp': None, 'tail_direction': 0,
        'rule_lower_bp': None, 'rule_upper_bp': None,
        'lower_inclusive': False, 'upper_inclusive': False,
        'rounded_to_25bp': 'rounded up to the nearest 25' in low,
        'rule_parse_source': 'description',
        'rules_core_ambiguous': False, 'rules_wording_inconsistency': False,
        'rule_notes': [],
        'no_statement_resolution': ('each_market_50_50' if '50-50' in low else
                                    'hold_bracket' if 'no statement' in low and
                                    ('no change' in low or 'no-change' in low) else 'unverified'),
    }
    evidence = [line.strip() for line in text.splitlines()
                if ('resolve to' in line.lower() or 'rounded up' in line.lower())]
    item['resolution_evidence'] = '\n'.join(evidence)
    if detail == 'CATCHALL_OTHER_LEVEL':
        item['bin_kind'] = 'catchall_other_level'
        item['rule_notes'].append('Residual nonstandard rate move has no unique bp representative.')
        return item
    match = re.fullmatch(r'(CUT|HIKE)(\d+)(P?)', detail)
    if detail in ('HOLD', 'HOLD_0BP'):
        n, sign, plus = 0, 0, False
    elif match:
        n = int(match.group(2)); sign = -1 if match.group(1) == 'CUT' else 1
        plus = bool(match.group(3))
    else:
        return item
    item['representative_bp'] = sign*n
    item['tail_direction'] = sign if plus else 0
    if sign == 0:
        a = b = 0.; ai = bi = True; kind = 'exact'
    elif item['rounded_to_25bp']:
        # The local text explicitly uses 12.5 -> 25 for cuts and hikes: rounding
        # is on the magnitude, not arithmetic ceiling on the signed change.
        if sign > 0:
            a, b, ai, bi = n-25., math.inf if plus else float(n), False, not plus
        else:
            a, b, ai, bi = -math.inf if plus else -float(n), -(n-25.), not plus, False
        kind = 'rounded_open_tail' if plus else 'rounded_interval'
    else:
        # Read numerical rule wording instead of treating the canonical enum
        # as a payoff definition.  Several stored labels differ from rules.
        between = re.search(r'between (\d+) \(inclusive\) and (\d+) \(inclusive\)', low)
        tail = re.search(r'(?:increased|decreased) by (\d+) or more basis points', low)
        exact = re.search(r'(?:increased|decreased)(?: by)? exactly (\d+) basis points', low)
        if between:
            lo, hi = map(float, between.groups())
            a, b = (lo, hi) if sign > 0 else (-hi, -lo)
            ai = bi = True; kind = 'interval'
            if 'inclusive) or more' in low:
                item['rules_wording_inconsistency'] = True
                item['rule_notes'].append('Literal bounded interval is followed by redundant or-more wording; interval interpretation is a convention.')
        elif tail:
            boundary = float(tail.group(1))
            a, b = (boundary, math.inf) if sign > 0 else (-math.inf, -boundary)
            ai, bi = (True, False) if sign > 0 else (False, True)
            kind = 'open_tail'
            if not plus:
                item['rules_core_ambiguous'] = True
                item['rule_notes'].append('Exact-size question/label conflicts with or-more description.')
            if boundary != n:
                item['rule_notes'].append(f'Rule boundary {boundary:g}bp differs from displayed {n}bp label; representative remains displayed-grid convention.')
        elif exact:
            a = b = sign*float(exact.group(1)); ai = bi = True; kind = 'exact'
            if plus or float(exact.group(1)) != n:
                item['rules_core_ambiguous'] = True
                item['rule_notes'].append('Displayed label and exact description disagree.')
        else:
            item['bin_kind'] = 'unverified_numeric_label'
            item['rules_core_ambiguous'] = True
            item['rule_notes'].append('Numerical label is readable, but payoff interval was not independently parsed.')
            return item
        if sign > 0 and re.search(r'increased by [^.]+ below the level', low):
            item['rules_wording_inconsistency'] = True
            item['rule_notes'].append('Description says increased but also below; positive direction follows question and increased verb.')
    item.update(bin_kind=kind, rule_lower_bp=a, rule_upper_bp=b,
                lower_inclusive=ai, upper_inclusive=bi)
    return item


def contains(b, xs):
    if b['rule_lower_bp'] is None:
        return np.zeros(len(xs), dtype=bool)
    lo, hi = b['rule_lower_bp'], b['rule_upper_bp']
    return ((xs >= lo) if b['lower_inclusive'] else (xs > lo)) & ((xs <= hi) if b['upper_inclusive'] else (xs < hi))


def event_rules(g):
    bins = g.to_dict('records')
    finite = sorted({v for b in bins for v in (b['rule_lower_bp'], b['rule_upper_bp'])
                     if v is not None and np.isfinite(v)})
    probe = np.array([-10000.,10000.] + finite + [(a+b)/2 for a,b in zip(finite[:-1],finite[1:])])
    hit = sum((contains(b, probe).astype(int) for b in bins), np.zeros(len(probe), int))
    grid = np.arange(-10000.,10000.1,25.)
    gridhit = sum((contains(b, grid).astype(int) for b in bins), np.zeros(len(grid), int))
    # This is an explicit finite-grid audit plus inspection of both open tails,
    # not a claim about unlisted outcomes or future contract amendments.
    tail_lo = any(b['rule_lower_bp'] == -math.inf for b in bins)
    tail_hi = any(b['rule_upper_bp'] == math.inf for b in bins)
    catchall = any(b['bin_kind'] == 'catchall_other_level' for b in bins)
    mutual = bool((hit <= 1).all())
    complete_grid = bool(tail_lo and tail_hi and (gridhit == 1).all())
    complete_real = bool(tail_lo and tail_hi and (hit == 1).all())
    if catchall and mutual and tail_lo and tail_hi:
        # The literal residual question is for moves "in between the other
        # brackets"; it fills holes, but cannot receive one policy-bp weight.
        complete_real = True
    known = bool(g.representative_bp.notna().all())
    core = bool(g.rules_core_ambiguous.any() or not mutual)
    return dict(n_legs=len(g), n_tokens=g.yes_token_id.nunique(),
                ladder_signature='|'.join(g.bin_label), all_known_labels=known,
                has_catchall=catchall, has_open_tails=tail_lo or tail_hi,
                mutually_exclusive=mutual, exhaustive_on_25bp_grid=complete_grid,
                exhaustive_real_line_under_rule_reading=complete_real,
                rules_core_ambiguous=core,
                rules_wording_inconsistency=bool(g.rules_wording_inconsistency.any()),
                rules_ambiguous=bool(core or g.rules_wording_inconsistency.any()),
                no_statement_resolution='|'.join(sorted(g.no_statement_resolution.unique())),
                lifetime_start_metadata_complete=bool(g.lifetime_start_metadata_known.all()))


def rolling_history(t, p):
    """O(n) distinct-value count and changes in strictly trailing 60 minutes."""
    q = deque(); counts = Counter(); changes = deque()
    out = np.zeros((len(t), 4), float)
    previous = None
    for i, (ts, value) in enumerate(zip(t, p)):
        cutoff = ts-3600
        while q and q[0][0] < cutoff:
            _, old = q.popleft(); counts[old] -= 1
            if not counts[old]: del counts[old]
        while changes and changes[0] < cutoff: changes.popleft()
        if np.isfinite(value) and 0 <= value <= 1:
            key = round(float(value), 12)
            if previous is not None and key != previous: changes.append(ts)
            q.append((ts,key)); counts[key] += 1; previous = key
        out[i] = [len(q), len(counts), len(changes), counts.get(.5,0)]
    return out


def main():
    manifest_path = ROOT/'outputs/data_acquisition_manifest.csv'
    manifests = pd.read_csv(manifest_path)
    windows = manifests.loc[manifests.stage.eq('candidate_26'),['start','end']].drop_duplicates().sort_values('end')
    assert len(windows) == 26
    windows = windows.rename(columns={'end':'meeting_date','start':'window_start'})
    markets = pd.read_parquet(RAW/'markets.parquet')
    selected = markets.loc[markets.fomc_date.isin(windows.meeting_date)].copy()
    # No selection by eventual volume, winning outcome, future price variability,
    # or existing is_primary ranking.  Include full event if it has direct rate legs.
    event_ids = selected.loc[selected.outcome_detail.str.match(r'^(CUT|HIKE)\d+P?$|^HOLD(?:_0BP)?$',na=False),'event_id'].unique()
    excluded_events = sorted(set(selected.event_id)-set(event_ids))
    selected = selected[selected.event_id.isin(event_ids)].copy()
    assert not selected.duplicated(['event_id','market_id']).any()
    assert not selected.yes_token_id.fillna('').eq('').any()
    selected = selected.rename(columns={'fomc_date':'meeting_date'}).merge(windows,on='meeting_date',validate='many_to_one')
    selected['window_start_ts'] = pd.to_datetime(selected.window_start,utc=True).astype('int64')//10**9
    selected['window_end_ts'] = pd.to_datetime(selected.meeting_date,utc=True).astype('int64')//10**9

    con = duckdb.connect(':memory:')
    con.register('chosen_markets',selected[['event_id','market_id','yes_token_id','window_start_ts','window_end_ts']])
    prices_path = str(RAW/'prices.parquet').replace("'","''")
    token_bounds = con.execute(f"""SELECT p.token_id,min(ts) first_ts,max(ts) last_ts,count(*) all_rows
        FROM read_parquet('{prices_path}') p JOIN chosen_markets m ON p.token_id=m.yes_token_id
        GROUP BY p.token_id""").fetchdf().set_index('token_id')
    raw = con.execute(f"""SELECT p.token_id,p.event_id,p.market_id,p.ts,p.p,p.fidelity
        FROM read_parquet('{prices_path}') p JOIN chosen_markets m
        ON p.token_id=m.yes_token_id AND p.event_id=m.event_id AND p.market_id=m.market_id
        WHERE p.outcome_label='Yes' AND p.ts>=m.window_start_ts AND p.ts<m.window_end_ts
        ORDER BY p.token_id,p.ts,p.fidelity""").fetchdf()
    con.close()
    assert set(raw.fidelity.unique()).issubset({1,60})
    raw['valid_probability'] = raw.p.between(0,1) & raw.p.notna()
    duplicate = raw.groupby(['token_id','ts'],sort=False).agg(rows=('p','size'),n_fidelities=('fidelity','nunique'),
                                                               p_min=('p','min'),p_max=('p','max'))
    duplicate = duplicate[duplicate.rows>1].copy()
    duplicate['conflict'] = (duplicate.p_max-duplicate.p_min).abs()>1e-12
    same_source_dupes = raw.duplicated(['token_id','ts','fidelity'],keep=False)
    duplicate.reset_index().to_parquet(W/'poly_duplicate_checks.parquet',index=False)
    # Prefer fidelity=1 only when observations have the exact same token and ts;
    # for different timestamps latest available observation controls.
    raw = raw.sort_values(['token_id','ts','fidelity','p'],kind='stable').drop_duplicates(['token_id','ts'],keep='first')
    raw['observed_utc'] = pd.to_datetime(raw.ts,unit='s',utc=True)
    raw['date_et'] = raw.observed_utc.dt.tz_convert(ET).dt.strftime('%Y-%m-%d')
    raw['local_minute'] = raw.observed_utc.dt.tz_convert(ET).dt.hour*60+raw.observed_utc.dt.tz_convert(ET).dt.minute
    raw_by_token = {t:g for t,g in raw[raw.local_minute.between(9*60,15*60)].groupby('token_id',sort=False)}

    parsed = pd.DataFrame([parse_bin(r) for r in selected.itertuples()], index=selected.index)
    selected = pd.concat([selected,parsed],axis=1)
    starts=[]; ends=[]; start_sources=[]; end_sources=[]; start_known=[]
    for r in selected.itertuples():
        start = stamp(r.gamma_start_date); ss='gamma_start_date'
        if pd.isna(start): start=stamp(r.start_date); ss='start_date_fallback'
        known=not pd.isna(start)
        if pd.isna(start) and r.yes_token_id in token_bounds.index:
            start=pd.to_datetime(token_bounds.loc[r.yes_token_id,'first_ts'],unit='s',utc=True)
            ss='first_observed_price_conservative_lower_bound'
        end=stamp(r.closed_time); es='closed_time'
        if pd.isna(end): end=stamp(r.gamma_end_date); es='gamma_end_date_fallback'
        if pd.isna(end): end=stamp(r.end_date); es='end_date_fallback'
        fetched=pd.to_datetime(r.fetched_at,unit='s',utc=True)
        if pd.isna(end) or fetched < end: end=fetched; es='fetched_at_cap'
        starts.append(start);ends.append(end);start_sources.append(ss);end_sources.append(es);start_known.append(known)
    selected['lifetime_start']=starts;selected['lifetime_end']=ends
    selected['lifetime_start_source']=start_sources;selected['lifetime_end_source']=end_sources
    selected['lifetime_start_metadata_known']=start_known
    selected=selected.sort_values(['meeting_date','event_id','representative_bp','bin_label'],na_position='last')

    event_frames=[];leg_frames=[]; summaries=[];rule_records=[]
    for (meeting,event_id),g in selected.groupby(['meeting_date','event_id'],sort=True):
        days=pd.date_range(g.window_start.iloc[0],pd.Timestamp(meeting)-pd.Timedelta(days=1),freq='B')
        anchors=pd.DatetimeIndex(np.concatenate([pd.date_range(str(d.date())+' 10:00',str(d.date())+' 15:00',freq='min',tz=ET).tz_convert('UTC').values for d in days])).tz_localize('UTC')
        grid=pd.DataFrame({'anchor_utc':anchors})
        grid['anchor_ts']=grid.anchor_utc.astype('int64')//10**9
        grid['date_et']=grid.anchor_utc.dt.tz_convert(ET).dt.strftime('%Y-%m-%d')
        grid['meeting_date']=meeting;grid['event_id']=event_id
        rules=event_rules(g)
        rule_records.append({'meeting_date':meeting,'event_id':event_id,**rules})
        for k,v in rules.items():grid[k]=v
        grid['days_to_meeting']=(pd.Timestamp(meeting)-pd.to_datetime(grid.date_et)).dt.days
        grid['price_kind']='historical_midpoint_proxy'
        grid['executable_edge_supported']=False
        this_legs=[]
        for r in g.itertuples():
            l=grid[['anchor_utc','anchor_ts','date_et','meeting_date','event_id']].copy()
            l['market_id']=r.market_id;l['token_id']=r.yes_token_id;l['bin_label']=r.bin_label
            l['representative_bp']=r.representative_bp;l['tail_direction']=r.tail_direction
            l['lifetime_start']=r.lifetime_start;l['lifetime_end']=r.lifetime_end
            l['lifetime_start_metadata_known']=r.lifetime_start_metadata_known
            obs=raw_by_token.get(r.yes_token_id,raw.iloc[:0]).copy()
            if pd.notna(r.lifetime_start):obs=obs[obs.observed_utc>=r.lifetime_start]
            if pd.notna(r.lifetime_end):obs=obs[obs.observed_utc<=r.lifetime_end]
            obs=obs[obs.local_minute.between(9*60,15*60)]
            diag=[]
            for _,d in obs.groupby('date_et',sort=True):
                d=d.sort_values('ts').copy()
                d[['trail60_n_obs','trail60_unique_p','trail60_n_changes','trail60_n_half']]=rolling_history(d.ts.to_numpy(),d.p.to_numpy())
                diag.append(d)
            cols=['date_et','ts','p','fidelity','valid_probability','trail60_n_obs','trail60_unique_p','trail60_n_changes','trail60_n_half']
            if diag:
                obs=pd.concat(diag,ignore_index=True)[cols].sort_values('ts')
                l=pd.merge_asof(l.sort_values('anchor_ts'),obs,left_on='anchor_ts',right_on='ts',by='date_et',direction='backward')
            else:
                for k in cols[1:]:l[k]=np.nan
            l=l.rename(columns={'ts':'observed_ts','p':'mid_raw'})
            l['age_sec']=l.anchor_ts-l.observed_ts
            l['in_lifetime']=l.anchor_utc.between(r.lifetime_start,r.lifetime_end) if pd.notna(r.lifetime_start) and pd.notna(r.lifetime_end) else False
            l['same_et_day']=l.observed_ts.notna() # asof join explicitly uses date_et
            l['is_half']=l.mid_raw.eq(.5)
            l['inplay_now']=l.mid_raw.between(.02,.98,inclusive='both')
            l['trail60_constant_inplay']=l.inplay_now & l.trail60_n_obs.ge(20) & l.trail60_unique_p.eq(1)
            l['source_resolution']=np.where(l.fidelity.eq(1),'minute',np.where(l.fidelity.eq(60),'hourly','none'))
            for age in AGES:
                ok=l.observed_ts.notna() & l.age_sec.between(0,age) & l.in_lifetime & l.valid_probability.fillna(False).astype(bool)
                l[f'usable_{age}s']=ok
                l[f'mid_{age}s']=l.mid_raw.where(ok)
                reason=np.select([~l.in_lifetime,l.observed_ts.isna(),~l.valid_probability.fillna(False).astype(bool),l.age_sec.gt(age)],
                                 ['outside_lifetime','no_previous_same_day','invalid_probability','stale'],default='usable')
                l[f'drop_reason_{age}s']=reason
            this_legs.append(l);leg_frames.append(l)
        wide=pd.concat(this_legs,ignore_index=True)
        n=len(g); known=g.representative_bp.notna(); nknown=int(known.sum())
        for age in AGES:
            s=f'{age}s';piv=wide.pivot(index='anchor_ts',columns='token_id',values=f'mid_{s}').reindex(grid.anchor_ts)
            reps=g.set_index('yes_token_id').representative_bp
            tails=g.set_index('yes_token_id').tail_direction
            reps=reps.reindex(piv.columns);tails=tails.reindex(piv.columns)
            good=piv.notna().sum(axis=1).eq(n)
            numeric_good=piv.loc[:,reps.notna()].notna().sum(axis=1).eq(nknown)
            numeric_weighted=piv.mul(reps,axis=1).sum(axis=1,min_count=nknown).where(numeric_good)
            sum_mid=piv.sum(axis=1,min_count=n).where(good)
            raw_weighted=numeric_weighted.where(good & rules['all_known_labels'])
            grid[f'n_valid_legs_{s}']=piv.notna().sum(axis=1).to_numpy()
            grid[f'all_legs_fresh_{s}']=good.to_numpy()
            grid[f'sum_mid_observed_{s}']=piv.sum(axis=1,min_count=1).to_numpy()
            grid[f'sum_mid_{s}']=sum_mid.to_numpy()
            grid[f'known_labels_weighted_move_bp_{s}']=numeric_weighted.to_numpy()
            grid[f'raw_weighted_move_bp_{s}']=raw_weighted.to_numpy()
            grid[f'normalized_move_bp_{s}']=(raw_weighted/sum_mid.where(sum_mid.ne(0))).to_numpy()
            grid[f'tail_probability_signed_{s}']=piv.mul(tails,axis=1).sum(axis=1,min_count=n).where(good).to_numpy()
            grid[f'cut_tail_probability_{s}']=piv.loc[:,tails.lt(0)].sum(axis=1).where(good).to_numpy()
            grid[f'hike_tail_probability_{s}']=piv.loc[:,tails.gt(0)].sum(axis=1).where(good).to_numpy()
            grid[f'usable_label_convention_{s}']=(good & rules['all_known_labels'] & (not rules['rules_core_ambiguous'])).to_numpy()
            grid[f'usable_strict_ladder_{s}']=(good & rules['all_known_labels'] & rules['exhaustive_on_25bp_grid'] & (not rules['rules_ambiguous'])).to_numpy()
            fresh=wide[wide[f'usable_{s}']]
            agg=fresh.groupby('anchor_ts').agg(max_age_sec=('age_sec','max'),n_half=('is_half','sum'),
                                              n_hourly=('fidelity',lambda x:int(x.eq(60).sum())),
                                              n_constant_inplay=('trail60_constant_inplay','sum'))
            for k in agg.columns:grid[f'{k}_{s}']=grid.anchor_ts.map(agg[k]).fillna(0 if k!='max_age_sec' else np.nan)
            grid[f'all_legs_minute_{s}']=grid[f'all_legs_fresh_{s}'] & grid[f'n_hourly_{s}'].eq(0)
            holds=wide[wide.bin_label.isin(['HOLD','HOLD_0BP'])].set_index('anchor_ts')
            assert not holds.index.duplicated().any()
            grid[f'hold_mid_{s}']=grid.anchor_ts.map(holds[f'mid_{s}'])
        holds=wide[wide.bin_label.isin(['HOLD','HOLD_0BP'])].set_index('anchor_ts')
        for src,dst in [('age_sec','hold_age_sec'),('observed_ts','hold_observed_ts'),('token_id','hold_token_id'),
                        ('fidelity','hold_source_fidelity'),('trail60_unique_p','hold_trail60_unique_p'),('trail60_constant_inplay','hold_trail60_constant_inplay')]:
            grid[dst]=grid.anchor_ts.map(holds[src])
        assert len(grid)==15*301
        assert grid.anchor_utc.dt.tz_convert(ET).dt.strftime('%Y-%m-%d').lt(meeting).all()
        event_frames.append(grid)
        summaries.append({'meeting_date':meeting,'event_id':event_id,**rules,'anchors':len(grid),
                          **{f'fresh_{a}s':int(grid[f'all_legs_fresh_{a}s'].sum()) for a in AGES},
                          **{f'label_usable_{a}s':int(grid[f'usable_label_convention_{a}s'].sum()) for a in AGES},
                          **{f'strict_usable_{a}s':int(grid[f'usable_strict_ladder_{a}s'].sum()) for a in AGES},
                          'first_anchor_utc':str(grid.anchor_utc.min()),'last_anchor_utc':str(grid.anchor_utc.max())})
        print(json.dumps({'meeting':meeting,'legs':n,'fresh60':summaries[-1]['fresh_60s'],'strict60':summaries[-1]['strict_usable_60s']}),flush=True)
    panel=pd.concat(event_frames,ignore_index=True)
    legs=pd.concat(leg_frames,ignore_index=True)
    assert not panel.duplicated(['meeting_date','event_id','anchor_ts']).any()
    assert not legs.duplicated(['meeting_date','event_id','token_id','anchor_ts']).any()
    assert legs.loc[legs.observed_ts.notna(),'age_sec'].ge(0).all()
    observed_dates=pd.to_datetime(legs.observed_ts.dropna(),unit='s',utc=True).dt.tz_convert(ET).dt.strftime('%Y-%m-%d')
    assert observed_dates.eq(legs.loc[observed_dates.index,'date_et']).all()
    assert panel[['meeting_date','anchor_ts']].drop_duplicates().shape[0] == 117390
    panel.to_parquet(W/'poly_event_panel.parquet',index=False)
    legs.to_parquet(W/'poly_leg_panel.parquet',index=False)
    pd.DataFrame(summaries).to_csv(W/'poly_event_coverage.csv',index=False)
    # Rules provenance is deliberately kept in work, not copied into public output.
    mapcols=['meeting_date','event_id','market_id','yes_token_id','question','bin_label','bin_kind','representative_bp',
             'tail_direction','rule_lower_bp','rule_upper_bp','lower_inclusive','upper_inclusive','rounded_to_25bp',
             'rules_core_ambiguous','rules_wording_inconsistency','rule_notes','resolution_evidence','no_statement_resolution',
             'lifetime_start','lifetime_end','lifetime_start_source','lifetime_end_source','lifetime_start_metadata_known','fetched_at']
    mappings=selected[mapcols].replace({np.inf:None,-np.inf:None}).to_dict('records')
    for item,(_,original) in zip(mappings,selected.iterrows()):
        item['lower_unbounded']=original.rule_lower_bp == -math.inf
        item['upper_unbounded']=original.rule_upper_bp == math.inf
    drops={f'{a}s':{str(k):int(v) for k,v in legs[f'drop_reason_{a}s'].value_counts().items()} for a in AGES}
    metadata={
        'created_at_utc':str(pd.Timestamp.now(tz='UTC')),
        'source_files':{str(p):{'sha256':sha(p)} for p in [RAW/'markets.parquet',RAW/'prices.parquet',manifest_path]},
        'scope':'Fixed candidate_26, full event ladders, no future-volume primary selection; all raw values remain under work/.',
        'grid':'Weekdays Monday-Friday, ET 10:00 through 15:00 inclusive, exact minutes; 21 calendar days before meeting, meeting date excluded. Holidays remain explicit candidate anchors.',
        'timestamp_convention':'Stored ts treated as observation time; backward asof ts <= anchor only. Exact API publication delay was not recorded. Original repository reader optionally adds one fidelity period; that conservative alternative is not the base panel.',
        'source_selection':'At identical token+ts, fidelity1 takes priority over fidelity60; otherwise latest timestamp controls. Source fidelity is retained. Hourly history is never filled for a whole hour: same 60s/300s freshness gates apply.',
        'lifetime_policy':'gamma_start_date then start_date; if absent, first observed price is a conservative existence lower bound and metadata_known remains false. closed_time then expected end, capped at fetched_at. No across-day fill.',
        'zero_point_five_policy':'Keep valid 0.5; flag half and trailing-only constant-in-play (>=20 valid observations, one distinct value in previous60min ending at the selected observation timestamp). The diagnostic is available as of that observation, never uses subsequent history, and is at most the permitted quote age old. These flags diagnose, not prove, placeholders.',
        'weights':'Use per-market displayed bp labels; no five-bin collapse. Open tails start at displayed minimum representative; +25/+50bp outward sensitivity equals raw_weighted_move + 25/50 * tail_probability_signed. Bounded intervals use displayed endpoint labels. Catchall has no unique representative.',
        'interpretation':'raw_weighted_move is an unnormalized price-weighted representative payoff proxy, not an executable price or uniquely identified expectation. normalized_move is diagnostic only. Incomplete ladders, special no-statement settlements, rule revisions and risk premia limit cross-venue expectation interpretation.',
        'availability_limitation':'Historical midpoint proxies only; no historical bid/ask, depth, receive timestamps or executable-edge validation.',
        'rules_version_limitation':'Descriptions are the stored fetched snapshot, not proven point-in-time amendment histories. Numerical rule parser flags contradictory wording rather than silently repairing it.',
        'raw_selected_rows_before_dedup':int(len(raw)+duplicate.rows.sub(1).sum()),
        'duplicate_token_ts_keys':len(duplicate),'duplicate_token_ts_conflicting_keys':int(duplicate.conflict.sum()),
        'same_source_duplicate_rows':int(same_source_dupes.sum()),
        'duplicate_resolution_detail_file':'work/poly_duplicate_checks.parquet',
        'raw_selected_invalid_probability_rows_after_dedup':int((~raw.valid_probability).sum()),
        'events':len(summaries),'meetings':panel.meeting_date.nunique(),'event_anchor_rows':len(panel),'leg_anchor_rows':len(legs),
        'unique_meeting_anchors':panel[['meeting_date','anchor_ts']].drop_duplicates().shape[0],
        'excluded_nondecision_events':excluded_events,'drop_reason_leg_counts':drops,
        'event_summaries':summaries,'event_rule_flags':rule_records,'market_mapping':mappings,
        'event_columns':{k:str(v) for k,v in panel.dtypes.items()},'leg_columns':{k:str(v) for k,v in legs.dtypes.items()},
        'validation':{'future_join_rows':0,'cross_et_day_join_rows':0,'duplicate_event_anchor_rows':0,'duplicate_leg_anchor_rows':0,
                      'all_candidate_meeting_anchors_present':True},
    }
    (W/'poly_analysis_metadata.json').write_text(json.dumps(json_clean(metadata),indent=2,allow_nan=False))
    print(json.dumps({'event_rows':len(panel),'leg_rows':len(legs),'drops':drops}),flush=True)


if __name__ == '__main__':
    main()
