"""Periodic global-grid sampling, preserving NOAA product definitions and gaps."""
from functools import lru_cache
import re

import numpy as np
import pandas as pd

from poseidon.ingest.global_wave import root, read_json
from poseidon.twin.environment import KNOT_MS


def manifest(cycle=None):
    if cycle is not None and not re.fullmatch(r'\d{8}T(?:00|06|12|18)', cycle):
        raise ValueError('전 지구 예보 사이클 형식이 올바르지 않습니다.')
    path = root() / cycle / 'manifest.json' if cycle else root() / 'latest.json'
    result = read_json(path)
    if not result:
        raise FileNotFoundError('전 지구 예보를 준비 중이거나 해당 사이클이 보관되어 있지 않습니다.')
    return result


@lru_cache(maxsize=4)
def frame(cycle, step):
    # Cycle/step must come from a published manifest, never arbitrary URL text.
    m = manifest(cycle)
    if step not in m['leads_h']:
        raise ValueError('보관되지 않은 예보 리드입니다.')
    with np.load(root() / cycle / f'f{step:03d}.npz', allow_pickle=False) as data:
        arrays = {k: data[k] for k in data.files}
    for array in arrays.values():
        array.flags.writeable = False
    return arrays


def corners(lat, lon, shape=(721, 1440)):
    """Canonical grid includes poles; longitude wraps instead of clamping at 180/360."""
    ny, nx = shape
    y = (lat+90)/180*(ny-1)
    x = (lon % 360)/360*nx
    j = min(max(int(np.floor(y)), 0), ny-2)
    i = int(np.floor(x)) % nx
    fy, fx = y-j, x-np.floor(x)
    return np.array([j,j,j+1,j+1]), np.array([i,(i+1)%nx,i,(i+1)%nx]), np.array([(1-fy)*(1-fx),(1-fy)*fx,fy*(1-fx),fy*fx])


def spatial(data, lat, lon):
    jj, ii, w = corners(lat, lon, data['hs'].shape)
    hs = data['hs'][jj, ii]
    wet = np.isfinite(hs) & (hs >= 0)
    ww = w*wet
    if ww.sum() < .05:
        return None
    result = {'hs': float(np.sum(np.where(wet, hs, 0)*ww)/ww.sum())}
    # Primary quantities: select the most influential wet cell, no arithmetic angle mean.
    k = int(np.argmax(ww))
    for name in ('primary_period', 'primary_direction'):
        value = float(data[name][jj[k], ii[k]])
        result[name] = value if np.isfinite(value) and (name != 'primary_period' or value > 0) else None
    # Forcing wind is masked on the wave grid. Use jointly valid u/v coastal weights.
    u, v = data['wind_u'][jj, ii], data['wind_v'][jj, ii]
    good = np.isfinite(u) & np.isfinite(v)
    weight = w*good
    for name, value in [('u',u),('v',v)]:
        result[name] = float(np.sum(np.where(good,value,0)*weight)/weight.sum()) if weight.sum() >= .05 else None
    return result


def wind_vector(u, v, speed, course):
    out = {'status': 'not_available', 'u10_ms': None, 'v10_ms': None, 'u_ms': u, 'v_ms': v,
           'speed_ms': None, 'from_deg': None, 'apparent_speed_ms': None, 'apparent_from_deg': None,
           'reference_level': 'surface forcing wind, NOAA GRIB level=surface'}
    if u is None or v is None:
        return out
    au = u-speed*KNOT_MS*np.sin(np.radians(course))
    av = v-speed*KNOT_MS*np.cos(np.radians(course))
    magnitude, apparent = float(np.hypot(u,v)), float(np.hypot(au,av))
    out.update(status='ok', speed_ms=magnitude, apparent_speed_ms=apparent,
               from_deg=float(np.degrees(np.arctan2(-u,-v))%360) if magnitude > 1e-8 else None,
               apparent_from_deg=float(np.degrees(np.arctan2(-au,-av))%360) if apparent > 1e-8 else None)
    return out


class GlobalForecast:
    def __init__(self, metadata):
        self.meta = metadata
        self.cycle = metadata['cycle']
        self.start = pd.to_datetime(self.cycle, format='%Y%m%dT%H', utc=True)
        self.leads = np.asarray(metadata['leads_h'])

    def sample(self, lat, lon, when, speed=0, course=0):
        lead = (pd.Timestamp(when)-self.start).total_seconds()/3600
        values = dict.fromkeys(('hs','tp','dirp','tm02','dirm','primary_period','primary_direction'))
        out = {'values': values, 'missing': {'tp':'not_provided','dirp':'not_provided',
                                            'tm02':'not_provided','dirm':'not_provided'},
               'status': 'ok', 'lead_h': lead, 'wind': wind_vector(None,None,speed,course),
               'applicability': {'verdict':'no_coverage','reasons':['global_provider_not_locally_validated'],
                                 'note':'부이 관측 대조 결과는 /v1/global/validation에서 확인할 수 있습니다. 이 지점·조건의 정확도는 확정되지 않았습니다.'}}
        if not -90 <= lat <= 90 or not np.isfinite(lon):
            out['status'] = 'outside_domain'
            return out
        if lead < self.leads[0] or lead > self.leads[-1]:
            out['status'] = 'outside_forecast_time'
            out['wind']['status'] = 'outside_forecast_time'
            return out
        hi = int(np.searchsorted(self.leads, lead))
        lo = hi if self.leads[hi] == lead else hi-1
        a = spatial(frame(self.cycle, int(self.leads[lo])), lat, lon)
        b = a if hi == lo else spatial(frame(self.cycle, int(self.leads[hi])), lat, lon)
        if a is None or b is None:
            out['status'] = 'provider_missing'
            return out
        f = (lead-self.leads[lo])/(self.leads[hi]-self.leads[lo]) if hi != lo else 0
        values['hs'] = a['hs']*(1-f)+b['hs']*f
        primary = a if f <= .5 else b
        values.update({k:primary[k] for k in ('primary_period','primary_direction')})
        out['primary_valid_time'] = (self.start+pd.Timedelta(hours=int(self.leads[lo if f <= .5 else hi]))).isoformat()
        u = a['u']*(1-f)+b['u']*f if a['u'] is not None and b['u'] is not None else None
        v = a['v']*(1-f)+b['v']*f if a['v'] is not None and b['v'] is not None else None
        out['wind'] = wind_vector(u,v,speed,course)
        return out
