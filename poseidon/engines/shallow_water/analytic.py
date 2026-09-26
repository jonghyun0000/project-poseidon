"""SWE 해석해 (M4 검증 게이트의 기준값).

- Stoker (1957) 습윤 하상 댐붕괴: 희박파 + 충격파, 폐형 프로파일
- Thacker (1981) 회전 포물면 분지의 평면 수면 진동: 습윤/건조 경계가 움직이는 정확해
"""

from __future__ import annotations

import math

import numpy as np
from numpy.typing import NDArray

Arr = NDArray[np.float64]


def stoker_dambreak(x: Arr, t: float, x0: float, h_l: float, h_r: float,
                    g: float) -> tuple[Arr, Arr]:
    """평평한 바닥 위 습윤 댐붕괴 (h_l > h_r > 0)의 (h, u).

    중간 수심 h_m은 희박파-충격파 접속 조건의 근:
      2(√(g h_l) − √(g h_m)) = (h_m − h_r) √( g (h_m + h_r) / (2 h_m h_r) )
    """
    if not (h_l > h_r > 0):
        raise ValueError("requires h_l > h_r > 0")

    def f(h: float) -> float:
        return (2.0 * (math.sqrt(g * h_l) - math.sqrt(g * h))
                - (h - h_r) * math.sqrt(g * (h + h_r) / (2.0 * h * h_r)))

    lo, hi = h_r, h_l
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if f(mid) > 0:
            lo = mid
        else:
            hi = mid
    h_m = 0.5 * (lo + hi)
    c_l, c_m = math.sqrt(g * h_l), math.sqrt(g * h_m)
    u_m = 2.0 * (c_l - c_m)
    s_shock = h_m * u_m / (h_m - h_r)

    xi = (x - x0) / max(t, 1e-300)
    h = np.empty_like(x)
    u = np.empty_like(x)

    left = xi <= -c_l
    rare = (~left) & (xi <= u_m - c_m)
    mid_ = (~left) & (~rare) & (xi <= s_shock)
    right = xi > s_shock

    h[left], u[left] = h_l, 0.0
    h[rare] = ((2.0 * c_l - xi[rare]) / 3.0) ** 2 / g
    u[rare] = 2.0 / 3.0 * (xi[rare] + c_l)
    h[mid_], u[mid_] = h_m, u_m
    h[right], u[right] = h_r, 0.0
    return h, u


def thacker_planar(x: Arr, y: Arr, t: float, *, a: float, h0: float, eta: float,
                   g: float) -> tuple[Arr, float, float, Arr]:
    """포물면 분지의 평면 수면 회전 진동 (Thacker 1981).

    지형:   z(x,y) = −h0 (1 − r²/a²)
    각진동수 ω = √(2 g h0) / a,  주기 T = 2π/ω
    수면:   w(x,y,t) = (η h0 / a²)(2x cos ωt + 2y sin ωt − η)
    속도:   u(t) = −η ω sin ωt,  v(t) = η ω cos ωt   (공간 균일)
    반환: (수심 h, u, v, 지형 z)
    """
    z = -h0 * (1.0 - (x ** 2 + y ** 2) / a ** 2)
    om = math.sqrt(2.0 * g * h0) / a
    w = (eta * h0 / a ** 2) * (2.0 * x * math.cos(om * t)
                               + 2.0 * y * math.sin(om * t) - eta)
    h = np.maximum(w - z, 0.0)
    u = -eta * om * math.sin(om * t)
    v = eta * om * math.cos(om * t)
    return h, u, v, z


def thacker_period(a: float, h0: float, g: float) -> float:
    return 2.0 * math.pi * a / math.sqrt(2.0 * g * h0)
