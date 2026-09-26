"""SWE 엔진 기본 연산 (단위 테스트 대상 순수 함수들).

근거 문헌:
- Kurganov & Petrova (2007) — 일반화 minmod, 속도 탈특이화(desingularization)
- Gottlieb, Shu & Tadmor (2001) — SSP-RK2
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

Arr = NDArray[np.float64]


def minmod3(a: Arr, b: Arr, c: Arr) -> Arr:
    """3인자 minmod: 부호가 모두 같을 때 최소 크기, 아니면 0."""
    pos = np.minimum(np.minimum(a, b), c)
    neg = np.maximum(np.maximum(a, b), c)
    return np.where((a > 0) & (b > 0) & (c > 0), pos,
                    np.where((a < 0) & (b < 0) & (c < 0), neg, 0.0))


def slopes(q: Arr, axis: int, theta: float = 1.3) -> Arr:
    """일반화 minmod 기울기 (KP07 식 (2.10), θ∈[1,2], θ=1.3).

    입력 대비 해당 축으로 2 셀 짧은 배열 반환 (양끝 셀 제외).
    """
    qm = np.take(q, range(0, q.shape[axis] - 2), axis=axis)
    q0 = np.take(q, range(1, q.shape[axis] - 1), axis=axis)
    qp = np.take(q, range(2, q.shape[axis]), axis=axis)
    return minmod3(theta * (q0 - qm), 0.5 * (qp - qm), theta * (qp - q0))


def desingularized_velocity(h: Arr, hq: Arr, h_dry: float) -> Arr:
    """u = √2·h·(hu) / √(h⁴ + max(h⁴, ε)),  ε = h_dry⁴ (KP07 식 (2.17)).

    h ≫ h_dry에서 u → hu/h, h → 0에서 u → 0 (0/0 특이점 제거).
    """
    eps = h_dry ** 4
    h4 = h ** 4
    return np.sqrt(2.0) * h * hq / np.sqrt(h4 + np.maximum(h4, eps))


def coriolis_rotate(u: Arr, v: Arr, f: Arr | float, dt: float) -> tuple[Arr, Arr]:
    """코리올리 항 정확 적분: du/dt = f v, dv/dt = −f u → 각 −f·dt 회전.

    속력 크기를 기계정밀도로 보존한다 (관성진동 에너지 보존).
    """
    th = f * dt
    c, s = np.cos(th), np.sin(th)
    return c * u + s * v, -s * u + c * v


def friction_semi_implicit(u: Arr, v: Arr, h: Arr, cf: float, dt: float,
                           h_dry: float) -> tuple[Arr, Arr]:
    """이차 해저마찰 반음해 적분 (PHASE2 §7.2): u/(1 + Δt·C_f·|u|/h).

    박수층(h→0)에서 무조건 안정 — 명시적 적분의 강성 문제를 회피한다.
    """
    speed = np.hypot(u, v)
    hh = np.maximum(h, h_dry)
    fac = 1.0 / (1.0 + dt * cf * speed / hh)
    return u * fac, v * fac
