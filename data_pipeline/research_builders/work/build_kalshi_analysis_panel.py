#!/usr/bin/env python3
"""Local research panel; quote scenarios, never a claim of executable arbitrage."""
import os
from pathlib import Path
import datetime as dt
from fractions import Fraction
import hashlib
import json
import math
import pathlib
import re
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore', category=pd.errors.PerformanceWarning)

WORK = pathlib.Path(__file__).resolve().parent
RAW = Path(os.environ['FOMC_DATA_ROOT']) / 'raw/kalshi/research_backfill_20260917'
INSTRUMENTS = Path(os.environ['FOMC_PROJECT_ROOT']) / 'data/fomc_instruments.csv'
AGES = (0, 60, 300)
RULE_RE = re.compile(r'\b(Cut|Hike)\s+of\s*([<>]=?)?\s*(\d+(?:\.\d+)?)\s*bps', re.I)
TITLE_RE = re.compile(r'\b(Cut|Hike)\s+rates\s+by\s*([<>]=?)?\s*(\d+(?:\.\d+)?)', re.I)


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def parsed_match(match):
    if not match:
        return None
    return (match[1].lower(), match[2] or '=', float(match[3]))


def parse_leg(m):
    primary = parsed_match(RULE_RE.search(m.get('rules_primary', '')))
    title = parsed_match(TITLE_RE.search(m.get('title', '')))
    custom = None
    custom_obj = m.get('custom_strike')
    if isinstance(custom_obj, dict) and len(custom_obj) == 1:
        direction, text = next(iter(custom_obj.items()))
        match = re.fullmatch(r'\s*([<>]=?)?\s*(\d+(?:\.\d+)?)\s*(?:bps)?\s*', str(text), re.I)
        if match and direction.lower() in ('cut', 'hike'):
            custom = (direction.lower(), match[1] or '=', float(match[2]))
    # Never infer a strike from the ticker suffix. A primary/custom conflict is flagged,
    # with a conditional structured+title interpretation kept for transparent diagnostics.
    conditional = custom if custom is not None and title == custom else primary
    conflict = any(x is not None and conditional is not None and x != conditional for x in (primary, custom, title))
    valid = conditional is not None and conditional[1] in ('=', '>')
    direction, op, threshold = conditional if valid else (None, None, None)
    sign = -1 if direction == 'cut' else 1
    exact = sign * threshold if valid and op == '=' else None
    representative = (exact if op == '=' else sign * (math.floor(threshold / 25) + 1) * 25) if valid else None
    if valid and op == '>':
        low, high = ((None, -threshold) if sign < 0 else (threshold, None))
    else:
        low = high = exact
    secondary = m.get('rules_secondary', '')
    if re.search(r'all strikes.*resolve to No', secondary, re.I | re.S):
        cancellation = 'all_no'
    elif 'Fed maintains rate' in secondary and 'canceled' in secondary:
        cancellation = 'hold_yes_after_may_2025_per_text'
    else:
        cancellation = 'unspecified'
    payout = m.get('settlement_value_dollars')
    payout = float(payout) if payout not in (None, '') else (1. if m.get('result') == 'yes' else 0. if m.get('result') == 'no' else None)
    return dict(ticker=m['ticker'], event_ticker=m['event_ticker'], title=m.get('title'),
                rules_primary=m.get('rules_primary'), rules_secondary=secondary, custom_strike=custom_obj,
                primary_parsed=primary, custom_parsed=custom, title_parsed=title,
                conditional_rule_parsed=conditional, mapping_valid=valid,
                mapping_strict_ok=bool(valid and not conflict and primary is not None), rules_conflict=conflict,
                direction=direction, operator=op, magnitude_threshold_bp=threshold,
                move_lower_bound_bp=low, move_upper_bound_bp=high,
                lower_bound_inclusive=op == '=', upper_bound_inclusive=op == '=',
                open_tail=bool(valid and op == '>'), move_exact_bp=exact,
                move_bp_proxy_25bp_grid=representative, weight_m=representative/25 if valid else None,
                proxy_requires_25bp_grid=bool(valid and op == '>'),
                cancellation_rule=cancellation, result_ex_post=m.get('result'),
                payout_dollars_ex_post=payout, expiration_value_ex_post=m.get('expiration_value'),
                open_time=m.get('open_time'), close_time=m.get('close_time'))


def exact_realized_move(spec):
    s = (spec['expiration_value_ex_post'] or '').strip()
    if re.fullmatch(r'[+-]?\d+(?:\.\d+)?(?:bps)?', s, re.I):
        return float(re.sub('bps$', '', s, flags=re.I)), 'expiration_value_numeric'
    match = re.fullmatch(r'(Hike|Cut)\s+(\d+(?:\.\d+)?)bps', s, re.I)
    if match:
        return float(match[2]) * (-1 if match[1].lower() == 'cut' else 1), 'expiration_value_named'
    if s.lower() in ('fed maintains rate', 'no hike') and spec['move_exact_bp'] == 0:
        return 0., 'hold_rules_plus_expiration_value'
    if spec['move_exact_bp'] is not None and spec['mapping_strict_ok']:
        return spec['move_exact_bp'], 'winning_exact_rule'
    return None, 'unknown'


def anchors(start, end):
    grid = pd.date_range(start, end, freq='min', inclusive='left', tz='UTC')
    et = grid.tz_convert('America/New_York')
    minute = et.hour*60 + et.minute
    keep = (et.dayofweek < 5) & (minute >= 600) & (minute <= 900)
    grid, et = grid[keep], et[keep]
    return pd.DataFrame({'anchor_ts':grid.asi8//10**9, 'anchor_ts_ns':grid.asi8,
                         'et_date':et.strftime('%Y-%m-%d'), 'et_time':et.strftime('%H:%M')})


def mark_grid_coverage(specs):
    missing = []
    overlapping = []
    for decision in range(-1000, 1001, 25):
        count = 0
        for s in specs:
            if not s['mapping_valid']:
                continue
            mag = -decision if s['direction'] == 'cut' else decision
            count += mag == s['magnitude_threshold_bp'] if s['operator'] == '=' else mag > s['magnitude_threshold_bp']
        if count == 0:
            missing.append(decision)
        if count > 1:
            overlapping.append(decision)
    return missing, overlapping


def fee_cents_per_leg(prices, quantities, half_rate=False):
    # Dollar quotes have at most 4 decimals. Integer arithmetic exactly implements
    # ceil_to_cent(rate*C*p*(1-p)); rate=7/100 or 7/200, before normalization by N.
    finite = np.isfinite(prices)
    price_scaled = np.rint(np.where(finite, prices, 0.) * 10000).astype(np.int64)
    if not np.allclose(prices[finite], price_scaled[finite]/10000, atol=1e-10, rtol=0):
        raise ValueError('Quote precision exceeds exact four-decimal fee implementation')
    denominator = 200_000_000 if half_rate else 100_000_000
    numerator = 7 * quantities * price_scaled * (10000-price_scaled)
    return (numerator + denominator-1)//denominator


def main():
    mapping = json.loads((RAW/'meeting_mapping.json').read_text())
    inventory = json.loads((RAW/'market_inventory.json').read_text())
    all_markets = {m['ticker']:m for records in inventory.values() for m in records}
    instruments = pd.read_csv(INSTRUMENTS).set_index('meeting_date')
    selected = {m['meeting']:m for m in mapping['mappings'] if m['mapping_basis']=='occurrence_or_close_exact_date'}
    excluded = [m for m in mapping['mappings'] if m['mapping_basis']!='occurrence_or_close_exact_date']
    all_leg_frames, event_frames, metadata_events, all_specs = [], [], [], []
    for start, meeting in mapping['windows']:
        grid = anchors(start, meeting)
        grid['meeting_date'] = meeting
        grid['anchor_utc'] = pd.to_datetime(grid.anchor_ts, unit='s', utc=True)
        inst = instruments.loc[meeting]
        w = Fraction(int(inst.days_post), int(inst.days_in_month))
        span_fraction = 1-w if inst.instrument=='FRONT' else w
        n_fraction = Fraction(104175,100)*span_fraction
        n = float(n_fraction)
        event = grid.copy()
        event['span'] = float(span_fraction)
        event['span_csv_display'] = float(inst.span)
        event['N_per_zq'] = n
        event['leg_near'] = inst.leg_near
        event['leg_far'] = inst.leg_far
        event['instrument'] = inst.instrument
        event['realized_change_bp_ex_post'] = float(inst.realized_change_bp)
        event['venue'] = 'kalshi'
        event['publication_delay_unknown'] = True
        event['depth_unknown'] = True
        if meeting not in selected:
            event['event_ticker'] = None
            event['has_market_metadata'] = False
            for age in AGES:
                for key in ('all_nonzero_two_sided', 'long_side_valid', 'short_side_valid', 'all_legs_mid_valid'):
                    event[f'{key}_{age}s'] = False
            event_frames.append(event)
            metadata_events.append({'meeting_date':meeting,'status':'no_mapped_event','anchor_rows':len(grid)})
            continue
        match = selected[meeting]
        specs = [parse_leg(all_markets[t]) for t in match['market_tickers']]
        all_specs.extend(specs)
        event['event_ticker'] = match['event_ticker']
        event['has_market_metadata'] = True
        event['leg_count'] = len(specs)
        event['any_rules_conflict'] = any(s['rules_conflict'] for s in specs)
        event['all_rules_strict_ok'] = all(s['mapping_strict_ok'] for s in specs)
        event['any_open_tail'] = any(s['open_tail'] for s in specs)
        event['linear_payoff_exact_all_states'] = False
        event['nonexhaustive_without_25bp_grid'] = True
        missing_grid, overlapping_grid = mark_grid_coverage(specs)
        event['nonexhaustive_on_25bp_grid'] = bool(missing_grid)
        event['overlapping_on_25bp_grid'] = bool(overlapping_grid)
        cancels = sorted(set(s['cancellation_rule'] for s in specs))
        event['cancellation_rule'] = '|'.join(cancels)
        event['cancel_all_no_risk'] = 'all_no' in cancels
        event['cancel_rule_unknown_or_mixed'] = 'unspecified' in cancels or len(cancels)>1
        event['rules_vintage_unknown'] = True
        winners = [s for s in specs if s['result_ex_post']=='yes']
        event['winner_count_ex_post'] = len(winners)
        actual_move, realized_source = exact_realized_move(winners[0]) if len(winners)==1 else (None,'unknown')
        event['actual_move_from_contract_metadata_bp_ex_post'] = actual_move
        event['actual_move_metadata_source_ex_post'] = realized_source
        event['actual_move_metadata_matches_calendar_ex_post'] = actual_move == float(inst.realized_change_bp) if actual_move is not None else False
        leg_frames = []
        for s in specs:
            file = RAW/'candles'/(s['ticker']+'.parquet')
            if file.exists():
                q = pd.read_parquet(file, columns=['ts','yes_bid_close','yes_ask_close','volume','request_id'])
                q = q[(q.ts >= int(pd.Timestamp(start,tz='UTC').timestamp())) &
                      (q.ts < int(pd.Timestamp(meeting,tz='UTC').timestamp())) &
                      (q.ts <= int(pd.Timestamp(s['close_time']).timestamp()))].copy()
                q = q.rename(columns={'ts':'quote_ts','yes_bid_close':'bid','yes_ask_close':'ask','volume':'observed_volume'})
                q['et_date'] = pd.to_datetime(q.quote_ts,unit='s',utc=True).dt.tz_convert('America/New_York').dt.strftime('%Y-%m-%d')
                leg = pd.merge_asof(grid.sort_values('anchor_ts'), q.sort_values('quote_ts'),
                                    left_on='anchor_ts',right_on='quote_ts',by='et_date',direction='backward',allow_exact_matches=True)
            else:
                leg = grid.copy()
                for col in ('quote_ts','bid','ask','observed_volume'):
                    leg[col] = np.nan
                leg['request_id'] = None
            leg['ticker'] = s['ticker']
            leg['event_ticker'] = s['event_ticker']
            leg['quote_age_s'] = leg.anchor_ts-leg.quote_ts
            leg['same_minute_closed'] = leg.quote_age_s.eq(0)
            leg['closed_asof_anchor'] = leg.quote_ts.le(leg.anchor_ts)
            leg['volume_current_minute'] = leg.observed_volume.where(leg.same_minute_closed)
            leg['mid'] = (leg.bid+leg.ask)/2
            leg['quote_numeric_valid'] = leg.bid.between(0,1)&leg.ask.between(0,1)&leg.bid.le(leg.ask)
            leg['quote_two_sided_interior'] = leg.quote_numeric_valid & leg.bid.gt(0) & leg.ask.lt(1)
            leg['quote_crossed'] = leg.bid.gt(leg.ask)
            for key in ('move_exact_bp','move_bp_proxy_25bp_grid','weight_m','open_tail','rules_conflict','mapping_strict_ok','payout_dollars_ex_post'):
                leg[key] = s[key]
            leg['move_lower_bound_bp'] = s['move_lower_bound_bp']
            leg['move_upper_bound_bp'] = s['move_upper_bound_bp']
            leg['ticker_result_ex_post'] = s['result_ex_post']
            leg['N_per_zq'] = n
            leg['span'] = float(span_fraction)
            q_int = int(n_fraction*Fraction(str(abs(s['weight_m'])))+Fraction(1,2)) if s['weight_m'] is not None else 0
            leg['quantity_integer_abs_per_zq'] = q_int
            leg['quantity_integer_signed_long_per_zq'] = q_int*np.sign(s['weight_m']) if s['weight_m'] is not None else np.nan
            if s['weight_m'] and s['weight_m'] > 0:
                leg['long_required_price'] = leg.ask
                leg['short_required_price'] = leg.bid
                leg['long_required_side_valid'] = leg.ask.ge(0)&leg.ask.lt(1)&~leg.quote_crossed
                leg['short_required_side_valid'] = leg.bid.gt(0)&leg.bid.le(1)&~leg.quote_crossed
                leg['long_implementation'] = 'buy_yes'
                leg['short_implementation'] = 'buy_no_plus_fixed_cash_adjustment'
            elif s['weight_m'] and s['weight_m'] < 0:
                leg['long_required_price'] = leg.bid
                leg['short_required_price'] = leg.ask
                leg['long_required_side_valid'] = leg.bid.gt(0)&leg.bid.le(1)&~leg.quote_crossed
                leg['short_required_side_valid'] = leg.ask.ge(0)&leg.ask.lt(1)&~leg.quote_crossed
                leg['long_implementation'] = 'buy_no_plus_fixed_cash_adjustment'
                leg['short_implementation'] = 'buy_yes'
            else:
                leg['long_required_price'] = np.nan
                leg['short_required_price'] = np.nan
                leg['long_required_side_valid'] = True
                leg['short_required_side_valid'] = True
                leg['long_implementation'] = leg['short_implementation'] = 'zero_weight_not_traded'
            for age in AGES:
                leg[f'fresh_{age}s'] = leg.quote_age_s.between(0,age)
            assert not (leg.quote_ts > leg.anchor_ts).any()
            observed = leg.quote_ts.notna()
            assert (pd.to_datetime(leg.loc[observed,'quote_ts'],unit='s',utc=True).dt.tz_convert('America/New_York').dt.strftime('%Y-%m-%d').values == leg.loc[observed,'et_date'].values).all()
            if s['move_exact_bp']==0:
                event['hold_ticker'] = s['ticker']
                for key in ('bid','ask','mid','quote_ts','quote_age_s','observed_volume','volume_current_minute','same_minute_closed','quote_numeric_valid'):
                    event['hold_'+key] = leg[key].values
            leg_frames.append(leg)
            all_leg_frames.append(leg)
        bid = np.column_stack([x.bid.values for x in leg_frames])
        ask = np.column_stack([x.ask.values for x in leg_frames])
        mids = (bid+ask)/2
        ages = np.column_stack([x.quote_age_s.values for x in leg_frames])
        numeric = np.column_stack([x.quote_numeric_valid.values for x in leg_frames])
        interior = np.column_stack([x.quote_two_sided_interior.values for x in leg_frames])
        crossed = bid>ask
        weights = np.array([s['weight_m'] for s in specs],dtype=float)
        nonzero = weights!=0
        quantities = np.array([int(n_fraction*Fraction(str(abs(x)))+Fraction(1,2)) for x in weights],dtype=np.int64)
        signed_q = quantities*np.sign(weights)
        event['nonzero_leg_count'] = int(nonzero.sum())
        event['needed_leg_max_age_s'] = np.max(ages[:,nonzero],axis=1)
        event['long_needed_leg_max_age_s'] = event.needed_leg_max_age_s
        event['short_needed_leg_max_age_s'] = event.needed_leg_max_age_s
        long_px = np.where(weights>0,ask,bid)
        short_px = np.where(weights>0,bid,ask)
        long_needed_valid = np.where(weights>0,(ask>=0)&(ask<1),(bid>0)&(bid<=1)) & ~crossed
        short_needed_valid = np.where(weights>0,(bid>0)&(bid<=1),(ask>=0)&(ask<1)) & ~crossed
        raw_long = np.sum(np.where(nonzero,weights*long_px,0),axis=1)
        raw_short = np.sum(np.where(nonzero,weights*short_px,0),axis=1)
        rounded_long = np.sum(np.where(nonzero,signed_q/n*long_px,0),axis=1)
        rounded_short = np.sum(np.where(nonzero,signed_q/n*short_px,0),axis=1)
        raw_move = 25*np.sum(np.where(nonzero,weights*mids,0),axis=1)
        raw_sum = np.sum(mids,axis=1)
        long_cash_offset = float(np.sum(np.abs(weights[weights<0])))
        short_cash_offset = float(np.sum(weights[weights>0]))
        event['long_no_cash_offset_per25_dollars'] = long_cash_offset
        event['short_no_cash_offset_per25_dollars'] = short_cash_offset
        event['long_no_cash_offset_rounded_per25_dollars'] = float(quantities[weights<0].sum()/n)
        event['short_no_cash_offset_rounded_per25_dollars'] = float(quantities[weights>0].sum()/n)
        payout = np.array([s['payout_dollars_ex_post'] for s in specs],dtype=float)
        event['linear_proxy_payout_dollars_per25_ex_post'] = float(np.sum(weights*payout))
        event['linear_proxy_payoff_error_bp_ex_post'] = float(25*np.sum(weights*payout)-float(inst.realized_change_bp))
        event['linear_proxy_payout_rounded_lot_dollars_per25_ex_post'] = float(np.sum(signed_q/n*payout))
        event['rounded_lot_payoff_error_bp_ex_post'] = float(25*np.sum(signed_q/n*payout)-float(inst.realized_change_bp))
        event['integer_quantity_max_weight_error_bp'] = float(25*np.max(np.abs(signed_q/n-weights)))
        for age in AGES:
            fresh = (ages>=0)&(ages<=age)
            mids_all_ok = np.all(fresh & numeric,axis=1)
            mids_needed_ok = np.all((fresh & numeric)[:,nonzero],axis=1)
            two_sided = np.all((fresh & interior)[:,nonzero],axis=1)
            long_ok = np.all((fresh & long_needed_valid)[:,nonzero],axis=1)
            short_ok = np.all((fresh & short_needed_valid)[:,nonzero],axis=1)
            event[f'all_legs_mid_valid_{age}s'] = mids_all_ok
            event[f'all_nonzero_two_sided_{age}s'] = two_sided
            event[f'long_side_valid_{age}s'] = long_ok
            event[f'short_side_valid_{age}s'] = short_ok
            event[f'sum_mids_{age}s'] = np.where(mids_all_ok,raw_sum,np.nan)
            event[f'weighted_move_bp_{age}s'] = np.where(mids_needed_ok,raw_move,np.nan)
            event[f'conditional_normalized_move_bp_{age}s'] = np.divide(raw_move,raw_sum,out=np.full(len(event),np.nan),where=mids_all_ok&(raw_sum>0))
            event[f'linear_long_value_dollars_per25_{age}s'] = np.where(long_ok,raw_long,np.nan)
            event[f'linear_short_proceeds_dollars_per25_{age}s'] = np.where(short_ok,raw_short,np.nan)
            event[f'linear_long_value_rounded_lot_dollars_per25_{age}s'] = np.where(long_ok,rounded_long,np.nan)
            event[f'linear_short_proceeds_rounded_lot_dollars_per25_{age}s'] = np.where(short_ok,rounded_short,np.nan)
            event[f'long_fullcash_yes_no_cost_dollars_per25_{age}s'] = np.where(long_ok,raw_long+long_cash_offset,np.nan)
            event[f'short_fullcash_yes_no_cost_dollars_per25_{age}s'] = np.where(short_ok,-raw_short+short_cash_offset,np.nan)
            event[f'long_fullcash_rounded_lot_yes_no_cost_dollars_per25_{age}s'] = np.where(long_ok,rounded_long+quantities[weights<0].sum()/n,np.nan)
            event[f'short_fullcash_rounded_lot_yes_no_cost_dollars_per25_{age}s'] = np.where(short_ok,-rounded_short+quantities[weights>0].sum()/n,np.nan)
            for tag, half_rate in [('070',False),('035',True)]:
                long_fee = np.sum(fee_cents_per_leg(long_px,quantities,half_rate),axis=1)/n
                short_fee = np.sum(fee_cents_per_leg(short_px,quantities,half_rate),axis=1)/n
                event[f'long_fee_rate{tag}_cents_per25_{age}s'] = np.where(long_ok,long_fee,np.nan)
                event[f'short_fee_rate{tag}_cents_per25_{age}s'] = np.where(short_ok,short_fee,np.nan)
        event_frames.append(event)
        metadata_events.append({'meeting_date':meeting,'event_ticker':match['event_ticker'],'anchor_rows':len(event),'leg_count':len(specs),
            'span_numerator':span_fraction.numerator,'span_denominator':span_fraction.denominator,
            'N_numerator':n_fraction.numerator,'N_denominator':n_fraction.denominator,
            'nonexhaustive_grid_missing_test_states_bp':missing_grid,'overlapping_grid_test_states_bp':overlapping_grid,
            'rules_conflict_tickers':[s['ticker'] for s in specs if s['rules_conflict']],
            'candidate_long_rows_by_age':{str(a):int(event[f'long_side_valid_{a}s'].sum()) for a in AGES},
            'candidate_short_rows_by_age':{str(a):int(event[f'short_side_valid_{a}s'].sum()) for a in AGES}})
        print(f'{meeting} legs={len(specs)} long0={int(event.long_side_valid_0s.sum())} short0={int(event.short_side_valid_0s.sum())}',flush=True)
    events = pd.concat(event_frames,ignore_index=True)
    legs = pd.concat(all_leg_frames,ignore_index=True)
    events['sum_mids'] = events.sum_mids_0s
    events['weighted_move_bp'] = events.weighted_move_bp_0s
    events['conditional_normalized_move_bp'] = events.conditional_normalized_move_bp_0s
    events.to_parquet(WORK/'kalshi_event_panel.parquet',index=False,compression='zstd')
    legs.to_parquet(WORK/'kalshi_leg_panel.parquet',index=False,compression='zstd')
    assert not events.duplicated(['meeting_date','anchor_ts']).any()
    assert not legs.duplicated(['ticker','anchor_ts']).any()
    metadata = {'created_at':dt.datetime.now(dt.timezone.utc).isoformat(),'event_rows':len(events),'leg_rows':len(legs),
        'meeting_count':26,'mapped_meeting_count':24,'leg_count':len(all_specs),'excluded_ambiguous_events':excluded,
        'event_summary':metadata_events,'leg_rules':all_specs,'input_hashes':{str(p):sha(p) for p in [RAW/'meeting_mapping.json',RAW/'market_inventory.json',RAW/'manifest.json',INSTRUMENTS]},
        'outputs':{name:{'path':str(WORK/name),'sha256':sha(WORK/name)} for name in ['kalshi_event_panel.parquet','kalshi_leg_panel.parquet']},
        'script_sha256':sha(__file__),
        'anchor_definition':'UTC timestamps whose ET local time is weekday Monday-Friday, 10:00 through 15:00 inclusive, whole minutes, within original [start,end); federal holidays are not independently excluded.',
        'asof_policy':'Backward asof only within the same ET date; quote end_period_ts <= anchor; baseline age=0; 60/300s are explicitly labeled sensitivities; no cross-day fill.',
        'publication_warning':'Candle end is assumed available by anchor for baseline, but actual publication timestamp is unknown. Apply separate lag sensitivity externally.',
        'mapping_policy':'No ticker-suffix parsing. Primary rules, custom_strike and title compared; structured+title consensus is conditional when primary conflicts. Tail proxy is nearest state beyond threshold on a 25bp grid, never exact unbounded-tail replication.',
        'fee_formula':'Scenario per direction/per leg: C_i=round_half_up(N*abs(m_i)); fee_i_dollars=ceil(100*rate*C_i*p_i*(1-p_i))/100; cents_per25=sum(fee_i_dollars)*100/N. Rates 0.07 and 0.035 are assumptions, not verified historical fees. Historical cent versus centicent rounding is disputed; full-cent upward rounding is conservative scenario only.',
        'fee_implementation':'Integer arithmetic at quote precision 1e-4 dollars. rate=.07 fee cents=ceil(7*C*P*(10000-P)/1e8); .035 denominator=2e8. Signed trade directions use each required side; equivalent NO complements yield identical p*(1-p).',
        'negative_weight_identity':'Short q YES at bid b is economically buy q NO at ask 1-b plus a fixed -q cash payoff: NO=1-YES. Long signed linear value plus sum(abs(negative weights)) equals full-cash YES/NO purchase cost; terminal payoff gains the same offset. Short-linear full-cash purchase cost is -short_proceeds + sum(positive weights). Negative signed value is not collateral-free withdrawal; funding/netting/borrow costs and depth remain unknown.',
        'side_gate':'Long needs positive-weight asks<1 and negative-weight bids>0; short needs positive-weight bids>0 and negative-weight asks<1. Prices must be finite/in [0,1], noncrossed if both known. All-nonzero two-sided interior is a separate stricter flag.',
        'ex_post_columns':'All result, payout, realized_change and payoff_error columns labeled ex_post must never select entry observations or model features.',
        'limitations':['No historical sizes, fill guarantees, fees vintage, margin/funding costs or publication timestamps.','All events have open tails: these are bounded-proxy quote packages, not exact linear payoffs for all decisions.','Current rule snapshots and changed cancellation rules are not point-in-time validated.','Missing archive rows remain unknown, never declared illiquid merely because no candle exists.']}
    (WORK/'kalshi_analysis_metadata.json').write_text(json.dumps(metadata,indent=2,ensure_ascii=False,allow_nan=False)+'\n')
    print(json.dumps({'event_rows':len(events),'leg_rows':len(legs),'rules_conflicts':sum(s['rules_conflict'] for s in all_specs),
                     'long0_rows':int(events.long_side_valid_0s.sum()),'short0_rows':int(events.short_side_valid_0s.sum())}),flush=True)


if __name__=='__main__':
    main()
