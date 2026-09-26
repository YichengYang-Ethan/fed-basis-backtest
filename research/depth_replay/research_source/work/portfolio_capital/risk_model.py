"""Synchronized, pre-observation portfolio cash scenarios; no payoff labels."""
from functools import lru_cache
import numpy as np
import pandas as pd

class InsufficientHistory(ValueError):pass

class PortfolioModel:
    def __init__(self,history,initial=1100.,maintenance=950.):
        self.history=history;self.initial=initial;self.maintenance=maintenance

    @lru_cache(maxsize=256)
    def scenarios(self,months,now):
        """Coefficients by ending expiry stage, including within-window transition.

        Use the exact same 60 dates for every candidate n, including zero positions.
        A hypothetical expiry after step tau changes exposures on step tau+1.
        """
        day=pd.to_datetime(now,unit='s',utc=True).tz_convert('America/New_York').tz_localize(None).normalize()
        h=self.history[self.history.dm.isin(months)&self.history.available_ts.lt(now)&
            self.history.ts_recv.lt(pd.to_datetime(now,unit='s',utc=True))&self.history.trade_date.lt(day)]
        z=h.pivot(index='trade_date',columns='dm',values='price').reindex(columns=list(months)).dropna().sort_index().tail(60)
        if len(z)<60:raise InsufficientHistory(f'{len(z)} common observations for {months}')
        max_days=int((z.index[5:]-z.index[:-5]).days.max())
        # Consecutive delivery month expiries are at least 28 days apart; with
        # the monthly proxy cash clock, allow a more conservative minimum of 20.
        if max_days>=20:raise InsufficientHistory('Historical short-window gap permits multiple expiry transitions')
        p=z.to_numpy();m=len(months);groups=[[] for _ in range(m+1)]
        masks=[np.asarray([float(i>=j) for i in range(m)]) for j in range(m+1)]
        for stage in range(m):
            for horizon in range(1,6):
                for start in range(60-horizon):
                    increments=np.diff(p[start:start+horizon+1],axis=0)
                    cumulative=np.vstack([np.zeros(m),increments.cumsum(axis=0)])
                    for tau in range(horizon+1):
                        split=min(tau,horizon)
                        ending=stage if horizon<=tau else stage+1
                        coef=(cumulative[split]*masks[stage]+(cumulative[horizon]-cumulative[split])*masks[stage+1])*4167
                        groups[ending].append(coef)
        # Always include zero-shock maintenance. Deduplicate identical path coefficients.
        matrices=[np.unique(np.vstack([np.zeros(m),*g]),axis=0) for g in groups]
        used=h[h.trade_date.isin(z.index)]
        info=dict(common_observations=len(z),history_start=str(z.index.min().date()),history_end=str(z.index.max().date()),
            latest_history_available_ts=float(used.available_ts.max()),max_5interval_calendar_days=max_days,
            scenario_rows=sum(len(x) for x in matrices),months='|'.join(months))
        return matrices,masks,info

    def requirements(self,net,now,candidate=None,quantities=None,universe_months=()):
        """R(n)=max(I_current, max_prefix(K_stage - path_cash(n)))."""
        months=set(k for k,v in net.items() if v)|set(universe_months)
        if candidate is not None:months.update([candidate['leg_near'],candidate['leg_far']])
        months=tuple(sorted(months))
        ns=np.asarray([0] if quantities is None else quantities,dtype=float)
        if not months:
            zero=np.zeros(len(ns))
            return zero,zero,zero,dict(common_observations=0,history_start='',history_end='',
                latest_history_available_ts=np.nan,max_5interval_calendar_days=0,scenario_rows=0,months='')
        matrices,masks,info=self.scenarios(months,float(now))
        existing=np.array([net.get(x,0) for x in months],dtype=float);unit=np.zeros(len(months))
        if candidate is not None:
            unit[months.index(candidate['leg_near'])]=-candidate['cme_direction_sign']
            unit[months.index(candidate['leg_far'])]=candidate['cme_direction_sign']
        q=existing[:,None]+unit[:,None]*ns[None,:]
        current_initial=self.initial*np.abs(q).sum(axis=0)
        current_maintenance=self.maintenance*np.abs(q).sum(axis=0)
        required=current_initial.copy()
        for mask,coef in zip(masks,matrices):
            k=self.maintenance*(np.abs(q)*mask[:,None]).sum(axis=0)
            base=coef@existing;new=coef@unit
            for start in range(0,len(ns),256):
                end=min(start+256,len(ns))
                loss=np.max(-base[:,None]-new[:,None]*ns[None,start:end],axis=0)
                required[start:end]=np.maximum(required[start:end],k[start:end]+loss)
        return required,current_initial,current_maintenance,info
