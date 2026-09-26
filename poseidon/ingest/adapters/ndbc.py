"""NDBC 부이 실시간 관측 어댑터 (realtime2, 무인증). 카탈로그 D1.

.txt(표준기상)만 파싱한다 — hs/tp/파향/바람/기압/수온 포함.
방향 스펙트럼(.data_spec 등)은 Phase 7 스펙트럼 검증 단계에서 추가.
"""

from __future__ import annotations

import asyncio
import io
from typing import ClassVar

import httpx
import pandas as pd

from poseidon.core.types import BBox
from poseidon.ingest.adapters.base import AdapterError, ObsAdapter, fetch_bytes

BASE = "https://www.ndbc.noaa.gov/data/realtime2"

# 열 이름 → 표준 변수명 (관심 변수만; 나머지 열은 버림)
VAR_MAP = {
    "WDIR": "wdir", "WSPD": "wspd", "GST": "gust",
    "WVHT": "hs", "DPD": "tp", "APD": "ta", "MWD": "dir",
    "PRES": "slp", "ATMP": "at", "WTMP": "sst",
}

# 파이프라인 검증용 기본 관측소. 52200(괌)·52211(사이판)은 동아시아 확장 도메인 내
# 유일한 무인증 실측 파랑부이 (Phase 7 검증·AI 학습의 진실값).
DEFAULT_STATIONS = ("46042", "51001", "52200", "52211", "21418", "46059")


def parse_realtime2_txt(text: str, station_id: str) -> pd.DataFrame:
    """realtime2 .txt → long-format [station_id, ts, var, value]. 'MM' = 결측."""
    lines = text.strip().splitlines()
    if len(lines) < 3 or not lines[0].startswith("#"):
        raise AdapterError(f"unexpected NDBC format for {station_id}")
    header = lines[0].lstrip("#").split()
    df = pd.read_csv(io.StringIO("\n".join(lines[2:])), sep=r"\s+", names=header,
                     na_values=["MM"])
    ts = pd.to_datetime(
        df["YY"].astype(int) * 100000000 + df["MM"].astype(int) * 1000000
        + df["DD"].astype(int) * 10000 + df["hh"].astype(int) * 100 + df["mm"].astype(int),
        format="%Y%m%d%H%M", utc=True,
    )
    out = []
    for col, var in VAR_MAP.items():
        if col in df.columns:
            vals = pd.to_numeric(df[col], errors="coerce")
            out.append(pd.DataFrame({
                "station_id": f"NDBC:{station_id}", "ts": ts, "var": var, "value": vals,
            }))
    long = pd.concat(out, ignore_index=True).dropna(subset=["value"])
    return long.sort_values(["var", "ts"]).reset_index(drop=True)


async def fetch_station_locations() -> pd.DataFrame:
    """NDBC 관측소 좌표표 (station_table.txt) — 콜로케이션에 필요."""
    async with httpx.AsyncClient() as client:
        raw = await fetch_bytes(
            client, "https://www.ndbc.noaa.gov/data/stations/station_table.txt")
    rows = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        if line.startswith("#") or "|" not in line:
            continue
        p = line.split("|")
        try:
            loc = p[6].split("(")[0].strip().split()   # "13.354 N 144.788 E ..."
            lat = float(loc[0]) * (1 if loc[1] == "N" else -1)
            lon = float(loc[2]) * (1 if loc[3] == "E" else -1)
        except (IndexError, ValueError):
            continue
        rows.append({"station_id": f"NDBC:{p[0].strip()}", "lat": lat, "lon": lon})
    return pd.DataFrame(rows)


class NDBCAdapter(ObsAdapter):
    source_id: ClassVar[str] = "noaa-ndbc-realtime2"
    tier: ClassVar[int] = 0

    def __init__(self, stations: tuple[str, ...] = DEFAULT_STATIONS) -> None:
        self.stations = stations

    async def fetch(self, bbox: BBox) -> pd.DataFrame:
        async def one(client: httpx.AsyncClient, sid: str) -> pd.DataFrame | None:
            try:
                raw = await fetch_bytes(client, f"{BASE}/{sid}.txt")
                return parse_realtime2_txt(raw.decode("utf-8", errors="replace"), sid)
            except AdapterError:
                return None  # 개별 관측소 실패는 배치를 죽이지 않는다

        async with httpx.AsyncClient() as client:
            frames = await asyncio.gather(*(one(client, s) for s in self.stations))
        good = [f for f in frames if f is not None and not f.empty]
        if not good:
            raise AdapterError("all NDBC stations failed")
        return pd.concat(good, ignore_index=True)
