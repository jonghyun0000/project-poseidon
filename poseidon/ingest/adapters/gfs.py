"""NOAA GFS 0.25° 대기 강제장 어댑터 (NOMADS grib filter, 무인증).

변수: 10 m 바람(u10, v10), 해면기압(msl). 카탈로그 A1.
"""

from __future__ import annotations

import dataclasses
import tempfile
from pathlib import Path
from typing import Any, ClassVar

import httpx
import xarray as xr

from poseidon.core.config import settings
from poseidon.core.types import BBox, Cycle
from poseidon.ingest.adapters.base import GridAdapter, fetch_bytes, lineage

FILTER_URL = "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl"
PUB_URL = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod"


def _params(cycle: Cycle, step: int, bbox: BBox) -> dict[str, Any]:
    return {
        "dir": f"/gfs.{cycle.ymd}/{cycle.hh}/atmos",
        "file": f"gfs.t{cycle.hh}z.pgrb2.0p25.f{step:03d}",
        "var_UGRD": "on", "var_VGRD": "on", "var_PRMSL": "on",
        "lev_10_m_above_ground": "on", "lev_mean_sea_level": "on",
        "subregion": "",
        "leftlon": bbox.west, "rightlon": bbox.east,
        "toplat": bbox.north, "bottomlat": bbox.south,
    }


def _decode_step(raw: bytes) -> xr.Dataset:
    """GRIB2 → (u10, v10, msl). cfgrib은 파일 경로가 필요하므로 임시 파일 사용."""
    with tempfile.NamedTemporaryFile(suffix=".grib2", delete=False) as f:
        f.write(raw)
        path = Path(f.name)
    try:
        wind = xr.open_dataset(
            path, engine="cfgrib", decode_timedelta=True,
            backend_kwargs={
                "filter_by_keys": {"typeOfLevel": "heightAboveGround", "level": 10},
                "indexpath": "",
            },
        ).load()
        msl = xr.open_dataset(
            path, engine="cfgrib", decode_timedelta=True,
            backend_kwargs={"filter_by_keys": {"typeOfLevel": "meanSea"}, "indexpath": ""},
        ).load()
    finally:
        path.unlink(missing_ok=True)
    ds = xr.merge([wind[["u10", "v10"]], msl.rename({"prmsl": "msl"})[["msl"]]])
    return ds


class GFSAdapter(GridAdapter):
    source_id: ClassVar[str] = "noaa-gfs-0p25"
    tier: ClassVar[int] = 0

    def __init__(self, steps: tuple[int, ...] | None = None) -> None:
        self.steps = steps or settings.forecast_steps

    def _last_file_url(self, cycle: Cycle) -> str:
        return (f"{PUB_URL}/gfs.{cycle.ymd}/{cycle.hh}/atmos/"
                f"gfs.t{cycle.hh}z.pgrb2.0p25.f{max(self.steps):03d}.idx")

    async def available(self, cycle: Cycle) -> bool:
        async with httpx.AsyncClient() as client:
            try:
                r = await client.head(self._last_file_url(cycle), timeout=20)
                return r.status_code == 200
            except httpx.HTTPError:
                return False

    async def fetch(self, cycle: Cycle, bbox: BBox) -> xr.Dataset:
        import asyncio

        sem = asyncio.Semaphore(settings.http_concurrency)

        async def one(client: httpx.AsyncClient, step: int) -> xr.Dataset:
            async with sem:
                raw = await fetch_bytes(client, FILTER_URL, _params(cycle, step, bbox))
            return await asyncio.to_thread(_decode_step, raw)

        async with httpx.AsyncClient() as client:
            parts = await asyncio.gather(*(one(client, s) for s in self.steps))

        ds = xr.concat(parts, dim="valid_time", coords="minimal", compat="override")
        ds = (ds.drop_vars([v for v in ("time", "step", "heightAboveGround", "meanSea")
                            if v in ds.variables])
                .rename({"valid_time": "time"})
                .sortby("latitude"))
        ds.attrs["poseidon_lineage"] = str(lineage(self.source_id, {
            "url": FILTER_URL, "cycle": cycle.label, "steps": list(self.steps),
            "bbox": dataclasses.asdict(bbox),
        }))
        return ds
