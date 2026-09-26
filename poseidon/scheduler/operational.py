"""운영 루프 — 수집 → 예보 → 콜로케이션 전체 체인의 무인 운전 (ADR-004).

`ingest_cycle --loop`는 수집만 하므로 error_sample이 쌓이지 않는다. 이 모듈은
새 GFS 사이클을 감지해 전체 체인을 돌리고, 관측은 별도 주기로 갱신한다.

사용:
  POSEIDON_KMA_AUTHKEY=... .venv/bin/python -m poseidon.scheduler.operational \
      --hours 72 --obs-interval 3600 --poll 600

설계 원칙
- 어떤 단계가 실패해도 루프는 죽지 않는다 (기록 후 다음 주기).
- 이미 완료된 사이클은 건너뛴다. 단 **요구 조건(리드 커버리지) 충족 여부**를 확인한다
  — 존재 여부만 보고 건너뛰어 72h 예보를 24h 강제장으로 돌린 사고가 있었다.
- 디스크 여유가 임계 미만이면 수집을 멈춘다 (예보·콜로케이션은 계속).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
import sys
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import xarray as xr

from poseidon.core.catalog import Catalog
from poseidon.core.config import settings
from poseidon.engines.spectral_wave import grid as spec
from poseidon.core.types import Cycle
from poseidon.scheduler import ingest_cycle as ing

log = logging.getLogger("poseidon.operational")

DISK_WARN_GB = 20.0
DISK_STOP_GB = 10.0


def disk_free_gb() -> float:
    return shutil.disk_usage(settings.data_root).free / 1e9


def forecast_coverage(
    catalog: Catalog, cycle_label: str
) -> tuple[float, frozenset[str]] | None:
    """기존 L1 예보의 (덮는 리드[시], 보유 변수). 예보가 없으면 None.

    리드만 보고 건너뛰면 안 된다. 리드는 충분한데 변수가 부족한 예보 —
    파주기·파향 저장부 추가 이전에 만들어진 hs-only 산출물 — 이 영구히
    갱신되지 않기 때문이다. "있는가"가 아니라 "요구를 충족하는가"로
    판정한다(docs/PHASE12_VALIDATION.md §6.4 교훈 2).
    """
    rows = [r for r in catalog.find_datasets("forecast", settings.domain_name, cycle_label)
            if r["source_id"] == "spectral_wave-L1"]
    if not rows:
        return None
    try:
        ds = xr.open_zarr(rows[-1]["uri"], consolidated=False)
        return float(np.max(ds.lead.values)), frozenset(ds.data_vars)
    except Exception:  # noqa: BLE001
        return None


def forecast_covered_hours(catalog: Catalog, cycle_label: str) -> float | None:
    """리드만 필요한 호출부를 위한 얇은 래퍼."""
    cov = forecast_coverage(catalog, cycle_label)
    return None if cov is None else cov[0]


def inputs_cover(catalog: Catalog, cycle_label: str, hours: float) -> bool:
    """두 원천의 시작·끝·중간 시간 간격을 확인한다. 외삽 예보 금지."""
    t0 = np.datetime64(datetime.strptime(cycle_label, "%Y%m%dT%H"))
    for collection in ("forcing", "boundary"):
        rows = catalog.find_datasets(collection, settings.domain_name, cycle_label)
        if not rows:
            return False
        try:
            with xr.open_zarr(rows[-1]["uri"], consolidated=False) as ds:
                leads = (ds.time.values - t0) / np.timedelta64(1, "h")
                if (len(leads) < 2 or not np.isfinite(leads).all()
                        or leads[0] != 0 or leads[-1] < hours
                        or np.any(np.diff(leads) <= 0) or np.any(np.diff(leads) > 3.01)):
                    return False
        except Exception:
            return False
    return True


def run_chain(cycle_label: str, hours: float, *, offline: bool = False) -> dict[str, str]:
    """한 사이클의 수집 → 예보 → 콜로케이션. 각 단계는 독립적으로 실패할 수 있다."""
    catalog = Catalog(settings.catalog_path)
    result: dict[str, str] = {"cycle": cycle_label}
    steps = tuple(range(0, int(hours) + 1, 3))

    # 1) 수집 (커버리지 부족 시 재수집 — ingest_cycle이 판단)
    if offline:
        result["ingest"] = "skipped-offline"
    elif disk_free_gb() < DISK_STOP_GB:
        result["ingest"] = f"skipped-disk({disk_free_gb():.1f}GB)"
        log.error("디스크 여유 %.1f GB < %.1f — 수집 중단", disk_free_gb(), DISK_STOP_GB)
    else:
        try:
            rc = asyncio.run(ing.run_once(steps, cycle_label))
            result["ingest"] = "ok" if rc == 0 else f"rc={rc}"
        except Exception as exc:  # noqa: BLE001
            result["ingest"] = f"error: {exc}"
            log.exception("수집 실패 %s", cycle_label)
            # 이미 확보된 입력으로 예보할 수 있으므로 수집 장애와 분리한다.

    # 2) 예보 (요구 리드와 변수를 **둘 다** 충족할 때만 건너뜀)
    cov = forecast_coverage(catalog, cycle_label)
    want_vars = frozenset(spec.SNAPSHOT_VARS)
    if cov is not None and cov[0] >= hours - 0.2 and want_vars <= cov[1]:
        result["forecast"] = f"skipped({cov[0]:.0f}h 확보)"
    else:
        if not inputs_cover(catalog, cycle_label, hours):
            result["forecast"] = "error: forcing/boundary time coverage insufficient"
            return result
        if cov is not None and cov[0] >= hours - 0.2:
            log.info("사이클 %s 재예보: 리드는 %.0fh 확보됐으나 변수 부족 %s",
                     cycle_label, cov[0], sorted(want_vars - cov[1]))
        try:
            from poseidon.scheduler.wave_cycle import run_wave_forecast
            t0 = time.time()
            m = run_wave_forecast(cycle_label, hours)
            result["forecast"] = f"ok ({time.time()-t0:.0f}s)"
            fin = m.get("final", {})
            log.info("예보 %s: 최종 리드 %.0fh RMSE %.3f (GFS-Wave 대비 자기검증)",
                     cycle_label, fin.get("lead_h", 0), fin.get("rmse", float("nan")))
        except Exception as exc:  # noqa: BLE001
            result["forecast"] = f"error: {exc}"
            log.exception("예보 실패 %s", cycle_label)
            return result

    # 3) 콜로케이션
    #
    # 주의: 이 호출은 **예보 직후 1회**만 실행된다. 그 시점에는 장리드를 덮는
    # 관측이 아직 존재하지 않으므로, 72h 예보라도 리드 몇 시간까지만 채점된다.
    # 관측이 쌓인 뒤 같은 사이클을 다시 콜로케이션해야 장리드 표본이 생긴다
    # (2026-08-27: 10개 사이클이 최대 69h 미채점 상태였고, 재콜로케이션으로
    #  표본이 2,301 -> 3,652건, 리드가 0 -> 72h 로 열렸다). (관측이 없으면 정상적으로 0건)
    try:
        from poseidon.validation.collocate import collocate_wave
        df = collocate_wave(cycle_label)
        result["collocate"] = f"{len(df)}건"
    except FileNotFoundError as exc:
        result["collocate"] = f"skipped: {exc}"
    except Exception as exc:  # noqa: BLE001
        result["collocate"] = f"error: {exc}"
        log.exception("콜로케이션 실패 %s", cycle_label)
    return result


def pending_cycles(catalog: Catalog, latest: Cycle | None = None,
                   catchup_days: int = 2) -> list[str]:
    """로컬 미예보 입력 + 최근 원천 창의 누락분. 기존 예보는 덮어쓰지 않는다."""
    domain = settings.domain_name
    forcing = {r["cycle"] for r in catalog.find_datasets("forcing", domain) if r["cycle"]}
    boundary = {r["cycle"] for r in catalog.find_datasets("boundary", domain) if r["cycle"]}
    existing = {r["cycle"] for r in catalog.find_datasets("forecast", domain)
                if r["source_id"] == "spectral_wave-L1"}
    candidates = forcing & boundary
    if latest is not None:
        cyc = latest
        for _ in range(max(1, catchup_days * 4)):
            candidates.add(cyc.label)
            cyc = cyc.previous()
    return sorted(candidates - existing)


def forecast_order(pending: list[str]) -> list[str]:
    """예보는 **최신 사이클부터** 만든다 (2026-09-22).

    오래된 순으로 처리하던 시절, 루프가 5.6일 멈췄다 재개되자 밀린 사이클을 하나에
    25~46분씩 처리하느라 2026-09-19 10:53 에도 6일 전 사이클(20260913T00)을 계산하고
    있었다. 항로 판단에 쓰는 것은 지금 예보다. 루프는 반복마다 예보를 하나만 성공시키고
    다시 원천을 조회하므로, 백로그 처리 중에 새 사이클이 도착하면 다음 반복에서 맨 앞에 선다.
    NOMADS 10일 보관 창 때문에 생기는 유실 위험은 예보 순서가 아니라 secure_inputs 가 막는다.
    """
    return sorted(pending, reverse=True)


def secure_inputs(catalog: Catalog, pending: list[str], hours: float) -> dict[str, str]:
    """입력(강제장·경계)이 없는 사이클을 **오래된 것부터** 전부 받는다.

    수집은 사이클당 약 40초, 예보는 25~46분이다. 원천(NOMADS)은 10일만 보관하므로
    보관 창의 가장자리에 있는 사이클부터 받아 두면, 예보를 최신 순으로 해도 입력이
    사라지지 않는다. 받은 입력은 기한 없이 오프라인 예보할 수 있다.
    이미 입력이 있는 사이클은 건드리지 않는다. 한 사이클의 실패가 나머지를 막지 않는다.
    """
    out: dict[str, str] = {}
    steps = tuple(range(0, int(hours) + 1, 3))
    for label in sorted(pending):
        if inputs_cover(catalog, label, hours):
            continue
        if disk_free_gb() < DISK_STOP_GB:
            out[label] = f"skipped-disk({disk_free_gb():.1f}GB)"
            log.error("디스크 여유 부족 — 입력 확보 중단")
            break
        try:
            rc = asyncio.run(ing.run_once(steps, label))
            out[label] = "ok" if rc == 0 and inputs_cover(catalog, label, hours) else f"rc={rc}"
        except Exception as exc:  # noqa: BLE001
            out[label] = f"error: {exc}"
            log.exception("입력 확보 실패 %s", label)
    if out:
        log.info("입력 확보: %s", out)
    return out


def refresh_collocations(catalog: Catalog, hours: float) -> None:
    """관측 갱신 뒤 최근 예보의 장리드를 다시 채점한다. 실패는 사이클별 격리."""
    from poseidon.validation.collocate import collocate_wave
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours + 48)
    rows = catalog.find_datasets("forecast", settings.domain_name)
    labels = sorted({r["cycle"] for r in rows
                     if r["source_id"] == "spectral_wave-L1" and r["cycle"]
                     and r["cycle"] >= cutoff.strftime("%Y%m%dT%H")})
    for label in labels:
        try:
            collocate_wave(label)
        except Exception:
            log.exception("장리드 재채점 실패 %s", label)


def loop(hours: float, obs_interval_s: float, poll_s: float,
         max_iter: int | None = None, catchup_days: int = 2) -> int:
    catalog = Catalog(settings.catalog_path)
    last_obs = 0.0
    it = 0
    log.info("운영 시작: %.0fh 예보, 최근 %d일 누락분 + 로컬 미예보 복구", hours, catchup_days)
    while max_iter is None or it < max_iter:
        it += 1
        now = time.time()
        if now - last_obs >= obs_interval_s:
            try:
                ok = asyncio.run(ing.ingest_obs(catalog))
                if ok:
                    last_obs = now
                    refresh_collocations(catalog, hours)
            except Exception:
                log.exception("관측 갱신 실패 — 예보 처리는 계속")
        try:
            adapters = [ing.GFSAdapter(tuple(range(0, int(hours) + 1, 3))),
                        ing.GFSWaveAdapter(tuple(range(0, int(hours) + 1, 3)))]
            latest = asyncio.run(ing.find_available_cycle(adapters))
        except Exception:
            log.exception("원천 조회 실패 — 로컬 입력 복구는 계속")
            latest = None
        try:
            pending = pending_cycles(catalog, latest, catchup_days)
            if latest is not None:          # 원천에 닿을 때만 받는다
                secure_inputs(catalog, pending, hours)
            # 실패한 사이클 하나가 다른 사이클을 영구 차단하지 않는다.
            for label in forecast_order(pending):
                res = run_chain(label, hours, offline=inputs_cover(catalog, label, hours))
                log.info("사이클 %s 결과: %s", label, res)
                if res.get("forecast", "").startswith(("ok", "skipped")):
                    break  # 관측 갱신·최신 원천 조회 사이에 예보는 하나씩
        except Exception:
            log.exception("루프 반복 실패 — 계속 진행")
        if max_iter is not None and it >= max_iter:
            break
        time.sleep(poll_s)
    return 0


def _main() -> int:
    ap = argparse.ArgumentParser(description="Poseidon 운영 루프 (수집→예보→콜로케이션)")
    ap.add_argument("--hours", type=float, default=72.0, help="예보 리드타임 (기본 72)")
    ap.add_argument("--obs-interval", type=float, default=3600.0, help="관측 갱신 주기(초)")
    ap.add_argument("--poll", type=float, default=900.0, help="사이클 폴링 주기(초)")
    ap.add_argument("--once", action="store_true", help="1회만 실행 (검증용)")
    ap.add_argument("--catchup-days", type=int, default=2,
                    help="최근 누락 사이클 탐색 일수(0~9); 로컬 입력은 기간 제한 없이 복구")
    ap.add_argument("--backfill", action="store_true", help="로컬 미예보를 오프라인으로 복구 후 종료")
    ap.add_argument("--dry-run", action="store_true", help="복구 대상만 출력")
    a = ap.parse_args()
    if not 0 <= a.catchup_days <= 9 or a.hours <= 0 or a.hours % 3 or a.poll < 0 or a.obs_interval < 0:
        ap.error("hours는 양의 3시간 배수, catchup-days는 0~9, 주기는 음수 불가")
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-5s %(name)s %(message)s")
    if a.backfill or a.dry_run:
        labels = pending_cycles(Catalog(settings.catalog_path))
        if a.dry_run:
            print("\n".join(labels))
            return 0
        failed = False
        for label in labels:
            res = run_chain(label, a.hours, offline=True)
            log.info("백필 %s: %s", label, res)
            failed |= not res.get("forecast", "").startswith(("ok", "skipped"))
        return int(failed)
    return loop(a.hours, a.obs_interval, a.poll, max_iter=1 if a.once else None,
                catchup_days=a.catchup_days)


def main() -> int:
    # 백필과 운영 프로세스가 같은 산출물을 동시에 쓰는 것을 막는다.
    import fcntl
    if "--dry-run" in sys.argv:
        return _main()
    settings.data_root.mkdir(parents=True, exist_ok=True)
    with (settings.data_root / "operational.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log.error("다른 운영/백필 프로세스가 실행 중")
            return 2
        return _main()


if __name__ == "__main__":
    sys.exit(main())
