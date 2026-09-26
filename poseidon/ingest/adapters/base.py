"""어댑터 공통 기반.

표준화 규약 (PHASE2 §3.5):
- 격자: dims (time, latitude, longitude), 좌표 오름차순, 변수명 CF 계열 소문자
- 관측: long-format DataFrame [station_id, ts(UTC), var, value]
- 모든 산출물 attrs/meta에 lineage(source_id, 취득시각, 요청 파라미터) 기록
"""

from __future__ import annotations

import abc
import asyncio
import random
from datetime import datetime, timezone
from typing import Any, ClassVar

import httpx
import pandas as pd
import xarray as xr

from poseidon.core.config import settings
from poseidon.core.types import BBox, Cycle


class AdapterError(RuntimeError):
    """소스 취득 실패 (재시도 소진 후)."""


async def fetch_bytes(client: httpx.AsyncClient, url: str,
                      params: dict[str, Any] | None = None) -> bytes:
    """재시도·지수 백오프 포함 GET. NOMADS 등 속도 제한 소스 대응 (카탈로그 §K-2)."""
    last: Exception | None = None
    for attempt in range(settings.http_retries):
        try:
            r = await client.get(url, params=params, timeout=settings.http_timeout_s)
            r.raise_for_status()
            return r.content
        except (httpx.HTTPError, httpx.TimeoutException) as e:  # noqa: PERF203
            last = e
            await asyncio.sleep((2 ** attempt) + random.random())
    raise AdapterError(f"GET failed after {settings.http_retries} tries: {url}") from last


def lineage(source_id: str, request: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "request": request,
    }


class GridAdapter(abc.ABC):
    """격자 데이터 소스 (강제장·경계조건)."""

    source_id: ClassVar[str]
    tier: ClassVar[int]

    @abc.abstractmethod
    async def available(self, cycle: Cycle) -> bool: ...

    @abc.abstractmethod
    async def fetch(self, cycle: Cycle, bbox: BBox) -> xr.Dataset: ...


class ObsAdapter(abc.ABC):
    """점 관측 소스 (부이·조위). fetch는 long-format DataFrame 반환."""

    source_id: ClassVar[str]
    tier: ClassVar[int]

    @abc.abstractmethod
    async def fetch(self, bbox: BBox) -> pd.DataFrame: ...
