"""보간 순서·육지 마스크·결측 회귀."""
import numpy as np
import pytest
import xarray as xr
from poseidon.api.point_values import sample_variables

pytestmark = pytest.mark.unit


def test_moments_before_period_and_circular_direction():
    # 서로 다른 에너지·주기, 북쪽 양옆 방향: 각도 평균(180도) 금지.
    m0 = np.array([[[1., 4.], [0., 0.]]])
    theta = np.deg2rad(np.array([[[271., 269.], [0., 0.]]]))
    ds = xr.Dataset({k: (("lead", "latitude", "longitude"), v) for k, v in {
        "m0": m0, "m1": np.array([[[.1, .2], [0., 0.]]]), "m2": m0 / 100,
        "a1": m0 * np.cos(theta), "b1": m0 * np.sin(theta),
        "tp": np.array([[[10., 20.], [np.nan, np.nan]]]),
        "dirp": np.array([[[359., 1.], [np.nan, np.nan]]]),
    }.items()})
    out = sample_variables(ds, np.array([0, 0, 1, 1]), np.array([0, 1, 0, 1]),
                           np.full(4, .25), np.array([1, 1, 0, 0]),
                           ["tm01", "dirm", "tp", "dirp"])
    assert out["tm01"][0] == pytest.approx(5 / .3)
    assert min(out["dirm"][0], 360 - out["dirm"][0]) < 1.1
    assert out["tp"][0] == 10
    assert out["dirp"][0] == 359


def test_old_forecast_does_not_invent_periods():
    ds = xr.Dataset({"hs": (("lead", "latitude", "longitude"), np.ones((2, 2, 2)))})
    out = sample_variables(ds, np.array([0, 0, 1, 1]), np.array([0, 1, 0, 1]),
                           np.full(4, .25), np.ones(4), ["tp", "tm02", "dirp"])
    assert all(np.isnan(v).all() for v in out.values())
