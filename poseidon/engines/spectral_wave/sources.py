"""3세대 파랑 모델 소스항 — WAM cycle 3 계열 (심수).

근거 (전부 동료심사 문헌):
- S_in : Snyder et al. (1981)의 관측 기반 선형 성장률, WAMDI (1988) 형식
         β = max(0, 0.25 (ρ_a/ρ_w)(28 u*/c · cos(θ−θ_w) − 1)) σ
- S_ds : Komen et al. (1984) 백파 소산
         S_ds = −C_ds σ̃ (k/k̃) (s̃²/s̃²_PM)² E,  s̃² = E_tot k̃²,  s̃²_PM = 3.02×10⁻³
- S_nl : Hasselmann et al. (1985) DIA — λ=0.25, C=3×10⁷,
         쿼드러플릿 (σ,σ,σ(1+λ),σ(1−λ)), 방향 오프셋 (+11.48°, −33.56°) + 경상 배치
- 항력계수: Wu (1982) C_d = (0.8 + 0.065 U₁₀)×10⁻³

배열 규약: E(..., nf, ntheta) [m²/Hz/rad]. 심수 전용 (유한수심 확장은 Phase 6).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from poseidon.engines.spectral_wave.grid import SpectralGrid
from poseidon.physics.constants import (G_STANDARD, NU_AIR, RHO_AIR,
                                        RHO_SEAWATER)

Arr = NDArray[np.float64]

S_PM2 = 3.02e-3          # Komen (1984) PM 스펙트럼 전체 경사도 제곱
CDS_KOMEN = 2.36e-5      # Komen 소산 계수 (SWAN KOMEN 기본값)
C_DIA = 3.0e7            # Hasselmann et al. (1985) DIA 비례상수
LAMBDA_DIA = 0.25


CD_CAP_U10 = 33.0
"""항력계수 포화가 시작되는 풍속 [m/s].

Powell, Vickery & Reinhold (2003) *Nature* 422, 279–283 이 GPS 낙하존데
331 프로파일에서 관측한 값이다 — 원문: "Z0 and Cd initially increased as
surface winds approached hurricane force (33 m/s)" 이후 "the tendency was
for a decrease in Cd for U10 > 33 m/s".

**감소 분기는 구현하지 않는다.** Powell 2003 자체가 51 m/s 초과 관측이 없다고
명시하고, Bell, Montgomery & Emanuel (2012) *JAS* 69, 3197–3222 이 52~72 m/s
구간에서 Cd ≈ 2.4×10⁻³ 를 보고해 급감 외삽과 충돌한다. 상한 클램프가 두 관측
모두와 모순되지 않는 최소 개입이다.

클램프 값 Wu(33) = 2.945×10⁻³ 은 실험실 관측의 포화대와도 겹친다 —
Curcic & Haus (2020) *GRL* 47, e2020GL087647 이 Donelan et al. (2004) 을
정정해 3.01×10⁻³ (U10 ≈ 29 m/s), 자체 신규 실험에서 2.3~2.9×10⁻³ 를 보고했다.
"""


def drag_coefficient(u10: Arr | float, scheme: str = "wu1982",
                     xp=np) -> Arr | float:
    """해면 항력계수 C_d.

    scheme:
      "wu1982"  (기본) Wu (1982): C_d = (0.8 + 0.065 U10) × 10⁻³. 선형이라 상한이 없다.
      "cap-wu"  같은 식에 **상한 클램프**: C_d = min(Wu(U10), Wu(33)).
                U10 < 33 m/s 에서는 wu1982 와 **비트 동일**하므로 중·저풍속
                (우리 관측소 대부분이 U10 3~10 m/s) 회귀 위험이 없다.

    `xp` 제네릭이다 — NumPy 참조와 JAX 커널이 이 함수 하나를 공유한다.
    Wu 식이 세 곳에 복제돼 있었고(`jax_kernel` 인라인, `surge_cycle`), 그중
    하나만 고치면 나머지가 조용히 옛 식으로 남는다(실측 이력).
    """
    u = xp.asarray(u10)
    if scheme == "cap-wu":
        u = xp.minimum(u, CD_CAP_U10)
    elif scheme != "wu1982":
        raise ValueError(f"unknown drag scheme: {scheme}")
    return (0.8 + 0.065 * u) * 1e-3


def friction_velocity(u10: Arr | float, scheme: str = "wu1982",
                      xp=np) -> Arr | float:
    u = xp.asarray(u10)
    return u * xp.sqrt(drag_coefficient(u, scheme=scheme, xp=xp))


def wind_input(e: Arr, grid: SpectralGrid, u10: Arr | float, theta_wind: Arr | float,
               g: float = G_STANDARD, c_phase: Arr | None = None,
               negative_input: bool = False, drag: str = "wu1982") -> Arr:
    """Snyder/WAM3 바람 입력. 성장률 β [1/s] × E.

    u10·theta_wind는 스칼라(1D 페치) 또는 공간 배열 (..., ) — E(..., nf, nθ)와 브로드캐스트.
    c_phase: 유한수심 위상속도 (..., nf) — 생략 시 심수 g/σ.

    negative_input: **진단 전용.** 기본 False는 WAM cycle 3 정식 그대로
        `max(β, 0)`으로 음의 분기를 잘라낸다. True로 두면 파가 바람보다 빠르거나
        역풍일 때(β < 0) 감쇠를 허용한다.

        왜 이 스위치가 필요한가: 절단하면 **스웰을 감쇠시키는 항이 모델에 하나도
        남지 않는다.** Komen 소산은 파형경사의 4제곱에 비례해 스웰 조건에서
        풍파의 1/300~1/500로 줄고, 저면마찰은 심해에서 무의미하다. 실제로
        심해 스웰 36 h 적분에서 총 감쇠가 Hs −3.5%에 그친다. 2026-08-24 사이클의
        과대예측이 이 구조와 정합한다(docs/PHASE12 §11-5).

        음의 입력은 3세대 모델에서 표준이다 — WW3의 ST4(Ardhuin et al. 2010
        *JPO* 40, 1917–1941)와 ST6(Zieger et al. 2015 *Ocean Modelling* 96, 2–25)가
        스웰 감쇠를 명시적으로 다룬다. **다만 Snyder의 선형식을 음수로 그대로
        연장하는 것은 그 문헌의 정식이 아니다** — 여기서는 감쇠 항의 부재가
        현상을 설명하는지 **크기를 재기 위한 진단**이며, 이 값으로 운영하지 않는다.
    """
    ustar = np.asarray(friction_velocity(u10, drag))[..., None, None]
    tw = np.asarray(theta_wind, dtype=np.float64)[..., None, None]
    sig = 2.0 * np.pi * grid.f
    if c_phase is None:
        c = (g / sig)[:, None]
    else:
        c = np.asarray(c_phase)[..., :, None]
    cosd = np.cos(grid.theta[None, :] - tw)               # (..., 1, nθ)
    beta = 0.25 * (RHO_AIR / RHO_SEAWATER) * (28.0 * ustar / c * cosd - 1.0)
    if not negative_input:
        beta = np.maximum(beta, 0.0)
    beta = beta * sig[:, None]
    return beta * e


def bottom_friction(e: Arr, grid: SpectralGrid, kh: Arr, g: float = G_STANDARD,
                    cb: float = 0.038) -> Arr:
    """JONSWAP 해저마찰 (Hasselmann et al. 1973): S = −C_b σ²/(g² sinh²kh) E.

    C_b = 0.038 m²s⁻³ (SWAN 현행 기본값). kh: (..., nf) 브로드캐스트.
    """
    sig2 = (2.0 * np.pi * grid.f) ** 2
    sh = np.sinh(np.clip(kh, 1e-6, 25.0)) ** 2
    return -(cb * sig2[:, None] / (g ** 2 * sh[..., :, None])) * e


def depth_breaking(e: Arr, grid: SpectralGrid, depth: Arr, g: float = G_STANDARD,
                   alpha_bj: float = 1.0, gamma_bj: float = 0.73) -> Arr:
    """Battjes & Janssen (1978) 수심 유도 쇄파 (음수 반환).

    D_tot = −(α/4) Q_b f̃ H_m²,  H_m = γ h,  (1−Q_b)/ln Q_b = −8 m₀/H_m²
    S(f,θ) = D_tot · E/E_tot  (스펙트럼 형상 비례 분배, SWAN 관행)
    """
    m0 = np.maximum(grid.total_energy(e), 1e-12)
    hm = gamma_bj * np.maximum(np.asarray(depth, dtype=np.float64), 0.01)
    b = hm ** 2 / (8.0 * m0)                              # 클수록 쇄파 없음
    qb = np.where(b < 0.3, 1.0, 0.0)                      # 초기값
    for _ in range(40):                                   # Q_b = exp(−b(1−Q_b))
        qb = np.exp(-b * (1.0 - qb))
    qb = np.where(b > 5.0, 0.0, qb)
    sig_m, _ = grid.mean_sigma_k(e, g)
    d_tot = 0.25 * alpha_bj * qb * (sig_m / (2.0 * np.pi)) * hm ** 2
    return -(d_tot / m0)[..., None, None] * e


def dia_depth_factor(kh_mean: Arr) -> Arr:
    """WAM 천해 DIA 배율 (WAMDI 1988): R = 1 + (5.5/x)(1 − 5x/6)e^(−5x/4), x = 0.75 k̃h."""
    x = np.maximum(0.75 * kh_mean, 0.15)
    r = 1.0 + (5.5 / x) * (1.0 - 5.0 * x / 6.0) * np.exp(-1.25 * x)
    return np.clip(r, 0.2, 5.0)  # WAM 관행: 극천해 폭주 방지 상한


def seed_spectrum(grid: SpectralGrid, u10: float, theta_wind: float,
                  g: float = G_STANDARD, level: float = 1e-5) -> Arr:
    """초기 시드 — PM 첨두 부근 미소 에너지 (성장의 씨앗, 물리 결과에 영향 없는 크기)."""
    fp = 0.13 * g / max(u10, 1.0)                         # PM 첨두 근사
    e1 = np.exp(-0.5 * ((np.log(grid.f / fp)) / 0.35) ** 2)
    spread = np.maximum(np.cos(grid.theta - theta_wind), 0.0) ** 2
    e = level * e1[:, None] * spread[None, :]
    return e


def dissipation(e: Arr, grid: SpectralGrid, g: float = G_STANDARD,
                cds: float = CDS_KOMEN, delta: float = 0.0) -> Arr:
    """Komen (1984) 백파 소산 (음수 반환).

    SWAN 일반화 (scientific doc): S_ds = −C_ds σ̃ (k/k̃)[(1−δ)+δ(k/k̃)](s̃/s̃_PM)⁴ E.
    δ=0 → Komen 원형(WAM cycle 3), δ=1 → SWAN 40.91+ 기본 (고주파 소산 강화).
    """
    sig_m, k_m = grid.mean_sigma_k(e, g)
    etot = grid.total_energy(e)
    s2 = etot * k_m ** 2
    k = (2.0 * np.pi * grid.f) ** 2 / g
    kr = k[:, None] / np.maximum(k_m[..., None, None], 1e-30)
    gamma = (cds * sig_m * (s2 / S_PM2) ** 2)[..., None, None] \
        * kr * ((1.0 - delta) + delta * kr)
    return -gamma * e


@dataclass
class DIA:
    """이산 상호작용 근사 (Hasselmann et al. 1985).

    쿼드러플릿당 기여: 중심 빈 −2S̃, σ(1+λ) 빈 +S̃, σ(1−λ) 빈 +S̃ (경상 배치 포함).
    로그 주파수 격자에서 오프셋이 상수이므로 전 빈을 동시에 처리한다.
    격자 밖 산란 기여는 소실을 허용(예단 범위 이탈), 수집 시 고주파는 f⁻⁵ 꼬리 외삽.
    """

    grid: SpectralGrid
    tail_power: float = 5.0

    def __post_init__(self) -> None:
        g = self.grid
        lnr = math.log(g.ratio)
        self.dp = math.log(1.0 + LAMBDA_DIA) / lnr        # +2.34 (r=1.1)
        self.dm = math.log(1.0 - LAMBDA_DIA) / lnr        # −3.02
        self.o3 = math.radians(11.48) / g.dtheta          # +1.148
        self.o4 = -math.radians(33.56) / g.dtheta         # −3.356

    # ── 격자 이동 도우미 ──────────────────────────────────────────
    def _shift(self, arr: Arr, di: float, dj: float, gather: bool) -> Arr:
        """(nf,nθ) 마지막 두 축을 (di, dj)만큼 이동한 값을 겹선형 보간으로 취득/산란.

        gather=True : 반환[i,j] = arr[i+di, j+dj] (고주파 밖은 꼬리 외삽, 저주파 밖 0)
        gather=False: 반환은 arr을 (di,dj)만큼 '밀어 넣은' 산란 결과 (범위 밖 소실)
        """
        i0 = math.floor(di)
        wi = di - i0
        j0 = math.floor(dj)
        wj = dj - j0
        out = np.zeros_like(arr)
        for a, wa in ((0, 1.0 - wi), (1, wi)):
            for b, wb in ((0, 1.0 - wj), (1, wj)):
                w = wa * wb
                if w == 0.0:
                    continue
                sf = i0 + a
                sd = j0 + b
                if gather:
                    part = self._shift_freq_gather(arr, sf)
                else:
                    part = self._shift_freq_scatter(arr, sf)
                out += w * np.roll(part, (-sd if gather else sd), axis=-1)
        return out

    def _shift_freq_gather(self, arr: Arr, s: int) -> Arr:
        nf = self.grid.nf
        out = np.zeros_like(arr)
        if s >= 0:
            out[..., : nf - s, :] = arr[..., s:, :]
            # 고주파 꼬리 외삽: E ∝ f^-p → 빈당 감쇠 r^-p
            decay = self.grid.ratio ** (-self.tail_power)
            for m in range(max(nf - s, 0), nf):
                out[..., m, :] = arr[..., nf - 1, :] * decay ** (m + s - (nf - 1))
        else:
            out[..., -s:, :] = arr[..., : nf + s, :]
        return out

    def _shift_freq_scatter(self, arr: Arr, s: int) -> Arr:
        nf = self.grid.nf
        out = np.zeros_like(arr)
        if s >= 0:
            out[..., s:, :] = arr[..., : nf - s, :]
        else:
            out[..., : nf + s, :] = arr[..., -s:, :]
        return out

    # ── 본체 ─────────────────────────────────────────────────────
    def __call__(self, e: Arr, g: float = G_STANDARD) -> Arr:
        gr = self.grid
        f = gr.f[:, None]
        snl = np.zeros_like(e)
        for mirror in (1.0, -1.0):
            o3, o4 = mirror * self.o3, mirror * self.o4
            e3 = self._shift(e, self.dp, o3, gather=True)
            e4 = self._shift(e, self.dm, o4, gather=True)
            # Hasselmann et al. (1985) 원 정식화: f-공간 F(f,θ), C = 3×10⁷
            s_rate = (C_DIA / g ** 4) * f ** 11 * (
                e * e * (e3 / (1 + LAMBDA_DIA) ** 4 + e4 / (1 - LAMBDA_DIA) ** 4)
                - 2.0 * e * e3 * e4 / (1 - LAMBDA_DIA ** 2) ** 4)
            snl += -2.0 * s_rate
            snl += self._shift(s_rate, self.dp, o3, gather=False)
            snl += self._shift(s_rate, self.dm, o4, gather=False)
        return snl


def apply_tail_and_limits(e: Arr, grid: SpectralGrid, g: float = G_STANDARD,
                          tail_power: float = 5.0, hf_factor: float = 2.5) -> Arr:
    """WAM 예단/진단 분리: f > 2.5 f̃ 구간에 f⁻ᵖ 진단 꼬리 부과 (완전 벡터화)."""
    lead = e.shape[:-2]
    ee = e.reshape(-1, grid.nf, grid.ntheta)
    n = ee.shape[0]

    sig_m, _ = grid.mean_sigma_k(ee, g)
    f_hf = hf_factor * np.maximum(sig_m / (2.0 * np.pi), 1e-6)
    idx = np.clip(np.searchsorted(grid.f, f_hf), 1, grid.nf - 1)      # (n,)

    anchor = ee[np.arange(n), idx - 1, :]                             # (n, nθ)
    ratio = (grid.f[None, :] / grid.f[idx - 1][:, None]) ** (-tail_power)
    mask = np.arange(grid.nf)[None, :] >= idx[:, None]                # (n, nf)
    out = np.where(mask[..., None], anchor[:, None, :] * ratio[..., None], ee)
    return out.reshape(*lead, grid.nf, grid.ntheta)


def growth_limiter(grid: SpectralGrid, dt: float, g: float = G_STANDARD,
                   frac: float = 0.1, t_ref: float = 300.0) -> Arr:
    """WAMDI(1988)식 증분 상한 — 시간률 기반 (dt 독립적 물리 보장).

    |ΔE(f,θ)| ≤ frac · α_P g² (2π)⁻⁴ f⁻⁵/(2π) · (dt/t_ref),  α_P = 0.0081
    (WAM 원형은 고정 소스 스텝당 상한이었으나, 가변 dt에서 물리가 dt에 의존하게
    되므로 기준 스텝 t_ref=300 s의 비율로 환산한다.)
    """
    lim = (frac * 0.0081 * g ** 2 * (2.0 * np.pi) ** -4 * grid.f ** -5
           / (2.0 * np.pi) * (dt / t_ref))
    return lim[:, None]


# ── 스웰 감쇠 (Ardhuin et al. 2010) ───────────────────────────────────────

@dataclass(frozen=True)
class SwellPar:
    """Ardhuin et al. (2010) *JPO* 40, 1917–1941, 식 (8)(9)(10)의 계수.

    기본값 근거:
      cdsv=1.2   원문 §3b "Here we shall use Cdsv = 1.2" (Dore 1978 층류 이론값은 1)
      fe=0.007   원문 §3b "constant values of fe in the range 0.004 to 0.007"
      re_c=2e5   원문 §3b "A constant threshold close to 2×10⁵ provides similar results"
                 (WW3 코드는 SWELLF4=1e5 상수를 쓴다 — 문헌·코드 불일치 [확인 필요])
      re_smooth  WW3 SSWELLF(7) tanh 평활폭. 0이면 계단 전환

    s2·s3(바람방향·마찰속도 보정)은 기본 0이다. 우리 엔진에는 Janssen 응력수지가
    없어 파랑 지지 응력 u_*를 정확히 낼 수 없고(Wu 1982 대용은 강풍에서 과대),
    노브를 하나로 묶어야 원인 귀속이 되기 때문이다.
    """

    cdsv: float = 1.2
    fe: float = 0.007
    s2: float = 0.0
    s3: float = 0.0
    re_c: float = 2.0e5
    re_smooth: float = 3.6e5
    gate: str = "beta_negative"


def orbital_moments(e: Arr, grid: SpectralGrid, *, etot=None, xp=np):
    """Ardhuin et al. (2010) 식 (2.107)의 유의 궤도속도·진폭.

        u_orb = 2 √(∬ σ² E df dθ)   [m/s]
        a_orb = 2 √(m0) = Hs/2       [m]
    """
    sig2 = (2.0 * np.pi * grid.f) ** 2
    w = grid.df[:, None] * grid.dtheta
    m2s = (e * (sig2[:, None] * w)).sum(axis=(-2, -1))
    m0 = ((e * w).sum(axis=(-2, -1)) if etot is None else etot)
    return (2.0 * xp.sqrt(xp.maximum(m2s, 0.0)),
            2.0 * xp.sqrt(xp.maximum(m0, 0.0)))


def swell_damping_rate(e: Arr, grid: SpectralGrid, *, k: Arr, par: SwellPar,
                       beta: Arr, ustar=None, theta_wind=None,
                       g: float = G_STANDARD, etot=None, visc_rate=None,
                       xp=np) -> Arr:
    """Ardhuin et al. (2010) S_out 의 대각 감쇠율 D [1/s], D ≤ 0. S_out = D·E.

    층류 — 식 (8):  D_visc = −C_dsv (ρ_a/ρ_w) · 2k √(2 ν_a σ)
    난류 — 식 (9)(10):
        D_turb = −(ρ_a/ρ_w) (16 σ²/g) · [ f_e·u_orb + (|s3| + s2 cos(θ−θ_u))·u_* ]

    **난류 항은 f_e·u_orb 를 곱한 형태로 쓴다.** 원문 식 (10)은 f_e 안에
    u_*/u_orb 를 두지만, 그대로 구현하면 에너지 0인 셀에서 u_orb → 0 으로
    발산한다(CLAUDE.md 함정 5). WW3 `w3src4md.F90`도 곱해 편 형태로 계산한다.

    두 분기는 경계 레이놀즈 수로 섞는다 (WW3 SSWELLF(7)):
        Re = 4 u_orb a_orb / ν_a,   p_turb = ½(1 + tanh((Re − Re_c)/s7))

    `gate="beta_negative"`는 Snyder 성장률이 음인 성분(파가 바람보다 빠르거나
    역풍 — 즉 스웰)에만 적용한다. 우리 Sin 은 그 분기를 `max(β,0)`으로 잘라내므로,
    이 게이트가 곧 "잘려나간 자리를 문헌 정식으로 채운다"는 뜻이다.

    **xp 제네릭이다.** NumPy 참조와 JAX 커널이 이 함수 하나를 공유한다 —
    소스항을 두 벌 구현하면 한쪽만 고쳐져 조용히 무시되는 사고가 난다(실측 이력).
    """
    sig = 2.0 * np.pi * grid.f
    u_orb, a_orb = orbital_moments(e, grid, etot=etot, xp=xp)
    rat = RHO_AIR / RHO_SEAWATER

    if visc_rate is None:
        # k 는 (nf,) 또는 (ny,nx,nf). 방향 축을 붙여 난류 항과 형상을 맞춘다 —
        # 맞추지 않으면 (nf,1)+(nf,) 가 (nf,nf) 로 조용히 브로드캐스트된다.
        visc_rate = (-par.cdsv * rat * 2.0 * k
                     * xp.sqrt(2.0 * NU_AIR * sig))[..., :, None]
    turb = -rat * (16.0 * sig ** 2 / g)[:, None] * (par.fe * u_orb[..., None, None])
    if par.s2 or par.s3:
        us = 0.0 if ustar is None else xp.asarray(ustar)[..., None, None]
        cosd = (1.0 if theta_wind is None else
                xp.cos(grid.theta[None, :] - xp.asarray(theta_wind)[..., None, None]))
        turb = turb - rat * (16.0 * sig ** 2 / g)[:, None] * (
            (abs(par.s3) + par.s2 * cosd) * us)

    re = 4.0 * u_orb * a_orb / NU_AIR
    if par.re_smooth > 0.0:
        p_t = 0.5 * (1.0 + xp.tanh((re - par.re_c) / par.re_smooth))
    else:
        p_t = xp.where(re > par.re_c, 1.0, 0.0)
    p_t = p_t[..., None, None]
    d = p_t * turb + (1.0 - p_t) * visc_rate

    if par.gate == "beta_negative":
        d = xp.where(beta <= 0.0, d, 0.0)
    return xp.minimum(d, 0.0)


def swell_dissipation(e: Arr, grid: SpectralGrid, **kw) -> Arr:
    """S = D·E. 다른 소스항과 같은 규약(음수 반환)."""
    return swell_damping_rate(e, grid, **kw) * e
