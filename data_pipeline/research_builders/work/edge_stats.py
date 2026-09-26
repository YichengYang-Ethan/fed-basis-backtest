"""Small statistical utilities for exploratory meeting-clustered diagnostics."""
import numpy as np
import pandas as pd

def pearson_from_sums(v):
    n,sx,sy,sxx,syy,sxy=np.asarray(v).T
    vx=sxx-sx*sx/n;vy=syy-sy*sy/n
    with np.errstate(divide='ignore',invalid='ignore'):
        out=(sxy-sx*sy/n)/np.sqrt(np.maximum(vx,0)*np.maximum(vy,0))
    return np.where((vx>1e-14)&(vy>1e-14)&(n>2),out,np.nan)

def clustered_correlation(frame,x,y,cluster='meeting_date',reps=2000,seed=91726):
    z=frame[[cluster,x,y]].replace([np.inf,-np.inf],np.nan).dropna()
    rows=[];local=[]
    for name,g in z.groupby(cluster,sort=True):
        a=g[x].to_numpy(dtype=float);b=g[y].to_numpy(dtype=float)
        row=[len(g),a.sum(),b.sum(),a@a,b@b,a@b]
        rows.append(row)
        local.append({'meeting_date':name,'n':len(g),'correlation':float(pearson_from_sums(row))})
    if not rows:return {'n':0,'meetings':0,'r':None,'ci_low':None,'ci_high':None},pd.DataFrame(local)
    v=np.array(rows,dtype=float);k=len(v);observed=float(pearson_from_sums(v.sum(axis=0)))
    ci=[np.nan,np.nan];valid=0
    if k>=6:
        weights=np.random.default_rng(seed).multinomial(k,np.full(k,1/k),size=reps)
        estimates=pearson_from_sums(weights@v);finite=estimates[np.isfinite(estimates)];valid=len(finite)
        if valid>=reps*.9:ci=np.quantile(finite,[.025,.975])
    return {'n':len(z),'meetings':k,'r':observed,'ci_low':float(ci[0]),'ci_high':float(ci[1]),
            'bootstrap_valid':valid,'bootstrap_reps':reps,'ci_method':'meeting-cluster percentile bootstrap'},pd.DataFrame(local)

def exact_change(frame,column,seconds=300,ts='anchor_ts',meeting='meeting_date',day='date_et'):
    """No row-count differencing across gaps or days."""
    now=frame[[meeting,day,ts,column]].copy()
    lag=now.rename(columns={column:'previous'});lag[ts]+=seconds
    pair=now.merge(lag,on=[meeting,day,ts],how='left',validate='one_to_one')
    return pd.Series(pair[column].to_numpy()-pair['previous'].to_numpy(),index=frame.index)

def correlation_diagnostics(frame,x,y,label):
    z=frame[['meeting_date','date_et','anchor_ts',x,y]].copy()
    result=[];per=[]
    for mode in ['levels','within_meeting','change_5m']:
        v=z.copy()
        if mode=='within_meeting':
            # The common-pair sample owns each meeting's centering.
            v=v.dropna(subset=[x,y])
            v[[x,y]]=v[[x,y]]-v.groupby('meeting_date')[[x,y]].transform('mean')
        elif mode=='change_5m':
            v[x]=exact_change(z,x);v[y]=exact_change(z,y)
        stats,local=clustered_correlation(v,x,y)
        result.append({'pair':label,'mode':mode,**stats})
        if len(local):local['pair']=label;local['mode']=mode;per.append(local)
    return result,pd.concat(per,ignore_index=True) if per else pd.DataFrame()

def lag_diagnostics(frame,x,y,label):
    """Positive lag = y changes after x; descriptive, not causality/execution."""
    v=frame[['meeting_date','date_et','anchor_ts',x,y]].copy()
    v['dx']=exact_change(v,x,60);v['dy']=exact_change(v,y,60)
    left=v[['meeting_date','date_et','anchor_ts','dx']]
    out=[]
    for minutes in [-15,-5,-1,0,1,5,15]:
        right=v[['meeting_date','date_et','anchor_ts','dy']].copy();right.anchor_ts-=minutes*60
        p=left.merge(right,on=['meeting_date','date_et','anchor_ts'],how='inner',validate='one_to_one')
        s,_=clustered_correlation(p,'dx','dy',reps=1000)
        out.append({'pair':label,'lag_minutes_y_after_x':minutes,**s})
    return out

def persistent_candidates(frame,score='edge_cents',direction='direction',threshold=2.,minutes=3):
    """First confirmed run per meeting; no future outcomes in selection."""
    z=frame.sort_values(['meeting_date','anchor_ts']).copy()
    valid=z[score].ge(threshold)&z[score].notna()
    same=(z.meeting_date.eq(z.meeting_date.shift())&z.date_et.eq(z.date_et.shift())
          &z[direction].eq(z[direction].shift())&z.anchor_ts.diff().eq(60)&valid&valid.shift(fill_value=False))
    z['run_id']=(~same).cumsum();z['run_length_so_far']=z.groupby('run_id').cumcount()+1
    z['qualifies_now']=valid&z.run_length_so_far.ge(minutes)
    chosen=z[z.qualifies_now].groupby('meeting_date',sort=False).head(1)
    episodes=z[z.qualifies_now].groupby(['meeting_date','run_id']).agg(start=('anchor_ts','min'),end=('anchor_ts','max'),
              qualifying_minutes=(score,'size'),minimum_edge=(score,'min'),maximum_edge=(score,'max')).reset_index()
    return chosen,episodes

def algebra_checks():
    """All states: signed YES cash accounting agrees with fully funded YES/NO."""
    q=np.array([-200,-100,0,100,200]);p=np.array([.04,.16,.55,.20,.05])
    signed_cost=float(q@p)
    funded_cost=float(np.maximum(q,0)@p+np.maximum(-q,0)@(1-p))
    cash=float(np.maximum(-q,0).sum())
    for state in range(len(q)):
        yes=np.eye(len(q))[state]
        signed_payout=float(q@yes)
        funded_payout=float(np.maximum(q,0)@yes+np.maximum(-q,0)@(1-yes))
        assert abs((signed_payout-signed_cost)-(funded_payout-funded_cost))<1e-10
        assert abs((funded_payout-signed_payout)-cash)<1e-10
    # Short CME + long digital has terminal correction -hedge_error.
    for decision in [-50,-25,0,25,50]:
        d_cme=8.;v_buy=.25;err_cents=1.3
        terminal=decision+err_cents/4
        pnl=4*(d_cme-terminal)+100*(decision/25-v_buy)
        assert abs(pnl-(4*d_cme-100*v_buy-err_cents))<1e-10
    return True

if __name__=='__main__':
    assert algebra_checks()
    z=pd.DataFrame({'meeting_date':['a']*3,'date_et':['d']*3,'anchor_ts':[0,60,180],'v':[1.,2.,8.]})
    assert exact_change(z,'v',60).iloc[1]==1 and np.isnan(exact_change(z,'v',60).iloc[2])
    print('Signed YES/NO funding equivalence, payoff signs, and exact-gap differencing checks passed.')
