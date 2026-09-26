"""동화용 Hs 관측 준비 — QC · 시각창 · 중복제거 · 좌표 결합.

collocate.collocate_wave 와 **같은 규약**을 쓴다(+-30분 창 평균, qc_flag==0,
동일 좌표표). 규약이 갈리면 params.SIGMA_O_RANDOM 의 유도 전제가 깨진다.

관측은 데이터이지 지시가 아니다 — 값 이외의 어떤 필드도 제어 흐름에 쓰지 않는다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from poseidon.assimilation.params import OBS_WINDOW_MIN
from poseidon.core.config import settings
from poseidon.validation.collocate import _station_locations

log = logging.getLogger("poseidon.assimilation.obs")

PROVIDERS = ("ndbc", "kma")


@dataclass(frozen=True)
class Observation:
    station_id: str
    lat: float
    lon: float
    value: float          # 관측 Hs [m] (창 평균)
    n_reports: int        # 창 내 보고 수 (sigma_o 의 1/sqrt(k) 항 진단용)
    provider: str


def cycle_to_timestamp(cycle: str) -> pd.Timestamp:
    """'20260821T00' -> UTC Timestamp."""
    return pd.Timestamp(
        f"{cycle[0:4]}-{cycle[4:6]}-{cycle[6:8]}T{cycle[9:11]}:00:00", tz="UTC"
    )


def _month_files(t0: pd.Timestamp, window_min: float) -> list[tuple[str, object]]:
    """창이 월 경계를 넘어도 놓치지 않도록 걸치는 모든 월 파일을 모은다."""
    lo = t0 - pd.Timedelta(minutes=window_min)
    hi = t0 + pd.Timedelta(minutes=window_min)
    months = sorted({(t.year, t.month) for t in (lo, hi)})
    out = []
    for provider in PROVIDERS:
        for y, m in months:
            p = settings.parquet_root / "obs" / provider / f"{y:04d}" / f"{m:02d}.parquet"
            if p.exists():
                out.append((provider, p))
    return out


def load_hs_observations(
    cycle: str,
    *,
    window_min: float = OBS_WINDOW_MIN,
    exclude_stations: tuple[str, ...] = (),
    reduce: str = "mean",
) -> list[Observation]:
    """사이클 t=0 시각의 Hs 관측 목록.

    - qc_flag == 0 만 사용 (1=의심, 2=불량은 폐기)
    - |ts - t0| <= window_min
    - 관측소당 1건으로 축약 (reduce='mean' 창 평균 = collocate 규약 | 'nearest' 최근접)
    - 좌표가 없는 관측소는 폐기 (격자에 사상할 수 없다)
    - exclude_stations 는 교차검증 홀드아웃 (해당 관측소를 동화에서 완전히 뺀다)
    """
    if reduce not in ("mean", "nearest"):
        raise ValueError("reduce must be 'mean' or 'nearest'")
    t0 = cycle_to_timestamp(cycle)
    frames = []
    for provider, path in _month_files(t0, window_min):
        df = pd.read_parquet(path)
        df = df[(df["var"] == "hs") & (df["qc_flag"] == 0)]
        if df.empty:
            continue
        df = df.assign(provider=provider)
        frames.append(df)
    if not frames:
        log.warning("cycle %s: Hs 관측 파일 없음", cycle)
        return []
    obs = pd.concat(frames, ignore_index=True)
    obs["ts"] = pd.to_datetime(obs["ts"], utc=True)
    lo, hi = t0 - pd.Timedelta(minutes=window_min), t0 + pd.Timedelta(minutes=window_min)
    obs = obs[(obs["ts"] >= lo) & (obs["ts"] <= hi)]
    obs = obs[np.isfinite(obs["value"].astype(float))]
    # 같은 (관측소, 시각) 중복 보고 제거 — 여러 제공자·재수집으로 생길 수 있다
    obs = obs.drop_duplicates(subset=["station_id", "ts"], keep="last")
    if obs.empty:
        log.warning("cycle %s: 시각창 %+.0f분 내 Hs 관측 0건", cycle, window_min)
        return []

    excl = set(exclude_stations)
    locs = _station_locations().drop_duplicates("station_id").set_index("station_id")

    out: list[Observation] = []
    for sid, g in obs.groupby("station_id"):
        if sid in excl or sid not in locs.index:
            continue
        if reduce == "mean":
            value = float(g["value"].astype(float).mean())
        else:
            value = float(g.loc[(g["ts"] - t0).abs().idxmin(), "value"])
        out.append(Observation(
            station_id=str(sid),
            lat=float(locs.loc[sid, "lat"]), lon=float(locs.loc[sid, "lon"]),
            value=value, n_reports=int(len(g)),
            provider=str(g["provider"].iloc[0]),
        ))
    out.sort(key=lambda o: o.station_id)
    log.info("cycle %s: Hs 관측 %d개소 (제외 %d)", cycle, len(out), len(excl))
    return out
