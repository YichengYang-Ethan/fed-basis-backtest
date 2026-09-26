"""Decimal calendar-day EFFR settlement reconstruction for every clean pair."""
import os
from pathlib import Path
from pathlib import Path
from decimal import Decimal,ROUND_HALF_UP
import pandas as pd
W=Path(__file__).resolve().parent
A=Path(os.environ['FOMC_PROJECT_ROOT']) / 'data/fomc_instruments.csv'
f=pd.read_csv(W/'effr_fresh.csv',dtype=str)
rates={pd.Timestamp(r.observation_date):Decimal(r.EFFR) for r in f.itertuples() if pd.notna(r.EFFR) and r.EFFR!='.'}
last=max(rates);daily={};v=None
for t in pd.date_range(min(rates),last):
 if t in rates:v=rates[t]
 daily[t]=v
cal=pd.read_csv(A).set_index('meeting_date');choices=pd.read_csv(W.parent/'outputs/cme_choice_calendar.csv')
choices=choices[choices.exact_zero_other_meeting_loading & choices.span.gt(0)]
records=[]
for q in choices.itertuples():
 if not '2023-01-01'<=q.meeting_date<='2026-09-16':continue
 c=cal.loc[q.meeting_date];den=Decimal(int(c.days_in_month));num=Decimal(int(c.days_post)) if q.instrument=='BACK' else den-Decimal(int(c.days_post));span=num/den
 assert abs(float(span)-q.span)<1e-12
 r=dict(meeting_date=q.meeting_date,instrument=q.instrument,leg_near=q.near_month,leg_far=q.far_month,span_exact=float(span),digital_contracts_per_spread=float(Decimal('1041.75')*span),realized_change_bp=c.realized_change_bp,regime=c.regime,terminal_implied_change_bp=float('nan'),cash_hedge_error_cents=float('nan'),terminal_months_complete=False)
 prices=[]
 for role,month in [('near',q.near_month),('far',q.far_month)]:
  days=pd.date_range(month+'-01',periods=pd.Period(month).days_in_month)
  if days[-1]>last:break
  mean=sum(daily[t] for t in days)/Decimal(len(days));price=Decimal(100)-mean.quantize(Decimal('.001'),rounding=ROUND_HALF_UP)
  r['mean_'+role+'_pct']=float(mean);r['terminal_'+role+'_price']=float(price);prices.append(price)
 if len(prices)==2:
  implied=(prices[0]-prices[1])*100/span;r['terminal_implied_change_bp']=float(implied);r['terminal_months_complete']=True
  if pd.notna(c.realized_change_bp):r['cash_hedge_error_cents']=float(4*(implied-Decimal(str(c.realized_change_bp))))
 records.append(r)
df=pd.DataFrame(records);assert not df.duplicated(['meeting_date','instrument']).any()
df.to_parquet(W/'expanded_terminal_cash_reference.parquet',index=False)
print(df[['meeting_date','instrument','terminal_months_complete','terminal_implied_change_bp']].to_string(index=False))
