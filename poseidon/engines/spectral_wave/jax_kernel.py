"""지역 파랑 엔진의 JAX 커널 (Phase 9, ADR-001 2단계).

NumPy 참조 구현(regional.py)과 동일한 물리·수치를 XLA로 컴파일한다.
- 전 상태 fp32 (참조 구현은 소스항을 fp64로 계산 — 동등성 게이트는 그 차이를 감안해
  5스텝 Hs 상대오차 < 2×10⁻³ 로 설정, tests/scientific/test_jax_equivalence.py)
- 정적 배열(수심 계수·굴절 속도·DIA 이동량)은 초기화 시 고정, jit 인자는 (E, dt, 바람, 경계)만
- CPU에서도 XLA 융합으로 가속되며, GPU(CUDA/Metal) 장착 시 코드 변경 없이 이관된다.
"""

from __future__ import annotations

import math
from functools import partial

import jax
import jax.numpy as jnp
import numpy as np

from poseidon.engines.spectral_wave.fetch1d import (
    CAL_CDS,
    CAL_DELTA,
    CAL_LIMITER_FRAC,
)
from poseidon.engines.spectral_wave.regional import (
    RegionalWaveModel,
    _uno2_sweep,
    gse_average,
)
from poseidon.engines.spectral_wave.sources import (
    C_DIA,
    LAMBDA_DIA,
    S_PM2,
    drag_coefficient,
    friction_velocity,
    swell_damping_rate,
)
from poseidon.physics.constants import G_STANDARD, RHO_AIR, RHO_SEAWATER


class JaxRegionalKernel:
    """RegionalWaveModel의 스텝을 jit 컴파일. 사용: k = JaxRegionalKernel(model); e = k.step(e, dt, u10, udir)."""

    def __init__(self, m: RegionalWaveModel) -> None:
        g = m.grid
        f32 = lambda a: jnp.asarray(a, dtype=jnp.float32)  # noqa: E731
        self.g = m.g
        self.grid = m.grid
        self.enable = m.enable
        # NumPy 참조 경로와 **같은 진단 스위치**를 받아야 한다. 운영은 JAX를 쓰므로
        # 여기 반영하지 않으면 스위치가 조용히 무시된다.
        self.negative_input = bool(getattr(m, "negative_input", False))
        self.drag = str(getattr(m, "drag", "wu1982"))
        self.swell = str(getattr(m, "swell", "none"))
        self.swell_par = getattr(m, "swell_par", None)
        self.k_deep = getattr(m, "k_deep", None)
        self.st = {
            "f": f32(g.f), "df": f32(g.df), "theta": f32(g.theta),
            "dtheta": float(g.dtheta), "nf": g.nf, "nth": g.ntheta,
            "cg": f32(m.cg), "kh": f32(m.kh), "cph": f32(m.c_phase),
            "depth": f32(m.depth), "sea": jnp.asarray(m.sea),
            "sea4": f32(m.sea[..., None, None]),
            "ctheta": f32(m.ctheta), "ct_max": float(m.ctheta_max),
            "dx": f32(m.dx), "dy": float(m.dy), "cosphi": f32(m.cosphi),
            "cos_t": f32(np.cos(g.theta)), "sin_t": f32(np.sin(g.theta)),
            # UNO2 정적 마스크 — NumPy 참조와 **동일 배열**을 공유 (동등성 보장)
            "ok_x": jnp.asarray(m.ok_x), "ok_y": jnp.asarray(m.ok_y),
        }
        self.advection, self.gse = m.advection, m.gse
        self._model = m
        self._dt_hint = 600.0            # prepare(dt)로 재설정 (θ 서브사이클 수 고정)
        lnr = math.log(g.ratio)
        self.dia_off = {
            "dp": math.log(1 + LAMBDA_DIA) / lnr, "dm": math.log(1 - LAMBDA_DIA) / lnr,
            "o3": math.radians(11.48) / g.dtheta, "o4": -math.radians(33.56) / g.dtheta,
        }
        self._step = jax.jit(self._build_step())

    # ── 격자 이동 (DIA) ───────────────────────────────────────────
    def _shift_freq(self, a, s: int, gather: bool, tail_pow: float = 5.0):
        nf = self.st["nf"]
        ratio_decay = float(np.exp(-tail_pow * math.log(1.1)))
        if gather:
            if s >= 0:
                main = a[..., s:, :]
                tails = [a[..., -1:, :] * ratio_decay ** (m + s - (nf - 1))
                         for m in range(nf - s, nf)]
                return jnp.concatenate([main] + tails, axis=-2) if tails else main
            pad = jnp.zeros_like(a[..., :(-s), :])
            return jnp.concatenate([pad, a[..., : nf + s, :]], axis=-2)
        if s >= 0:
            pad = jnp.zeros_like(a[..., :s, :]) if s else None
            out = a[..., : nf - s, :]
            return jnp.concatenate([pad, out], axis=-2) if s else out
        out = a[..., -s:, :]
        return jnp.concatenate([out, jnp.zeros_like(a[..., : (-s), :])], axis=-2)

    def _shift(self, a, di: float, dj: float, gather: bool):
        i0, wi = math.floor(di), di - math.floor(di)
        j0, wj = math.floor(dj), dj - math.floor(dj)
        out = 0.0
        for ai, wa in ((0, 1 - wi), (1, wi)):
            for bj, wb in ((0, 1 - wj), (1, wj)):
                w = wa * wb
                if w == 0.0:
                    continue
                part = self._shift_freq(a, i0 + ai, gather)
                out = out + w * jnp.roll(part, (-(j0 + bj) if gather else (j0 + bj)),
                                         axis=-1)
        return out

    # ── 스텝 빌더 ─────────────────────────────────────────────────
    def _build_step(self):
        st, g_acc = self.st, self.g
        dtheta, nf = st["dtheta"], st["nf"]
        dp, dm, o3, o4 = (self.dia_off[k] for k in ("dp", "dm", "o3", "o4"))
        enable = self.enable

        # GSE 가중치는 dt 의존 → θ 서브사이클 수와 같은 규약으로 _dt_hint 에 고정.
        # prepare(model, dt) 를 호출하면 재빌드된다.
        w_gse = (jnp.asarray(self._model.gse_weights(self._dt_hint))
                 if self.gse == "tolman" else None)
        uno2 = self.advection == "uno2"

        def advect(e, dt):
            cgx = st["cg"][..., None] * st["cos_t"]
            cgy = st["cg"][..., None] * st["sin_t"]
            w = st["cosphi"][..., None, None]
            if uno2:
                # NumPy 참조(regional._advect)와 **동일 함수·동일 산술 순서**
                crx = cgx * (dt / st["dx"][..., None, None])
                e = _uno2_sweep(e, crx, 1, st["ok_x"], jnp)
                cry = cgy * (dt / st["dy"])
                e = _uno2_sweep(e * w, cry, 0, st["ok_y"], jnp) / w
            else:
                fp = jnp.maximum(cgx, 0) * e
                fm = jnp.minimum(cgx, 0) * e
                div = (fp - jnp.concatenate([jnp.zeros_like(fp[:, :1]), fp[:, :-1]], 1)
                       + jnp.concatenate([fm[:, 1:], jnp.zeros_like(fm[:, :1])], 1) - fm)
                e = e - (dt / st["dx"][..., None, None]) * div
                fp = jnp.maximum(cgy, 0) * e * w
                fm = jnp.minimum(cgy, 0) * e * w
                div = (fp - jnp.concatenate([jnp.zeros_like(fp[:1]), fp[:-1]], 0)
                       + jnp.concatenate([fm[1:], jnp.zeros_like(fm[:1])], 0) - fm)
                e = e - (dt / (st["dy"] * w)) * div
            # θ 서브사이클 — fori_loop로 그래프 크기 고정 (unroll 시 컴파일 폭발)
            n_sub = max(1, int(np.ceil(self._dt_hint * st["ct_max"] / (0.7 * dtheta))))
            dts = dt / n_sub
            ct = st["ctheta"]

            def theta_body(_, ee):
                fp = jnp.maximum(ct, 0) * ee
                fm = jnp.minimum(ct, 0) * ee
                div = fp - jnp.roll(fp, 1, -1) + jnp.roll(fm, -1, -1) - fm
                return ee - (dts / dtheta) * div

            e = jax.lax.fori_loop(0, n_sub, theta_body, e)
            e = jnp.maximum(e, 0.0)
            if w_gse is not None:
                e = gse_average(e, w_gse, w, st["sea4"], jnp)
            return e

        def mean_sigma_k(e):
            sig = 2 * jnp.pi * st["f"]
            k = sig ** 2 / self.g
            w = st["df"][:, None] * dtheta
            etot = jnp.maximum((e * w).sum((-2, -1)), 1e-30)
            sig_m = etot / jnp.maximum((e / sig[:, None] * w).sum((-2, -1)), 1e-30)
            rk = (e / jnp.sqrt(k)[:, None] * w).sum((-2, -1)) / etot
            # fp32 보호: E≈0 셀에서 k̃=rk⁻²가 inf로 넘쳐 inf×0=NaN 발생 (fp64 참조는 무해).
            # 물리 범위로 클램프 — 활성 셀 결과는 불변, 공셀 진단값만 유한화.
            sig_m = jnp.clip(sig_m, 1e-3, 60.0)
            k_m = jnp.clip(1.0 / jnp.maximum(rk, 1e-30) ** 2, 1e-7, 5.0)
            return etot, sig_m, k_m

        def sources(e, dt, u10, udir):
            sig = 2 * jnp.pi * st["f"]
            s = jnp.zeros_like(e)
            etot, sig_m, k_m = mean_sigma_k(e)
            if "wind" in enable:
                # Wu 식을 여기 인라인으로 복제해 두면 sources.py 만 고쳤을 때
                # 조용히 옛 식으로 남는다. 공유 함수를 xp=jnp 로 호출한다.
                ustar = friction_velocity(
                    u10, self.drag, xp=jnp)[..., None, None]
                cosd = jnp.cos(st["theta"][None, :] - udir[..., None, None])
                beta = 0.25 * (RHO_AIR / RHO_SEAWATER) * (
                    28.0 * ustar / st["cph"][..., :, None] * cosd - 1.0)
                bpos = beta if self.negative_input else jnp.maximum(beta, 0.0)
                s += bpos * sig[:, None] * e
                if self.swell != "none":
                    # NumPy 참조와 **같은 함수**를 xp=jnp 로 호출한다. 소스항을
                    # 두 벌 구현하면 한쪽만 고쳐져 조용히 무시된다(실측 이력).
                    s += swell_damping_rate(
                        e, self.grid, k=jnp.asarray(self.k_deep),
                        par=self.swell_par, beta=beta,
                        ustar=ustar[..., 0, 0], theta_wind=udir,
                        g=self.g, etot=etot, xp=jnp) * e
            if "ds" in enable:
                s2 = etot * k_m ** 2
                k = (sig ** 2 / self.g)
                kr = k[:, None] / jnp.maximum(k_m[..., None, None], 1e-30)
                gam = (CAL_CDS * sig_m * (s2 / S_PM2) ** 2)[..., None, None] \
                    * kr * ((1 - CAL_DELTA) + CAL_DELTA * kr)
                s -= gam * e
            if "nl" in enable:
                x = jnp.maximum(0.75 * k_m * st["depth"], 0.15)
                r = jnp.clip(1 + (5.5 / x) * (1 - 5 * x / 6) * jnp.exp(-1.25 * x),
                             0.2, 5.0)[..., None, None]
                snl = 0.0
                f_ = st["f"][:, None]
                for mir in (1.0, -1.0):
                    e3 = self._shift(e, dp, mir * o3, True)
                    e4 = self._shift(e, dm, mir * o4, True)
                    rate = (C_DIA / self.g ** 4) * f_ ** 11 * (
                        e * e * (e3 / (1 + LAMBDA_DIA) ** 4 + e4 / (1 - LAMBDA_DIA) ** 4)
                        - 2 * e * e3 * e4 / (1 - LAMBDA_DIA ** 2) ** 4)
                    snl = snl - 2 * rate \
                        + self._shift(rate, dp, mir * o3, False) \
                        + self._shift(rate, dm, mir * o4, False)
                s += r * snl
            if "bot" in enable:
                sh = jnp.sinh(jnp.clip(st["kh"], 1e-6, 25.0)) ** 2
                s -= (0.038 * (sig ** 2)[:, None] / (self.g ** 2 * sh[..., :, None])) * e
            if "brk" in enable:
                m0 = jnp.maximum(etot, 1e-12)
                hm = 0.73 * jnp.maximum(st["depth"], 0.01)
                b = hm ** 2 / (8 * m0)
                qb = jnp.where(b < 0.3, 1.0, 0.0)
                qb = jax.lax.fori_loop(0, 40, lambda _, q: jnp.exp(-b * (1 - q)), qb)
                qb = jnp.where(b > 5.0, 0.0, qb)
                dtot = 0.25 * qb * (sig_m / (2 * jnp.pi)) * hm ** 2
                s -= (dtot / m0)[..., None, None] * e

            d = jnp.where(e > 1e-30, s / jnp.maximum(e, 1e-30), 0.0)
            de = s * dt / (1 + jnp.maximum(-d, 0.0) * dt)
            lim = (CAL_LIMITER_FRAC * 0.0081 * self.g ** 2 * (2 * jnp.pi) ** -4
                   * st["f"] ** -5 / (2 * jnp.pi) * (dt / 300.0))[:, None]
            e2 = jnp.maximum(e + jnp.clip(de, -lim, lim), 0.0)

            # 진단 꼬리 f^-5 (f > 2.5 f̃)
            _, sig_m2, _ = mean_sigma_k(e2)
            f_hf = 2.5 * jnp.maximum(sig_m2 / (2 * jnp.pi), 1e-6)
            idx = jnp.clip(jnp.searchsorted(st["f"], f_hf), 1, nf - 1)
            anchor = jnp.take_along_axis(
                e2, (idx - 1)[..., None, None].repeat(e2.shape[-1], -1), axis=-2)
            ratio = (st["f"][None, None, :] / st["f"][idx - 1][..., None]) ** -5.0
            mask = jnp.arange(nf)[None, None, :] >= idx[..., None]
            return jnp.where(mask[..., None], anchor * ratio[..., None], e2)

        def step(e, dt, u10, udir, e_bc, ring):
            e = advect(e, dt)
            e = jnp.where(ring[..., None, None], e_bc, e)
            e = jnp.where(st["sea"][..., None, None], e, 0.0)
            e = sources(e, dt, u10, udir)
            e = jnp.where(ring[..., None, None], e_bc, e)
            e = jnp.where(st["sea"][..., None, None], e, 0.0)
            return e

        return step

    # ── 공개 API ──────────────────────────────────────────────────
    def prepare(self, model: RegionalWaveModel, dt: float) -> None:
        """θ 서브사이클 수를 dt에 맞춰 고정 (재-트레이스 방지를 위해 스텝 전 1회)."""
        self._dt_hint = dt
        self._model = model
        self.advection, self.gse = model.advection, model.gse
        self._step = jax.jit(self._build_step())

    def step(self, e, dt, u10, udir, e_bc, ring):
        return self._step(jnp.asarray(e, jnp.float32), jnp.float32(dt),
                          jnp.asarray(u10, jnp.float32), jnp.asarray(udir, jnp.float32),
                          jnp.asarray(e_bc, jnp.float32), jnp.asarray(ring))


def ring_mask(ny: int, nx: int) -> np.ndarray:
    m = np.zeros((ny, nx), dtype=bool)
    m[0, :] = m[-1, :] = m[:, 0] = m[:, -1] = True
    return m
