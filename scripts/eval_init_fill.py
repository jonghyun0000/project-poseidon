"""PHASE31 사전 등록 평가 — 초기장·경계 격자 매핑(init_fill) 후보 대 기존 방식.

이 파일은 **후보 실행 결과를 보기 전에** 커밋했다(판정 방식의 사후 조정을 막기 위해).
게이트 정의는 docs/PHASE28 §4, 해석·절차는 docs/PHASE31 §2.

사용:
    PYTHONPATH=. .venv/bin/python scripts/eval_init_fill.py gates \\
        --cycles 20260907T00 20260908T12 20260910T00 20260911T12 20260918T12 \\
        --candidate seanorm=p31sn --candidate seanorm_nn1=p31nn
    PYTHONPATH=. .venv/bin/python scripts/eval_init_fill.py lead0-all      # 비게이트 민감도

채점은 collocate_wave(..., write=False) 로 한다 — error_sample 에 쓰지 않는다.
대조군은 운영 산출물(source_id=spectral_wave-L1)이다. 기존 방식 진단 실행이 운영과 8변수
비트 동일함을 PHASE31 §1 에서 확인했으므로 같은 조건의 대조군이다.
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

S3 = ("KMA:22520", "KMA:22513", "KMA:22106")    # PHASE28 §2 — 영값 가중 0.52 이상
SEED = 20260927
REPS = 2000
LAND_MAX = 0.05
REPORTS = Path(__file__).resolve().parents[1] / "reports"


def boot_mean(df: pd.DataFrame, col: str, rng: np.random.Generator) -> tuple[float, float, float]:
    """사이클 군집 복원 추출. (평균, 2.5%, 97.5%). 인접 사이클은 독립이 아니다 — 구간은 낙관적이다."""
    if df.empty:
        return float("nan"), float("nan"), float("nan")
    by = {k: g[col].to_numpy() for k, g in df.groupby("cycle")}
    keys = list(by)
    bs = [np.concatenate([by[k] for k in rng.choice(keys, len(keys))]).mean() for _ in range(REPS)]
    return float(df[col].mean()), float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5))


def boot_rmse_diff(df: pd.DataFrame, rng: np.random.Generator) -> tuple[float, float, float]:
    """RMSE(후보) − RMSE(기존), 사이클 군집 복원 추출."""
    if df.empty:
        return float("nan"), float("nan"), float("nan")
    by = {k: (g["e_c"].to_numpy(), g["e_b"].to_numpy()) for k, g in df.groupby("cycle")}
    keys = list(by)

    def d(ks):
        ec = np.concatenate([by[k][0] for k in ks]); eb = np.concatenate([by[k][1] for k in ks])
        return np.sqrt((ec ** 2).mean()) - np.sqrt((eb ** 2).mean())
    bs = [d(rng.choice(keys, len(keys))) for _ in range(REPS)]
    return float(d(keys)), float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5))


def _hs(df: pd.DataFrame) -> pd.DataFrame:
    df = df[(df["var"] == "hs") & (df["land_frac"] <= LAND_MAX)]
    return df[["cycle", "station_id", "valid_time", "lead_h", "predicted", "observed"]]


def gates(cycles: list[str], candidates: dict[str, str]) -> dict:
    from poseidon.validation.collocate import collocate_wave
    out: dict = {"cycles": cycles, "seed": SEED, "reps": REPS, "s3": S3, "candidates": {}}
    base = pd.concat([_hs(collocate_wave(c, source_id="spectral_wave-L1", write=False))
                      for c in cycles], ignore_index=True)
    key = ["cycle", "station_id", "valid_time", "lead_h"]
    for mode, tag in candidates.items():
        rng = np.random.default_rng(SEED)
        cand = pd.concat([_hs(collocate_wave(c, source_id=f"spectral_wave-L1-{tag}", write=False))
                          for c in cycles], ignore_index=True)
        m = base.merge(cand, on=key, suffixes=("_b", "_c"))
        assert np.allclose(m.observed_b, m.observed_c), "같은 관측에 짝지어져야 한다"
        m["e_b"] = m.predicted_b - m.observed_b
        m["e_c"] = m.predicted_c - m.observed_c
        m["d"] = m.predicted_c - m.predicted_b
        l0 = m[m.lead_h < 0.1]
        g1s = l0[l0.station_id.isin(S3)]
        b_b = boot_mean(g1s, "e_b", rng); b_c = boot_mean(g1s, "e_c", rng)
        g1 = {"n": len(g1s), "cycles": int(g1s.cycle.nunique()),
              "bias_base": b_b, "bias_cand": b_c,
              "reduction": (1 - abs(b_c[0]) / abs(b_b[0])) if b_b[0] else float("nan")}
        g1["pass"] = bool(abs(b_c[0]) <= 0.5 * abs(b_b[0]) and (b_c[1] > b_b[2] or b_c[2] < b_b[1]))
        g2s = l0[~l0.station_id.isin(S3)]
        dd = boot_mean(g2s, "d", rng)
        g2 = {"n": len(g2s), "mean_diff": dd, "pass": bool(dd[1] <= 0 <= dd[2])}
        g3s = m[m.lead_h >= 11.9]
        bd = boot_mean(g3s, "d", rng); rd = boot_rmse_diff(g3s, rng)
        g3 = {"n": len(g3s), "bias_diff": bd, "rmse_diff": rd,
              "rmse_base": float(np.sqrt((g3s.e_b ** 2).mean())),
              "rmse_cand": float(np.sqrt((g3s.e_c ** 2).mean())),
              "pass": bool(bd[1] <= 0 <= bd[2] and rd[1] <= 0 <= rd[2])}
        per_cycle = (l0[l0.station_id.isin(S3)].groupby("cycle")[["e_b", "e_c"]].mean()
                     .round(4).reset_index().to_dict("records"))
        out["candidates"][mode] = {"tag": tag, "paired_n": len(m),
                                   "changed_samples": int((m.d != 0).sum()),
                                   "G1": g1, "G2": g2, "G3": g3, "g1_per_cycle": per_cycle}
    out["base_n"] = len(base)
    return out


def lead0_all() -> dict:
    """비게이트 민감도 — 발행 사이클 전체의 리드 0 을 모델 없이 재구성해 채점한다.

    리드 0 hs 는 초기 스펙트럼의 Hs 이고, 스펙트럼은 m0 를 목표 Hs 에 정확히 맞춘다
    (기존 방식에서 zarr 대비 최대 2.6e-7 m — PHASE31 §1). 그래서 NaN→0 처리한 격자 값에
    콜로케이션과 같은 해상 정규화 보간을 적용하면 리드 0 표본이 된다.
    """
    import xarray as xr

    from poseidon.core.config import settings
    from poseidon.scheduler.wave_cycle import DOMAIN
    from poseidon.scheduler.wave_inputs import grid_wave_fields
    from poseidon.validation.collocate import (_grid_sea, _station_locations, corner_weights,
                                               sea_normalized)
    d = DOMAIN
    lats = np.arange(d["south"], d["north"] + 1e-9, d["res"])
    lons = np.arange(d["west"], d["east"] + 1e-9, d["res"])
    sea, _ = _grid_sea(lats, lons, "L1")
    locs = _station_locations().set_index("station_id")
    con = sqlite3.connect(settings.catalog_path)
    cycles = [r[0] for r in con.execute(
        "select distinct cycle from forecast_run where engine='wave-L1' and status='PUBLISHED' order by cycle")]
    obs = []
    for prov in ("kma", "ndbc"):
        for f in (settings.parquet_root / "obs" / prov).rglob("*.parquet"):
            if f.name.startswith("._"):   # exFAT AppleDouble 부속 파일 (사전 등록 후 수정, PHASE31 §3)
                continue
            obs.append(pd.read_parquet(f, columns=["station_id", "ts", "var", "value", "qc_flag"]))
    obs = pd.concat(obs, ignore_index=True)
    obs = obs[(obs["var"] == "hs") & (obs.qc_flag == 0)]
    obs["ts"] = pd.to_datetime(obs.ts, utc=True)
    rows = []
    for cy in cycles:
        r = con.execute("select uri from dataset where collection='boundary' and cycle=? "
                        "order by created_at", (cy,)).fetchall()
        if not r:
            continue
        b = xr.open_zarr(r[-1][0], consolidated=False).isel(time=0)
        t0 = pd.Timestamp(datetime.strptime(cy, "%Y%m%dT%H"), tz="UTC")
        win = obs[(obs.ts >= t0 - pd.Timedelta(minutes=30)) & (obs.ts <= t0 + pd.Timedelta(minutes=30))]
        if win.empty:
            continue
        fields = {m: np.where(sea, np.nan_to_num(grid_wave_fields(b, lats, lons, m)[0], nan=0.0), 0.0)
                  for m in ("none", "seanorm", "seanorm_nn1")}
        for sid, g in win.groupby("station_id"):
            if sid not in locs.index:
                continue
            la, lo = float(locs.loc[sid, "lat"]), float(locs.loc[sid, "lon"])
            if not (lats[0] + 0.5 <= la <= lats[-1] - 0.5 and lons[0] + 0.5 <= lo <= lons[-1] - 0.5):
                continue
            jj, ii, w = corner_weights(lats, lons, la, lo)
            s4 = sea[jj, ii]
            vals = {}
            for m, fld in fields.items():
                v, lf, _ = sea_normalized(fld[jj, ii], w, s4)
                vals[m] = float(v)
            if lf > LAND_MAX or not np.isfinite(vals["none"]):
                continue
            rows.append({"cycle": cy, "station_id": sid, "observed": float(g.value.mean()), **vals})
    df = pd.DataFrame(rows)
    rng = np.random.default_rng(SEED)
    out = {"cycles": int(df.cycle.nunique()), "n": len(df), "groups": {}}
    for grp, sub in (("S3", df[df.station_id.isin(S3)]), ("R28", df[~df.station_id.isin(S3)])):
        res = {}
        for m in ("none", "seanorm", "seanorm_nn1"):
            sub = sub.assign(**{f"e_{m}": sub[m] - sub.observed})
            res[m] = boot_mean(sub, f"e_{m}", rng)
        out["groups"][grp] = {"n": len(sub), "cycles": int(sub.cycle.nunique()), "bias": res}
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gates")
    g.add_argument("--cycles", nargs="+", required=True)
    g.add_argument("--candidate", action="append", required=True, help="mode=tag")
    sub.add_parser("lead0-all")
    a = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)
    if a.cmd == "gates":
        res = gates(a.cycles, dict(x.split("=", 1) for x in a.candidate))
    else:
        res = lead0_all()
    res["generated_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    REPORTS.mkdir(exist_ok=True)
    path = REPORTS / f"init-fill-{a.cmd}.json"
    path.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=float))
    print(json.dumps(res, ensure_ascii=False, indent=1, default=float))
    print(f"\n-> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
