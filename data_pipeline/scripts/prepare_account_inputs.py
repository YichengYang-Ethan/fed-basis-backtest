#!/usr/bin/env python3
"""Materialize the existing frozen signal/execution/evaluation functions' inputs.

The original daily-marks extraction was ad hoc; build_daily_marks implements its
recorded provenance explicitly. It was compared field-for-field with that extract
on the author's private source files. This is data preparation, not rule fitting.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import pandas as pd
from pipeline_config import DATA_ROOT, PROJECT_ROOT


def build_daily_marks(executions, data_root, cutoff_et):
    months=sorted(set(executions.leg_near)|set(executions.leg_far))
    start=pd.to_datetime(int(executions.anchor_ts.min()),unit='s',utc=True).tz_convert('America/New_York').tz_localize(None).normalize()
    end=pd.Timestamp(cutoff_et).tz_localize(None).normalize()
    a=pd.read_parquet(data_root/'raw/cme/databento/zq_outrights.parquet').copy()
    a['trade_date']=pd.to_datetime(a.trade_date)
    a=a[a.dm.isin(months)&a.trade_date.between(start,end)]
    b=pd.read_parquet(data_root/'raw/cme/databento/zq_settlements.parquet',columns=['trade_date','instrument_id','ts_recv','price','stat_flags'])
    b['trade_date']=pd.to_datetime(b.trade_date)
    keys=['trade_date','instrument_id','ts_recv','price']
    if a.duplicated(keys).any() or b.duplicated(keys).any():raise ValueError('Duplicate source settlement keys')
    z=a.drop(columns=['stat_flags'],errors='ignore').merge(b,on=keys,how='left',validate='one_to_one',indicator=True)
    if not z['_merge'].eq('both').all():raise ValueError('Missing settlement flag join')
    z=z[['trade_date','dm','price','ts_recv','stat_flags']].sort_values(['trade_date','dm']).reset_index(drop=True)
    if z.duplicated(['trade_date','dm']).any():raise ValueError('Duplicate delivery-month marks')
    return z


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--research-root',type=Path,default=Path(os.environ.get('FOMC_RESEARCH_ROOT',DATA_ROOT/'research')))
    a=p.parse_args();root=a.research_root.resolve()
    os.environ['FOMC_PRIVATE_RESEARCH_ROOT']=str(root)
    os.environ['FOMC_REPLAY_OUTPUT_ROOT']=str(root)
    os.environ['FOMC_RAW_ROOT']=str(DATA_ROOT/'raw')
    path=PROJECT_ROOT/'research/depth_replay/research_source/work/capital100k/backtest.py'
    spec=importlib.util.spec_from_file_location('frozen_account_data_builder',path)
    model=importlib.util.module_from_spec(spec);spec.loader.exec_module(model)
    dest=root/'work/capital100k';dest.mkdir(parents=True,exist_ok=True)
    signals=model.select_signals(model.load_features())
    executions=model.execution_observations(signals,delay=model.PROTO['delay_seconds'])
    daily=build_daily_marks(executions,DATA_ROOT,model.PROTO['cutoff_et'])
    daily.to_parquet(dest/'daily_marks_with_flags.parquet',index=False)
    signals.to_parquet(dest/'signals.parquet',index=False)
    executions.to_parquet(dest/'executions.parquet',index=False)
    winners,terminal,marks=model.evaluation_data(signals)
    marks.to_parquet(dest/'marks_prepared.parquet',index=False)
    (dest/'evaluation.json').write_text(json.dumps({'winners':winners,'terminal':terminal},indent=2,allow_nan=True)+'\n')
    print(json.dumps({'signals':len(signals),'execution_status_counts':executions.execution_status.value_counts().to_dict(),'daily_marks':len(daily),'source_preliminary_versions_reconstructed':False,'orders':0,'paid_requests':0},indent=2))

if __name__=='__main__':main()
