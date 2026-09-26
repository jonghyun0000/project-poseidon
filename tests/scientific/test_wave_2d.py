"""M6 부분 게이트: 2D 구면 파랑 전파·유한수심 물리 검증.

1. 심해 스웰 전파: 군속도 정확성 + 에너지 보존 (구면 메트릭)
2. Snell 굴절: 평면 해빈에서 파향이 스넬 법칙대로 회전
3. Battjes-Janssen 쇄파: 쇄파대에서 Hs ≤ γh 상한
4. JONSWAP 해저마찰: 해석적 지수 감쇠율 재현
"""

import numpy as np
import pytest

from poseidon.engines.spectral_wave.grid import SpectralGrid
from poseidon.engines.spectral_wave.regional import RegionalWaveModel
from poseidon.physics.constants import G_STANDARD as G
from poseidon.physics.waves import deep_group_velocity, wavenumber

pytestmark = pytest.mark.scientific


def _narrow_spectrum(model, f_idx, th_idx, hs=1.0):
    gr = model.grid
    e = np.zeros((model.ny, model.nx, gr.nf, gr.ntheta), dtype=np.float32)
    m0 = (hs / 4.0) ** 2
    e[..., f_idx, th_idx] = m0 / (gr.df[f_idx] * gr.dtheta)
    return e


# ── 1. 심해 스웰 전파 ───────────────────────────────────────────────
def test_deep_swell_propagation_speed_and_energy():
    grid = SpectralGrid()
    lats = 10.0 + 0.5 * np.arange(30)                 # 저위도 (대권 전환 최소화)
    lons = 130.0 + 0.5 * np.arange(60)
    depth = np.full((30, 60), 5000.0)
    m = RegionalWaveModel(lats, lons, depth, grid, enable=())

    fi = int(np.argmin(np.abs(grid.f - 0.1)))         # f≈0.1 Hz, 동진(θ=0)
    e = _narrow_spectrum(m, fi, 0)
    e[...] = 0.0
    # 공간 가우시안 블롭 (서쪽 1/4 지점)
    X, Y = np.meshgrid(np.arange(60), np.arange(30))
    blob = np.exp(-((X - 12) ** 2 + (Y - 15) ** 2) / 3.0 ** 2)
    e[..., fi, 0] = (blob / (grid.df[fi] * grid.dtheta)).astype(np.float32)

    w = np.cos(np.radians(lats))[:, None]
    m0_0 = float((grid.total_energy(e) * w).sum())
    cx0 = float((grid.total_energy(e) * w * X).sum()) / m0_0

    T = 24 * 3600.0
    dt = m.cfl_dt()
    ee = e.copy()
    for _ in range(int(T / dt)):
        ee = m.step(ee, dt, sources=False)

    m0_1 = float((grid.total_energy(ee) * w).sum())
    cx1 = float((grid.total_energy(ee) * w * X).sum()) / m0_1

    cg = float(deep_group_velocity(grid.f[fi]))
    expected_cells = cg * T / (111_320.0 * np.cos(np.radians(17.5)) * 0.5)
    assert abs((cx1 - cx0) / expected_cells - 1.0) < 0.10   # 군속도 ±10%
    assert abs(m0_1 - m0_0) / m0_0 < 0.02                   # 에너지 보존 2%


# ── 2. Snell 굴절 ──────────────────────────────────────────────────
def _beach_model(grid, ny=30, nx=70, enable=()):
    lats = -0.29 + 0.02 * np.arange(ny)               # 적도 부근 (메트릭 균일)
    lons = 130.0 + 0.02 * np.arange(nx)
    x = np.arange(nx)
    depth1d = np.maximum(500.0 - (500.0 - 4.0) * x / (nx - 1), 4.0)
    depth = np.tile(depth1d, (ny, 1))
    return RegionalWaveModel(lats, lons, depth, grid, enable=enable), depth1d


def _mean_dir_deg(grid, e_cell):
    e1 = e_cell.sum(axis=0)
    c = (e1 * np.cos(grid.theta)).sum()
    s = (e1 * np.sin(grid.theta)).sum()
    return np.degrees(np.arctan2(s, c))


def test_snell_refraction_on_planar_beach():
    grid = SpectralGrid()
    m, depth1d = _beach_model(grid, ny=60)            # 넓은 폭: SW 코너 음영 회피
    fi = int(np.argmin(np.abs(grid.f - 0.1)))
    ti = 3                                            # θ=30° (10° 빈)
    e_bc = _narrow_spectrum(m, fi, ti, hs=1.0)
    m.set_boundary(e_bc, sides="W")                   # 서쪽 유입만 고정, 나머지 유출

    e = e_bc.copy()
    dt = m.cfl_dt()
    for _ in range(int(8 * 3600 / dt)):               # 정상상태 도달
        e = m.step(e, dt, sources=False)

    sig = 2 * np.pi * grid.f[fi]
    c_deep = sig / float(wavenumber(sig, depth1d[0]))
    row = 42                                          # 유입 광선이 도달하는 행
    for h_target in (10.0, 6.0):
        ix = int(np.argmin(np.abs(depth1d - h_target)))
        c_sh = sig / float(wavenumber(sig, depth1d[ix]))
        snell = np.degrees(np.arcsin(np.sin(np.radians(30.0)) * c_sh / c_deep))
        measured = _mean_dir_deg(grid, e[row, ix].astype(np.float64))
        assert abs(measured - snell) < 5.0, \
            f"h={h_target}: measured {measured:.1f}°, Snell {snell:.1f}°"


# ── 3. 수심 유도 쇄파 ───────────────────────────────────────────────
def test_depth_breaking_caps_wave_height():
    grid = SpectralGrid()
    m, depth1d = _beach_model(grid, enable=("brk",))
    fi = int(np.argmin(np.abs(grid.f - 0.08)))
    e_bc = _narrow_spectrum(m, fi, 0, hs=2.5)
    m.set_boundary(e_bc)
    e = e_bc.copy()
    dt = m.cfl_dt()
    for _ in range(int(8 * 3600 / dt)):
        e = m.step(e, dt, u10=0.0, udir=0.0)

    hs_x = grid.hs(e[15].astype(np.float64))          # 중앙 행 Hs(x)
    for h_target in (6.0, 4.5):
        ix = int(np.argmin(np.abs(depth1d - h_target)))
        assert hs_x[ix] <= 0.73 * depth1d[ix] * 1.15, \
            f"h={depth1d[ix]:.1f}: Hs={hs_x[ix]:.2f} > γh 상한"
    # 쇄파대 내 단조 감소 (h=8m → h=4.5m 구간)
    i8 = int(np.argmin(np.abs(depth1d - 8.0)))
    i45 = int(np.argmin(np.abs(depth1d - 4.5)))
    assert hs_x[i45] < hs_x[i8]


# ── 4. 해저마찰 감쇠 (소스항 직접 적분 — 이류 배제한 순수 감쇠 검증) ──
def test_bottom_friction_exponential_decay():
    from poseidon.engines.spectral_wave.sources import bottom_friction

    grid = SpectralGrid()
    h = 8.0
    fi = int(np.argmin(np.abs(grid.f - 0.1)))
    sig_all = 2 * np.pi * grid.f
    kh = wavenumber(sig_all, h) * h                   # (nf,)

    e = np.zeros((grid.nf, grid.ntheta))
    e[fi, 0] = (0.5 / 4.0) ** 2 / (grid.df[fi] * grid.dtheta)
    e0 = grid.total_energy(e)

    T, dt = 3 * 3600.0, 120.0
    for _ in range(int(T / dt)):                      # 반음해 적분 (엔진과 동일)
        s = bottom_friction(e, grid, kh, G)
        d = np.where(e > 1e-30, -s / np.maximum(e, 1e-30), 0.0)
        e = np.maximum(e + s * dt / (1.0 + d * dt), 0.0)

    sig = sig_all[fi]
    k = float(wavenumber(sig, h))
    gamma = 0.038 * sig ** 2 / (G ** 2 * np.sinh(k * h) ** 2)
    ratio = float(grid.total_energy(e) / e0)
    assert abs(np.log(ratio) - (-gamma * T)) < 0.05 * gamma * T
