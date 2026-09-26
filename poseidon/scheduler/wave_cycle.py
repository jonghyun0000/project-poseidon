"""L1 지역 파랑 예보 사이클 — 데이터 레이크의 실제 강제장으로 RegionalWaveModel 구동 (M6).

흐름: 카탈로그에서 forcing(GFS 바람)·boundary(GFS-Wave) 조회 → ETOPO 수심 →
GFS-Wave 적분 파라미터로 초기 스펙트럼 재구성 → 경계 링 클램프 + 바람 강제로 전진 →
Hs/Tp/Dir 필드를 forecast 컬렉션에 기록 + GFS-Wave 대비 자기검증 지표 산출.

사용:
  python -m poseidon.scheduler.wave_cycle --cycle 20260803T06 [--hours 6]

방향 규약: 내부 θ = 진행 방향, 수학각(동=0, CCW).
GRIB DIRPW/기상 방향(북=0, 시계, ~로부터)과의 변환은 _from_compass_from().
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from poseidon.core.catalog import Catalog
from poseidon.core.config import settings
from poseidon.engines.spectral_wave import grid as spec
from poseidon.engines.spectral_wave.grid import SpectralGrid
from poseidon.engines.spectral_wave.regional import RegionalWaveModel

log = logging.getLogger("poseidon.wave_cycle")

# 남쪽 12.5°N까지 확장: 괌(52200)·사이판(52211) NDBC 파랑부이 포함 (무인증 진실값)
DOMAIN = {"west": 120.0, "east": 148.0, "south": 12.5, "north": 46.0, "res": 0.25}
ETOPO_PATH = "data/static/etopo2022_60s_east_asia.nc"
SNAP_HOURS = 3.0                                            # 리드 스냅숏 간격


def _from_compass_from(deg_from: xr.DataArray | np.ndarray) -> np.ndarray:
    """기상 규약(북=0°, 시계방향, '~로부터') → 수학각 라디안('~로 향해')."""
    toward = np.asarray(deg_from, dtype=np.float64) + 180.0
    return np.radians((90.0 - toward) % 360.0)


def _load_depth(lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    ds = xr.open_dataset(settings.data_root.parent / ETOPO_PATH
                         if not Path(ETOPO_PATH).exists() else ETOPO_PATH)
    z = ds.z.interp(lat=xr.DataArray(lats, dims="y"),
                    lon=xr.DataArray(lons, dims="x"), method="linear")
    return np.maximum(-z.values, 0.0)                       # 수심(양수), 육지 0


def _assim_attrs(info: dict | None) -> dict:
    """동화 정보를 zarr attrs 형태로 (미적용이면 assimilation='none')."""
    if info is None:
        return {"assimilation": "none"}
    from poseidon.assimilation.cycle import assim_attrs
    return assim_attrs(info)


def _open_latest(catalog: Catalog, collection: str, cycle: str) -> xr.Dataset:
    rows = catalog.find_datasets(collection, settings.domain_name, cycle)
    if not rows:
        raise FileNotFoundError(f"no {collection} dataset for cycle {cycle}")
    return xr.open_zarr(rows[-1]["uri"], consolidated=False)


def run_wave_forecast(cycle: str, hours: float = 6.0, *,
                      assimilate: str = "none",
                      assim_config=None,
                      out_tag: str | None = None,
                      enable: tuple[str, ...] | None = None,
                      negative_input: bool = False,
                      drag: str = "wu1982",
                      swell: str = "none",
                      swell_fe: float | None = None,
                      init_gamma: float | None = None,
                      init_spread_power: float | None = None,
                      init_fill: str = "none") -> dict:
    """assimilate: "none"(기본, 기존 동작 비트 동일) | "oi-hs-v1" Hs 최적내삽.

    동화를 켜면 산출물이 **다른 파일·다른 source_id** 로 기록된다
    (`{cycle}_wave_{tag}.zarr`, `spectral_wave-L1-{tag}`). 대조군 표본을 덮어쓰거나
    한 테이블에 두 시스템을 섞지 않기 위한 것이다 (CLAUDE.md 함정 9·10).
    out_tag: 교차검증 폴드별 산출물 분리용 (기본 "da").
    enable: 소스항 스위치 (기본 None = 동결 구성). **진단 전용이다.**
        `("wind","ds","nl","bot","brk")` 중 일부만 켜서 항별 기여를 분리한다.
        기본값에서는 모델 생성 인자를 아예 넘기지 않으므로 기존 동작과
        비트 동일하다. 동결 구성이 아닌 실행은 `out_tag`를 **반드시** 함께
        주어야 한다 — 그래야 `source_id`가 갈려 운영 `error_sample`에
        섞이지 않는다(CLAUDE.md 함정 9).
    """
    # init_fill: GFS-Wave 적분량을 격자로 옮길 때 NaN 셀 처리 (wave_inputs, PHASE31).
    # 기본 "none" 은 기존 동작과 비트 동일. 다른 모드는 진단 전용 — out_tag 필수.
    from poseidon.scheduler.wave_inputs import (INIT_FILL_MODES, INIT_FILL_VERSION,
                                                grid_wave_fields)
    if init_fill not in INIT_FILL_MODES:
        raise ValueError(f"unknown init_fill {init_fill!r} (supported: {INIT_FILL_MODES})")
    _diag = (enable is not None or negative_input or swell != "none"
             or drag != "wu1982" or init_fill != "none"
             or init_gamma is not None or init_spread_power is not None)
    if _diag and not out_tag:
        raise ValueError(
            "물리를 바꾼 진단 실행은 out_tag가 필수다. 태그 없이 저장하면 "
            "운영 예보를 덮어쓰고 두 물리가 한 테이블에 섞인다."
        )
    catalog = Catalog(settings.catalog_path)
    forcing = _open_latest(catalog, "forcing", cycle)
    waves = _open_latest(catalog, "boundary", cycle)

    d = DOMAIN
    lats = np.arange(d["south"], d["north"] + 1e-9, d["res"])
    lons = np.arange(d["west"], d["east"] + 1e-9, d["res"])
    depth = _load_depth(lats, lons)

    grid = SpectralGrid()
    # 0.25° 격자는 쇄파대를 해상하지 못하므로 수심 하한 10 m (θ-서브사이클 비용도 절감)
    _mkw: dict = {} if enable is None else {"enable": tuple(enable)}
    if negative_input:
        _mkw["negative_input"] = True
    if drag != "wu1982":
        _mkw["drag"] = drag
    if swell != "none":
        from poseidon.engines.spectral_wave.sources import SwellPar
        _mkw["swell"] = swell
        if swell_fe is not None:
            _mkw["swell_par"] = SwellPar(fe=float(swell_fe))
    model = RegionalWaveModel(lats, lons, depth, grid, h_min=10.0, **_mkw)

    fill_info: list[dict] = []

    def wave_fields(t_idx_ds: xr.Dataset, mode: str = "none"):
        hs, tp, dfrom, info = grid_wave_fields(t_idx_ds, lats, lons, mode)
        if mode != "none":
            fill_info.append(info)
        return hs, tp, _from_compass_from(dfrom)

    # 초기조건 + 경계 (t=0)
    hs0, tp0, dir0 = wave_fields(waves.isel(time=0), init_fill)
    _spec_kw = {"gamma": init_gamma, "spread_power": init_spread_power}
    e = model.spectra_from_integrals(hs0, tp0, dir0, **_spec_kw)
    model.set_boundary(e, sides="WESN")

    # 시간 보간기 (강제장 3시간 간격)
    t_axis = ((forcing.time - forcing.time[0]) / np.timedelta64(1, "s")).values

    def wind_at(t_sec: float):
        w = forcing.interp(time=forcing.time[0].values
                           + np.timedelta64(int(t_sec), "s"))
        i = {"latitude": xr.DataArray(lats, dims="y"),
             "longitude": xr.DataArray(lons, dims="x")}
        u = w.u10.interp(**i).values
        v = w.v10.interp(**i).values
        return np.hypot(u, v), np.arctan2(v, u)

    # ── 관측 동화 (옵션, 기본 off) ─────────────────────────────────────
    # 초기장 E(f,θ) 만 교정한다. 물리(advection=uno2, gse=none)는 동결 상태 그대로다.
    # 경계 스펙트럼은 GFS-Wave 원본을 유지한다 — OI 증분은 경계 링에서 0 이라
    # 수치적으로는 같지만, 출처를 섞지 않기 위해 명시적으로 분리한다.
    e_boundary = e
    assim_info = None
    if assimilate != "none":
        # 지연 임포트: collocate -> wave_cycle 순환 임포트를 피한다
        from poseidon.assimilation.cycle import assimilate_initial_state
        from poseidon.assimilation.params import METHOD_TAG, AssimConfig
        if assimilate != METHOD_TAG:
            raise ValueError(f"unknown assimilation method {assimilate!r} "
                             f"(supported: 'none', {METHOD_TAG!r})")
        cfg = assim_config or AssimConfig()
        u10_0, udir_0 = wind_at(0.0)
        e, assim_info = assimilate_initial_state(model, e, cycle, u10_0, udir_0,
                                                 cfg=cfg)
        if assim_info.get("status") != "ok":
            log.warning("동화 미적용(%s) — 산출물은 assimilation='none' 으로 기록된다",
                        assim_info.get("status"))

    # 태그 붙은 실행(진단·동화 실험)은 운영 사이클의 실행 상태를 건드리지 않는다.
    # 건드리면 실험이 도중에 죽을 때 운영 사이클이 RUNNING_L1 에 멈춰 발행 목록에서 빠진다.
    run_id = None
    if not out_tag and assimilate == "none":
        run_id = catalog.create_run(cycle=cycle, engine="wave-L1",
                                    domain=settings.domain_name) \
            if catalog.get_run(cycle=cycle, engine="wave-L1",
                               domain=settings.domain_name) is None \
            else catalog.get_run(cycle=cycle, engine="wave-L1",
                                 domain=settings.domain_name)["run_id"]
        catalog.set_status(run_id, "RUNNING_L1")

    dt = model.cfl_dt()
    n = int(round(hours * 3600 / dt))

    # JAX 커널 (Phase 9): 가용하면 사용 — CPU에서도 ~8×, GPU 장착 시 자동 가속
    use_jax = False
    try:
        from poseidon.engines.spectral_wave.jax_kernel import (
            JaxRegionalKernel,
            ring_mask,
        )
        kern = JaxRegionalKernel(model)
        kern.prepare(model, dt)
        ring = ring_mask(model.ny, model.nx)
        use_jax = True
        log.info("backend: JAX (%s)", kern and "jit")
    except Exception as exc:  # noqa: BLE001 — 폴백은 항상 NumPy 참조
        log.warning("JAX unavailable (%s) — NumPy reference backend", exc)

    e_bc = e_boundary.copy()          # 경계는 동화 전 GFS-Wave 스펙트럼
    bc_next_update, bc_interval = 0.0, 3600.0
    snap_t = [0.0]
    snaps = [spec.snapshot(grid, e)]
    next_snap = SNAP_HOURS * 3600.0
    t = 0.0
    log.info("L1 wave run: %dx%d grid, dt=%.0fs, %d steps",
             model.ny, model.nx, dt, n)
    for _ in range(n):
        if t >= bc_next_update and t_axis[-1] > 0:
            ds_t = waves.interp(time=waves.time[0].values
                                + np.timedelta64(int(min(t, float(t_axis[-1]))), "s"))
            # 경계도 초기장과 같은 방식으로 옮긴다 (같은 결함, 같은 수정)
            eb = model.spectra_from_integrals(*wave_fields(ds_t, init_fill), **_spec_kw)
            model.set_boundary(eb, sides="WESN")
            e_bc = eb
            bc_next_update += bc_interval
        u10, udir = wind_at(min(t, float(t_axis[-1])))
        if use_jax:
            e = np.asarray(kern.step(e, dt, np.nan_to_num(u10),
                                     np.nan_to_num(udir), e_bc, ring))
        else:
            e = model.step(e, dt, np.nan_to_num(u10), np.nan_to_num(udir))
        t += dt
        if t >= next_snap - 1e-6:
            snap_t.append(round(t / 3600.0, 2))
            snaps.append(spec.snapshot(grid, e))
            next_snap += SNAP_HOURS * 3600.0

    # 리드별 GFS-Wave 자기검증
    leads = np.array([snap_t[0]] + snap_t[1:], dtype=float)
    per_lead = []
    for lh, snap in zip(leads, snaps):
        hs_fc = snap["hs"]
        tv = waves.time[0].values + np.timedelta64(int(min(lh, hours) * 3600), "s")
        hs_ref, _, _ = wave_fields(waves.interp(time=tv))
        sea = model.sea & np.isfinite(hs_ref)
        a, b = hs_fc[sea], hs_ref[sea]
        per_lead.append({"lead_h": float(lh),
                         "corr": float(np.corrcoef(a, b)[0, 1]),
                         "bias": float((a - b).mean()),
                         "rmse": float(np.sqrt(((a - b) ** 2).mean()))})
    metrics = {"per_lead_vs_gfswave": per_lead,
               "final": per_lead[-1], "n_sea": int(model.sea.sum())}
    if assim_info is not None:
        metrics["assimilation"] = {k: v for k, v in assim_info.items()
                                   if k not in ("rejected", "config")}

    valid_times = (pd.Timestamp(waves.time[0].values)
                   + pd.to_timedelta(leads, unit="h"))
    dims = ("lead", "latitude", "longitude")
    out = xr.Dataset(
        {v: (dims, np.stack([s[v] for s in snaps]).astype(np.float32))
         for v in spec.SNAPSHOT_VARS},
        coords={"lead": leads, "latitude": lats, "longitude": lons,
                "valid_time": ("lead", valid_times)},
        attrs={"cycle": cycle, "engine": "spectral_wave-L1-0.25deg",
               # 파일이 자기 출처를 말하게 한다 (docs/ENGINE_FREEZE.md)
               "advection": model.advection, "gse": model.gse,
               "gse_gamma": float(model.gse_gamma),
               "produced_at": pd.Timestamp.utcnow().isoformat(timespec="seconds"),
               # 규약을 파일에 못박는다. 어긋나면 180° 계통 편차가 물리 오차로
               # 오인된다. 유도는 engines.spectral_wave.grid.derive()로만 한다.
               "source_terms": ",".join(model.enable),
               "negative_input": str(bool(getattr(model, "negative_input", False))),
               "drag": str(getattr(model, "drag", "wu1982")),
               "swell_dissipation": str(getattr(model, "swell", "none")),
               "swell_fe": str(getattr(getattr(model, "swell_par", None), "fe", "")),
               "init_gamma": "default" if init_gamma is None else str(init_gamma),
               "init_spread_power": ("default" if init_spread_power is None
                                     else str(init_spread_power)),
               # 초기장·경계의 격자 매핑 방식 — 채점 표본이 방식별로 추적되게 한다 (PHASE31)
               "init_fill": init_fill, "init_fill_version": INIT_FILL_VERSION,
               "dir_convention": "coming_from, deg true, clockwise",
               "moment_units": "m0[m2] m1[m2/s] m2[m2/s2] a1,b1[m2]",
               "period_defs": "tp=peak; tm01=m0/m1, tm02=sqrt(m0/m2) via grid.derive",
               # 동화 여부·방법·관측소를 파일이 스스로 밝힌다 (CLAUDE.md 규칙 3)
               **_assim_attrs(assim_info)},
    )
    tag = (out_tag or "da") if assimilate != "none" else out_tag
    suffix = f"_wave_{tag}.zarr" if tag else "_wave.zarr"
    source_id = f"spectral_wave-L1-{tag}" if tag else "spectral_wave-L1"
    path = settings.zarr_root / "forecast" / settings.domain_name / f"{cycle}{suffix}"
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_zarr(path, mode="w", consolidated=False)
    catalog.register_dataset(collection="forecast", domain=settings.domain_name,
                             cycle=cycle, uri=str(path),
                             source_id=source_id,
                             retrieval={"metrics": metrics, "hours": hours,
                                        "advection": model.advection, "gse": model.gse,
                                        "gse_gamma": float(model.gse_gamma),
                                        "assimilation": out.attrs.get("assimilation",
                                                                      "none"),
                                        "init_fill": init_fill,
                                        "init_fill_first": fill_info[0] if fill_info else None,
                                        "assim": assim_info})
    if run_id is not None:
        catalog.set_status(run_id, "PUBLISHED")
    log.info("final lead metrics: %s", per_lead[-1])
    return metrics


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cycle", required=True)
    parser.add_argument("--hours", type=float, default=6.0)
    parser.add_argument("--assimilate", default="none",
                        help="'none'(기본) 또는 'oi-hs-v1' — Hs 관측 최적내삽 동화")
    parser.add_argument("--out-tag", default=None,
                        help="산출물 파일·source_id 접미사 (교차검증 폴드 분리용)")
    parser.add_argument("--drag", default="wu1982", choices=["wu1982", "cap-wu"],
                        help="[진단] 항력계수 스킴. cap-wu 는 Powell(2003) 정지점 "
                             "33 m/s 에서 상한 클램프. --out-tag 필수")
    parser.add_argument("--swell", default="none", choices=["none", "ardhuin2010"],
                        help="[진단] 스웰 감쇠 정식 (Ardhuin et al. 2010). --out-tag 필수")
    parser.add_argument("--swell-fe", type=float, default=None,
                        help="[진단] 스웰 감쇠 마찰계수 f_e (원문 권장 0.004~0.007)")
    parser.add_argument("--negative-input", action="store_true",
                        help="[진단] Snyder 음의 바람입력 허용 (스웰 감쇠). --out-tag 필수")
    parser.add_argument("--init-gamma", type=float, default=None,
                        help="[진단] 초기·경계 재구성 JONSWAP gamma (기본 3.3)")
    parser.add_argument("--init-spread-power", type=float, default=None,
                        help="[진단] 초기·경계 재구성 방향확산 cos^n 의 n (기본 2.0)")
    parser.add_argument("--enable", default=None,
                    help="소스항 진단 스위치 (쉼표 구분: wind,ds,nl,bot,brk). "
                         "빈 문자열이면 전부 끔. --out-tag 필수")
    parser.add_argument("--assim-corr-len-km", type=float, default=None)
    parser.add_argument("--assim-corr-shape", default=None,
                        choices=["soar", "exp", "gauss"])
    parser.add_argument("--assim-rescale", default=None,
                        choices=["ecmwf", "energy"])
    parser.add_argument("--assim-no-bias-correction", action="store_true")
    parser.add_argument("--assim-no-line-of-sight", action="store_true")
    parser.add_argument("--assim-exclude-stations", default="",
                        help="쉼표 구분 관측소 ID — 동화에서 제외 (독립 검증용)")
    parser.add_argument("--init-fill", default="none",
                        choices=("none", "seanorm", "seanorm_nn1"),
                        help="GFS-Wave -> 격자 NaN 셀 처리 (진단 전용, --out-tag 필수). PHASE31")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-5s %(name)s %(message)s")
    cfg = None
    if args.assimilate != "none":
        from dataclasses import replace

        from poseidon.assimilation.params import AssimConfig
        cfg = AssimConfig()
        if args.assim_corr_len_km is not None:
            cfg = replace(cfg, corr_len_km=args.assim_corr_len_km)
        if args.assim_corr_shape is not None:
            cfg = replace(cfg, corr_shape=args.assim_corr_shape)
        if args.assim_rescale is not None:
            cfg = replace(cfg, rescale=args.assim_rescale)
        if args.assim_no_bias_correction:
            cfg = replace(cfg, bias_correction=False)
        if args.assim_no_line_of_sight:
            cfg = replace(cfg, line_of_sight=False)
        if args.assim_exclude_stations:
            cfg = replace(cfg, exclude_stations=tuple(
                x.strip() for x in args.assim_exclude_stations.split(",") if x.strip()))
    m = run_wave_forecast(args.cycle, args.hours, assimilate=args.assimilate,
                          assim_config=cfg, out_tag=args.out_tag,
                          enable=(tuple(t for t in args.enable.split(",") if t)
                                  if args.enable is not None else None),
                          negative_input=args.negative_input,
                          drag=args.drag,
                          swell=args.swell, swell_fe=args.swell_fe,
                          init_gamma=args.init_gamma,
                          init_spread_power=args.init_spread_power,
                          init_fill=args.init_fill)
    print(m)
    return 0


if __name__ == "__main__":
    sys.exit(main())
