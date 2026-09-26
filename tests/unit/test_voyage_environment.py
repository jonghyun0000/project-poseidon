"""Analytic vector cases and supplied-curve accounting, not ship validation."""
import numpy as np
import pandas as pd
import pytest
import xarray as xr

from poseidon.twin.environment import KNOT_MS, sample_wind, wind_summary
from poseidon.twin.performance import arrival_constraint, fuel_baseline

pytestmark = pytest.mark.unit


@pytest.fixture
def wind():
    return xr.Dataset({name: (('time', 'latitude', 'longitude'), np.zeros((2, 2, 2)))
                       for name in ('u10', 'v10')},
                      coords={'time': np.array(['2026-09-06T18', '2026-09-06T20'], dtype='datetime64[ns]'),
                              'latitude': [29., 31.], 'longitude': [134., 136.]})


def sample(ds, speed=0, course=0, when='2026-09-06T19:00Z', lat=30):
    return sample_wind(ds, lat, 135, pd.Timestamp(when), speed, course)


def test_space_time_interpolate_vectors_before_direction(wind):
    # Midpoint averages all eight corners: u=3.5, v=7, regardless of compass wrap.
    wind.u10.values[:] = np.arange(8).reshape(2, 2, 2)
    wind.v10.values[:] = 7
    p = sample(wind)
    assert p['u10_ms'] == 3.5
    assert p['v10_ms'] == 7
    assert p['speed_ms'] == pytest.approx(np.hypot(3.5, 7))
    assert p['from_deg'] == pytest.approx(206.565051177)
    wind.u10.values[:] = np.array([-1., 1.])[:, None, None]
    wind.v10.values[:] = -10
    assert sample(wind)['from_deg'] == pytest.approx(0)


@pytest.mark.parametrize('u,v,course,apparent,direction', [
    (0, 0, 0, 5, 0),         # Still air, ship going north at 5 m/s: apparent from north.
    (0, -10, 0, 15, 0),      # Headwind.
    (0, 10, 0, 5, 180),      # Following wind faster than vessel.
    (0, 5, 0, 0, None),      # Vessel moves with air: undefined direction.
    (0, 0, 90, 5, 90),      # Eastbound in still air.
])
def test_apparent_wind_known_vectors(wind, u, v, course, apparent, direction):
    wind.u10.values[:] = u
    wind.v10.values[:] = v
    p = sample(wind, speed=5/KNOT_MS, course=course)
    assert p['apparent_speed_ms'] == pytest.approx(apparent)
    if direction is None:
        assert p['apparent_from_deg'] is None
    else:
        assert p['apparent_from_deg'] == pytest.approx(direction)


def test_wind_no_extrapolation_and_missingness(wind):
    assert sample(None)['speed_ms'] is None
    assert sample(wind[['u10']])['status'] == 'not_available'
    assert sample(wind, lat=32)['status'] == 'outside_domain'
    for when in ['2026-09-06T17:59Z', '2026-09-06T20:01Z']:
        assert sample(wind, when=when)['status'] == 'outside_forecast_time'
    assert sample(wind, when='2026-09-06T20:00Z')['status'] == 'ok'
    assert sample(wind)['speed_ms'] == 0
    assert sample(wind)['from_deg'] is None
    wind.u10.values[0, 0, 0] = np.nan
    assert sample(wind)['status'] == 'missing_values'
    assert sample(wind)['apparent_speed_ms'] is None


def test_wind_coverage_is_time_weighted_and_preserves_gaps():
    pts = [{'elapsed_h': t, 'wind': {'status': 'ok' if w is not None else 'not_available',
                                    'speed_ms': w}} for t, w in [(0, 5), (2, 7), (3, None), (4, 8)]]
    assert wind_summary(pts) == {'max_wind_ms': 8, 'wind_coverage_pct': 50}


@pytest.fixture
def profile():
    return {'name': 'Test only', 'source': 'Synthetic unit test fixture',
            'source_kind': 'synthetic_example', 'load_condition': 'No actual vessel',
            'fuel_type': 'hfo', 'assume_zero_current': True,
            'curve': [{'speed_stw_kn': 10, 'fuel_t_day': 12}, {'speed_stw_kn': 20, 'fuel_t_day': 36}]}


def test_fuel_interpolation_units_and_explicit_synthetic_source(profile):
    f = fuel_baseline(profile, 15, 12)
    assert f['fuel_rate_t_day'] == 24
    assert f['fuel_t'] == 12
    assert f['co2_t'] == pytest.approx(37.368)
    assert f['status'] == 'synthetic_example'
    assert f['source'] == profile['source']
    assert f['source_verification'] == 'user_declared_not_verified'
    assert 'no port/departure-wait fuel' in f['scope']


@pytest.mark.parametrize('fuel_type,factor', [('hfo', 3.114), ('lfo', 3.151), ('mdo', 3.206)])
def test_declared_curves_and_fuel_specific_co2(profile, fuel_type, factor):
    profile.update(source_kind='measured', fuel_type=fuel_type)
    f = fuel_baseline(profile, 10, 24)
    assert f['status'] == 'baseline_estimate'
    assert f['fuel_t'] == 12
    assert f['co2_t'] == pytest.approx(12*factor)


def test_fuel_never_fabricated_or_extrapolated(profile):
    assert fuel_baseline(None, 14, 24)['fuel_t'] is None
    for speed in (9.99, 20.01):
        f = fuel_baseline(profile, speed, 24)
        assert f['status'] == 'outside_curve'
        assert f['fuel_t'] is f['co2_t'] is None
    assert fuel_baseline(profile, 20, 24)['fuel_t'] == 36
    f = fuel_baseline(profile, 15, 12, invalid_route=True)
    assert f['status'] == 'invalid_route'
    assert f['fuel_t'] is None


@pytest.mark.parametrize('deadline,status,margin', [
    ('2026-09-07T09:00+09:00', 'on_time', 0),
    ('2026-09-07T01:00Z', 'on_time', 1),
    ('2026-09-06T23:30Z', 'late', -.5),
])
def test_arrival_deadline_utc_equivalence(deadline, status, margin):
    r = arrival_constraint('2026-09-07T00:00Z', deadline)
    assert r['status'] == status
    assert r['margin_h'] == margin


def test_arrival_unset_and_invalid_route():
    assert arrival_constraint('2026-09-07T00:00Z', None)['margin_h'] is None
    r = arrival_constraint('2026-09-07T00:00Z', '2026-09-07T01:00Z', invalid_route=True)
    assert r['status'] == 'invalid_route'
    assert r['margin_h'] is None
