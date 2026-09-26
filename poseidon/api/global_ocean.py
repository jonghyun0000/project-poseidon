"""Global provider endpoints; never attach regional-model skill to NOAA fields."""
import io
from typing import Literal

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from poseidon.ingest.global_wave import root, read_json, ensure_refresh, SOURCE
from poseidon.twin.global_forecast import manifest, frame, GlobalForecast
from poseidon.twin.global_coast import land_points

router = APIRouter()


def resolve(cycle=None):
    try:
        return manifest(cycle)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(503 if cycle is None else 404, str(exc)) from exc


def metadata(m):
    start = pd.to_datetime(m['cycle'], format='%Y%m%dT%H', utc=True)
    now = pd.Timestamp.now(tz='UTC')
    times = [(start+pd.Timedelta(hours=h)).isoformat() for h in m['leads_h']]
    age = (now-start).total_seconds()/3600
    latest = read_json(root() / 'latest.json')
    return {'source': 'global', 'provider': SOURCE, 'cycle': m['cycle'], 'level': 'GLOBAL',
            'bounds': {'west':-180,'east':180,'south':-85,'north':85},
            'data_bounds': {'west':-180,'east':180,'south':-90,'north':90},
            'resolution_deg': .25, 'leads_h': m['leads_h'], 'valid_times': times,
            'produced_at': None, 'retrieved_at': m['retrieved_at'], 'cycle_age_h': round(age,2),
            'covers_now': bool(start <= now <= pd.Timestamp(times[-1])),
            'freshness': 'realtime' if age <= 18 else 'delayed',
            'latest_cycle': latest['cycle'] if latest else m['cycle'],
            'hs_color_max':12, 'hs_color_gamma':.55, 'corrected':False,
            'definitions':m['definitions'], 'refresh':read_json(root() / 'status.json'),
            'validation': 'Limited observational comparison: /v1/global/validation; point/global accuracy not established',
            'validation_url': '/v1/global/validation',
            'missing_policy':'Land, ice and provider gaps remain null; no temporal extrapolation.'}


@router.get('/v1/global/meta')
def global_meta(cycle: str | None = None):
    ensure_refresh()
    return metadata(resolve(cycle))


@router.get('/v1/global/status')
def global_status():
    return read_json(root() / 'status.json') or {'state':'not_started'}


@router.get('/v1/global/point')
def global_point(lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=360),
                 cycle: str | None = None):
    m = resolve(cycle)
    model = GlobalForecast(m)
    land = land_points([lat],[lon])
    on_land = bool(land[0]) if land is not None else False
    items = []
    try:
        for lead in m['leads_h']:
            when = model.start+pd.Timedelta(hours=lead)
            p = model.sample(lat,lon,when)
            if on_land:
                p['status'] = 'land_detected'
                p['values'] = dict.fromkeys(p['values'])
            items.append({**p, 'valid_time':when.isoformat(), 'lead_h':lead,
                          'q50':p['values']['hs'], 'physics_raw':p['values']['hs'],
                          'model_version':'NOAA GFS-Wave raw; no Poseidon correction'})
    except (OSError, ValueError, KeyError) as exc:
        raise HTTPException(503, '전 지구 캐시를 읽지 못했습니다. 데이터 상태를 확인하세요.') from exc
    return {'lat':lat, 'lon':(lon+180)%360-180, 'cycle':m['cycle'], 'source':'global',
            'provider':SOURCE,'level':'GLOBAL','produced_at':None,'retrieved_at':m['retrieved_at'],
            'corrected':False,'model_version':'NOAA GFS-Wave raw', 'items':items,
            'engine': {'provider':SOURCE,'resolution_deg':.25,'time_interval_h':6,
                       'definitions':m['definitions']},
            'land_screening': 'land_detected' if on_land else 'no_land_detected' if land is not None else 'unavailable'}


def mercator_rgba(hs, width=1440, height=720):
    """Reproject the global lat/lon raster; an unwarped image would shift coastlines."""
    from matplotlib import colormaps
    max_y = np.arcsinh(np.tan(np.radians(85)))
    lat = np.degrees(np.arctan(np.sinh(np.linspace(max_y,-max_y,height))))
    lon = np.linspace(-180,180,width,endpoint=False)
    # Native values, nearest pixel display. Point/route interpolation is separate.
    jj = np.clip(np.rint((lat+90)/180*(hs.shape[0]-1)).astype(int),0,hs.shape[0]-1)
    ii = np.rint((lon%360)/360*hs.shape[1]).astype(int)%hs.shape[1]
    field = hs[np.ix_(jj,ii)]
    valid = np.isfinite(field) & (field>=0)
    rgba = colormaps['turbo']((np.clip(np.nan_to_num(field)/12,0,1))**.55)
    rgba[...,3] = valid*.86
    return (rgba*255).astype('uint8')


@router.get('/v1/global/hs.png')
def global_hs(cycle: str | None = None, lead: int = Query(default=0,ge=0,le=384),
              part: Literal['all','west','east'] = 'all'):
    from PIL import Image
    m = resolve(cycle)
    if lead not in m['leads_h']:
        raise HTTPException(422, '전 지구 지도는 6시간 간격 리드를 사용합니다.')
    try:
        hs = frame(m['cycle'],lead)['hs']
    except (OSError, ValueError, KeyError) as exc:
        raise HTTPException(503,'전 지구 지도 자료를 읽지 못했습니다.') from exc
    stream = io.BytesIO()
    rgba = mercator_rgba(hs)
    if part != 'all':
        rgba = rgba[:,:720] if part == 'west' else rgba[:,720:]
    Image.fromarray(rgba).save(stream,format='PNG')
    return Response(stream.getvalue(),media_type='image/png',headers={'Cache-Control':'max-age=300'})
