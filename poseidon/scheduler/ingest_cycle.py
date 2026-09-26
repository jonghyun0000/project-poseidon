"""수집 사이클 실행기 — ADR-004 상태기계의 INGESTING 경로.

사용:
  python -m poseidon.scheduler.ingest_cycle --once            # 최신 사이클 1회 수집
  python -m poseidon.scheduler.ingest_cycle --once --steps 12 # 리드 0..12h만 (빠른 검증)
  python -m poseidon.scheduler.ingest_cycle --loop            # 무인 운전 (M3: 24h 연속)

상태 흐름: WAITING_SOURCES → INGESTING → INGESTED (관측 실패만 있으면 DEGRADED, 격자 실패는 FAILED)
"""

from __future__ import annotations

import argparse
import asyncio
import logging

import numpy as np
import sys
from datetime import datetime, timezone

from poseidon.core.catalog import Catalog
from poseidon.core.config import settings
from poseidon.core.types import Cycle
from poseidon.datalake.store import write_grid, write_obs
from poseidon.ingest.adapters import (
    AdapterError,
    GFSAdapter,
    GFSWaveAdapter,
    IOCSeaLevelAdapter,
    KHOAAdapter,
    KMAMarineAdapter,
    NDBCAdapter,
)
from poseidon.ingest.qc import apply_qc

log = logging.getLogger("poseidon.ingest")

OBS_ADAPTERS = {"ndbc": NDBCAdapter, "ioc": IOCSeaLevelAdapter}
# 키(환경변수)가 설정된 한국 소스만 루프에 포함 — 미설정은 실패가 아니라 생략
if KMAMarineAdapter.configured():
    OBS_ADAPTERS["kma"] = KMAMarineAdapter
if KHOAAdapter.configured():
    OBS_ADAPTERS["khoa"] = KHOAAdapter
GRID_COLLECTIONS = {"noaa-gfs-0p25": "forcing", "noaa-gfswave-0p25": "boundary"}


async def find_available_cycle(adapters: list, max_back: int = 4) -> Cycle | None:
    """최신부터 최대 max_back 사이클 거슬러가며 전 격자 소스가 가용한 사이클 탐색."""
    c = Cycle.latest()
    for _ in range(max_back):
        checks = await asyncio.gather(*(a.available(c) for a in adapters))
        if all(checks):
            return c
        c = c.previous()
    return None


async def ingest_grids(cycle: Cycle, catalog: Catalog, run_id: str, steps: tuple[int, ...]) -> bool:
    ok = True
    for adapter in (GFSAdapter(steps), GFSWaveAdapter(steps)):
        col = GRID_COLLECTIONS[adapter.source_id]
        try:
            ds = await adapter.fetch(cycle, settings.domain_bbox)
            path = write_grid(ds, collection=col, domain=settings.domain_name,
                              cycle_label=cycle.label, catalog=catalog,
                              source_id=adapter.source_id)
            nbytes = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
            catalog.add_event(run_id, f"GRID_OK:{adapter.source_id}",
                              {"vars": list(ds.data_vars), "steps": len(steps),
                               "zarr_bytes": nbytes})
            log.info("grid ok  %-20s vars=%s zarr=%.1f MB",
                     adapter.source_id, list(ds.data_vars), nbytes / 1e6)
        except (AdapterError, OSError, ValueError) as e:
            ok = False
            catalog.add_event(run_id, f"GRID_FAIL:{adapter.source_id}", {"error": str(e)})
            log.error("grid FAIL %s: %s", adapter.source_id, e)
    return ok


async def ingest_obs(catalog: Catalog, run_id: str | None = None) -> bool:
    ok = True
    for name, cls in OBS_ADAPTERS.items():
        try:
            df = await cls().fetch(settings.domain_bbox)
            df = apply_qc(df)
            write_obs(df, provider=name, catalog=catalog)
            flagged = int((df["qc_flag"] > 0).sum())
            if run_id:
                catalog.add_event(run_id, f"OBS_OK:{name}",
                                  {"rows": len(df), "flagged": flagged,
                                   "stations": df["station_id"].nunique()})
            log.info("obs  ok  %-6s rows=%d stations=%d qc_flagged=%d",
                     name, len(df), df["station_id"].nunique(), flagged)
        except (AdapterError, OSError, ValueError) as e:
            ok = False
            if run_id:
                catalog.add_event(run_id, f"OBS_FAIL:{name}", {"error": str(e)})
            log.error("obs  FAIL %s: %s", name, e)
    return ok


def _covered_hours(catalog: Catalog, cycle_label: str) -> float | None:
    """기존 forcing zarr이 덮는 리드 시간(시). 없으면 None."""
    import xarray as xr
    rows = catalog.find_datasets("forcing", settings.domain_name, cycle_label)
    if not rows:
        return None
    try:
        ds = xr.open_zarr(rows[-1]["uri"], consolidated=False)
        t = ds.time.values
        return float((t[-1] - t[0]) / np.timedelta64(1, "h"))
    except Exception:  # noqa: BLE001 — 읽을 수 없으면 재수집이 안전
        return None


async def run_once(steps: tuple[int, ...], cycle_label: str | None = None) -> int:
    catalog = Catalog(settings.catalog_path)
    grid_adapters = [GFSAdapter(steps), GFSWaveAdapter(steps)]

    if cycle_label:
        from datetime import datetime, timezone
        cycle = Cycle(datetime.strptime(cycle_label, "%Y%m%dT%H").replace(tzinfo=timezone.utc))
    else:
        cycle = await find_available_cycle(grid_adapters)
    if cycle is None:
        log.error("no available cycle within lookback window")
        return 1

    existing = catalog.get_run(cycle=cycle.label, engine="ingest", domain=settings.domain_name)
    if existing and existing["status"] in ("INGESTED", "DEGRADED"):
        # 기존 산출물이 요청 리드를 **덮지 못하면** 건너뛰면 안 된다.
        # (실측 사고: 72h 예보를 요청했는데 24h 강제장으로 돌아, 24h 이후는 마지막
        #  시점 바람이 고정된 채 외삽됐다. wave_cycle의 wind_at은 t_axis[-1]로 클램프한다.)
        covered = _covered_hours(catalog, cycle.label)
        want = float(max(steps))
        if covered is not None and covered >= want - 1e-6:
            log.info("cycle %s already ingested (%s, %.0fh 확보) — obs refresh only",
                     cycle.label, existing["status"], covered)
            await ingest_obs(catalog)
            return 0
        log.info("cycle %s 재수집: 기존 %sh < 요청 %.0fh",
                 cycle.label, "?" if covered is None else f"{covered:.0f}", want)

    run_id = existing["run_id"] if existing else catalog.create_run(
        cycle=cycle.label, engine="ingest", domain=settings.domain_name)
    catalog.set_status(run_id, "INGESTING")

    grids_ok = await ingest_grids(cycle, catalog, run_id, steps)
    obs_ok = await ingest_obs(catalog, run_id)

    if grids_ok and obs_ok:
        catalog.set_status(run_id, "INGESTED")
    elif grids_ok:
        catalog.set_status(run_id, "DEGRADED", degraded=True)  # 관측 일부 실패
    else:
        catalog.set_status(run_id, "FAILED")
        return 1
    log.info("cycle %s done", cycle.label)
    return 0


async def run_loop(steps: tuple[int, ...], obs_interval_s: int = 3600,
                   cycle_poll_s: int = 600) -> None:
    """무인 운전: 관측은 매시, 새 격자 사이클은 10분마다 확인. M3 증명용."""
    catalog = Catalog(settings.catalog_path)
    last_obs = 0.0
    while True:
        now = datetime.now(timezone.utc).timestamp()
        try:
            if now - last_obs >= obs_interval_s:
                await ingest_obs(catalog)
                last_obs = now
            await run_once(steps)
        except Exception:  # 루프는 어떤 예외에도 죽지 않는다 (기록 후 계속)
            log.exception("ingest loop iteration failed")
        await asyncio.sleep(cycle_poll_s)


def main() -> int:
    parser = argparse.ArgumentParser(description="Poseidon ingest cycle runner")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--loop", action="store_true")
    parser.add_argument("--steps", type=int, default=max(settings.forecast_steps),
                        help="최대 리드타임(시간). 3h 간격 (기본 72)")
    parser.add_argument("--cycle", default=None, help="특정 사이클 지정 (예 20260821T00)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-5s %(name)s %(message)s")
    steps = tuple(range(0, args.steps + 1, 3))
    if args.once:
        return asyncio.run(run_once(steps, args.cycle))
    asyncio.run(run_loop(steps))
    return 0


if __name__ == "__main__":
    sys.exit(main())
