import numpy as np
import pytest

from poseidon.engines.spectral_wave.grid import SpectralGrid
from poseidon.engines.spectral_wave.sources import (
    DIA,
    dissipation,
    wind_input,
)
from poseidon.physics.constants import G_STANDARD as G
from poseidon.physics.waves import (
    deep_group_velocity,
    group_velocity,
    jonswap_spectrum,
    wavenumber,
)


# ── 분산관계 ───────────────────────────────────────────────────────
def test_dispersion_residual_and_limits():
    sig = 2 * np.pi * np.array([0.05, 0.1, 0.3, 1.0])
    for h in (5.0, 50.0, 4000.0):
        k = wavenumber(sig, h)
        res = G * k * np.tanh(k * h) - sig ** 2
        np.testing.assert_allclose(res, 0.0, atol=1e-9 * sig.max() ** 2)
    # 심수 극한: k → σ²/g
    k_deep = wavenumber(sig, 1e5)
    np.testing.assert_allclose(k_deep, sig ** 2 / G, rtol=1e-8)
    # 천수 극한: k → σ/√(gh)
    k_sh = wavenumber(np.array([0.05]), 1.0)
    np.testing.assert_allclose(k_sh, 0.05 / np.sqrt(G * 1.0), rtol=1e-3)


def test_group_velocity_limits():
    sig = np.array([2 * np.pi * 0.1])
    np.testing.assert_allclose(group_velocity(sig, 1e5),
                               deep_group_velocity(np.array([0.1])), rtol=1e-6)
    np.testing.assert_allclose(group_velocity(np.array([0.05]), 2.0),
                               np.sqrt(G * 2.0), rtol=1e-3)  # 천수: cg → √(gh)


# ── 스펙트럼 격자 ──────────────────────────────────────────────────
def _jonswap_2d(grid: SpectralGrid, fp: float, alpha: float = 0.01):
    e1 = jonswap_spectrum(grid.f, fp, alpha=alpha)
    spread = np.maximum(np.cos(grid.theta), 0.0) ** 2
    spread /= spread.sum() * grid.dtheta
    return e1[:, None] * spread[None, :]


def test_grid_integration_and_peak():
    grid = SpectralGrid()
    fp = 0.12
    e = _jonswap_2d(grid, fp)
    # m0 적분: 세밀 격자 수치적분과 비교
    ff = np.linspace(0.03, 1.2, 4000)
    m0_ref = np.trapezoid(jonswap_spectrum(ff, fp, alpha=0.01), ff)
    np.testing.assert_allclose(grid.total_energy(e), m0_ref, rtol=0.03)
    np.testing.assert_allclose(grid.peak_frequency(e), fp, rtol=0.03)


# ── 소스항 ─────────────────────────────────────────────────────────
def test_wind_input_sign_structure():
    grid = SpectralGrid()
    e = _jonswap_2d(grid, 0.25) + 1e-6
    s = wind_input(e, grid, u10=12.0, theta_wind=0.0)
    assert s.min() >= 0.0                                 # WAM3: 음의 입력 없음
    down = np.argmin(np.abs(grid.theta - 0.0))
    upw = np.argmin(np.abs(grid.theta - np.pi))
    hi = grid.nf - 5                                      # 젊은 고주파 성분
    assert s[hi, down] > 0.0                              # 순풍 성분 성장
    assert s[hi, upw] == 0.0                              # 역풍 성분 입력 없음


def test_dissipation_negative_and_steepness_scaling():
    grid = SpectralGrid()
    e = _jonswap_2d(grid, 0.12)
    s1 = dissipation(e, grid)
    s2 = dissipation(2.0 * e, grid)
    assert s1.max() <= 0.0
    # 에너지 2배 → s̃⁴ ∝ E² 이므로 소산율은 초선형 증가 (|S₂| > 2|S₁|)
    assert -s2.sum() > 2.0 * -s1.sum()


def test_dia_approximate_energy_conservation():
    grid = SpectralGrid()
    dia = DIA(grid)
    e = _jonswap_2d(grid, 0.15, alpha=0.014)
    snl = dia(e)
    w = grid.df[:, None] * grid.dtheta
    net = float((snl * w).sum())
    gross = float((np.abs(snl) * w).sum())
    assert gross > 0.0
    assert abs(net) < 0.05 * gross     # 보존성 (격자 밖 소실 허용 오차 내)


def test_dia_downshifts_peak():
    # JONSWAP에 DIA만 잠깐 작용시키면 첨두는 저주파로 이동해야 한다 (H85 핵심 성질)
    grid = SpectralGrid()
    dia = DIA(grid)
    e = _jonswap_2d(grid, 0.2, alpha=0.014)
    fp0 = float(grid.peak_frequency(e))
    for _ in range(60):
        e = np.maximum(e + 30.0 * dia(e), 0.0)
    assert float(grid.peak_frequency(e)) < fp0
