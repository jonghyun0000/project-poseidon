"""2차 이류 승급 게이트 (PHASE2 §7.1): UNO2 + Tolman(2002) GSE 완화.

승급 근거는 1차 도너셀의 수치확산 D_num = c_g Δx (1−Cr)/2 가 격자간격에 비례해
커져 **격자를 바꾸면 물리가 바뀌는** 상태였다는 점이다 (L1 0.25° 는 PHASE5 보정
기준 Δx = 5 km 대비 5~6배). 여기서 게이트하는 항목:

  A. 수치확산 감소       — 1차 해석값 대비 잔여 비율
  B. 해상도 독립성       — 동일 물리 문제를 L1/L2 로 풀었을 때 팩킷 폭 일치
  C. 단조성·양수보존     — 대좌 위 톱햇의 오버/언더슈트·전변동 (TVD)
  D. 질량 보존
  E. GSE(부채살) 완화    — 2차 승급은 GSE를 드러내므로 동시 검증이 필수
  F. GSE 스텐실 불변량   — W ≥ 0, ΣW = 1

1차 도너셀 기준선(측정값, tests 아래 값과 동일 방법):
  L1 D_eff = 63,200 m²/s (잔여 100%), L2 = 13,560 / 24h 팩킷폭 불일치 +47.8%
  GSE 부채살: L1 로브 3개·리플 0.493, L2 로브 5개·리플 0.933

참고: Li (2008) MWR 136, 4709–4729 / WW3 w3uno2md.F90 · w3pro3md.F90 /
      Tolman (2002) Ocean Modelling 4, 269–289.
"""

import math
from functools import lru_cache

import numpy as np
import pytest

from poseidon.engines.spectral_wave.grid import SpectralGrid
from poseidon.engines.spectral_wave.regional import RegionalWaveModel, gse_stencil
from poseidon.physics.constants import G_STANDARD, R_EARTH
from poseidon.physics.waves import deep_group_velocity

pytestmark = pytest.mark.scientific

DEEP = 4000.0
LAT0 = 35.0
NF = 12          # 단일 (f,θ) 빈만 채우므로 nf 축소는 결과 불변 (비용만 절감)


def _model(dstep_deg, ny, nx, *, advection, gse, nf=NF, gamma=1.5):
    grid = SpectralGrid(nf=nf, ntheta=36)
    lats = LAT0 + dstep_deg * (np.arange(ny) - ny // 2)
    lons = 130.0 + dstep_deg * np.arange(nx)
    depth = np.full((ny, nx), DEEP)
    m = RegionalWaveModel(lats, lons, depth, grid, enable=(),
                          advection=advection, gse=gse, gse_gamma=gamma)
    m.ctheta = np.zeros_like(m.ctheta)       # θ축 동결 → 지리 이류만 분리
    m.ctheta_max = 0.0
    return m


def _packet(dstep_deg, *, cfl, advection, gse, f_target=0.10, sigma0_m=70e3,
            hours=12.0, ny=3):
    """가우시안 스웰팩킷 x축 자유전파 → D_eff · 위상 · 질량 · 최소값.

    σ²(T) = σ²(0) + 2 D_eff T. 1차 도너셀의 해석해 D = c_g Δx (1−Cr)/2 는
    이산 모멘트 전개로 엄밀하므로 그대로 비교 기준선이 된다.
    θ = 0 단일 빈이라 c_gy = 0 → y 플럭스가 정확히 0 인 1D 문제가 된다
    (단 GSE 평균화는 광선법선 = y 로 퍼지므로 gse="tolman" 시 ny 를 넉넉히 준다).
    """
    tmp = _model(dstep_deg, ny, 8, advection=advection, gse=gse)
    gr = tmp.grid
    fi = int(np.argmin(np.abs(gr.f - f_target)))
    cg = float(deep_group_velocity(gr.f[fi]))
    dx = float(tmp.dx[ny // 2, 0])
    dt = tmp.cfl_dt(cfl)
    cr = cg * dt / dx
    nsteps = max(1, int(round(hours * 3600.0 / dt)))
    T = nsteps * dt
    d_up = cg * dx * (1.0 - cr) / 2.0                     # 1차 도너셀 해석값
    sig_T = math.sqrt(sigma0_m ** 2 + 2.0 * d_up * T)
    i0 = int(math.ceil(5.0 * sigma0_m / dx)) + 3
    nx = i0 + int(math.ceil(cg * T / dx)) + int(math.ceil(5.0 * sig_T / dx)) + 3

    m = _model(dstep_deg, ny, nx, advection=advection, gse=gse)
    x = np.arange(nx) * dx
    wlat = np.cos(np.radians(m.lats))[:, None]
    e = np.zeros((ny, nx, gr.nf, gr.ntheta), np.float32)
    prof = np.exp(-0.5 * ((x - i0 * dx) / sigma0_m) ** 2)
    e[:, :, fi, 0] = (prof / (gr.df[fi] * gr.dtheta)).astype(np.float32)

    def mom(ee):
        p = (gr.total_energy(ee.astype(np.float64)) * wlat).sum(axis=0)
        s = p.sum()
        xb = (p * x).sum() / s
        return p, s, xb, (p * (x - xb) ** 2).sum() / s

    _, s0, xb0, v0 = mom(e)
    for _ in range(nsteps):
        e = m.step(e, dt, sources=False)
    p1, s1, xb1, v1 = mom(e)
    return {
        "dx": dx, "dt": dt, "cr": cr, "cg": cg, "T": T, "nsteps": nsteps,
        "D_eff": (v1 - v0) / (2.0 * T), "D_upwind": d_up,
        "sigma_T": math.sqrt(max(v1, 0.0)),
        "phase_err": (xb1 - xb0) / T / cg - 1.0,
        "mass_drift": (s1 - s0) / s0, "min_value": float(p1.min()),
    }


# ── A. 수치확산 감소 (이류 스킴 단독 — GSE 평균화는 별도 게이트) ──
@pytest.mark.parametrize("tag,dstep,cfl", [("L1_0.25deg", 0.25, 0.60),
                                           ("L2_0.05deg", 0.05, 0.50)])
def test_uno2_cuts_numerical_diffusion(tag, dstep, cfl):
    r = _packet(dstep, cfl=cfl, advection="uno2", gse="none")
    frac = r["D_eff"] / r["D_upwind"]
    assert frac < 0.25, (f"{tag}: D_eff 잔여 {frac:.1%} "
                         f"(D_eff={r['D_eff']:.4g}, 1차={r['D_upwind']:.4g})")
    assert abs(r["phase_err"]) < 5e-3, f"{tag}: 질량중심 위상오차 {r['phase_err']:+.3%}"
    assert abs(r["mass_drift"]) < 1e-4, f"{tag}: 질량 표류 {r['mass_drift']:+.2e}"
    assert r["min_value"] >= 0.0


def test_upwind_fallback_reproduces_first_order_diffusion():
    """회귀 안전장치: advection="upwind" 는 1차 도너셀 해석해를 그대로 재현."""
    r = _packet(0.25, cfl=0.60, advection="upwind", gse="none")
    assert abs(r["D_eff"] / r["D_upwind"] - 1.0) < 0.02, \
        f"D_eff/D_theory = {r['D_eff'] / r['D_upwind']:.4f}"


# ── B. 해상도 독립성 (이 작업의 진짜 합격 기준) ────────────────────
def test_resolution_independence_l1_vs_l2():
    """동일 물리 문제(σ0 = 70 km 팩킷)를 24 h 전파한 뒤 L1/L2 폭 비교.

    1차 도너셀 기준선: σ(L1)/σ(L2) − 1 = +47.8%.
    """
    kw = dict(hours=24.0, advection="uno2", gse="none")
    l1 = _packet(0.25, cfl=0.60, **kw)
    l2 = _packet(0.05, cfl=0.50, **kw)
    dev = l1["sigma_T"] / l2["sigma_T"] - 1.0
    assert abs(dev) <= 0.25, (f"팩킷 폭 불일치 {dev:+.1%} "
                              f"(σ_L1={l1['sigma_T'] / 1e3:.1f} km, "
                              f"σ_L2={l2['sigma_T'] / 1e3:.1f} km)")
    assert abs(dev) < 0.478, "1차 도너셀 기준선(+47.8%)보다 나빠짐"


# ── A'. GSE 평균화가 승급 이득을 되먹지 않는가 (운영 기본 구성) ────
def test_gse_averaging_keeps_diffusion_budget():
    """gse="tolman" 을 켠 운영 기본 구성에서도 L1 확산이 1차의 40% 이하.

    이산 겹선형 산포의 보간확산 때문에 실효 D_ss ≈ γ_cg·½(r−1/r)·c_g·Δx/3 가
    더해진다(연속 top-hat 이론값의 ~23배). 이 게이트가 그 되먹임을 감시한다.
    """
    r = _packet(0.25, cfl=0.60, advection="uno2", gse="tolman", ny=11)
    frac = r["D_eff"] / r["D_upwind"]
    assert frac < 0.40, f"D_eff 잔여 {frac:.1%} (D_eff={r['D_eff']:.4g})"


# ── C. 단조성·양수보존 ─────────────────────────────────────────────
@pytest.mark.parametrize("courant", [0.30, 0.60, 0.90])
def test_uno2_is_monotone_on_pedestal_tophat(courant):
    """대좌 위 톱햇 — _advect 말미의 max(e,0) 이 언더슈트를 삼키지 않게 우회."""
    dstep = 0.25
    tmp = _model(dstep, 3, 8, advection="uno2", gse="none")
    gr = tmp.grid
    fi = int(np.argmin(np.abs(gr.f - 0.10)))
    cg = float(deep_group_velocity(gr.f[fi]))
    dx = float(tmp.dx[1, 0])
    dt = courant * dx / cg
    nsteps = int(round(6 * 3600.0 / dt))
    T = nsteps * dt
    travel = int(math.ceil(cg * T / dx))
    # 서쪽 경계에서 대좌가 배수되며 만드는 전선을 평가창 밖으로 밀어낸다
    smear = int(math.ceil(6.0 * math.sqrt(cg * dx * (1 - courant) * T) / dx)) + 2
    w0 = travel + smear
    i1, width = w0 + 4, 10
    nx = i1 + width + travel + smear + 6

    m = _model(dstep, 3, nx, advection="uno2", gse="none")
    base = np.full(nx, 1.0)
    base[i1:i1 + width] += 1.0
    e = np.zeros((3, nx, gr.nf, gr.ntheta), np.float32)
    e[:, :, fi, 0] = (base / (gr.df[fi] * gr.dtheta)).astype(np.float32)
    p0 = gr.total_energy(e.astype(np.float64))[1]
    for _ in range(nsteps):
        e = m.step(e, dt, sources=False)
    p1 = gr.total_energy(e.astype(np.float64))[1]

    seg0, seg1 = p0[w0:nx - 3], p1[w0:nx - 3]
    over = seg1.max() - 2.0
    under = 1.0 - seg1.min()
    tv = np.abs(np.diff(seg1)).sum() / np.abs(np.diff(seg0)).sum()
    assert over <= 0.01, f"오버슈트 {over:+.4%}"
    assert under <= 0.01, f"언더슈트 {under:+.4%}"
    assert tv <= 1.01, f"TV비 {tv:.4f}"
    assert seg1.min() >= 0.0


# ── E. GSE 부채살 완화 ─────────────────────────────────────────────
@lru_cache(maxsize=None)
def _fan(advection, gse, dstep=0.25, hours=24.0, cfl=0.60, n_dir=5):
    """방향 5빈 cos² 부채꼴 자유전파 → 횡단(x 적분) 프로파일의 로브·리플.

    물리적으로 옳은 답은 로브 1개·리플 0 (연속 방향 스펙트럼의 매끈한 부채꼴).
    단면이 아니라 x 적분을 쓰는 이유: 방향빈마다 c_gx 가 달라 단일 열 단면은
    바깥 로브를 과소평가한다.
    """
    tmp = _model(dstep, 5, 8, advection=advection, gse=gse, nf=4)
    gr = tmp.grid
    fi = int(np.argmin(np.abs(gr.f - 0.07)))
    cg = float(deep_group_velocity(gr.f[fi]))
    dx, dy = float(tmp.dx[2, 0]), float(tmp.dy)
    dt = tmp.cfl_dt(cfl)
    nsteps = max(1, int(round(hours * 3600.0 / dt)))
    T = nsteps * dt
    half = 0.5 * (n_dir - 1) * gr.dtheta
    cr_x = cg * dt / dx
    sig_x = math.sqrt(2 * (cg * dx * max(1 - cr_x, 0.0) / 2.0) * T) / dx
    cgy = cg * math.sin(half)
    sig_y = math.sqrt(2 * (cgy * dy * max(1 - cgy * dt / dy, 0.0) / 2.0) * T) / dy
    nx = int(cg * T / dx) + 14 + int(3 * sig_x) + 12
    ny = 2 * (int(cg * T * math.sin(half) / dy) + int(3 * sig_y) + 8) + 1

    m = _model(dstep, ny, nx, advection=advection, gse=gse, nf=4)
    e = np.zeros((ny, nx, gr.nf, gr.ntheta), np.float32)
    xg, yg = np.meshgrid(np.arange(nx), np.arange(ny))
    blob = np.exp(-0.5 * (((xg - 12) ** 2 + (yg - ny // 2) ** 2) / 1.5 ** 2))
    wts = np.cos((np.arange(n_dir) - (n_dir - 1) / 2.0) * gr.dtheta) ** 2
    wts /= wts.sum()
    for kk, wt in enumerate(wts):
        e[:, :, fi, (kk - (n_dir - 1) // 2) % gr.ntheta] = \
            (wt * blob / (gr.df[fi] * gr.dtheta)).astype(np.float32)
    wlat = np.cos(np.radians(m.lats))[:, None]
    m0_0 = float((gr.total_energy(e.astype(np.float64)) * wlat).sum())

    for _ in range(nsteps):
        e = m.step(e, dt, sources=False)
    p = gr.total_energy(e.astype(np.float64))
    m0_1 = float((p * wlat).sum())
    q = p.sum(axis=1)
    q = q / q.max()
    pk = [i for i in range(1, ny - 1)
          if q[i] > q[i - 1] and q[i] >= q[i + 1] and q[i] > 0.15]
    dips = [(0.5 * (q[a] + q[b]) - q[a:b + 1].min()) / max(0.5 * (q[a] + q[b]), 1e-12)
            for a, b in zip(pk[:-1], pk[1:])]
    ripple = float(np.mean(dips)) if dips else 0.0
    # "유의 로브" = 15% 이상 깊은 골로 갈라진 봉우리. 얕은 잔물결은 하나로 센다
    # (리플이 5% 수준이면 육안·산출물에서 부채살로 보이지 않는다).
    n_sig = 1 + sum(1 for d in dips if d >= 0.15) if pk else 0
    return {"n_lobes": len(pk), "n_lobes_sig": n_sig, "ripple": ripple,
            "mass_ratio": m0_1 / m0_0, "min": float(p.min())}


def test_gse_averaging_removes_garden_sprinkler_lobes():
    """1차 도너셀 L1 기준선: 로브 3개 / 리플 0.493 (L2 는 5개 / 0.933).

    수치확산을 없앤 2차 스킴은 GSE 를 그대로 드러내므로 평균화가 필수다
    (WW3 매뉴얼 §3.4.6: 고차 스킴은 "sufficiently free of numerical diffusion
    for the ... Garden Sprinkler Effect to occur").
    """
    off = _fan("uno2", "none")
    on = _fan("uno2", "tolman")
    assert on["ripple"] <= 0.30, (f"리플 {on['ripple']:.3f} "
                                  f"(완화 없음 {off['ripple']:.3f})")
    assert on["n_lobes_sig"] <= 2, (f"유의 로브 {on['n_lobes_sig']}개 "
                                    f"(완화 없음 {off['n_lobes_sig']}개)")
    assert on["ripple"] < off["ripple"], "평균화가 리플을 줄이지 못함"


def test_gse_averaging_conserves_mass_and_positivity():
    r = _fan("uno2", "tolman")
    assert abs(r["mass_ratio"] - 1.0) < 0.01, f"질량비 {r['mass_ratio']:.5f}"
    assert r["min"] >= 0.0


# ── F. GSE 스텐실 불변량 (운영 L1 형상, 초고속) ────────────────────
@pytest.mark.parametrize("gamma", [1.0, 1.5, 2.5, 4.0])
def test_gse_stencil_invariants(gamma):
    gr = SpectralGrid()
    lats = np.arange(12.5, 46.0 + 1e-9, 0.25)
    dx = R_EARTH * np.cos(np.radians(lats)) * math.radians(0.25)
    dy = R_EARTH * math.radians(0.25)
    w = gse_stencil(dx, dy, gr.f, gr.ratio, gr.theta, gr.dtheta, G_STANDARD,
                    dt=619.0, gamma=gamma)
    assert w.min() >= 0.0, f"음수 가중치 {w.min():.3e} — 양수보존 위반"
    assert np.abs(w.sum(axis=(-1, -2)) - 1.0).max() < 1e-12, "질량 보존 위반"
    # 중심 가중치 > 0 은 부분셀 조건 |p|,|q| < 1 의 필요조건 (γ·0.27·Cr < 1)
    assert w[..., 1, 1].min() > 0.0
