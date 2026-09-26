"""Global voyage analysis using a single published NOAA cycle and WGS84 transit."""
from uuid import uuid4

import pandas as pd
from fastapi import HTTPException

from poseidon.api.global_ocean import resolve, metadata
from poseidon.twin.global_forecast import GlobalForecast
from poseidon.twin.global_coast import global_screening
from poseidon.twin.voyage import route_geometry, exposure_summary
from poseidon.twin.environment import wind_summary
from poseidon.twin.performance import fuel_baseline, arrival_constraint


def analyze_global(request):
    m = resolve(request.cycle)
    meta = metadata(m)
    model = GlobalForecast(m)
    waypoints = [w.model_dump() for w in request.waypoints]
    spacing = min(5, min(s.speed_kn for s in request.scenarios))
    try:
        geometry = route_geometry(waypoints, spacing, max_distance_nm=25000)
        if any(geometry['distance_nm']/s.speed_kn > 1440 for s in request.scenarios):
            raise ValueError('전 지구 항로 계산 한도는 시나리오당 60일입니다. 예보 범위 밖은 결측입니다.')
        check = global_screening(route_geometry(waypoints,1,max_distance_nm=25000))
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from exc
    invalid = check['status'] == 'land_detected'
    scenarios = []
    for scenario in request.scenarios:
        start = pd.Timestamp(request.departure_utc).tz_convert('UTC')+pd.Timedelta(hours=scenario.departure_offset_h)
        points = []
        try:
            for position in geometry['points']:
                elapsed = position['distance_nm']/scenario.speed_kn
                when = start+pd.Timedelta(hours=elapsed)
                p = {**position,**model.sample(position['lat'],position['lon'],when,scenario.speed_kn,position['course_deg']),
                     'valid_time':when.isoformat(),'elapsed_h':elapsed,'relative_wave_deg':None}
                points.append(p)
        except (OSError,ValueError,KeyError) as exc:
            raise HTTPException(503,'전 지구 예보 캐시를 읽지 못했습니다.') from exc
        summary = {**exposure_summary(points,request.hs_threshold_m),**wind_summary(points)}
        arrival = points[-1]['valid_time']
        scenarios.append({'name':scenario.name,'speed_kn':scenario.speed_kn,
                          'departure_offset_h':scenario.departure_offset_h,
                          'departure_utc':start.isoformat(),'arrival_utc':arrival,
                          'status':'invalid_route' if invalid else 'covered' if summary['coverage_pct']>99.999 else 'partial',
                          'points':points,'summary':summary,
                          'fuel':fuel_baseline(request.performance_profile.model_dump() if request.performance_profile else None,
                                               scenario.speed_kn,summary['duration_h'],invalid_route=invalid),
                          'arrival_constraint':arrival_constraint(arrival,request.arrival_deadline_utc,invalid_route=invalid)})
    return {'analysis_id':str(uuid4()),'schema_version':'voyage-1.2','created_at_utc':pd.Timestamp.now(tz='UTC').isoformat(),
            'request':request.model_dump(mode='json'),'source':'global','cycle':m['cycle'],'level':'GLOBAL',
            'produced_at':None,'retrieved_at':m['retrieved_at'],'corrected':False,
            'engine':{'provider':m['source'],'source_id':m['source_id'],'definitions':m['definitions']},
            'units':{'distance':'nautical_mile','speed':'knot_SOG','wind_speed':'m/s surface forcing wind',
                     'primary_period':'second','primary_direction':'degree true; NOAA DIRPW',
                     'fuel':'metric_tonne','co2':'metric_tonne combustion CO2'},
            'forecast_period':{'first':meta['valid_times'][0],'last':meta['valid_times'][-1],'covers_now':meta['covers_now']},
            'distance_nm':geometry['distance_nm'],'legs_nm':geometry['legs_nm'],'screening':check,'scenarios':scenarios,
            'environment':{'wind':{'status':'available','source':m['source']+' surface forcing wind','cycle':m['cycle']},
                           'current':{'status':'not_available'}},
            'source_records':m['records'],
            'methods':{'geometry':'WGS84 / GeographicLib; periodic longitude',
                       'motion':'constant SOG; instantaneous waypoint turns',
                       'hs':'finite ocean-cell normalized spatial weights; linear time; no extrapolation',
                       'primary_wave':'maximum finite ocean weight cell; nearest forecast time, ties earlier',
                       'wind':'surface forcing u/v, normalized finite spatial weights, linear time; apparent=wind minus SOG vector',
                       'sample_spacing_nm_max':spacing,'sample_interval_h_max':1,'provider_time_interval_h':6},
            'limitations':['NOAA 전 지구 원천 예보입니다. Poseidon 동아시아 모델의 검증 성적은 적용되지 않습니다.',
                           '원자료는 0.25도·6시간 간격입니다. 지점 수를 늘려도 원자료 해상도가 높아지지 않습니다.',
                           '육지·해빙·원천 결측을 임의로 채우지 않습니다. 최대 16일 예보 범위 밖은 결측입니다.',
                           '주 파주기/파향은 NOAA PERPW/DIRPW입니다. Tp·평균주기 Tm02와 동일한 변수로 취급하지 않습니다.',
                           '바람은 GFS-Wave GRIB surface 강제장입니다. 갑판 풍속·돌풍·풍저항은 계산하지 않습니다.',
                           '항로는 사용자 입력입니다. Natural Earth 육지 표본 검사는 항행 가능 승인·공식 해도가 아닙니다.',
                           '일정 SOG 도착 시각입니다. 해류·기상 감속·선회·입출항 대기는 제외합니다.',
                           '연료는 제공 곡선의 정온·무해류 해상 항해 기준값입니다. 연소 CO₂만 계산합니다.']}
