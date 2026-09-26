"""Hs 증분 -> 스펙트럼 E(f,theta) 갱신 (2 파라미터 재척도).

**문제**: 관측은 Hs 하나(적분량 1개)인데 상태는 셀당 32 x 36 = 1,152 자유도다.
심각한 under-determined 이므로 "형상을 어떻게 유지할 것인가"가 곧 해의 정의다.
문헌은 스펙트럼 형상을 보존하는 2 파라미터 변환으로 답한다:

    E_a(f, theta) = A * E_b(B * f, theta)                       (ECMWF IFS Cy45r1 식 4.3)

적분 관계: m0_a = (A/B) m0_b,  f_mean_a = f_mean_b / B.

분기
----
너울(SWELL)  — Lionello & Janssen (1990); IFS 4.8~4.9
    파형경사 s = k_bar H / 8 를 보존하도록
        B = q^{1/2},  A = q^{5/2}          (q = Hs_a / Hs_b)
    검산: m0_a/m0_b = A/B = q^2 (OK),  k_bar_a H_a = k_bar_b H_b (심수 k ∝ f^2) (OK)
    천해 정지조건 (IFS 4.11): k_mean <= 0.0981 * d^{-0.425} 이면 B=1, A=q^2.
      [확인 필요 2026-08-24] 이 임계의 수치 상수는 설계 문서에서 인용된 IFS 값이며
      본 세션에서 원문으로 독립 확인하지 못했다. shallow_stop=False 로 끌 수 있다.

풍파(WINDSEA) — Hasselmann et al. (1988) JPO 18, 1775; Bauer et al. (1992)
        B = 1,  A = q^2   (형상 보존 · 에너지만 척도)
    **[미구현]** IFS 4.4~4.6 의 Kitaigorodskii 무차원 성장곡선 역산
    (eps(tau) 관계로 tau -> nu -> B 를 정하는 방식)은 성장법칙 상수를
    동료심사 원문에서 확인하지 못해 구현하지 않았다. 추측으로 메우지 않는다
    (CLAUDE.md 규칙 1). 풍파는 수 시간 내에 바람과 재평형하므로 주파수 이동의
    지속 효과가 작다는 것이 이 대체안의 물리적 근거다(Janssen et al. 1987).

전 분기 공통
------------
- 기하 주파수 격자 f_i = f0 r^i 에서 B*f_i = f_{i+s}, s = ln B / ln r
  -> 로그주파수 인덱스 시프트 + 선형보간 한 번.
- i+s > nf-1: 진단 꼬리 f^{-5} 연속 (엔진 apply_tail_and_limits 와 같은 규약)
- i+s < 0: 0
- 이산 보간 오차는 마지막에 E_a *= (Hs_target/hs(E_a))^2 로 정확히 흡수한다
  (양수 스칼라 곱이라 양수성 불변).
"""

from __future__ import annotations

import logging

import numpy as np
from numpy.typing import NDArray

from poseidon.assimilation.params import WINDSEA_ENERGY_FRAC
from poseidon.engines.spectral_wave.grid import SpectralGrid
from poseidon.engines.spectral_wave.sources import friction_velocity

log = logging.getLogger("poseidon.assimilation.spectral")

Arr = NDArray[np.float64]

SHALLOW_K_COEF = 0.0981     # IFS 4.11  k_min = 0.0981 d^{-0.425}  [확인 필요]
SHALLOW_K_EXP = -0.425
TAIL_POWER = 5.0            # 진단 꼬리 E ∝ f^{-5}


def windsea_fraction(e: Arr, grid: SpectralGrid, u10: Arr, udir: Arr,
                     c_phase: Arr | None = None, g: float = 9.80665) -> Arr:
    """셀별 풍파 에너지 비율 E_ws / E_tot.

    판정식은 엔진 wind_input(Snyder/WAM3, Komen et al. 1994)과 **동일**하다:
        성분 (f,theta) 가 풍파  <=>  28 u* cos(theta - theta_u) / c(f) > 1
    (wind_input 의 beta 가 양이 되는 조건과 같은 식이므로 물리 일관성이 보장된다.)
    """
    ustar = np.asarray(friction_velocity(u10), dtype=np.float64)[..., None, None]
    tw = np.asarray(udir, dtype=np.float64)[..., None, None]
    if c_phase is None:
        c = (g / (2.0 * np.pi * grid.f))[:, None]
    else:
        c = np.asarray(c_phase, dtype=np.float64)[..., :, None]
    cosd = np.cos(grid.theta[None, :] - tw)
    ws = (28.0 * ustar / c * cosd) > 1.0
    w = grid.df[:, None] * grid.dtheta
    e64 = np.asarray(e, dtype=np.float64)
    tot = (e64 * w).sum(axis=(-2, -1))
    ews = (np.where(ws, e64, 0.0) * w).sum(axis=(-2, -1))
    return np.where(tot > 1e-12, ews / np.maximum(tot, 1e-30), 0.0)


def rescale_factors(q: Arr, is_windsea: NDArray[np.bool_], mode: str,
                    shallow_stop: NDArray[np.bool_] | None = None,
                    ) -> tuple[Arr, Arr]:
    """(A, B) 계수. mode='ecmwf' 분기 | 'energy' 는 전 셀 B=1, A=q^2."""
    q = np.asarray(q, dtype=np.float64)
    if mode == "energy":
        return q ** 2, np.ones_like(q)
    if mode != "ecmwf":
        raise ValueError(f"rescale must be 'ecmwf' or 'energy', got {mode!r}")
    b = np.where(is_windsea, 1.0, np.sqrt(q))
    if shallow_stop is not None:
        b = np.where(shallow_stop, 1.0, b)
    a = q ** 2 * b
    return a, b


def shift_spectrum(e: Arr, grid: SpectralGrid, a: Arr, b: Arr,
                   dtype=np.float32) -> Arr:
    """E_a(f_i, theta) = A * E_b(B f_i, theta), 로그주파수 선형보간 + f^-5 꼬리.

    e: (..., nf, ntheta), a/b: (...) 셀별 계수.
    """
    e64 = np.asarray(e, dtype=np.float64)
    nf = grid.nf
    lr = np.log(grid.ratio)
    s = np.log(np.maximum(np.asarray(b, dtype=np.float64), 1e-12)) / lr   # (...)
    base = np.arange(nf, dtype=np.float64)
    tgt = base + s[..., None]                       # (..., nf)
    clipped = np.clip(tgt, 0.0, nf - 1.0)
    i0 = np.floor(clipped).astype(np.int64)
    i1 = np.minimum(i0 + 1, nf - 1)
    w = (clipped - i0)[..., None]                   # (..., nf, 1)
    lo = np.take_along_axis(e64, i0[..., None], axis=-2)
    hi = np.take_along_axis(e64, i1[..., None], axis=-2)
    out = (1.0 - w) * lo + w * hi
    # 격자 상한을 넘어선 표본: f^{-5} 진단 꼬리로 연속 (r^{-5*(tgt-(nf-1))})
    over = np.maximum(tgt - (nf - 1.0), 0.0)[..., None]
    out = out * grid.ratio ** (-TAIL_POWER * over)
    # 격자 하한 아래: 에너지 없음
    out = np.where((tgt < 0.0)[..., None], 0.0, out)
    out = out * np.asarray(a, dtype=np.float64)[..., None, None]
    return np.maximum(out, 0.0).astype(dtype)


def apply_increment(e: Arr, grid: SpectralGrid, q: Arr, *,
                    is_windsea: NDArray[np.bool_] | None = None,
                    mode: str = "ecmwf",
                    shallow_stop: NDArray[np.bool_] | None = None,
                    sea: NDArray[np.bool_] | None = None,
                    dtype=np.float32,
                    row_block: int = 16) -> tuple[Arr, dict[str, object]]:
    """스펙트럼 필드 (ny,nx,nf,nth) 에 셀별 배율 q 를 반영한다.

    q == 1 인 셀은 **비트 동일**로 통과시킨다(항등성). 마지막에 Hs 를 정확히
    q*Hs_b 로 재정규화한다.
    """
    e = np.asarray(e)
    ny, nx = e.shape[0], e.shape[1]
    q = np.asarray(q, dtype=np.float64)
    if is_windsea is None:
        is_windsea = np.zeros((ny, nx), dtype=bool)
    if sea is None:
        sea = np.ones((ny, nx), dtype=bool)
    touch = sea & (np.abs(q - 1.0) > 1e-12)
    out = e.copy()
    if not touch.any():
        return out, {"n_cells": 0, "hs_err_max": 0.0}

    hs_b_all = grid.hs(np.asarray(e, dtype=np.float64))
    err_max = 0.0
    for y0 in range(0, ny, row_block):
        y1 = min(y0 + row_block, ny)
        m = touch[y0:y1]
        if not m.any():
            continue
        eb = np.asarray(e[y0:y1], dtype=np.float64)
        qa = q[y0:y1]
        ss = None if shallow_stop is None else shallow_stop[y0:y1]
        a, b = rescale_factors(qa, is_windsea[y0:y1], mode, ss)
        ea = shift_spectrum(eb, grid, a, b, dtype=np.float64)
        # 정확 재정규화: hs(E_a) 를 목표 q*Hs_b 에 맞춘다 (양수 스칼라 곱)
        hs_tgt = qa * hs_b_all[y0:y1]
        hs_now = grid.hs(ea)
        scale = np.where(hs_now > 1e-9, (hs_tgt / np.maximum(hs_now, 1e-9)) ** 2, 1.0)
        ea = ea * scale[..., None, None]
        err = np.abs(grid.hs(ea) - hs_tgt)[m]
        err_max = max(err_max, float(err.max()) if err.size else 0.0)
        blk = out[y0:y1]
        blk[m] = ea[m].astype(e.dtype)
        out[y0:y1] = blk
    if sea is not None:
        out[~sea] = 0.0
    return out, {"n_cells": int(touch.sum()), "hs_err_max": err_max,
                 "mode": mode}


def shallow_stop_mask(e: Arr, grid: SpectralGrid, depth: Arr, g: float) -> NDArray[np.bool_]:
    """IFS 4.11 천해 정지조건 k_mean <= 0.0981 d^{-0.425}.  [확인 필요: 상수 미확인]"""
    _, k_mean = grid.mean_sigma_k(np.asarray(e, dtype=np.float64), g)
    k_min = SHALLOW_K_COEF * np.maximum(np.asarray(depth, dtype=np.float64), 1e-3) ** SHALLOW_K_EXP
    return k_mean <= k_min


__all__ = ["WINDSEA_ENERGY_FRAC", "apply_increment", "rescale_factors",
           "shallow_stop_mask", "shift_spectrum", "windsea_fraction"]
