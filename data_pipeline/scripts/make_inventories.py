#!/usr/bin/env python3
"""Refresh public event metadata into the two legacy inventory input schemas.

Identifiers come from a reviewed seed list, not a volume/outcome ranking. This
refresh does not recreate historical metadata vintages; save each acquisition
manifest separately if you need point-in-time membership evidence.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
import requests
from pipeline_config import DATA_ROOT, PROJECT_ROOT


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--seeds',type=Path,default=PROJECT_ROOT/'data_pipeline/config/inventory_seeds.json')
    a=p.parse_args();seeds=json.loads(a.seeds.read_text())
    sess=requests.Session();sess.headers['User-Agent']='fed-basis-backtest/research'
    raw=DATA_ROOT/'raw';raw.mkdir(parents=True,exist_ok=True)
    errors=[];poly={};kalshi={}
    def get(url,**params):
        r=sess.get(url,params=params,timeout=60);r.raise_for_status();time.sleep(.4);return r.json()
    for slug in seeds['polymarket_slugs']:
        try:
            payload=get('https://gamma-api.polymarket.com/events',slug=slug)
            if len(payload)!=1 or payload[0].get('slug')!=slug:raise ValueError('Expected one exact event')
            ev=payload[0]
            poly[slug]=dict(title=ev.get('title'),start=(ev.get('startDate') or '')[:10],end=(ev.get('endDate') or '')[:10],closed=ev.get('closed'),markets=[dict(q=m.get('question',''),tokens=m.get('clobTokenIds'),outcomes=m.get('outcomes'),vol=m.get('volume'),closed=m.get('closed'),end=(m.get('endDate') or '')[:10]) for m in ev.get('markets',[])])
        except Exception as exc:errors.append({'venue':'Polymarket','identifier':slug,'error':type(exc).__name__})
    for ticker in seeds['kalshi_event_tickers']:
        try:
            payload=get(f'https://api.elections.kalshi.com/trade-api/v2/events/{ticker}',with_nested_markets='true')
            ev=payload.get('event',{});markets=payload.get('markets') or ev.get('markets') or []
            kalshi[ticker]=dict(title=ev.get('title'),legs={m['ticker'].rsplit('-',1)[-1]:dict(ticker=m['ticker'],result=m.get('result',''),status=m.get('status'),open=m.get('open_time'),close=m.get('close_time'),vol=m.get('volume')) for m in markets})
        except Exception as exc:errors.append({'venue':'Kalshi','identifier':ticker,'error':type(exc).__name__})
    (raw/'poly_fed_inventory.json').write_text(json.dumps(poly,indent=2)+'\n')
    (raw/'kalshi_fed_inventory.json').write_text(json.dumps(kalshi,indent=2)+'\n')
    report={'fetched_at_utc':datetime.now(timezone.utc).isoformat(),'polymarket_events':len(poly),'kalshi_events':len(kalshi),'errors':errors,'historical_metadata_vintage_recreated':False}
    (raw/'inventory_refresh_report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
    if errors:raise SystemExit(1)

if __name__=='__main__':main()
