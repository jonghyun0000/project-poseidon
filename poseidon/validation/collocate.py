"""예보-관측 콜로케이션 → error_sample (AI 학습·검증 통계의 원천, PHASE2 §3.3).

파랑: forecast zarr(리드별 Hs)를 NDBC 부이 위치·시각에 **해양 마스크 정규화 겹선형**으로
보간해 관측 Hs(±30분 평균)와 짝짓고, 바람 강제장 U10을 피처로 첨부한다.

해양 마스크 정규화(sea-normalized interpolation)가 필요한 이유:
regional.py는 육지 셀의 스펙트럼을 0으로 흡수(`e[~self.sea] = 0`)하므로 육지 셀 Hs=0이다.
순수 겹선형은 이 0을 그대로 섞어 연안 부이의 예측을 계통적으로 과소평가한다.
격자가 성길수록(L1 0.25°) 스텐실에 육지가 걸릴 확률이 커져 오염이 L1에 편향 작용하고,
L1↔L2 중첩 비교 자체를 무효화한다(예: 연평도 KMA:22522 — L1 예측 0.000 m, 육지가중 1.000).
따라서 WMO/WAM 계열 검증 관행(육지 스텐실 제외·재정규화, Bidlot et al. 2002 "Intercomparison
of the performance of operational ocean wave forecasting systems")을 따라
    Hs = Σ(W·S·Hs) / Σ(W·S),   Σ(W·S) < MIN_SEA_WEIGHT 이면 결측 처리
로 계산하고, 육지 가중 land_frac = Σ(W·(1−S))를 피처에 남겨 사후 필터링을 가능케 한다.

사용:  python -m poseidon.validation.collocate --cycle 20260804T00
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

import numpy as np
import pandas as pd
import xarray as xr

from poseidon.core.catalog import Catalog
from poseidon.core.config import settings
from poseidon.engines.spectral_wave import grid as spec
from poseidon.ingest.adapters.ndbc import fetch_station_locations
from poseidon.scheduler.wave_cycle import _load_depth

log = logging.getLogger("poseidon.collocate")

# 모델 격자 해양 판정 하한 (RegionalWaveModel.sea = depth > h_min).
# L1: wave_cycle.run_wave_forecast(h_min=10.0), L2: nested_cycle.L2_HMIN = 3.0
H_MIN = {"L1": 10.0, "L2": 3.0}
MIN_SEA_WEIGHT = 0.05        # Σ(W·S) 하한 — 이보다 작으면 사실상 육지 표본으로 보고 제외
LAND_FRAC_MAX = 0.05         # 기본 사후 필터 임계 (육지 가중 5% 초과 표본 제외)

# error_sample 테이블 컬럼 (DataFrame에는 진단용 여분 컬럼이 더 붙는다)
_DB_COLS = ("cycle", "station_id", "valid_time", "lead_h", "var",
            "predicted", "observed", "features")

# 관측 변수 → (모델 유도량, error_sample.var, 관측 정의 메모)
#
# 정의가 어긋난 것끼리 맞대면 그 불일치가 물리 오차로 오인된다. 그래서 대응을
# 코드에 명시하고 features에도 남긴다.
#   KMA WP  파주기 — 기상청 문서에 정의가 없다. 연속변화 지문이 NDBC DPD와 같아
#           첨두 계열로 **추정**하나 확인되지 않았다 [가설].
#   NDBC DPD 첨두 주기(dominant), APD 평균 주기 = sqrt(m0/m2) = Tm02.
#   NDBC MWD 첨두 대역의 파향이지 전 스펙트럼 평균이 아니다 → dirp와 대조한다.
#   KMA WO  기상청 문서에 정의가 없다. 다만 규약(오는/가는 방향)은 세 독립 증거로
#           **오는 방향**임이 확인됐다 [2026-08-25]:
#             (1) 고풍속(≥8 m/s) 조건에서 WD1(풍향, 오는 방향)과의 순환평균 편차
#                 +13.5°, R=0.70 — 반대 규약이면 180° 부근이어야 한다
#             (2) 모델(오는 방향)과의 채점 순환 bias −5.65°, |오차|>150° 비율 0.033
#             (3) GFS-Wave DIRPW(오는 방향)와 리드 0에서 순환차 0.00° (10,921점)
#           정의(첨두/평균)는 여전히 미확인이다 [확인 필요: KMA 문서].
_OBS_MAP = {
    "hs":  ("hs",   "hs",   "significant wave height"),
    "tp":  ("tp",   "tp",   "KMA WP def unverified [가설:peak] / NDBC DPD peak"),
    "ta":  ("tm02", "tm02", "NDBC APD = sqrt(m0/m2)"),
    "dir": ("dirm", "dir",  "KMA WO def unverified (from-convention confirmed) / NDBC MWD is peak-band"),
}
_CIRCULAR = frozenset({"dir"})       # 각도 — 오차는 순환차로만 의미를 갖는다
_MOMENTS = ("m0", "m1", "m2", "a1", "b1")


def circular_error(pred: float, obs: float) -> float:
    """각도 잔차를 **[-180, 180)** 으로 감싼다.

    감싸지 않으면 359°−1°가 358°가 되어, 실제로는 2° 차이인 표본이 최악의
    오차로 집계된다. 정확히 반대 방향(180°)은 −180으로 나오지만 크기가 같아
    통계에는 영향이 없다.
    """
    return float((pred - obs + 180.0) % 360.0 - 180.0)


def corner_weights(lats: np.ndarray, lons: np.ndarray, la: float, lo: float):
    """정규 격자에서 (la, lo)를 둘러싼 4코너 인덱스와 겹선형 가중치 W(합=1).

    반환: (jj, ii, w) — 각각 길이 4, 순서는 (j0,i0) (j0,i0+1) (j0+1,i0) (j0+1,i0+1).
    격자 밖 지점은 가장자리 셀로 클램프한다(nested_cycle.bilinear_weights와 동일 규약).
    """
    lats = np.asarray(lats, dtype=np.float64)
    lons = np.asarray(lons, dtype=np.float64)
    dy = lats[1] - lats[0]
    dx = lons[1] - lons[0]
    fy = (la - lats[0]) / dy
    fx = (lo - lons[0]) / dx
    j0 = int(np.clip(np.floor(fy), 0, len(lats) - 2))
    i0 = int(np.clip(np.floor(fx), 0, len(lons) - 2))
    wy = float(np.clip(fy - j0, 0.0, 1.0))
    wx = float(np.clip(fx - i0, 0.0, 1.0))
    jj = np.array([j0, j0, j0 + 1, j0 + 1])
    ii = np.array([i0, i0 + 1, i0, i0 + 1])
    w = np.array([(1 - wy) * (1 - wx), (1 - wy) * wx, wy * (1 - wx), wy * wx])
    return jj, ii, w


def sea_normalized(values: np.ndarray, w: np.ndarray, sea: np.ndarray,
                   min_sea_weight: float = MIN_SEA_WEIGHT):
    """해양 마스크 정규화 겹선형.

    values: (..., 4) 코너 값, w: (4,) 겹선형 가중, sea: (4,) 해양 여부(bool/0·1).
    반환: (value, land_frac, raw) — value는 Σ(W·S) < min_sea_weight 이면 NaN.
    전부 해양(S≡1)이면 value == raw(순수 겹선형)이 되어 기존 동작과 동일하다.
    """
    w = np.asarray(w, dtype=np.float64)
    s = np.asarray(sea, dtype=np.float64)
    values = np.asarray(values, dtype=np.float64)
    sw = float((w * s).sum())
    land_frac = float((w * (1.0 - s)).sum())
    raw = values @ w
    if sw < min_sea_weight:
        value = np.full(raw.shape, np.nan) if np.ndim(raw) else float("nan")
    else:
        value = (values @ (w * s)) / sw
    return value, land_frac, raw


def _grid_sea(lats: np.ndarray, lons: np.ndarray, level: str):
    """모델 격자의 (해양 마스크, 수심). ETOPO 미존재 시 (None, None) → 순수 겹선형 폴백."""
    try:
        depth = _load_depth(np.asarray(lats), np.asarray(lons))
    except Exception as exc:  # noqa: BLE001 — ETOPO 없으면 마스킹 없이 진행
        log.warning("모델 격자 수심 로드 실패(%s) — 육지 마스킹 없이 진행", exc)
        return None, None
    return depth > H_MIN.get(level, 10.0), depth


def land_frac_of(df: pd.DataFrame) -> pd.Series:
    """표본별 육지 가중치. 구버전(sea-norm 이전) 표본은 NaN(미상) — 청정으로 위장 금지."""
    if "land_frac" in df.columns:
        base = pd.to_numeric(df["land_frac"], errors="coerce")
    else:
        base = pd.Series(np.nan, index=df.index, dtype=float)
    if "features" in df.columns:
        parsed = []
        for f in df["features"]:
            try:
                d = json.loads(f) if isinstance(f, str) else (f or {})
            except json.JSONDecodeError:
                d = {}
            v = d.get("land_frac")
            parsed.append(np.nan if v is None else float(v))
        base = base.fillna(pd.Series(parsed, index=df.index, dtype=float))
    return base


def collocation_version(df: pd.DataFrame) -> pd.Series:
    """표본이 어느 콜로케이션 알고리즘으로 만들어졌는지 (없으면 'legacy')."""
    out = []
    for f in df.get("features", pd.Series([None] * len(df), index=df.index)):
        try:
            d = json.loads(f) if isinstance(f, str) else (f or {})
        except json.JSONDecodeError:
            d = {}
        out.append(str(d.get("collocation", "legacy")))
    return pd.Series(out, index=df.index)


def filter_by_land(df: pd.DataFrame, max_land_frac: float = LAND_FRAC_MAX,
                   legacy: str = "drop") -> pd.DataFrame:
    """육지 오염 표본 제외.

    legacy='drop'  : land_frac 미상(구버전) 표본도 제외 — 통계·학습 기본값
    legacy='keep'  : 미상 표본 유지 (과거 결과 재현용)
    """
    lf = land_frac_of(df)
    ok = lf <= max_land_frac
    if legacy == "keep":
        ok = ok | lf.isna()
    elif legacy != "drop":
        raise ValueError("legacy must be 'drop' or 'keep'")
    return df[ok.fillna(False)]


def _station_locations() -> pd.DataFrame:
    """NDBC(자동 취득) + KMA(키 설정 시 어댑터가 캐시) 좌표표 병합."""
    cache = settings.data_root / "static" / "ndbc_stations.parquet"
    if cache.exists():
        df = pd.read_parquet(cache)
    else:
        df = asyncio.run(fetch_station_locations())
        cache.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(cache, index=False)
    kma = settings.data_root / "static" / "kma_stations.parquet"
    if kma.exists():
        df = pd.concat([df, pd.read_parquet(kma)[["station_id", "lat", "lon"]]],
                       ignore_index=True).drop_duplicates("station_id")
    return df


def collocate_wave(cycle: str, obs_window_min: float = 30.0,
                   level: str = "L1", *, source_id: str | None = None,
                   write: bool = True) -> pd.DataFrame:
    """level: L1(0.25°, var=hs) | L2(0.05° 연안 중첩, var=hs_l2).

    source_id: 기본은 운영 산출물. 진단 산출물(`spectral_wave-L1-<tag>`)을 채점할 때 준다.
    write: False 면 error_sample 에 쓰지 않고 DataFrame 만 돌려준다 — 진단 실행과 대조군을
        **운영 채점 표본과 섞지 않고** 같은 조건으로 비교하기 위한 것이다(AGENTS.md §6-9, PHASE31).
        진단 source_id 를 write=True 로 채점하는 것은 막는다.
    """
    src = source_id or ("spectral_wave-L2" if level == "L2" else "spectral_wave-L1")
    if write and src not in ("spectral_wave-L1", "spectral_wave-L2"):
        raise ValueError(f"진단 산출물 {src!r} 은 error_sample 에 쓰지 않는다 — write=False 로 채점할 것")
    catalog = Catalog(settings.catalog_path)
    fc_rows = [r for r in catalog.find_datasets("forecast", settings.domain_name, cycle)
               if r["source_id"] == src]
    if not fc_rows:
        raise FileNotFoundError(f"no {level} wave forecast for {cycle}")
    fc = xr.open_zarr(fc_rows[-1]["uri"], consolidated=False)
    # 예보 파일이 스스로 밝힌 엔진 설정 (없으면 unknown — 추론하지 않는다)
    engine_tag = {k: str(fc.attrs.get(k, "unknown"))
                  for k in ("advection", "gse", "gse_gamma", "produced_at",
                            "init_fill", "init_fill_version")}
    forcing = xr.open_zarr(
        catalog.find_datasets("forcing", settings.domain_name, cycle)[-1]["uri"],
        consolidated=False)
    waves_ref = xr.open_zarr(
        catalog.find_datasets("boundary", settings.domain_name, cycle)[-1]["uri"],
        consolidated=False)
    etopo_p = settings.data_root / "static" / "etopo2022_60s_east_asia.nc"
    etopo = xr.open_dataset(etopo_p) if etopo_p.exists() else None

    frames = []
    for provider in ("ndbc", "kma"):                       # 파고 관측 제공자
        pth = settings.parquet_root / "obs" / provider / f"{cycle[:4]}" / f"{cycle[4:6]}.parquet"
        if pth.exists():
            frames.append(pd.read_parquet(pth))
    if not frames:
        raise FileNotFoundError("no wave observations for cycle month")
    obs = pd.concat(frames, ignore_index=True)
    obs = obs[obs["var"].isin(_OBS_MAP) & (obs["qc_flag"] == 0)]
    locs = _station_locations().set_index("station_id")

    lat0, lat1 = float(fc.latitude.min()), float(fc.latitude.max())
    lon0, lon1 = float(fc.longitude.min()), float(fc.longitude.max())

    g_lats, g_lons = fc.latitude.values, fc.longitude.values
    sea_grid, depth_grid = _grid_sea(g_lats, g_lons, level)      # 모델 격자 해양 마스크
    def _field(name: str) -> np.ndarray | None:
        if name not in fc.data_vars:
            return None
        return fc[name].transpose("lead", "latitude", "longitude").values
    hs_all = _field("hs")                                   # (lead, ny, nx)
    # 옛 예보 zarr는 hs 하나뿐이다. 없는 변수는 조용히 건너뛰고, 그 사실이
    # features의 model_vars에 남는다 — 결측을 값으로 위장하지 않는다.
    mom_all = {v: _field(v) for v in _MOMENTS}
    has_mom = all(a is not None for a in mom_all.values())
    peak_all = {v: _field(v) for v in ("tp", "dirp")}
    has_peak = all(a is not None for a in peak_all.values())
    lead_idx = {float(v): k for k, v in enumerate(fc.lead.values)}

    rows = []
    for sid, g in obs.groupby("station_id"):
        if sid not in locs.index:
            continue
        la, lo = float(locs.loc[sid, "lat"]), float(locs.loc[sid, "lon"])
        if not (lat0 + 0.5 <= la <= lat1 - 0.5 and lon0 + 0.5 <= lo <= lon1 - 0.5):
            continue                                       # 경계 링 인접 제외
        jj, ii, w = corner_weights(g_lats, g_lons, la, lo)
        corners = hs_all[:, jj, ii]                        # (lead, 4)
        s4 = np.ones(4) if sea_grid is None else sea_grid[jj, ii]
        hs_pt, land_frac, hs_raw = sea_normalized(corners, w, s4)   # (lead,)

        # 주기·파향은 **모멘트를 보간한 뒤** 유도한다. 모멘트는 E에 선형이고
        # 육지에서 0이라 hs와 똑같은 해상 정규화가 그대로 성립한다. 각도나
        # 첨두 주기를 직접 보간하면 359°+1°가 180°가 되고 첨두는 모드 선택이라
        # 평균 자체가 정의되지 않는다.
        der: dict[str, np.ndarray] = {}
        if has_mom:
            mom_pt = {v: sea_normalized(mom_all[v][:, jj, ii], w, s4)[0]
                      for v in _MOMENTS}
            der = spec.derive(**mom_pt)
        if has_peak:
            # 첨두량은 보간하지 않는다 — 가중치가 가장 큰 **해양** 코너 값만 쓴다.
            ws = w * s4
            kb = int(np.argmax(ws))
            if ws[kb] > 0.0:
                for v in ("tp", "dirp"):
                    der[v] = peak_all[v][:, jj[kb], ii[kb]]
        depth_model = float("nan") if depth_grid is None else float(depth_grid[jj, ii] @ w)
        depth = float(max(-float(etopo.z.interp(lat=la, lon=lo)), 1.0)) if etopo is not None else 50.0
        for lead, tv in zip(fc.lead.values, pd.to_datetime(fc.valid_time.values, utc=True)):
            win = g[(g["ts"] >= tv - pd.Timedelta(minutes=obs_window_min))
                    & (g["ts"] <= tv + pd.Timedelta(minutes=obs_window_min))]
            if win.empty:
                continue
            li = lead_idx[float(lead)]
            if not np.isfinite(hs_pt[li]):
                continue                                   # 전부 육지(Σ W·S < 0.05) → 결측
            tv64 = np.datetime64(tv.tz_localize(None))
            wf = forcing.interp(time=min(tv64, forcing.time[-1].values),
                                latitude=la, longitude=lo)   # 코너 가중 w와 이름 분리
            u10 = float(np.hypot(float(wf.u10), float(wf.v10)))
            try:
                hs_ref = float(waves_ref.swh.interp(time=min(tv64, waves_ref.time[-1].values),
                                                    latitude=la, longitude=lo))
            except Exception:  # noqa: BLE001
                hs_ref = float("nan")
            base = {"collocation": "sea-norm-v1", **engine_tag,
                    "u10": u10, "depth": depth,
                    "hs_gfsw": None if np.isnan(hs_ref) else hs_ref,
                    "land_frac": land_frac,
                    "depth_model": None if np.isnan(depth_model) else depth_model}

            # 관측 변수별로 채점한다. 한 시간창에 hs·tp·dir이 함께 들어오므로
            # 변수를 나누지 않고 평균내면 파고와 주기가 뒤섞인다.
            for obs_var, sub in win.groupby("var"):
                model_key, var_stem, obs_def = _OBS_MAP[obs_var]
                if obs_var == "hs":
                    pred = float(hs_pt[li])
                elif model_key in der and der[model_key] is not None:
                    pred = float(der[model_key][li])
                else:
                    continue                               # 옛 zarr — 해당 변수 없음
                if not np.isfinite(pred):
                    continue
                vals = sub["value"].to_numpy(dtype=float)
                if obs_var in _CIRCULAR:
                    # 각도의 산술 평균은 성립하지 않는다 (359°와 1°의 평균은 0°다)
                    r = np.radians(vals)
                    observed = float(np.degrees(np.arctan2(
                        np.sin(r).mean(), np.cos(r).mean())) % 360.0)
                else:
                    observed = float(vals.mean())

                feat = dict(base, obs_def=obs_def, model_def=model_key,
                            obs_n=int(len(vals)))
                if obs_var == "hs":
                    # 구방식(순수 겹선형) 값은 육지 오염 재현 검증에 쓰인다
                    feat["predicted_raw"] = float(hs_raw[li])
                else:
                    # 동시각 파고 — 저파고에서 주기·파향은 요동친다. 임계 게이트와
                    # 파고대별 집계가 이 값 없이는 불가능하다.
                    feat["obs_hs_ctx"] = None
                    hs_win = win[win["var"] == "hs"]["value"]
                    if not hs_win.empty:
                        feat["obs_hs_ctx"] = float(hs_win.mean())
                    feat["model_hs"] = float(hs_pt[li])
                    for extra in ("tm01", "tm02", "dirm", "dspr", "tp", "dirp"):
                        if extra in der and der[extra] is not None:
                            ev = float(der[extra][li])
                            feat[extra] = None if not np.isfinite(ev) else ev
                suffix = "_l2" if level == "L2" else ""
                rows.append({
                    "cycle": cycle, "station_id": sid,
                    "valid_time": tv.isoformat(), "lead_h": float(lead),
                    "var": f"{var_stem}{suffix}",
                    "predicted": pred, "observed": observed,
                    "features": json.dumps(feat),
                    # ↓ DataFrame 전용 진단 컬럼 (error_sample 테이블에는 넣지 않는다)
                    "land_frac": land_frac,
                    "predicted_raw": float(hs_raw[li]) if obs_var == "hs" else float("nan"),
                    "depth_model": depth_model,
                })
    if rows and write:
        catalog.add_error_samples([{k: r[k] for k in _DB_COLS} for r in rows])
    log.info("collocated %d samples (%s, %s%s)", len(rows), cycle, src,
             "" if write else ", 쓰지 않음")
    return pd.DataFrame(rows)


def skill_by_lead(df: pd.DataFrame, max_land_frac: float | None = None) -> pd.DataFrame:
    """(변수, 리드)별 bias/rmse. max_land_frac 지정 시 육지 오염 표본을 먼저 제외한다.

    변수를 나누지 않고 집계하면 파고(m)와 파향(deg)이 한 평균에 섞인다.
    파향은 순환량이라 산술 오차가 성립하지 않으므로 `circular_error`로 감싼
    잔차를 쓰고, 산포는 CMAE(순환 평균절대오차)와 R(결과 길이)로 보고한다.
    """
    if max_land_frac is not None:
        df = filter_by_land(df, max_land_frac)

    def _agg(g: pd.DataFrame, var: str) -> pd.Series:
        if var.split("_")[0] in _CIRCULAR:
            e = np.array([circular_error(p, o)
                          for p, o in zip(g["predicted"], g["observed"])])
            rad = np.radians(e)
            sn, cs = float(np.sin(rad).mean()), float(np.cos(rad).mean())
            return pd.Series({"n": len(g),
                              "bias": float(np.degrees(np.arctan2(sn, cs))),
                              "rmse": float(np.sqrt((e ** 2).mean())),
                              "cmae": float(np.abs(e).mean()),
                              "R": float(np.hypot(sn, cs))})
        err = g["predicted"] - g["observed"]
        return pd.Series({"n": len(g), "bias": err.mean(),
                          "rmse": float(np.sqrt((err ** 2).mean())),
                          "cmae": float(np.nan), "R": float(np.nan)})

    if df.empty or "var" not in df.columns:
        return pd.DataFrame()
    out, idx = [], []
    for (var, lead), g in df.groupby(["var", "lead_h"]):
        out.append(_agg(g, str(var)))
        idx.append((var, float(lead)))
    return pd.DataFrame(out, index=pd.MultiIndex.from_tuples(
        idx, names=["var", "lead_h"]))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cycle", required=True)
    parser.add_argument("--level", default="L1", choices=["L1", "L2"])
    parser.add_argument("--max-land-frac", type=float, default=None,
                        help="지정 시 육지 가중치가 이보다 큰 표본을 통계에서 제외")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    df = collocate_wave(args.cycle, level=args.level)
    if df.empty:
        print("no collocations (도메인 내 관측 없음 또는 시각 불일치)")
        return 1
    lf = land_frac_of(df)
    print(f"land_frac: 평균 {lf.mean():.3f} | >0.05 {int((lf > 0.05).sum())}/{len(df)} "
          f"({100 * (lf > 0.05).mean():.1f}%)")
    print(skill_by_lead(df, args.max_land_frac).round(3))
    return 0


if __name__ == "__main__":
    sys.exit(main())


# --- 파향·주기 검증의 파고 조건 --------------------------------------------
#
# 2026-08-25 Hs 임계 민감도 스캔의 결론: **단일 임계를 확정하지 않는다.**
# 네 갈래 조사가 독립적으로 같은 결론에 도달했다.
#
#   관측 자기일관성 — 연속 관측 쌍의 |Δdir|은 Hs가 낮을수록 매끄럽게 나빠지고
#     (0-0.1 m에서 고파고 대비 5.3~5.7배), 0.45~0.55 m 부근에서 평탄해진다.
#     **무릎(knee)은 없다.** 어떤 값을 골라도 연속 곡선 위의 선택이다.
#     31개소 중 28개소에서 재현되고 LOSO에 견딘다.
#   모델 채점 — 표본 55건·1사이클에서 임계 0 대 0.5 m의 CMAE 차가
#     −5.1° [95% CI −15.2, +1.1]로 0을 포함한다. 확정하면 과적합이다.
#   문헌 — 파향 검증에 Hs 임계를 쓴 운영 문서·논문을 찾지 못했다. Met Office
#     AMM15와 CMEMS는 필터 **없이** 파향을 검증한다(RMSD 17.6~32.6°, 21.75°).
#     통용되는 1 m에는 근거가 없다.
#   최종 용도 — IMO MSC.1/Circ.1228의 위험 조건은 Hs > 0.04L로 **선박 길이의
#     함수**다(L=60 m → 2.4 m, L=294 m → 11.8 m). 상수로 역산할 수 없다.
#
# 그래서 파고대별로 나눠 보고하고, 이용자가 자기 조건의 행을 읽게 한다.

INSTRUMENT_HS_MIN = 0.25
"""파향·주기 검증의 **계기 유효 하한** [m]. 성능 임계가 아니다.

NDBC Technical Document T80-10 §6.2.11.3 "Calm Sea Check"가 Hs < 0.25 m에서
DPD·MWD 발표를 차단한다(사유: 신호대잡음비). 확증된 유일한 운영 숫자다.
KMA에 같은 하한이 있는지는 미확인이므로 `[가설]`로 적용한다.
"""

HS_BANDS = (0.25, 0.5, 1.0, 1.5, 2.0)
"""파고대 경계 [m]. 마지막 값 위는 열린 구간."""


def _obs_hs(df: pd.DataFrame) -> pd.Series:
    """표본의 동시각 관측 파고. 없으면 NaN — 0으로 채우지 않는다."""
    def _get(f: object) -> float:
        try:
            d = json.loads(f) if isinstance(f, str) else (f or {})
        except json.JSONDecodeError:
            return float("nan")
        v = d.get("obs_hs_ctx")
        return float("nan") if v is None else float(v)
    return pd.Series([_get(f) for f in df["features"]], index=df.index)


def _circ_stats(err: np.ndarray) -> dict[str, float]:
    rad = np.radians(err)
    sn, cs = float(np.sin(rad).mean()), float(np.cos(rad).mean())
    R = float(np.hypot(sn, cs))
    return {"bias": float(np.degrees(np.arctan2(sn, cs))),
            "cmae": float(np.abs(err).mean()),
            "crmse": float(np.sqrt((err ** 2).mean())),
            "R": R,
            "circ_sd": float(np.degrees(np.sqrt(-2.0 * np.log(max(R, 1e-12))))),
            "within_30": float(np.mean(np.abs(err) < 30.0)),
            "within_45": float(np.mean(np.abs(err) < 45.0))}


def _boot_ci(df: pd.DataFrame, stat: str = "cmae", key: str = "station_id",
             b: int = 2000, seed: int = 0) -> tuple[float, float]:
    """군집 부트스트랩 CI. 군집이 3개 미만이면 (nan, nan).

    key="station_id"는 공간 상관을, key="cycle"은 시간(기상 상황) 상관을 흡수한다.
    **둘은 서로를 대체하지 못한다.** 사이클이 하나뿐이면 관측소 군집 CI는 같은
    기상 상황을 여러 번 세는 셈이라 낙관적이다 — 1사이클 55건에서 임계 1.0 m가
    최적으로 보였다가 8사이클에서 유의하게 최악으로 뒤집힌 것이 그 사례다
    (docs/PHASE13 §9-10).
    """
    keys = df[key].unique()
    if len(keys) < 3:
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    by = {k: g["_err"].to_numpy(float) for k, g in df.groupby(key)}
    out = []
    for _ in range(b):
        pick = rng.choice(keys, size=len(keys), replace=True)
        out.append(_circ_stats(np.concatenate([by[k] for k in pick]))[stat])
    return float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5))


def _station_boot_ci(df: pd.DataFrame, stat: str, b: int = 2000,
                     seed: int = 0) -> tuple[float, float]:
    """관측소 군집 CI (하위 호환 래퍼)."""
    return _boot_ci(df, stat, "station_id", b, seed)


def direction_skill(df: pd.DataFrame, by_band: bool = True,
                    hs_min: float = INSTRUMENT_HS_MIN) -> pd.DataFrame:
    """파향 성적을 파고대별로 낸다. 단일 임계 대신 이 표를 산출물로 쓴다.

    hs_min은 계기 유효 하한이며 성능 임계가 아니다(`INSTRUMENT_HS_MIN` 참조).
    표본 수·관측소 수·CMAE의 관측소 군집 95% CI를 항상 함께 낸다 —
    CI 없이 CMAE만 인용하면 n=5의 9°가 성적으로 읽힌다.
    """
    if df.empty:
        return pd.DataFrame()
    d = df.copy()
    d["_hs"] = _obs_hs(d)
    d["_err"] = [circular_error(p, o) for p, o in zip(d["predicted"], d["observed"])]
    d = d[d["_hs"] >= hs_min]
    if d.empty:
        return pd.DataFrame()

    edges = [hs_min, *[b for b in HS_BANDS if b > hs_min], np.inf]
    rows, idx = [], []
    for lo, hi in zip(edges[:-1], edges[1:]):
        g = d[(d["_hs"] >= lo) & (d["_hs"] < hi)] if by_band else d
        if g.empty:
            continue
        st = _circ_stats(g["_err"].to_numpy(float))
        lo_ci, hi_ci = _station_boot_ci(g, "cmae")
        rows.append({"n": len(g), "stations": g["station_id"].nunique(),
                     **{k: round(v, 3) for k, v in st.items()},
                     "cmae_lo": round(lo_ci, 3), "cmae_hi": round(hi_ci, 3)})
        idx.append(f"{lo:.2f}-{hi:.2f}" if np.isfinite(hi) else f"{lo:.2f}+")
        if not by_band:
            break
    return pd.DataFrame(rows, index=idx)


def threshold_scan(df: pd.DataFrame,
                   thresholds: tuple[float, ...] = (0.0, 0.25, 0.5, 0.75, 1.0)
                   ) -> pd.DataFrame:
    """임계 민감도 표. **임계를 고르기 위한 것이 아니라 병기하기 위한 것이다.**

    한 임계만 보고하면 그 임계가 성적을 만들었는지 물리가 만들었는지 사후에
    귀속할 수 없다(CLAUDE.md §6-2와 같은 구조). 순위가 임계에 따라 뒤집히면
    그 결과는 임계의 산물이다.
    """
    if df.empty:
        return pd.DataFrame()
    d = df.copy()
    d["_hs"] = _obs_hs(d)
    d["_err"] = [circular_error(p, o) for p, o in zip(d["predicted"], d["observed"])]
    rows, idx = [], []
    for t in thresholds:
        g = d[d["_hs"] >= t]
        if g.empty:
            continue
        st = _circ_stats(g["_err"].to_numpy(float))
        lo, hi = _boot_ci(g, "cmae", "station_id")
        clo, chi = _boot_ci(g, "cmae", "cycle")
        rows.append({"n": len(g), "cycles": g["cycle"].nunique(),
                     "stations": g["station_id"].nunique(),
                     **{k: round(v, 3) for k, v in st.items()},
                     "cmae_lo": round(lo, 3), "cmae_hi": round(hi, 3),
                     "cmae_cyc_lo": round(clo, 3), "cmae_cyc_hi": round(chi, 3)})
        idx.append(f">={t:.2f}")
    return pd.DataFrame(rows, index=idx)


# --- 방향 센서 결함 검출 ---------------------------------------------------

WINDSEA_TP_MAX = 6.0
WINDSEA_WSPD_MIN = 6.0
WINDSEA_HS_MIN = 0.5
SENSOR_BAD_FRACTION = 0.40


def direction_sensor_check(cycle_month: str = "2026/08",
                           provider: str = "kma") -> pd.DataFrame:
    """관측소별 방향 센서 건전성. **모델을 전혀 쓰지 않는다.**

    젊은 풍성파(짧은 주기 + 유의한 바람 + 충분한 파고)에서는 파랑이 바람 방향으로
    발달하므로 파향과 풍향이 90°를 넘게 어긋날 수 없다. 그런 표본이 40%를 넘으면
    그 관측소의 방향 관측을 신뢰할 수 없다.

    이 검사가 필요한 이유: 2026-08-25에 "고파고에서 파향이 유의하게 나빠진다"는
    결론을 보고했는데, 그 신호의 대부분이 관측 결함이었다. 결함 관측소를 빼면
    열화가 +7.2° [+2.6, +10.4]에서 +2.4° [−2.3, +6.6]로 유의성을 잃는다.
    모델 오차로 오인되는 관측 결함은 검증 코드가 걸러야 한다.

    반환: station_id별 (n, 파향−풍향 순환평균, R, |차|>90° 비율, suspect).
    관측소를 하드코딩하지 않는다 — 데이터가 판정한다.
    """
    path = (settings.parquet_root / "obs" / provider
            / cycle_month.split("/")[0] / f"{cycle_month.split('/')[1]}.parquet")
    if not path.exists():
        return pd.DataFrame()
    obs = pd.read_parquet(path)
    obs = obs[obs["qc_flag"] == 0]
    piv = obs.pivot_table(index=["station_id", "ts"], columns="var",
                          values="value", aggfunc="mean").reset_index()
    need = {"dir", "wdir", "wspd", "tp", "hs"}
    if not need <= set(piv.columns):
        return pd.DataFrame()
    w = piv.dropna(subset=list(need))
    w = w[(w["tp"] < WINDSEA_TP_MAX) & (w["wspd"] >= WINDSEA_WSPD_MIN)
          & (w["hs"] >= WINDSEA_HS_MIN)]
    rows = []
    for sid, g in w.groupby("station_id"):
        if len(g) < 15:
            continue
        dd = (g["dir"] - g["wdir"] + 180.0) % 360.0 - 180.0
        rad = np.radians(dd)
        sn, cs = float(np.sin(rad).mean()), float(np.cos(rad).mean())
        bad = float(np.mean(np.abs(dd) > 90.0))
        rows.append({"station_id": sid, "n": len(g),
                     "wave_minus_wind": round(float(np.degrees(np.arctan2(sn, cs))), 1),
                     "R": round(float(np.hypot(sn, cs)), 3),
                     "frac_over_90": round(bad, 3),
                     "suspect": bad > SENSOR_BAD_FRACTION})
    return pd.DataFrame(rows).sort_values("frac_over_90", ascending=False)


def suspect_direction_stations(cycle_month: str = "2026/08") -> list[str]:
    """방향 센서가 물리적으로 불가능한 값을 내는 관측소 목록."""
    chk = direction_sensor_check(cycle_month)
    if chk.empty:
        return []
    return sorted(chk.loc[chk["suspect"], "station_id"].tolist())
