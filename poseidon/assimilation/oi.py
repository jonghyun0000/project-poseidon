"""Hs 공간 최적내삽 (Optimal Interpolation) — 해양 마스크·시선제약 포함.

x_a = x_b + BH^T (H B H^T + R)^{-1} (y - H x_b)          (Gandin 1963; Daley 1991)

운영 파랑 모델의 표준 동화 방식이다: ECMWF IFS Cy45r1 Part VII Ch.4,
Lionello, Gunther & Janssen (1992) JGR 97(C9) 14453,
Aouf et al. (2015, CMEMS MFWAM), Greenslade & Young (2004) Ocean Modelling 6, 97.

EnKF/3D-Var 를 쓰지 않은 이유 (설계 판정):
- EnKF: L1 1런 4분 x 20~50 멤버 = 사이클당 80~200분. 6시간 주기에 불가.
- EnOI: 정적 앙상블을 만들 아카이브가 11 사이클뿐.
- 3D-Var: E 공간 B 행렬 1.76e7 자유도를 명세해야 하는데 관측이 41개 스칼라뿐이라
  해가 전적으로 B 로 결정된다 = OI 와 같은 가정에 비용만 10~100배.
여기서 실제로 푸는 선형계는 (H B H^T + R): p x p, p <= 45 하나뿐이다.

**육지 마스크 규약** (이 프로젝트에서 네 번 반복된 결함 — CLAUDE.md 함정 1):
1. 관측 -> 격자 사상은 collocate.sea_normalized 를 그대로 재사용한다.
2. 증분은 sea 셀에만, 경계 링(가장자리 1셀) 제외.
3. 관측과 격자점 사이 대권 선분이 육지를 지나면 그 쌍의 기여를 0으로 한다(시선제약).
   PSD 보장을 위해 시선제약은 BH^T 에만 적용하고 HBH^T 에는 적용하지 않는다
   (HBH^T 는 유효 상관함수 x Gaspari-Cohn 국지화의 Schur 곱이라 항상 PSD).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from poseidon.assimilation.observations import Observation
from poseidon.assimilation.params import (
    GAMMA_BJ,
    HS_B_FLOOR,
    MIN_SEA_WEIGHT,
    AssimConfig,
)
from poseidon.physics.constants import R_EARTH
from poseidon.validation.collocate import corner_weights, sea_normalized

log = logging.getLogger("poseidon.assimilation.oi")

Arr = NDArray[np.float64]


# ── 상관함수 ──────────────────────────────────────────────────────────────────
def correlation(r_km: Arr, length_km: float, shape: str) -> Arr:
    """등방 배경오차 상관.

    soar : (1 + r/L) exp(-r/L)   — Greenslade & Young (2004) 채택형(2차 자기회귀)
    exp  : exp(-r/L)             — ECMWF IFS Cy45r1 운영형, Lionello et al. (1992)
    gauss: exp(-r^2/(2 L^2))
    """
    x = np.asarray(r_km, dtype=np.float64) / float(length_km)
    if shape == "soar":
        return (1.0 + x) * np.exp(-x)
    if shape == "exp":
        return np.exp(-x)
    if shape == "gauss":
        return np.exp(-0.5 * x * x)
    raise ValueError(f"corr_shape must be soar|exp|gauss, got {shape!r}")


def gaspari_cohn(r_km: Arr, support_km: float) -> Arr:
    """Gaspari & Cohn (1999) QJRMS 125, 723 식 (4.10) 5차 조각다항식.

    유한 지지(r >= support 에서 정확히 0)의 유효 상관함수. 상관함수와의 Schur 곱이
    PSD 를 보존하므로(Schur 곱 정리) 국지화를 여기에 맡기고 하드 컷오프를 피한다.
    c = support/2 로 두면 지지반경이 support 가 된다.
    """
    c = float(support_km) / 2.0
    z = np.asarray(r_km, dtype=np.float64) / max(c, 1e-9)
    out = np.zeros_like(z)
    m1 = z <= 1.0
    m2 = (z > 1.0) & (z <= 2.0)
    z1 = z[m1]
    out[m1] = (((-0.25 * z1 + 0.5) * z1 + 0.625) * z1 - 5.0 / 3.0) * z1 * z1 + 1.0
    z2 = z[m2]
    out[m2] = ((((z2 / 12.0 - 0.5) * z2 + 0.625) * z2 + 5.0 / 3.0) * z2
               - 5.0) * z2 + 4.0 - 2.0 / (3.0 * np.maximum(z2, 1e-9))
    return np.maximum(out, 0.0)


def haversine_km(lat1, lon1, lat2, lon2) -> Arr:
    """대권 거리 [km] (R_EARTH: poseidon.physics.constants)."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp = p2 - p1
    dl = np.radians(np.asarray(lon2, dtype=np.float64) - np.asarray(lon1, dtype=np.float64))
    a = np.sin(dp / 2.0) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2.0) ** 2
    return 2.0 * (R_EARTH / 1000.0) * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


# ── 관측 연산자 ───────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ObsSlot:
    """격자에 사상된 관측 1건."""

    station_id: str
    lat: float
    lon: float
    obs: float            # 편차 보정 후 관측 Hs [m]
    obs_raw: float        # 원 관측 [m]
    bias: float           # 뺀 관측소 편차 b_s [m]
    background: float     # H(Hs_b) [m]
    land_frac: float
    sigma_o: float
    sigma_b: float

    @property
    def innovation(self) -> float:
        return self.obs - self.background


def build_obs_slots(model, hs_b: Arr, observations: list[Observation],
                    cfg: AssimConfig, bias: dict[str, float] | None = None,
                    ) -> tuple[list[ObsSlot], list[dict[str, object]]]:
    """관측을 격자에 사상하고 채택/폐기를 판정한다.

    폐기 사유: 도메인 밖 · 경계 링 인접 · 육지 가중 초과 · 배경 결측.
    """
    bias = bias or {}
    lats, lons = model.lats, model.lons
    lat0, lat1 = float(lats[0]), float(lats[-1])
    lon0, lon1 = float(lons[0]), float(lons[-1])
    # 경계 링(1셀)은 Dirichlet 이라 증분이 0 이다 -> 그 안쪽 2셀 여유를 둔다.
    margin_y = 2.0 * float(lats[1] - lats[0])
    margin_x = 2.0 * float(lons[1] - lons[0])

    slots: list[ObsSlot] = []
    rejected: list[dict[str, object]] = []
    for o in observations:
        if not (lat0 + margin_y <= o.lat <= lat1 - margin_y
                and lon0 + margin_x <= o.lon <= lon1 - margin_x):
            rejected.append({"station_id": o.station_id, "reason": "outside-domain"})
            continue
        jj, ii, w = corner_weights(lats, lons, o.lat, o.lon)
        bg, land_frac, _ = sea_normalized(hs_b[jj, ii], w, model.sea[jj, ii],
                                          MIN_SEA_WEIGHT)
        if not np.isfinite(bg):
            rejected.append({"station_id": o.station_id, "reason": "all-land"})
            continue
        if land_frac > cfg.max_land_frac:
            rejected.append({"station_id": o.station_id, "reason": "land-frac",
                             "land_frac": float(land_frac)})
            continue
        if not np.isfinite(o.value) or o.value < 0.0:
            rejected.append({"station_id": o.station_id, "reason": "bad-value"})
            continue
        b = float(bias.get(o.station_id, 0.0)) if cfg.bias_correction else 0.0
        slots.append(ObsSlot(
            station_id=o.station_id, lat=o.lat, lon=o.lon,
            obs=float(o.value) - b, obs_raw=float(o.value), bias=b,
            background=float(bg), land_frac=float(land_frac),
            sigma_o=cfg.sigma_o(), sigma_b=float(cfg.sigma_b(bg)),
        ))
    return slots, rejected


# ── 시선제약 ──────────────────────────────────────────────────────────────────
def line_of_sight(sea: NDArray[np.bool_], lats: Arr, lons: Arr,
                  olat: float, olon: float,
                  cj: NDArray[np.int64], ci: NDArray[np.int64]) -> NDArray[np.bool_]:
    """관측점 -> 각 후보 셀 선분이 전부 바다인지 (격자 인덱스 공간 직선 표본).

    육지(반도·섬)를 가로지르는 상관은 물리적으로 성립하지 않는다. WW3/SWAN 계열
    연안 OI 의 관행이며, 이 프로젝트의 반복된 육지 오염 결함에 대한 직접 방어다.
    """
    ny, nx = sea.shape
    fy = (olat - lats[0]) / (lats[1] - lats[0])
    fx = (olon - lons[0]) / (lons[1] - lons[0])
    if cj.size == 0:
        return np.zeros(0, dtype=bool)
    span = float(max(np.abs(cj - fy).max(), np.abs(ci - fx).max()))
    nsteps = int(min(max(int(np.ceil(2.0 * span)) + 2, 3), 512))
    t = np.linspace(0.0, 1.0, nsteps)[None, :]
    py = fy + t * (cj[:, None] - fy)
    px = fx + t * (ci[:, None] - fx)
    jj = np.clip(np.rint(py).astype(np.int64), 0, ny - 1)
    ii = np.clip(np.rint(px).astype(np.int64), 0, nx - 1)
    return sea[jj, ii].all(axis=1)


# ── OI 본체 ───────────────────────────────────────────────────────────────────
def analyse_hs(model, hs_b: Arr, slots: list[ObsSlot], cfg: AssimConfig,
               ) -> tuple[Arr, Arr, dict[str, object]]:
    """Hs 배경장에 OI 증분을 더한 분석장.

    반환 (hs_a, d_hs, 진단). 관측이 없으면 증분은 정확히 0 (항등).
    """
    ny, nx = hs_b.shape
    d_hs = np.zeros((ny, nx), dtype=np.float64)
    diag: dict[str, object] = {"n_obs": len(slots)}
    if not slots:
        diag["status"] = "no-obs"
        return hs_b.copy(), d_hs, diag

    # [F2] 조대오차 검사 (ECMWF IFS 관행 k=3~4).
    # 실측: 관측 하나를 +5 m 오염시키자 폐기 0건으로 통과해 증분 |max|가
    # 0.25 → 1.47 m로 폭주하고 인접 관측소 분석값이 0.49 m 끌려갔다.
    # 상류 QC(range/spike/stuck)는 지속 offset(생물부착 등)을 걸러내지 못한다.
    kept, gross = [], []
    for s_ in slots:
        thr = cfg.gross_k * float(np.hypot(s_.sigma_b, s_.sigma_o))
        (gross if abs(float(s_.innovation)) > thr else kept).append(s_)
    if gross:
        diag["rejected_gross"] = [
            {"station_id": g.station_id, "innovation": round(float(g.innovation), 3),
             "threshold": round(cfg.gross_k * float(np.hypot(g.sigma_b, g.sigma_o)), 3)}
            for g in gross]
    slots = kept
    diag["n_obs_gross_rejected"] = len(gross)
    diag["n_obs"] = len(slots)
    if not slots:
        diag["status"] = "all-obs-rejected-gross"
        return hs_b.copy(), d_hs, diag

    p = len(slots)
    olat = np.array([s.lat for s in slots])
    olon = np.array([s.lon for s in slots])
    sb_o = np.array([s.sigma_b for s in slots])
    so_o = np.array([s.sigma_o for s in slots])
    d = np.array([s.innovation for s in slots])

    support = cfg.loc_factor * cfg.corr_len_km

    # HBH^T: 관측-관측 (시선제약 미적용 -> PSD 보장)
    r_oo = haversine_km(olat[:, None], olon[:, None], olat[None, :], olon[None, :])
    rho_oo = correlation(r_oo, cfg.corr_len_km, cfg.corr_shape) * gaspari_cohn(r_oo, support)
    hbht = (sb_o[:, None] * sb_o[None, :]) * rho_oo
    a = hbht + np.diag(so_o ** 2)
    try:
        z = np.linalg.solve(a, d)                      # (HBH^T + R)^{-1} d
    except np.linalg.LinAlgError:                      # 특이 -> 최소제곱 폴백
        z = np.linalg.lstsq(a, d, rcond=None)[0]
        diag["solver"] = "lstsq"
    else:
        diag["solver"] = "solve"
    diag["chi2"] = float(d @ z / p)                    # Desroziers 계열 자기일관성 (1.0 목표)
    # [F2] chi2를 계산만 하고 소비하지 않으면 B/R 예산이 크게 틀린 사이클에서도
    # 증분이 그대로 들어간다. 실측 사이클별 chi2 범위 0.57~4.87.
    if diag["chi2"] > cfg.chi2_max:
        diag["status"] = "rejected-chi2"
        return hs_b.copy(), d_hs, diag

    # 갱신 대상: 바다 & 경계 링 제외
    active = model.sea.copy()
    active[0, :] = active[-1, :] = False
    active[:, 0] = active[:, -1] = False
    aj, ai = np.nonzero(active)
    glat = model.lats[aj]
    glon = model.lons[ai]
    sb_g = np.asarray(cfg.sigma_b(hs_b[aj, ai]), dtype=np.float64)

    incr = np.zeros(aj.size, dtype=np.float64)
    n_blocked = 0
    for o in range(p):
        r = haversine_km(olat[o], olon[o], glat, glon)
        near = r < support
        if not near.any():
            continue
        rho = (correlation(r[near], cfg.corr_len_km, cfg.corr_shape)
               * gaspari_cohn(r[near], support))
        if cfg.line_of_sight:
            vis = line_of_sight(model.sea, model.lats, model.lons,
                                olat[o], olon[o], aj[near], ai[near])
            if not vis.any():
                # 관측 셀 자체가 육지로 판정되는 병리 — 시선제약을 그 관측에만 해제
                log.warning("%s: 시선제약이 전 격자점을 차단 — 해당 관측만 해제",
                            slots[o].station_id)
                vis = np.ones(vis.shape, dtype=bool)
            n_blocked += int((~vis).sum())
            rho = rho * vis
        incr[near] += sb_g[near] * sb_o[o] * rho * z[o]

    d_hs[aj, ai] = incr
    diag["n_los_blocked_pairs"] = n_blocked
    diag["support_km"] = float(support)

    # 쇄파 한계·양수성: Hs_a <= gamma_bj * depth (엔진 depth_breaking 과 같은 상수)
    hs_a = hs_b + d_hs
    hs_a = np.clip(hs_a, 0.0, GAMMA_BJ * model.depth)
    hs_a[~model.sea] = hs_b[~model.sea]
    d_hs = hs_a - hs_b
    diag.update({
        "incr_abs_mean_m": float(np.abs(d_hs[model.sea]).mean()),
        "incr_abs_max_m": float(np.abs(d_hs).max()),
        "incr_rms_m": float(np.sqrt((d_hs[model.sea] ** 2).mean())),
        "n_cells_touched": int((np.abs(d_hs) > 1e-6).sum()),
        "innov_mean_m": float(d.mean()), "innov_rms_m": float(np.sqrt((d ** 2).mean())),
        "status": "ok",
    })
    return hs_a, d_hs, diag


def energy_ratio(hs_a: Arr, hs_b: Arr, model, cfg: AssimConfig,
                 m0_b: Arr | None = None) -> Arr:
    """셀별 에너지 배율 q = Hs_a/Hs_b (클립 + ln q 평활, 육지 q=1).

    평활은 해양셀 한정 1-2-1 필터(마스크 정규화). 증분이 0 이면 ln q = 0 이 보존되어
    항등성이 정확히 유지된다.

    경계 링(가장자리 1셀)은 analyse_hs 와 **같은 정의**로 제외한다. analyse_hs 가 링에서
    d_hs=0 을 만들어도 평활이 내부의 ln q 를 링으로 번지게 하므로, 여기서 다시 막지
    않으면 Dirichlet 경계가 오염된다(실측 누출 6.8e-4 m, 20260821T00).
    """
    q = np.ones_like(hs_b, dtype=np.float64)
    ok = model.sea & (hs_b > HS_B_FLOOR)
    ok[0, :] = ok[-1, :] = False
    ok[:, 0] = ok[:, -1] = False
    if m0_b is not None:
        ok &= m0_b > 1e-6
    q[ok] = np.clip(hs_a[ok] / hs_b[ok], cfg.q_min, cfg.q_max)
    lnq = np.log(q)
    w = model.sea.astype(np.float64)
    for _ in range(int(cfg.smooth_passes)):
        lnq = _smooth121_masked(lnq, w)
    # 평활이 미소에너지 셀로 번지면 무한 증폭이 되므로 그 셀은 q=1 로 되돌린다
    lnq[~ok] = 0.0
    return np.exp(lnq)


def _smooth121_masked(a: Arr, w: Arr) -> Arr:
    """해양 마스크 정규화 1-2-1 x 1-2-1 (분리형). 육지 셀은 가중 0 이라 새지 않는다."""
    def pass1(x, axis):
        num = 2.0 * x + np.roll(x, 1, axis) + np.roll(x, -1, axis)
        # 가장자리는 롤 대신 자기 자신 (반사 경계)
        sl_lo = [slice(None)] * x.ndim
        sl_hi = [slice(None)] * x.ndim
        sl_lo[axis] = 0
        sl_hi[axis] = -1
        num[tuple(sl_lo)] = (2.0 * x[tuple(sl_lo)] + x[tuple(sl_lo)]
                             + np.take(x, 1, axis=axis))
        num[tuple(sl_hi)] = (2.0 * x[tuple(sl_hi)] + x[tuple(sl_hi)]
                             + np.take(x, -2, axis=axis))
        return num

    def _div(num, den):
        out = np.zeros_like(num)
        np.divide(num, den, out=out, where=den > 1e-12)
        return out

    num, den = pass1(a * w, 0), pass1(w, 0)
    tmp = _div(num, den) * w
    num, den = pass1(tmp, 1), pass1(w, 1)
    return _div(num, den)
