"""2D 구면 지역 파랑 모델 — WAE 전파(위경도 격자) + 전체 소스항 (M6 핵심).

∂E/∂t + (전파: 지리 x·y + 방향 θ) = S_in + S_ds + S_nl·R(kh) + S_bot + S_db

- 지리 전파: 유한수심 군속도, 보존형 플럭스, 구면 메트릭 cos φ
             advection="upwind" 1차 도너셀 / "uno2" 2차 비진동(Li 2008, WW3 UNO)
- GSE 완화: gse="tolman" 시 Tolman(2002) 공간평균 3×3 스텐실 (WW3 PR3 등가)
- 방향 전파: 수심 굴절 θ̇ = (σ/sinh 2kh)(sinθ ∂h/∂x − cosθ ∂h/∂y)
             + 대권 전환 θ̇ = −(c_g/R) cosθ tanφ,  1차 상향(주기 경계)
- σ축 이동 없음: 정상 수심·무해류에서 σ는 파선을 따라 보존 (해류 결합 시 도입)
- 소스항: sources.py 재사용 (Phase 5 보정 유지) + 유한수심 3종
- 경계: 가장자리 링을 경계 스펙트럼으로 고정(Dirichlet), 육지 흡수(E=0)

방향 규약: θ=0 → 동쪽으로 진행, θ=π/2 → 북쪽으로 진행 (수학 규약, CCW).
상태 배열: E(ny, nx, nf, nθ), 기본 dtype float32 (메모리 절반).
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from poseidon.engines.spectral_wave.fetch1d import (
    CAL_CDS,
    CAL_DELTA,
    CAL_LIMITER_FRAC,
)
from poseidon.engines.spectral_wave.grid import SpectralGrid
from poseidon.engines.spectral_wave.sources import (
    DIA,
    apply_tail_and_limits,
    bottom_friction,
    depth_breaking,
    dia_depth_factor,
    dissipation,
    growth_limiter,
    SwellPar,
    friction_velocity,
    swell_damping_rate,
    wind_input,
)
from poseidon.physics.constants import G_STANDARD, R_EARTH
from poseidon.physics.waves import group_velocity, jonswap_spectrum, wavenumber

Arr = NDArray

GSE_GAMMA_DEFAULT = 1.5   # WW3 WDTHCG = WDTHTH = 1.5 (model/src/w3gridmd.F90 L2634)


# ══════════════════════════════════════════════════════════════════
# 이류 커널 — NumPy/JAX 공용 (xp = np 또는 jnp)
#
# 두 백엔드가 **같은 함수, 같은 산술 순서**를 쓰도록 xp 인자로 일반화한다.
# tests/scientific/test_jax_equivalence.py 가 이 동등성을 게이트한다.
# ══════════════════════════════════════════════════════════════════
def _shift(a, s: int, axis: int, xp=np):
    """0-패딩 이동: 결과[i] = a[i−s] (범위 밖은 0). s>0 이면 내용이 +방향으로 이동."""
    if s == 0:
        return a
    n = a.shape[axis]
    if abs(s) >= n:
        return xp.zeros_like(a)
    sl = [slice(None)] * a.ndim
    if s > 0:
        sl[axis] = slice(0, s)
        pad = xp.zeros_like(a[tuple(sl)])
        sl[axis] = slice(0, n - s)
        return xp.concatenate([pad, a[tuple(sl)]], axis=axis)
    sl[axis] = slice(-s, n)
    body = a[tuple(sl)]
    sl[axis] = slice(0, -s)
    return xp.concatenate([body, xp.zeros_like(a[tuple(sl)])], axis=axis)


def _next_repeat(a, axis: int, xp=np):
    """결과[i] = a[i+1], 마지막 원소만 복제 — 최외곽 면 Courant 정의용."""
    n = a.shape[axis]
    sl = [slice(None)] * a.ndim
    sl[axis] = slice(1, n)
    body = a[tuple(sl)]
    sl[axis] = slice(n - 1, n)
    return xp.concatenate([body, a[tuple(sl)]], axis=axis)


def _uno2_flux(q, cr, axis: int, ok, xp=np):
    """UNO2 면 플럭스 F[i] = F_{i+1/2} (무차원 Ĉ·q_face).

    Li, J.-G. (2008) *Upstream Nonoscillatory Advection Schemes*, Mon. Wea. Rev.
    136, 4709–4729 — WW3 `model/src/w3uno2md.F90` subroutine W3UNO2r 정규격자형:

        Ĉ_{i+1/2} = ½(Cr_i + Cr_{i+1})                       (WW3: CFL)
        C = 상류셀 (Ĉ ≥ 0 → i, 그 외 i+1),  D = C + sgn(Ĉ)   (WW3: IXYC/IXYD)
        s      = sgn(q_D − q_C)·min(|q_{C+1} − q_C|, |q_C − q_{C−1}|)
        q_face = q_C + ½(1 − |Ĉ|)·s                           (WW3: QB)

    min-modulus 제한자가 TVD·양수보존을 보장한다 (WW3 주석: "This positive filter
    is not necessary for UNO2 scheme but kept here"). |Ĉ|→1 에서 s 항이 소멸해
    1차 상향으로 축퇴하므로 안정조건은 1차와 동일한 |Ĉ| ≤ 1.

    ok=False 인 면(도메인 경계·육지 인접 = 4점 스텐실 미확보)은 1차 상향으로
    강등한다 — WW3도 동일 ("Fluxes boundary point ... 1st order without limiter").
    """
    qm1 = _shift(q, 1, axis, xp)
    qp1 = _shift(q, -1, axis, xp)
    qp2 = _shift(q, -2, axis, xp)
    crf = 0.5 * (cr + _next_repeat(cr, axis, xp))
    pos = crf >= 0.0                       # NumPy/JAX 동일 술어 (>= 로 고정)
    qc = xp.where(pos, q, qp1)
    qcm = xp.where(pos, qm1, q)
    qcp = xp.where(pos, qp1, qp2)
    qd = xp.where(pos, qcp, qcm)
    # Fortran SIGN(0.5,0.)=+0.5 ≠ np.sign(0)=0 이지만 q_D=q_C 이면 min 항도 0 이라 무해.
    slope = xp.sign(qd - qc) * xp.minimum(xp.abs(qcp - qc), xp.abs(qc - qcm))
    f2 = crf * (qc + 0.5 * (1.0 - xp.abs(crf)) * slope)
    f1 = crf * qc
    return xp.where(ok, f2, f1)


def _uno2_sweep(q, cr, axis: int, ok, xp=np):
    """1축 UNO2 플럭스형 갱신. 경계 규약은 현행 도너셀과 동일(유입 0 / 유출 자유)."""
    f = _uno2_flux(q, cr, axis, ok, xp)
    sl = [slice(None)] * q.ndim
    sl[axis] = slice(0, 1)
    # 최외곽(하단/서쪽) 면 F_{−1/2}: 도메인 밖 유입 0, 역방향 유출은 허용
    f_edge = xp.minimum(cr[tuple(sl)], 0.0) * q[tuple(sl)]
    sl[axis] = slice(0, -1)
    fm = xp.concatenate([f_edge, f[tuple(sl)]], axis=axis)
    # WW3 W3UNO2 와 동일한 안전망: 발산 속도장·차원분할에서 엄밀 TVD가 자동
    # 보장되지 않으므로 클립을 남긴다 (등속 1D 에서는 발동하지 않음).
    return xp.maximum(q + (fm - f), 0.0)


def _face_ok(sea, axis: int):
    """2차 재구성 4점 스텐실(i−1,i,i+1,i+2)이 전부 바다·도메인 내부인 면."""
    return (_shift(sea, 1, axis) & sea & _shift(sea, -1, axis)
            & _shift(sea, -2, axis))


def gse_stencil(dx_row, dy, f, ratio, theta, dtheta, g, dt, gamma,
                gamma_cg=None):
    """Tolman(2002) 평균화 GSE 완화 가중치 W[ny, nf, nθ, 3, 3] (WW3 PR3 등가).

    Tolman, H.L. (2002) *Alleviating the Garden Sprinkler Effect in wind wave
    models*, Ocean Modelling 4, 269–289 / WW3 `w3pro3md.F90` W3XYP3 §1.c–3.a:

        Δn = γ_th · c_g Δθ Δt          (광선 법선 — 방향 이산화 보상)
        Δs = γ_cg · ½(r − 1/r) c_g Δt  (광선 방향 — 주파수 이산화 보상)
        네 모서리 r = ±Δs ŝ ± Δn n̂ ,  ŝ=(cosθ,sinθ), n̂=(−sinθ,cosθ)
        W(center) += 1/3,  각 모서리에 1/6 을 겹선형 배분
        (WW3: VQ(IXY)*(3−RD2−RD4)/3 + RD2·RD1/6 + … 와 동일한 볼록결합)

    W ≥ 0, Σ W = 1 이므로 무조건 단조·양수보존이고 시간스텝 제약이 없다
    (WW3 매뉴얼 §3.4.6: 평균화는 "never influences the time step").
    오프셋은 WW3 `RD2 = MIN(1., RDI2*CG)` 와 같이 1셀로 클립(방향 유지)한다.

    ⚠ 실측 주의 — 이산 겹선형 산포의 보간확산.
    오프셋이 1셀보다 훨씬 작으면(운영 L1 에서 Δn/Δy ≈ 0.066, Δs/Δx ≈ 0.044)
    질량의 |p| 만큼이 **한 셀 통째로** 옮겨가므로 실효 2차모멘트가 연속
    top-hat 값 Δ²/(3Δt) 이 아니라 Δ·Δx/(3Δt) 가 된다(직접 측정: L1 f=0.10 에서
    8,648 vs 이론 383 m²/s, 이산식 예측 8,705 과 일치). 즉 실효 확산은
        D_nn ≈ γ_th·c_g·Δθ·Δy/3 ,  D_ss ≈ γ_cg·½(r−1/r)·c_g·Δx/3
    으로 **Δt 에 무관하고 격자간격에 비례**한다. GSE 간극을 메우는 것은 D_nn 이고,
    D_ss 는 전파방향 확산을 되돌리므로 γ_cg 를 따로 낮출 수 있게 분리했다
    (WW3 도 WDTHCG/WDTHTH 를 별도 입력으로 둔다).
    """
    ny, nf, nth = len(dx_row), len(f), len(theta)
    cg = g / (4.0 * np.pi * np.asarray(f, dtype=np.float64))     # 심해 c_g (nf,)
    g_cg = gamma if gamma_cg is None else gamma_cg
    ds = g_cg * 0.5 * (ratio - 1.0 / ratio) * cg * dt            # (nf,) [m]
    dn = gamma * cg * dtheta * dt                                # (nf,) [m]
    ct, st = np.cos(theta), np.sin(theta)
    dxa = np.asarray(dx_row, dtype=np.float64).reshape(ny, 1, 1)
    w = np.zeros((ny, nf, nth, 3, 3))
    w[..., 1, 1] = 1.0 / 3.0
    for sg in (1.0, -1.0):
        for sn in (1.0, -1.0):
            rx = sg * ds[:, None] * ct - sn * dn[:, None] * st   # (nf,nθ) [m]
            ry = sg * ds[:, None] * st + sn * dn[:, None] * ct
            px = np.broadcast_to(rx[None] / dxa, (ny, nf, nth))  # 열(경도) 격자단위
            qy = np.broadcast_to(ry[None] / dy, (ny, nf, nth))   # 행(위도) 격자단위
            scale = 1.0 / np.maximum(1.0, np.maximum(np.abs(px), np.abs(qy)))
            px, qy = px * scale, qy * scale
            i0, j0 = np.floor(px), np.floor(qy)
            fx, fy = px - i0, qy - j0
            for ai, wa in ((0, 1.0 - fx), (1, fx)):
                for bj, wb in ((0, 1.0 - fy), (1, fy)):
                    wgt = wa * wb / 6.0
                    di = (i0 + ai).astype(np.int64) + 1
                    dj = (j0 + bj).astype(np.int64) + 1
                    for a in range(3):
                        for b in range(3):
                            w[..., a, b] += np.where((dj == a) & (di == b), wgt, 0.0)
    return w


def gse_average(e, w, cosphi, sea4=None, xp=np):
    """3×3 볼록결합 평균. scatter 형(가중치를 먼저 곱한 뒤 이동) → 질량 엄밀 보존.

    보존량은 E 가 아니라 셀면적 가중 E·cos φ 다. W 가 위도행에 의존하므로
    gather 형(이동 후 곱)으로 쓰면 O(∂W/∂φ) 질량오차가 매 스텝 누적된다.

    **육지 마스크 필수 (sea4).** 마스크 없이 산포하면 육지로 간 몫을 직후의
    `e[~sea]=0` 이 소각해 해안이 일방향 에너지 싱크가 된다 — 실측 해안 첫 셀
    Hs −34~−47%. 육지·도메인 밖으로 갈 몫은 중심 셀에 환원(keep)해 ΣW=1(바다 위)을
    유지한다. 이래야 볼록결합(단조·양수·질량보존)이 그대로 성립한다.
    sea4=None 이면 전-바다로 간주 (이상화 실험 전용).
    """
    m = e * cosphi
    out = None
    keep = None
    for a in range(3):
        row = None
        for b in range(3):
            wab = w[..., a, b]
            if sea4 is None:
                t = _shift(m * wab, b - 1, 1, xp)
            else:
                # 목적지가 바다인지: 마스크를 이동량의 반대로 옮겨 확인
                dst = _shift(_shift(sea4, -(b - 1), 1, xp), -(a - 1), 0, xp)
                k = wab * (1.0 - dst)                    # 육지·밖으로 갈 몫
                keep = k if keep is None else keep + k
                t = _shift(m * wab * dst, b - 1, 1, xp)
            row = t if row is None else row + t
        row = _shift(row, a - 1, 0, xp)
        out = row if out is None else out + row
    if keep is not None:
        out = out + m * keep                             # 중심 환원
    return out / cosphi


class RegionalWaveModel:
    def __init__(self, lats: Arr, lons: Arr, depth: Arr, grid: SpectralGrid, *,
                 g: float = G_STANDARD, h_min: float = 2.0,
                 enable: tuple[str, ...] = ("wind", "ds", "nl", "bot", "brk"),
                 negative_input: bool = False,
                 drag: str = "wu1982",
                 swell: str = "none",
                 swell_par: "SwellPar | None" = None,
                 advection: str = "uno2", gse: str = "none",
                 gse_gamma: float = GSE_GAMMA_DEFAULT,
                 gse_gamma_cg: float | None = None,
                 dtype=np.float32) -> None:
        """advection: "uno2" 2차 비진동(기본) | "upwind" 1차 도너셀(회귀 복원용).
        gse: "none"(기본) | "tolman" Tolman(2002) 평균화.

        **[동결 2026-08-23] 기본값 "none" 확정 근거** — 육지 마스크 수정 후 재측정.
        이상화 점소스 스웰(0.07 Hz, 단일 방향빈, 48 h, L1 0.25°) γ 스윕:
        | 구성 | 진동지수 | 첨두 Hs | 폭 |
        |---|---|---|---|
        | none | 3.11 | 0.601 m | 22셀 |
        | γ=0.4 | 0.78 (−75%) | 0.459 (**−24%**) | 28셀 |
        | γ=0.75 | 0.55 (−82%) | 0.405 (**−33%**) | 31셀 |
        | γ=1.5 (WW3) | 0.36 (−89%) | 0.352 (**−42%**) | 36셀 |
        방향빈 36→72로 늘리면 none의 진동이 3.11→2.28(−27%) — GSE 아티팩트는 실재하나
        진동의 27%만 방향 이산화 기인이고, 어떤 γ에서도 첨두 손실이 24% 이상이다.
        운영 구성에서는 경계를 매시 GFS-Wave(0.25°)로 재클램프하므로 GSE가 발현할
        좁은 방향 구조가 애초에 적다(실사이클 |∇Hs| 변화 +0.5%, 이상화 대비 미미).
        게다가 3×3 클립으로 실효 확산이 D ∝ c_g·Δx/3 (격자간격 비례)라 이 승급의
        목적인 해상도 독립성과 충돌한다.
        → 장기 리드·스웰 지배 사례에서 재평가하되, 켤 경우 γ=0.4~0.75를 권장한다.
        gse_gamma: 광선법선 평균폭 계수 (WW3 WDTHTH, 기본 1.5) — GSE 완화 본체.
        gse_gamma_cg: 광선방향 평균폭 계수 (WW3 WDTHCG); None 이면 gse_gamma 와 동일.
        """
        if advection not in ("upwind", "uno2"):
            raise ValueError(f"advection must be 'upwind' or 'uno2', got {advection!r}")
        if gse not in ("none", "tolman"):
            raise ValueError(f"gse must be 'none' or 'tolman', got {gse!r}")
        self.advection, self.gse = advection, gse
        self.gse_gamma = float(gse_gamma)
        self.gse_gamma_cg = (float(gse_gamma) if gse_gamma_cg is None
                             else float(gse_gamma_cg))
        self._gse_cache: tuple[float, Arr] | None = None
        self.grid, self.g, self.dtype, self.enable = grid, g, dtype, enable
        # 진단 전용 — 기본 False 는 WAM3 정식(음의 바람입력 절단)과 동일하다
        self.negative_input = bool(negative_input)
        # 항력계수 스킴. "wu1982"(기본)는 기존과 비트 동일하다.
        self.drag = str(drag)
        # 스웰 감쇠는 계수를 동반하므로 enable 키가 아니라 직교 인자로 둔다.
        # "none"(기본) 이면 이 항이 계산조차 되지 않아 기존 동작과 비트 동일하다.
        self.swell = str(swell)
        self.swell_par = swell_par if swell_par is not None else SwellPar()
        self.lats = np.asarray(lats, dtype=np.float64)
        self.lons = np.asarray(lons, dtype=np.float64)
        self.ny, self.nx = len(lats), len(lons)

        d = np.asarray(depth, dtype=np.float64)
        self.sea = d > h_min                                  # (ny, nx)
        self.depth = np.maximum(d, h_min)

        sig = 2.0 * np.pi * grid.f
        # (ny, nx, nf) 유한수심 계수
        k = wavenumber(sig[None, None, :], self.depth[:, :, None], g)
        self.kh = k * self.depth[:, :, None]
        # 스웰 감쇠의 층류 항이 쓰는 심수 파수 (Ardhuin 2010 식 8)
        self.k_deep = sig ** 2 / g
        self.cg = group_velocity(sig[None, None, :], self.depth[:, :, None], g)
        self.c_phase = sig[None, None, :] / k
        self.kh_mean_proxy = None  # DIA 깊이 배율은 스텝에서 k̃h로 계산

        # 격자 메트릭
        phi = np.radians(self.lats)
        dlam = np.radians(self.lons[1] - self.lons[0])
        dphi = np.radians(self.lats[1] - self.lats[0])
        self.dx = (R_EARTH * np.cos(phi) * dlam)[:, None]     # (ny,1) [m]
        self.dy = R_EARTH * dphi
        self.cosphi = np.cos(phi)[:, None]
        # UNO2 정적 마스크 — 4점 스텐실이 확보된 면만 2차, 나머지는 1차 강등.
        # 육지의 인공 0이 2차 재구성에 섞이면 해안 셀 Hs가 계통적으로 깎인다.
        self.ok_x = _face_ok(self.sea, 1)[:, :, None, None]
        self.ok_y = _face_ok(self.sea, 0)[:, :, None, None]
        self._dx4 = self.dx[:, :, None, None].astype(dtype)     # (ny,1,1,1)
        self._w4 = self.cosphi[:, :, None, None].astype(dtype)  # (ny,1,1,1)
        self._sea4 = self.sea[:, :, None, None].astype(dtype)  # GSE 육지 마스크

        # 방향 전파 속도 θ̇ (ny,nx,nf,nθ): 수심 굴절 + 대권 전환 (정지 수심 → 사전 계산)
        dhdx = np.gradient(self.depth, axis=1) / self.dx
        dhdy = np.gradient(self.depth, axis=0) / self.dy
        s2kh = np.sinh(np.clip(2.0 * self.kh, 1e-6, 25.0))
        ct = (sig[None, None, :, None] / s2kh[..., None]
              * (np.sin(grid.theta)[None, None, None, :] * dhdx[:, :, None, None]
                 - np.cos(grid.theta)[None, None, None, :] * dhdy[:, :, None, None]))
        ct += (-self.cg[..., None] / R_EARTH * np.tan(phi)[:, None, None, None]
               * np.cos(grid.theta)[None, None, None, :])
        ct[~self.sea] = 0.0
        self.ctheta = ct.astype(dtype)
        self.ctheta_max = float(np.abs(ct).max())

        self.dia = DIA(grid)
        self._bc: Arr | None = None
        self._bc_sides: str = "WESN"

    # ── 상태 생성 ─────────────────────────────────────────────────
    def spectra_from_integrals(self, hs: Arr, tp: Arr, mdir: Arr,
                               gamma: float | None = None,
                               spread_power: float | None = None) -> Arr:
        """Hs·Tp·평균방향 필드 → JONSWAP × cos² 확산 스펙트럼 재구성.

        gamma·spread_power는 **진단 전용**이며 기본값(None)은 운영 동작과 동일하다
        (JONSWAP γ=3.3, cos² 확산).

        왜 필요한가: 이 재구성은 격자 전체에 **같은 폭의 단봉 스펙트럼**을 부여한다
        (실측: 리드 0의 Hs>4 m 셀 1,180개가 전부 dspr 31.51°). 태풍 주변의 광대역
        혼합해가 응집된 스웰로 치환되고, 좁은 스펙트럼은 Komen 소산의 경사도 인자를
        낮춰 감쇠를 더 약하게 만든다. gamma를 낮추고(1.0 = Pierson-Moskowitz)
        spread_power를 낮추면 더 넓은 초기장이 되며, 그것이 2026-08-24 과대예측을
        줄이는지 보는 것이 이 스위치의 목적이다(docs/PHASE12 §11-5의 후보 A).
        """
        gr = self.grid
        gam = 3.3 if gamma is None else float(gamma)
        sp_pow = 2.0 if spread_power is None else float(spread_power)
        fp = 1.0 / np.clip(np.nan_to_num(tp, nan=8.0), 2.0, 25.0)
        e1 = jonswap_spectrum(gr.f[None, None, :], fp[..., None], gamma=gam)
        m0_raw = (e1 * gr.df[None, None, :]).sum(-1)
        m0_tgt = (np.nan_to_num(hs, nan=0.0) / 4.0) ** 2
        scale = np.where(m0_raw > 1e-12, m0_tgt / np.maximum(m0_raw, 1e-12), 0.0)
        spread = np.maximum(np.cos(gr.theta[None, None, :]
                                   - np.nan_to_num(mdir, nan=0.0)[..., None]), 0.0) ** sp_pow
        spread /= np.maximum(spread.sum(-1, keepdims=True) * gr.dtheta, 1e-12)
        e = (e1 * scale[..., None])[..., :, None] * spread[..., None, :]
        e[~self.sea] = 0.0
        return e.astype(self.dtype)

    def set_boundary(self, e_bc: Arr, sides: str = "WESN") -> None:
        """가장자리 링에 고정할 경계 스펙트럼 (전체 필드 형상, 지정 면만 사용).

        sides: 'W','E','S','N' 조합. 미지정 면은 자유 유출(outflow).
        """
        self._bc = e_bc.astype(self.dtype)
        self._bc_sides = sides

    def _clamp_boundary(self, e: Arr) -> Arr:
        if self._bc is not None:
            side_slices = {"S": np.s_[0, :], "N": np.s_[-1, :],
                           "W": np.s_[:, 0], "E": np.s_[:, -1]}
            for side, sl in side_slices.items():
                if side in self._bc_sides:
                    e[sl] = self._bc[sl]
        e[~self.sea] = 0.0
        return e

    # ── 전파 ─────────────────────────────────────────────────────
    def gse_weights(self, dt: float) -> Arr:
        """Tolman(2002) 평균화 3×3 가중치 (dt 의존 → 값 기준 캐시)."""
        if self._gse_cache is not None and self._gse_cache[0] == float(dt):
            return self._gse_cache[1]
        gr = self.grid
        w = gse_stencil(self.dx[:, 0], self.dy, gr.f, gr.ratio, gr.theta,
                        gr.dtheta, self.g, float(dt), self.gse_gamma,
                        self.gse_gamma_cg)
        w = w[:, None].astype(self.dtype)              # (ny,1,nf,nθ,3,3)
        self._gse_cache = (float(dt), w)
        return w

    def cfl_dt(self, cfl: float = 0.6) -> float:
        """지리 CFL만 반영 — θ 이류는 _advect에서 자체 서브사이클 (WW3 관행)."""
        cg_max = float(self.cg.max())
        dt_x = float(self.dx.min()) / cg_max
        dt_y = self.dy / cg_max
        return cfl * min(dt_x, dt_y)

    def _advect(self, e: Arr, dt: float) -> Arr:
        gr = self.grid
        cgx = (self.cg[..., None] * np.cos(gr.theta)).astype(self.dtype)
        cgy = (self.cg[..., None] * np.sin(gr.theta)).astype(self.dtype)

        if self.advection == "uno2":
            # x (경도): Δx 는 행 내 상수 → 정규격자 UNO2 를 E 위에 직접 적용
            crx = cgx * (self.dtype(dt) / self._dx4)
            e = _uno2_sweep(e, crx, 1, self.ok_x, np)
            # y (위도): 보존밀도 q = E·cos φ 위에서 재구성해야 플럭스형 TVD 성질이
            # 유지된다 (WW3 도 VQ = N/CG × CLATS 를 전송한다).
            w = self._w4
            cry = cgy * self.dtype(dt / self.dy)
            e = _uno2_sweep(e * w, cry, 0, self.ok_y, np) / w
        else:
            # x (경도): 도너셀 분할 플럭스, 경계 밖 유입 0
            fp_ = np.maximum(cgx, 0) * e
            fm = np.minimum(cgx, 0) * e
            div_x = np.empty_like(e)
            div_x[:, :, ...] = fp_ - np.concatenate(
                [np.zeros_like(fp_[:, :1]), fp_[:, :-1]], axis=1)
            div_x += np.concatenate([fm[:, 1:], np.zeros_like(fm[:, :1])], axis=1) - fm
            e = e - (dt / self.dx[..., None, None]) * div_x

            # y (위도): cos φ 메트릭 보존형
            w = self.cosphi[..., None, None]
            fp_ = np.maximum(cgy, 0) * e * w
            fm = np.minimum(cgy, 0) * e * w
            div_y = fp_ - np.concatenate([np.zeros_like(fp_[:1]), fp_[:-1]], axis=0)
            div_y += np.concatenate([fm[1:], np.zeros_like(fm[:1])], axis=0) - fm
            e = e - (dt / (self.dy * w)) * div_y

        # θ (굴절+대권): 주기 상향 — 급경사 셀의 θ-CFL을 위해 서브사이클
        # (1단계는 1차 유지: 진단된 과잉확산은 지리축이고, 변경 축을 줄여야
        #  회귀 원인 추적이 가능하다. θ축 UNO2 승급은 후속 PR.)
        ct = self.ctheta
        n_sub = max(1, int(np.ceil(dt * self.ctheta_max / (0.7 * gr.dtheta))))
        dts = dt / n_sub
        for _ in range(n_sub):
            fp_ = np.maximum(ct, 0) * e
            fm = np.minimum(ct, 0) * e
            div_t = fp_ - np.roll(fp_, 1, axis=-1) + np.roll(fm, -1, axis=-1) - fm
            e = e - (dts / gr.dtheta) * div_t
        e = np.maximum(e, 0.0)

        # GSE 완화 — 이류와 완전히 분리된 별도 분수 스텝 (WW3 PR3 와 동일 위치).
        # 소스항 뒤가 아니라 이류 직후여야 성장 중 풍파가 뭉개지기 전에 소스가 작동한다.
        if self.gse == "tolman":
            e = gse_average(e, self.gse_weights(dt), self._w4, self._sea4, np)
        return e

    # ── 소스항 ────────────────────────────────────────────────────
    def _sources(self, e: Arr, dt: float, u10: Arr, udir: Arr,
                 row_block: int = 16) -> Arr:
        """행 블록 단위 처리 — 대형 격자에서 fp64 중간 배열 메모리 상한 유지."""
        out = np.empty_like(e)
        u10 = np.broadcast_to(np.asarray(u10, dtype=np.float64), e.shape[:2])
        udir = np.broadcast_to(np.asarray(udir, dtype=np.float64), e.shape[:2])
        for j0 in range(0, e.shape[0], row_block):
            j1 = min(j0 + row_block, e.shape[0])
            out[j0:j1] = self._sources_block(
                e[j0:j1], dt, u10[j0:j1], udir[j0:j1], np.s_[j0:j1])
        return out

    def _sources_block(self, e: Arr, dt: float, u10: Arr, udir: Arr,
                       rows) -> Arr:
        gr = self.grid
        e64 = e.astype(np.float64)
        s = np.zeros_like(e64)
        if "wind" in self.enable:
            s += wind_input(e64, gr, u10, udir, self.g,
                            negative_input=self.negative_input,
                            drag=self.drag,
                            c_phase=self.c_phase[rows])
        if self.swell != "none":
            # Snyder 성장률의 부호로 스웰 성분을 가른다. Sin 이 max(β,0) 으로
            # 잘라낸 그 자리를 Ardhuin et al. (2010) 정식으로 채우는 것이다.
            _ust = np.asarray(friction_velocity(u10, self.drag))[..., None, None]
            _cd = np.cos(gr.theta[None, :] - np.asarray(udir)[..., None, None])
            _beta = (28.0 * _ust / self.c_phase[rows][..., :, None] * _cd - 1.0)
            s += swell_damping_rate(
                e64, gr, k=self.k_deep, par=self.swell_par, beta=_beta,
                ustar=np.asarray(friction_velocity(u10, self.drag)), theta_wind=udir,
                g=self.g, xp=np) * e64
        if "ds" in self.enable:
            s += dissipation(e64, gr, self.g, cds=CAL_CDS, delta=CAL_DELTA)
        if "nl" in self.enable:
            _, k_m = gr.mean_sigma_k(e64, self.g)
            r = dia_depth_factor(k_m * self.depth[rows])[..., None, None]
            s += r * self.dia(e64, self.g)
        if "bot" in self.enable:
            s += bottom_friction(e64, gr, self.kh[rows], self.g)
        if "brk" in self.enable:
            s += depth_breaking(e64, gr, self.depth[rows], self.g)

        d = np.where(e64 > 1e-30, s / np.maximum(e64, 1e-30), 0.0)
        de = s * dt / (1.0 + np.maximum(-d, 0.0) * dt)
        lim = growth_limiter(gr, dt, self.g, frac=CAL_LIMITER_FRAC)
        de = np.clip(de, -lim, lim)
        out = np.maximum(e64 + de, 0.0)
        out = apply_tail_and_limits(out, gr, self.g)
        return out.astype(self.dtype)

    # ── 시간 전진 ─────────────────────────────────────────────────
    def step(self, e: Arr, dt: float, u10: Arr | float = 0.0,
             udir: Arr | float = 0.0, sources: bool = True) -> Arr:
        e = self._advect(e, dt)
        e = self._clamp_boundary(e)
        if sources and self.enable:
            e = self._sources(e, dt, np.asarray(u10), np.asarray(udir))
            e = self._clamp_boundary(e)
        return e

    def run(self, e: Arr, hours: float, dt: float | None = None,
            wind_fn=None, sources: bool = True) -> Arr:
        """wind_fn(t_sec) -> (u10, udir) 필드. 시간 보간은 호출자 책임."""
        dt = dt or self.cfl_dt()
        n = int(round(hours * 3600.0 / dt))
        t = 0.0
        for _ in range(n):
            u10, udir = wind_fn(t) if wind_fn else (0.0, 0.0)
            e = self.step(e, dt, u10, udir, sources=sources)
            t += dt
            if not np.isfinite(e).all():
                raise FloatingPointError(f"regional wave model diverged at t={t:.0f}s")
        return e
