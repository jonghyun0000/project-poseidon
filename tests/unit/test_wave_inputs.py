"""GFS-Wave -> 예보 격자 매핑 (PHASE31). 기존 유한 셀은 어떤 모드에서도 비트 불변이어야 한다."""

import numpy as np
import pytest
import xarray as xr

from poseidon.scheduler.wave_inputs import INIT_FILL_MODES, grid_wave_fields

pytestmark = pytest.mark.unit

OFF = 4e-6   # 실제 원천 경도 어긋남(100.000004)을 재현 — 가중치가 (≈0, ≈1) 이 된다


def _source(dir_a=90.0, dir_b=90.0):
    """15.0~16.0N × 120~121E, 0.25° — (15.5N, 120.5E) 가 육지(NaN)."""
    lat = np.arange(15.0, 16.0 + 1e-9, 0.25)
    lon = np.arange(120.0, 121.0 + 1e-9, 0.25) + OFF
    hs = np.add.outer(np.arange(5) * 0.1, np.arange(5) * 0.01) + 1.0
    tp = np.full((5, 5), 8.0)
    dr = np.full((5, 5), 90.0)
    hs[2, 2] = tp[2, 2] = dr[2, 2] = np.nan
    dr[1, 2], dr[3, 2] = dir_a, dir_b          # 육지 셀의 남·북 이웃
    return xr.Dataset({"swh": (("latitude", "longitude"), hs),
                       "perpw": (("latitude", "longitude"), tp),
                       "dirpw": (("latitude", "longitude"), dr)},
                      coords={"latitude": lat, "longitude": lon})


LATS = np.arange(14.75, 16.0 + 1e-9, 0.25)     # 첫 행 14.75N 은 원천 범위 밖 (D1 재현)
LONS = np.arange(120.25, 121.0 - 0.25 + 1e-9, 0.25)


def test_none_is_plain_xarray_interp():
    ds = _source()
    hs, tp, dr, info = grid_wave_fields(ds, LATS, LONS, "none")
    i = {"latitude": xr.DataArray(LATS, dims="y"), "longitude": xr.DataArray(LONS, dims="x")}
    assert np.array_equal(hs, ds.swh.interp(**i).values, equal_nan=True)
    assert np.array_equal(dr, ds.dirpw.interp(**i).values, equal_nan=True)
    assert info["nan_before"] > 0


def test_zero_weight_nan_corner_poisons_plain_interp():
    """결함 재현: 가중치 ≈0 인 육지 모서리가 이웃 해상 셀을 NaN 으로 만든다.

    scipy 는 격자점 위의 점에 대해 아래쪽 구간을 고르므로 오염은 육지 격자점의
    **북쪽·동쪽** 셀에 난다. 남쪽·서쪽 셀은 멀쩡하다.
    """
    hs, *_ = grid_wave_fields(_source(), LATS, LONS, "none")
    assert np.isnan(hs[4, 1])       # 북쪽 (15.75N, 120.5E) — 자기 격자점은 바다
    assert np.isnan(hs[3, 2])       # 동쪽 (15.5N, 120.75E)
    assert np.isfinite(hs[2, 1])    # 남쪽 (15.25N, 120.5E) 은 오염되지 않는다


@pytest.mark.parametrize("mode", ["seanorm", "seanorm_nn1"])
def test_fill_never_changes_finite_cells(mode):
    base, btp, bdr, _ = grid_wave_fields(_source(), LATS, LONS, "none")
    hs, tp, dr, _ = grid_wave_fields(_source(), LATS, LONS, mode)
    fin = np.isfinite(base)
    assert np.array_equal(hs[fin], base[fin])            # 비트 불변
    assert np.array_equal(tp[fin], btp[fin]) and np.array_equal(dr[fin], bdr[fin])


def test_seanorm_recovers_own_node_but_does_not_extrapolate():
    ds = _source()
    hs, _, _, info = grid_wave_fields(ds, LATS, LONS, "seanorm")
    assert hs[4, 1] == pytest.approx(float(ds.swh.values[3, 2]), abs=1e-4)   # 북쪽 셀: 자기 격자점 값
    assert hs[3, 2] == pytest.approx(float(ds.swh.values[2, 3]), abs=1e-4)   # 동쪽 셀
    assert np.isnan(hs[3, 1])       # 육지 격자점 자체는 채우지 않는다 (먼 모서리 가중 ≈0)
    assert np.all(np.isnan(hs[0]))  # 원천 범위 밖(14.75N)은 어떤 모드도 채우지 않는다
    assert info["out_of_source"] == len(LONS) and info["filled_seanorm"] > 0


def test_nn1_fills_land_node_from_nearest_sea_node_within_one_cell():
    ds = _source()
    hs, _, _, info = grid_wave_fields(ds, LATS, LONS, "seanorm_nn1")
    # 육지 격자점(15.5N,120.5E)의 등거리 4방향 이웃 평균
    nb = ds.swh.values[[1, 3, 2, 2], [2, 2, 1, 3]]
    assert hs[3, 1] == pytest.approx(float(nb.mean()), abs=1e-4)
    assert info["filled_nn1"] == 1
    assert np.all(np.isnan(hs[0]))  # 범위 밖은 여전히 NaN


def test_nn1_direction_is_circular_mean():
    """350°와 10°를 평균하면 0° 여야 한다 — 산술 평균이면 180° 가 된다."""
    ds = _source(dir_a=350.0, dir_b=10.0)
    # 동·서 이웃도 같은 거리이므로 둘을 0° 로 맞춰 네 방향 평균이 0° 가 되게 한다
    ds["dirpw"].values[2, 1] = 0.0
    ds["dirpw"].values[2, 3] = 0.0
    _, _, dr, _ = grid_wave_fields(ds, LATS, LONS, "seanorm_nn1")
    assert min(dr[3, 1], 360.0 - dr[3, 1]) < 1e-6


def test_unknown_mode_rejected():
    with pytest.raises(ValueError):
        grid_wave_fields(_source(), LATS, LONS, "extrapolate_everything")
    assert INIT_FILL_MODES[0] == "none"


def test_run_wave_forecast_requires_out_tag_for_fill():
    from poseidon.scheduler.wave_cycle import run_wave_forecast
    with pytest.raises(ValueError, match="out_tag"):
        run_wave_forecast("20260101T00", 1.0, init_fill="seanorm")
    with pytest.raises(ValueError, match="init_fill"):
        run_wave_forecast("20260101T00", 1.0, init_fill="bogus", out_tag="x")


def test_collocate_refuses_to_write_diagnostic_output():
    from poseidon.validation.collocate import collocate_wave
    with pytest.raises(ValueError, match="write=False"):
        collocate_wave("20260101T00", source_id="spectral_wave-L1-diag")
