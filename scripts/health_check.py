"""Poseidon 운영 상태 점검 — 조용히 멈추는 것을 막는다 (2026-09-22).

왜 필요한가
    운영 루프가 멈춘 채 방치된 것이 세 번이다(08-27 36시간, 09-07 4일, 09-12~18 5.6일).
    launchd KeepAlive 는 프로세스가 죽을 때만 되살린다. 기계 절전·재부팅·T7 분리·키 누락으로
    "살아 있지만 일을 못 하는" 상태는 잡지 못한다. 09-07~09-22 에는 기상청 키가 빠져
    새 예보 23사이클의 채점이 전부 0건이었는데 아무 신호도 없었다.

무엇을 보는가 (하나라도 걸리면 macOS 알림 + health.json)
    1. 프로젝트 폴더(T7)가 보이는가
    2. 운영 루프 프로세스가 있는가
    3. 최신 발행 예보가 18시간보다 오래됐는가 (정상 범위 약 5.5~11.5시간)
    4. 발행 후 48시간이 지난 최근 사이클(2개 이상, 최대 4개)의 채점 표본이 전부 0건인가
       — 4개를 요구하면 루프가 멈춰 사이클이 적은 바로 그 상황을 놓친다
    5. T7 여유 공간이 20 GB 미만인가
    6. 배터리로 돌고 있는가 — caffeinate -s 는 전원 연결 때만 시스템 절전을 막는다.
       멈춘 뒤 "예보가 오래됐다"로 알게 되기 전에, 멈출 조건을 먼저 알린다
       (2026-09-27: 배터리 85% 방전 중에 운영하고 있었다)

왜 파이썬 표준 라이브러리인가 (셸 스크립트가 아닌 이유)
    처음엔 zsh + /usr/bin/sqlite3 로 짰다. launchd 에서 실행하자 macOS 개인정보 보호(TCC)가
    외장 볼륨 접근을 막았다("Operation not permitted", "authorization denied").
    운영·API 서비스가 쓰는 Python.app 은 이미 권한을 받았으므로 같은 실행 파일로 돈다.
    venv(T7 위)가 아니라 **내장 디스크의 프레임워크 파이썬**으로 실행한다 — T7 이 빠졌을 때도
    알려야 하기 때문이다. 표준 라이브러리만 쓴다.

실행: manage_services.py install 이 ~/Library/Application Support/Poseidon/ 로 복사해 30분마다 돌린다.
수동: python3 scripts/health_check.py "$PWD"        (알림 없이: HEALTH_NO_NOTIFY=1)
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

STATE_DIR = Path.home() / "Library" / "Application Support" / "Poseidon"
AGE_LIMIT_H = 18
SCORE_AGE_H = 48
SCORE_WINDOW_H = 240
DISK_MIN_GB = 20
RENOTIFY_S = 6 * 3600


def on_battery() -> bool | None:
    """pmset 으로 전원을 본다. 판정할 수 없으면 None (경보를 내지 않는다)."""
    try:
        out = subprocess.run(["/usr/bin/pmset", "-g", "batt"], capture_output=True,
                             text=True, timeout=10).stdout
    except Exception:  # noqa: BLE001
        return None
    if "Battery Power" in out:
        return True
    if "AC Power" in out:
        return False
    return None


def check(root: Path, now: datetime) -> tuple[list[str], str]:
    alerts: list[str] = []
    detail: list[str] = []
    if on_battery():
        alerts.append("배터리로 동작 중 — 전원을 연결하지 않으면 절전으로 운영이 멈춘다")
    if not root.is_dir():
        return ["프로젝트 폴더 없음 — T7 이 연결되지 않았다"], ""
    if subprocess.run(["/usr/bin/pgrep", "-f", "poseidon.scheduler.operational"],
                      capture_output=True).returncode != 0:
        alerts.append("운영 루프 프로세스가 없다")
    db = root / "data" / "catalog.sqlite"
    # 조회 실패와 "예보 없음"을 섞지 않는다. 섞으면 원인이 권한·잠금인데 예보 문제로 읽힌다.
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        latest = con.execute("select max(cycle) from forecast_run "
                             "where engine='wave-L1' and status='PUBLISHED'").fetchone()[0]
        cut48 = (now - timedelta(hours=SCORE_AGE_H)).strftime("%Y%m%dT%H")
        cut240 = (now - timedelta(hours=SCORE_WINDOW_H)).strftime("%Y%m%dT%H")
        scores = [r[0] for r in con.execute(
            "select (select count(*) from error_sample e where e.cycle = f.cycle) "
            "from forecast_run f where f.engine='wave-L1' and f.status='PUBLISHED' "
            "and f.cycle <= ? and f.cycle >= ? order by f.cycle desc limit 4",
            (cut48, cut240))]
        con.close()
    except Exception as exc:  # noqa: BLE001
        return alerts + [f"카탈로그 조회 실패: {str(exc)[:160]}"], ""
    if latest:
        t = datetime.strptime(latest, "%Y%m%dT%H").replace(tzinfo=timezone.utc)
        age_h = int((now - t).total_seconds() // 3600)
        detail.append(f"최신 발행 {latest} ({age_h} h)")
        if age_h > AGE_LIMIT_H:
            alerts.append(f"최신 예보가 {age_h}시간 전 것이다 ({latest})")
    else:
        alerts.append("발행된 예보가 없다")
    detail.append(f"48 h 경과 최근 {len(scores)}사이클 채점 {sum(scores)}건")
    if len(scores) >= 2 and sum(scores) == 0:
        alerts.append(f"채점 0건 {len(scores)}사이클 연속 — 관측 수집(기상청 키) 확인")
    free_gb = shutil.disk_usage(root).free / 1e9
    detail.append(f"T7 여유 {free_gb:.0f} GB")
    if free_gb < DISK_MIN_GB:
        alerts.append(f"T7 여유 {free_gb:.0f} GB — {DISK_MIN_GB} GB 미만")
    return alerts, " · ".join(detail)


def notify(alerts: list[str], now_s: int) -> None:
    """같은 경보는 6시간에 한 번만 알린다(알림 피로 방지). 경보 내용이 바뀌면 바로 알린다."""
    last = STATE_DIR / "last_alert"
    key = "|".join(alerts)
    try:
        prev_key, prev_t = last.read_text().split("\n")[:2]
        prev_t = int(prev_t)
    except Exception:  # noqa: BLE001
        prev_key, prev_t = "", 0
    if key == prev_key and now_s - prev_t <= RENOTIFY_S:
        return
    msg = alerts[0] + (f" 외 {len(alerts) - 1}건" if len(alerts) > 1 else "")
    script = f'display notification {json.dumps(msg)} with title "Poseidon 운영 경보"'
    subprocess.run(["/usr/bin/osascript", "-e", script], capture_output=True)
    last.write_text(f"{key}\n{now_s}\n")


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    now = datetime.now(timezone.utc)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    alerts, detail = check(root, now)
    state = "alert" if alerts else "ok"
    out = {"checked_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "state": state,
           "detail": detail, "alerts": alerts}
    (STATE_DIR / "health.json").write_text(json.dumps(out, ensure_ascii=False) + "\n")
    if alerts and not os.environ.get("HEALTH_NO_NOTIFY"):
        notify(alerts, int(now.timestamp()))
    elif not alerts:
        (STATE_DIR / "last_alert").unlink(missing_ok=True)
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
