"""Voyage API contract and real archived forecast coverage (local lake)."""
import copy

import pytest
from fastapi.testclient import TestClient

from poseidon.api.app import app
from poseidon.core.config import settings

pytestmark = pytest.mark.integration


@pytest.fixture
def request_body():
    return {'cycle':'20260906T18','departure_utc':'2026-09-06T18:00:00Z',
            'waypoints':[{'lat':30,'lon':135},{'lat':29.5,'lon':135}],
            'scenarios':[{'name':'기준','speed_kn':14}], 'hs_threshold_m':3}


@pytest.mark.parametrize('edit',[
    {'departure_utc':'2026-09-06T18:00:00'},
    {'departure_utc':'9999-01-01T00:00:00Z'},
    {'scenarios':[{'speed_kn':0}]},
    {'waypoints':[{'lat':30,'lon':135}]},
    {'waypoints':[{'lat':91,'lon':135},{'lat':30,'lon':135}]},
    {'cycle':'../../data'},
    {'hs_threshold_m':0},
    {'arrival_deadline_utc':'2026-09-07T00:00:00'},
    {'arrival_deadline_utc':'9999-01-01T00:00:00Z'},
])
def test_bad_requests_are_422(request_body,edit):
    request_body.update(edit)
    assert TestClient(app).post('/v1/voyage/analyze',json=request_body).status_code==422


@pytest.mark.skipif(not settings.catalog_path.exists(),reason='local lake required')
def test_real_forecast_and_expired_window(request_body):
    c=TestClient(app)
    r=c.post('/v1/voyage/analyze',json=request_body)
    assert r.status_code==200
    d=r.json();s=d['scenarios'][0]
    assert s['summary']['coverage_pct']==pytest.approx(100)
    assert s['points'][0]['values']['hs']>0
    assert d['corrected'] is False
    assert d['methods']['peak_variables']
    assert d['request']['departure_utc'].endswith('Z')
    assert d['schema_version'] == 'voyage-1.1'
    assert d['environment']['wind']['cycle'] == request_body['cycle']
    assert s['summary']['wind_coverage_pct'] == pytest.approx(100)
    assert s['points'][0]['wind']['speed_ms'] > 0
    assert s['fuel']['status'] == 'not_configured'
    assert s['fuel']['fuel_t'] is None
    late=copy.deepcopy(request_body);late['departure_utc']='2026-09-12T00:00:00Z'
    d=c.post('/v1/voyage/analyze',json=late).json();s=d['scenarios'][0]
    assert s['status']=='partial'
    assert s['summary']['coverage_pct']==0
    assert s['summary']['above_threshold_h'] is None
    assert all(p['values']['hs'] is None for p in s['points'])
    assert s['summary']['wind_coverage_pct'] == 0
    assert s['summary']['max_wind_ms'] is None
    assert all(p['wind']['status'] == 'outside_forecast_time' for p in s['points'])


@pytest.mark.skipif(not settings.catalog_path.exists(),reason='local lake required')
def test_korean_peninsula_crossing_is_invalid(request_body):
    request_body['waypoints']=[{'lat':36,'lon':125},{'lat':36,'lon':131}]
    d=TestClient(app).post('/v1/voyage/analyze',json=request_body).json()
    assert d['screening']['status']=='land_detected'
    assert d['scenarios'][0]['status']=='invalid_route'


@pytest.fixture
def profile():
    return {'name':'Synthetic API fixture', 'source':'No real vessel; API contract test',
            'source_kind':'synthetic_example', 'load_condition':'Test only',
            'fuel_type':'hfo', 'assume_zero_current':True,
            'curve':[{'speed_stw_kn':10,'fuel_t_day':12}, {'speed_stw_kn':20,'fuel_t_day':36}]}


@pytest.mark.parametrize('edit', [
    {'source':' '}, {'source_kind':'unknown'}, {'fuel_type':'lng'},
    {'assume_zero_current':False}, {'assume_zero_current':None},
    {'curve':[{'speed_stw_kn':10,'fuel_t_day':12}]},
    {'curve':[{'speed_stw_kn':10,'fuel_t_day':12},{'speed_stw_kn':10,'fuel_t_day':14}]},
    {'curve':[{'speed_stw_kn':20,'fuel_t_day':12},{'speed_stw_kn':10,'fuel_t_day':14}]},
    {'curve':[{'speed_stw_kn':10,'fuel_t_day':0},{'speed_stw_kn':20,'fuel_t_day':14}]},
    {'rate_scope':'main_engine_only'},
])
def test_bad_performance_profile_422(request_body, profile, edit):
    profile.update(edit)
    request_body['performance_profile'] = profile
    assert TestClient(app).post('/v1/voyage/analyze', json=request_body).status_code == 422


@pytest.mark.skipif(not settings.catalog_path.exists(),reason='local lake required')
def test_supplied_fuel_and_deadline_survive_missing_wind(request_body, profile, monkeypatch):
    import poseidon.api.voyage as voyage
    def unavailable(*args):
        raise OSError('test missing wind store')
    monkeypatch.setattr(voyage, '_wind', unavailable)
    request_body.update(performance_profile=profile, arrival_deadline_utc='2026-09-07T00:00Z',
                        scenarios=[{'speed_kn':14}, {'speed_kn':14,'departure_offset_h':6}, {'speed_kn':21}])
    response = TestClient(app).post('/v1/voyage/analyze', json=request_body)
    assert response.status_code == 200
    d = response.json()
    assert d['environment']['wind']['status'] == 'read_failed'
    a,b,c = d['scenarios']
    assert a['summary']['coverage_pct'] == 100  # Wind failure must not erase wave data.
    assert a['summary']['wind_coverage_pct'] == 0
    assert a['fuel']['status'] == 'synthetic_example'
    assert a['fuel']['fuel_t'] == pytest.approx(21.6*a['summary']['duration_h']/24)
    assert a['fuel']['fuel_t'] == b['fuel']['fuel_t']  # At-sea only; no waiting fuel.
    assert a['arrival_constraint']['status'] == 'on_time'
    assert b['arrival_constraint']['status'] == 'late'
    assert c['fuel']['status'] == 'outside_curve'
    assert c['fuel']['fuel_t'] is None
    assert d['request']['performance_profile']['source'] == profile['source']
