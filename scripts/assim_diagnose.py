"""동화 t=0 분석장 진단 — 격자 증분과 관측소 성적을 사이클 단위로 뽑는다.

예보를 전진시키지 않고 **초기장 교정만** 재현하므로 수 초에 끝난다. 용도:
  1) 증분장의 공간 구조 점검 (얼룩·육지 누출·영향 반경)
  2) 관측소에서 배경/분석이 관측에 대해 어떻게 움직였는지 (동화에 쓴 관측소는
     자기충족적이므로 `used` 딱지를 붙여 분리 보고한다 — 성능 근거가 아니다)
  3) 홀드아웃 관측소(--exclude)에서의 독립 성적

사용:
  PYTHONPATH=. .venv/bin/python scripts/assim_diagnose.py --cycle 20260821T00
  ... --exclude KMA:22103,KMA:22104        # 해당 관측소를 동화에서 빼고 그 자리에서 채점
"""

from __future__ import annotations

import argparse
import json
import logging

import numpy as np

from poseidon.assimilation.cycle import assimilate_initial_state
from poseidon.assimilation.observations import load_hs_observations
from poseidon.assimilation.params import MIN_SEA_WEIGHT, AssimConfig
from poseidon.validation.collocate import corner_weights, sea_normalized


def build(cycle: str):
    """wave_cycle 과 **같은 경로**로 t=0 배경 스펙트럼을 만든다 (train/serve skew 방지)."""
    from poseidon.scheduler import wave_cycle as wc

    catalog_ds = wc._open_latest
    from poseidon.core.catalog import Catalog
    from poseidon.core.config import settings
    from poseidon.engines.spectral_wave.grid import SpectralGrid
    from poseidon.engines.spectral_wave.regional import RegionalWaveModel

    cat = Catalog(settings.catalog_path)
    forcing = catalog_ds(cat, "forcing", cycle)
    waves = catalog_ds(cat, "boundary", cycle)
    d = wc.DOMAIN
    lats = np.arange(d["south"], d["north"] + 1e-9, d["res"])
    lons = np.arange(d["west"], d["east"] + 1e-9, d["res"])
    depth = wc._load_depth(lats, lons)
    model = RegionalWaveModel(lats, lons, depth, SpectralGrid(), h_min=10.0)

    import xarray as xr
    i = {"latitude": xr.DataArray(lats, dims="y"), "longitude": xr.DataArray(lons, dims="x")}
    w0 = waves.isel(time=0)
    e = model.spectra_from_integrals(
        w0.swh.interp(**i).values, w0.perpw.interp(**i).values,
        wc._from_compass_from(w0.dirpw.interp(**i).values))
    f0 = forcing.isel(time=0)
    u, v = f0.u10.interp(**i).values, f0.v10.interp(**i).values
    return model, e, np.hypot(u, v), np.arctan2(v, u)


def in_domain(model, lat, lon) -> bool:
    """corner_weights 는 도메인 밖 지점을 **가장자리 셀로 클램프**한다. 그대로 쓰면
    태평양 부이가 도메인 가장자리 값으로 채점돼 표가 오염된다 (실측: NDBC:46059).
    build_obs_slots 와 같은 여유(경계 링 2셀)를 적용한다.
    """
    my = 2.0 * float(model.lats[1] - model.lats[0])
    mx = 2.0 * float(model.lons[1] - model.lons[0])
    return bool(model.lats[0] + my <= lat <= model.lats[-1] - my
                and model.lons[0] + mx <= lon <= model.lons[-1] - mx)


def at_station(model, field, lat, lon):
    jj, ii, w = corner_weights(model.lats, model.lons, lat, lon)
    val, land_frac, _ = sea_normalized(field[jj, ii], w, model.sea[jj, ii], MIN_SEA_WEIGHT)
    return float(val), float(land_frac)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycle", required=True)
    ap.add_argument("--exclude", default="")
    ap.add_argument("--corr-len-km", type=float, default=None)
    ap.add_argument("--no-bias-correction", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)

    from dataclasses import replace
    cfg = AssimConfig()
    excl = tuple(x.strip() for x in args.exclude.split(",") if x.strip())
    if excl:
        cfg = replace(cfg, exclude_stations=excl)
    if args.corr_len_km is not None:
        cfg = replace(cfg, corr_len_km=args.corr_len_km)
    if args.no_bias_correction:
        cfg = replace(cfg, bias_correction=False)

    model, e_b, u10, udir = build(args.cycle)
    e_a, info = assimilate_initial_state(model, e_b, args.cycle, u10, udir, cfg=cfg)

    hs_b = model.grid.hs(np.asarray(e_b, dtype=np.float64))
    hs_a = model.grid.hs(np.asarray(e_a, dtype=np.float64))
    d = hs_a - hs_b

    print(f"=== {args.cycle}  method={info.get('method')} status={info.get('status')} ===")
    print(f"관측 {info.get('n_obs_loaded')}건 로드 / {info.get('n_obs_used')}건 채택 "
          f"/ {info.get('n_obs_rejected')}건 폐기, 제외 {list(excl)}")
    oi = info.get("oi", {})
    print(f"chi2={oi.get('chi2', float('nan')):.3f} (자기일관 목표 1.0+-0.3), "
          f"지지반경 {oi.get('support_km')} km, 시선차단쌍 {oi.get('n_los_blocked_pairs')}")
    print(f"증분: RMS {np.sqrt((d[model.sea]**2).mean()):.4f} m, "
          f"|max| {np.abs(d).max():.4f} m, 갱신셀 {int((np.abs(d)>1e-6).sum())}/{int(model.sea.sum())}")
    print(f"육지셀 증분 |max| {np.abs(d[~model.sea]).max():.3e} m (0 이어야 함)")
    print(f"경계링 증분 |max| "
          f"{max(np.abs(d[0]).max(), np.abs(d[-1]).max(), np.abs(d[:,0]).max(), np.abs(d[:,-1]).max()):.3e} m")
    print(f"유한성: NaN {int(np.isnan(np.asarray(e_a)).sum())}, "
          f"inf {int(np.isinf(np.asarray(e_a)).sum())}, min E {float(np.asarray(e_a).min()):.3e}")

    used = set(info.get("stations", []))
    obs_all = load_hs_observations(args.cycle, window_min=cfg.obs_window_min)
    rows = []
    print(f"\n{'station':>12} {'tag':>8} {'obs':>6} {'bg':>6} {'an':>6} "
          f"{'d_bg':>7} {'d_an':>7} {'land':>5}")
    for o in obs_all:
        if not in_domain(model, o.lat, o.lon):
            continue
        b, lf = at_station(model, hs_b, o.lat, o.lon)
        a, _ = at_station(model, hs_a, o.lat, o.lon)
        if not np.isfinite(b) or lf > cfg.max_land_frac:
            continue
        tag = "used" if o.station_id in used else ("HELD" if o.station_id in excl else "unused")
        rows.append((tag, o.value - b, o.value - a))
        print(f"{o.station_id:>12} {tag:>8} {o.value:6.2f} {b:6.2f} {a:6.2f} "
              f"{o.value-b:+7.3f} {o.value-a:+7.3f} {lf:5.3f}")

    print(f"\n{'group':>8} {'n':>4} {'bias_bg':>9} {'bias_an':>9} {'rmse_bg':>9} {'rmse_an':>9} {'dRMSE%':>8}")
    for tag in ("used", "HELD", "unused"):
        g = [(x, y) for t, x, y in rows if t == tag]
        if not g:
            continue
        eb = np.array([x for x, _ in g]); ea = np.array([y for _, y in g])
        rb, ra = np.sqrt((eb**2).mean()), np.sqrt((ea**2).mean())
        note = "  <- 자기충족적, 성능 근거 아님" if tag == "used" else ""
        print(f"{tag:>8} {len(g):4d} {eb.mean():+9.4f} {ea.mean():+9.4f} "
              f"{rb:9.4f} {ra:9.4f} {100*(ra-rb)/rb:+8.2f}{note}")

    print("\nattrs:", json.dumps(
        {k: v for k, v in info.items() if k not in ("rejected", "config", "oi")},
        ensure_ascii=False, default=str)[:600])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
