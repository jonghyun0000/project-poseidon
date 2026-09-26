"""검증 콜로케이션의 해양 마스크 정규화 보간 (결함 1 회귀 방지).

regional.py가 육지 셀 스펙트럼을 0으로 흡수하므로(`e[~self.sea] = 0`),
순수 겹선형은 육지 Hs=0을 연안 부이 예측에 섞어 계통 과소평가를 만든다.
정규화 보간 Hs = Σ(W·S·Hs)/Σ(W·S)이 이를 배제하는지 확인한다.
"""

import json

import numpy as np
import pandas as pd

from poseidon.validation.collocate import (
    MIN_SEA_WEIGHT,
    corner_weights,
    filter_by_land,
    land_frac_of,
    sea_normalized,
)

LATS = np.arange(30.0, 33.01, 1.0)      # 30 31 32 33
LONS = np.arange(120.0, 123.01, 1.0)    # 120 121 122 123


def test_corner_weights_sum_to_one_and_reproduce_bilinear():
    jj, ii, w = corner_weights(LATS, LONS, 31.25, 121.75)
    assert list(jj) == [1, 1, 2, 2]
    assert list(ii) == [1, 2, 1, 2]
    assert w.sum() == 1.0
    np.testing.assert_allclose(w, [0.75 * 0.25, 0.75 * 0.75, 0.25 * 0.25, 0.25 * 0.75])


def test_sea_normalized_excludes_land_zeros():
    """4코너 중 2개가 육지(hs=0)면 해상 코너만으로 재정규화된다."""
    hs = np.array([2.0, 0.0, 2.4, 0.0])          # 인덱스 1,3 = 육지
    sea = np.array([1, 0, 1, 0])
    w = np.array([0.25, 0.25, 0.25, 0.25])
    val, land_frac, raw = sea_normalized(hs, w, sea)
    assert land_frac == 0.5
    assert raw == 1.1                             # 구방식: 육지 0이 절반 희석
    assert val == (0.25 * 2.0 + 0.25 * 2.4) / 0.5  # = 2.2, 해상 평균
    assert val > raw


def test_sea_normalized_all_land_is_missing():
    hs = np.zeros(4)
    val, land_frac, raw = sea_normalized(hs, np.full(4, 0.25), np.zeros(4))
    assert not np.isfinite(val)
    assert land_frac == 1.0
    assert raw == 0.0


def test_sea_normalized_below_min_weight_is_missing():
    """해상 가중이 하한(0.05) 미만이면 결측 — 육지에 사실상 파묻힌 스텐실."""
    w = np.array([1.0 - 0.5 * MIN_SEA_WEIGHT, 0.5 * MIN_SEA_WEIGHT, 0.0, 0.0])
    val, land_frac, _ = sea_normalized(np.array([0.0, 3.0, 0.0, 0.0]), w,
                                       np.array([0, 1, 0, 0]))
    assert not np.isfinite(val)
    assert land_frac == 1.0 - 0.5 * MIN_SEA_WEIGHT


def test_all_sea_matches_plain_bilinear_exactly():
    """전부 해양이면 기존 겹선형과 동일 (허용오차 1e-12)."""
    rng = np.random.default_rng(3)
    field = rng.random((len(LATS), len(LONS))) * 4.0
    for la, lo in [(30.0, 120.0), (31.4, 122.9), (32.75, 121.1), (30.5, 120.5)]:
        jj, ii, w = corner_weights(LATS, LONS, la, lo)
        val, land_frac, raw = sea_normalized(field[jj, ii], w, np.ones(4))
        # 독립 구현한 참조 겹선형
        j = min(int(la - LATS[0]), len(LATS) - 2)
        i = min(int(lo - LONS[0]), len(LONS) - 2)
        ty, tx = la - LATS[j], lo - LONS[i]
        ref = ((1 - ty) * (1 - tx) * field[j, i] + (1 - ty) * tx * field[j, i + 1]
               + ty * (1 - tx) * field[j + 1, i] + ty * tx * field[j + 1, i + 1])
        assert abs(val - ref) < 1e-12
        assert abs(raw - ref) < 1e-12
        assert land_frac == 0.0


def test_sea_normalized_supports_lead_axis():
    """(lead, 4) 코너 배열이면 리드별 벡터가 그대로 반환된다."""
    corners = np.array([[1.0, 0.0, 1.0, 0.0], [2.0, 0.0, 3.0, 0.0]])
    val, land_frac, raw = sea_normalized(corners, np.full(4, 0.25),
                                         np.array([1, 0, 1, 0]))
    np.testing.assert_allclose(val, [1.0, 2.5])
    np.testing.assert_allclose(raw, [0.5, 1.25])
    assert land_frac == 0.5


def test_land_frac_filter_handles_legacy_samples():
    """구버전 표본(land_frac 없음)은 NaN(미상) — 기본적으로 제외, legacy='keep'로만 유지."""
    df = pd.DataFrame([
        {"features": json.dumps({"u10": 5.0, "depth": 40.0})},              # 구버전
        {"features": json.dumps({"u10": 5.0, "land_frac": 0.0})},
        {"features": json.dumps({"u10": 5.0, "land_frac": 0.62})},
    ])
    lf = land_frac_of(df).to_numpy()
    assert np.isnan(lf[0]), "구버전은 0.0으로 위장하지 않는다"
    np.testing.assert_allclose(lf[1:], [0.0, 0.62])
    assert len(filter_by_land(df, 0.05)) == 1                  # 기본 legacy='drop'
    assert len(filter_by_land(df, 0.05, legacy="keep")) == 2   # 미상 유지


def test_collocation_version_tag():
    from poseidon.validation.collocate import collocation_version
    df = pd.DataFrame([
        {"features": json.dumps({"collocation": "sea-norm-v1", "land_frac": 0.0})},
        {"features": json.dumps({"u10": 5.0})},
    ])
    assert list(collocation_version(df)) == ["sea-norm-v1", "legacy"]
