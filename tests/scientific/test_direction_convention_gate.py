"""파향 규약 회귀 감시 (마일스톤 게이트).

**이 게이트는 파향 예보의 성능을 주장하지 않는다.** 표본이 1사이클 규모라
성능 기준을 세울 근거가 없다. 감시하는 것은 규약이다 — 저장부·콜로케이션·
관측 어댑터 중 하나라도 방향 규약이 뒤집히면 순환 편차가 180° 부근으로
몰리는데, 그 사고는 성적표에서 "물리가 나쁘다"로 보이지 규약 오류로 보이지
않는다. 육지 마스크 결함이 물리 결함으로 오인된 사고(CLAUDE.md §6-1)와 같은
구조라서, 사람이 알아채기 전에 테스트가 잡아야 한다.

표본이 없으면 skip한다 — 없는 것을 통과로 위장하지 않는다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from poseidon.core.catalog import Catalog
from poseidon.core.config import settings
from poseidon.validation.collocate import circular_error, filter_by_land

pytestmark = pytest.mark.scientific

MIN_SAMPLES = 20
BIAS_LIMIT_DEG = 20.0        # 규약이 뒤집히면 180° 부근이 된다
REVERSED_FRACTION_MAX = 0.10  # |오차| > 150°인 표본 비율


def _clean_dir_samples() -> pd.DataFrame:
    try:
        rows = Catalog(settings.catalog_path).error_samples("dir")
    except Exception:  # noqa: BLE001 — 카탈로그 없는 환경
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return filter_by_land(df, 0.05)


def test_direction_bias_is_not_reversed() -> None:
    """순환 편차가 0° 부근이다. 규약이 뒤집히면 180° 부근으로 간다."""
    df = _clean_dir_samples()
    if len(df) < MIN_SAMPLES:
        pytest.skip(f"파향 표본 {len(df)}건 < {MIN_SAMPLES} — 판정 불가")
    err = np.array([circular_error(p, o)
                    for p, o in zip(df["predicted"], df["observed"])])
    rad = np.radians(err)
    bias = float(np.degrees(np.arctan2(np.sin(rad).mean(), np.cos(rad).mean())))
    assert abs(bias) < BIAS_LIMIT_DEG, (
        f"파향 순환 편차 {bias:+.1f}° — 규약 반전 의심. "
        "engines.spectral_wave.grid.to_compass_from 과 관측 어댑터를 확인할 것"
    )


def test_direction_errors_are_not_clustered_near_180() -> None:
    """정반대 오차가 소수여야 한다. 몰려 있으면 규약 또는 짝짓기 오류다."""
    df = _clean_dir_samples()
    if len(df) < MIN_SAMPLES:
        pytest.skip(f"파향 표본 {len(df)}건 < {MIN_SAMPLES} — 판정 불가")
    err = np.array([circular_error(p, o)
                    for p, o in zip(df["predicted"], df["observed"])])
    frac = float(np.mean(np.abs(err) > 150.0))
    assert frac < REVERSED_FRACTION_MAX, (
        f"|오차|>150° 표본이 {frac:.1%} — 규약 반전 또는 관측 정의 불일치 의심"
    )


def test_stored_direction_range_is_compass() -> None:
    """저장된 파향이 [0, 360) 나침반각이다. 라디안이나 음수각이 새면 여기서 걸린다."""
    df = _clean_dir_samples()
    if df.empty:
        pytest.skip("파향 표본 없음")
    pred = df["predicted"].to_numpy(float)
    assert np.all(pred >= 0.0) and np.all(pred < 360.0), (
        f"예측 파향 범위 [{pred.min():.2f}, {pred.max():.2f}] — 나침반각이 아니다"
    )


def test_periods_are_physically_bounded() -> None:
    """주기가 스펙트럼 격자 범위 안이다. 격자 밖 값은 수치 오류 신호다."""
    try:
        rows = Catalog(settings.catalog_path).error_samples("tp")
    except Exception:  # noqa: BLE001
        pytest.skip("카탈로그 접근 불가")
    df = pd.DataFrame(rows)
    if df.empty:
        pytest.skip("주기 표본 없음")
    df = filter_by_land(df, 0.05)
    if df.empty:
        pytest.skip("청정 주기 표본 없음")
    tp = df["predicted"].to_numpy(float)
    # 격자: f0=0.05 Hz (20 s) ~ 0.05*1.1^31 (약 1.04 s)
    assert np.all(tp > 0.9) and np.all(tp < 21.0), (
        f"예측 주기 범위 [{tp.min():.2f}, {tp.max():.2f}] s — 스펙트럼 격자 밖"
    )
