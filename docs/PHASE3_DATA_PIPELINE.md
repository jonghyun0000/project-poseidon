# Project Poseidon — Phase 3: Data Pipeline (데이터 파이프라인)

**문서 버전:** 1.0 · **작성일:** 2026-08-04 · **상태:** 구현 완료, M3(24h 무인 운전) 증명 대기
**선행:** [PHASE2_ARCHITECTURE.md](PHASE2_ARCHITECTURE.md)

---

## 0. 제약 개정 — Open-Data-Only (중요)

사용자 조건: **1인 개발, 등록·계정 발급 없이 전 세계 공개 데이터만 사용.**
이에 따라 Phase 1 카탈로그의 Tier 0를 무인증 소스로 재편성했다:

| 역할 | 기존 계획 | 개정 (무인증) |
|---|---|---|
| 대기 강제장 | GFS + ECMWF(무인증 유지) | 동일 — GFS NOMADS ✓ |
| 파랑 경계·기준선 | GFS-Wave + CMEMS(계정) | **GFS-Wave 단독** (CMEMS는 선택) |
| 현장 파랑 관측 | KMA 부이(키) + NDBC | **NDBC + CDIP** (KMA는 선택) |
| 조위 관측 | KHOA(키) + IOC | **IOC 조위망 단독** — 부산 포함 동아시아 11개소 확인 ✓ |
| 조석 조화상수 | FES2022(AVISO 승인) | **EOT20** (SEANOE, CC-BY, 무등록) |
| 수심 | GEBCO | 동일 — **GEBCO_2026** (2026-04 공개 최신판) ✓ |

**정직한 트레이드오프:** 동아시아 해역의 파랑 진실값(부이)이 없어진다. NDBC는 태평양 외곽(괌·일본 근해 쓰나미 부이 등)뿐이다. 대응: (a) 위성 고도계 SWH가 유일한 광역 파랑 진실값이 되며 이는 CMEMS 무료 계정 하나로 열린다 — **추후 계정 1개만 만든다면 최우선은 CMEMS**임을 기록한다. (b) 그 전까지 파랑 검증은 GFS-Wave와의 교차비교 + IOC 조위(해일 성분)로 수행한다.

## 1. 과학적 설명

수집 파이프라인은 예보 시스템의 "관측 전처리(pre-processing)" 단계에 해당한다. 원칙:
(1) **원값 불변** — QC는 플래그만 부여하고 값을 바꾸지 않는다(GTSPP 관행).
(2) **계보(lineage) 완전성** — 모든 산출물은 재취득 가능한 요청 파라미터와 함께 기록된다.
(3) **멱등성** — 같은 사이클 재실행 시 중복 없이 수렴한다(관측은 station·ts·var 키 병합).

## 2. 참고문헌 (추가)

- UNESCO/IOC (2010). *GTSPP Real-Time Quality Control Manual.* — QC 플래그 체계·범위/스파이크/정체 검사의 근거.
- Hart-Davis, M. et al. (2021). "EOT20: a global ocean tide model from multi-mission satellite altimetry." *Earth Syst. Sci. Data*, 13, 3869–3884. — 조석 조화상수 대체 소스.

## 3. 구현된 아키텍처

```
scheduler.ingest_cycle (상태기계: WAITING_SOURCES→INGESTING→INGESTED|DEGRADED|FAILED)
  ├─ GridAdapter: GFSAdapter(u10,v10,msl) · GFSWaveAdapter(swh,perpw,dirpw)
  │    └→ datalake.write_grid → data/zarr/{forcing|boundary}/east-asia/{cycle}.zarr
  ├─ ObsAdapter: NDBCAdapter(.txt 8변수) · IOCSeaLevelAdapter(bbox 동적 발견, wl)
  │    └→ qc.apply_qc(범위·스파이크·정체) → datalake.write_obs → 월 파티션 parquet
  └─ Catalog(SQLite): dataset(lineage) · forecast_run · cycle_event(감사)
```
- SQLite는 PostGIS의 1인-개발 대체 (논리 스키마 동일 부분집합, Phase 11에서 이전).
- 개별 관측소 실패는 배치를 죽이지 않고, 관측 전체 실패는 DEGRADED, 격자 실패는 FAILED.

## 4. 폴더 구조 (신규 코드)

`poseidon/core/{types,config,catalog}.py` · `poseidon/ingest/adapters/{base,gfs,gfswave,ndbc,ioc}.py` · `poseidon/ingest/qc/checks.py` · `poseidon/datalake/store.py` · `poseidon/scheduler/ingest_cycle.py` · `scripts/fetch_sample_data/fetch_static.py` · `tests/unit/*` (13개)

## 5. 마일스톤 상태

- [x] Tier 0 어댑터 4종 (GFS, GFS-Wave, NDBC, IOC) — **라이브 E2E 검증 완료** (2026-08-03 06Z 사이클)
- [x] QC 3종 + 튜닝 (과잉 플래깅 18–22% → 2.4–7%)
- [x] Zarr/Parquet 데이터 레이크 + lineage
- [x] 상태기계 INGESTING 경로 + 멱등 재실행
- [x] 정적 데이터 스크립트 (GEBCO_2026·TID·OSM·EOT20, URL 전수 검증, TID 실다운로드 확인)
- [ ] **M3: 24h 무인 운전** — 사용자 실행 필요 (§6)
- [ ] 정적 데이터 전체 다운로드 (~5.2 GB, 사용자 실행)

## 6. 운전 방법 (M3 증명)

```bash
.venv/bin/python -m poseidon.scheduler.ingest_cycle --loop
```
24시간 후 확인: `forecast_run`에 사이클 4개 이상 INGESTED, `cycle_event`에 실패 없음(또는 기록된 폴백만).
정적 데이터: `.venv/bin/python scripts/fetch_sample_data/fetch_static.py` (GEBCO 4.3 GB 포함, 재개 가능).

## 7. 수학적 모델

해당 없음 (파이프라인 단계). QC 스파이크 판정: |Δ전|>τ ∧ |Δ후|>τ 인 고립점 (τ는 변수별, checks.py).

## 8. API — 해당 없음 (Phase 8에서 카탈로그 조회 API 노출)

## 9. DB — SQLite 카탈로그 (schema.sql의 dataset/forecast_run/cycle_event 부분집합)

## 10. 테스트

단위 13개 통과 (타입·QC 5종·카탈로그 2종·NDBC 파서). 라이브 E2E 1회: 격자 2종 3스텝 + 관측 2종 수집 → Zarr 2개(물리값 검수: 태풍 상황 946 hPa·Hs 15 m 정합) + parquet 173k행 → 상태 INGESTED.

## 11. 성능 (측정)

동아시아 서브셋 기준 사이클당: GRIB 스텝당 ~145 KB × 2소스, 3스텝 E2E 약 2분(대부분 NOMADS 응답 대기). 72h 전 스텝(25개)은 약 10–15분 추정 — 6시간 주기 대비 여유 충분.

## 12. 리스크

| 리스크 | 상태·대응 |
|---|---|
| 동아시아 파랑 진실값 부재 (개정 §0) | 인지됨. CMEMS 무료 계정 1개가 최선의 해제 수단 — 사용자 결정 대기 |
| NOMADS 속도 제한·간헐 5xx | 백오프 재시도 + available() 사전 확인 + 사이클 룩백 구현됨 |
| IOC 관측소 품질 편차 (검조기 정체 등) | 정체 검사(wl 30분) 적용, 플래그만 부여 |
| GEBCO_2026.nc 7.5 GB 로컬 처리 | Phase 4에서 동아시아 서브셋 추출 후 원본 보관 여부 결정 |
| **iCloud Desktop 동기화 + 디스크 95% (실제 발생, 2026-08-04)** | macOS 저장공간 최적화가 venv·소스 파일 로컬 사본을 제거(dataless)해 파일 읽기가 파일당 수십 초로 저하, pytest가 4분 소요. **조치 완료:** `.venv`·`data`를 `*.nosync` 디렉토리+심볼릭 링크로 동기화 제외, venv 재생성, 소스 전체 강제 실체화 → 테스트 0.7 s 복구. **근본 해결(사용자):** 프로젝트를 Desktop 밖(예: `~/poseidon`)으로 이전 + 디스크 여유 확보(외장 SSD). 데이터 위치는 `POSEIDON_DATA_ROOT`로 이동 가능 |

## 13. 다음 단계: Phase 4 — Physics Engine (SWE)

천수방정식 엔진(NumPy 참조 구현): Kurganov–Petrova + 정수압 재구성 + SSP-RK2 (PHASE2 §7.2).
게이트 M4 = 해석해 3종 통과(정지호수 기계정밀도, Stoker 댐붕괴, Thacker 습윤건조).
진입 조건: 본 문서 승인. (M3 24h 운전은 Phase 4와 병행 가능 — 사용자 머신에서 루프 실행만 하면 됨.)
