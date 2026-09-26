#!/usr/bin/env python3
"""Local-only CME minute as-of panel. No network, purchases, raw mutation, or strategy.

DBN is decoded once per file; durable selected-row caches allow regeneration of
reference annotations and derived columns without decoding source records again.
Price columns ending _nano retain fixed 1e-9 source units, with UNDEF_PRICE as
NA; true zero spread prices remain zero. All CME quote detail stays in work/.
"""
import os
from pathlib import Path
from pathlib import Path
import json, hashlib, time, argparse
import numpy as np
import pandas as pd
import databento as db

W=Path(__file__).resolve().parent
RAW=Path(os.environ['FOMC_DATA_ROOT']) / 'raw/cme/databento/intraday_20260917'
CAL=Path(os.environ['FOMC_PROJECT_ROOT']) / 'data/fomc_instruments.csv'
TZ='America/New_York'; UNDEF=9223372036854775807; USIZE=4294967295; UTS=18446744073709551615
VERSION=1
PX=[f'{s}_px_{i:02d}' for i in range(10) for s in ['bid','ask']]
SZ=[f'{s}_sz_{i:02d}' for i in range(10) for s in ['bid','ask']]
CT=[f'{s}_ct_{i:02d}' for i in range(10) for s in ['bid','ask']]
NUM=['ts_recv','ts_event','publisher_id','instrument_id','flags','sequence','ts_in_delta','depth']+PX+SZ+CT


def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def sym_month(s):
    t=pd.Timestamp(s+'-01');return 'ZQ'+'FGHJKMNQUVXZ'[t.month-1]+str(t.year%10)
def anchors_for(req,meeting):
    start=pd.Timestamp(req['start'],tz='UTC');end=pd.Timestamp(req['end'],tz='UTC')
    days=pd.date_range(start.tz_convert(TZ).normalize(),end.tz_convert(TZ).normalize(),freq='D')
    aa=[]
    for day in days:
        if day.weekday()>=5 or day.date().isoformat()==meeting:continue
        a=pd.date_range(day+pd.Timedelta(hours=10),day+pd.Timedelta(hours=15),freq='min').tz_convert('UTC')
        aa.extend(x for x in a if start<=x<end)
    ix=pd.DatetimeIndex(aa)
    assert len(ix)==15*301,(meeting,len(ix))
    return ix

def scan_job(job,anchor,chunk):
    """Right-asof within each ET date, retaining latest source row even if bad.
    Same-time ties are resolved by original file order, including chunk borders.
    An independent filter-based 15:00 selector is evaluated in the same pass.
    """
    cache=W/'cme_minute_selection_cache';cache.mkdir(exist_ok=True)
    output=cache/(job['id']+'.parquet');meta=cache/(job['id']+'.json')
    if output.exists() and meta.exists():
        m=json.loads(meta.read_text())
        assert m['sha256']==job['sha256'] and m['version']==VERSION
        return pd.read_parquet(output),m
    symbols=job['request']['symbols'].split(',');n=len(anchor);ans=anchor.asi8
    midnight=anchor.tz_convert(TZ).normalize().tz_convert('UTC').asi8
    lastminute=np.flatnonzero(anchor.tz_convert(TZ).strftime('%H:%M')=='15:00')
    states={s:{c:np.full(n,-1,dtype=np.int64) for c in NUM+['source_row']} for s in symbols}
    texts={s:{c:np.full(n,'',dtype=object) for c in ['action','side']} for s in symbols}
    independent={};rows=0;previous=-1;chunkcount=0
    store=db.DBNStore.from_file(job['path'])
    assert str(store.schema)=='mbp-10',str(store.schema)
    for frame in store.to_df(price_type='fixed',pretty_ts=False,map_symbols=True,count=chunk):
        frame=frame.reset_index();chunkcount+=1
        rec=frame.ts_recv.to_numpy(dtype=np.int64)
        assert len(rec) and rec[0]>=previous and np.all(rec[1:]>=rec[:-1]),'Receive order violation'
        previous=int(rec[-1]);assert set(frame.symbol.unique())<=set(symbols)
        for symbol,g in frame.groupby('symbol',sort=False):
            receive=g.ts_recv.to_numpy(dtype=np.int64)
            idx=np.searchsorted(receive,ans,side='right')-1
            ok=idx>=0
            ok[ok] &= receive[idx[ok]]>=midnight[ok]
            dest=np.flatnonzero(ok);take=idx[ok]
            if len(dest):
                chosen=g.iloc[take]
                for c in NUM:states[symbol][c][dest]=chosen[c].to_numpy(dtype=np.int64)
                states[symbol]['source_row'][dest]=rows+g.index.to_numpy(dtype=np.int64)[take]
                for c in ['action','side']:texts[symbol][c][dest]=chosen[c].to_numpy()
            # A filter/tail implementation, independent of searchsorted selection.
            for j in lastminute:
                pick=g[(g.ts_recv>=midnight[j])&(g.ts_recv<=ans[j])]
                if len(pick):
                    q=pick.iloc[-1]
                    independent[(symbol,int(j))]={c:int(q[c]) for c in NUM}
        rows+=len(frame)
    assert rows==job['expected_records'],(job['id'],rows,job['expected_records'])
    tables=[];crosschecks=0
    for symbol in symbols:
        selected=states[symbol];present=selected['ts_recv']>=0
        for j in lastminute:
            check=independent.get((symbol,int(j)))
            assert (check is not None)==bool(present[j])
            if check:
                assert all(selected[c][j]==v for c,v in check.items()),(job['id'],symbol,int(j))
                crosschecks+=1
        out={'anchor_ns':ans,'symbol':symbol,'present':present,'source_job':job['id'],'source_file':Path(job['path']).name}
        for c,v in selected.items():
            name=c+'_nano' if c in PX else c+'_ns' if c in ['ts_recv','ts_event'] else c
            a=pd.array(v,dtype='Int64');a[~present]=pd.NA
            if c in PX:a[a==UNDEF]=pd.NA
            if c in SZ:a[a==USIZE]=pd.NA
            out[name]=a
        for c,v in texts[symbol].items():out[c]=pd.array(np.where(present,v,None),dtype='string')
        tables.append(pd.DataFrame(out))
    result=pd.concat(tables,ignore_index=True)
    result.to_parquet(output,index=False,compression='zstd')
    m={'id':job['id'],'version':VERSION,'sha256':job['sha256'],'source_rows_decoded':rows,'chunks':chunkcount,'independent_15h_price_timestamp_checks':crosschecks,'observed_anchor_symbol_rows':int(result.present.sum()),'selection_rows':len(result)}
    meta.write_text(json.dumps(m,indent=2));print(json.dumps(m),flush=True)
    return result,m


def reference_tables():
    d=pd.read_parquet(RAW/'definitions/contract_definitions.parquet')
    s=pd.read_parquet(RAW/'status/contract_status.parquet')
    # Both clocks precede every reference receive timestamp in these local files.
    # This invariant makes receive-asof also event-asof; fail closed if it changes.
    assert (d.ts_event<=d.ts_recv).all() and (s.ts_event<=s.ts_recv).all()
    defs={}
    for symbol,g in d.groupby('raw_symbol'):
        recs=[]
        for recv,b in g.groupby('ts_recv',sort=True):
            q=b.iloc[-1]
            conflict=any(b[c].nunique(dropna=False)>1 for c in ['expiration','activation','security_update_action','display_factor','min_price_increment'])
            leg=b[['leg_count','leg_index','leg_raw_symbol','leg_side','leg_ratio_qty_numerator','leg_ratio_qty_denominator']].drop_duplicates()
            mapping={str(x.leg_raw_symbol):str(x.leg_side) for x in leg.itertuples()}
            qty=bool(len(leg)==2 and leg.leg_count.eq(2).all() and leg.leg_index.nunique()==2 and (leg.leg_ratio_qty_numerator==leg.leg_ratio_qty_denominator).all() and leg.leg_ratio_qty_denominator.between(1,2147483646).all())
            expiration=int(q.expiration);activation=int(q.activation)
            recs.append({'definition_ts_recv_ns':int(recv),'definition_ts_event_ns':int(b.ts_event.max()),'expiration_ns':expiration if expiration!=UTS else -1,'activation_ns':activation if activation!=UTS else -1,'definition_conflict':conflict,'security_update_action':q.security_update_action,'legs_one_to_one':qty,'leg_mapping':json.dumps(mapping,sort_keys=True),'display_factor_nano':int(q.display_factor),'min_price_increment_nano':int(q.min_price_increment)})
        defs[symbol]=pd.DataFrame(recs)
    statuses={}
    for symbol,g in s.groupby('symbol'):
        g=g.sort_values('ts_recv',kind='stable').reset_index(drop=True)
        # Duplicated records across overlapping requests do not change state.
        # Source Y/N/~ is never overwritten. Carry is a separate sensitivity.
        prior='~';priorrecv=-1;carryallowed=False;records=[]
        for q in g.itertuples():
            raw=str(q.is_trading)
            if raw in ['Y','N']:prior=raw;priorrecv=int(q.ts_recv);carryallowed=True
            elif not (int(q.action)==0 and int(q.trading_event) in [3,4]):carryallowed=False
            permitted=raw=='~' and carryallowed and prior in ['Y','N']
            records.append({'status_ts_recv_ns':int(q.ts_recv),'status_ts_event_ns':int(q.ts_event),'status_is_trading':raw,'status_is_quoting':str(q.is_quoting),'status_action':int(q.action),'status_reason':int(q.reason),'status_trading_event':int(q.trading_event),'status_is_trading_carry':prior if permitted else raw,'status_nochange_carried':permitted,'status_prior_explicit':prior,'status_prior_explicit_recv_ns':priorrecv})
        statuses[symbol]=pd.DataFrame(records)
    return defs,statuses


def ref_asof(table,ns,timecol):
    idx=np.searchsorted(table[timecol].to_numpy(dtype=np.int64),ns,side='right')-1
    assert (idx>=0).all(),'Reference history unexpectedly absent; must retain unknown if expanding sample'
    return table.iloc[idx].reset_index(drop=True).copy()

def attach_role(wide,obs,symbol,role,anchor,defs,statuses):
    n=len(anchor);ns=anchor.asi8;g=obs[obs.symbol==symbol].reset_index(drop=True)
    assert len(g)==n and np.array_equal(g.anchor_ns.to_numpy(),ns)
    data={role+'_'+c:g[c] for c in g if c!='anchor_ns'}
    d=ref_asof(defs[symbol],ns,'definition_ts_recv_ns');s=ref_asof(statuses[symbol],ns,'status_ts_recv_ns')
    for table in [d,s]:
        for c in table:data[role+'_'+c]=table[c]
    out=pd.DataFrame(data)
    p=lambda c:out[role+'_'+c]
    present=p('present')
    out[role+'_age_ns']=pd.array(ns-p('ts_recv_ns'),dtype='Int64')
    out[role+'_event_age_ns']=pd.array(ns-p('ts_event_ns'),dtype='Int64')
    out[role+'_event_after_anchor']=(p('ts_event_ns')>ns).fillna(False)
    out[role+'_bad_flags']=((p('flags').fillna(0).astype('int64')&12)!=0)
    two=p('bid_px_00_nano').notna()&p('ask_px_00_nano').notna()&p('bid_sz_00').gt(0).fillna(False)&p('ask_sz_00').gt(0).fillna(False)
    out[role+'_bbo_two_sided']=two
    out[role+'_bbo_crossed']=(p('bid_px_00_nano')>p('ask_px_00_nano')).fillna(False)
    out[role+'_bbo_locked']=(p('bid_px_00_nano')==p('ask_px_00_nano')).fillna(False)
    out[role+'_book_basic']=present&two&~p('bbo_crossed')&~p('bad_flags')&~p('event_after_anchor')
    out[role+'_expired']=p('expiration_ns').gt(0)&(p('expiration_ns')<=ns)
    out[role+'_definition_admits']=(~p('definition_conflict'))&p('expiration_ns').gt(0)&(p('expiration_ns')>ns)&((p('activation_ns')<0)|(p('activation_ns')<=ns))&p('security_update_action').ne('D')
    out[role+'_status_strict_open']=p('status_is_trading').eq('Y')
    out[role+'_status_carry_open']=p('status_is_trading_carry').eq('Y')
    for sec in [1,60,300]:out[role+f'_age_le_{sec}s']=present&p('age_ns').ge(0).fillna(False)&p('age_ns').le(sec*10**9).fillna(False)
    for side in ['bid','ask']:out[role+'_'+side]=p(side+'_px_00_nano').astype('float64')/1e9
    out[role+'_mid']=(p('bid')+p('ask'))/2
    return pd.concat([wide,out],axis=1)


def build(chunk):
    started=time.time();manifest=json.loads((RAW/'acquisition_manifest.json').read_text())
    jobs=manifest['jobs'];assert len(jobs)==52 and all(x['status']=='complete' for x in jobs)
    plan=pd.read_csv(W.parent/'outputs/data_acquisition_manifest.csv');plan=plan[plan.stage=='candidate_26'];assert len(plan)==52
    calendar=pd.read_csv(CAL).set_index('meeting_date');defs,statuses=reference_tables()
    conditions={r['date']:r for r in json.loads((RAW/'dataset_conditions.json').read_text())}
    tables=[];scans=[]
    for meeting in sorted(set(j['meeting_date'] for j in jobs)):
        mj=[j for j in jobs if j['meeting_date']==meeting]
        pair=next(j for j in mj if j['instrument_kind']=='outright_pair');listed=next(j for j in mj if j['instrument_kind']=='listed_calendar_spread')
        for job in mj:
            p=plan[(plan.end==meeting)&(plan.instrument_kind==job['instrument_kind'])]
            assert len(p)==1
            assert all(str(p.iloc[0][k])==str(job['request'][k]) for k in ['symbols','start','end','schema','dataset','stype_in'])
        c=calendar.loc[meeting];near,far=pair['request']['symbols'].split(',');spread=listed['request']['symbols']
        assert [near,far]==[sym_month(c.leg_near),sym_month(c.leg_far)] and spread==near+'-'+far
        anchor=anchors_for(pair['request'],meeting);assert anchor.equals(anchors_for(listed['request'],meeting))
        numerator=int(c.days_in_month)-int(c.days_post) if c.instrument=='FRONT' else int(c.days_post)
        span=numerator/int(c.days_in_month)
        wide=pd.DataFrame({'meeting_date':meeting,'anchor_ns':anchor.asi8,'anchor_utc':anchor,'date_et':anchor.tz_convert(TZ).strftime('%Y-%m-%d'),'time_et':anchor.tz_convert(TZ).strftime('%H:%M:%S'),'instrument':c.instrument,'leg_near':c.leg_near,'leg_far':c.leg_far,'days_post':int(c.days_post),'days_in_month':int(c.days_in_month),'span_numerator':numerator,'span_denominator':int(c.days_in_month),'span_exact':span,'span_csv':float(c.span),'span_csv_minus_exact':float(c.span)-span,'digital_contracts_per_spread':1041.75*span})
        for job,roles in [(pair,[(near,'near'),(far,'far')]),(listed,[(spread,'listed')])]:
            obs,stats=scan_job(job,anchor,chunk);scans.append(stats)
            for symbol,role in roles:wide=attach_role(wide,obs,symbol,role,anchor,defs,statuses)
        wide['listed_definition_direction_one_to_one']=wide.listed_legs_one_to_one &wide.listed_leg_mapping.eq(json.dumps({near:'B',far:'A'},sort_keys=True))
        wide['package_definition_admits']=wide.near_definition_admits&wide.far_definition_admits
        wide['synthetic_book_basic']=wide.near_book_basic&wide.far_book_basic
        wide['synthetic_bid']=wide.near_bid-wide.far_ask
        wide['synthetic_ask']=wide.near_ask-wide.far_bid
        wide['synthetic_mid']=(wide.synthetic_bid+wide.synthetic_ask)/2
        # Instantaneous capacity from displayed BBO only; not a fill guarantee.
        wide['synthetic_bid_size']=np.minimum(wide.near_bid_sz_00,wide.far_ask_sz_00)
        wide['synthetic_ask_size']=np.minimum(wide.near_ask_sz_00,wide.far_bid_sz_00)
        wide['listed_bid_size']=wide.listed_bid_sz_00;wide['listed_ask_size']=wide.listed_ask_sz_00
        wide['synthetic_max_age_ns']=wide[['near_age_ns','far_age_ns']].max(axis=1,skipna=False).astype('Int64')
        wide['synthetic_leg_time_gap_ns']=(wide.near_ts_recv_ns-wide.far_ts_recv_ns).abs()
        # GLBX ZQ listed calendar spreads are quoted in native basis-point units,
        # while the outright prices above are index points. Raw 1e-9 decoding
        # alone does NOT make those two economic units identical. Resolve from
        # contemporaneous definitions, never from a fitted price ratio.
        assert (wide.near_display_factor_nano==wide.far_display_factor_nano).all()
        assert wide.near_display_factor_nano.eq(10000000).all()
        assert wide.listed_display_factor_nano.eq(1000000000).all()
        wide['listed_to_index_price_factor']=wide.near_display_factor_nano/wide.listed_display_factor_nano
        for path in ['synthetic','listed']:
            base=wide[path+'_book_basic']&wide.package_definition_admits
            if path=='listed':base=base&wide.listed_definition_admits&wide.listed_definition_direction_one_to_one
            for side in ['bid','ask','mid']:
                wide[path+'_'+side+'_index_points']=wide[path+'_'+side]*(wide.listed_to_index_price_factor if path=='listed' else 1.)
                wide[path+'_'+side+'_D_bp']=wide[path+'_'+side+'_index_points']*100/span
            for convention in ['strict','carry']:
                status=(wide.near_status_strict_open&wide.far_status_strict_open if convention=='strict' else wide.near_status_carry_open&wide.far_status_carry_open) if path=='synthetic' else wide['listed_status_'+convention+'_open']
                wide[path+'_usable_'+convention]=base&status
                for side in ['bid','ask']:wide[path+'_'+side+'_one_spread_'+convention]=wide[path+'_usable_'+convention]&wide[path+'_'+side+'_size'].ge(1).fillna(False)
            for sec in [1,60,300]:
                age=wide['near_age_le_'+str(sec)+'s']&wide['far_age_le_'+str(sec)+'s'] if path=='synthetic' else wide['listed_age_le_'+str(sec)+'s']
                wide[path+'_age_le_'+str(sec)+'s']=age
        utcday=anchor.strftime('%Y-%m-%d')
        wide['dataset_condition']=pd.Series([conditions.get(x,{}).get('condition','unknown') for x in utcday])
        wide['dataset_condition_last_modified_date']=pd.Series([conditions.get(x,{}).get('last_modified_date') for x in utcday])
        wide['dataset_degraded']=wide.dataset_condition.eq('degraded')
        tables.append(wide)
        print(json.dumps({'meeting_done':meeting,'anchors':len(wide),'synthetic_usable_strict':int(wide.synthetic_usable_strict.sum()),'listed_usable_strict':int(wide.listed_usable_strict.sum())}),flush=True)
    panel=pd.concat(tables,ignore_index=True);assert len(panel)==390*301
    assert not panel.duplicated(['meeting_date','anchor_ns']).any()
    assert set(panel.time_et.unique())==set(pd.date_range('2000-01-01 10:00','2000-01-01 15:00',freq='min').strftime('%H:%M:%S'))
    assert (panel.date_et!=panel.meeting_date).all()
    assert (pd.to_datetime(panel.date_et).dt.weekday<5).all()
    for role in ['near','far','listed']:
        assert ((panel[role+'_ts_recv_ns']<=panel.anchor_ns)|~panel[role+'_present']).all()
        for typ in ['definition','status']:
            assert (panel[role+'_'+typ+'_ts_recv_ns']<=panel.anchor_ns).all()
            assert (panel[role+'_'+typ+'_ts_event_ns']<=panel.anchor_ns).all()
    panel.to_parquet(W/'cme_minute_panel.parquet',index=False,compression='zstd')
    summary=quality(panel,scans)
    summary['elapsed_seconds']=time.time()-started
    summary['source_hashes']={'acquisition_manifest':sha(RAW/'acquisition_manifest.json'),'calendar':sha(CAL),'definitions':sha(RAW/'definitions/contract_definitions.parquet'),'status':sha(RAW/'status/contract_status.parquet'),'conditions':sha(RAW/'dataset_conditions.json')}
    summary['field_contract']={'grain':'One row per meeting and UTC minute anchor; 26 meetings, 15 Monday-Friday dates each including holidays, 301 anchors 10:00..15:00 America/New_York inclusive. Request boundaries and meeting-date exclusion enforced.','prices':'near/far/listed_{bid,ask}_px_00_nano..09_nano are nullable source fixed-point integers; divide by 1e9 for native displayed units. Near/far native units are index points, but listed native units are basis points. listed_to_index_price_factor is derived from PIT definitions (0.01 in every sampled row); *_index_points and *_D_bp apply it. All 10 sizes/counts retained. UNDEF_PRICE/UNDEF_ORDER_SIZE become missing; actual 0 remains 0.','timestamps':'All *_ns are integer Unix nanoseconds. anchor_utc is timezone-aware UTC. Book age refers to ts_recv. ts_event and event_after_anchor are preserved separately.','span':'Exact integer-calendar ratio; rounded CSV retained only for comparison. Implied D_bp = normalized spread index points*100/span_exact. Listed native quotes first multiply the PIT near/listed display-factor ratio (0.01); failing to convert creates a 100x false edge. Digital contracts =1041.75*span_exact.','selection':'Last source row with ts_recv <= anchor within same ET date; source order resolves ties. Latest bad state is retained and flagged; no backtracking to a good state, no previous-day quote forward-fill, no F_LAST requirement.','statuses':'Strict preserves last Y/N/~. Carry is sensitivity only: last explicit Y/N survives exclusively action=0 No-change implied matching on/off (events3/4) messages. No age ceiling is imposed on status.','reference':'Definitions require both clocks <= anchor, known expiry, no conflicting/deleted definition, activation <= anchor when known. Entire package disallowed when near expires, even if far keeps quoting. Listed side/quantity must match near B/far A 1:1.','usability':'Same-day observed two-sided positive-size noncrossed BBO, no flags4/8, event not after anchor, definitions and status. Locked book is recorded, not automatically excluded. Age filters 1/60/300s are separate sensitivity flags. Neither display nor flags prove execution or locked arbitrage.','degraded':'Retrospectively published dataset-level condition, annotation only; not applied as a historical gate. Record bad flags independently invalidate basic book state.','private_data':'All quote details and selected-row caches remain in workspace work; no raw feed export or publishing.'}
    summary['official_sources']=['https://databento.com/docs/venues-and-datasets/glbx-mdp3','https://databento.com/docs/schemas-and-data-formats/status','https://databento.com/docs/schemas-and-data-formats/instrument-definitions']
    (W/'cme_minute_panel_quality.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary,indent=2),flush=True)


def quality(p,scans):
    result={'rows':len(p),'columns':len(p.columns),'meetings':int(p.meeting_date.nunique()),'meeting_days':int(p[['meeting_date','date_et']].drop_duplicates().shape[0]),'key_duplicates':int(p.duplicated(['meeting_date','anchor_ns']).sum()),'files':len(scans),'source_rows':sum(x['source_rows_decoded'] for x in scans),'independent_15h_price_timestamp_checks':sum(x['independent_15h_price_timestamp_checks'] for x in scans),'observed':{},'coverage':{},'qa_15h':{}}
    for role in ['near','far','listed']:
        result['observed'][role]={k:int(p[role+'_'+k].fillna(False).sum()) for k in ['present','book_basic','bad_flags','event_after_anchor','bbo_locked','bbo_crossed','expired','status_strict_open','status_carry_open','status_nochange_carried']}
        result['observed'][role]['forward_receive_rows']=int((p[role+'_ts_recv_ns']>p.anchor_ns).fillna(False).sum())
    for path in ['synthetic','listed']:
        result['coverage'][path]={}
        for conv in ['strict','carry']:
            result['coverage'][path][conv]={'all_ages':int(p[path+'_usable_'+conv].sum()),**{f'age_le_{sec}s':int((p[path+'_usable_'+conv]&p[path+f'_age_le_{sec}s']).sum()) for sec in [1,60,300]}}
    old=pd.read_csv(W/'cme_final_asof_quality.csv',dtype={'ts_recv_ns':'Int64','ts_event_ns':'Int64'})
    at15=p[p.time_et=='15:00:00'];differences=[]
    checks={'present':0,'ts_recv_ns':0,'ts_event_ns':0,'flags':0,'status_is_trading':0,'status_is_trading_carry':0,'book_usable_strict':0,'book_usable_carry':0}
    for q in old.itertuples():
        row=at15[(at15.meeting_date==q.meeting_date)&(at15.date_et==q.date_et)].iloc[0]
        role=next(r for r in ['near','far','listed'] if row[r+'_symbol']==q.symbol)
        expected={'present':q.has_same_day_observed_state,'ts_recv_ns':q.ts_recv_ns,'ts_event_ns':q.ts_event_ns,'flags':q.flags,'status_is_trading':q.status_is_trading,'status_is_trading_carry':q.status_is_trading_nochange_sensitivity}
        for field,e in expected.items():
            actual=row[role+'_'+field]
            equal=(pd.isna(actual) and pd.isna(e)) or (not pd.isna(actual) and not pd.isna(e) and actual==e)
            if not equal:differences.append({'meeting':q.meeting_date,'date':q.date_et,'role':role,'field':field})
            checks[field]+=int(equal)
        for conv,oldv in [('strict',q.book_reference_status_checks_pass),('carry',q.book_reference_status_checks_pass_nochange_sensitivity)]:
            actual=bool(row[role+'_book_basic'] and row.package_definition_admits and row[role+'_definition_admits'] and row[role+'_status_'+conv+'_open'])
            if actual!=oldv:differences.append({'meeting':q.meeting_date,'date':q.date_et,'role':role,'field':'usable_'+conv})
            checks['book_usable_'+conv]+=int(actual==oldv)
    result['qa_15h']={'symbol_rows_compared':len(old),'matching_fields':checks,'differences':differences,'synthetic_usable_strict':int(at15.synthetic_usable_strict.sum()),'synthetic_usable_carry':int(at15.synthetic_usable_carry.sum()),'listed_usable_strict':int(at15.listed_usable_strict.sum()),'listed_usable_carry':int(at15.listed_usable_carry.sum())}
    assert not differences,differences[:10]
    assert result['qa_15h']['synthetic_usable_strict']==321 and result['qa_15h']['synthetic_usable_carry']==324
    result['dataset_degraded_anchors']=int(p.dataset_degraded.sum())
    result['package_definition_admits_anchors']=int(p.package_definition_admits.sum())
    result['listed_one_to_one_anchors']=int(p.listed_definition_direction_one_to_one.sum())
    result['carry_affected_dates']=sorted(p.loc[p[['near_status_nochange_carried','far_status_nochange_carried','listed_status_nochange_carried']].any(axis=1),'date_et'].unique())
    cover=p.groupby(['meeting_date','date_et'],as_index=False).agg(anchors=('anchor_ns','size'),synthetic_usable_strict=('synthetic_usable_strict','sum'),synthetic_usable_carry=('synthetic_usable_carry','sum'),listed_usable_strict=('listed_usable_strict','sum'),listed_usable_carry=('listed_usable_carry','sum'),near_expired=('near_expired','sum'),degraded=('dataset_degraded','sum'))
    cover.to_csv(W/'cme_minute_panel_daily_coverage.csv',index=False)
    pd.DataFrame({'field':p.columns,'dtype':[str(x) for x in p.dtypes],'non_null':[int(p[x].notna().sum()) for x in p]}).to_csv(W/'cme_minute_panel_fields.csv',index=False)
    return result

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--chunk',type=int,default=200000);args=ap.parse_args();build(args.chunk)
