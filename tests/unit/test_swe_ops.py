import numpy as np
import pytest

from poseidon.engines.shallow_water.ops import (
    coriolis_rotate,
    desingularized_velocity,
    friction_semi_implicit,
    minmod3,
)


def test_minmod3_selects_smallest_same_sign():
    a = np.array([1.0, -1.0, 1.0, 0.5])
    b = np.array([2.0, -3.0, -1.0, 0.2])
    c = np.array([0.5, -2.0, 2.0, 0.9])
    out = minmod3(a, b, c)
    np.testing.assert_allclose(out, [0.5, -1.0, 0.0, 0.2])


def test_desingularized_velocity_limits():
    h_dry = 1e-3
    # 깊은 물: u ≈ hu/h
    h = np.array([10.0])
    hu = np.array([25.0])
    u = desingularized_velocity(h, hu, h_dry)
    np.testing.assert_allclose(u, 2.5, rtol=1e-12)
    # 완전 건조: u = 0 (0/0 특이점 없음)
    np.testing.assert_allclose(desingularized_velocity(
        np.array([0.0]), np.array([0.0]), h_dry), 0.0)
    # 극박수층: 속도 폭주 억제 (|u| ≤ |hu|/h 나이브 계산보다 작음)
    h = np.array([1e-6])
    hu = np.array([1e-6])  # 나이브 u = 1 m/s
    assert abs(desingularized_velocity(h, hu, h_dry)[0]) < 1e-2


def test_coriolis_rotation_preserves_speed_and_period():
    f = 1e-4
    u = np.array([1.0])
    v = np.array([0.0])
    dt = 2 * np.pi / f / 1000
    uu, vv = u.copy(), v.copy()
    for _ in range(1000):  # 관성진동 한 주기
        uu, vv = coriolis_rotate(uu, vv, f, dt)
    np.testing.assert_allclose(np.hypot(uu, vv), 1.0, rtol=1e-12)  # 에너지 보존
    np.testing.assert_allclose(uu, 1.0, atol=1e-9)                 # 주기 복귀
    np.testing.assert_allclose(vv, 0.0, atol=1e-9)


def test_friction_matches_ode_solution():
    # du/dt = -cf u|u|/h (h, cf 상수) → u(t) = u0 / (1 + cf u0 t / h)
    cf, h, u0, t_end, n = 3e-3, 2.0, 1.5, 200.0, 400
    dt = t_end / n
    u = np.array([u0])
    v = np.array([0.0])
    hh = np.array([h])
    for _ in range(n):
        u, v = friction_semi_implicit(u, v, hh, cf, dt, 1e-3)
    exact = u0 / (1 + cf * u0 * t_end / h)
    np.testing.assert_allclose(u[0], exact, rtol=5e-3)
    assert u[0] <= u0  # 단조 감쇠


def test_friction_stable_in_thin_layer():
    u = np.array([5.0])
    v = np.array([0.0])
    h = np.array([1e-9])  # 극박수층에서도 발산 금지 (반음해)
    for _ in range(100):
        u, v = friction_semi_implicit(u, v, h, 3e-3, 10.0, 1e-3)
    assert np.isfinite(u).all() and abs(u[0]) < 5.0
