"""1D 페치 파랑 솔버 — M5 검증(JONSWAP 성장곡선)용 최소 지리 전파 + 전체 소스항.

∂E/∂t + ∂(c_g cosθ · E)/∂x = S_in + S_ds + S_nl

- 전파: 1차 상향(upwind), 심수 군속도. 해안(x=0) 유입 0, 외해 측 유출.
- 소스 적분: WAM 준음해(semi-implicit) + WAMDI 성장 제한자 + 진단 꼬리.
2D 구면 전파는 Phase 6에서 동일 소스항을 재사용해 확장한다.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from poseidon.engines.spectral_wave.grid import SpectralGrid
from poseidon.engines.spectral_wave.sources import (
    DIA,
    apply_tail_and_limits,
    dissipation,
    growth_limiter,
    seed_spectrum,
    wind_input,
)
from poseidon.physics.constants import G_STANDARD
from poseidon.physics.waves import deep_group_velocity

Arr = NDArray[np.float64]

# ── POSEIDON-WAM3L 보정 (2026-08-04, M5 페치 실험으로 확정) ─────────────
# 문헌 범위 내 보정: Cds ∈ [1.7, 3.3]×10⁻⁵ (Komen 84 / SWAN), δ=1 (SWAN 40.91+),
# 성장 제한자는 WAM cycle 3 전통대로 운영 설정의 일부다 (Tolman 1992 참조).
# 검증 성적은 docs/PHASE5_WAVE_ENGINE.md §5.
CAL_CDS = 2.0e-5
CAL_DELTA = 1.0
CAL_LIMITER_FRAC = 0.134


class Fetch1DSolver:
    def __init__(self, grid: SpectralGrid, nx: int, dx: float, *,
                 u10: float, theta_wind: float = 0.0, g: float = G_STANDARD,
                 cds: float | None = CAL_CDS, delta: float = CAL_DELTA,
                 limiter_frac: float = CAL_LIMITER_FRAC) -> None:
        self.grid, self.nx, self.dx = grid, nx, dx
        self.u10, self.theta_wind, self.g = u10, theta_wind, g
        self.cds, self.delta = cds, delta
        self.limiter_frac = limiter_frac
        cg = deep_group_velocity(grid.f, g)
        self.cgx = cg[:, None] * np.cos(grid.theta)[None, :]     # (nf, nθ)
        self.dia = DIA(grid)
        self.x = (np.arange(nx) + 0.5) * dx

    def initial_state(self) -> Arr:
        e0 = seed_spectrum(self.grid, self.u10, self.theta_wind, self.g)
        return np.broadcast_to(e0, (self.nx, *e0.shape)).copy()

    def cfl_dt(self, cfl: float = 0.7) -> float:
        return cfl * self.dx / float(np.abs(self.cgx).max())

    def _advect(self, e: Arr, dt: float) -> Arr:
        cp = np.maximum(self.cgx, 0.0)
        cm = np.minimum(self.cgx, 0.0)
        up = np.empty_like(e)
        up[0] = 0.0                                    # 해안(x=0): 유입 파랑 없음
        up[1:] = e[:-1]
        dn = np.empty_like(e)
        dn[-1] = e[-1]                                 # 외해 측: 구배 0 유출
        dn[:-1] = e[1:]
        flux_div = cp * (e - up) / self.dx + cm * (dn - e) / self.dx
        return e - dt * flux_div

    def _sources(self, e: Arr, dt: float) -> Arr:
        kw: dict = {"delta": self.delta}
        if self.cds is not None:
            kw["cds"] = self.cds
        s = (wind_input(e, self.grid, self.u10, self.theta_wind, self.g)
             + dissipation(e, self.grid, self.g, **kw)
             + self.dia(e, self.g))
        # 준음해: 감쇠 성분만 음해 처리 (WAM 방식의 대각 근사)
        d = np.where(e > 1e-30, s / np.maximum(e, 1e-30), 0.0)
        damp = np.maximum(-d, 0.0)
        de = s * dt / (1.0 + damp * dt)
        lim = growth_limiter(self.grid, dt, self.g, frac=self.limiter_frac)
        de = np.clip(de, -lim, lim)
        e2 = np.maximum(e + de, 0.0)
        return apply_tail_and_limits(e2, self.grid, self.g)

    def step(self, e: Arr, dt: float) -> Arr:
        e = self._advect(e, dt)
        e = np.maximum(e, 0.0)
        return self._sources(e, dt)

    def run(self, hours: float, dt: float | None = None,
            e: Arr | None = None) -> Arr:
        dt = dt or self.cfl_dt()
        e = self.initial_state() if e is None else e
        n = int(round(hours * 3600.0 / dt))
        for _ in range(n):
            e = self.step(e, dt)
            if not np.isfinite(e).all():
                raise FloatingPointError("wave solver diverged")
        return e
