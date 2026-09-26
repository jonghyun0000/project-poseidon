import pytest
from fastapi.testclient import TestClient
from poseidon.api.app import app
from poseidon.ingest.global_wave import root

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not (root()/'latest.json').exists(), reason='local global cache required')]


def test_global_point_variables_cycle_and_land(monkeypatch):
    monkeypatch.setattr('poseidon.api.global_ocean.ensure_refresh',lambda:None)
    c=TestClient(app)
    m=c.get('/v1/global/meta').json()
    assert m['source']=='global'
    assert m['leads_h'][-1]==384
    for lat,lon in [(45,-40),(-25,70),(30,-150)]:
        r=c.get('/v1/global/point',params={'lat':lat,'lon':lon,'cycle':m['cycle']})
        assert r.status_code==200
        d=r.json();p=d['items'][0]
        assert len(d['items'])==65
        assert p['q50']>0
        assert p['values']['primary_period']>0
        assert p['values']['tp'] is None
        assert p['applicability']['verdict']=='no_coverage'
        assert d['engine']['provider'].startswith('NOAA')
    d=c.get('/v1/global/point?lat=48.85&lon=2.35').json()
    assert d['land_screening']=='land_detected'
    assert all(p['q50'] is None for p in d['items'])
    assert c.get('/v1/global/meta?cycle=../../bad').status_code==422
    assert c.get('/v1/global/point?lat=91&lon=0').status_code==422
    assert c.get('/v1/global/hs.png?lead=7').status_code==422


def test_global_voyage_crosses_dateline_and_keeps_out_of_time_null():
    c=TestClient(app)
    from poseidon.twin.global_forecast import manifest,GlobalForecast
    model=GlobalForecast(manifest())
    request={'source':'global','cycle':model.cycle,'departure_utc':model.start.isoformat(),
             'waypoints':[{'lat':35,'lon':179},{'lat':35,'lon':-179}],
             'scenarios':[{'name':'seam','speed_kn':14}],'hs_threshold_m':3}
    r=c.post('/v1/voyage/analyze',json=request)
    assert r.status_code==200
    d=r.json();s=d['scenarios'][0]
    assert d['source']=='global'
    assert d['distance_nm']<120
    assert s['summary']['coverage_pct']==100
    assert s['fuel']['fuel_t'] is None
    assert d['screening']['status']=='no_land_detected'
    request['departure_utc']=(model.start+__import__('pandas').Timedelta(hours=400)).isoformat()
    d=c.post('/v1/voyage/analyze',json=request).json();s=d['scenarios'][0]
    assert s['status']=='partial'
    assert s['summary']['coverage_pct']==0
    assert s['summary']['above_threshold_h'] is None
    request['waypoints']=[{'lat':48.85,'lon':2.35},{'lat':49,'lon':3}]
    d=c.post('/v1/voyage/analyze',json=request).json()
    assert d['scenarios'][0]['status']=='invalid_route'
