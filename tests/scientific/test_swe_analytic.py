"""M4 게이트: SWE 엔진 해석해 검증 3종 (PHASE2 §7.2, PHASE4 §10).

전부 통과해야 Phase 5 진입 가능:
1. 정지호수 (well-balanced): 섬이 있는 불규칙 지형에서 기계정밀도 정지 유지
2. Stoker 댐붕괴: 해석해 대비 L1 오차 + 격자 세분화 수렴
3. Thacker 포물면 진동: 습윤/건조 이동 경계에서 1주기 후 해석해 복귀 + 질량 보존
"""

import numpy as np
import pytest

from poseidon.engines.shallow_water import SWESolver, SWEState
from poseidon.engines.shallow_water.analytic import (
    stoker_dambreak,
    thacker_period,
    thacker_planar,
)

G = 9.80665
pytestmark = pytest.mark.scientific


# ── 1. 정지호수 (C-property) ────────────────────────────────────────
def test_lake_at_rest_with_island_stays_still():
    n = 50
    L = 1000.0
    xc = np.linspace(0, L, n + 1)
    X, Y = np.meshgrid(xc, xc)
    # 수면(0) 위로 솟는 섬 + 수중 둔덕 + 급경사 — 지형 불규칙성 총동원
    b = (-8.0
         + 6.0 * np.exp(-((X - 300) ** 2 + (Y - 300) ** 2) / 150 ** 2) * 1.6  # 섬 (최고 +1.6)
         + 3.0 * np.exp(-((X - 700) ** 2 + (Y - 650) ** 2) / 200 ** 2))       # 수중 둔덕
    solver = SWESolver(b, dx=L / n, dy=L / n, g=G)
    s0 = solver.state_at_rest(0.0)
    m0 = solver.mass(s0)

    s = s0
    for _ in range(200):
        s = solver.step(s)

    wet = solver.depth(s0) > 0
    assert float(np.abs(s.w - s0.w)[wet].max()) < 1e-10      # 수면 부동
    assert float(np.abs(s.hu).max()) < 1e-10                 # 유속 발생 없음
    assert float(np.abs(s.hv).max()) < 1e-10
    assert abs(solver.mass(s) - m0) / m0 < 1e-13             # 질량 보존


# ── 2. Stoker 댐붕괴 ───────────────────────────────────────────────
def _run_dambreak(nx: int, t_end: float = 0.7):
    L, x0, hl, hr = 10.0, 5.0, 1.0, 0.1
    ny = 4
    dx = L / nx
    b = np.zeros((ny + 1, nx + 1))                            # 평평한 바닥
    solver = SWESolver(b, dx=dx, dy=dx, g=G, bc="wall")
    xc = (np.arange(nx) + 0.5) * dx
    w = np.where(xc < x0, hl, hr)[None, :].repeat(ny, axis=0)
    s = SWEState(w=w.astype(float), hu=np.zeros((ny, nx)),
                 hv=np.zeros((ny, nx)), t=0.0)
    s = solver.run(s, t_end)
    h_num = solver.depth(s)[ny // 2]
    h_ex, _ = stoker_dambreak(xc, t_end, x0, hl, hr, G)
    return float(np.abs(h_num - h_ex).mean()), h_num, h_ex


def test_stoker_dambreak_accuracy_and_convergence():
    e200, _, _ = _run_dambreak(200)
    e400, h_num, h_ex = _run_dambreak(400)

    assert e400 < 0.01                    # 평균 L1 오차 < 1 cm (수심 0.1–1 m 문제)
    assert e200 / e400 > 1.4              # 격자 2배 세분화 시 오차 감소 (수렴성)
    # 충격파 위치: 수심이 (h_m+h_r)/2를 지나는 지점 오차 < 3%
    h_m = 0.5 * (h_ex.max() + h_ex.min())
    pos_num = np.argmin(np.abs(h_num - h_m))
    pos_ex = np.argmin(np.abs(h_ex - h_m))
    assert abs(pos_num - pos_ex) <= 12    # 400셀 중 12셀(3%) 이내


def test_dambreak_conserves_mass():
    nx, ny, L = 200, 4, 10.0
    dx = L / nx
    b = np.zeros((ny + 1, nx + 1))
    solver = SWESolver(b, dx=dx, dy=dx, g=G, bc="wall")
    xc = (np.arange(nx) + 0.5) * dx
    w = np.where(xc < 5.0, 1.0, 0.1)[None, :].repeat(ny, axis=0)
    s = SWEState(w=w.astype(float), hu=np.zeros((ny, nx)),
                 hv=np.zeros((ny, nx)), t=0.0)
    m0 = solver.mass(s)
    s = solver.run(s, 0.5)
    assert abs(solver.mass(s) - m0) / m0 < 1e-12


# ── 3. Thacker 포물면 진동 (습윤/건조) ──────────────────────────────
def test_thacker_planar_one_period():
    a, h0, eta = 3000.0, 10.0, 300.0
    n = 100
    L = 8000.0                                               # [-4000, 4000]²
    dx = L / n
    corner = np.linspace(-L / 2, L / 2, n + 1)
    Xc, Yc = np.meshgrid(corner, corner)
    b = -h0 * (1.0 - (Xc ** 2 + Yc ** 2) / a ** 2)           # 꼭짓점 지형

    cell = 0.5 * (corner[:-1] + corner[1:])
    X, Y = np.meshgrid(cell, cell)
    h_i, u_i, v_i, z = thacker_planar(X, Y, 0.0, a=a, h0=h0, eta=eta, g=G)

    solver = SWESolver(b, dx=dx, dy=dx, g=G, h_dry=1e-3)
    s = SWEState(w=np.where(h_i > 0, h_i + z, solver.bc_cell),
                 hu=h_i * u_i, hv=h_i * v_i, t=0.0)
    m0 = solver.mass(s)

    T = thacker_period(a, h0, G)
    s = solver.run(s, T)                                     # 정확히 한 주기

    h_num = solver.depth(s)
    h_ex, _, _, _ = thacker_planar(X, Y, T, a=a, h0=h0, eta=eta, g=G)

    assert float(h_num.min()) >= 0.0                                     # 양수성
    assert abs(solver.mass(s) - m0) / m0 < 1e-10                         # 질량 보존
    rel_l1 = float(np.abs(h_num - h_ex).sum() / h_ex.sum())
    assert rel_l1 < 0.05                                                 # 1주기 후 L1 < 5%
    # 습윤 면적도 해석해와 근접 (이동 경계 추적 검증)
    wet_err = abs((h_num > 0.01).sum() - (h_ex > 0.01).sum()) / (h_ex > 0.01).sum()
    assert wet_err < 0.1
