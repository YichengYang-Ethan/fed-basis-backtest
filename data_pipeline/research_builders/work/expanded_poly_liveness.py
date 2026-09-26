"""Fixed, contemporaneous four-actual-token liveness sensitivity.

This is an additional control. It does not alter the original principal panel.
Historical observations are midpoint prints, not proof of executable liquidity.
"""
import os
from pathlib import Path
from pathlib import Path
from collections import Counter
import json
import numpy as np
import pandas as pd
import duckdb
import pyarrow.parquet as pq

W=Path(__file__).resolve().parent
D=Path(os.environ['FOMC_DATA_ROOT']) / 'raw/poly'
ET='America/New_York'
KEY=['meeting_date','event_id','anchor_ts']
P=W/'expanded_poly_fullminute_principal_panel.parquet'
RAW=W/'expanded_poly_liveness_observations.parquet'
OUT=W/'expanded_poly_fullminute_liveness.parquet'
MIN_OBS=20
MIN_DISTINCT_PRICES=2
WINDOW_SECONDS=3600

def rolling_at_anchors(t,prices,fidelity,anchors,lower_bounds):
    """Distinct timestamps (input deduplicated) and exact prices in [L, anchor]."""
    out=np.zeros((len(anchors),4),dtype=np.int64)
    extrema=np.full((len(anchors),2),np.nan)
    left=right=0
    counts=Counter()
    minute_count=0
    for i,(a,lo) in enumerate(zip(anchors,lower_bounds)):
        new_left=int(np.searchsorted(t,lo,side='left'))
        new_right=int(np.searchsorted(t,a,side='right'))
        if new_left>=right:
            counts.clear();minute_count=0;left=right=new_left
        while left<new_left:
            price=prices[left];counts[price]-=1
            if counts[price]==0:del counts[price]
            minute_count-=int(fidelity[left]==1)
            left+=1
        while right<new_right:
            counts[prices[right]]+=1
            minute_count+=int(fidelity[right]==1)
            right+=1
        out[i]=[right-left,len(counts),minute_count,(right-left)-minute_count]
        if right>left:extrema[i]=[t[left],t[right-1]]
    return out,extrema

def main():
    p=pd.read_parquet(P).reset_index(drop=True)
    assert not p.duplicated(KEY).any()
    p['row_id']=np.arange(len(p))
    parts=[]
    for leg in ['lower','upper']:
        for side in ['yes','no']:
            pref=f'{leg}_{side}'
            token=f'{leg}_token_id' if side=='yes' else f'{leg}_no_token_id'
            z=p[KEY+['date_et','row_id',token,f'{pref}_mid_60s',f'{pref}_age_sec',f'{pref}_observed_ts']].copy()
            z.columns=KEY+['date_et','row_id','token_id','current_mid_60s','current_age_sec','current_observed_ts']
            z['leg_side']=pref
            parts.append(z)
    anchors=pd.concat(parts,ignore_index=True)
    ranges=anchors.groupby('token_id',as_index=False).agg(min_anchor=('anchor_ts','min'),max_anchor=('anchor_ts','max'))
    ranges['min_ts']=ranges.min_anchor-WINDOW_SECONDS
    metadata=pd.read_parquet(W/'expanded_poly_market_metadata.parquet')
    life=pd.concat([metadata[[col,'lifetime_start','lifetime_end']].rename(columns={col:'token_id'}) for col in ['yes_token_id','no_token_id']]).drop_duplicates('token_id')
    life['life_start_ts']=life.lifetime_start.astype('int64')//10**9
    life['life_end_ts']=life.lifetime_end.astype('int64')//10**9
    ranges=ranges.merge(life[['token_id','life_start_ts','life_end_ts']],on='token_id',validate='one_to_one')
    c=duckdb.connect()
    c.execute("SET memory_limit='1GB'")
    c.execute('SET threads=2')
    c.execute(f"SET temp_directory='{W / 'expanded_poly_liveness_duckdb_tmp'}'")
    c.register('needed',ranges)
    # Exact same-ts observations from the two source fidelities are counted once.
    # Prefer the minute source; previous full-source audit found no price conflict.
    query=f"""
    SELECT p.token_id,p.ts,min(p.p) AS p,min(p.fidelity) AS fidelity
    FROM read_parquet(['{D}/prices.parquet','{W}/expanded_poly_fullminute_parts/*.parquet'],union_by_name=true) p
    INNER JOIN needed n ON p.token_id=n.token_id
    WHERE p.ts>=greatest(n.min_ts,n.life_start_ts)
      AND p.ts<=n.max_anchor AND p.ts<n.life_end_ts
      AND p.p>=0 AND p.p<=1
      AND CAST(timezone('{ET}',to_timestamp(p.ts)) AS TIME)>=TIME '09:00:00'
      AND CAST(timezone('{ET}',to_timestamp(p.ts)) AS TIME)<=TIME '15:00:00'
    GROUP BY p.token_id,p.ts
    ORDER BY p.token_id,p.ts
    """
    c.execute(f"COPY ({query}) TO '{RAW}' (FORMAT PARQUET,COMPRESSION ZSTD,ROW_GROUP_SIZE 250000)")
    raw_rows=pq.ParquetFile(RAW).metadata.num_rows
    print(json.dumps({'stage':'deduplicated_history','raw_rows':raw_rows,'tokens':len(ranges)}),flush=True)
    results=[];checks=[]
    # Parquet predicate pushdown reads only the few row groups for each token.
    for token,g in anchors.groupby('token_id',sort=True):
        g=g.sort_values(['anchor_ts','leg_side','row_id']).copy()
        raw=pd.read_parquet(RAW,filters=[('token_id','==',token)],columns=['ts','p','fidelity'])
        t=raw.ts.to_numpy(np.int64);prices=raw.p.to_numpy(float);fidelity=raw.fidelity.to_numpy(np.int64)
        assert len(t)==0 or (np.diff(t)>0).all()
        a=g.anchor_ts.to_numpy(np.int64)
        midnight=pd.to_datetime(g.date_et).dt.tz_localize(ET).dt.tz_convert('UTC').astype('int64').to_numpy()//10**9
        lower=np.maximum(a-WINDOW_SECONDS,midnight)
        counts,extrema=rolling_at_anchors(t,prices,fidelity,a,lower)
        for i,col in enumerate(['trail60_n_observation_timestamps','trail60_n_distinct_prices','trail60_n_minute_observations','trail60_n_other_fidelity_observations']):g[col]=counts[:,i]
        g['trail60_first_observed_ts']=extrema[:,0];g['trail60_last_observed_ts']=extrema[:,1]
        g['trail60_window_start_ts']=lower
        valid=g.current_mid_60s.between(0,1)&g.current_age_sec.between(0,60)&g.current_observed_ts.le(g.anchor_ts)
        same_day=pd.to_datetime(g.current_observed_ts,unit='s',utc=True).dt.tz_convert(ET).dt.strftime('%Y-%m-%d').eq(g.date_et)
        valid &= same_day
        g['current_quote_valid']=valid
        g['liveness_required']=valid & g.current_mid_60s.between(.02,.98)
        g['history_available']=g.trail60_n_observation_timestamps.gt(0)
        live=g.trail60_n_observation_timestamps.ge(MIN_OBS)&g.trail60_n_distinct_prices.ge(MIN_DISTINCT_PRICES)
        g['liveness_pass']=valid & g.history_available & (~g.liveness_required|live)
        g['liveness_status']=np.select([
            ~valid,~g.history_available,~g.liveness_required,
            g.trail60_n_observation_timestamps.lt(MIN_OBS),g.trail60_n_distinct_prices.lt(MIN_DISTINCT_PRICES)
        ],['missing_current_quote','no_history','not_required_outside_inplay','insufficient_observations','insufficient_price_variation'],default='pass')
        # Independent direct masks, including first/last and evenly spaced anchors.
        for i in np.unique(np.linspace(0,len(g)-1,min(7,len(g)),dtype=int)):
            mask=(t>=lower[i])&(t<=a[i]);nt=int(mask.sum());np_=len(np.unique(prices[mask]))
            assert nt==counts[i,0] and np_==counts[i,1]
            checks.append({'token_id':token,'anchor_ts':int(a[i]),'n_obs':nt,'n_prices':np_})
        nonempty=g.history_available
        assert g.loc[nonempty,'trail60_first_observed_ts'].ge(g.loc[nonempty,'trail60_window_start_ts']).all()
        assert g.loc[nonempty,'trail60_last_observed_ts'].le(g.loc[nonempty,'anchor_ts']).all()
        for col in ['trail60_first_observed_ts','trail60_last_observed_ts']:
            assert pd.to_datetime(g.loc[nonempty,col],unit='s',utc=True).dt.tz_convert(ET).dt.strftime('%Y-%m-%d').eq(g.loc[nonempty,'date_et']).all()
        results.append(g)
    long=pd.concat(results,ignore_index=True).sort_values(['row_id','leg_side'])
    assert len(long)==4*len(p) and not long.duplicated(['row_id','leg_side']).any()
    out=p[KEY+['date_et']].copy()
    value_cols=['token_id','current_mid_60s','current_observed_ts','current_age_sec','current_quote_valid','liveness_required','history_available','trail60_n_observation_timestamps','trail60_n_distinct_prices','trail60_n_minute_observations','trail60_n_other_fidelity_observations','trail60_first_observed_ts','trail60_last_observed_ts','trail60_window_start_ts','liveness_pass','liveness_status']
    for pref,g in long.groupby('leg_side'):
        g=g.set_index('row_id').reindex(p.row_id)
        for col in value_cols:out[f'{pref}_{col}']=g[col].to_numpy()
    pass_cols=[f'{l}_{s}_liveness_pass' for l in ['lower','upper'] for s in ['yes','no']]
    required_cols=[f'{l}_{s}_liveness_required' for l in ['lower','upper'] for s in ['yes','no']]
    out['principal_liveness_pass']=out[pass_cols].all(axis=1)
    out['n_actual_tokens_requiring_liveness']=out[required_cols].sum(axis=1).astype('int8')
    out['liveness_window_seconds']=WINDOW_SECONDS
    out['liveness_min_observation_timestamps']=MIN_OBS
    out['liveness_min_distinct_prices']=MIN_DISTINCT_PRICES
    out.to_parquet(OUT,index=False)
    long.to_parquet(W/'expanded_poly_fullminute_liveness_token_audit.parquet',index=False)
    merged=p[['meeting_date','strict_complete_two_principal_gate']].join(out[['principal_liveness_pass']])
    bymeeting=merged.groupby('meeting_date').agg(pair_rows=('principal_liveness_pass','size'),passing_liveness_rows=('principal_liveness_pass','sum'),strict_pair_rows=('strict_complete_two_principal_gate','sum'))
    bymeeting['strict_liveness_rows']=merged.assign(ok=merged.principal_liveness_pass&merged.strict_complete_two_principal_gate).groupby('meeting_date').ok.sum()
    bymeeting.to_csv(W/'expanded_poly_fullminute_liveness_by_meeting.csv')
    qa=dict(rows=len(out),tokens=len(ranges),raw_deduplicated_observations=raw_rows,principal_liveness_pass_rows=int(out.principal_liveness_pass.sum()),strict_liveness_pass_rows=int((out.principal_liveness_pass&p.strict_complete_two_principal_gate).sum()),leg_status_counts=long.liveness_status.value_counts().to_dict(),independent_window_checks=len(checks),thresholds=dict(window_seconds=WINDOW_SECONDS,min_distinct_observation_timestamps=MIN_OBS,min_distinct_prices=MIN_DISTINCT_PRICES,inplay_inclusive=[.02,.98]),policy='Actual YES and NO independently; exact anchor-relative inclusive 60-minute window; same ET date; no forward observations; metadata lifetime; missing current quote or no history fails; outside-inplay valid quote is explicitly not required. Observation timestamps are midpoint print timestamps, not evidence of executable activity.',original_panel_unchanged=True)
    (W/'expanded_poly_fullminute_liveness_qa.json').write_text(json.dumps(qa,indent=2))
    print(json.dumps(qa,indent=2),flush=True)

if __name__=='__main__':main()
