"""기상청 API허브 해양관측 어댑터 — 실제 응답 스키마 기준 (2026-08-22 검증). 카탈로그 D3.

엔드포인트 (https://apihub.kma.go.kr/api/typ01/url/):
  kma_buoy.php   tm=YYYYMMDDHHMI            해양기상부이 (요청시각 -59분~00분, 10분 자료)
  kma_buoy2.php  tm1=…&tm2=…&stn=0          해양기상부이 기간 조회 (백필용)
  kma_lhaws.php  tm=…  / kma_lhaws2.php tm1,tm2   등표 기상관측 (파고·주기·조위 LS)
  stn_inf.php    inf=BUOY|LHAWS             관측지점 좌표표
응답: EUC-KR 텍스트, '#' 주석, 공백 또는 쉼표 구분(끝에 ',=' 꼬리), 결측 -99/-999.
키: 환경변수 POSEIDON_KMA_AUTHKEY (저장 금지).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import ClassVar

import httpx
import pandas as pd

from poseidon.core.types import BBox
from poseidon.ingest.adapters.base import AdapterError, ObsAdapter, fetch_bytes
from poseidon.core.log_redact import install as _install_log_redaction

HUB = "https://apihub.kma.go.kr/api/typ01/url"
ENV_KEY = "POSEIDON_KMA_AUTHKEY"
# httpx 는 요청 URL 전체를 INFO 로 남기고, 이 API 는 키를 URL 파라미터로 받는다.
# 가리지 않으면 운영 로그에 키가 쌓인다(2026-09-11 에 150건 발견). -> poseidon/core/log_redact.py
_install_log_redaction()
KST = timezone(timedelta(hours=9))

SCHEMA = {
    "buoy": ["TM", "STN", "WD1", "WS1", "WS1_GST", "WD2", "WS2", "WS2_GST", "PA", "HM",
             "TA", "TW", "WH_MAX", "WH_SIG", "WH_AVE", "WP", "WO"],
    "lhaws": ["TM", "STN", "WD", "WS", "WD_INS", "WS_INS", "WS_INS_TM", "TA", "TA_MIN",
              "TA_MIN_TM", "TA_MAX", "TA_MAX_TM", "PS", "TW", "WH_MAX", "WH_SIG", "WP",
              "LS", "VT"],
}
# 기상청 열 → 표준 변수
VAR_MAP = {"WH_SIG": "hs", "WH_MAX": "hmax", "WP": "tp", "WO": "dir",
           "WS1": "wspd", "WD1": "wdir", "WS": "wspd", "WD": "wdir",
           "WS1_GST": "gust", "PA": "slp", "PS": "slp", "TA": "at", "TW": "sst",
           "LS": "wl"}


def _decode(raw: bytes) -> str:
    return raw.decode("euc-kr", errors="replace")


def _check_status(text: str) -> None:
    if '"status" : 401' in text or "유효한 인증키" in text:
        raise AdapterError("KMA authKey rejected (401)")
    if '"status" : 403' in text or "활용신청" in text:
        raise AdapterError("KMA 403: API허브에서 해당 API '활용신청' 승인 필요")


def _rows(text: str) -> list[list[str]]:
    out = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        toks = [t.strip() for t in (s.split(",") if "," in s else s.split())]
        toks = [t for t in toks if t not in ("", "=")]
        out.append(toks)
    return out


def parse_obs(text: str, kind: str) -> pd.DataFrame:
    """kma_buoy/kma_lhaws 계열 → long-format [station_id, ts(UTC), var, value]."""
    _check_status(text)
    cols = SCHEMA[kind]
    rows = [r for r in _rows(text) if len(r) >= len(cols)]
    if not rows:
        raise AdapterError(f"KMA {kind}: no data rows")
    df = pd.DataFrame([r[: len(cols)] for r in rows], columns=cols)
    ts = pd.to_datetime(df["TM"], format="%Y%m%d%H%M", errors="coerce") \
        .dt.tz_localize(KST).dt.tz_convert("UTC")
    sid = "KMA:" + df["STN"].astype(str).str.strip()
    parts = []
    for col, var in VAR_MAP.items():
        if col not in df.columns:
            continue
        if var in ("wspd", "wdir") and any(p["var"].iloc[0] == var for p in parts if len(p)):
            continue                                     # 센서1 우선
        v = pd.to_numeric(df[col], errors="coerce")
        v = v.where(v > -90.0)
        parts.append(pd.DataFrame({"station_id": sid, "ts": ts, "var": var, "value": v}))
    long = pd.concat(parts, ignore_index=True).dropna(subset=["value", "ts"])
    return long.drop_duplicates(subset=["station_id", "ts", "var"]).reset_index(drop=True)


def parse_station_info(text: str) -> pd.DataFrame:
    """stn_inf.php → [station_id, lat, lon, name]  (STN LON LAT … STN_KO …)."""
    _check_status(text)
    recs = []
    for r in _rows(text):
        try:
            recs.append({"station_id": f"KMA:{r[0]}", "lon": float(r[1]),
                         "lat": float(r[2]), "name": r[6] if len(r) > 6 else ""})
        except (ValueError, IndexError):
            continue
    if not recs:
        raise AdapterError("KMA stn_inf: no stations parsed")
    return pd.DataFrame(recs)


class KMAMarineAdapter(ObsAdapter):
    source_id: ClassVar[str] = "kma-apihub"
    tier: ClassVar[int] = 0

    def __init__(self, hours_back: int = 3) -> None:
        self.key = os.environ.get(ENV_KEY, "")
        self.hours_back = hours_back

    @staticmethod
    def configured() -> bool:
        return bool(os.environ.get(ENV_KEY))

    async def _get(self, client: httpx.AsyncClient, path: str, **params) -> str:
        raw = await fetch_bytes(client, f"{HUB}/{path}",
                                {**params, "help": 0, "authKey": self.key})
        return _decode(raw)

    async def fetch(self, bbox: BBox) -> pd.DataFrame:
        """최근 N시간 부이·등표 (운영 증분)."""
        if not self.key:
            raise AdapterError(f"{ENV_KEY} not set")
        now = datetime.now(KST).replace(minute=0, second=0, microsecond=0)
        tm1 = (now - timedelta(hours=self.hours_back)).strftime("%Y%m%d%H%M")
        tm2 = now.strftime("%Y%m%d%H%M")
        async with httpx.AsyncClient() as client:
            frames = []
            for path, kind in (("kma_buoy2.php", "buoy"), ("kma_lhaws2.php", "lhaws")):
                try:
                    frames.append(parse_obs(await self._get(client, path, tm1=tm1, tm2=tm2,
                                                            stn=0), kind))
                except AdapterError as e:
                    if "401" in str(e) or "403" in str(e):
                        raise
            await self._refresh_station_table(client)
        if not frames:
            raise AdapterError("KMA: no observations parsed")
        return pd.concat(frames, ignore_index=True)

    async def fetch_range(self, start_kst: datetime, end_kst: datetime) -> pd.DataFrame:
        """기간 백필 (하루 단위 분할 호출)."""
        frames = []
        async with httpx.AsyncClient() as client:
            t = start_kst
            while t < end_kst:
                t2 = min(t + timedelta(days=1), end_kst)
                for path, kind in (("kma_buoy2.php", "buoy"), ("kma_lhaws2.php", "lhaws")):
                    try:
                        frames.append(parse_obs(await self._get(
                            client, path, tm1=t.strftime("%Y%m%d%H%M"),
                            tm2=t2.strftime("%Y%m%d%H%M"), stn=0), kind))
                    except AdapterError:
                        continue
                t = t2
            await self._refresh_station_table(client)
        if not frames:
            raise AdapterError("KMA backfill: nothing parsed")
        return pd.concat(frames, ignore_index=True).drop_duplicates(
            subset=["station_id", "ts", "var"]).reset_index(drop=True)

    async def _refresh_station_table(self, client: httpx.AsyncClient) -> None:
        from poseidon.core.config import settings
        cache = settings.data_root / "static" / "kma_stations.parquet"
        if cache.exists() and (datetime.now().timestamp() - cache.stat().st_mtime) < 86400:
            return
        tables = []
        for inf in ("BUOY", "LHAWS"):
            try:
                tables.append(parse_station_info(
                    await self._get(client, "stn_inf.php", inf=inf, stn="", tm="")))
            except AdapterError:
                continue
        if tables:
            cache.parent.mkdir(parents=True, exist_ok=True)
            pd.concat(tables).drop_duplicates("station_id").to_parquet(cache, index=False)


def _probe() -> int:
    if not os.environ.get(ENV_KEY):
        print(f"{ENV_KEY} 환경변수에 인증키를 넣고 다시 실행하세요.")
        return 1
    a = KMAMarineAdapter()
    df = asyncio.run(a.fetch(BBox(100, 15, 150, 50)))
    print(f"최근 {a.hours_back}h: {len(df)} rows, 관측소 {df.station_id.nunique()}, "
          f"변수 {sorted(df['var'].unique())}")
    print(df[df['var'] == 'hs'].sort_values('ts').tail(5).to_string(index=False))
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--probe", action="store_true")
    sys.exit(_probe() if p.parse_args().probe else 0)
