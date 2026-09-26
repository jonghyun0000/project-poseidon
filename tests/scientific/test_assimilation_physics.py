"""동화 스펙트럼 재척도의 물리 일관성 (마일스톤 게이트 — `scientific` 마커).

검사 대상
- 질량(Hs) 정합: |hs(E_a) - Hs_target| < 1e-4 m
- 양수성·유한성: E_a >= 0, NaN/inf 0건 (CLAUDE.md 함정 5)
- 적분 관계: m0_a/m0_b = A/B = q^2               (ECMWF IFS Cy45r1 식 4.3)
- 평균 주파수: f_a = f_b / B
- 너울 파형경사 보존: k_bar_a H_a = k_bar_b H_b  (Lionello & Janssen 1990)
- 풍파 분류식이 엔진 wind_input 의 성장 조건과 동일한 부호를 준다
- OI 가 교과서 단일관측 해와 일치
"""

from __future__ import annotations

import numpy as np
import pytest

from poseidon.assimilation import oi, spectral
from poseidon.assimilation.params import AssimConfig
from poseidon.engines.spectral_wave.grid import SpectralGrid
from poseidon.engines.spectral_wave.regional import RegionalWaveModel
from poseidon.engines.spectral_wave.sources import wind_input
from poseidon.physics.constants import G_STANDARD
from poseidon.physics.waves import jonswap_spectrum

pytestmark = pytest.mark.scientific

GRID = SpectralGrid()
Q_CASES = (0.5, 0.7, 0.9, 1.0, 1.1, 1.35, 1.8, 2.0)


def jonswap_field(ny, nx, fp=0.12, mdir=0.0, hs=1.0):
    e1 = jonswap_spectrum(GRID.f[None, None, :], np.full((ny, nx, 1), fp))
    spread = np.maximum(np.cos(GRID.theta - mdir), 0.0) ** 2
    spread /= spread.sum() * GRID.dtheta
    e = e1[..., None] * spread[None, None, None, :]
    m0 = GRID.total_energy(e)
    return e * ((hs / 4.0) ** 2 / np.maximum(m0, 1e-30))[..., None, None]


@pytest.mark.parametrize("q", Q_CASES)
@pytest.mark.parametrize("mode", ["ecmwf", "energy"])
@pytest.mark.parametrize("windsea", [True, False])
def test_hs_target_exact(q, mode, windsea):
    e = jonswap_field(4, 5, hs=1.4)
    hs_b = GRID.hs(e)
    qf = np.full((4, 5), q)
    ws = np.full((4, 5), windsea)
    ea, diag = spectral.apply_increment(e, GRID, qf, is_windsea=ws, mode=mode,
                                        dtype=np.float64)
    hs_a = GRID.hs(ea)
    assert diag["hs_err_max"] < 1e-4
    assert np.allclose(hs_a, q * hs_b, atol=1e-9)
    assert ea.min() >= 0.0
    assert np.isfinite(ea).all()


@pytest.mark.parametrize("q", Q_CASES)
def test_integral_relation_A_over_B(q):
    """정확 재정규화 전의 원 변환이 m0_a/m0_b = A/B = q^2 를 만족하는지."""
    e = jonswap_field(2, 2, hs=1.0)
    a, b = spectral.rescale_factors(np.full((2, 2), q), np.zeros((2, 2), bool), "ecmwf")
    ea = spectral.shift_spectrum(e, GRID, a, b, dtype=np.float64)
    ratio = GRID.total_energy(ea) / GRID.total_energy(e)
    # 이산 보간·꼬리 절단 때문에 정확히 q^2 는 아니지만 수 % 이내여야 한다
    assert np.allclose(ratio, q ** 2, rtol=0.06), (ratio.mean(), q ** 2)


@pytest.mark.parametrize("q", [0.6, 0.8, 1.25, 1.6, 2.0])
def test_swell_mean_frequency_shifts_as_one_over_B(q):
    """너울 분기: f_a = f_b / B,  B = sqrt(q)."""
    e = jonswap_field(1, 1, fp=0.09, hs=1.0)
    a, b = spectral.rescale_factors(np.full((1, 1), q), np.zeros((1, 1), bool), "ecmwf")
    ea = spectral.shift_spectrum(e, GRID, a, b, dtype=np.float64)
    f_b = _mean_freq(e)
    f_a = _mean_freq(ea)
    assert f_a / f_b == pytest.approx(1.0 / np.sqrt(q), rel=0.05)


@pytest.mark.parametrize("q", [0.7, 1.3, 1.9])
def test_swell_steepness_is_preserved(q):
    """심수에서 k_bar H 보존 (Lionello & Janssen 1990). k ∝ f^2 이므로 k_bar ∝ 1/B^2."""
    e = jonswap_field(1, 1, fp=0.08, hs=1.0)
    a, b = spectral.rescale_factors(np.full((1, 1), q), np.zeros((1, 1), bool), "ecmwf")
    ea = spectral.shift_spectrum(e, GRID, a, b, dtype=np.float64)
    ea = ea * ((q * GRID.hs(e)) / np.maximum(GRID.hs(ea), 1e-12))[..., None, None] ** 2
    _, k_b = GRID.mean_sigma_k(e, G_STANDARD)
    _, k_a = GRID.mean_sigma_k(ea, G_STANDARD)
    s_b = k_b * GRID.hs(e)
    s_a = k_a * GRID.hs(ea)
    assert float(np.ravel(s_a / s_b)[0]) == pytest.approx(1.0, rel=0.08)


@pytest.mark.parametrize("q", [0.8, 1.5])
def test_windsea_branch_preserves_shape(q):
    """풍파 분기(A=q^2, B=1)는 정규화 형상을 정확히 보존한다."""
    e = jonswap_field(2, 2, hs=1.0)
    ea, _ = spectral.apply_increment(e, GRID, np.full((2, 2), q),
                                     is_windsea=np.ones((2, 2), bool),
                                     mode="ecmwf", dtype=np.float64)
    n_b = e / GRID.total_energy(e)[..., None, None]
    n_a = ea / GRID.total_energy(ea)[..., None, None]
    assert np.allclose(n_a, n_b, atol=1e-12)


def test_windsea_classifier_matches_engine_growth_condition():
    """풍파 판정식이 엔진 wind_input 의 beta>0 조건과 같은 성분 집합을 고른다."""
    u10, udir = 12.0, 0.3
    e = np.ones((GRID.nf, GRID.ntheta)) * 1e-4
    s = wind_input(e, GRID, u10, udir)
    grows = s > 0.0
    ea = np.zeros_like(e)
    ea[grows] = 1e-4
    frac_all = spectral.windsea_fraction(e[None, None], GRID,
                                         np.array([[u10]]), np.array([[udir]]))
    frac_grow = spectral.windsea_fraction(ea[None, None], GRID,
                                          np.array([[u10]]), np.array([[udir]]))
    assert 0.0 < float(np.ravel(frac_all)[0]) < 1.0
    assert float(np.ravel(frac_grow)[0]) == pytest.approx(1.0, abs=1e-9)


def test_zero_energy_cells_stay_zero_and_finite():
    """에너지 0 셀에서 진단 평균량이 발산해 NaN 이 번지지 않는지 (함정 5)."""
    e = jonswap_field(3, 3, hs=1.0)
    e[1, 1] = 0.0
    q = np.full((3, 3), 1.8)
    ea, _ = spectral.apply_increment(e, GRID, q, mode="ecmwf", dtype=np.float64)
    assert np.isfinite(ea).all()
    assert np.all(ea[1, 1] == 0.0)


def test_shallow_stop_mask_disables_frequency_shift():
    e = jonswap_field(1, 2, fp=0.06, hs=1.0)
    depth = np.array([[5.0, 4000.0]])
    ss = spectral.shallow_stop_mask(e, GRID, depth, G_STANDARD)
    assert ss[0, 0] and not ss[0, 1]
    a, b = spectral.rescale_factors(np.full((1, 2), 1.6), np.zeros((1, 2), bool),
                                    "ecmwf", shallow_stop=ss)
    assert b[0, 0] == 1.0 and b[0, 1] == pytest.approx(np.sqrt(1.6))


def test_oi_single_obs_matches_textbook_gain():
    lats = 34.0 + 0.25 * np.arange(21)
    lons = 128.0 + 0.25 * np.arange(21)
    m = RegionalWaveModel(lats, lons, np.full((21, 21), 2000.0), GRID, h_min=10.0)
    e = m.spectra_from_integrals(np.full((21, 21), 1.0), np.full((21, 21), 9.0),
                                 np.zeros((21, 21)))
    hs_b = GRID.hs(e.astype(np.float64))
    cfg = AssimConfig(bias_correction=False, line_of_sight=False, smooth_passes=0)
    from poseidon.assimilation.observations import Observation
    o = Observation("T", float(lats[10]), float(lons[10]), float(hs_b[10, 10] + 0.25),
                    1, "test")
    slots, rej = oi.build_obs_slots(m, hs_b, [o], cfg)
    assert rej == [] and len(slots) == 1
    s = slots[0]
    _, d_hs, diag = oi.analyse_hs(m, hs_b, slots, cfg)
    gain = s.sigma_b ** 2 / (s.sigma_b ** 2 + s.sigma_o ** 2)
    assert d_hs[10, 10] == pytest.approx(gain * 0.25, rel=1e-8)
    # chi^2 = d^2/(sigma_b^2+sigma_o^2) — 단일 관측 자기일관성
    assert diag["chi2"] == pytest.approx(0.25 ** 2 / (s.sigma_b ** 2 + s.sigma_o ** 2),
                                         rel=1e-8)


def _mean_freq(e):
    e1 = GRID.spectrum_1d(e)
    w = GRID.df
    return float((e1 * w * GRID.f).sum() / (e1 * w).sum())
