"""L2 폭풍해일 사이클 — SWE 엔진 + GFS 바람응력·기압 강제, 부산 검조소 검증.

surge = 기상 성분만 계산 (조석은 조화 모듈이 별도 제공, 선형 중첩 1차 근사).
검증: IOC 부산 관측 − 조화 조석 예측 = 관측 해일 성분 ↔ 모델 η(부산 셀).

사용:
  python -m poseidon.scheduler.surge_cycle --cycle 20260804T00 --hours 12
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
from poseidon.engines.shallow_water import AtmosForcing, SWESolver, SWEState
from poseidon.ingest.adapters.base import lineage
from poseidon.physics.constants import RHO_AIR, coriolis_f

log = logging.getLogger("poseidon.surge_cycle")

DOMAIN = {"west": 124.0, "east": 133.0, "south": 31.0, "north": 39.0, "res": 0.05}
BUSAN = (35.096, 129.035)
ETOPO = "data/static/etopo2022_60s_east_asia.nc"


def _wind_stress(u10: np.ndarray, v10: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    spd = np.hypot(u10, v10)
    cd = (0.8 + 0.065 * spd) * 1e-3                       # Wu (1982)
    tau = RHO_AIR * cd * spd
    return tau * u10, tau * v10


def run_surge(cycle: str, hours: float = 12.0) -> dict:
    catalog = Catalog(settings.catalog_path)
    forcing_ds = xr.open_zarr(
        catalog.find_datasets("forcing", settings.domain_name, cycle)[-1]["uri"],
        consolidated=False)

    d = DOMAIN
    lat_corner = np.arange(d["south"], d["north"] + 1e-9, d["res"])
    lon_corner = np.arange(d["west"], d["east"] + 1e-9, d["res"])
    etopo = xr.open_dataset(ETOPO)
    b = etopo.z.interp(lat=xr.DataArray(lat_corner, dims="cy"),
                       lon=xr.DataArray(lon_corner, dims="cx")).values
    lat_c = 0.5 * (lat_corner[:-1] + lat_corner[1:])
    lon_c = 0.5 * (lon_corner[:-1] + lon_corner[1:])
    ny, nx = len(lat_c), len(lon_c)

    dx = 111_320.0 * np.cos(np.radians(lat_c.mean())) * d["res"]
    dy = 111_320.0 * d["res"]
    f2d = np.array([[coriolis_f(la)] * nx for la in lat_c])
    solver = SWESolver(b, dx=dx, dy=dy, f_coriolis=f2d,
                       c_friction=2.5e-3, bc="open")
    s = solver.state_at_rest(0.0)

    # 개방경계 스펀지층: 구배-0 경계는 코리올리 흐름과 결합해 에너지를 주입하므로
    # (지수 불안정 실측, 2026-08-04) 폭 12셀 코사인 램프로 상태를 0으로 완화한다.
    # Flather 방사 조건은 조석 경계 결합 시 솔버 차원에서 도입 (백로그).
    n_sp = 12
    ramp = np.zeros((ny, nx))
    for k in range(n_sp):
        val = 0.5 * (1 + np.cos(np.pi * k / n_sp))        # 경계 1 → 내부 0
        ramp[k, :] = np.maximum(ramp[k, :], val)
        ramp[ny - 1 - k, :] = np.maximum(ramp[ny - 1 - k, :], val)
        ramp[:, k] = np.maximum(ramp[:, k], val)
        ramp[:, nx - 1 - k] = np.maximum(ramp[:, nx - 1 - k], val)
    sponge_tau = 1800.0

    jb = int(np.argmin(np.abs(lat_c - BUSAN[0])))
    ib = int(np.argmin(np.abs(lon_c - BUSAN[1])))
    # 부산 셀이 육지면 가장 가까운 해양 셀로
    if not (solver.depth(s)[jb, ib] > 0):
        sea_idx = np.argwhere(solver.depth(s) > 1.0)
        k = np.argmin((sea_idx[:, 0] - jb) ** 2 + (sea_idx[:, 1] - ib) ** 2)
        jb, ib = map(int, sea_idx[k])
    log.info("Busan cell: %.3fN %.3fE depth=%.0fm",
             lat_c[jb], lon_c[ib], solver.depth(s)[jb, ib])

    interp_pts = {"latitude": xr.DataArray(lat_c, dims="y"),
                  "longitude": xr.DataArray(lon_c, dims="x")}
    times, etas = [], []
    t, next_forcing, next_out = 0.0, 0.0, 0.0
    forcing = None
    while t < hours * 3600.0 - 1e-9:
        if t >= next_forcing:
            w = forcing_ds.interp(time=forcing_ds.time[0].values
                                  + np.timedelta64(int(t), "s")).interp(**interp_pts)
            taux, tauy = _wind_stress(np.nan_to_num(w.u10.values),
                                      np.nan_to_num(w.v10.values))
            forcing = AtmosForcing(taux=taux, tauy=tauy,
                                   pa=np.nan_to_num(w.msl.values, nan=101325.0))
            next_forcing += 3600.0
        t_prev = t
        s = solver.step(s, dt_max=min(next_forcing, next_out + 1) - t + 1.0,
                        forcing=forcing)
        t = s.t
        relax = ramp * ((t - t_prev) / sponge_tau)
        wet = s.w > solver.bc_cell
        s = SWEState(w=np.where(wet, s.w * (1.0 - relax), s.w),  # η→0 완화, 육지 불변
                     hu=s.hu * (1.0 - relax), hv=s.hv * (1.0 - relax), t=s.t)
        if t >= next_out:
            times.append(t)
            etas.append(float(s.w[jb, ib]))
            next_out += 600.0
            if int(t) % 1800 < 600:
                log.info("t=%5.2fh dt=%5.2fs eta_busan=%+.3f m",
                         t / 3600, t - t_prev, etas[-1] - etas[0])

    eta = np.array(etas) - etas[0]                        # 초기 스핀 기준화
    out = xr.Dataset(
        {"eta_busan": (("t",), eta.astype(np.float32))},
        coords={"t": np.array(times)},
        attrs={"cycle": cycle, "engine": "swe-L2-surge",
               "busan_cell": [float(lat_c[jb]), float(lon_c[ib])],
               **lineage("swe-L2-surge", {"cycle": cycle, "hours": hours})},
    )
    path = settings.zarr_root / "forecast" / settings.domain_name / f"{cycle}_surge.zarr"
    out.to_zarr(path, mode="w", consolidated=False)
    catalog.register_dataset(collection="forecast", domain=settings.domain_name,
                             cycle=cycle, uri=str(path), source_id="swe-L2-surge",
                             retrieval={"hours": hours})
    stats = {"eta_min": float(eta.min()), "eta_max": float(eta.max()),
             "eta_final": float(eta[-1]), "n_out": len(eta)}
    log.info("surge stats: %s", stats)
    return {"times_s": times, "eta": eta.tolist(), **stats}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cycle", required=True)
    parser.add_argument("--hours", type=float, default=12.0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-5s %(name)s %(message)s")
    run_surge(args.cycle, args.hours)
    return 0


if __name__ == "__main__":
    sys.exit(main())
