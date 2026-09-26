"""스펙트럼 모멘트와 유도량 (파주기·파향 저장부).

핵심 불변식 세 가지를 고정한다.
  1. hs가 비트 단위로 불변이다 — 모멘트 추가가 기존 산출물을 건드리지 않는다.
  2. 모멘트가 E에 선형이다 — 이 성질이 콜로케이션에서 해상 정규화를
     그대로 재사용할 수 있게 하는 근거다.
  3. 에너지가 없는 셀이 NaN이다 — 그럴듯한 가짜 값이 하류로 새지 않는다.
"""

from __future__ import annotations

import numpy as np
import pytest

from poseidon.engines.spectral_wave import grid as G
from poseidon.engines.spectral_wave.regional import jonswap_spectrum

pytestmark = pytest.mark.unit


def _spec(g: G.SpectralGrid, hs: float, tp: float, mdir: float) -> np.ndarray:
    """JONSWAP × cos² 확산으로 알려진 (Hs, Tp, 진행방향) 스펙트럼."""
    e1 = jonswap_spectrum(g.f, 1.0 / tp)
    e1 = e1 * ((hs / 4.0) ** 2 / (e1 * g.df).sum())
    sp = np.maximum(np.cos(g.theta - mdir), 0.0) ** 2
    sp = sp / (sp.sum() * g.dtheta)
    return e1[:, None] * sp[None, :]


def test_hs_bit_identical() -> None:
    """모멘트 경로의 m0가 기존 hs를 최하위 비트까지 재현한다."""
    g = G.SpectralGrid()
    e = np.random.default_rng(0).random((7, 5, g.nf, g.ntheta))
    m = g.moments(e)
    assert np.array_equal(g.hs(e), 4.0 * np.sqrt(np.maximum(m["m0"], 0.0)))


def test_moments_are_linear() -> None:
    """모멘트가 E에 선형이다 — 보간과 유도의 교환을 보장하는 성질."""
    g = G.SpectralGrid()
    rng = np.random.default_rng(1)
    ea = rng.random((g.nf, g.ntheta))
    eb = rng.random((g.nf, g.ntheta))
    wa, wb = 0.3, 0.7
    mix = g.moments(wa * ea + wb * eb)
    ma, mb = g.moments(ea), g.moments(eb)
    for k in mix:
        assert np.allclose(mix[k], wa * ma[k] + wb * mb[k], rtol=1e-13)


def test_moments_vanish_on_zero_energy() -> None:
    g = G.SpectralGrid()
    m = g.moments(np.zeros((g.nf, g.ntheta)))
    assert all(v == 0.0 for v in m.values())


@pytest.mark.parametrize("tp", [6.0, 10.0, 14.0])
def test_jonswap_period_ratios(tp: float) -> None:
    """JONSWAP γ=3.3의 문헌 비율 tm01/Tp≈0.834, tm02/Tp≈0.781을 재현한다."""
    g = G.SpectralGrid()
    d = G.derive(**g.moments(_spec(g, 2.0, tp, np.pi / 2)))
    assert float(d["hs"]) == pytest.approx(2.0, rel=1e-6)
    assert float(d["tm01"]) / tp == pytest.approx(0.834, abs=0.006)
    assert float(d["tm02"]) / tp == pytest.approx(0.781, abs=0.008)
    assert float(d["tm02"]) < float(d["tm01"]) < tp   # Tm02 < Tm01 < Tp


@pytest.mark.parametrize(
    ("theta_math", "compass_from"),
    [(0.0, 270.0), (np.pi / 2, 180.0), (np.pi, 90.0), (3 * np.pi / 2, 0.0)],
)
def test_direction_convention(theta_math: float, compass_from: float) -> None:
    """수학각·진행방향 → 나침반각·오는방향. 어긋나면 180° 계통 편차가 된다."""
    assert float(G.to_compass_from(np.array(theta_math))) == pytest.approx(
        compass_from, abs=1e-9
    )


def test_convention_roundtrip_with_forcing_reader() -> None:
    """저장부 변환이 강제장 입력 변환(`_from_compass_from`)의 정확한 역이다."""
    from poseidon.scheduler.wave_cycle import _from_compass_from

    deg_from = np.array([0.0, 45.0, 90.0, 170.0, 250.0, 359.0])
    back = G.to_compass_from(_from_compass_from(deg_from))
    assert np.allclose((back - deg_from + 180.0) % 360.0 - 180.0, 0.0, atol=1e-9)


def test_derived_direction_matches_input() -> None:
    g = G.SpectralGrid()
    for mdir, want in [(np.pi / 2, 180.0), (0.0, 270.0), (np.pi * 1.25, 45.0)]:
        d = G.derive(**g.moments(_spec(g, 2.0, 10.0, mdir)))
        err = (float(d["dirm"]) - want + 180.0) % 360.0 - 180.0
        assert abs(err) < 0.05


def test_zero_energy_derives_to_nan() -> None:
    """육지·무에너지 셀은 NaN. 0이나 그럴듯한 상수를 내면 안 된다."""
    g = G.SpectralGrid()
    z = np.zeros((g.nf, g.ntheta))
    assert all(np.isnan(float(v)) for v in G.derive(**g.moments(z)).values())
    assert all(np.isnan(float(v)) for v in G.derive_peak(g, z).values())


def test_peak_quantities_would_lie_without_masking() -> None:
    """마스킹을 제거하면 어떤 가짜 값이 새는지 고정한다.

    이 테스트는 회귀 방지용이다. `derive_peak`의 에너지 검사를 지우면
    육지가 18.18 s / 0°(동쪽)라는 물리적으로 그럴듯한 값을 갖게 되고,
    하류는 그것을 결손으로 인지하지 못한다.
    """
    g = G.SpectralGrid()
    z = np.zeros((g.nf, g.ntheta))
    assert 1.0 / float(g.peak_frequency(z)) == pytest.approx(18.18, abs=0.05)
    assert float(g.peak_direction(z)) == 0.0


def test_directional_spread_bounds() -> None:
    """확산은 좁은 스펙트럼일수록 작고, r 클램프가 NaN을 막는다."""
    g = G.SpectralGrid()
    d = G.derive(**g.moments(_spec(g, 2.0, 10.0, np.pi / 2)))
    assert 0.0 < float(d["dspr"]) < 90.0
    # 단일 방향에 집중된 스펙트럼: r→1, 확산→0
    e = np.zeros((g.nf, g.ntheta))
    e[10, 9] = 1.0
    d1 = G.derive(**g.moments(e))
    assert float(d1["dspr"]) == pytest.approx(0.0, abs=1e-6)


def test_enable_switch_requires_isolation_tag() -> None:
    """소스항을 바꾼 실행은 out_tag 없이 저장할 수 없다.

    태그가 없으면 `{cycle}_wave.zarr`를 덮어쓰고 source_id가 운영과 같아져
    한 error_sample 테이블에 두 물리가 섞인다(CLAUDE.md 함정 9·10).
    진단 실험이 운영 표본을 오염시키는 경로를 코드가 막는다.
    """
    from poseidon.scheduler.wave_cycle import run_wave_forecast

    with pytest.raises(ValueError, match="out_tag"):
        run_wave_forecast("20260101T00", enable=("wind",))


def test_enable_default_is_frozen_configuration() -> None:
    """기본값은 동결 구성이며, 모델에 인자를 넘기지 않는 경로를 탄다."""
    from poseidon.engines.spectral_wave.regional import RegionalWaveModel

    sig = __import__("inspect").signature(RegionalWaveModel.__init__)
    assert sig.parameters["enable"].default == ("wind", "ds", "nl", "bot", "brk")
