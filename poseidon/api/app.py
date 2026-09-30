"""Poseidon API 서버 — docs/api/openapi.yaml 계약의 1차 구현 (Phase 8).

실행:  .venv/bin/uvicorn poseidon.api.app:app --port 8811
대시보드: http://localhost:8811/
"""

from __future__ import annotations

import io
import json
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from poseidon.api.voyage import router as voyage_router
from poseidon.api.global_ocean import router as global_router
from poseidon.api.global_validation import router as global_validation_router
from poseidon.api.helm_coast import router as helm_coast_router
from poseidon.api.ports import router as ports_router
from poseidon.api.routing import router as routing_router
from poseidon.ai.correction import ResidualCorrector
from poseidon.core.catalog import Catalog
from poseidon.core.config import settings
from poseidon.physics.tides import HarmonicTide
from poseidon.engines.spectral_wave import grid as spec_grid
from poseidon.physics import sea_surface, ship_response
from poseidon.validation import collocate
from poseidon.validation.collocate import LAND_FRAC_MAX, filter_by_land, land_frac_of

app = FastAPI(title="Project Poseidon API", version="0.4.0")

WEB_ROOT = Path(__file__).resolve().parents[2] / "web" / "public"
app.mount("/console-assets", StaticFiles(directory=WEB_ROOT / "console"), name="console-assets")

app.include_router(voyage_router)
app.include_router(global_router)
app.include_router(global_validation_router)
app.include_router(helm_coast_router)
app.include_router(ports_router)
app.include_router(routing_router)
CORR_PATH = settings.data_root / "models" / "corr-hs.json"
TIDE_BUSAN = settings.data_root / "models" / "tide_busan.json"

HS_MAX_COLOR = 12.0  # 컬러맵 상한 [m]
HS_COLOR_GAMMA = 0.85  # hs.png 색 매핑 지수 — 범례가 눈금을 맞출 수 있게 노출한다

# 신선도 임계 [h] — **사이클 공칭 시각** 기준 (예보 유효구간이 지났는지가 판단 기준).
# 생산 시각(produced_at) 기준 경과는 produced_age_h로 따로 노출한다.
FRESH_REALTIME_H = 9.0
FRESH_DELAYED_H = 24.0

SPARSE_N = 20  # 관측소/구간 표본이 이보다 적으면 sparse로 표시 (통계 신뢰 경고)
BOOT_B = 1000  # 관측소 클러스터 부트스트랩 반복수
BOOT_SEED = 20260824  # 고정 시드 — 같은 표본이면 같은 CI가 나온다 (재현성)
NEAR_STATION_KM = 200.0  # 이 거리 안에 검증 관측소가 없으면 성적을 적용하지 않는다

# 오프라인 교차검증 성적. **배포 성적이 아니다** — 출처를 함께 노출한다 (CLAUDE.md §1-5).
OFFLINE_LSO_CV = {
    "gain_pct": 9.3, "ci95": [3.9, 14.3], "n": 2419,
    "method": "leave-one-station-out CV",
    "source": "docs/PHASE9_OPTIMIZATION.md",
    "note": "오프라인 교차검증 결과이며 배포된 보정기의 실측 성적이 아니다",
}


def _catalog() -> Catalog:
    return Catalog(settings.catalog_path)


def _zarr_stamp(uri: str) -> float:
    """zarr 디렉토리의 최신 mtime — 캐시 무효화 키 (같은 사이클 재실행 시 갱신)."""
    from pathlib import Path as _P
    try:
        return max((f.stat().st_mtime for f in _P(uri).rglob("*") if f.is_file()),
                   default=0.0)
    except OSError:
        return 0.0


@lru_cache(maxsize=8)
def _wave_forecast_cached(uri: str, stamp: float) -> xr.Dataset:
    return xr.open_zarr(uri, consolidated=False).load()


def _wave_forecast(cycle: str, level: str = "L1") -> xr.Dataset:
    src = "spectral_wave-L2" if level == "L2" else "spectral_wave-L1"
    rows = [r for r in _catalog().find_datasets("forecast", settings.domain_name, cycle)
            if r["source_id"] == src]
    if not rows:
        raise HTTPException(404, f"no {level} wave forecast for cycle {cycle}")
    uri = rows[-1]["uri"]
    return _wave_forecast_cached(uri, _zarr_stamp(uri))


def _has_level(cycle: str, level: str) -> bool:
    src = "spectral_wave-L2" if level == "L2" else "spectral_wave-L1"
    return any(r["source_id"] == src for r in
               _catalog().find_datasets("forecast", settings.domain_name, cycle))


def _latest_wave_cycle() -> str:
    """가장 **최신 사이클 시각**의 L1 예보 사이클.

    created_at(생산 시각) 정렬은 틀린다: 전량 재생산 시 오래된 사이클이 마지막에 만들어지면
    그것이 "최신"으로 뽑혀 20일 전 예보가 준실시간으로 표시된다(2026-08-24 실측 버그).
    사이클 문자열 `YYYYMMDDTHH`는 고정폭이라 사전식 정렬 = 시각순 정렬이 성립한다.
    동일 사이클이 여러 번 생산된 경우(CLAUDE.md §6-10)의 tie-break만 created_at으로 한다.
    """
    with _catalog()._conn() as c:
        row = c.execute(
            "SELECT cycle FROM dataset WHERE collection='forecast' AND source_id="
            "'spectral_wave-L1' ORDER BY cycle DESC, created_at DESC LIMIT 1").fetchone()
    if row is None:
        raise HTTPException(404, "no wave forecast available")
    return str(row["cycle"])


def _cycle_time(cycle: str) -> pd.Timestamp:
    """사이클 문자열 `YYYYMMDDTHH` → UTC 타임스탬프."""
    return pd.Timestamp(f"{cycle[:4]}-{cycle[4:6]}-{cycle[6:8]}T{cycle[9:11]}:00:00Z")


def _freshness(cycle_age_h: float, *, requested: bool = False) -> str:
    """realtime | delayed | historical | replay — 임계는 FRESH_* 상수."""
    if requested:
        return "replay"
    if cycle_age_h <= FRESH_REALTIME_H:
        return "realtime"
    if cycle_age_h <= FRESH_DELAYED_H:
        return "delayed"
    return "historical"


def _latest_surge_cycle() -> str | None:
    with _catalog()._conn() as c:
        r = c.execute("SELECT cycle FROM dataset WHERE source_id='swe-L2-surge'"
                      " ORDER BY cycle DESC, created_at DESC LIMIT 1").fetchone()
    return str(r["cycle"]) if r else None


# ── 검증 표본 데이터 층 (error_sample) ─────────────────────────────
#
# 원칙 (CLAUDE.md §1-5, §6-1·6-9):
#  - n을 항상 반환한다. 표본 0은 null이 아니라 status="no_samples"로 명시한다.
#  - 육지 오염 필터는 collocate.filter_by_land를 그대로 재사용한다(중복 구현 금지).
#  - 엔진 태그(advection/gse/collocation)가 섞이면 그 사실을 응답에 노출한다.

_SAMPLE_FEATURE_NUM = ("u10", "depth", "depth_model", "hs_gfsw", "land_frac", "predicted_raw")
_SAMPLE_FEATURE_TAG = ("collocation", "advection", "gse", "gse_gamma", "produced_at")
_EMPTY_COLS = ["cycle", "station_id", "valid_time", "lead_h", "var", "predicted",
               "observed", "features", "created_at", "land_frac", "error", "provider",
               "lat", "lon", *_SAMPLE_FEATURE_NUM, *_SAMPLE_FEATURE_TAG]


@lru_cache(maxsize=1)
def _station_locs() -> pd.DataFrame:
    """NDBC + KMA 관측소 좌표표 (station_id 인덱스). 없으면 빈 표."""
    tables = []
    for name in ("ndbc_stations", "kma_stations"):
        p = settings.data_root / "static" / f"{name}.parquet"
        if p.exists():
            tables.append(pd.read_parquet(p)[["station_id", "lat", "lon"]])
    if not tables:
        return pd.DataFrame(columns=["station_id", "lat", "lon"]).set_index("station_id")
    return pd.concat(tables).drop_duplicates("station_id").set_index("station_id")


def _sample_fingerprint(var: str) -> tuple[int, str]:
    """표본 캐시 무효화 키 — 콜로케이션을 다시 돌리면 자동으로 갱신된다."""
    with _catalog()._conn() as c:
        r = c.execute("SELECT count(*) AS n, coalesce(max(created_at),'') AS m"
                      " FROM error_sample WHERE var=?", (var,)).fetchone()
    return int(r["n"]), str(r["m"])


@lru_cache(maxsize=8)
def _error_frame_cached(var: str, fingerprint: tuple[int, str]) -> pd.DataFrame:
    rows = _catalog().error_samples(var)
    if not rows:
        return pd.DataFrame(columns=_EMPTY_COLS)
    df = pd.DataFrame(rows)
    feats = []
    for f in df["features"]:
        try:
            feats.append(json.loads(f) if isinstance(f, str) else (f or {}))
        except json.JSONDecodeError:
            feats.append({})
    for k in _SAMPLE_FEATURE_NUM:
        df[k] = pd.to_numeric(pd.Series([d.get(k) for d in feats], index=df.index),
                              errors="coerce")
    for k in _SAMPLE_FEATURE_TAG:
        df[k] = [("unknown" if d.get(k) is None else str(d.get(k))) for d in feats]
    df["land_frac"] = land_frac_of(df)          # 재사용: 구버전 표본은 NaN(미상) 유지
    _pred = df["predicted"].astype(float)
    _obs = df["observed"].astype(float)
    if _is_circular(var):
        # 각도 잔차는 [-180,180)으로 감싼다. 감싸지 않으면 359°−1°가 358°가 되어
        # 실제로는 2° 차이인 표본이 최악의 오차로 집계된다.
        df["error"] = (_pred - _obs + 180.0) % 360.0 - 180.0
    else:
        df["error"] = _pred - _obs
    df.attrs["circular"] = _is_circular(var)
    df["valid_time"] = pd.to_datetime(df["valid_time"], utc=True, format="mixed")
    df["provider"] = df["station_id"].astype(str).str.split(":").str[0]
    locs = _station_locs()
    df["lat"] = df["station_id"].map(locs["lat"]) if len(locs) else np.nan
    df["lon"] = df["station_id"].map(locs["lon"]) if len(locs) else np.nan
    return df


def _error_frame(var: str) -> pd.DataFrame:
    return _error_frame_cached(var, _sample_fingerprint(var))


def _apply_filter(df: pd.DataFrame, *, clean: bool, land_frac_max: float,
                  cycle: str | None, station_id: str | None,
                  lead: float | None) -> pd.DataFrame:
    if cycle is not None:
        df = df[df["cycle"] == cycle]
    if station_id is not None:
        df = df[df["station_id"] == station_id]
    if lead is not None:
        df = df[np.isclose(df["lead_h"].astype(float), float(lead), atol=0.5)]
    if clean and not df.empty:
        df = filter_by_land(df, land_frac_max)      # legacy(미상) 표본은 drop
    return df


def _filter_block(clean: bool, land_frac_max: float, cycle: str | None,
                  station_id: str | None, n_all: int, n_kept: int) -> dict:
    return {
        "clean": clean,
        "land_frac_max": land_frac_max if clean else None,
        "legacy_samples": "drop" if clean else "keep",
        "cycle": cycle, "station_id": station_id,
        "n_before_filter": n_all, "n_excluded_by_filter": n_all - n_kept,
        "definition": "육지 가중치 land_frac <= land_frac_max 인 표본만 사용"
                      " (poseidon.validation.collocate.filter_by_land)",
    }


def _metrics(df: pd.DataFrame) -> dict:
    """기본 오차 지표. 표본이 0이면 n=0과 함께 지표는 None (0으로 위장 금지)."""
    n = int(len(df))
    if n == 0:
        return {"n": 0, "rmse": None, "bias": None, "mae": None, "si": None,
                "obs_mean": None, "pred_mean": None}
    err = df["error"].to_numpy(float)
    obs = df["observed"].to_numpy(float)
    # attrs는 필터링 과정에서 유실될 수 있다. 표본이 스스로 밝히는 var로 판단한다.
    circular = "var" in df.columns and _is_circular(str(df["var"].iloc[0]))
    if circular:
        # 각도: 잔차는 이미 [-180,180)으로 감싸여 들어온다. 편차는 원형 평균이고
        # SI는 정의되지 않는다 — 분모(관측 각도의 평균)가 의미를 갖지 않기 때문이다.
        rad = np.radians(err)
        sn, cs = float(np.sin(rad).mean()), float(np.cos(rad).mean())
        return {
            "n": n,
            "rmse": round(float(np.sqrt((err ** 2).mean())), 4),
            "bias": round(float(np.degrees(np.arctan2(sn, cs))), 4),
            "mae": round(float(np.abs(err).mean()), 4),
            "si": None, "R": round(float(np.hypot(sn, cs)), 4),
            "within_45deg": round(float(np.mean(np.abs(err) < 45.0)), 4),
            "obs_mean": None, "pred_mean": None,
        }
    bias = float(err.mean())
    rmse = float(np.sqrt((err ** 2).mean()))
    obs_mean = float(obs.mean())
    sd_err = float(np.sqrt(((err - bias) ** 2).mean()))
    return {
        "n": n,
        "rmse": round(rmse, 4), "bias": round(bias, 4),
        "mae": round(float(np.abs(err).mean()), 4),
        "si": round(sd_err / obs_mean, 4) if obs_mean > 1e-6 else None,
        "obs_mean": round(obs_mean, 4), "pred_mean": round(float(df["predicted"].mean()), 4),
    }


def _boot_ci(df: pd.DataFrame, b: int = BOOT_B) -> dict:
    """관측소 클러스터 부트스트랩 RMSE/bias 95% CI.

    관측소별 (n, Σe, Σe²)만 재표집하면 RMSE = sqrt(Σsse/Σn)이 정확히 복원되므로
    B=1000이어도 O(B·S)로 끝난다. 관측소가 2개 미만이면 CI를 만들지 않는다.
    """
    if len(df) < SPARSE_N or df["station_id"].nunique() < 3:
        return {"rmse_ci95": None, "bias_ci95": None,
                "ci_method": "표본 부족 — CI 산출 안 함"}
    g = df.groupby("station_id")["error"]
    cnt = g.count().to_numpy(float)
    se = g.sum().to_numpy(float)
    sse = g.apply(lambda x: float((x.to_numpy(float) ** 2).sum())).to_numpy(float)
    rng = np.random.default_rng(BOOT_SEED)
    idx = rng.integers(0, len(cnt), size=(b, len(cnt)))
    n_b = cnt[idx].sum(axis=1)
    rmse_b = np.sqrt(sse[idx].sum(axis=1) / n_b)
    bias_b = se[idx].sum(axis=1) / n_b
    return {
        "rmse_ci95": [round(float(np.percentile(rmse_b, 2.5)), 4),
                      round(float(np.percentile(rmse_b, 97.5)), 4)],
        "bias_ci95": [round(float(np.percentile(bias_b, 2.5)), 4),
                      round(float(np.percentile(bias_b, 97.5)), 4)],
        "ci_method": f"관측소 클러스터 부트스트랩 B={b}, seed={BOOT_SEED}",
    }


def _engine_tags(df: pd.DataFrame) -> dict:
    """표본이 어떤 엔진 설정으로 만들어졌는지. 섞였으면 숨기지 않고 조합별 건수를 낸다."""
    keys = ["advection", "gse", "gse_gamma", "collocation"]
    if df.empty:
        return {"tags": None, "mixed": False, "combinations": []}
    combos = df.groupby(keys, dropna=False).size().reset_index(name="n")
    combos = combos.sort_values("n", ascending=False)
    rows = [{**{k: str(r[k]) for k in keys}, "n": int(r["n"])} for _, r in combos.iterrows()]
    return {"tags": {k: rows[0][k] for k in keys}, "mixed": len(rows) > 1,
            "combinations": rows}


def _corrector_block() -> dict:
    """AI 보정 상태. deployed는 **모델 파일 실존 여부**로만 판정한다 (문서 성적 금지)."""
    deployed = CORR_PATH.exists()
    version = None
    if deployed:
        try:
            version = str(json.loads(CORR_PATH.read_text()).get("version"))
        except (OSError, json.JSONDecodeError):
            version = "unreadable"
    return {"deployed": deployed,
            "path": str(CORR_PATH.relative_to(settings.data_root.parent))
            if settings.data_root.parent in CORR_PATH.parents else str(CORR_PATH),
            "model_version": version,
            "offline_lso_cv": OFFLINE_LSO_CV}


def _period(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"first_valid_time": None, "last_valid_time": None,
                "first_cycle": None, "last_cycle": None}
    return {"first_valid_time": df["valid_time"].min().isoformat(),
            "last_valid_time": df["valid_time"].max().isoformat(),
            "first_cycle": str(df["cycle"].min()), "last_cycle": str(df["cycle"].max())}


def _haversine_km(lat1: float, lon1: float, lat2: np.ndarray, lon2: np.ndarray) -> np.ndarray:
    r = 6371.0088
    p1, p2 = np.radians(lat1), np.radians(np.asarray(lat2, float))
    dp = p2 - p1
    dl = np.radians(np.asarray(lon2, float) - lon1)
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * r * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


# ── 시스템 ─────────────────────────────────────────────────────────
@app.get("/v1/system/cycles")
def system_cycles() -> list[dict]:
    with _catalog()._conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT run_id, cycle, engine, domain, status, degraded FROM forecast_run"
            " ORDER BY cycle DESC LIMIT 40")]


@app.get("/v1/system/forecast_cycles")
def system_forecast_cycles(limit: int = 40) -> dict:
    """선택 가능한 예보 사이클 목록 — 레벨별 가용성과 검증 표본 수를 함께 준다.

    `produced_at`은 카탈로그 등록 시각(= 예보 zarr 생산 시각)이다. 같은 사이클을 다시
    돌리면 행이 하나 더 생기므로(CLAUDE.md §6-10) **최신 1건**만 대표로 쓴다.
    """
    now = pd.Timestamp.now(tz="UTC")
    with _catalog()._conn() as c:
        ds_rows = [dict(r) for r in c.execute(
            "SELECT cycle, source_id, max(created_at) AS created_at FROM dataset"
            " WHERE collection IN ('forecast','forcing','boundary') AND cycle LIKE '2%T%'"
            " GROUP BY cycle, source_id")]
        runs = [dict(r) for r in c.execute(
            "SELECT cycle, engine, status, degraded FROM forecast_run")]
        smp = [dict(r) for r in c.execute(
            "SELECT cycle, var, count(*) AS n FROM error_sample GROUP BY cycle, var")]

    clean_counts: dict[tuple[str, str], int] = {}
    for var in ("hs", "hs_l2"):
        df = _error_frame(var)
        if df.empty:
            continue
        kept = filter_by_land(df, LAND_FRAC_MAX)
        for cyc, n in kept.groupby("cycle").size().items():
            clean_counts[(str(cyc), var)] = int(n)

    by_cycle: dict[str, dict] = {}
    for r in ds_rows:
        e = by_cycle.setdefault(r["cycle"], {"cycle": r["cycle"], "sources": {}})
        e["sources"][r["source_id"]] = r["created_at"]
    for r in smp:
        e = by_cycle.setdefault(r["cycle"], {"cycle": r["cycle"], "sources": {}})
        e.setdefault("samples", {})[r["var"]] = int(r["n"])
    run_map: dict[str, list[dict]] = {}
    for r in runs:
        run_map.setdefault(r["cycle"], []).append(
            {"engine": r["engine"], "status": r["status"], "degraded": bool(r["degraded"])})

    out = []
    for cyc in sorted(by_cycle, reverse=True)[:max(int(limit), 1)]:
        e = by_cycle[cyc]
        src = e["sources"]
        samples = e.get("samples", {})
        produced = src.get("spectral_wave-L1")
        age = float((now - _cycle_time(cyc)).total_seconds() / 3600.0)
        out.append({
            "cycle": cyc,
            "has_l1": "spectral_wave-L1" in src,
            "has_l2": "spectral_wave-L2" in src,
            "has_surge": "swe-L2-surge" in src,
            "has_forcing": "noaa-gfs-0p25" in src,
            "has_boundary": "noaa-gfswave-0p25" in src,
            "produced_at": produced,
            "cycle_age_h": round(age, 2),
            "freshness": _freshness(age),
            "n_samples": {"hs": samples.get("hs", 0), "hs_l2": samples.get("hs_l2", 0)},
            "n_samples_clean": {"hs": clean_counts.get((cyc, "hs"), 0),
                                "hs_l2": clean_counts.get((cyc, "hs_l2"), 0)},
            "runs": run_map.get(cyc, []),
        })
    return {"n": len(out), "latest_l1_cycle": _latest_wave_cycle(),
            "cycles": out,
            "note": "n_samples_clean은 land_frac<=0.05 필터 통과 표본 수"}


@app.get("/v1/system/status")
def system_status() -> dict:
    """대시보드 최상단용 — 지금 보는 것이 언제 것인가 + 무엇이 비어 있는가."""
    now = pd.Timestamp.now(tz="UTC")
    cycle = _latest_wave_cycle()
    ds = _wave_forecast(cycle)
    age = float((now - _cycle_time(cycle)).total_seconds() / 3600.0)
    produced = ds.attrs.get("produced_at")
    prod_age = (float((now - pd.Timestamp(str(produced))).total_seconds() / 3600.0)
                if produced else None)
    vt = pd.to_datetime(ds.valid_time.values, utc=True)

    surge_cycle = _latest_surge_cycle()
    surge = None
    if surge_cycle:
        off = float((_cycle_time(cycle) - _cycle_time(surge_cycle)).total_seconds() / 3600.0)
        surge = {"cycle": surge_cycle, "offset_h_from_wave": round(off, 2),
                 "same_cycle": surge_cycle == cycle,
                 "warning": None if abs(off) <= 6.0 else
                 f"파고장과 다른 사이클 (차이 {off / 24:.1f}일) — 같은 상황이 아니다"}

    with _catalog()._conn() as c:
        gaps = [dict(r) for r in c.execute(
            "SELECT cycle, engine, status, degraded FROM forecast_run WHERE cycle NOT IN"
            " (SELECT cycle FROM dataset WHERE source_id='spectral_wave-L1')"
            " ORDER BY cycle DESC LIMIT 10")]

    val = {}
    for var in ("hs", "hs_l2"):
        df = _error_frame(var)
        kept = filter_by_land(df, LAND_FRAC_MAX) if not df.empty else df
        val[var] = {"n": int(len(df)), "n_clean": int(len(kept)),
                    "n_stations": int(kept["station_id"].nunique()) if len(kept) else 0,
                    "n_cycles": int(kept["cycle"].nunique()) if len(kept) else 0}

    return {
        "now": now.isoformat(),
        "cycle": cycle,
        "produced_at": produced,
        "cycle_age_h": round(age, 2),
        "produced_age_h": None if prod_age is None else round(prod_age, 2),
        "freshness": _freshness(age),
        "freshness_thresholds_h": {"realtime_max": FRESH_REALTIME_H,
                                   "delayed_max": FRESH_DELAYED_H,
                                   "basis": "cycle_age_h (사이클 공칭 시각 기준)"},
        "valid_time": {"first": vt[0].isoformat(), "last": vt[-1].isoformat(),
                       "covers_now": bool(vt[0] <= now <= vt[-1])},
        "engine": {k: (str(v) if not isinstance(v, (int, float)) else v)
                   for k, v in ds.attrs.items()},
        "levels": {"L1": True, "L2": _has_level(cycle, "L2")},
        "surge": surge,
        "runs_without_wave_forecast": gaps,
        "validation": val,
        "corrector": _corrector_block(),
    }


# ── 격자 필드 ──────────────────────────────────────────────────────
@app.get("/v1/field/meta")
def field_meta(cycle: str | None = None, level: str = "L1") -> dict:
    requested = cycle is not None
    latest = _latest_wave_cycle()
    cycle = cycle or latest
    ds = _wave_forecast(cycle, level)
    now = pd.Timestamp.now(tz="UTC")
    age = float((now - _cycle_time(cycle)).total_seconds() / 3600.0)
    produced = ds.attrs.get("produced_at")
    prod_age = (float((now - pd.Timestamp(str(produced))).total_seconds() / 3600.0)
                if produced else None)
    has_l2 = _has_level(cycle, "L2")
    l2_bounds = None
    if has_l2:
        d2 = _wave_forecast(cycle, "L2")
        l2_bounds = {"west": float(d2.longitude.min()), "east": float(d2.longitude.max()),
                     "south": float(d2.latitude.min()), "north": float(d2.latitude.max())}
    vt = pd.to_datetime(ds.valid_time.values, utc=True)
    return {
        "cycle": cycle, "level": level, "has_l2": has_l2,
        "leads_h": [float(v) for v in ds.lead.values],
        "valid_times": [v.isoformat() for v in vt],
        "bounds": {"west": float(ds.longitude.min()), "east": float(ds.longitude.max()),
                   "south": float(ds.latitude.min()), "north": float(ds.latitude.max())},
        "hs_color_max": HS_MAX_COLOR,
        # ── 이하 v0.4 추가 (기존 필드는 그대로) ──
        "hs_color_gamma": HS_COLOR_GAMMA,
        "hs_color_scale": f"turbo, norm = clip(hs/{HS_MAX_COLOR:g},0,1)**{HS_COLOR_GAMMA:g}"
                          " — 비선형. 범례 눈금은 이 식으로 배치해야 맞는다",
        "latest_cycle": latest,
        "is_latest": cycle == latest,
        "produced_at": produced,
        "cycle_age_h": round(age, 2),
        "produced_age_h": None if prod_age is None else round(prod_age, 2),
        "freshness": _freshness(age, requested=requested and cycle != latest),
        "freshness_thresholds_h": {"realtime_max": FRESH_REALTIME_H,
                                   "delayed_max": FRESH_DELAYED_H,
                                   "basis": "cycle_age_h"},
        "covers_now": bool(vt[0] <= now <= vt[-1]),
        "engine": {k: (v if isinstance(v, (int, float)) else str(v))
                   for k, v in ds.attrs.items()},
        "l2_bounds": l2_bounds,
        "surge_cycle": _latest_surge_cycle(),
    }


@app.get("/v1/field/hs.png")
def field_hs_png(cycle: str | None = None, lead: float = 0.0,
                 upscale: int = 4, level: str = "L1") -> Response:
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import cm

    cycle = cycle or _latest_wave_cycle()
    ds = _wave_forecast(cycle, level)
    i = int(np.argmin(np.abs(ds.lead.values - lead)))
    hs = ds.hs.isel(lead=i).values
    if level == "L2":
        upscale = min(upscale, 2)                        # 0.05°는 2×면 충분

    sea = np.isfinite(hs) & (hs > 0.01)
    upscale = int(np.clip(upscale, 1, 6))
    if upscale > 1:
        from poseidon.api.raster import upsample_sea
        hs, sea_a = upsample_sea(hs, sea, upscale)
    else:
        sea_a = sea.astype(float)

    # 저파고 대비 강화. 비선형이므로 범례 눈금은 /v1/field/meta의 hs_color_gamma로 배치할 것
    norm = np.clip(hs / HS_MAX_COLOR, 0.0, 1.0) ** HS_COLOR_GAMMA
    rgba = cm.turbo(norm)
    rgba[..., 3] = 0.86 * sea_a
    img = (rgba[::-1] * 255).astype(np.uint8)            # 북쪽이 위로

    from PIL import Image
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, format="PNG")
    return Response(buf.getvalue(), media_type="image/png",
                    headers={"Cache-Control": "max-age=300"})


@app.get("/v1/field/wind.json")
def field_wind(cycle: str | None = None, lead: float = 0.0, stride: int = 4) -> dict:
    """입자 애니메이션용 바람장 서브샘플 (GFS 강제장)."""
    cycle = cycle or _latest_wave_cycle()
    rows = _catalog().find_datasets("forcing", settings.domain_name, cycle)
    if not rows:
        raise HTTPException(404, "no forcing")
    f = xr.open_zarr(rows[-1]["uri"], consolidated=False)
    t = f.time[0].values + np.timedelta64(int(lead * 3600), "s")
    w = f.interp(time=min(t, f.time[-1].values))
    s = max(int(stride), 1)
    return {
        "lats": [round(float(v), 3) for v in w.latitude.values[::s]],
        "lons": [round(float(v), 3) for v in w.longitude.values[::s]],
        "u": np.round(np.nan_to_num(w.u10.values[::s, ::s]), 1).tolist(),
        "v": np.round(np.nan_to_num(w.v10.values[::s, ::s]), 1).tolist(),
    }


@app.get("/v1/field/storm.json")
def field_storm(cycle: str | None = None) -> dict:
    """리드별 최저해면기압 위치 (태풍 트랙 근사)."""
    cycle = cycle or _latest_wave_cycle()
    rows = _catalog().find_datasets("forcing", settings.domain_name, cycle)
    if not rows:
        raise HTTPException(404, "no forcing")
    f = xr.open_zarr(rows[-1]["uri"], consolidated=False)
    track = []
    for k in range(f.sizes["time"]):
        msl = f.msl.isel(time=k).values
        j, i = np.unravel_index(np.nanargmin(msl), msl.shape)
        p_hpa = float(msl[j, i]) / 100.0
        if p_hpa < 995.0:                                # 명확한 저기압만
            track.append({"time": str(f.time.values[k]),
                          "lat": float(f.latitude[j]), "lon": float(f.longitude[i]),
                          "p_hpa": round(p_hpa, 1)})
    return {"cycle": cycle, "track": track}


# ── 검증 성적 (/v1/skill — openapi.yaml의 미구현 계약을 채운다) ────

_VAR_LEVEL = {"hs": "L1", "hs_l2": "L2",
              "tp": "L1", "tp_l2": "L2",
              "tm02": "L1", "tm02_l2": "L2",
              "dir": "L1", "dir_l2": "L2"}
_VAR_UNIT = {"hs": "m", "tp": "s", "tm02": "s", "dir": "deg"}


def _is_circular(var: str) -> bool:
    """파향은 순환량이다. 산술 오차·SI가 성립하지 않는다."""
    return var.split("_")[0] == "dir"


def _check_var(var: str) -> str:
    if var not in _VAR_LEVEL:
        raise HTTPException(400, f"var must be one of {sorted(_VAR_LEVEL)}")
    return var


def _no_samples(var: str, filt: dict, extra: dict | None = None) -> dict:
    """표본 0 — null이 아니라 명시적 상태로 표현한다 (CLAUDE.md §1-5)."""
    return {"var": var, "level": _VAR_LEVEL[var], "status": "no_samples",
            "reason": "필터 조건을 통과한 error_sample이 없다 — 검증 불가",
            "n": 0, "n_stations": 0, "n_cycles": 0, "filter": filt,
            "rmse": None, "bias": None, "mae": None, "si": None,
            "by_lead": [], "engine_tags": None, "engine_tags_mixed": False,
            "corrector": _corrector_block(), **(extra or {})}


@lru_cache(maxsize=16)
def _skill_payload(var: str, clean: bool, land_frac_max: float, cycle: str | None,
                   station_id: str | None, ci: bool, fingerprint: tuple[int, str]) -> dict:
    all_df = _error_frame(var)
    df = _apply_filter(all_df, clean=clean, land_frac_max=land_frac_max,
                       cycle=cycle, station_id=station_id, lead=None)
    filt = _filter_block(clean, land_frac_max, cycle, station_id, len(all_df), len(df))
    if df.empty:
        return _no_samples(var, filt)

    tags = _engine_tags(df)
    by_lead = []
    for lead, g in df.groupby("lead_h"):
        row = {"lead_h": round(float(lead), 2), "n_stations": int(g["station_id"].nunique()),
               **_metrics(g)}
        row["sparse"] = row["n"] < SPARSE_N
        if ci:
            row.update(_boot_ci(g))
        by_lead.append(row)
    by_lead.sort(key=lambda r: r["lead_h"])

    hs_bins = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 99.0]
    by_hs = []
    cut = pd.cut(df["observed"].astype(float), hs_bins, right=False)
    for interval, g in df.groupby(cut, observed=True):
        row = {"obs_hs_min": float(interval.left), "obs_hs_max": float(interval.right),
               "n_stations": int(g["station_id"].nunique()), **_metrics(g)}
        row["sparse"] = row["n"] < SPARSE_N
        by_hs.append(row)

    out = {
        "var": var, "level": _VAR_LEVEL[var], "status": "ok",
        "filter": filt,
        "n": int(len(df)),
        "n_stations": int(df["station_id"].nunique()),
        "n_cycles": int(df["cycle"].nunique()),
        "period": _period(df),
        **{k: v for k, v in _metrics(df).items() if k != "n"},
        "engine_tags": tags["tags"], "engine_tags_mixed": tags["mixed"],
        "engine_tag_combinations": tags["combinations"],
        "station_mix": {str(k): int(v) for k, v in df["provider"].value_counts().items()},
        "observed_range": {"min": round(float(df["observed"].min()), 3),
                           "p95": round(float(df["observed"].quantile(0.95)), 3),
                           "max": round(float(df["observed"].max()), 3),
                           "lead_h_max": round(float(df["lead_h"].max()), 2)},
        "by_lead": by_lead,
        "by_obs_hs": by_hs,
        "corrector": _corrector_block(),
    }
    if ci:
        out.update(_boot_ci(df))
    return out


@app.get("/v1/skill")
def skill(var: str = "hs", clean: bool = True, land_frac_max: float = LAND_FRAC_MAX,
          cycle: str | None = None, station_id: str | None = None,
          ci: bool = True) -> dict:
    """검증 성적 종합 — 전체 + 리드별 + 관측 파고대별.

    clean=true(기본)면 육지 오염 표본을 collocate.filter_by_land로 제외한다.
    n / n_stations / n_cycles는 항상 응답에 있고, 표본이 없으면 status="no_samples"다.
    """
    _check_var(var)
    return _skill_payload(var, bool(clean), float(land_frac_max), cycle, station_id,
                          bool(ci), _sample_fingerprint(var))


@lru_cache(maxsize=16)
def _skill_stations_payload(var: str, clean: bool, land_frac_max: float,
                            cycle: str | None, fingerprint: tuple[int, str]) -> dict:
    all_df = _error_frame(var)
    df = _apply_filter(all_df, clean=clean, land_frac_max=land_frac_max,
                       cycle=cycle, station_id=None, lead=None)
    filt = _filter_block(clean, land_frac_max, cycle, None, len(all_df), len(df))
    if df.empty:
        return _no_samples(var, filt, {"stations": [], "sparse_threshold_n": SPARSE_N})
    stations = []
    for sid, g in df.groupby("station_id"):
        lat = float(g["lat"].iloc[0]) if np.isfinite(g["lat"].iloc[0]) else None
        lon = float(g["lon"].iloc[0]) if np.isfinite(g["lon"].iloc[0]) else None
        m = _metrics(g)
        stations.append({
            "station_id": str(sid), "provider": str(g["provider"].iloc[0]),
            "lat": lat, "lon": lon,
            **m,
            "sparse": m["n"] < SPARSE_N,
            "n_cycles": int(g["cycle"].nunique()),
            "first_cycle": str(g["cycle"].min()), "last_cycle": str(g["cycle"].max()),
            "land_frac_mean": (round(float(g["land_frac"].mean()), 4)
                               if np.isfinite(g["land_frac"]).any() else None),
            "has_location": lat is not None and lon is not None,
        })
    stations.sort(key=lambda s: -s["n"])
    return {"var": var, "level": _VAR_LEVEL[var], "status": "ok", "filter": filt,
            "n": int(len(df)), "n_stations": len(stations),
            "n_cycles": int(df["cycle"].nunique()),
            "sparse_threshold_n": SPARSE_N,
            "no_location": [s["station_id"] for s in stations if not s["has_location"]],
            "stations": stations}


@app.get("/v1/skill/stations")
def skill_stations(var: str = "hs", clean: bool = True,
                   land_frac_max: float = LAND_FRAC_MAX,
                   cycle: str | None = None) -> dict:
    """관측소별 성적 — 지도에 올릴 수 있게 lat/lon 포함. n<20은 sparse=true."""
    _check_var(var)
    return _skill_stations_payload(var, bool(clean), float(land_frac_max), cycle,
                                   _sample_fingerprint(var))


@lru_cache(maxsize=32)
def _skill_station_payload(station_id: str, var: str, clean: bool, land_frac_max: float,
                           fingerprint: tuple[int, str]) -> dict:
    all_df = _error_frame(var)
    if station_id not in set(all_df.get("station_id", pd.Series(dtype=str))):
        raise HTTPException(404, f"no {var} samples for station {station_id}")
    df = _apply_filter(all_df, clean=clean, land_frac_max=land_frac_max,
                       cycle=None, station_id=station_id, lead=None)
    filt = _filter_block(clean, land_frac_max, None, station_id, len(all_df), len(df))
    if df.empty:
        return _no_samples(var, filt, {"station_id": station_id, "series": []})
    tags = _engine_tags(df)
    series = []
    for cyc, g in df.sort_values(["cycle", "lead_h"]).groupby("cycle"):
        pts = [{"valid_time": r.valid_time.isoformat(), "lead_h": round(float(r.lead_h), 2),
                "predicted": round(float(r.predicted), 3),
                "observed": round(float(r.observed), 3),
                "predicted_raw": (None if not np.isfinite(r.predicted_raw)
                                  else round(float(r.predicted_raw), 3)),
                "land_frac": (None if not np.isfinite(r.land_frac)
                              else round(float(r.land_frac), 4)),
                "u10": None if not np.isfinite(r.u10) else round(float(r.u10), 2)}
               for r in g.itertuples()]
        series.append({"cycle": str(cyc), "n": len(pts),
                       "produced_at": str(g["produced_at"].iloc[0]), "points": pts})
    series.sort(key=lambda s: s["cycle"])
    by_lead = []
    for lead, g in df.groupby("lead_h"):
        row = {"lead_h": round(float(lead), 2), **_metrics(g)}
        row["sparse"] = row["n"] < SPARSE_N
        by_lead.append(row)
    by_lead.sort(key=lambda r: r["lead_h"])
    lat = float(df["lat"].iloc[0]) if np.isfinite(df["lat"].iloc[0]) else None
    lon = float(df["lon"].iloc[0]) if np.isfinite(df["lon"].iloc[0]) else None
    return {"station_id": station_id, "provider": str(df["provider"].iloc[0]),
            "lat": lat, "lon": lon, "var": var, "level": _VAR_LEVEL[var],
            "status": "ok", "filter": filt,
            "n": int(len(df)), "n_cycles": int(df["cycle"].nunique()),
            "period": _period(df),
            **{k: v for k, v in _metrics(df).items() if k != "n"},
            "sparse": len(df) < SPARSE_N,
            "engine_tags": tags["tags"], "engine_tags_mixed": tags["mixed"],
            "by_lead": by_lead, "series": series}


@app.get("/v1/skill/station/{station_id:path}")
def skill_station(station_id: str, var: str = "hs", clean: bool = True,
                  land_frac_max: float = LAND_FRAC_MAX) -> dict:
    """한 관측소의 예보 vs 관측 짝 — **사이클별로 묶어** 반환(스파게티 플롯용).

    평탄화하면 사이클 간 예보 흔들림(run-to-run consistency)을 그릴 수 없다.
    """
    _check_var(var)
    return _skill_station_payload(station_id, var, bool(clean), float(land_frac_max),
                                  _sample_fingerprint(var))


@lru_cache(maxsize=8)
def _skill_pairs_payload(clean: bool, land_frac_max: float,
                         fp1: tuple[int, str], fp2: tuple[int, str]) -> dict:
    """L1 vs L2 — (cycle, station_id, valid_time, lead_h)로 **매칭한 표본만** 비교.

    서로 다른 표본을 나란히 놓는 순간 "L2가 천해 27% 개선" 같은 허구가 생긴다
    (CLAUDE.md §6-1). 매칭을 서버가 하므로 클라이언트가 미매칭 비교를 할 방법이 없다.
    """
    key = ["cycle", "station_id", "valid_time", "lead_h"]
    raw_a, raw_b = _error_frame("hs"), _error_frame("hs_l2")
    a = _apply_filter(raw_a, clean=clean, land_frac_max=land_frac_max,
                      cycle=None, station_id=None, lead=None)
    b = _apply_filter(raw_b, clean=clean, land_frac_max=land_frac_max,
                      cycle=None, station_id=None, lead=None)
    filt = _filter_block(clean, land_frac_max, None, None,
                         len(raw_a) + len(raw_b), len(a) + len(b))
    filt["n_l1_after_filter"] = int(len(a))
    filt["n_l2_after_filter"] = int(len(b))
    filt["join_key"] = key
    if a.empty or b.empty:
        return {"status": "no_samples", "n_pairs": 0, "filter": filt,
                "reason": "L1 또는 L2 표본이 없다 — 비교 불가"}
    m = a.merge(b, on=key, suffixes=("_l1", "_l2"))
    if m.empty:
        return {"status": "no_samples", "n_pairs": 0, "filter": filt,
                "reason": "공통 (cycle, station, valid_time, lead) 표본이 없다 — 비교 불가"}
    out = {"status": "ok", "n_pairs": int(len(m)),
           "n_stations": int(m["station_id"].nunique()),
           "n_cycles": int(m["cycle"].nunique()),
           "cycles": sorted(str(c) for c in m["cycle"].unique()),
           "filter": filt}
    for lvl in ("l1", "l2"):
        d = pd.DataFrame({"error": m[f"error_{lvl}"], "observed": m[f"observed_{lvl}"],
                          "predicted": m[f"predicted_{lvl}"],
                          "station_id": m["station_id"]})
        out[lvl] = {**_metrics(d), **_boot_ci(d)}
    r1, r2 = out["l1"]["rmse"], out["l2"]["rmse"]
    out["rmse_delta_l2_minus_l1"] = round(r2 - r1, 4)
    out["rmse_gain_pct_l2_over_l1"] = round(100.0 * (r1 - r2) / r1, 2) if r1 else None
    out["note"] = ("동일 표본 매칭 비교. 부호: rmse_delta>0 이면 L2가 더 나쁘다."
                   " 유의성 판단은 두 CI의 겹침만으로 하지 말 것(짝지은 검정 미구현).")
    return out


@app.get("/v1/skill/pairs")
def skill_pairs(clean: bool = True, land_frac_max: float = LAND_FRAC_MAX) -> dict:
    """L1/L2 매칭 비교 전용 — 표본 구성이 다른 두 성적을 나란히 놓을 수 없게 한다."""
    return _skill_pairs_payload(bool(clean), float(land_frac_max),
                                _sample_fingerprint("hs"), _sample_fingerprint("hs_l2"))


# ── 점 예보 (corrected 투명성 계약) ────────────────────────────────

def _serving_features(cycle: str, lat: float, lon: float,
                      valid_times: np.ndarray) -> dict[str, np.ndarray]:
    """보정기 서빙 피처 — 학습(collocate.py)과 **동일한 원천**에서 만든다.

    학습은 실제 U10·수심·GFS-Wave를 쓰는데 서빙이 상수 스텁(u10=8, depth=50)을 쓰면
    train/serve skew가 생겨 보정이 오히려 예보를 악화시킨다 (교차검증 실측:
    실피처 RMSE 0.1577 vs 스텁 0.1712 — 후자는 무보정 0.1634보다 나쁘다).
    """
    cat = _catalog()
    n = len(valid_times)
    u10 = np.full(n, np.nan)
    hs_gfsw = np.full(n, np.nan)
    depth = float("nan")

    rows = cat.find_datasets("forcing", settings.domain_name, cycle)
    if rows:
        f = xr.open_zarr(rows[-1]["uri"], consolidated=False)
        t = np.clip(np.asarray(valid_times, dtype="datetime64[ns]"),
                    f.time.values[0], f.time.values[-1])
        w = f.interp(time=xr.DataArray(t, dims="lead"), latitude=lat, longitude=lon)
        u10 = np.hypot(np.asarray(w.u10), np.asarray(w.v10))
    rows = cat.find_datasets("boundary", settings.domain_name, cycle)
    if rows:
        b = xr.open_zarr(rows[-1]["uri"], consolidated=False)
        t = np.clip(np.asarray(valid_times, dtype="datetime64[ns]"),
                    b.time.values[0], b.time.values[-1])
        hs_gfsw = np.asarray(b.swh.interp(time=xr.DataArray(t, dims="lead"),
                                          latitude=lat, longitude=lon))
    etopo = settings.data_root / "static" / "etopo2022_60s_east_asia.nc"
    if etopo.exists():
        z = xr.open_dataset(etopo).z.interp(lat=lat, lon=lon)
        depth = float(max(-float(z), 1.0))
    return {"u10": u10, "hs_gfsw": hs_gfsw, "depth": np.full(n, depth)}


def _lead_skill_map(var: str) -> list[dict]:
    p = _skill_payload(var, True, LAND_FRAC_MAX, None, None, True, _sample_fingerprint(var))
    return p["by_lead"] if p["status"] == "ok" else []


def _skill_for_lead(table: list[dict], lead_h: float) -> dict | None:
    """가장 가까운 리드의 실측 성적. 표본이 없으면 None (0으로 위장하지 않는다)."""
    if not table:
        return None
    row = min(table, key=lambda r: abs(r["lead_h"] - lead_h))
    if abs(row["lead_h"] - lead_h) > 1.5:
        return None
    return {"lead_h": row["lead_h"], "n": row["n"], "n_stations": row["n_stations"],
            "rmse": row["rmse"], "bias": row["bias"],
            "rmse_ci95": row.get("rmse_ci95"), "sparse": row["sparse"]}


def _applicability(var: str, lat: float, lon: float, hs_max_fc: float,
                   lead_max: float) -> dict:
    """이 지점·이 파고대에 검증 성적을 적용해도 되는가 (CLAUDE.md §6-1의 UI 방어).

    verdict: in_range | sparse | out_of_range | no_coverage
    """
    fp = _sample_fingerprint(var)
    summ = _skill_payload(var, True, LAND_FRAC_MAX, None, None, False, fp)
    if summ["status"] != "ok":
        return {"verdict": "no_coverage", "reasons": ["no_validation_samples"],
                "note": "검증 표본이 없다 — 검증 불가", "nearest_station": None,
                "validated_range": None}
    st = _skill_stations_payload(var, True, LAND_FRAC_MAX, None, fp)
    cand = [s for s in st["stations"] if s["has_location"]]
    nearest = None
    if cand:
        d = _haversine_km(lat, lon, np.array([s["lat"] for s in cand]),
                          np.array([s["lon"] for s in cand]))
        k = int(np.argmin(d))
        nearest = {"station_id": cand[k]["station_id"], "distance_km": round(float(d[k]), 1),
                   "n": cand[k]["n"], "rmse": cand[k]["rmse"], "bias": cand[k]["bias"]}

    rng = summ["observed_range"]
    band = next((b for b in summ["by_obs_hs"]
                 if b["obs_hs_min"] <= hs_max_fc < b["obs_hs_max"]), None)
    reasons: list[str] = []
    if nearest is None or nearest["distance_km"] > NEAR_STATION_KM:
        reasons.append("no_nearby_validation_station")
    if hs_max_fc > rng["max"]:
        reasons.append("hs_above_validated_max")
    elif hs_max_fc > rng["p95"]:
        reasons.append("hs_above_validated_p95")
    if band is None or band["n"] < SPARSE_N:
        reasons.append("sparse_hs_band")
    if lead_max > rng["lead_h_max"] + 0.5:
        reasons.append("lead_beyond_validated")

    if "no_nearby_validation_station" in reasons:
        verdict = "no_coverage"
    elif "hs_above_validated_max" in reasons or "lead_beyond_validated" in reasons:
        verdict = "out_of_range"
    elif reasons:
        verdict = "sparse"
    else:
        verdict = "in_range"
    notes = {
        "in_range": "검증 범위 내 — 표시된 성적을 이 지점에 적용할 수 있다",
        "sparse": "표본이 얇은 구간 — 성적의 불확실성이 크다",
        "out_of_range": "검증 표본 범위 밖 — 이 조건의 성적은 검증 불가",
        "no_coverage": f"{NEAR_STATION_KM:g} km 내 검증 관측소 없음 — 성적 적용 불가",
    }
    return {
        "verdict": verdict, "reasons": reasons, "note": notes[verdict],
        "nearest_station": nearest,
        "nearest_station_max_km": NEAR_STATION_KM,
        "hs_band": band and {"obs_hs_min": band["obs_hs_min"], "obs_hs_max": band["obs_hs_max"],
                             "n": band["n"], "rmse": band["rmse"], "bias": band["bias"]},
        "validated_range": {"hs_max_observed": rng["max"], "hs_p95_observed": rng["p95"],
                            "lead_h_max": rng["lead_h_max"],
                            "n": summ["n"], "n_stations": summ["n_stations"]},
    }


@app.get("/v1/seastate/point")
def seastate_point(lat: float = Query(ge=-90, le=90),
                   lon: float = Query(ge=-180, le=360),
                   cycle: str | None = None,
                   lead: float = 0.0,
                   n_freq: int = Query(24, ge=6, le=48),
                   n_dir: int = Query(12, ge=4, le=36),
                   seed: int = 0,
                   heading: float = Query(0.0, ge=0, le=360),
                   ship_length: float = Query(200.0, ge=10, le=450),
                   ship_beam: float = Query(32.0, ge=3, le=70),
                   ship_draft: float = Query(11.0, ge=1, le=25),
                   ship_gm: float = Query(1.2, ge=0.1, le=10),
                   ship_speed: float = Query(14.0, ge=0, le=40)) -> dict:
    """한 지점의 해면을 그릴 수 있는 파 성분 목록.

    스펙트럼 적률(예보)에서 방향 스펙트럼을 재구성하고 이산 파 성분으로
    샘플링해 돌려준다. 화면은 이 성분들을 중첩해 파면을 그린다.

    **개별 파는 예측이 아니다.** 예보가 보장하는 것은 통계량(유의파고·주기·
    방향 분포)이고, 어느 파정이 언제 어디 오는지는 위상 난수가 정한다.
    응답의 `provenance`가 무엇이 검증됐고 무엇이 가정인지 밝힌다.
    """
    cycle = cycle or _latest_wave_cycle()
    ds = _wave_forecast(cycle)
    leads = [float(v) for v in ds.lead.values]
    li = int(np.argmin([abs(v - lead) for v in leads]))

    have = {"m0", "m1", "m2", "a1", "b1"} <= set(ds.data_vars)
    if not have:
        raise HTTPException(
            409, f"cycle {cycle} 은 파주기·파향을 저장하지 않은 옛 예보다 "
                 "(hs 단일 변수). 새 사이클을 쓰거나 재예보해야 한다.")

    sea_grid, _ = collocate._grid_sea(ds.latitude.values, ds.longitude.values, "L1")
    jj, ii, w = collocate.corner_weights(ds.latitude.values, ds.longitude.values, lat, lon)
    s4 = np.ones(4) if sea_grid is None else sea_grid[jj, ii]
    mom = {}
    for v in ("m0", "m1", "m2", "a1", "b1"):
        arr = ds[v].transpose("lead", "latitude", "longitude").values[:, jj, ii]
        mom[v], land_frac, _ = collocate.sea_normalized(arr, w, s4)
    der = spec_grid.derive(**mom)
    if not np.isfinite(der["hs"][li]):
        raise HTTPException(422, "해당 지점은 육지이거나 파랑 에너지가 없다")

    hs = float(der["hs"][li])
    tm01, tm02 = float(der["tm01"][li]), float(der["tm02"][li])
    dirm, dspr = float(der["dirm"][li]), float(der["dspr"][li])
    tp_arr = ds["tp"].transpose("lead", "latitude", "longitude").values if "tp" in ds.data_vars else None
    kb = int(np.argmax(w * s4))
    tp = float(tp_arr[li, jj[kb], ii[kb]]) if tp_arr is not None else tm01 * 1.2
    if not np.isfinite(tp):
        tp = tm01 * 1.2
    dirp_arr = ds["dirp"].transpose("lead", "latitude", "longitude").values if "dirp" in ds.data_vars else None
    dirp = float(dirp_arr[li, jj[kb], ii[kb]]) if dirp_arr is not None else float("nan")

    depth = 1000.0
    try:
        etopo = xr.open_dataset(settings.data_root / "static" / "etopo2022_60s_east_asia.nc")
        depth = float(max(-float(etopo.z.interp(lat=lat, lon=lon)), 5.0))
    except Exception:  # noqa: BLE001 — 수심 없으면 심해로 그린다
        pass

    # f_max 를 넉넉히 잡는다. 장주기 스웰만 담으면 파장 대비 파고가 300:1이라
    # 화면이 밋밋해지고 스케일을 읽을 단서가 사라진다 — 실제 바다에는 그 위에
    # 짧은 파가 얹혀 있고, 스펙트럼도 그 대역을 갖고 있다.
    comp = sea_surface.realize_components(
        hs, tp, dirm, dspr, depth=depth,
        n_freq=n_freq, n_dir=n_dir, seed=seed, f_max=0.9)

    ship = ship_response.Ship(
        length_m=ship_length, beam_m=ship_beam, draft_m=ship_draft,
        gm_m=ship_gm, speed_kn=ship_speed)
    motion = ship_response.motion_estimate(
        ship, hs, tp, tm02, dirm, heading, dspr_deg=dspr)
    hazards = ship_response.imo_hazards(ship, hs, tp, tm02, dirm, heading)

    return {
        "point": {"lat": lat, "lon": lon, "depth_m": round(depth, 1),
                  "land_frac": round(float(land_frac), 4)},
        "ship": {"length_m": ship.length_m, "beam_m": ship.beam_m,
                 "draft_m": ship.draft_m, "gm_m": ship.gm_m,
                 "speed_kn": ship.speed_kn, "heading_deg": heading,
                 "roll_period_s": round(ship.roll_period_s, 2),
                 "bridge_height_m": ship.bridge_height_m},
        "motion": motion,
        "hazards": hazards,
        "cycle": cycle, "lead_h": leads[li],
        "valid_time": pd.to_datetime(ds.valid_time.values[li], utc=True).isoformat(),
        "integrals": {
            "hs_m": round(hs, 3), "tm01_s": round(tm01, 2), "tm02_s": round(tm02, 2),
            "tp_s": round(tp, 2), "dir_mean_deg": round(dirm, 1),
            "dir_peak_deg": None if not np.isfinite(dirp) else round(dirp, 1),
            "dir_spread_deg": round(dspr, 1),
        },
        "components": {
            "n": int(comp["n_kept"]),
            "amp_m": [round(float(v), 5) for v in comp["amp"]],
            "k_rad_per_m": [round(float(v), 6) for v in comp["k"]],
            "omega_rad_per_s": [round(float(v), 5) for v in comp["omega"]],
            "theta_rad": [round(float(v), 5) for v in comp["theta"]],
            "phase_rad": [round(float(v), 5) for v in comp["phase"]],
        },
        "check": {
            "hs_realized_m": round(4.0 * float(np.sqrt(comp["m0_realized"])), 3),
            "energy_kept": round(float(comp["energy_kept"]), 4),
        },
        "convention": {
            "direction": "coming_from, deg true, clockwise",
            "theta_rad": "going_to, math angle (east=0, CCW) — 파수벡터는 (k cosθ, k sinθ)",
        },
        "provenance": {
            "ship": ("IMO MSC.1/Circ.1228 조건의 근사 표시다(Tp 사용, 주기 근접 폭 ±20%). 운동 추정은 "
                     "1자유도 공진 근사이며 **절대 각도·변위는 신뢰할 수 없다** — "
                     "실제 응답은 선체 형상·적재·안정기에 좌우된다"),
            "hs": "검증됨 — 실측 RMSE는 /v1/skill?var=hs 참조",
            "period": "부분 검증 — 최신 표본은 /v1/skill?var=tp 참조. Tm02 채점 표본 없음",
            "direction": "첨두 파향 부분 검증 — /v1/skill?var=dir 참조. 평균 파향 성적은 아님",
            "spread": ("가정 — 리드 0에서는 초기장 cos² 확산 유래(약 31.5°)이지 "
                       "물리가 아니다. 채점된 적이 없다"),
            "phases": "예측 아님 — 위상은 시드 고정 난수다. 개별 파의 위치·시각은 정보가 없다",
            "surface_model": "선형 중첩 (Longuet-Higgins 1963). 파정이 실제보다 완만하다",
        },
    }


@app.get("/v1/forecast/point")
def forecast_point(lat: float = Query(ge=-90, le=90),
                   lon: float = Query(ge=-180, le=360),
                   cycle: str | None = None,
                   vars: str = "hs",
                   level: str = "auto") -> dict:
    requested_vars = list(dict.fromkeys(v.strip() for v in vars.split(",")))
    allowed = {"hs", "tp", "tm01", "tm02", "dirm", "dirp"}
    if not requested_vars or not set(requested_vars) <= allowed:
        raise HTTPException(422, "vars must contain hs,tp,tm01,tm02,dirm,dirp")
    if level not in {"auto", "L1", "L2"}:
        raise HTTPException(422, "level must be auto, L1 or L2")
    cycle = cycle or _latest_wave_cycle()
    level_used = "L2" if level == "L2" else "L1"
    ds = _wave_forecast(cycle, level_used)
    if level == "auto" and _has_level(cycle, "L2"):
        d2 = _wave_forecast(cycle, "L2")
        if (float(d2.latitude.min()) <= lat <= float(d2.latitude.max())
                and float(d2.longitude.min()) <= lon <= float(d2.longitude.max())):
            ds, level_used = d2, "L2"
    if not (float(ds.latitude.min()) <= lat <= float(ds.latitude.max())
            and float(ds.longitude.min()) <= lon <= float(ds.longitude.max())):
        raise HTTPException(400, "point outside forecast domain")
    # 검증 파이프라인(collocate.sea_normalized)과 **동일한 보간**을 쓴다.
    # 순수 겹선형은 육지 셀 hs=0을 섞어 연안에서 계통적으로 낮은 값을 낸다
    # (실측 land_frac=0.38 지점에서 −38%). 이 프로젝트에서 네 번째 반복된 결함이라
    # 서빙 경로도 검증과 같은 함수를 쓰도록 통일한다.
    from poseidon.validation.collocate import (corner_weights, sea_normalized,
                                               _grid_sea)
    g_lats, g_lons = ds.latitude.values, ds.longitude.values
    sea_mask, _ = _grid_sea(g_lats, g_lons, level_used)
    jj, ii, cw = corner_weights(g_lats, g_lons, lat, lon)
    s4 = np.ones(4) if sea_mask is None else sea_mask[jj, ii]
    hs, land_frac, hs_raw = sea_normalized(ds.hs.values[:, jj, ii], cw, s4)
    hs = np.atleast_1d(hs)
    if not np.isfinite(hs).any() or np.nanmax(hs) <= 0.011:
        raise HTTPException(400, "land or dry point")

    from poseidon.api.point_values import sample_variables, finite_value
    values = sample_variables(ds, jj, ii, cw, s4, requested_vars)
    leads = ds.lead.values.astype(float)
    corrected = False
    version = None
    q05 = q95 = None
    hs_out = hs.copy()
    if CORR_PATH.exists():
        m = ResidualCorrector.load(CORR_PATH)
        feats = _serving_features(cycle, lat, lon, ds.valid_time.values)
        finite = np.isfinite(feats["u10"]).all() and np.isfinite(feats["depth"]).all()
        if finite:                                  # 스텁 피처로는 보정하지 않는다
            g = np.where(np.isfinite(feats["hs_gfsw"]), feats["hs_gfsw"], hs)
            out = m.apply(hs, leads, feats["u10"], feats["depth"], g)
            hs_out, q05, q95 = out["corrected"], out["q05"], out["q95"]
            corrected, version = True, m.version
        else:
            version = f"{m.version} (미적용: 피처 결측)"

    skill_var = "hs_l2" if level_used == "L2" else "hs"
    lead_tbl = _lead_skill_map(skill_var)
    items = []
    for k, (lh, tv) in enumerate(zip(leads, ds.valid_time.values)):
        it = {"valid_time": pd.to_datetime(tv, utc=True).isoformat(),
              "lead_h": lh, "var": "hs",
              "q50": finite_value(hs_out[k]),
              "physics_raw": finite_value(hs[k])}
        it["values"] = {v: finite_value(hs_out[k] if v == "hs" else values[v][k])
                        for v in requested_vars}
        it["missing"] = {v: ("not_stored" if v not in ds and v in {"tp", "dirp"}
                              or v in {"tm01", "tm02", "dirm"} and "m0" not in ds
                              else "no_wave_energy")
                         for v, value in it["values"].items() if value is None}
        it["applicability"] = (
            _applicability(skill_var, lat, lon, float(hs_out[k]), float(lh))
            if np.isfinite(hs_out[k]) and hs_out[k] > 0.011 else
            {"verdict": "no_coverage", "reasons": ["no_wave_energy"],
             "note": "이 리드에 유효한 파랑 값이 없다"})
        if q05 is not None:
            it["q05"] = finite_value(q05[k])
            it["q95"] = finite_value(q95[k])
        # skill = 과거 실측 오차, q05/q95 = 모델 예측구간. 의미가 다르므로 합치지 않는다.
        it["skill"] = _skill_for_lead(lead_tbl, float(lh))
        items.append(it)

    now = pd.Timestamp.now(tz="UTC")
    age = float((now - _cycle_time(cycle)).total_seconds() / 3600.0)
    return {"lat": lat, "lon": lon, "cycle": cycle, "level": level_used,
            "land_frac": round(float(land_frac), 4),
            "interp": "sea-normalized",
            "corrected": corrected, "model_version": version, "items": items,
            "requested_vars": requested_vars, "requested_level": level,
            "units": {v: ("m" if v == "hs" else "deg_true_from" if v in {"dirm", "dirp"}
                            else "s") for v in requested_vars},
            "variable_validation": {"hs": "see applicability (hs only)",
                                    "tp": "partial", "dirp": "partial",
                                    "tm01": "unvalidated", "tm02": "unvalidated",
                                    "dirm": "unvalidated"},
            # ── 이하 v0.4 추가 ──
            "cycle_age_h": round(age, 2), "freshness": _freshness(age),
            "produced_at": ds.attrs.get("produced_at"),
            "engine": {k: (v if isinstance(v, (int, float)) else str(v))
                       for k, v in ds.attrs.items()},
            "skill_var": skill_var,
            "applicability": _applicability(skill_var, lat, lon,
                                            float(np.nanmax(hs_out)), float(leads.max())),
            "corrector": _corrector_block()}


# ── 관측 ───────────────────────────────────────────────────────────
def _forecast_bbox() -> tuple[float, float, float, float]:
    """L1 예보 격자 경계 (west, south, east, north). 예보가 없으면 설정 도메인."""
    try:
        ds = _wave_forecast(_latest_wave_cycle())
        return (float(ds.longitude.min()), float(ds.latitude.min()),
                float(ds.longitude.max()), float(ds.latitude.max()))
    except HTTPException:
        b = settings.domain_bbox
        return (b.west, b.south, b.east, b.north)


@app.get("/v1/obs/latest")
def obs_latest(var: str = "hs", hours: float = Query(default=48.0, gt=0, le=744), in_domain: bool = True,
               bbox: str | None = None) -> list[dict]:
    """관측소별 최신 관측.

    hours 기본값이 48인 이유: 12 h 창에서는 예보 도메인 내 관측소가 0개가 된다
    (2026-08-24 실측 — KMA hs 최신 ts가 38 h 전). 도메인 밖 부이(캘리포니아·하와이)는
    in_domain=true(기본)로 제외한다. `age_h`로 관측의 신선도를 클라이언트가 판단한다.
    """
    rows: list[dict] = []
    from poseidon.api.observations import latest_observations
    now = pd.Timestamp.now(tz="UTC")
    locs = _station_locs()
    if locs.empty:
        return rows
    if bbox is not None:
        try:
            w, s, e, n = (float(x) for x in bbox.split(","))
        except ValueError:
            raise HTTPException(400, "bbox must be 'west,south,east,north'") from None
    else:
        w, s, e, n = _forecast_bbox()
    for provider in ("ndbc", "kma"):
        base = settings.parquet_root / "obs" / provider
        last = latest_observations(base, var, now, hours)
        if last.empty:
            continue
        for _, r in last.iterrows():
            sid = r["station_id"]
            if sid not in locs.index:
                continue
            la, lo = float(locs.loc[sid, "lat"]), float(locs.loc[sid, "lon"])
            if (bbox is not None or in_domain) and not (s <= la <= n and w <= lo <= e):
                continue
            ts = pd.Timestamp(r["ts"])
            if ts.tzinfo is None:
                ts = ts.tz_localize("UTC")
            rows.append({"station_id": sid, "provider": provider,
                         "lat": la, "lon": lo,
                         "ts": ts.isoformat(), "var": var,
                         "value": round(float(r["value"]), 2),
                         "qc_flag": int(r["qc_flag"]),
                         "age_h": round(float((now - ts).total_seconds() / 3600.0), 2)})
    return rows


# ── 조석·해일 (부산) ───────────────────────────────────────────────
@app.get("/v1/tide/busan")
def tide_busan(hours: float = 48.0, step_min: int = 10) -> dict:
    if not TIDE_BUSAN.exists():
        raise HTTPException(404, "busan tide constants not fitted")
    d = json.loads(TIDE_BUSAN.read_text())
    ht = HarmonicTide.from_dict(d)
    t0 = pd.Timestamp.now(tz="UTC").floor("10min")
    times = pd.date_range(t0, periods=int(hours * 60 / step_min), freq=f"{step_min}min")
    vals = ht.predict(times)
    return {"station": d.get("station", "IOC:busa"), "datum": "관측 평균 (Z0 포함)",
            "times": [t.isoformat() for t in times],
            "values": [round(float(v), 3) for v in vals]}


@app.get("/v1/surge/busan")
def surge_busan(cycle: str | None = None) -> dict:
    """부산 해일. 요청한 사이클이 없으면 **다른 사이클로 조용히 대체하지 않고 404**.

    폴백은 cycle 미지정일 때만 하고, 그 경우에도 파고 사이클과의 시차를 응답에 담는다
    (파고=최신, 해일=19일 전을 아무 표시 없이 나란히 보여주던 문제).
    """
    cat = _catalog()
    if cycle is not None:
        rows = [r for r in cat.find_datasets("forecast", settings.domain_name, cycle)
                if r["source_id"] == "swe-L2-surge"]
        if not rows:
            raise HTTPException(404, f"no surge forecast for cycle {cycle}")
        uri, requested = rows[-1]["uri"], True
    else:
        with cat._conn() as c:
            r = c.execute("SELECT uri, cycle FROM dataset WHERE source_id='swe-L2-surge'"
                          " ORDER BY cycle DESC, created_at DESC LIMIT 1").fetchone()
        if r is None:
            raise HTTPException(404, "no surge forecast")
        uri, cycle, requested = r["uri"], str(r["cycle"]), False
    ds = xr.open_zarr(uri, consolidated=False)
    t0 = _cycle_time(cycle)
    now = pd.Timestamp.now(tz="UTC")
    age = float((now - t0).total_seconds() / 3600.0)
    try:
        wave_cycle: str | None = _latest_wave_cycle()
        offset = float((_cycle_time(wave_cycle) - t0).total_seconds() / 3600.0)
    except HTTPException:
        wave_cycle, offset = None, 0.0
    return {"cycle": cycle,
            "times": [(t0 + pd.Timedelta(seconds=float(s))).isoformat()
                      for s in ds.t.values],
            "eta": [round(float(v), 3) for v in ds.eta_busan.values],
            # ── 이하 v0.4 추가 ──
            "requested_cycle": requested,
            "cycle_age_h": round(age, 2), "freshness": _freshness(age),
            "wave_cycle": wave_cycle,
            "offset_h_from_wave_cycle": round(offset, 2),
            "same_cycle_as_wave": wave_cycle == cycle,
            "warning": None if abs(offset) <= 6.0 else
            f"현재 파고장과 다른 사이클이다 (차이 {offset / 24:.1f}일) — 같은 상황이 아니다"}


# ── 대시보드 정적 파일 ─────────────────────────────────────────────
@app.get("/")
@app.get("/console")
def index() -> FileResponse:
    return FileResponse(WEB_ROOT / "console" / "index.html")


@app.get("/legacy")
def legacy() -> FileResponse:
    return FileResponse(WEB_ROOT / "index.html")


@app.get("/bridge")
def bridge() -> FileResponse:
    """선교 시점 — 예보 스펙트럼으로 실현한 해면을 1인칭으로 본다."""
    return FileResponse(WEB_ROOT / "bridge.html")
