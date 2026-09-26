import numpy as np
import pandas as pd
import pytest
from shapely.geometry import box
from shapely import STRtree

from poseidon.twin import global_forecast as gf
from poseidon.twin.global_coast import land_points
from poseidon.twin.voyage import route_geometry
from poseidon.api.global_ocean import mercator_rgba
from poseidon.ingest import global_wave as ingest
from poseidon.core.types import Cycle

pytestmark = pytest.mark.unit


@pytest.fixture
def grid():
    return {'hs':np.tile([2.,4.,6.,8.],(3,1)),
            'primary_period':np.ones((3,4))*10, 'primary_direction':np.ones((3,4))*359,
            'wind_u':np.ones((3,4))*3,'wind_v':np.ones((3,4))*4}


def test_periodic_longitudes_match_at_dateline_and_greenwich(grid):
    for a,b in [(-180,180),(-90,270),(0,360),(-.1,359.9)]:
        assert gf.spatial(grid,0,a) == gf.spatial(grid,0,b)
    assert gf.spatial(grid,0,315)['hs'] == 5  # Wrap interpolation uses 270 and 0, not a clamp.
    assert gf.spatial(grid,90,0)['hs'] == 2
    assert gf.spatial(grid,-90,0)['hs'] == 2


def test_global_missing_cells_do_not_dilute_ocean_or_invent_period(grid):
    grid['hs'][:,0] = np.nan
    assert gf.spatial(grid,0,315)['hs'] == 8
    assert gf.spatial(grid,0,0) is None
    grid['primary_period'][:,3] = np.nan
    assert gf.spatial(grid,0,315)['primary_period'] is None


def test_global_time_no_extrapolation_and_primary_definition(grid,monkeypatch):
    second = {k:v.copy() for k,v in grid.items()}
    second['hs'] *= 2
    second['primary_direction'][:] = 1
    monkeypatch.setattr(gf,'frame',lambda cycle,step:grid if step==0 else second)
    g = gf.GlobalForecast({'cycle':'20260911T06','leads_h':[0,6]})
    p = g.sample(0,90,g.start+pd.Timedelta(hours=3))
    assert p['values']['hs'] == 6
    assert p['values']['primary_direction'] == 359  # Nearest time, ties earlier.
    assert p['values']['tp'] is p['values']['dirp'] is None
    assert p['wind']['speed_ms'] == 5
    assert p['wind']['u10_ms'] is None  # Surface field is not silently relabelled 10 m.
    assert p['applicability']['verdict'] == 'no_coverage'
    for hour in (-.01,6.01):
        p = g.sample(0,90,g.start+pd.Timedelta(hours=hour))
        assert p['status'] == 'outside_forecast_time'
        assert p['values']['hs'] is None


def test_wgs84_dateline_route_is_short_and_long_haul_allowed():
    g = route_geometry([{'lat':0,'lon':179},{'lat':0,'lon':-179}])
    assert g['distance_nm'] == pytest.approx(120.21543,abs=1e-5)
    assert min(abs(p['lon']) for p in g['points']) >= 179
    points = [{'lat':0,'lon':0},{'lat':0,'lon':90}]
    with pytest.raises(ValueError):
        route_geometry(points)
    assert route_geometry(points,max_distance_nm=25000)['distance_nm'] > 5000


def test_land_index_and_longitude_wrap():
    tree = STRtree([box(-180,-10,-170,10),box(10,-10,20,10)])
    assert land_points([0,0,0,0],[185,-175,15,90],tree).tolist() == [True,True,True,False]


def test_global_raster_projects_latitude_and_wraps_longitudes():
    hs = np.full((181,360),np.nan)
    hs[150:,:] = 3  # >=60 N must occupy Mercator northern latitudes, not equator.
    rgba = mercator_rgba(hs,width=360,height=181)
    assert rgba.shape == (181,360,4)
    assert rgba[0,:,3].max() > 0
    assert rgba[90,:,3].max() == 0
    assert rgba[-1,:,3].max() == 0


def test_manifest_path_validation(tmp_path,monkeypatch):
    monkeypatch.setattr(gf,'root',lambda:tmp_path)
    for cycle in ('../20260911T06','20260911T25','20260911T07'):
        with pytest.raises(ValueError):
            gf.manifest(cycle)
    with pytest.raises(FileNotFoundError):
        gf.manifest('20260911T06')


def test_failed_download_keeps_previous_published_cycle(tmp_path,monkeypatch):
    monkeypatch.setattr(ingest,'root',lambda:tmp_path)
    monkeypatch.setattr(ingest.time,'sleep',lambda seconds:None)
    cycle = Cycle(pd.Timestamp('2026-09-11T06:00Z').to_pydatetime())
    monkeypatch.setattr(ingest,'discover',lambda client:cycle)
    monkeypatch.setattr(ingest,'STEPS',(0,6))
    class Client:
        def __init__(self,**kw): pass
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def get(self,*a,**kw):raise ValueError('test unavailable')
    monkeypatch.setattr(ingest.httpx,'Client',Client)
    old={'cycle':'20260911T00'}
    ingest.atomic_json(tmp_path/'latest.json',old)
    with pytest.raises(ValueError):
        ingest.refresh()
    assert ingest.read_json(tmp_path/'latest.json') == old
    assert ingest.read_json(tmp_path/'status.json')['state'] == 'failed'
    assert not (tmp_path/'20260911T06/manifest.json').exists()
