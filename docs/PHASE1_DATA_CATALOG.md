# Project Poseidon — Phase 1 부속서 A: 검증된 데이터 소스 카탈로그

**문서 버전:** 1.0
**작성일:** 2026-08-04
**검증 방법:** 각 소스의 공식 문서·엔드포인트를 2026-08 시점에 웹 조사로 확인
**상위 문서:** [PHASE1_RESEARCH.md](PHASE1_RESEARCH.md)

> 표기: **Tier 0** = M3(파이프라인 가동)에 필수 · **Tier 1** = M6–M7(예보+AI 보정)에 필수 · **Tier 2** = 고도화 단계

---

## A. 대기 강제장 (바람 U₁₀, 해면기압, 복사)

### A1. NOAA GFS 0.25° — Tier 0
- **접근:** NOMADS grib filter — `https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl` (변수·영역·시간 서브셋 후 GRIB2 반환). 대량/과거분은 AWS Open Data `s3://noaa-gfs-bdp-pds` (인증 불필요)
- **내용:** UGRD/VGRD 10 m, PRMSL, 단파복사 등. 사이클 00/06/12/18Z, 리드 384 h
- **지연:** 사이클 후 ~3.5–5 h. **인증:** 불필요. **라이선스:** 미국 공공 데이터 (제한 없음)
- **역할:** L1/L2 자체 엔진의 바람·기압 강제장 주 소스
- **주의:** NOMADS는 IP당 요청 속도 제한이 있으므로 어댑터에 재시도·백오프 필수

### A2. ECMWF Open Data (IFS/AIFS) 0.25° — Tier 0 (이중화)
- **접근:** Python `ecmwf-opendata` 패키지 (PyPI), Azure/AWS 미러 존재
- **내용:** 대기 변수 + **파랑 변수 포함**(swh, mwd, mwp — IFS 결합 WAM 출력). AIFS(AI 모델) 예보도 동일 채널로 제공
- **지연:** 사이클 후 수 시간. **인증:** 불필요. **라이선스:** CC-BY-4.0 (출처 표기)
- **역할:** GFS 장애 시 폴백 + 멀티모델 앙상블 멤버

### A3. ERA5 재분석 — Tier 1 (AI 학습·후측 전용)
- **접근:** CDS API (`cdsapi`, `~/.cdsapirc` 토큰 필요) — `reanalysis-era5-single-levels`
- **내용:** 1940–현재, 시간별, 0.25°(대기)/0.5°(파랑). 통합 파랑 파라미터 + (ERA5-complete) 2D 파랑 스펙트럼
- **인증:** CDS 무료 계정. **라이선스:** Copernicus 라이선스 — 상업 이용 가능, 출처 표기 의무
- **역할:** AI 보정 모델 학습용 장기 시계열, 검증 기준선. 대기 시간(큐)이 길 수 있으므로 학습 데이터는 사전 일괄 확보

---

## B. 파랑 배경장·경계조건 (L0 전지구 계층)

### B1. NOAA GFS-Wave (WAVEWATCH III) — Tier 0
- **접근:** grib filter — `https://nomads.ncep.noaa.gov/cgi-bin/filter_gfswave.pl` (인터페이스: `https://nomads.ncep.noaa.gov/gribfilter.php?ds=gfswave`)
- **내용:** Hs, Tp, 방향, 풍파/너울 분리, 전지구 0.25° + 지역 격자. 사이클 00/06/12/18Z
- **역할:** L1 지역 파랑 엔진의 **개방 경계조건** 주 소스 + 비교 기준선
- **인증:** 불필요. **라이선스:** 제한 없음

### B2. Copernicus Marine 전지구 파랑 분석/예보 (MFWAM) — Tier 1
- **접근:** `copernicusmarine` Python 툴박스 (subset → Zarr/NetCDF, ARCO 스트리밍 지원)
- **데이터셋 ID:** `cmems_mod_glo_wav_anfc_0.083deg_PT3H-i` (1/12°, 3시간 간격, 분석+10일 예보)
- **인증:** Copernicus Marine 무료 계정. **라이선스:** 무료, 출처 표기; 재배포 조건 확인 필요
- **역할:** GFS-Wave보다 고해상도인 제2 경계조건·검증 소스

### B3. IFREMER IOWAGA WW3 후측 아카이브 — Tier 1 (AI 학습)
- **접근:** THREDDS `http://tds1.ifremer.fr/thredds/IOWAGA-WW3-HINDCAST/…` + FTP `ftp.ifremer.fr/ifremer/ww3/HINDCAST`
- **내용:** 1990–현재+6일, 정규격자(30′–2′) 및 비정형 격자(해안 100 m), **스펙트럼 데이터베이스 포함**
- **역할:** ST4 물리의 참조 구현 결과물 — 자체 엔진 검증과 AI 대리모델 학습에 모두 활용

---

## C. 해양 순환·수온·염분 (성층·해류)

### C1. CMEMS 전지구 물리 분석/예보 — Tier 1
- **데이터셋:** `GLOBAL_ANALYSISFORECAST_PHY_001_024` (NEMO 1/12°, 일별, +10일 예보) — 해류 u/v, T, S, SSH
- **역할:** 파랑-해류 상호작용(굴절 항의 U), 트윈 상태의 3D 배경장

### C2. GLORYS12 재분석 — Tier 1 (학습·후측)
- **데이터셋:** `GLOBAL_MULTIYEAR_PHY_001_030` (1993–, 1/12°)
- **역할:** AI 학습용 장기 해양 상태, hindcast 실험

### C3. OSTIA SST (GHRSST L4) — Tier 1
- **데이터셋:** `SST_GLO_SST_L4_NRT_OBSERVATIONS_010_001` (Met Office, 0.05°≈6 km, 일별, 지연 ~24 h, GDS 2.0 규격)
- **역할:** 해수온 검증·동화, 대시보드 SST 레이어

---

## D. 현장 관측 (검증·동화의 진실값)

### D1. NDBC 부이 — Tier 0
- **접근:** `https://www.ndbc.noaa.gov/data/realtime2/` (최근 45일, HTTP 텍스트)
- **형식:** `{station}.txt`(표준기상), `{station}.spec`(파랑 요약: WVHT SwH SwP WWH WWP SwD WWD STEEPNESS APD MWD), `{station}.data_spec`(원시 스펙트럼 밀도), `.swdir/.swdir2/.swr1/.swr2`(방향 스펙트럼 α₁ α₂ r₁ r₂)
- **핵심 가치:** **방향 스펙트럼 관측** — 스펙트럼 파랑 엔진을 스펙트럼 수준에서 검증 가능
- **인증:** 불필요

### D2. CDIP 파랑부이 — Tier 1
- **접근:** THREDDS `https://thredds.cdip.ucsd.edu/thredds/catalog/cdip/realtime/` (NetCDF, OPeNDAP/ERDDAP), 1991–현재 아카이브
- **내용:** Datawell Waverider 고해상도 방향 스펙트럼, 관측→공개 지연 1–3분
- **역할:** 미국 서해안 중심이지만 QC 품질이 가장 높은 스펙트럼 검증 세트

### D3. 기상청(KMA) API허브 — Tier 0 (1차 관심해역 핵심)
- **접근:** `https://apihub.kma.go.kr` (2023-02 개시, 무료 API 키). 해양 분야: **해양기상부이**(풍향풍속·기압·수온·파고·파주기·파향), **파고부이**, **표류부이**. 공공데이터포털(`data.go.kr`)에도 병행 제공
- **역할:** 동아시아 도메인의 실시간 검증·동화 관측 주 소스
- **주의:** API 키 발급 필요, 호출 한도 존재 — 수집기는 증분 수집으로 설계

### D4. 국립해양조사원(KHOA) 바다누리 오픈API — Tier 0
- **접근:** `http://www.khoa.go.kr/oceangrid/khoa/takepart/openapi/openApiDeveloperGuide.do` — 회원가입 후 API 키 자동 발급
- **내용:** **조위관측소 실시간 조위**·수온·염분·유향유속, 해양관측부이, 해양과학기지. 조석예보(조화상수 기반)도 제공
- **한도:** **일 20,000 호출** (초과 필요시 관리자 협의)
- **역할:** 조석·해일 엔진(L2)의 한반도 연안 검증 진실값 — 폭풍해일 검증의 핵심

### D5. NOAA CO-OPS 조위 API — Tier 1
- **접근:** `https://api.tidesandcurrents.noaa.gov/api/prod/` (수위·조석예측·바람), Metadata API `…/mdapi/prod/` 별도
- **역할:** 미국 연안 조석·해일 검증, 조화상수 교차검증

### D6. IOC Sea Level Station Monitoring Facility (VLIZ) — Tier 1
- **접근:** `https://www.ioc-sealevelmonitoring.org/` (전지구 실시간 검조소, 쓰나미 경보망 포함)
- **역할:** 전지구 조위 검증, 쓰나미 시나리오 검증(역사 사건 재현 시)

### D7. ARGO 플로트 — Tier 2
- **접근:** Python `argopy` (기본 소스: Ifremer ERDDAP, 일 갱신) / GDAC rsync `vdmzrs.ifremer.fr` / AWS S3 GDAC 버킷
- **역할:** 수온·염분 프로파일 → 성층·밀도 검증 (순환 엔진 도입 시)

### D8. HF 레이더 표층 해류 — Tier 2
- **접근:** NDBC THREDDS `https://dods.ndbc.noaa.gov/thredds/catalog/hfradar.html` (RTV 시간별, 500 m/1/2/6 km 격자, 표층 상부 ~2.4 m 유속)
- **역할:** 표층 해류 검증 (미국 연안). 한국은 KHOA HF 레이더 자료 별도 확인 필요

---

## E. 위성 원격탐사

### E1. 다중임무 고도계 L3 유의파고 — Tier 1 (AI 보정의 핵심 관측)
- **데이터셋:** `WAVE_GLO_PHY_SWH_L3_NRT_014_001` (CMEMS) — CryoSat-2, HY-2B/2C, Jason-3, SARAL, Sentinel-3A/B, **Sentinel-6A**(기준 임무), **SWOT nadir** 통합
- **내용:** 궤도 따라 ~7 km 간격 SWH + 고도계 풍속, 부이 교정 완료, QC 필터링 적용
- **역할:** 외해(부이 공백 해역) Hs 검증·동화 — 전지구 오차 지도를 만들 수 있는 유일한 소스
- **보조:** L4 격자 일합성 `WAVE_GLO_PHY_SWH_L4_NRT_014_003`

### E2. SWOT 광폭 SSH — Tier 2
- **접근:** AVISO L3 (`SWOT_L3_LR_SSH` v3.0, 2 km / Unsmoothed 250 m v2.0.1) 또는 NASA PO.DAAC (L2)
- **역할:** 중규모·아중규모 해면고 구조 — 순환 동화 고도화 단계

### E3. CFOSAT SWIM 방향 파랑 스펙트럼 — Tier 2
- **접근:** IFREMER IWWOC — WW3와의 콜로케이션 산출물 제공
- **역할:** 방향 스펙트럼의 위성 검증 (부이 외 유일한 스펙트럼 관측)

### E4. 산란계 해상풍 (ASCAT 계열) — Tier 1
- **데이터셋:** CMEMS `WIND_GLO_PHY_L3/L4` 계열 (KNMI/OSI SAF 처리)
- **역할:** 강제장 바람의 해상 검증 — 바람 오차는 파랑 오차의 최대 원인이므로 AI 보정 입력 특징으로 사용

### E5. 정지궤도 위성 영상 — Tier 2 (시각화·운량)
- **Himawari-9:** AWS `s3://noaa-himawari9` (인증 불필요, `--no-sign-request`), AHI L1b 풀디스크, **SNS 신규객체 알림 토픽 제공** (`NewHimawariNineObject`) — 동아시아 실시간 구름 레이어에 최적
- **GOES 시리즈:** AWS Open Data 병행 (`noaa-goes16/18/19` 버킷 계열)
- **역할:** 대시보드 구름·태풍 시각화, 향후 단파복사 추정

---

## F. 정적 데이터 (수심·해안선)

### F1. GEBCO_2025 — Tier 0
- **접근:** `https://www.gebco.net` — 전지구 15초각(≈450 m) NetCDF(압축 4 GB/해제 7.5 GB), 영역 지정 다운로드 앱(GeoTIFF/ASCII), **OPeNDAP 직접 접근 가능**
- **버전:** GEBCO_2025 (2025-08 공개, Seabed 2030 7번째 격자). ice-surface / sub-ice / **TID 격자**(소스 유형 식별 — 실측 vs 보간 구분) 3종
- **역할:** 모든 계층의 수심장. TID로 수심 신뢰도 지도 생성 → 예측 불확실성에 반영
- **라이선스:** 공개 (출처 표기)

### F2. ETOPO 2022 — Tier 1
- 15초각 전지구 지형·수심 통합 (NOAA NCEI). GEBCO와 교차검증 및 육상 범람 계산용 지형

### F3. OSM 해안선·수역 폴리곤 — Tier 0
- **접근:** `https://osmdata.openstreetmap.de` — water polygons / land polygons / coastlines (WGS84·Mercator, 주기 갱신, Shapefile 변환 용이)
- **역할:** L2 연안 격자의 육/해 마스크, 시각화 해안선. **라이선스:** ODbL (출처 표기)

### F4. Natural Earth — Tier 1
- 소축척(1:10m/50m/110m) 해안선·국경 — 전지구 줌아웃 시각화용. 퍼블릭 도메인

---

## G. 조석 조화상수

### G1. FES2022 (CNES/AVISO) — Tier 0 (권장 주 소스)
- **접근:** AVISO+ 등록 → 라이선스 동의 → 승인 후 FTP/SFTP/THREDDS 다운로드. 공식 파이썬 라이브러리 **`pyfes`** (github.com/CNES/aviso-fes) 제공
- **라이선스:** 조위(heights) 산출물은 **과학·상업 등 모든 용도 사용 가능** — TPXO보다 유리
- **역할:** 전지구 조위 계산, L2 SWE 엔진의 개방경계 조석 강제

### G2. TPXO9-atlas (OSU) — Tier 1 (교차검증)
- **접근:** tpxo.net 등록 후 NetCDF 수령. **학술·비상업 무료, 상업 이용은 별도 계약** (Egbert/Erofeeva 교수 접촉)
- **역할:** FES2022와 조화상수 교차검증. **리스크:** 서비스 상용화 시 라이선스 재검토 필수 → 주 소스는 FES2022로 확정

---

## H. AI 파랑 예측 최신 문헌 (2025–2026 추가 조사분)

기존 §2 참고문헌에 다음을 추가한다:

36. Zhang, et al. (2025). "Ocean Wave Forecasting With Deep Learning as Alternative to Conventional Models." *JAMES*, 10.1029/2025MS005285 — **OceanCastNet**: AFNO 기반 전지구 파랑 대리모델, WW3와 직접 비교. Poseidon AI 계층의 1차 참조 아키텍처.
37. "A deep operator network method for high-precision and robust real-time ocean wave prediction (DON-WP)." *Physics of Fluids* 37, 037134 (2025) — DeepONet 기반 실시간 파고 예측.
38. "A Neural Operator Emulator for Coastal and Riverine Shallow Water Dynamics." arXiv:2502.14782 — 천수방정식 신경연산자 에뮬레이터 (L2 가속의 참조).
39. "Ocean-E2E: Hybrid Physics-Based and Data-Driven Global Forecasting … with End-to-End Neural Assimilation." arXiv:2505.22071 — 물리+신경 동화 하이브리드 (Poseidon의 EnKF+AI 설계와 동일 방향, 최신 검증 사례).
40. "WaveUformer: a bias correction model for GWSM4C Wave Forecasting." *Front. Mar. Sci.* (2026) — 파랑 예보 편향 보정 전용 트랜스포머 — M7(AI 보정) 직접 참조.
41. "Accurate Mediterranean Sea forecasting via graph-based deep learning." arXiv:2506.23900 — 지역해 GNN 예보 사례.

**시사점:** 2025–26 문헌은 (a) 전지구 대리모델(AFNO/FNO 계열)과 (b) 물리모델 편향 보정(트랜스포머)의 두 갈래가 모두 성숙했음을 보여준다. Poseidon 전략(§0)의 "물리 엔진 + AI 보정 + 대리모델 가속"이 문헌과 정합함을 확인.

---

## I. 수집 우선순위·사이클 설계 (M3 요구사항으로 승격)

| 순위 | 소스 | 주기 | 사이클당 용량(동아시아 서브셋 기준 추정) |
|---|---|---|---|
| 1 | GFS 0.25 (바람·기압) | 6 h | ~50–150 MB (변수·리드 서브셋) |
| 2 | GFS-Wave (경계조건) | 6 h | ~30–100 MB |
| 3 | KMA 부이·파고부이 | 10 min–1 h | <1 MB |
| 4 | KHOA 조위 | 1 min–10 min | <1 MB (호출 한도 내 증분) |
| 5 | NDBC realtime2 | 1 h | ~수 MB (선별 관측소) |
| 6 | ECMWF Open Data | 12 h | ~100 MB |
| 7 | CMEMS 파랑/물리/SST | 일 1회 | ~200 MB–1 GB |
| 8 | 고도계 L3 SWH | 일 1회 | ~50 MB |
| 정적 | GEBCO_2025 + TID, OSM 폴리곤, FES2022 | 1회 | ~20 GB |

**저장소 추정:** 실시간 운영 1년 ≈ 1.5–3 TB (동아시아 서브셋, Zarr 압축 후). 전지구 원본 보관은 하지 않는다 — 서브셋·재격자 후 원본 폐기, 원본 재취득 경로만 기록 (데이터 계보 lineage 테이블).

---

## J. 계정·라이선스 액션 체크리스트 (사용자 조치 필요)

코드 작성 전 아래 무료 계정 발급이 선행되어야 한다 (모두 무료, 승인 대기 있음):

- [ ] **Copernicus Marine** (data.marine.copernicus.eu) — B2, C1–C3, E1, E4에 필수
- [ ] **ECMWF CDS** (cds.climate.copernicus.eu) — ERA5 (A3)
- [ ] **AVISO+** — FES2022 (G1) 라이선스 동의 + 승인 대기, (선택) SWOT L3
- [ ] **OSU TPXO** — TPXO9-atlas (G2), 학술용 등록
- [ ] **기상청 API허브** (apihub.kma.go.kr) — API 키 발급 (D3)
- [ ] **KHOA 바다누리** — 회원가입 후 API 키 자동 발급 (D4)
- [ ] (선택) **공공데이터포털** (data.go.kr) — KMA 보조 API

인증 불필요로 즉시 사용 가능한 것: NOMADS(A1, B1), ECMWF Open Data(A2), NDBC(D1), CDIP(D2), CO-OPS(D5), IOC(D6), HFRnet(D8), Himawari AWS(E5), GEBCO(F1), OSM(F3), IFREMER WW3(B3).

**라이선스 요약:** 전 소스가 연구 목적 무료. 서비스 공개·상업화 시 유의점 2건 — TPXO(비상업 한정 → FES2022를 주 소스로), CMEMS(출처 표기 + 재배포 조건 확인).

---

## K. 본 카탈로그가 Phase 2에 부과하는 설계 제약

1. **어댑터 격리:** 소스 7개 계열(GRIB filter, ecmwf-opendata, copernicusmarine, HTTP 텍스트, REST+키, THREDDS/OPeNDAP, S3)이 서로 다른 프로토콜 — 소스별 어댑터 + 공통 인터페이스(`fetch(cycle) → 표준화 xarray`)로 설계
2. **호출 한도 관리:** KHOA 일 20,000회, NOMADS 속도 제한 — 어댑터에 rate limiter·증분 커서 내장
3. **이벤트 구동 수집:** Himawari SNS 알림처럼 push 채널이 있는 소스는 폴링 대신 구독 구조 고려
4. **QC 계층 필수:** NDBC/KMA 부이는 결측·스파이크 빈번 — 수집 직후 QC 플래그 부여 (범위·스파이크·정체 검사, GTSPP 기준 참조)
5. **데이터 계보:** 모든 산출물에 원본 소스·취득시각·버전 기록 (forecast_run.metrics와 동일하게 JSONB)
