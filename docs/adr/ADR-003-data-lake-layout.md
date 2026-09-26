# ADR-003: 데이터 레이크 레이아웃 (Zarr 청크 전략)

**상태:** 승인 대기 · **날짜:** 2026-08-04 · **단계:** Phase 2

## 맥락
격자 시계열(강제장·예보·분석장)과 점 관측(부이·조위)을 저장해야 한다. 조회 패턴이 상반된다:
(a) 대시보드 — 한 시각의 공간 슬라이스, (b) 점 예보/검증 — 한 지점의 시간 시계열, (c) AI 학습 — 대용량 순차 읽기.

## 결정
1. **격자 데이터: Zarr v3** (로컬 디스크 → 추후 S3 호환 스토리지로 무변경 이전).
   - 스토어 구조: `data/zarr/{collection}/{domain}/{cycle:YYYYMMDDTHH}.zarr`
     (collection = forcing | boundary | forecast | analysis)
   - **청크 규칙:**
     - 2D 필드 (Hs, U10, η): `(time=1, y=256, x=256)` — 슬라이스 조회 최적
     - 시계열 전용 미러 (선별 변수만): `(time=all, y=8, x=8)` — 점 조회용 재청크 사본 (예보 공개 후 생성)
     - 스펙트럼 (선별 출력점만): `(time=24, freq=32, dir=36, point=64)` — 전 격자 스펙트럼 저장은 금지(용량 폭발), 지정 출력점(부이 위치+경계)만 저장
   - 압축: Blosc zstd level 3 + bitround(유효자릿수 관리, 변수별 문서화)
2. **점 관측: GeoParquet** — `data/parquet/obs/{provider}/{yyyy}/{mm}.parquet`, DuckDB로 질의.
3. **카탈로그·계보(lineage):** PostGIS의 `dataset`·`forecast_run` 테이블이 유일한 진입점.
   파일 경로를 코드에 하드코딩하는 것을 금지하고 카탈로그 경유를 강제한다.
4. **원본 보존 정책:** GRIB/NetCDF 원본은 변환 검증 후 삭제, 재취득 URL·요청 파라미터를 lineage에 기록 (PHASE1_DATA_CATALOG §I).

## 결과
- 1년 운영 추정 용량 1.5–3 TB 유지 가능 (동아시아 서브셋).
- xarray+zarr 상에서 (a) <100 ms 슬라이스, (b) 재청크 미러로 <200 ms 점 시계열 목표.
- 스키마 변경은 collection 버전 디렉토리(`…/v2/`)로 처리, 혼합 버전 읽기는 카탈로그가 중재.
