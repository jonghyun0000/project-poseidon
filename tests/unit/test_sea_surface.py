"""해면 실현 — 스펙트럼에서 만든 파면이 그 스펙트럼의 통계를 갖는가.

1인칭 바다 화면이 보여주는 파면은 **예측이 아니라 통계적 실현**이다.
보장되는 것은 유의파고·주기·방향 분포뿐이므로, 그 통계가 실제로
보장되는지를 여기서 고정한다. 이것이 깨지면 화면의 바다가 예보와
다른 바다가 된다.
"""

from __future__ import annotations

import numpy as np
import pytest

from poseidon.physics.sea_surface import (
    directional_spectrum,
    elevation,
    encounter_angle,
    realize_components,
    spread_exponent,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("dspr", [10.0, 20.0, 31.5, 45.0, 60.0])
def test_spread_exponent_roundtrip(dspr: float) -> None:
    """cos^2s 지수 역산이 왕복한다. 어긋나면 화면의 파가 실제보다 모이거나 퍼진다."""
    s = spread_exponent(dspr)
    r = s / (s + 1.0)                       # cos^2s 의 1차 모멘트 크기
    assert np.degrees(np.sqrt(2.0 * (1.0 - r))) == pytest.approx(dspr, rel=1e-6)


@pytest.mark.parametrize(("hs", "tp"), [(0.5, 5.0), (1.0, 7.0), (2.5, 9.0), (5.0, 12.0)])
def test_realized_variance_matches_target(hs: float, tp: float) -> None:
    """Σ a²/2 = m0. 성분 샘플링이 에너지를 잃지 않는다."""
    c = realize_components(hs, tp, 270.0, 28.0, seed=1)
    assert c["m0_realized"] == pytest.approx(c["m0_target"], rel=1e-6)
    assert 4.0 * np.sqrt(c["m0_realized"]) == pytest.approx(hs, rel=1e-6)


def test_elevation_statistics_are_gaussian_with_right_height() -> None:
    """실제 파면의 표준편차가 √m0 이고 가우시안이다."""
    c = realize_components(2.5, 9.0, 270.0, 28.0, seed=7)
    g = np.linspace(0.0, 4000.0, 300)
    X, Y = np.meshgrid(g, g, indexing="ij")
    eta = elevation(c, X, Y, 0.0)
    assert eta.std() == pytest.approx(np.sqrt(c["m0_realized"]), rel=0.05)
    assert abs(eta.mean()) < 0.05
    skew = ((eta - eta.mean()) ** 3).mean() / eta.std() ** 3
    assert abs(skew) < 0.15                 # 선형 중첩은 대칭 해면이다


def test_spectrum_integrates_to_target() -> None:
    """스펙트럼의 m0가 hs에 맞는다 (임의 방향 격자에서)."""
    f = np.geomspace(0.04, 0.5, 40)
    th = np.linspace(0.0, 2.0 * np.pi, 36, endpoint=False)
    dth, df = float(th[1] - th[0]), np.gradient(f)
    for mdir in (0.0, 90.0, 200.0, 315.0):
        e = directional_spectrum(f, th, 2.0, 8.0, mdir, 25.0)
        m0 = float((e * df[:, None]).sum() * dth)
        assert 4.0 * np.sqrt(m0) == pytest.approx(2.0, rel=1e-6)


@pytest.mark.parametrize("mdir", [0.0, 45.0, 90.0, 200.0, 315.0, 359.0])
def test_realized_direction_recovers_input(mdir: float) -> None:
    """실현 성분의 평균파향이 입력한 '오는 방향'을 복원한다.

    `realize_components`가 방향 격자를 평균파향에 정렬하므로, 거친
    격자(n_dir=12)에서도 이산화 편향이 생기지 않아야 한다. 이것이 틀리면
    화면의 파가 예보와 다른 쪽에서 온다.
    """
    c = realize_components(2.0, 8.0, mdir, 25.0, seed=0, n_dir=12)
    var = c["amp"] ** 2 / 2.0
    a1 = float((var * np.cos(c["theta"])).sum())
    b1 = float((var * np.sin(c["theta"])).sum())
    back = (270.0 - np.degrees(np.arctan2(b1, a1))) % 360.0
    assert abs((back - mdir + 180.0) % 360.0 - 180.0) < 0.5


def test_waves_actually_propagate() -> None:
    """시간이 흐르면 파면이 바뀐다. 정지 화면이면 분산관계가 깨진 것이다."""
    c = realize_components(2.0, 8.0, 270.0, 28.0, seed=3)
    g = np.linspace(0.0, 1000.0, 60)
    X, Y = np.meshgrid(g, g, indexing="ij")
    a = elevation(c, X, Y, 0.0)
    b = elevation(c, X, Y, 6.0)
    assert not np.allclose(a, b, atol=0.05)
    assert b.std() == pytest.approx(a.std(), rel=0.15)     # 통계는 정상 상태


@pytest.mark.parametrize(
    ("mdir", "heading", "want"),
    [(270.0, 270.0, 0.0), (270.0, 90.0, 180.0), (270.0, 0.0, -90.0),
     (0.0, 180.0, 180.0), (45.0, 30.0, 15.0)],
)
def test_encounter_angle(mdir: float, heading: float, want: float) -> None:
    got = encounter_angle(mdir, heading)
    assert abs((got - want + 180.0) % 360.0 - 180.0) < 1e-9


def test_zero_energy_is_flat() -> None:
    """파고 0이면 해면이 평평하다 — NaN이나 발산이 아니다."""
    c = realize_components(0.0, 8.0, 270.0, 28.0, seed=0)
    eta = elevation(c, np.linspace(0, 500, 20), np.zeros(20), 0.0)
    assert np.all(np.isfinite(eta))
    assert np.abs(eta).max() < 1e-6
