"""SWE 대기 강제 해석 검증 (Phase 7): 역기압계 + 정상 바람 셋업.

1. 역기압(inverse barometer): 정지 기압 요란의 정상 응답 η = −Δp_a/(ρg)
2. 바람 셋업: 닫힌 수로의 정상 경사 ∂η/∂x = τ/(ρ g h)  (Pugh & Woodworth 2014, §7)

주의: 이차 해저마찰은 미소 진폭 지진동을 사실상 감쇠시키지 못하므로(선형 무감쇠),
정상 성분은 스냅숏이 아니라 **지진동 주기 평균**으로 추출한다 — 진동은 평균에서 상쇄된다.
"""

import numpy as np
import pytest

from poseidon.engines.shallow_water import AtmosForcing, SWESolver, SWEState
from poseidon.physics.constants import RHO_SEAWATER as RHO

G = 9.80665
pytestmark = pytest.mark.scientific


def _mean_surface(solver, s, forcing, t_avg: float):
    """t_avg 동안 매 스텝 수면을 평균 (지진동 상쇄)."""
    acc = np.zeros_like(s.w)
    n = 0
    t_end = s.t + t_avg
    while s.t < t_end - 1e-9:
        s = solver.step(s, dt_max=t_end - s.t, forcing=forcing)
        acc += s.w
        n += 1
    return acc / n, s


def test_inverse_barometer_response():
    n, L, h0 = 50, 500e3, 100.0
    dx = L / n
    b = np.full((n + 1, n + 1), -h0)
    solver = SWESolver(b, dx=dx, dy=dx, g=G, c_friction=2.5e-3)
    s = solver.state_at_rest(0.0)

    xc = (np.arange(n) + 0.5) * dx
    X, Y = np.meshgrid(xc, xc)
    dp = -2000.0 * np.exp(-((X - L / 2) ** 2 + (Y - L / 2) ** 2) / (120e3) ** 2)
    forcing = AtmosForcing(pa=101325.0 + dp)

    s = solver.run(s, 24 * 3600.0, forcing=forcing)          # 스핀업
    t_mode = 2 * L / np.sqrt(G * h0)                          # 최장 모드 주기 ≈ 8.9 h
    eta, _ = _mean_surface(solver, s, forcing, 2 * t_mode)    # 2주기 평균

    ib = -(dp - dp.mean()) / (RHO * G)                        # 닫힌 분지: 평균 0 기준
    err = float(np.abs(eta - ib).max())
    assert err < 0.10 * float(ib.max()), f"IB err {err:.3f} vs peak {ib.max():.3f}"
    assert abs(float(eta[25, 25]) / float(ib[25, 25]) - 1.0) < 0.07


def test_steady_wind_setup_slope():
    nx, ny, L, h0 = 100, 4, 200e3, 20.0
    dx = L / nx
    b = np.full((ny + 1, nx + 1), -h0)
    solver = SWESolver(b, dx=dx, dy=dx, g=G, c_friction=2.5e-3)
    s = solver.state_at_rest(0.0)

    tau = 0.3                                                 # N/m² (~13 m/s 바람)
    forcing = AtmosForcing(taux=tau)
    s = solver.run(s, 24 * 3600.0, forcing=forcing)           # 스핀업
    t_mode = 2 * L / np.sqrt(G * h0)                          # ≈ 7.9 h
    eta2d, _ = _mean_surface(solver, s, forcing, 2 * t_mode)  # 2주기 평균

    eta = eta2d[ny // 2]
    d_eta_theory = tau / (RHO * G * h0) * (L - dx)
    d_eta = float(eta[-1] - eta[0])
    assert abs(d_eta / d_eta_theory - 1.0) < 0.10, \
        f"setup {d_eta:.3f} m vs theory {d_eta_theory:.3f} m"
    # 중앙부 경사도 이론값과 일치 (끝단 효과 제외한 내부 25–75% 구간)
    slope = np.polyfit(np.arange(nx)[25:75] * dx, eta[25:75], 1)[0]
    assert abs(slope / (tau / (RHO * G * h0)) - 1.0) < 0.10