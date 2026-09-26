# ADR-004: 예보 사이클 오케스트레이션 (상태기계)

**상태:** 승인 대기 · **날짜:** 2026-08-04 · **단계:** Phase 2

## 맥락
6시간 주기 예보 사이클은 외부 데이터 지연·부분 실패가 일상인 환경에서 무인 운전되어야 한다.
G-1은 Airflow+Kafka를 전제했으나, 단일 노드에서 이는 운영 복잡도만 더한다.

## 검토한 대안
- Airflow/Prefect/Dagster — 분산·UI 강점, 단일 노드에선 과잉. 상태 의미론이 우리 도메인(사이클·폴백)과 어긋남.
- Kafka — 다중 생산자/소비자 없음(현 단계). Phase 9+에서 스트리밍 관측 유입 시 재평가.
- **자체 상태기계 + asyncio 스케줄러** — 도메인 상태를 1급으로 모델링, DB에 상태 영속, 재시작 시 재개 가능.

## 결정
`poseidon/scheduler`에 명시적 상태기계를 구현한다. 상태는 DB(`forecast_run.status`)에 영속.

```
        ┌──────────────────────────────────────────────────────────┐
        │  WAITING_SOURCES ──(필수 소스 확보|타임아웃)──► INGESTING │
        └──────────────────────────────────────────────────────────┘
INGESTING ──► PREPROCESSING ──► BOUNDARY_READY ──► RUNNING_L1 ──► RUNNING_L2
    │(소스 실패)                                        │(엔진 실패)
    ▼                                                   ▼
 DEGRADED(폴백 소스로 계속: GFS↔ECMWF)               FAILED(사이클 폐기, 이전 예보 유지+경보)
RUNNING_L2 ──► AI_CORRECTING ──► VALIDATING ──► PUBLISHING ──► PUBLISHED ──► ARCHIVED
                   │(모델/피처 결측)      │(지표 이상)
                   ▼                      ▼
              SKIP_CORRECTION        PUBLISHED_WITH_WARNING
```

전이 규칙:
- 모든 상태에 **타임아웃·재시도 예산**(소스별 백오프)을 부여. `WAITING_SOURCES`는 필수 소스(GFS 바람, 경계 파랑)가 T+5h까지 미확보 시 폴백 소스로 `DEGRADED` 진입.
- `AI_CORRECTING` 실패는 사이클을 죽이지 않는다 — 물리 예측 원본으로 강등 공개(`SKIP_CORRECTION`), 플래그 기록.
- `VALIDATING`은 공개 전 자기건전성 검사(NaN, 물리 범위, 전 사이클 대비 급변)이며, 실패 시 경고 부착 공개 또는 차단을 규칙표로 결정.
- 모든 전이는 `cycle_event` 테이블에 감사 로그로 남긴다.

## 결과
- 외부 의존: APScheduler(크론 트리거) + asyncio뿐. Airflow/Kafka 도입은 Phase 9 재평가 항목.
- 대시보드 '시스템 상태' 페이지는 이 상태기계를 그대로 시각화한다.
