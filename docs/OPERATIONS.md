# 운영 가이드 — 무인 운전 (2026-08-25)

## 운영 루프 가동

```bash
cd "/Volumes/T7/클로드 코드 T7/클로드 대형 프로젝트/해양 파도 예측 프로그램 클로드"
export POSEIDON_KMA_AUTHKEY="<기상청 API허브 인증키>"     # 없으면 KMA 관측 생략
nohup .venv/bin/python -m poseidon.scheduler.operational \
      --hours 72 --obs-interval 3600 --poll 900 \
      > data/operational.log 2>&1 &
```

**하는 일** (`poseidon/scheduler/operational.py`)
1. 매시 관측 갱신 (NDBC·IOC·KMA — 키가 있는 소스만)
2. 15분마다 새 GFS 사이클 확인 → 감지 시 **수집(72 h) → L1 예보(72 h) → 콜로케이션** 전체 체인
3. 결과를 카탈로그 상태기계와 `error_sample`에 기록

**하지 않는 일**: L2 중첩, 해일, AI 보정 배포 — 전부 수동/옵션이다.

## 왜 `ingest_cycle --loop`가 아닌가

`--loop`는 **수집만** 한다. 예보·콜로케이션이 없어 `error_sample`이 쌓이지 않고,
검증 표본이 늘지 않는다. 운영 루프는 전체 체인을 돌린다.

## 안전 성질 (tests/unit/test_operational.py)

| 성질 | 보증 |
|---|---|
| 어떤 단계가 실패해도 루프가 죽지 않는다 | 전 예외를 잡아 기록 후 다음 주기 |
| 이미 완료된 사이클은 재실행하지 않는다 | 단, **리드 커버리지 충족 여부**를 확인 |
| 리드 부족 시 재생산한다 | 2026-08-25 사고(72 h 예보를 24 h 강제장으로 돌림) 회귀 방지 |
| 디스크 부족 시 수집을 멈춘다 | 여유 < 10 GB 중단, < 20 GB 경고 |

## 자원

| 항목 | 실측 |
|---|---|
| 사이클당 저장 | forcing 62 MB + boundary 62 MB = **124 MB** |
| 사이클당 계산 | 수집 ~5분 + 예보 ~15분 (419스텝, JAX) = **약 20분** |
| 6시간 주기 여유 | 충분 (20분/360분 = 6%) |
| 연간 저장 추정 | 4사이클/일 × 124 MB × 365 = **약 180 GB** |
| 현재 디스크 여유 | 541 GB (T7) |

## 모니터링

```bash
tail -f data/operational.log                      # 실시간 로그
curl -s localhost:8811/v1/system/cycles | head     # 사이클 상태(대시보드 API)
```

대시보드([localhost:8811](http://localhost:8811))의 HUD가 사이클 신선도를
`realtime` / `recent` / `historical` 3단계로 표시한다. 루프가 정상이면
`realtime`(적색 "아카이브" 아님)이어야 한다.

## 정지

```bash
pkill -f "poseidon.scheduler.operational"
```

## 관측 공백 해소 목적

2026-08-25 기준 KMA 관측이 **2026-08-22 12:40 UTC에서 끊겨 있다**(루프 미가동).
이 때문에 72 h 예보의 리드 48–72 h 구간이 **검증 불가**였다. 루프를 켜 두면
관측이 연속 축적되어 장리드 검증이 가능해진다 — 이것이 이번 가동의 1차 목적이다.

## 알려진 제약

- **KHOA 키 없음** — 조위·해류 관측 미수집. 키 확보 시 환경변수만 추가하면 자동 편입
- **NDBC 52211(사이판)** 마지막 데이터 2026-08-10 — 도메인 남단의 유일한 무인증 파랑부이가
  사실상 정지 상태다
- 루프는 L1만 돌린다. L2 중첩은 `scripts/nested_chain.sh`로 별도 실행

## 2026-09-11 인수인계 메모

- 운영 루프는 **2026-09-07 13:11(로컬) 이후 멈춰 있다.** 마지막 PUBLISHED 사이클은 `20260906T18`. 재가동 절차는 `HANDOFF.md` §3
- **로그 키 유출을 막았다.** httpx 가 요청 URL 전체를 INFO 로 남기고 기상청은 키를 URL 파라미터(`authKey=`)로 받아,
  기존 `data/operational.log` 에 키가 150건 쌓여 있었다. 가렸고, 이제 `poseidon/core/log_redact.py` 가
  kma·khoa 어댑터 import 시 httpx 로거에 필터를 붙인다(`tests/unit/test_log_redact.py`)
- 폴더를 옮겼다면 가동 전에 `.venv/bin/python scripts/rebase_catalog.py` (카탈로그가 절대경로를 저장한다)

## 2026-09-11 운영 복구 기능

현재 폴더에서 `.venv/bin/python -m poseidon.scheduler.operational --dry-run`으로
로컬 미예보 목록을 확인한다. `--backfill --hours 72`는 해당 입력만으로 예보/채점 후 종료한다.
일반 루프는 로컬 입력과 최근 `--catchup-days`일(기본 2, 0~9)의 누락분을 오래된 순서로 복구한다.
기존 L1 산출물을 덮어쓰지 않는다. 장시간 중단 뒤에는 `--catchup-days 9`를 사용한다.
강제장과 경계 모두 커버리지를 만족해야 생산한다. 관측 갱신 뒤 최근 예보를 재채점한다.

macOS 사용자 서비스:

```bash
.venv/bin/python scripts/manage_services.py install
.venv/bin/python scripts/manage_services.py status
.venv/bin/python scripts/manage_services.py remove
```

로그는 `~/Library/Logs/Poseidon/api.service.log`, `~/Library/Logs/Poseidon/operational.service.log`. 로그인 시 시작하고 비정상 종료 후
재시작한다. SSD 분리·Mac 수면 중에는 동작하지 않는다. 키는 plist에 복사하지 않는다.
KMA/KHOA 키 없이도 공개 무인증 원천으로 운영되지만 해당 관측은 수집하지 못한다.
수동 운영 및 백필은 동일 프로세스 잠금으로 중복 실행을 막는다.

## 2026-09-22 — 서비스 운영 (PHASE27)

```bash
set -a; source .env; set +a                          # POSEIDON_KMA_AUTHKEY 가 있어야 채점이 돈다
.venv/bin/python scripts/manage_services.py install  # api · operational(caffeinate) · health(30분)
.venv/bin/python scripts/manage_services.py status
```

- 운영 루프: 반복마다 관측 갱신 → 원천 조회 → **입력 없는 사이클을 오래된 순으로 전부 확보** → **예보는 최신 순으로 하나**
- 감시: `~/Library/Application Support/Poseidon/health.json`, 경보는 macOS 알림(같은 경보 6시간 1회)
- 상시 가동 전제: 전원 연결·덮개 열림·T7 연결. `caffeinate` 는 유휴 절전과 (전원 연결 시) 시스템 절전만 막는다
- 키를 바꾸면: 새 키를 셸에 올리고 `manage_services.py install --service operational`
- 위 "신선도 3단계" 서술은 낡았다 — 코드는 `realtime / delayed / historical / replay` 4종이다

