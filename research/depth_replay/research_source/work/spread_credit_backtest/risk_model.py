"""Hypothetical adjacent-month margin credits, not a SPAN implementation.

Only the collateral function changes. Causal cash-shock scenarios are inherited
unchanged from the prior research model. All candidate quantities share a fixed
historical month universe, even at quantities that fully offset a month.
"""
from pathlib import Path
import importlib.util
import numpy as np
import pandas as pd

_s=importlib.util.spec_from_file_location('original_margin_scenarios',Path(__file__).parents[1]/'portfolio_capital/risk_model.py')
_m=importlib.util.module_from_spec(_s);_s.loader.exec_module(_m)
InsufficientHistory=_m.InsufficientHistory
ScenarioSource=_m.PortfolioModel

def paired_contracts(months,quantities):
    """Maximum adjacent opposite-sign pairs on a calendar-month path.

    Uniform credit per pair permits a leftmost-leaf greedy solution. A contract
    is consumed at most once. A removed/zero month cannot create a new calendar
    adjacency. Supports columns of candidate portfolios.
    """
    q=np.asarray(quantities,dtype=float)
    if q.ndim==1:q=q[:,None]
    if q.shape[0]!=len(months):raise ValueError('Month/quantity shape mismatch')
    if list(months)!=sorted(months) or len(set(months))!=len(months):raise ValueError('Sorted unique months required')
    capacity=np.abs(q).copy();matched=np.zeros(q.shape[1])
    ordinal=[pd.Period(x,freq='M').ordinal for x in months]
    for j in range(len(months)-1):
        if ordinal[j+1]-ordinal[j]!=1:continue
        used=np.where(q[j]*q[j+1]<0,np.minimum(capacity[j],capacity[j+1]),0.)
        capacity[j]-=used;capacity[j+1]-=used;matched+=used
    return matched

def margin_vector(months,q,outright,pair):
    q=np.asarray(q,dtype=float)
    if q.ndim==1:q=q[:,None]
    if not 0<=pair<=2*outright:raise ValueError('Pair rate outside declared model')
    total=np.abs(q).sum(axis=0)
    matched=paired_contracts(months,q)
    return outright*(total-2*matched)+pair*matched

class PortfolioModel:
    def __init__(self,history,initial=1100.,maintenance=950.,pair_initial=2200.,pair_maintenance=1900.,scenario_source=None):
        if not 0<pair_maintenance<=pair_initial:raise ValueError('Invalid paired initial/maintenance')
        if not 0<maintenance<=initial:raise ValueError('Invalid outright initial/maintenance')
        self.history=history;self.initial=initial;self.maintenance=maintenance
        self.pair_initial=pair_initial;self.pair_maintenance=pair_maintenance
        self.source=scenario_source if scenario_source is not None else ScenarioSource(history,initial,maintenance)

    def margin(self,net,level='initial'):
        months=tuple(sorted(net));q=np.array([net[x] for x in months],dtype=float)
        outright,pair=(self.initial,self.pair_initial) if level=='initial' else (self.maintenance,self.pair_maintenance)
        return float(margin_vector(months,q,outright,pair)[0])

    def requirements(self,net,now,candidate=None,quantities=None,universe_months=()):
        months=set(k for k,v in net.items() if v)|set(universe_months)
        if candidate is not None:months.update([candidate['leg_near'],candidate['leg_far']])
        months=tuple(sorted(months));ns=np.asarray([0] if quantities is None else quantities,dtype=float)
        if not months:
            zero=np.zeros(len(ns))
            return zero,zero,zero,dict(common_observations=0,history_start='',history_end='',latest_history_available_ts=np.nan,max_5interval_calendar_days=0,scenario_rows=0,months='')
        matrices,masks,info=self.source.scenarios(months,float(now))
        existing=np.array([net.get(x,0) for x in months],dtype=float);unit=np.zeros(len(months))
        if candidate is not None:
            unit[months.index(candidate['leg_near'])]=-candidate['cme_direction_sign']
            unit[months.index(candidate['leg_far'])]=candidate['cme_direction_sign']
        q=existing[:,None]+unit[:,None]*ns[None,:]
        current_initial=margin_vector(months,q,self.initial,self.pair_initial)
        current_maintenance=margin_vector(months,q,self.maintenance,self.pair_maintenance)
        required=current_initial.copy()
        for mask,coef in zip(masks,matrices):
            k=margin_vector(months,q*mask[:,None],self.maintenance,self.pair_maintenance)
            old=coef@existing;new=coef@unit
            for start in range(0,len(ns),256):
                end=min(start+256,len(ns))
                loss=np.max(-old[:,None]-new[:,None]*ns[None,start:end],axis=0)
                required[start:end]=np.maximum(required[start:end],k[start:end]+loss)
        return required,current_initial,current_maintenance,info
