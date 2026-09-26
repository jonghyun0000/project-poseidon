"""IOC Sea Level Station Monitoring Facility (VLIZ) 조위 어댑터 (무인증). 카탈로그 D6.

동아시아 bbox 내 관측소를 stationlist에서 동적으로 찾아 실시간 수위를 수집한다.
한국(부산 등)·일본 검조소가 포함되어 KMA/KHOA 계정 없이도 연안 진실값을 확보한다.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, ClassVar

import httpx
import pandas as pd

from poseidon.core.types import BBox
from poseidon.ingest.adapters.base import AdapterError, ObsAdapter, fetch_bytes

SERVICE = "https://www.ioc-sealevelmonitoring.org/service.php"


class IOCSeaLevelAdapter(ObsAdapter):
    source_id: ClassVar[str] = "ioc-sealevel"
    tier: ClassVar[int] = 0

    def __init__(self, max_stations: int = 12, period_days: float = 0.5) -> None:
        self.max_stations = max_stations
        self.period_days = period_days
        self._stations: list[dict[str, Any]] | None = None

    async def _station_codes(self, client: httpx.AsyncClient, bbox: BBox) -> list[str]:
        if self._stations is None:
            raw = await fetch_bytes(client, SERVICE,
                                    {"query": "stationlist", "showall": "a", "output": "json"})
            self._stations = json.loads(raw)
        seen: dict[str, None] = {}  # stationlist에 코드 중복 존재 → 순서 보존 dedupe
        for s in self._stations:
            if (s.get("Lat") is not None and s.get("Lon") is not None
                    and bbox.contains(float(s["Lat"]), float(s["Lon"]))):
                seen.setdefault(s["Code"], None)
        return list(seen)[: self.max_stations]

    async def fetch(self, bbox: BBox) -> pd.DataFrame:
        async def one(client: httpx.AsyncClient, code: str) -> pd.DataFrame | None:
            try:
                raw = await fetch_bytes(client, SERVICE, {
                    "query": "data", "code": code,
                    "period": self.period_days, "output": "json",
                })
                rows = json.loads(raw)
            except (AdapterError, json.JSONDecodeError):
                return None
            if not isinstance(rows, list) or not rows:
                return None
            df = pd.DataFrame(rows)
            if "slevel" not in df.columns or "stime" not in df.columns:
                return None
            return pd.DataFrame({
                "station_id": f"IOC:{code}",
                "ts": pd.to_datetime(df["stime"], utc=True),
                "var": "wl",
                "value": pd.to_numeric(df["slevel"], errors="coerce"),
            }).dropna(subset=["value"])

        async with httpx.AsyncClient() as client:
            codes = await self._station_codes(client, bbox)
            if not codes:
                raise AdapterError("no IOC stations in bbox")
            frames = await asyncio.gather(*(one(client, c) for c in codes))
        good = [f for f in frames if f is not None and not f.empty]
        if not good:
            raise AdapterError("all IOC stations failed")
        return pd.concat(good, ignore_index=True).sort_values(["station_id", "ts"]).reset_index(drop=True)
