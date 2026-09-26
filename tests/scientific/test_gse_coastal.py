"""GSE 평균화의 해안 에너지 보존 (회귀 방지).

이번 프로젝트에서 육지 마스크 결함이 세 번 반복됐다(검증 콜로케이션, 중첩 보간, GSE 평균화).
GSE 스텐실이 육지로 산포한 에너지는 직후 `e[~sea]=0`이 소각하므로, 마스크 없이 쓰면
해안이 일방향 싱크가 되어 해안 첫 셀 Hs가 −34~−47% 파괴된다(실측).
이 테스트는 **육지가 있는 도메인**에서 해안 평행 전파의 에너지가 보존되는지 확인한다.
"""

import numpy as np
import pytest

from poseidon.engines.spectral_wave.grid import SpectralGrid
from poseidon.engines.spectral_wave.regional import RegionalWaveModel

pytestmark = pytest.mark.scientific


def _coast_model(gse: str, ny=24, nx=30, land_cols=6):
    grid = SpectralGrid()
    lats = 33.0 + 0.25 * np.arange(ny)
    lons = 125.0 + 0.25 * np.arange(nx)
    depth = np.full((ny, nx), 3000.0)
    depth[:, :land_cols] = 0.0                      # 서쪽 육지 (남북 직선 해안)
    return grid, RegionalWaveModel(lats, lons, depth, grid, h_min=10.0,
                                   enable=(), advection="uno2", gse=gse)


def _uniform_northward(m, grid, hs=1.0):
    """북향(θ=90°) 단일 방향 빈, 공간 균일 스웰 — 해안과 평행하므로 육지로 가지 않는다."""
    e = np.zeros((m.ny, m.nx, grid.nf, grid.ntheta), dtype=np.float32)
    fi = int(np.argmin(np.abs(grid.f - 0.08)))
    ti = int(np.argmin(np.abs(grid.theta - np.pi / 2)))
    e[..., fi, ti] = (hs / 4.0) ** 2 / (grid.df[fi] * grid.dtheta)
    e[~m.sea] = 0.0
    return e


@pytest.mark.parametrize("gse", ["none", "tolman"])
def test_coast_parallel_swell_is_not_drained(gse):
    """해안과 평행하게 흐르는 균일 스웰은 해안 인접 셀에서도 감쇠하면 안 된다."""
    grid, m = _coast_model(gse)
    e = _uniform_northward(m, grid)
    m.set_boundary(e, sides="WESN")

    dt = m.cfl_dt()
    for _ in range(int(12 * 3600 / dt)):
        e = m.step(e, dt, sources=False)

    hs = grid.hs(e.astype(np.float64))
    row = m.ny // 2
    sea_cols = np.where(m.sea[row])[0]
    coast = sea_cols[0]                              # 해안 첫 바다 셀
    interior = sea_cols[len(sea_cols) // 2]

    assert hs[row, coast] > 0.95, (
        f"gse={gse}: 해안 첫 셀 Hs {hs[row, coast]:.3f} — 육지로 에너지가 새고 있다")
    assert abs(hs[row, coast] / hs[row, interior] - 1.0) < 0.05, (
        f"gse={gse}: 해안/내부 비 {hs[row, coast]/hs[row, interior]:.3f}")


def test_gse_conserves_mass_over_sea():
    """GSE 평균화는 바다 위에서 질량(면적가중 에너지)을 보존한다."""
    from poseidon.engines.spectral_wave.regional import gse_average

    grid, m = _coast_model("tolman")
    rng = np.random.default_rng(0)
    e = rng.random((m.ny, m.nx, grid.nf, grid.ntheta)).astype(np.float32)
    e[~m.sea] = 0.0
    w = m.gse_weights(m.cfl_dt())

    before = float((e * m._w4).sum())
    out = gse_average(e, w, m._w4, m._sea4, np)
    out = np.where(m.sea[:, :, None, None], out, 0.0)
    after = float((out * m._w4).sum())

    assert abs(after / before - 1.0) < 1e-5, f"질량 {before:.6e} → {after:.6e}"
    assert out.min() >= 0.0                          # 양수보존
