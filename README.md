# Project Poseidon — Global Ocean Digital Twin AI

최신: [Phase 26 자동 해상 항로](docs/PHASE26_AUTOMATIC_SEA_ROUTES.md) — 세계 항구 쌍의 거리 기준 항로망 후보·육지 교차 검사·파랑/바람 시뮬레이션 연결. 미검증 항구 접근 구간은 계산에서 제외한다. [화면 열기](http://127.0.0.1:8811/console?view=voyage&source=global).

2026-09-12: [Phase 25 세계 항구](docs/PHASE25_WORLD_PORT_CATALOG.md) — 234개 국가·지역의 18,582개 항만 위치·시설 검색, 14,057개 좌표 군집 지도, 항로 출발·도착·경유 연결. [화면 열기](http://127.0.0.1:8811/console?view=ports&source=global).

2026-09-12: [전 지구 원천의 관측 대조](docs/PHASE24_GLOBAL_OBSERVATIONAL_VALIDATION.md).
부이 159개소·8사이클·4,840쌍을 실제 대조했다. 파고 RMSE 0.300 m는 확보한 표본의 성적이며
전 지구 정확도 승인이 아니다. 리드·해역·고파고의 표본 수와 미검증 조건을 `/console?view=validation`에 표시한다.
Sentinel-3A 외해 관측도 별도 대조했다: 고유 7,484구간/15,953쌍, RMSE 0.381 m.
남극해 1.115 m, 6–8 m 파고대 0.954 m로 오차가 더 크며, 8 m 초과·계절·연안 등은 검증되지 않았다.

2026-09-12: [전 세계 해역·항로 확장](docs/PHASE23_GLOBAL_OCEAN.md) — 기본 콘솔이 전 지구 NOAA 예보로 열린다.
0.25°·6시간 간격·16일 파랑/해상풍, 원양 항로, 날짜변경선, 전 지구 육지 표본 검사.
상단에서 기존 동아시아 Poseidon 모델로 전환할 수 있다. 후속 관측 대조와 한계는 Phase 24에 기록한다.

2026-09-12: [항로 바람·도착 마감·선박 연료 기준선](docs/PHASE22_VOYAGE_ENVIRONMENT_PERFORMANCE.md).
같은 사이클의 GFS 바람을 조회하고 도착 제약을 비교한다. 연료·연소 CO₂는 제공 성능표가 있을 때만
정온·무해류 가정으로 계산하며, 실선 성능 자료가 없는 기본 화면은 값을 만들지 않는다.

2026-09-12: [항로 시뮬레이션 1차](docs/PHASE21_VOYAGE_SIMULATION.md) — `/console?view=voyage`.
웨이포인트·UTC 출항·SOG 입력으로 거리/침로/도착 예정시각/이동 파랑을 계산하고, 출항 지연·감속을 비교한다.

기본 분석 화면: <http://127.0.0.1:8811/> · 기존 분석: `/legacy` · 선교 시뮬레이션: `/bridge`.
2026-09-12: 지점·추세·검증·운영 이력·출처 포함 CSV를 새 콘솔로 통합했다.
[구현 결과와 산업용 완료 조건](docs/PHASE20_INDUSTRIAL_PRODUCT.md). **현업 투입 적합성은 아직 미확보**다.

해양 표면·파랑·조석·해일을 다계층(전지구 L0 → 해역 L1 → 연안 L2)으로 시뮬레이션·예측하고,
관측으로 스스로 오차를 학습해 개선하는 해양 디지털 트윈.

> **인수인계 (2026-09-11)** — 처음이면 [HANDOFF.md](HANDOFF.md) → [AGENTS.md](AGENTS.md) → [docs/LESSONS.md](docs/LESSONS.md) 순으로 읽을 것.
> 아래 '현재 단계'는 **작성 당시의 기록**이다. 이후 정정된 줄에는 **[정정]** 을 달았고 원문은 지우지 않았다(AGENTS.md §1-5).

## 현재 단계
- [x] **Phase 1 — Research:** [docs/PHASE1_RESEARCH.md](docs/PHASE1_RESEARCH.md) + [데이터 카탈로그](docs/PHASE1_DATA_CATALOG.md)
- [x] **Phase 2 — Architecture:** [docs/PHASE2_ARCHITECTURE.md](docs/PHASE2_ARCHITECTURE.md) + [ADR 6건](docs/adr/)
- [x] **Phase 3 — Data Pipeline:** [docs/PHASE3_DATA_PIPELINE.md](docs/PHASE3_DATA_PIPELINE.md) — 무인증 공개 소스 4종, 라이브 E2E 검증 완료. M3(24h 루프)는 `python -m poseidon.scheduler.ingest_cycle --loop`
- [x] **Phase 4 — Physics Engine (SWE):** [docs/PHASE4_PHYSICS_ENGINE.md](docs/PHASE4_PHYSICS_ENGINE.md) — **M4 통과**: 정지호수 <10⁻¹⁰, Stoker 수렴차수 ~1, Thacker 오차 0.2%·질량 10⁻¹⁶
- [x] **Phase 5 — Ocean Engine (스펙트럼 파랑):** [docs/PHASE5_WAVE_ENGINE.md](docs/PHASE5_WAVE_ENGINE.md) — **M5 통과**: JONSWAP 에너지 곡선 ±7%, DIA 보존 <5%, 테스트 34/34
- [x] **Phase 6 — 2D 지역 모델 + 실데이터 결합:** [docs/PHASE6_REGIONAL_COUPLING.md](docs/PHASE6_REGIONAL_COUPLING.md) — 실제 GFS 사이클로 6 h 예보 생산 (GFS-Wave 대비 corr 0.982/RMSE 0.44 m), 부산 조석 RMSE 3.9 cm, 테스트 41/41
- [x] **Phase 7 — Prediction Engine:** [docs/PHASE7_PREDICTION_ENGINE.md](docs/PHASE7_PREDICTION_ENGINE.md) — 24 h 다중 리드 사이클(리드별 skill 곡선), 콜로케이션→error_sample, AI 잔차 보정기(LSO-CV), 해일 강제 해석검증 2종, 테스트 46/46. M7 최종 게이트는 운전 데이터 축적 후
- [x] **Phase 8 — Visualization + API (1차):** [docs/PHASE8_VISUALIZATION_API.md](docs/PHASE8_VISUALIZATION_API.md) — FastAPI 8종 + MapLibre globe 대시보드, 브라우저 실검증 (태풍 서진·지점 예보·조석 패널). 실행: `.venv/bin/python -m uvicorn poseidon.api.app:app --port 8811`
- [x] **Phase 8-2 — 대시보드 개편:** [docs/PHASE8_DASHBOARD_V2.md](docs/PHASE8_DASHBOARD_V2.md) — 감사에서 버그 15건(치명 3) 발견·수정, 검증 성적 5탭·부이 예보대조·적용가능성 배지 신설. 서빙 육지오염(네 번째 반복) 수정
- [x] **Phase 9 — Optimization (1차):** [docs/PHASE9_OPTIMIZATION.md](docs/PHASE9_OPTIMIZATION.md) — 파랑 엔진 JAX 이관: 24 h 사이클 27.5분→**4분**(CPU ×7), 동등성 게이트 통과(리드 지표 5자리 일치), 테스트 51/51
- [~] **M7 진행 중 (2026-08-23):** 기상청 API허브 연동(52개소) → 6사이클 1,341표본. 자체 L1 RMSE 0.204 m vs GFS-Wave 0.230 m(부이 기준 우위), AI 보정 LSO-CV +10.3%(게이트 15% 미달)
  **[정정]** GFS-Wave는 초기장·경계·비교 기준선을 겸하는 비독립 기준선이다(오차 상관 0.835) — 이 우위는 독립 스킬이 아니다([PHASE12 §5](docs/PHASE12_VALIDATION.md)). +10.3%는 이후 재생산 수치로 대체됐다(PHASE12 §6.2)
- [~] **L2 연안 0.05° 중첩 (Phase 9-2):** 구현·검증 완료. 검증 코드 육지오염 결함을 발견·정정한 결과
  L2는 **편차 제거 시 연안에서 L1보다 정확**(천해 −9.6%, 중간대륙붕 −3.5%)하나 계통 저편향(−0.064 m)이 있다.
  **[정정]** 매칭 표본에서 L2의 성적 이득은 확인되지 않았다. 확인된 가치는 연안 커버리지 확대뿐이다([PHASE12 §3.6](docs/PHASE12_VALIDATION.md)). 최신 사이클에는 L2가 없다
  원인: GFS-Wave 경계 자체 bias −0.10 m + L1의 과잉 수치확산(보정기준의 4.8배)이 우연히 보상하던 것.
- [~] **AI 보정 (M7):** 동결 설정(uno2/none) 전량 재생산 후 LSO-CV **+9.3%** [95%CI +3.9, +14.3] **유의**.
  게이트 15% 미달이나 그 기준은 오염 데이터(bias −0.075) 시절 값 — 현재 −0.047이므로 재검토 대상
  게이트 15% 미달 + 부트스트랩 유의성 미달 → **배포 보류, 표본 축적 중**. 상세: [PHASE9 §4–5](docs/PHASE9_OPTIMIZATION.md)
  **[2026-09-11 현재]** 보정기 미배포. +9.3%는 오프라인 교차검증 결과이며 배포 성적이 아니다(`/v1/system/status`의 `corrector.offline_lso_cv`). 화면·API의 모든 예보값은 물리 원값이다

## 운영 (무인 운전)
```bash
export POSEIDON_KMA_AUTHKEY="<기상청 인증키>"
nohup .venv/bin/python -m poseidon.scheduler.operational --hours 72 > data/operational.log 2>&1 &
```
수집 → 72 h 예보 → 콜로케이션 전체 체인을 자동 실행. 상세: [docs/OPERATIONS.md](docs/OPERATIONS.md)

## 엔진 동결
수치 설정(`advection="uno2"`, `gse="none"`)은 [docs/ENGINE_FREEZE.md](docs/ENGINE_FREEZE.md)에 동결·선언됨.
변경 시 전 산출물 재생산이 의무. 모든 zarr·error_sample은 자기 출처(엔진 설정·생성시각)를 기록한다.

## 제약 (2026-08 확정)
1인 개발 · **등록 없이 공개된 데이터만 사용** (조석은 FES2022 대신 EOT20, 상세: PHASE3 §0)

## 구조 (ADR-006)
`poseidon/` 단일 파이썬 패키지(모듈 경계는 임포트 린트로 강제) · `web/` Cesium+deck.gl+WebGPU(계획 — **실제는 `web/public` 단일 HTML, MapLibre·WebGL2. `web/src`는 비어 있다**) ·
`tests/` 5분류(과학 테스트가 마일스톤 게이트) · `data/` Zarr 데이터 레이크(git 제외) · `docs/` 전 산출물.
각 모듈의 경계 정의는 해당 디렉토리 README 참조.

## 원칙
1. 물리 방정식은 동료심사 문헌에서만 (추측 금지)
2. 모든 엔진은 해석해 검증 통과 전 운영 금지
3. AI는 물리를 대체하지 않고 보정한다 — 보정 여부는 API에 항상 노출
4. Phase 게이트 통과 없이 다음 단계 진입 금지

## 한국 관측 API (선택 — 키는 환경변수로만, 저장소에 커밋 금지)
```bash
export POSEIDON_KMA_AUTHKEY="<기상청 API허브 인증키>"     # 해양기상부이·파고부이 (sea_obs)
export POSEIDON_KHOA_KEY="<바다누리 서비스키>"            # 조위관측소·해양관측부이
.venv/bin/python -m poseidon.ingest.adapters.kma --probe    # 첫 사용 시 응답 형식 확인
.venv/bin/python -m poseidon.ingest.adapters.khoa --probe
```
키가 설정되면 수집 루프(`ingest_cycle`)가 자동으로 두 소스를 포함하고, 관측은 error_sample을
통해 검증·AI 보정으로 흘러간다. 미설정 시 조용히 생략(실패 아님).

- [x] **72 h 리드 확장:** 강제장 커버리지 결함 수정 후 재생산. GFS-Wave 대비 우위가 리드에 비례 증가 — **0–12 h +12%, 12–24 h +22%, 24–48 h +28%** (전부 95% CI 유의, 48–72 h는 관측 없어 검증 불가). 사이클당 15분
  **[정정]** 0–12 h 유의성은 표본 증가로 소멸했다(PHASE12 §6.2). 48–72 h '검증 불가'는 관측 부족이 아니라 **채점 누락**이었고 재콜로케이션으로 해소됐다(PHASE12 §3.2). GFS-Wave 대비 우위는 비독립 기준선 위의 증분이다. 현재의 리드별 편향 구조는 [PHASE17](docs/PHASE17_BIAS_STRUCTURE.md)
- [~] **Phase 10 — 관측 동화(OI):** [docs/PHASE10_ASSIMILATION.md](docs/PHASE10_ASSIMILATION.md) — **조건부 채택**(기본값 off). 독립검증에서 리드 0–9 h −3~−5% 유의 개선, **리드 12 h 이상 효과 0**, 유효 반경 150 km. 사전등록 G1(편향 40% 제거) 실패·G2~G4 통과. 해운 항로(리드 12–72 h)에는 기여 없음

- [x] **Phase 12 — 과학적 검증 보고서:** [docs/PHASE12_VALIDATION.md](docs/PHASE12_VALIDATION.md) — 비독립 기준선 명시, 정정 이력 보존. **해운 적합성 판정: 사용 불가**(Hs 4 m 이상·파주기/파향 미검증)
- [x] **Phase 13 — 파주기·파향 저장:** [docs/PHASE13_WAVE_PERIOD_DIRECTION.md](docs/PHASE13_WAVE_PERIOD_DIRECTION.md) — 예보 zarr 8변수(hs + 모멘트 m0/m1/m2/a1/b1 + tp/dirp). 방향 규약 진북 0·시계·오는 방향. Tm02 채점 불가
- [x] **Phase 14 — 선교 시점:** [docs/PHASE14_BRIDGE_VIEW.md](docs/PHASE14_BRIDGE_VIEW.md) — `/bridge`, 선형 중첩 해면 + IMO MSC.1/Circ.1228 판정. 유체역학 해석이 아니다
- [x] **Phase 15 — 스웰 감쇠(Ardhuin 2010):** [docs/PHASE15_SWELL_DISSIPATION.md](docs/PHASE15_SWELL_DISSIPATION.md) — 구현했으나 **승급 안 함**(정상 사례 게이트 G2 실패)
- [x] **Phase 16 — 태풍역 Cd 포화:** [docs/PHASE16_DRAG_SATURATION.md](docs/PHASE16_DRAG_SATURATION.md) — **가설 반증, 승급 안 함**(적용 면적 0.02~0.19%). §6에서 '태풍역 생성 과잉'이 단일 사이클 현상임을 확인
- [x] **Phase 17 — 편향 구조:** [docs/PHASE17_BIAS_STRUCTURE.md](docs/PHASE17_BIAS_STRUCTURE.md) — 자체 엔진 관측 대비 편향 무의(−0.003 m). 유의한 것은 리드 0–6 h 과소(−0.163 m)뿐이며 GFS-Wave 상속분
- [x] **Phase 18 — 대시보드 재설계:** [docs/PHASE18_DASHBOARD_V3.md](docs/PHASE18_DASHBOARD_V3.md) — 현업 제품 원문 대조, 시간표 골격·자격 줄·주간/야간 팔레트. 서버 무변경. 남은 단계 6~12

## 2026-09-11 후속 작업

로컬 실행 오류, 다변수 시간표, UTC 시각, IMO 조건 해석, 운영 누락 복구를 보완했다.
변경 및 미완료 조건은 [Phase 19](docs/PHASE19_COMPLETION.md)에 기록했다.
대시보드는 `http://127.0.0.1:8811/`, 최신 API 계약은 실행 서버의 `/openapi.json`과 `/docs`에 있다.
상업 항해용 완성·검증 판정은 아직 아니다.
