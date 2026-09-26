# Project Poseidon — Phase 8: Visualization + API (1차)

**문서 버전:** 1.0 · **작성일:** 2026-08-04 · **상태:** 대시보드·API 가동, 브라우저 실검증 완료
**선행:** [PHASE7_PREDICTION_ENGINE.md](PHASE7_PREDICTION_ENGINE.md) · 계약: [api/openapi.yaml](api/openapi.yaml)

---

## 1. 개요

처음으로 눈에 보이는 산출물: **FastAPI 서버 + 3D 지구 대시보드**. 데이터 레이크의 실예보
(20260804T00, 태풍 사례)를 지구 위에 렌더링하고, 클릭 지점 예보·리드 슬라이더·부이 실측
·부산 조석/해일 패널을 제공한다.

실행:
```bash
.venv/bin/uvicorn poseidon.api.app:app --port 8811
# → http://localhost:8811/
```

## 2. API (구현된 엔드포인트)

| 경로 | 내용 |
|---|---|
| `GET /v1/system/cycles` | 상태기계 노출 (사이클·엔진·상태) |
| `GET /v1/field/meta` | 최신 파랑 예보 메타 (리드·경계·시각) |
| `GET /v1/field/hs.png?lead=` | Hs 필드 PNG (turbo 컬러맵, 육지 투명, 0–12 m) |
| `GET /v1/forecast/point?lat&lon` | **투명성 계약**: q50 + physics_raw + corrected + 모델 버전 (+q05/q95) |
| `GET /v1/obs/latest?var=hs` | 최근 부이 실측 (지도 마커용) |
| `GET /v1/tide/busan?hours=` | 조화 8분조 합성 조위 (37일 적합 상수 아티팩트) |
| `GET /v1/surge/busan` | SWE 해일 성분 시계열 |
| `GET /` | 대시보드 |

AI 보정 아티팩트(`data/models/corr-hs.json`)가 생기면 점 예보가 자동으로 corrected=true
+ 예측구간을 포함한다 — 코드 변경 불필요.

## 3. 대시보드 (web/public/index.html)

- **MapLibre GL v5 globe** + OSM 타일 (무인증·무토큰) + Hs 이미지 오버레이(리드 전환 시 갱신)
- 리드 슬라이더(0–24 h), 부이 마커(실측 Hs 라벨), 해양 클릭 → 지점 예보 테이블
- 부산 패널: 조석 48 h 곡선 + 해일 성분 요약(평온/주의)

**브라우저 실검증 (스크린숏 확인):** 태풍 핵(암적색)이 예보대로 24 h에 걸쳐 서진하는 것,
지점 예보의 태풍 통과 시그니처(Hs 4.0→9.3→7.8 m), 사이판 부이 2.5 m 실측 마커 표시 확인.

### ADR-005 이행 기록 (정직 보고)
CesiumJS 1.119는 이 환경의 내장 브라우저에서 쿼드트리가 무오류 정지(타일 0개 렌더)하는
문제가 있어, **1차 대시보드는 MapLibre GL globe로 구현**했다. Cesium+deck.gl 정식 빌드는
Phase 8 후속(웹 빌드 도입 시)에 재시도하며 ADR-005는 그 시점 기준으로 유지한다.
개발 중 발견한 API 변경(1.107+에서 `Viewer({imageryProvider})` 제거, `SingleTileImageryProvider.fromUrl`
비동기화)도 기록해 둔다.

## 4. 테스트

API 계약 4종 (integration 마커, 데이터 레이크 없으면 skip): 사이클 목록 / meta·PNG 헤더 /
**점 예보 투명성 계약(physics_raw 필수, 육지 400)** / 부산 조차 상식 범위. 전체 50개 통과.

## 5. 남은 항목 (Phase 8 후속·Phase 9)

- 벡터장(바람·파향) 오버레이, 시간 애니메이션, 비교 모드 — deck.gl 도입 시
- WebSocket 실시간 갱신, 타일 엔드포인트의 양자화 바이너리 전환 (openapi.yaml 계약)
- **Phase 9 (성능):** JAX/GPU 포팅 — 실측 근거: 파랑 24 h @0.25° 27.5분, 해일 12 h @0.05° NumPy로는 운영 주기 내 곤란

## 6. 다음 단계

Phase 9 — JAX 포팅(NumPy↔JAX 일치 게이트) + 0.05° 승급, 또는 운전 데이터 축적 후 M7 게이트
평가 중 사용자 선택.
