"""파향·주기 콜로케이션의 원형 통계와 육지 정합성.

과거 사고가 반복될 수 있는 두 자리를 고정한다.
  1. 각도를 산술 처리하면 359°와 1°가 최악의 오차로 집계된다.
  2. 육지 셀이 보간에 섞이면 값이 조용히 축소된다 (CLAUDE.md §6-1).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from poseidon.engines.spectral_wave import grid as spec
from poseidon.validation.collocate import (
    _CIRCULAR,
    _MOMENTS,
    _OBS_MAP,
    circular_error,
    sea_normalized,
    skill_by_lead,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("pred", "obs", "want"),
    [(359.0, 1.0, -2.0), (1.0, 359.0, 2.0), (10.0, 350.0, 20.0),
     (180.0, 0.0, -180.0), (90.0, 90.0, 0.0), (270.0, 89.0, -179.0)],
)
def test_circular_error_wraps(pred: float, obs: float, want: float) -> None:
    assert circular_error(pred, obs) == pytest.approx(want, abs=1e-9)


def test_circular_error_bounded() -> None:
    rng = np.random.default_rng(0)
    a, b = rng.uniform(0, 360, 500), rng.uniform(0, 360, 500)
    e = np.array([circular_error(p, o) for p, o in zip(a, b)])
    assert np.all(e >= -180.0) and np.all(e < 180.0)


def test_direction_is_registered_as_circular() -> None:
    """대응표와 순환 집합이 어긋나면 파향이 산술 통계로 새어나간다."""
    assert _OBS_MAP["dir"][1] in _CIRCULAR
    assert _OBS_MAP["hs"][1] not in _CIRCULAR
    assert _OBS_MAP["tp"][1] not in _CIRCULAR


def test_skill_splits_variables_and_uses_circular_stats() -> None:
    """한 프레임에 파고와 파향이 섞여도 각각의 단위로 집계된다."""
    rows = []
    for i in range(20):
        rows.append({"var": "hs", "lead_h": 0.0, "predicted": 1.0 + 0.01 * i,
                     "observed": 1.0, "land_frac": 0.0})
        # 예측 359°, 관측 1° — 실제 차이는 2°다
        rows.append({"var": "dir", "lead_h": 0.0, "predicted": 359.0,
                     "observed": 1.0, "land_frac": 0.0})
    out = skill_by_lead(pd.DataFrame(rows))
    hs = out.loc[("hs", 0.0)]
    dr = out.loc[("dir", 0.0)]
    assert hs["n"] == 20 and dr["n"] == 20
    assert dr["cmae"] == pytest.approx(2.0, abs=1e-6)      # 358이 아니다
    assert dr["bias"] == pytest.approx(-2.0, abs=1e-6)
    assert dr["R"] == pytest.approx(1.0, abs=1e-6)         # 완전히 일관된 오차
    assert np.isnan(hs["cmae"])                            # 파고에는 순환 지표 없음


def test_moment_interpolation_matches_all_sea_bilinear() -> None:
    """전부 해양이면 모멘트 경로가 순수 겹선형과 일치한다 (회귀 기준선)."""
    rng = np.random.default_rng(1)
    w = np.array([0.1, 0.2, 0.3, 0.4])
    vals = rng.random((6, 4))
    got, land, raw = sea_normalized(vals, w, np.ones(4))
    assert np.allclose(got, raw, rtol=0, atol=1e-15)
    assert land == 0.0


def test_moment_path_excludes_land_from_direction() -> None:
    """육지 코너가 방향 모멘트를 끌어당기지 않는다.

    육지에서 a1=b1=0이므로, 그것을 섞으면 벡터 크기가 줄어 방향 확산이
    과대해지고 (에너지 가중이 왜곡되어) 파향도 흔들린다. 해상 정규화는
    육지를 분모에서도 빼기 때문에 남은 해양 코너의 방향을 정확히 보존한다.
    """
    g = spec.SpectralGrid()
    # 네 코너 중 둘만 바다. 두 해양 코너는 같은 방향(북쪽 진행)을 갖는다.
    m0 = np.array([[0.04, 0.04, 0.0, 0.0]])
    ang = np.pi / 2
    a1 = np.array([[0.04 * np.cos(ang), 0.04 * np.cos(ang), 0.0, 0.0]])
    b1 = np.array([[0.04 * np.sin(ang), 0.04 * np.sin(ang), 0.0, 0.0]])
    m1 = np.array([[0.004, 0.004, 0.0, 0.0]])
    m2 = np.array([[0.0005, 0.0005, 0.0, 0.0]])
    w = np.array([0.25, 0.25, 0.25, 0.25])
    sea = np.array([1.0, 1.0, 0.0, 0.0])

    pt = {}
    for name, arr in zip(_MOMENTS, (m0, m1, m2, a1, b1)):
        pt[name] = sea_normalized(arr, w, sea)[0]
    d = spec.derive(**pt)
    # 해양 코너의 방향이 그대로 보존된다: 북쪽 진행 -> 남쪽에서 오는 파 = 180°
    assert float(d["dirm"][0]) == pytest.approx(180.0, abs=1e-6)
    # hs 도 해양 코너 값과 같다 (육지 0 이 평균을 끌어내리지 않는다)
    assert float(d["hs"][0]) == pytest.approx(4.0 * np.sqrt(0.04), abs=1e-9)


def test_raw_bilinear_would_shrink_wave_height() -> None:
    """구방식(육지 0 포함)이 얼마나 축소하는지 고정 — 회귀 감시용."""
    m0 = np.array([[0.04, 0.04, 0.0, 0.0]])
    w = np.array([0.25, 0.25, 0.25, 0.25])
    _, land, raw = sea_normalized(m0, w, np.array([1.0, 1.0, 0.0, 0.0]))
    assert land == pytest.approx(0.5)
    assert float(raw[0]) == pytest.approx(0.02)            # m0 가 절반으로
    assert 4.0 * np.sqrt(float(raw[0])) < 4.0 * np.sqrt(0.04)


def test_all_land_gives_nan_not_zero() -> None:
    """전부 육지면 NaN. 0을 내면 하류가 '파고 0'으로 받아들인다."""
    vals = np.array([[0.0, 0.0, 0.0, 0.0]])
    got, land, _ = sea_normalized(vals, np.full(4, 0.25), np.zeros(4))
    assert np.isnan(got).all()
    assert land == pytest.approx(1.0)


def test_direction_sensor_check_is_data_driven() -> None:
    """방향 센서 결함 검출이 관측소를 하드코딩하지 않는다.

    2026-08-25에 "고파고에서 파향이 나빠진다"고 보고한 신호의 대부분이
    관측 센서 결함이었다. 그 검출은 데이터가 해야 하고, 목록을 코드에
    박아두면 다음 달 다른 관측소가 고장났을 때 그대로 통과한다.
    """
    from poseidon.validation import collocate as col

    src = __import__("inspect").getsource(col.direction_sensor_check)
    assert "KMA:22" not in src, "관측소 ID가 하드코딩돼 있다"
    assert "22105" not in src and "22192" not in src

    chk = col.direction_sensor_check()
    if chk.empty:
        pytest.skip("관측 자료 없음")
    assert {"station_id", "n", "frac_over_90", "suspect"} <= set(chk.columns)
    # 젊은 풍성파에서 파향이 풍향과 90도 넘게 어긋나는 비율이 판정 기준이다
    assert chk["frac_over_90"].between(0.0, 1.0).all()
    flagged = chk.loc[chk["suspect"], "frac_over_90"]
    if len(flagged):
        assert (flagged > col.SENSOR_BAD_FRACTION).all()
