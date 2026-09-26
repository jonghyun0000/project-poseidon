"""Descriptive Hs verification, with cycle clustering and explicit coverage.

Error = forecast - observed. RMSE/MAE/bias are paired-sample weighted;
SI = standard deviation of errors / mean observed Hs (bias removed).
Cycle bootstrap is a sensitivity diagnostic, not an independence guarantee:
adjacent cycles and overlapping valid times share weather.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

REGIONS = {
    'north_pacific':'북태평양 구역', 'south_pacific':'남태평양 구역',
    'north_atlantic':'북대서양 구역', 'south_atlantic':'남대서양 구역',
    'indian':'인도양 구역', 'mediterranean':'지중해 구역',
    'arctic':'북극 구역', 'southern':'남극해 구역',
}
HS_BANDS = [('0_2','0–2 m',0,2), ('2_4','2–4 m',2,4),
            ('4_6','4–6 m',4,6), ('6_plus','6 m 이상',6,float('inf'))]


def region(lat, lon):
    """Declared disjoint geographic sectors, not hydrographic basin polygons."""
    lon = (lon+180)%360-180
    if lat >= 66.5:
        return 'arctic'
    if lat <= -60:
        return 'southern'
    if 30 <= lat < 47 and -6 <= lon < 42:
        return 'mediterranean'
    if (lat < 0 and 20 <= lon < 140) or (0 <= lat < 30 and 40 <= lon < 100):
        return 'indian'
    if lat >= 0:
        return 'north_pacific' if lon >= 100 or lon < (-70 if lat < 10 else -100) else 'north_atlantic'
    return 'south_pacific' if lon >= 140 or lon < -70 else 'south_atlantic'


def metrics(rows, bootstrap=False):
    d = pd.DataFrame(rows)
    if d.empty:
        return {'n':0, 'n_stations':0, 'n_cycles':0, 'n_unique_observations':0,
                'n_valid_times':0, 'rmse_m':None, 'mae_m':None, 'bias_m':None,
                'scatter_index':None, 'centered_rmse_m':None, 'observed_min_m':None, 'observed_max_m':None,
                'cycle_bootstrap_95':None}
    e = np.asarray(d.forecast_hs_m-d.observed_hs_m, dtype=float)
    mean_obs = float(d.observed_hs_m.mean())
    out = {'n':len(d), 'n_stations':int(d.station_id.nunique()), 'n_cycles':int(d.cycle.nunique()),
           'n_unique_observations':len(d[['station_id','observation_time']].drop_duplicates()),
           'n_valid_times':int(d.valid_time.nunique()), 'rmse_m':float(np.sqrt(np.mean(e*e))),
           'mae_m':float(np.mean(np.abs(e))), 'bias_m':float(np.mean(e)),
           'centered_rmse_m':float(np.std(e)),
           'scatter_index':float(np.std(e)/mean_obs) if mean_obs > 1e-8 else None,
           'observed_min_m':float(d.observed_hs_m.min()), 'observed_max_m':float(d.observed_hs_m.max()),
           'cycle_bootstrap_95':None}
    if bootstrap and out['n_cycles'] >= 5:
        stats = d.assign(error=e, squared=e*e).groupby('cycle').agg(
            n=('error','size'), error=('error','sum'), squared=('squared','sum')).to_numpy()
        rng = np.random.default_rng(240911)
        idx = rng.integers(0,len(stats),(2000,len(stats)))
        totals = stats[idx].sum(axis=1)
        out['cycle_bootstrap_95'] = {
            'rmse_m':np.quantile(np.sqrt(totals[:,2]/totals[:,0]), [.025,.975]).tolist(),
            'bias_m':np.quantile(totals[:,1]/totals[:,0], [.025,.975]).tolist(),
            'replicates':2000, 'seed':240911,
            'interpretation':'Cycle-cluster sensitivity only; adjacent cycles are not independent.'}
        out['leave_one_cycle_out_rmse_m'] = [float(np.sqrt(
            (stats[:,2].sum()-v[2])/(stats[:,0].sum()-v[0]))) for v in stats]
    station_rmse = d.assign(squared=e*e).groupby('station_id').squared.mean().pow(.5)
    out['station_balanced_mean_rmse_m'] = float(station_rmse.mean())
    return out


def summarize(rows, stations, leads):
    by_lead = [{'lead_h':h, **metrics([p for p in rows if p['lead_h']==h])} for h in leads]
    by_region = [{'region':key, 'label':label,
                  **metrics([p for p in rows if p['region']==key])} for key,label in REGIONS.items()]
    by_hs = [{'band':key, 'label':label,
              **metrics([p for p in rows if low <= p['observed_hs_m'] < high])}
             for key,label,low,high in HS_BANDS]
    station_metrics = [{**{k:s.get(k) for k in ('station_id','name','lat','lon','owner','pgm')},
                        'region':region(s['lat'],s['lon']),
                        **metrics([p for p in rows if p['station_id']==s['station_id']])} for s in stations]
    for station in station_metrics:
        matched = [p for p in rows if p['station_id']==station['station_id']]
        if matched:
            latest = max(matched,key=lambda p:p['observation_time'])
            station.update(lat=latest['lat'],lon=latest['lon'],region=latest['region'],
                           position_source='latest matched historical deployment')
        else:
            station['position_source'] = 'current catalog; no scored observation'
    return {'overall':metrics(rows,bootstrap=True), 'by_lead':by_lead, 'by_region':by_region,
            'by_hs':by_hs, 'stations':station_metrics}
