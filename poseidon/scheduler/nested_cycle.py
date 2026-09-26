"""L1(0.25°) → L2(0.05° 연안) 단방향 스펙트럼 중첩 파랑 사이클 (Phase 9-2).

- 두 RegionalWaveModel을 같은 프로세스에서 동시 전진 (JAX 커널 2개)
- L2 경계 링 = L1 전체 스펙트럼 E(f,θ)의 겹선형 보간 (적분 파라미터 재구성 아님)
- L2는 L1 스텝당 여러 번 서브사이클 (dt2 < dt1), 바람은 GFS를 L2 격자에 직접 보간
- 산출: forecast 컬렉션 `{cycle}_wave_L2.zarr` (source_id spectral_wave-L2) + L2 리드별 지표

사용: python -m poseidon.scheduler.nested_cycle --cycle 20260821T00 --hours 24
"""

from __future__ import annotations

import argparse
import logging
import sys

import numpy as np
import pandas as pd
import xarray as xr

from poseidon.core.catalog import Catalog
from poseidon.core.config import settings
from poseidon.engines.spectral_wave import grid as spec
from poseidon.engines.spectral_wave.grid import SpectralGrid
from poseidon.engines.spectral_wave.jax_kernel import JaxRegionalKernel, ring_mask
from poseidon.engines.spectral_wave.regional import RegionalWaveModel
from poseidon.scheduler.wave_cycle import (
    DOMAIN,
    ETOPO_PATH,
    SNAP_HOURS,
    _from_compass_from,
    _load_depth,
    _open_latest,
)

log = logging.getLogger("poseidon.nested")

L2_DOMAIN = {"west": 124.0, "east": 132.0, "south": 32.0, "north": 39.0, "res": 0.05}
L2_HMIN = 3.0


def bilinear_weights(src_lats, src_lons, lats, lons):
    """목표 격자(lats×lons) 각 셀의 원천 격자 인덱스·가중치 (정규 격자 가정)."""
    dy = src_lats[1] - src_lats[0]
    dx = src_lons[1] - src_lons[0]
    fy = (lats - src_lats[0]) / dy
    fx = (lons - src_lons[0]) / dx
    j0 = np.clip(np.floor(fy).astype(int), 0, len(src_lats) - 2)
    i0 = np.clip(np.floor(fx).astype(int), 0, len(src_lons) - 2)
    wy = np.clip(fy - j0, 0, 1)
    wx = np.clip(fx - i0, 0, 1)
    return j0, i0, wy, wx


SW_MIN = 0.05        # 해상 가중 하한 — 이하이면 사실상 완전 육지 스텐실로 보고 0


def interp_cells(e1, jj, ii, wyy, wxx, sea1=None, sw_min: float = SW_MIN):
    """임의 목표 셀 집합에 대한 **해상 가중 정규화** 겹선형 보간.

    jj, ii, wyy, wxx : 동일 형상의 원천 좌하단 인덱스와 (y, x) 소수부.
    sea1 : 원천(L1) 해상 마스크. 주면 스텐실 4점 중 해상점만으로 정규화한다.

        E_c = Σ w_i·sea_i·E_i / Σ w_i·sea_i      (Σ w_i·sea_i > sw_min)
            = 0                                   (그 외 — 완전 육지 스텐실)

    L1은 육지에서 E=0 (regional.py `e[~self.sea]=0`)이므로 순수 겹선형은 육지
    스텐실 점의 0을 그대로 섞어 L2 해상 셀 에너지를 희석한다. 해상 가중 정규화는
    WW3/SWAN 중첩(nesting)의 표준 처리다 (Tolman 2009 WW3 Tech.Note 316 §3.7
    경계 보간; Booij et al. 1999 SWAN — 육지점 제외 후 재정규화).
    sea1=None이면 정규화를 건너뛰어 기존 순수 겹선형과 비트 단위로 동일하다.

    반환: jj.shape + (nf, nθ) float32. 가중 합산은 기존 구현과 같이 float64로 하되,
    셀 축을 청크로 잘라 누적한다 — 전체 필드를 한 번에 만들던 기존 방식의
    피크 메모리(≈0.5 GB)를 청크당 ~60 MB로 낮춘다.
    """
    shp = np.shape(jj)
    jj = np.asarray(jj).ravel()
    ii = np.asarray(ii).ravel()
    wyy = np.asarray(wyy, np.float64).ravel()
    wxx = np.asarray(wxx, np.float64).ravel()
    nsp = e1.shape[2:]                                  # (nf, nθ)
    bc = (-1,) + (1,) * len(nsp)                        # 셀축 브로드캐스트 형상
    n = jj.size
    out = np.empty((n,) + nsp, np.float32)
    per = max(1, int(np.prod(nsp, dtype=np.int64)))
    chunk = max(1, int(3e7) // (per * 8))               # 청크당 ~30MB (E는 …×32×36)
    for a in range(0, n, chunk):
        b = min(a + chunk, n)
        j, i, wy_, wx_ = jj[a:b], ii[a:b], wyy[a:b], wxx[a:b]
        acc = np.zeros((b - a,) + nsp, np.float64)
        sw = np.zeros(b - a, np.float64)
        for dj, di, w in ((0, 0, (1 - wy_) * (1 - wx_)), (0, 1, (1 - wy_) * wx_),
                          (1, 0, wy_ * (1 - wx_)), (1, 1, wy_ * wx_)):
            if sea1 is not None:
                w = w * sea1[j + dj, i + di]
            acc += w.reshape(bc) * e1[j + dj, i + di]
            sw += w
        if sea1 is not None:
            acc *= np.where(sw > sw_min, 1.0 / np.maximum(sw, 1e-12), 0.0).reshape(bc)
        out[a:b] = acc
    return out.reshape(tuple(shp) + nsp)


def interp_field(e1, j0, i0, wy, wx, sea1=None, sw_min: float = SW_MIN):
    """E1(ny1,nx1,nf,nθ) → 목표 격자 전체 (ny2,nx2,nf,nθ) 해상 가중 정규화 겹선형."""
    J0, I0 = np.meshgrid(j0, i0, indexing="ij")
    WY, WX = np.meshgrid(wy, wx, indexing="ij")
    return interp_cells(e1, J0, I0, WY, WX, sea1, sw_min)


def run_nested(cycle: str, hours: float = 24.0) -> dict:
    catalog = Catalog(settings.catalog_path)
    forcing = _open_latest(catalog, "forcing", cycle)
    waves = _open_latest(catalog, "boundary", cycle)
    grid = SpectralGrid()

    # ── L1
    d = DOMAIN
    lats1 = np.arange(d["south"], d["north"] + 1e-9, d["res"])
    lons1 = np.arange(d["west"], d["east"] + 1e-9, d["res"])
    m1 = RegionalWaveModel(lats1, lons1, _load_depth(lats1, lons1), grid, h_min=10.0)
    # ── L2
    d2 = L2_DOMAIN
    lats2 = np.arange(d2["south"], d2["north"] + 1e-9, d2["res"])
    lons2 = np.arange(d2["west"], d2["east"] + 1e-9, d2["res"])
    m2 = RegionalWaveModel(lats2, lons2, _load_depth(lats2, lons2), grid, h_min=L2_HMIN)
    j0, i0, wy, wx = bilinear_weights(lats1, lons1, lats2, lons2)

    def fields_at(ds_t, lats, lons):
        i = {"latitude": xr.DataArray(lats, dims="y"), "longitude": xr.DataArray(lons, dims="x")}
        return (ds_t.swh.interp(**i).values, ds_t.perpw.interp(**i).values,
                _from_compass_from(ds_t.dirpw.interp(**i).values))

    e1 = m1.spectra_from_integrals(*fields_at(waves.isel(time=0), lats1, lons1))
    e2 = interp_field(e1, j0, i0, wy, wx, m1.sea)     # 초기장: 전체 필드 필요
    e2[~m2.sea] = 0.0
    e1_bc, e2_bc = e1.copy(), e2.copy()

    t_axis = ((forcing.time - forcing.time[0]) / np.timedelta64(1, "s")).values

    def wind_at(t_sec, lats, lons):
        w = forcing.interp(time=forcing.time[0].values + np.timedelta64(int(t_sec), "s"))
        i = {"latitude": xr.DataArray(lats, dims="y"), "longitude": xr.DataArray(lons, dims="x")}
        u, v = w.u10.interp(**i).values, w.v10.interp(**i).values
        return np.nan_to_num(np.hypot(u, v)), np.nan_to_num(np.arctan2(v, u))

    dt1 = m1.cfl_dt()
    n_sub = int(np.ceil(dt1 / m2.cfl_dt()))
    dt2 = dt1 / n_sub
    k1, k2 = JaxRegionalKernel(m1), JaxRegionalKernel(m2)
    k1.prepare(m1, dt1)
    k2.prepare(m2, dt2)
    ring1, ring2 = ring_mask(m1.ny, m1.nx), ring_mask(m2.ny, m2.nx)
    # L2 경계 링(약 600셀)만 매 스텝 갱신 — 커널은 e2_bc를 ring에서만 읽으므로
    # 전체 필드(141×161×32×36) 재보간은 낭비다 (링 셀 수 = 전체의 2.6%).
    ry, rx = np.nonzero(ring2)
    r_j0, r_i0, r_wy, r_wx = j0[ry], i0[rx], wy[ry], wx[rx]
    log.info("L1 %dx%d dt=%.0fs | L2 %dx%d dt=%.0fs (x%d sub) | %d L1 steps",
             m1.ny, m1.nx, dt1, m2.ny, m2.nx, dt2, n_sub, int(hours * 3600 / dt1))

    run_id = catalog.create_run(cycle=cycle, engine="wave-L2", domain="korea-0p05") \
        if catalog.get_run(cycle=cycle, engine="wave-L2", domain="korea-0p05") is None \
        else catalog.get_run(cycle=cycle, engine="wave-L2", domain="korea-0p05")["run_id"]
    catalog.set_status(run_id, "RUNNING_L2")

    snaps2, snap_t = [spec.snapshot(grid, e2)], [0.0]
    next_snap, bc_next, t = SNAP_HOURS * 3600, 0.0, 0.0
    n1 = int(round(hours * 3600 / dt1))
    for _ in range(n1):
        if t >= bc_next and t_axis[-1] > 0:                      # L1 경계: GFS-Wave 매시
            ds_t = waves.interp(time=waves.time[0].values
                                + np.timedelta64(int(min(t, float(t_axis[-1]))), "s"))
            e1_bc = m1.spectra_from_integrals(*fields_at(ds_t, lats1, lons1))
            bc_next += 3600.0
        tt = min(t, float(t_axis[-1]))
        u1, d1 = wind_at(tt, lats1, lons1)
        e1 = np.asarray(k1.step(e1, dt1, u1, d1, e1_bc, ring1))
        # L2 경계 = L1 스펙트럼 보간 (이 L1 스텝 동안 고정) — 링 셀만 갱신
        e2_bc[ry, rx] = interp_cells(e1, r_j0, r_i0, r_wy, r_wx, m1.sea)
        u2, d2w = wind_at(tt, lats2, lons2)
        for _k in range(n_sub):
            e2 = np.asarray(k2.step(e2, dt2, u2, d2w, e2_bc, ring2))
        t += dt1
        if t >= next_snap - 1e-6:
            snap_t.append(round(t / 3600, 2))
            snaps2.append(spec.snapshot(grid, e2))
            next_snap += SNAP_HOURS * 3600

    leads = np.array(snap_t, dtype=float)
    valid = pd.Timestamp(waves.time[0].values) + pd.to_timedelta(leads, unit="h")
    _dims = ("lead", "latitude", "longitude")
    out = xr.Dataset({v: (_dims, np.stack([s[v] for s in snaps2]).astype(np.float32))
                      for v in spec.SNAPSHOT_VARS},
                     coords={"lead": leads, "latitude": lats2, "longitude": lons2,
                             "valid_time": ("lead", valid)},
                     attrs={"cycle": cycle, "engine": "spectral_wave-L2-0.05deg",
                            "dir_convention": "coming_from, deg true, clockwise",
                            "period_defs": "tp=peak; tm01=m0/m1, tm02=sqrt(m0/m2) via grid.derive",
                            "bc_interp": "sea-normalized-v1", "sw_min": SW_MIN,
                            "advection": m2.advection, "gse": m2.gse,
                            "gse_gamma": float(m2.gse_gamma),
                            "produced_at": pd.Timestamp.utcnow().isoformat(timespec="seconds"),
                            "parent": "spectral_wave-L1-0.25deg"})
    path = settings.zarr_root / "forecast" / settings.domain_name / f"{cycle}_wave_L2.zarr"
    out.to_zarr(path, mode="w", consolidated=False)
    catalog.register_dataset(collection="forecast", domain=settings.domain_name, cycle=cycle,
                             uri=str(path), source_id="spectral_wave-L2",
                             retrieval={"hours": hours, "l2_domain": L2_DOMAIN,
                                        "bc_interp": "sea-normalized-v1",
                                        "sw_min": SW_MIN,
                                        "advection": m2.advection, "gse": m2.gse,
                                        "gse_gamma": float(m2.gse_gamma)})
    catalog.set_status(run_id, "PUBLISHED")
    log.info("L2 done: hs max %.2f m (lead %.0fh)", float(snaps2[-1]["hs"].max()), leads[-1])
    return {"leads": leads.tolist(), "hs_max_final": float(snaps2[-1]["hs"].max())}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycle", required=True)
    ap.add_argument("--hours", type=float, default=24.0)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-5s %(name)s %(message)s")
    run_nested(a.cycle, a.hours)
    return 0


if __name__ == "__main__":
    sys.exit(main())
