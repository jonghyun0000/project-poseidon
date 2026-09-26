"""선형 파동 이론 기본량 (Airy). 근거: 임의 수심 분산관계 σ² = g k tanh(kh).

전 함수 벡터화·순수 함수. 수심 h→∞에서 심수 극한, kh→0에서 천수 극한과 일치해야 한다
(단위 테스트로 강제).
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from poseidon.physics.constants import G_STANDARD

Arr = NDArray[np.float64]


def wavenumber(sigma: Arr | float, depth: Arr | float, g: float = G_STANDARD,
               n_iter: int = 30) -> Arr:
    """분산관계 σ² = g k tanh(kh)의 k를 뉴턴법으로 푼다.

    초기값: Eckart (1952) 근사 — 전 수심에서 오차 <5%라 뉴턴 수렴이 빠르다.
    """
    sigma = np.asarray(sigma, dtype=np.float64)
    depth = np.maximum(np.asarray(depth, dtype=np.float64), 1e-8)
    s2 = sigma ** 2
    x = s2 * depth / g                                # (kh)_deep 스케일
    k = (x / np.sqrt(np.tanh(x))) / depth             # Eckart 초기값
    for _ in range(n_iter):
        kh = np.clip(k * depth, 1e-12, 50.0)
        t = np.tanh(kh)
        f = g * k * t - s2
        df = g * t + g * kh * (1.0 - t * t)
        k = np.maximum(k - f / df, 1e-12)
    return k


def group_velocity(sigma: Arr | float, depth: Arr | float,
                   g: float = G_STANDARD) -> Arr:
    """군속도 c_g = ∂σ/∂k = (c/2)(1 + 2kh/sinh 2kh)."""
    k = wavenumber(sigma, depth, g)
    kh = np.clip(k * np.asarray(depth, dtype=np.float64), 1e-12, 50.0)
    c = np.asarray(sigma, dtype=np.float64) / k
    n = 0.5 * (1.0 + 2.0 * kh / np.sinh(2.0 * kh))
    return c * n


def deep_group_velocity(f: Arr | float, g: float = G_STANDARD) -> Arr:
    """심수 군속도 c_g = g/(4πf)."""
    return g / (4.0 * np.pi * np.asarray(f, dtype=np.float64))


def jonswap_spectrum(f: Arr, fp: float, alpha: float = 0.0081, gamma: float = 3.3,
                     g: float = G_STANDARD) -> Arr:
    """JONSWAP 1D 스펙트럼 E(f) [m²/Hz] (Hasselmann et al. 1973). 테스트 기준용."""
    f = np.asarray(f, dtype=np.float64)
    sig = np.where(f <= fp, 0.07, 0.09)
    r = np.exp(-((f - fp) ** 2) / (2.0 * sig ** 2 * fp ** 2))
    pm = alpha * g ** 2 * (2.0 * np.pi) ** -4 * f ** -5 * np.exp(-1.25 * (fp / f) ** 4)
    return pm * gamma ** r
