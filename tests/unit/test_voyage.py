import numpy as np
import pandas as pd
import pytest
import xarray as xr

from poseidon.twin.voyage import route_geometry, sample_weather, screening, simulate, exposure_summary

pytestmark = pytest.mark.unit


@pytest.fixture
def wave():
    shape = (2, 2, 2)
    one = np.ones(shape)
    angles = np.radians([271., 269.])[:, None, None]  # compass FROM 359°, 1°
    return xr.Dataset({
        'hs': (('lead','latitude','longitude'), one*np.array([2.,4.])[:,None,None]),
        'm0': (('lead','latitude','longitude'), one),
        'm1': (('lead','latitude','longitude'), one/8),
        'm2': (('lead','latitude','longitude'), one/64),
        'a1': (('lead','latitude','longitude'), one*np.cos(angles)),
        'b1': (('lead','latitude','longitude'), one*np.sin(angles)),
        'tp': (('lead','latitude','longitude'), one*10),
        'dirp': (('lead','latitude','longitude'), one*np.array([359.,1.])[:,None,None]),
    }, coords={'lead':[0.,3.], 'latitude':[29.,31.], 'longitude':[134.,136.],
               'valid_time':('lead',np.array(['2026-09-06T18:00','2026-09-06T21:00'],dtype='datetime64[ns]'))})


def test_wgs84_distance_and_waypoints():
    g=route_geometry([{'lat':0,'lon':0},{'lat':0,'lon':1},{'lat':1,'lon':1}],5)
    # WGS84 equatorial circumference / 360; meridional leg differs on ellipsoid.
    assert g['legs_nm'][0]*1852 == pytest.approx(111319.490793,abs=1e-5)
    assert g['points'][-1]['lat'] == pytest.approx(1)
    assert g['points'][-1]['lon'] == pytest.approx(1)
    assert any(abs(p['lat'])<1e-10 and abs(p['lon']-1)<1e-10 for p in g['points'])
    assert max(np.diff([p['distance_nm'] for p in g['points']])) <= 5+1e-9


def test_duplicate_waypoint_rejected():
    with pytest.raises(ValueError,match='중복'):
        route_geometry([{'lat':30,'lon':135}]*2)


def test_space_time_and_angle_wrap(wave):
    p=sample_weather(wave,np.ones((2,2),bool),30,135,pd.Timestamp('2026-09-06T19:30Z'))
    assert p['values']['hs'] == 3
    assert min(p['values']['dirm'],360-p['values']['dirm']) < 1e-8
    assert p['values']['dirp'] == 359  # nearest-time tie goes earlier, never 180
    assert p['values']['tm02'] == pytest.approx(8)


@pytest.mark.parametrize('when',['2026-09-06T17:59Z','2026-09-06T21:01Z'])
def test_no_temporal_extrapolation(wave,when):
    p=sample_weather(wave,np.ones((2,2),bool),30,135,pd.Timestamp(when))
    assert p['status']=='outside_forecast_time'
    assert p['values']['hs'] is None


def test_land_mask_and_missing_moments(wave):
    t=pd.Timestamp('2026-09-06T18:00Z')
    assert sample_weather(wave,np.zeros((2,2),bool),30,135,t)['status']=='land_or_dry'
    assert sample_weather(wave,None,30,135,t)['status']=='mask_unavailable'
    assert sample_weather(wave,np.ones((2,2),bool),40,135,t)['status']=='outside_domain'
    wave=wave[['hs']]
    p=sample_weather(wave,np.ones((2,2),bool),30,135,t)
    assert p['values']['hs']==2
    assert p['values']['tp'] is None
    assert p['missing']['tp']=='not_stored'


def test_land_does_not_dilute_waves(wave):
    sea=np.array([[True,False],[True,False]])
    wave['hs'].values[:,:,1]=0
    p=sample_weather(wave,sea,30,135,pd.Timestamp('2026-09-06T18:00Z'))
    assert p['values']['hs']==2
    assert p['land_frac']==0.5


def test_exposure_integrates_threshold_crossing_and_preserves_gaps():
    pts=[{'elapsed_h':t,'values':{'hs':h}} for t,h in [(0,2),(2,4),(3,None),(4,4)]]
    s=exposure_summary(pts,3)
    assert s['covered_duration_h']==2
    assert s['above_threshold_h']==1
    assert s['coverage_pct']==50
    assert s['mean_hs_m']==3


def test_sog_controls_eta(wave):
    g=route_geometry([{'lat':30,'lon':134.5},{'lat':30,'lon':135}],5)
    start=pd.Timestamp('2026-09-06T18:00Z')
    slow=simulate(wave,np.ones((2,2),bool),g,start,10,3)
    fast=simulate(wave,np.ones((2,2),bool),g,start,20,3)
    assert slow['summary']['duration_h']==pytest.approx(2*fast['summary']['duration_h'])
    assert pd.Timestamp(fast['arrival_utc'])-start==pd.Timedelta(hours=g['distance_nm']/20)
    assert fast['summary']['coverage_pct']==pytest.approx(100)


def test_land_screen_interior_crossing():
    lat,lon=np.linspace(-1,1,9),np.linspace(0,2,9)
    z=np.full((9,9),-100.);z[:,4]=20.
    terrain=xr.DataArray(z,coords={'lat':lat,'lon':lon},dims=('lat','lon'))
    g=route_geometry([{'lat':0,'lon':0},{'lat':0,'lon':2}],1)
    result=screening(g,terrain)
    assert result['status']=='land_detected'
    assert result['land_count']>0
    assert screening(g,None)['status']=='unavailable'
