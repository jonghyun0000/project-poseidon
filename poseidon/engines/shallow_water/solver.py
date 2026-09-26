"""2D 천수방정식 엔진 — NumPy 참조 구현 (ADR-001 1단계).

기법 (PHASE2 §7.2, 전부 동료심사 문헌):
- 인터페이스 처리: Audusse et al. (2004) 정수압 재구성(hydrostatic reconstruction)
  — 습윤/건조 전선을 포함해 정지호수 균형이 증명된 기법. 자유수면 w와 수심 h를
  각각 minmod-MUSCL 재구성하고 z± = w± − h±, 인터페이스에서
  h* = max(0, w± − max(z_L, z_R)) 로 별표 상태를 만든다.
- 수치 플럭스: Kurganov 계열 central-upwind (별표 상태에 적용)
- 시간: SSP-RK2 (Gottlieb, Shu & Tadmor 2001)
- 속도 탈특이화: Kurganov & Petrova (2007) 식 (2.17)
- 코리올리: 정확 회전 / 해저마찰: 반음해 (분할 적분)

좌표: 등간격 직교 격자 (dx, dy [m]). 상태: w(자유수면고), hu, hv — shape (ny, nx).
지형: 셀 꼭짓점 표고 b_corner (ny+1, nx+1) → 셀 평균 Bc 사용. 습윤/건조 자동 처리.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
from numpy.typing import NDArray

from poseidon.engines.shallow_water.ops import (
    coriolis_rotate,
    desingularized_velocity,
    friction_semi_implicit,
    slopes,
)
from poseidon.physics.constants import G_STANDARD, RHO_SEAWATER

Arr = NDArray[np.float64]
_TINY = 1e-12


@dataclass(frozen=True)
class SWEState:
    w: Arr    # 자유수면 표고 h + B  (ny, nx)
    hu: Arr   # x 방향 단위폭 유량   (ny, nx)
    hv: Arr   # y 방향 단위폭 유량   (ny, nx)
    t: float


@dataclass(frozen=True)
class AtmosForcing:
    """대기 강제 (PHASE2 §7.2): 해면 바람응력 τ [N/m²]과 해면기압 p_a [Pa]."""

    taux: Arr | float = 0.0
    tauy: Arr | float = 0.0
    pa: Arr | None = None


def _pad_axis1(q: Arr, bc: str, negate: bool) -> Arr:
    """axis=1 방향 2셀 고스트 패딩. bc: wall(경면 반사) | open(구배 0)."""
    ny, nx = q.shape
    p = np.empty((ny, nx + 4), dtype=q.dtype)
    p[:, 2:-2] = q
    if bc == "wall":
        sgn = -1.0 if negate else 1.0
        p[:, 1] = sgn * q[:, 0]
        p[:, 0] = sgn * q[:, 1]
        p[:, -2] = sgn * q[:, -1]
        p[:, -1] = sgn * q[:, -2]
    elif bc == "open":
        p[:, 1] = p[:, 0] = q[:, 0]
        p[:, -2] = p[:, -1] = q[:, -1]
    else:
        raise ValueError(f"unknown bc: {bc}")
    return p


def _pad_edge(q: Arr) -> Arr:
    return np.pad(q, ((0, 0), (2, 2)), mode="edge")


def _sweep(w: Arr, hqn: Arr, hqt: Arr, b_cell: Arr, g: float, h_dry: float,
           theta: float, bc: str) -> tuple[Arr, Arr, Arr, Arr, Arr, Arr, float]:
    """한 방향(축=1) 플럭스 스윕 — Audusse 정수압 재구성 + central-upwind.

    반환: (Hc, Hn, Ht) 인터페이스 플럭스 (ny, nx+1),
          (corr_L, corr_R) 좌/우 셀 운동량 압력 보정 (ny, nx+1),
          sc 셀 중심 경사 소스·운동량 (ny, nx)  [이미 1/dx 제외],
          최대 파속.
    """
    wp = _pad_axis1(w, bc, negate=False)
    np_ = _pad_axis1(hqn, bc, negate=True)   # 벽에서 법선 운동량 반사
    tp = _pad_axis1(hqt, bc, negate=False)
    bp = _pad_edge(b_cell)
    hp = np.maximum(wp - bp, 0.0)

    # minmod 재구성 (패딩 셀 1..nx+2 → nx+2개)
    sw, sh = slopes(wp, 1, theta), slopes(hp, 1, theta)
    sn, st = slopes(np_, 1, theta), slopes(tp, 1, theta)
    w0, h0 = wp[:, 1:-1], hp[:, 1:-1]
    n0, t0 = np_[:, 1:-1], tp[:, 1:-1]

    wE, wW = w0 + 0.5 * sw, w0 - 0.5 * sw
    hE = np.maximum(h0 + 0.5 * sh, 0.0)
    hW = np.maximum(h0 - 0.5 * sh, 0.0)
    zE, zW = wE - hE, wW - hW
    nE, nW = n0 + 0.5 * sn, n0 - 0.5 * sn
    tE, tW = t0 + 0.5 * st, t0 - 0.5 * st

    # 셀 내부 속도 (탈특이화, 별표 이전 수심 기준 — Audusse 2004)
    uE = desingularized_velocity(hE, nE, h_dry)
    uW = desingularized_velocity(hW, nW, h_dry)
    vE = desingularized_velocity(hE, tE, h_dry)
    vW = desingularized_velocity(hW, tW, h_dry)

    # 인터페이스 m=0..nx: 좌셀(E면) = recon[:, :-1], 우셀(W면) = recon[:, 1:]
    zL, zR = zE[:, :-1], zW[:, 1:]
    zmax = np.maximum(zL, zR)
    hLs = np.maximum(hE[:, :-1] + zL - zmax, 0.0)   # = max(0, wE_L − zmax)
    hRs = np.maximum(hW[:, 1:] + zR - zmax, 0.0)
    uL, uR = uE[:, :-1], uW[:, 1:]
    vL, vR = vE[:, :-1], vW[:, 1:]

    cL, cR = np.sqrt(g * hLs), np.sqrt(g * hRs)
    ap = np.maximum.reduce([uL + cL, uR + cR, np.zeros_like(cL)])
    am = np.minimum.reduce([uL - cL, uR - cR, np.zeros_like(cL)])
    denom = ap - am
    safe = denom > _TINY
    denom = np.where(safe, denom, 1.0)

    def _cuflux(fl: Arr, fr: Arr, ql: Arr, qr: Arr) -> Arr:
        h = (ap * fl - am * fr) / denom + (ap * am / denom) * (qr - ql)
        return np.where(safe, h, 0.0)

    qnL, qnR = hLs * uL, hRs * uR
    hc = _cuflux(qnL, qnR, hLs, hRs)
    hn = _cuflux(qnL * uL + 0.5 * g * hLs ** 2,
                 qnR * uR + 0.5 * g * hRs ** 2, qnL, qnR)
    ht = _cuflux(qnL * vL, qnR * vR, hLs * vL, hRs * vR)

    # Audusse 압력 보정 (운동량): 좌셀 동쪽 플럭스에 corr_L, 우셀 서쪽 플럭스에 corr_R
    corr_l = 0.5 * g * (hE[:, :-1] ** 2 - hLs ** 2)
    corr_r = 0.5 * g * (hW[:, 1:] ** 2 - hRs ** 2)

    # 2차 정확도 셀 중심 경사 소스 (real 셀 = recon 1..nx)
    hEr, hWr = hE[:, 1:-1], hW[:, 1:-1]
    zEr, zWr = zE[:, 1:-1], zW[:, 1:-1]
    sc = 0.5 * g * (hEr + hWr) * (zWr - zEr)

    amax = float(max(ap.max(initial=0.0), -am.min(initial=0.0)))
    return hc, hn, ht, corr_l, corr_r, sc, amax


class SWESolver:
    def __init__(self, b_corner: Arr, dx: float, dy: float, *,
                 g: float = G_STANDARD, cfl: float = 0.2, theta: float = 1.3,
                 h_dry: float = 1e-3, f_coriolis: float | Arr = 0.0,
                 c_friction: float = 0.0, bc: str = "wall",
                 rho: float = RHO_SEAWATER) -> None:
        self.rho = rho
        b_corner = np.asarray(b_corner, dtype=np.float64)
        self.bc_cell = 0.25 * (b_corner[:-1, :-1] + b_corner[:-1, 1:]
                               + b_corner[1:, :-1] + b_corner[1:, 1:])  # (ny, nx)
        self.dx, self.dy = float(dx), float(dy)
        self.g, self.cfl, self.theta, self.h_dry = g, cfl, theta, h_dry
        self.f, self.cf, self.bc = f_coriolis, c_friction, bc

    # ── 초기화 도우미 ──────────────────────────────────────────────
    def state_at_rest(self, w0: float) -> SWEState:
        w = np.maximum(np.full_like(self.bc_cell, w0), self.bc_cell)
        z = np.zeros_like(w)
        return SWEState(w=w, hu=z.copy(), hv=z.copy(), t=0.0)

    def depth(self, s: SWEState) -> Arr:
        return np.maximum(s.w - self.bc_cell, 0.0)

    def mass(self, s: SWEState) -> float:
        return float(self.depth(s).sum() * self.dx * self.dy)

    # ── 우변 L(U) ─────────────────────────────────────────────────
    def _rhs(self, s: SWEState,
             forcing: AtmosForcing | None = None) -> tuple[Arr, Arr, Arr, float, float]:
        xc, xn, xt, xcl, xcr, xsc, ax = _sweep(
            s.w, s.hu, s.hv, self.bc_cell, self.g, self.h_dry, self.theta, self.bc)
        # y 스윕: 전치 좌표계 (법선 운동량 = hv, 접선 = hu)
        yc, yn, yt, ycl, ycr, ysc, ay = _sweep(
            s.w.T, s.hv.T, s.hu.T, self.bc_cell.T, self.g, self.h_dry, self.theta, self.bc)
        yc, yn, yt = yc.T, yn.T, yt.T           # (ny+1, nx)
        ycl, ycr, ysc = ycl.T, ycr.T, ysc.T

        lw = (-(xc[:, 1:] - xc[:, :-1]) / self.dx
              - (yc[1:, :] - yc[:-1, :]) / self.dy)
        lhu = (-((xn[:, 1:] + xcl[:, 1:]) - (xn[:, :-1] + xcr[:, :-1])) / self.dx
               - (yt[1:, :] - yt[:-1, :]) / self.dy
               + xsc / self.dx)
        lhv = (-(xt[:, 1:] - xt[:, :-1]) / self.dx
               - ((yn[1:, :] + ycl[1:, :]) - (yn[:-1, :] + ycr[:-1, :])) / self.dy
               + ysc / self.dy)

        if forcing is not None and forcing.pa is not None:
            # 역기압 항: −(h/ρ) ∇p_a (해일의 기압 성분, PHASE2 §7.2)
            h_bar = np.maximum(s.w - self.bc_cell, 0.0)
            lhu -= h_bar / self.rho * np.gradient(forcing.pa, axis=1) / self.dx
            lhv -= h_bar / self.rho * np.gradient(forcing.pa, axis=0) / self.dy
        return lw, lhu, lhv, ax, ay

    # ── 시간 전진 ─────────────────────────────────────────────────
    def step(self, s: SWEState, dt_max: float = np.inf,
             forcing: AtmosForcing | None = None) -> SWEState:
        lw, lu, lv, ax, ay = self._rhs(s, forcing)
        dt = self.cfl * min(self.dx / max(ax, _TINY), self.dy / max(ay, _TINY))
        dt = float(min(dt, dt_max))

        s1 = self._clean(SWEState(s.w + dt * lw, s.hu + dt * lu, s.hv + dt * lv, s.t))
        lw1, lu1, lv1, _, _ = self._rhs(s1, forcing)
        s2 = self._clean(SWEState(
            0.5 * (s.w + s1.w + dt * lw1),
            0.5 * (s.hu + s1.hu + dt * lu1),
            0.5 * (s.hv + s1.hv + dt * lv1),
            s.t + dt,
        ))
        return self._split_sources(s2, dt, forcing)

    def _clean(self, s: SWEState) -> SWEState:
        """반올림 오차 수준 음수 수심 제거 + 건조 셀 운동량 소거."""
        h = s.w - self.bc_cell
        w = np.where(h < 0.0, self.bc_cell, s.w)
        dry = h <= 0.0
        return SWEState(w, np.where(dry, 0.0, s.hu), np.where(dry, 0.0, s.hv), s.t)

    def _split_sources(self, s: SWEState, dt: float,
                       forcing: AtmosForcing | None = None) -> SWEState:
        has_wind = forcing is not None and (
            np.any(np.asarray(forcing.taux) != 0.0)
            or np.any(np.asarray(forcing.tauy) != 0.0))
        if np.all(np.asarray(self.f) == 0.0) and self.cf == 0.0 and not has_wind:
            return s
        h = self.depth(s)
        u = desingularized_velocity(h, s.hu, self.h_dry)
        v = desingularized_velocity(h, s.hv, self.h_dry)
        if has_wind:
            assert forcing is not None
            h_eff = np.maximum(h, 10.0 * self.h_dry)   # 극박수층 가속 폭주 방지
            wet = h > self.h_dry
            u = u + dt * np.where(wet, np.asarray(forcing.taux) / (self.rho * h_eff), 0.0)
            v = v + dt * np.where(wet, np.asarray(forcing.tauy) / (self.rho * h_eff), 0.0)
        if np.any(np.asarray(self.f) != 0.0):
            u, v = coriolis_rotate(u, v, self.f, dt)
        if self.cf > 0.0:
            u, v = friction_semi_implicit(u, v, h, self.cf, dt, self.h_dry)
        return replace(s, hu=h * u, hv=h * v)

    def run(self, s: SWEState, t_end: float, max_steps: int = 2_000_000,
            forcing: AtmosForcing | None = None) -> SWEState:
        for _ in range(max_steps):
            if s.t >= t_end - 1e-12:
                return s
            s = self.step(s, dt_max=t_end - s.t, forcing=forcing)
            if not np.isfinite(s.w).all():
                raise FloatingPointError(f"solver diverged at t={s.t:.3f}")
        raise RuntimeError("max_steps exceeded")
