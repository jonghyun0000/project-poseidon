"""국립해양조사원(KHOA) 바다누리 오픈API 어댑터 — 조위관측소·해양관측부이. 카탈로그 D4.

엔드포인트 (바다누리 개발자 가이드, 2026-08 경로 확인):
  관측소 목록  https://www.khoa.go.kr/api/oceangrid/ObsServiceObj/search.do
  조위 실시간  https://www.khoa.go.kr/api/oceangrid/tideObsRecent/search.do?ObsCode=DT_xxxx
  부이 실시간  https://www.khoa.go.kr/api/oceangrid/buObsRecent/search.do?ObsCode=TW_xxxx
  공통: ServiceKey=<키>&ResultType=json   (일 20,000회 한도 → 증분 수집)
키: 환경변수 POSEIDON_KHOA_KEY. 첫 사용 시 `python -m poseidon.ingest.adapters.khoa --probe`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any, ClassVar

import httpx
import pandas as pd

from poseidon.core.types import BBox
from poseidon.ingest.adapters.base import AdapterError, ObsAdapter, fetch_bytes
from poseidon.core.log_redact import install as _install_log_redaction

BASE = "https://www.khoa.go.kr/api/oceangrid"
ENV_KEY = "POSEIDON_KHOA_KEY"
# httpx 는 요청 URL 전체를 INFO 로 남기고, 이 API 는 키를 URL 파라미터로 받는다.
# 가리지 않으면 운영 로그에 키가 쌓인다(2026-09-11 에 150건 발견). -> poseidon/core/log_redact.py
_install_log_redaction()
# 바다누리 응답 필드 → 표준 변수
FIELD_MAP = {"tide_level": ("wl", 0.01),          # cm → m
             "wave_height": ("hs", 1.0), "wave_period": ("tp", 1.0),
             "water_temp": ("sst", 1.0), "wind_speed": ("wspd", 1.0),
             "wind_dir": ("wdir", 1.0), "air_press": ("slp", 1.0),
             "Salinity": ("sal", 1.0), "current_speed": ("cspd", 0.01),
             "current_dir": ("cdir", 1.0)}


class KHOAAdapter(ObsAdapter):
    source_id: ClassVar[str] = "khoa-badanuri"
    tier: ClassVar[int] = 0

    def __init__(self, max_stations: int = 40) -> None:
        self.key = os.environ.get(ENV_KEY, "")
        self.max_stations = max_stations
        self._stations: list[dict[str, Any]] | None = None

    @staticmethod
    def configured() -> bool:
        return bool(os.environ.get(ENV_KEY))

    async def _station_list(self, client: httpx.AsyncClient) -> list[dict[str, Any]]:
        if self._stations is None:
            raw = await fetch_bytes(client, f"{BASE}/ObsServiceObj/search.do",
                                    {"ServiceKey": self.key, "ResultType": "json"})
            try:
                js = json.loads(raw)
            except json.JSONDecodeError as e:
                raise AdapterError("KHOA station list not JSON (key rejected?)") from e
            data = js.get("result", {}).get("data", [])
            self._stations = data if isinstance(data, list) else [data]
        return self._stations

    async def fetch(self, bbox: BBox) -> pd.DataFrame:
        if not self.key:
            raise AdapterError(f"{ENV_KEY} not set")
        rows: list[dict[str, Any]] = []
        async with httpx.AsyncClient() as client:
            stations = await self._station_list(client)
            picked = []
            for s in stations:
                try:
                    la, lo = float(s.get("obs_lat")), float(s.get("obs_lon"))
                except (TypeError, ValueError):
                    continue
                code = str(s.get("obs_post_id", ""))
                if bbox.contains(la, lo) and code[:3] in ("DT_", "TW_"):
                    picked.append(code)
            for code in picked[: self.max_stations]:
                ep = "tideObsRecent" if code.startswith("DT_") else "buObsRecent"
                try:
                    raw = await fetch_bytes(client, f"{BASE}/{ep}/search.do",
                                            {"ServiceKey": self.key, "ObsCode": code,
                                             "ResultType": "json"})
                    rec = json.loads(raw).get("result", {}).get("data", {})
                except (AdapterError, json.JSONDecodeError, AttributeError):
                    continue
                if not isinstance(rec, dict) or "record_time" not in rec:
                    continue
                ts = pd.to_datetime(rec["record_time"]).tz_localize("Asia/Seoul").tz_convert("UTC")
                for field, (var, scale) in FIELD_MAP.items():
                    v = rec.get(field)
                    if v in (None, "", "-"):
                        continue
                    try:
                        rows.append({"station_id": f"KHOA:{code}", "ts": ts, "var": var,
                                     "value": float(v) * scale})
                    except ValueError:
                        continue
        if not rows:
            raise AdapterError("KHOA: no observations parsed (run --probe)")
        return pd.DataFrame(rows)


def _probe() -> int:
    key = os.environ.get(ENV_KEY)
    if not key:
        print(f"{ENV_KEY} 환경변수에 바다누리 서비스 키를 넣고 다시 실행하세요.")
        return 1
    r = httpx.get(f"{BASE}/ObsServiceObj/search.do",
                  params={"ServiceKey": key, "ResultType": "json"}, timeout=60,
                  follow_redirects=True)
    print(r.text[:1500])
    r2 = httpx.get(f"{BASE}/tideObsRecent/search.do",
                   params={"ServiceKey": key, "ObsCode": "DT_0001", "ResultType": "json"},
                   timeout=60, follow_redirects=True)
    print("\n[tideObsRecent DT_0001]\n", r2.text[:800])
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--probe", action="store_true")
    sys.exit(_probe() if p.parse_args().probe else 0)
