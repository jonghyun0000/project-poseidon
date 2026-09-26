"""Phase 9 게이트: NumPy 참조 ↔ JAX 커널 동등성 (PHASE2 §10).

동일 초기조건·강제로 5스텝 전진 후 Hs 필드 비교.
허용오차: 상대 L∞ < 2×10⁻³ — 참조 구현은 소스항을 fp64로, JAX는 전 구간 fp32로
계산하므로 DIA(E³·f¹¹)의 정밀도 차이가 누적된다 (문서화된 게이트).
"""

import numpy as np
import pytest

from poseidon.engines.spectral_wave.grid import SpectralGrid
from poseidon.engines.spectral_wave.regional import RegionalWaveModel

pytestmark = pytest.mark.scientific

jax = pytest.importorskip("jax")


def _setup():
    from poseidon.engines.spectral_wave.jax_kernel import JaxRegionalKernel, ring_mask

    grid = SpectralGrid()
    ny, nx = 24, 30
    lats = 20.0 + 0.25 * np.arange(ny)
    lons = 130.0 + 0.25 * np.arange(nx)
    x, y = np.meshgrid(np.arange(nx), np.arange(ny))
    depth = 60.0 + 3500.0 * (0.5 + 0.5 * np.sin(x / 6) * np.cos(y / 5))
    depth[:3, :5] = 0.5                                   # 육지 조각 포함

    m = RegionalWaveModel(lats, lons, depth, grid, h_min=10.0)
    hs = 1.5 + 1.0 * np.exp(-((x - 15) ** 2 + (y - 12) ** 2) / 36.0)
    tp = np.full_like(hs, 9.0)
    md = np.full_like(hs, 0.5)
    e0 = m.spectra_from_integrals(hs, tp, md)
    m.set_boundary(e0, sides="WESN")

    u10 = np.full((ny, nx), 12.0)
    udir = np.full((ny, nx), 0.7)
    k = JaxRegionalKernel(m)
    return m, k, e0, u10, udir, ring_mask(ny, nx)


def test_numpy_jax_equivalence_gate():
    m, k, e0, u10, udir, ring = _setup()
    dt = m.cfl_dt()
    k.prepare(m, dt)

    e_np = e0.copy()
    for _ in range(5):
        e_np = m.step(e_np, dt, u10, udir)

    e_jx = e0.copy()
    for _ in range(5):
        e_jx = np.asarray(k.step(e_jx, dt, u10, udir, e0, ring))

    hs_np = m.grid.hs(e_np.astype(np.float64))
    hs_jx = m.grid.hs(e_jx.astype(np.float64))
    sea = m.sea & (hs_np > 0.05)
    rel = np.abs(hs_jx[sea] - hs_np[sea]) / np.maximum(hs_np[sea], 1e-6)
    assert float(rel.max()) < 2e-3, f"max rel diff {rel.max():.2e}"
    assert np.isfinite(hs_jx).all()