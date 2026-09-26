# Project Poseidon — Phase 1: Research (연구 단계)

**문서 버전:** 1.1
**작성일:** 2026-08-04
**상태:** Phase 1 산출물 (코드 작성 이전 단계)
**다음 단계:** Phase 2 — Architecture
**부속서:** [PHASE1_DATA_CATALOG.md](PHASE1_DATA_CATALOG.md) — 웹 조사로 검증한 데이터 소스 카탈로그 (접근 경로·형식·지연·라이선스·우선순위)

---

## 0. 과학적 정직성 선언 (Scientific Honesty Statement)

본 프로젝트를 시작하기 전에, 국제 연구소 수준의 정직성으로 현실을 명시한다.

- ECMWF, NOAA(GFS/WW3), Copernicus Marine(NEMO 기반)의 전지구 운영 예보 시스템은
  **수만 코어급 슈퍼컴퓨터**에서 돌아간다. 단일 워크스테이션으로 이를 그대로 재현하는 것은 불가능하다.
- 그러나 2023년 이후 AI 기상 모델(GraphCast, Pangu-Weather, FourCastNet, AIFS)이 증명했듯,
  **학습된 신경망 대리모델(surrogate)은 단일 GPU에서 전지구 예측을 수 초 만에 수행**할 수 있다.
- 따라서 Poseidon의 과학적으로 타당한 전략은:
  1. **전지구(coarse):** 공개 운영예보(GFS-Wave, ECMWF Open Data, CMEMS)를 데이터 동화의 배경장(background)으로 소비
  2. **지역(fine):** 자체 스펙트럼 파랑 모델 + 천수방정식 엔진을 GPU에서 구동 (관심 해역 중심, 예: 한반도 주변해)
  3. **AI 계층:** 물리 모델의 오차를 관측(부이, 위성 고도계)으로 학습하여 보정 — 이것이 "스스로 개선되는 예측 엔진"의 실체
- 1 m² 해상도 전지구 시뮬레이션은 물리적으로 불가능하다(격자 수 ~3.6×10¹⁴).
  줌 레벨별로 **모델 계층을 전환**(전지구 스펙트럼 → 지역 위상평균 → 연안 위상분해 Boussinesq → 시각화용 스펙트럼 합성 해수면)하는 것이
  실제 디지털 트윈(예: ECMWF Destination Earth)이 쓰는 방법이며, 우리도 이를 따른다.

이 원칙 위에서, 아래의 모든 내용은 동료심사(peer-reviewed) 문헌에 근거한다.

---

## 1. 과학적 설명 (Scientific Explanation)

### 1.1 해양 상태의 물리적 구성

해양 표면 상태는 시간 규모가 다른 여러 물리 과정의 중첩이다:

| 과정 | 주기/시간규모 | 지배 방정식 | 대표 모델 |
|---|---|---|---|
| 풍파 (wind sea) | 1–10 s | 파작용 평형방정식 (WAE) | WAVEWATCH III, SWAN |
| 너울 (swell) | 10–25 s | WAE (비국지 전파) | WW3 전지구 |
| 조석 (tides) | 12.42 h (M2) 등 | 조화상수 + 천수방정식 | TPXO, FES2014 |
| 폭풍해일 (storm surge) | 시간~일 | 2D 천수방정식 + 기압/바람 강제 | ADCIRC, SCHISM |
| 해류 (currents) | 일~계절 | 원시방정식 (primitive eq.) | NEMO, HYCOM, ROMS, MITgcm |
| 쓰나미 | 10분~시간 | 천수/Boussinesq | MOST, FUNWAVE |
| 내부파/성층 | 시간~일 | 비정수압 Navier–Stokes | MITgcm (nonhydrostatic) |

**핵심 통찰:** 이들은 서로 다른 수치 모델을 요구하며, 단일 방정식계로 통합할 수 없다.
운영 기관들도 파랑 모델(WW3) + 해양순환 모델(NEMO/HYCOM) + 대기 모델(IFS/GFS) + 조석 모델을
**결합(coupling)** 하여 운영한다. Poseidon도 모듈형 다중 모델 아키텍처를 채택한다.

### 1.2 스펙트럼 파랑 모델링 (핵심 엔진)

풍파·너울 예측의 표준은 **3세대 스펙트럼 파랑 모델**이다. 해수면을 개별 파도가 아닌
파작용밀도 스펙트럼 N(k, θ; x, t)로 기술하고, 다음 파작용 평형방정식(Wave Action Equation)을 푼다:

```
∂N/∂t + ∇ₓ·(ẋN) + ∂/∂k(k̇N) + ∂/∂θ(θ̇N) = S_tot / σ

S_tot = S_in + S_nl + S_ds + S_bot + S_db + S_tr
```

- **S_in** — 바람 입력 (Janssen 1991; Ardhuin et al. 2010 ST4)
- **S_nl** — 4파 비선형 상호작용 (Hasselmann 1962; DIA 근사, Hasselmann et al. 1985)
- **S_ds** — 백파(whitecapping) 소산 (Ardhuin et al. 2010)
- **S_bot** — 해저 마찰 (JONSWAP, Hasselmann et al. 1973)
- **S_db** — 수심 유도 쇄파 (Battjes & Janssen 1978)
- **S_tr** — 3파 상호작용 (천해, Eldeberky 1996)

이것이 WAVEWATCH III(NOAA)와 SWAN(TU Delft)의 공통 물리다. 전자는 외해·전지구,
후자는 연안 고해상도에 최적화되어 있으며, 우리는 두 개념을 각각 전지구/연안 계층에 적용한다.

### 1.3 조석

조석은 예측 가능성이 가장 높은 성분이다. 전지구 조석은 위성 고도계로 역산된
조화상수 데이터베이스(TPXO9-atlas, FES2014)로 임의 지점·시각의 조위를 mm~cm 정확도로 계산 가능하다.
항만 공진, 연안 증폭은 조화상수를 경계조건으로 하는 지역 천수방정식 모델로 해석한다.
달·태양 중력은 평형조석 퍼텐셜(Doodson 전개)로 천수방정식의 체적력 항에 들어간다.

### 1.4 해양 순환·성층

수온/염분/밀도/해류의 3차원 구조는 정수압 원시방정식(Boussinesq 근사 하 Navier–Stokes + 상태방정식 TEOS-10)으로 기술된다.
초기 단계에서는 Copernicus Marine의 GLORYS/전지구 분석장을 소비하고,
Phase 5 이후 ROMS 개념(지형추종 s-좌표, split-explicit 시간적분)의 지역 순환 엔진을 구현한다.

### 1.5 데이터 동화와 AI 보정

- **관측:** NDBC/KMA 부이(유의파고 Hs, 주기 Tp, 파향), Jason-3/Sentinel-6/SWOT 고도계(Hs, SSH), ASCAT 산란계(해상풍), ARGO(수온·염분 프로파일)
- **동화:** 앙상블 칼만필터(EnKF, Evensen 1994/2003) → 이후 4DVAR 개념 도입
- **AI 오차 학습:** 물리 모델 예측 − 관측 잔차를 입력(바람장, 수심, 예측 스펙트럼 특성)에 대해 회귀 학습.
  운영기관에서도 검증된 접근(모델 출력 통계 MOS의 신경망 일반화)이며,
  최신 연구는 FNO/GNN 기반 전 해역 파랑 대리모델(예: 2023–2025 WW3 에뮬레이터 연구)이
  물리 모델 대비 10³–10⁴배 빠른 추론을 보임을 확인했다.

### 1.6 시각화 계층의 과학

전시구 3D 지구(CesiumJS/deck.gl) 위에 스펙트럼 모델 출력(Hs, Tp, 방향 스펙트럼)을 렌더링하고,
근접 줌에서는 방향 스펙트럼으로부터 **선형 파 성분 합성**(random-phase superposition; Tessendorf 2001의 해양 스펙트럼 렌더링 기법을
모델 스펙트럼으로 구동)으로 사실적 해수면·거품·백파를 WebGPU 셰이더로 생성한다.
즉 "1 m² 줌"은 **물리 모델 스펙트럼과 통계적으로 일치하는 시각적 실현(realization)**이며, 이는 과학적으로 정직한 표현 방식이다.

---

## 2. 연구 참고문헌 (Research References)

### 파랑 모델
1. The WAMDI Group (1988). "The WAM Model — A Third Generation Ocean Wave Prediction Model." *J. Phys. Oceanogr.*, 18, 1775–1810.
2. Booij, N., Ris, R.C., Holthuijsen, L.H. (1999). "A third-generation wave model for coastal regions: 1. Model description and validation." *JGR Oceans*, 104(C4), 7649–7666. (SWAN)
3. Tolman, H.L. (1991). "A Third-Generation Model for Wind Waves on Slowly Varying, Unsteady, and Inhomogeneous Depths and Currents." *J. Phys. Oceanogr.*, 21, 782–797. (WAVEWATCH 기초)
4. The WAVEWATCH III Development Group (2019). *User Manual and System Documentation of WAVEWATCH III version 6.07.* NOAA/NCEP Tech. Note 333.
5. Ardhuin, F. et al. (2010). "Semiempirical Dissipation Source Functions for Ocean Waves. Part I." *J. Phys. Oceanogr.*, 40, 1917–1941. (ST4 물리)
6. Hasselmann, K. et al. (1973). "Measurements of wind-wave growth and swell decay during the Joint North Sea Wave Project (JONSWAP)." *Dtsch. Hydrogr. Z.*, A8(12).
7. Hasselmann, S. et al. (1985). "Computations and Parameterizations of the Nonlinear Energy Transfer in a Gravity-Wave Spectrum. Part II: DIA." *J. Phys. Oceanogr.*, 15, 1378–1391.
8. Battjes, J.A., Janssen, J.P.F.M. (1978). "Energy loss and set-up due to breaking of random waves." *Proc. 16th ICCE*, 569–587.
9. Komen, G.J. et al. (1994). *Dynamics and Modelling of Ocean Waves.* Cambridge Univ. Press.
10. Zieger, S. et al. (2015). "Observation-based source terms in the third-generation wave model WAVEWATCH." *Ocean Modelling*, 96, 2–25. (ST6)

### 해양 순환
11. Shchepetkin, A.F., McWilliams, J.C. (2005). "The regional oceanic modeling system (ROMS)." *Ocean Modelling*, 9, 347–404.
12. Marshall, J. et al. (1997). "A finite-volume, incompressible Navier Stokes model for studies of the ocean on parallel computers." *JGR*, 102(C3), 5753–5766. (MITgcm)
13. Chassignet, E.P. et al. (2007). "The HYCOM (HYbrid Coordinate Ocean Model) data assimilative system." *J. Mar. Syst.*, 65, 60–83.
14. Madec, G. et al. (2019). *NEMO Ocean Engine.* Scientific Notes of Climate Modelling Center, IPSL.
15. IOC, SCOR, IAPSO (2010). *TEOS-10: The international thermodynamic equation of seawater.* UNESCO.

### 조석·해일·쓰나미
16. Egbert, G.D., Erofeeva, S.Y. (2002). "Efficient Inverse Modeling of Barotropic Ocean Tides." *J. Atmos. Oceanic Technol.*, 19, 183–204. (TPXO)
17. Lyard, F. et al. (2021). "FES2014 global ocean tide atlas: design and performance." *Ocean Sci.*, 17, 615–649.
18. Luettich, R.A., Westerink, J.J. (2004). *Formulation and Numerical Implementation of the 2D/3D ADCIRC Finite Element Model.*
19. Zhang, Y.J. et al. (2016). "Seamless cross-scale modeling with SCHISM." *Ocean Modelling*, 102, 64–81.
20. Titov, V.V., Synolakis, C.E. (1998). "Numerical Modeling of Tidal Wave Runup." *J. Waterw. Port Coast. Ocean Eng.*, 124(4). (MOST)
21. Shi, F. et al. (2012). "A high-order adaptive time-stepping TVD solver for Boussinesq modeling of breaking waves and coastal inundation." *Ocean Modelling*, 43–44, 36–51. (FUNWAVE-TVD)

### 수치기법
22. LeVeque, R.J. (2002). *Finite Volume Methods for Hyperbolic Problems.* Cambridge Univ. Press.
23. Audusse, E. et al. (2004). "A Fast and Stable Well-Balanced Scheme with Hydrostatic Reconstruction for Shallow Water Flows." *SIAM J. Sci. Comput.*, 25(6), 2050–2065.
24. Kurganov, A., Petrova, G. (2007). "A second-order well-balanced positivity preserving central-upwind scheme for the Saint-Venant system." *Commun. Math. Sci.*, 5(1), 133–160.
25. Berger, M.J., Oliger, J. (1984). "Adaptive mesh refinement for hyperbolic partial differential equations." *J. Comput. Phys.*, 53, 484–512.

### AI·데이터 동화
26. Evensen, G. (2003). "The Ensemble Kalman Filter: theoretical formulation and practical implementation." *Ocean Dynamics*, 53, 343–367.
27. Carrassi, A. et al. (2018). "Data assimilation in the geosciences: An overview." *WIREs Climate Change*, 9(5), e535.
28. Li, Z. et al. (2021). "Fourier Neural Operator for Parametric Partial Differential Equations." *ICLR 2021.* (FNO)
29. Raissi, M., Perdikaris, P., Karniadakis, G.E. (2019). "Physics-informed neural networks." *J. Comput. Phys.*, 378, 686–707. (PINN)
30. Pathak, J. et al. (2022). "FourCastNet: A Global Data-driven High-resolution Weather Model using Adaptive Fourier Neural Operators." *arXiv:2202.11214.*
31. Lam, R. et al. (2023). "Learning skillful medium-range global weather forecasting." *Science*, 382, 1416–1421. (GraphCast)
32. Bi, K. et al. (2023). "Accurate medium-range global weather forecasting with 3D neural networks." *Nature*, 619, 533–538. (Pangu-Weather)
33. Kochkov, D. et al. (2024). "Neural general circulation models for weather and climate." *Nature*, 632, 1060–1066. (NeuralGCM)
34. Hersbach, H. (2000). "Decomposition of the Continuous Ranked Probability Score for Ensemble Prediction Systems." *Wea. Forecasting*, 15, 559–570. (CRPS)

### 시각화
35. Tessendorf, J. (2001). "Simulating Ocean Water." *SIGGRAPH Course Notes.* (스펙트럼 기반 해수면 렌더링)

---

## 3. 아키텍처 (Preliminary Architecture — Phase 2에서 상세화)

```
┌─────────────────────────────────────────────────────────────────┐
│                        PRESENTATION LAYER                       │
│   Web Dashboard (CesiumJS + deck.gl + WebGPU ocean shader)      │
│   Time slider · Replay · Comparison · Alerts                    │
└──────────────▲──────────────────────────────▲───────────────────┘
               │ WebSocket / REST / tiles      │
┌──────────────┴──────────────────────────────┴───────────────────┐
│                          API LAYER                              │
│   FastAPI (REST/WS) · gRPC (내부) · Tile server (COG/PMTiles)   │
└──────────────▲──────────────────────────────▲───────────────────┘
               │                              │
┌──────────────┴───────────┐   ┌──────────────┴───────────────────┐
│    PREDICTION CORE       │   │        DIGITAL TWIN STATE        │
│  Spectral Wave Engine    │   │  현재 해양 상태 (분석장)          │
│  (WAE solver, GPU)       │   │  Zarr/Parquet 시계열 저장소       │
│  SWE Engine (조석/해일)  │   │  PostGIS (관측 메타데이터)        │
│  AI Correction Layer     │   └──────────────▲───────────────────┘
│  EnKF Data Assimilation  │                  │
└──────────────▲───────────┘                  │
               │                              │
┌──────────────┴──────────────────────────────┴───────────────────┐
│                     DATA PIPELINE LAYER                         │
│  Ingest: GFS/GFS-Wave · ECMWF Open Data · CMEMS · NDBC · KMA    │
│          고도계(CMEMS L3) · ASCAT · GEBCO · TPXO/FES            │
│  Validate → Clean → Regrid → Sync → Data Lake (Zarr on disk/S3) │
│  Scheduler: 예보 사이클 오케스트레이션 (00/06/12/18Z)            │
└─────────────────────────────────────────────────────────────────┘
```

**모델 계층 (줌 레벨별 전환):**

| 계층 | 공간 범위 | 해상도 | 모델 | 소스 |
|---|---|---|---|---|
| L0 전지구 | 전 해양 | 0.25° | 소비 (GFS-Wave/ECMWF/CMEMS) | 외부 운영예보 |
| L1 해역 | 예: 동아시아 | 0.05° (~5 km) | 자체 스펙트럼 파랑 엔진 (GPU) | 자체 계산 |
| L2 연안 | 항만/해변 | 50–500 m | SWAN 개념 정상상태 + SWE 해일/조석 | 자체 계산 |
| L3 시각화 | 1 m² | 연속 | 스펙트럼 → 해수면 합성 (WebGPU) | 렌더링 |

---

## 4. 폴더 구조 (제안 — Phase 2에서 확정)

```
poseidon/
├── docs/                    # 본 문서 및 각 Phase 산출물
│   ├── PHASE1_RESEARCH.md
│   ├── references/          # 논문 노트, 알고리즘 도출 노트
│   └── adr/                 # Architecture Decision Records
├── data/                    # Data Lake (gitignore)
│   ├── raw/  processed/  zarr/  static/ (GEBCO, 해안선, TPXO)
├── poseidon/                # Python 패키지 (strict typing, mypy)
│   ├── ingest/              # 01–04 수집·검증·정제·동기화
│   ├── datalake/            # 05 Zarr/Parquet 카탈로그
│   ├── physics/             # 06 공통 물리 (분산관계, TEOS-10, 조석퍼텐셜)
│   ├── engines/
│   │   ├── spectral_wave/   # 07–08 WAE 솔버 (NumPy 참조 구현 + GPU)
│   │   ├── shallow_water/   # 조석·해일·쓰나미 FVM 엔진
│   │   └── circulation/     # (Phase 5+) 지역 순환
│   ├── assimilation/        # EnKF, 관측 연산자
│   ├── ai/                  # 10–11 오차 보정, FNO 대리모델, 신뢰구간
│   ├── twin/                # 14 상태 관리, 예보 사이클
│   ├── api/                 # 15 FastAPI/gRPC/WS
│   ├── scheduler/           # 18 사이클 오케스트레이션
│   └── validation/          # 12단계 검증 지표 (RMSE, CRPS, skill)
├── web/                     # 12–13, 16 Cesium/deck.gl/WebGPU 프런트엔드
├── tests/                   # unit / integration / scientific / benchmark
├── deploy/                  # Docker, (후기) K8s
└── .github/workflows/       # CI
```

---

## 5. 마일스톤

| ID | 마일스톤 | Phase | 완료 기준 (검증 가능) |
|---|---|---|---|
| M1 | Phase 1 연구 문서 | 1 | 본 문서 승인 |
| M2 | 상세 아키텍처 + ADR | 2 | 모듈 인터페이스 정의 완료 |
| M3 | 데이터 파이프라인 가동 | 3 | GFS-Wave·NDBC·KMA·GEBCO 자동 수집, 24h 무인 운전 |
| M4 | SWE 엔진 검증 통과 | 4 | 해석해 3종(정지호수, 댐붕괴, Thacker) 수렴차수 확인 |
| M5 | 스펙트럼 파랑 엔진 검증 | 5 | 페치제한 성장곡선이 JONSWAP과 ±10% 일치 |
| M6 | 지역 예보 사이클 | 6–7 | 동아시아 5 km 파랑 72h 예보 자동 생산 |
| M7 | AI 보정 가동 | 7 | 부이 대비 Hs RMSE가 순수 물리 대비 ≥15% 개선 |
| M8 | 3D 지구 대시보드 | 8 | 전지구→연안 줌, 시간 슬라이더, 벡터장 |
| M9 | 운영 안정화 | 9–11 | 30일 연속 사이클, 테스트 커버리지 ≥80% |
| M10 | 과학적 검증 보고서 | 12 | 3개월 hindcast 통계 공개 (부이 ≥10개 지점) |

---

## 6. 구현 계획 (Phase 1 범위의 결정 사항)

Phase 1에서 확정하는 기술 선택과 그 근거:

1. **언어:** Python 3.12+ (파이프라인·API·AI) + GPU 커널은 JAX 우선
   - 근거: JAX는 XLA로 CPU/GPU 동일 코드, `jit/vmap/grad` — PINN·4DVAR의 수반(adjoint) 모델을 자동미분으로 획득 가능. 참조 구현은 NumPy로 작성해 수치 검증 후 JAX 포팅.
2. **파랑 엔진 1차 목표:** 구형좌표 WAE, 1차 상향풍(upwind) 전파 + ST4-계열 소스항, DIA 비선형
   - 근거: WW3/SWAN과 동일한 물리 계보, 문헌으로 전 항 검증 가능.
3. **SWE 엔진:** well-balanced central-upwind FVM (Kurganov–Petrova 2007), wetting/drying 지원
   - 근거: 조석·해일·쓰나미·범람을 하나의 엔진으로 해석 가능, 양성보존(positivity) 증명된 기법.
4. **조석:** TPXO9/FES2014 조화상수 소비 + 지역 SWE 경계강제. 자체 전지구 조석 역산은 범위 외.
5. **데이터 형식:** Zarr(격자 시계열), GeoParquet(관측점), PostGIS(메타데이터), COG/PMTiles(타일)
6. **1차 관심 해역:** 동아시아 (한반도 주변, 100°E–150°E, 15°N–50°N) — KMA 부이·조위관측소 검증 데이터가 풍부.
7. **예보 사이클:** 6시간 주기(00/06/12/18Z), 리드타임 5 min–7 d (14 d는 앙상블 도입 후).

---

## 7. 수학적 모델 (지배 방정식 요약)

### 7.1 파작용 평형방정식 (구형좌표)

```
∂N/∂t + (cos φ)⁻¹ ∂/∂φ (φ̇ N cos φ) + ∂/∂λ (λ̇ N) + ∂/∂k (k̇ N) + ∂/∂θ (θ̇ N) = S/σ
```

특성속도 (심수·해류 U 포함):
```
ẋ = c_g + U,   c_g = ∂σ/∂k,   σ² = g k tanh(kh)
k̇ = −∂σ/∂h (∂h/∂s) − k·∂U/∂s        (수심·해류 변화에 의한 굴절)
θ̇ = −k⁻¹ [∂σ/∂h ∂h/∂m + k·∂U/∂m]     (방향 회전)
```

### 7.2 2D 천수방정식 (보존형, 조석·해일·쓰나미)

```
∂h/∂t + ∇·(hu) = 0
∂(hu)/∂t + ∇·(hu⊗u) + g h ∇η = −g h ∇(η_EQ + η_SAL)  ← 평형조석·자기인력
              + f k̂×(hu)                                ← 코리올리 (f = 2Ω sin φ)
              + τ_s/ρ − τ_b/ρ                            ← 바람응력 · 해저마찰
              − (h/ρ) ∇p_a                               ← 역기압 (해일)
```
바람응력: τ_s = ρ_a C_d |U₁₀| U₁₀, C_d는 Wu (1982) 또는 COARE 3.5.
해저마찰: Manning 또는 Chézy 이차마찰.

### 7.3 상태방정식

밀도 ρ(T, S, p)는 TEOS-10 (IOC et al. 2010) 다항 근사(Roquet et al. 2015) 사용. 자체 유도 금지.

### 7.4 AI 보정 (정식화)

물리 예측 ŷ_phys(x, t+τ)에 대해 잔차 모델 ε_θ를 학습:
```
y_corrected = ŷ_phys + ε_θ(ŷ_phys, U₁₀, h, fetch, τ, …)
목적함수: E[ CRPS(y_corrected, y_obs) ]  (확률예측 시) 또는 MSE (결정론)
```
분포 예측(신뢰구간)은 quantile regression 또는 앙상블 스프레드 보정(EMOS)으로 확장.

---

## 8. API 사양 (초안 — Phase 2에서 OpenAPI로 확정)

```
GET  /v1/state/current?var=hs,tp,dir,u10,ssh&bbox=...&zoom=...
GET  /v1/forecast?lat=..&lon=..&vars=hs,tp,tide,surge&horizon=72h
GET  /v1/forecast/grid?var=hs&t=2026-08-05T00:00Z&bbox=...   → COG/Zarr chunk
GET  /v1/tide?lat=..&lon=..&from=..&to=..                    → 조화합성 시계열
GET  /v1/observations?type=buoy&bbox=...&from=..&to=..
GET  /v1/skill?var=hs&region=east-asia&window=30d            → 검증 통계
WS   /v1/stream?vars=...&bbox=...                            → 실시간 갱신
GET  /v1/alerts?bbox=...                                     → 고파랑·해일 경보
```
gRPC는 엔진↔트윈 상태 내부 통신 전용. GraphQL은 대시보드 요구 확정 후 도입 여부 결정.

---

## 9. 데이터베이스 스키마 (초안)

```sql
-- PostGIS: 관측 메타데이터·시계열 인덱스
CREATE TABLE station (
  station_id   TEXT PRIMARY KEY,          -- 'NDBC:46042', 'KMA:22101'
  provider     TEXT NOT NULL,
  kind         TEXT NOT NULL,             -- buoy|tide_gauge|hf_radar|argo
  geom         GEOMETRY(Point, 4326) NOT NULL,
  depth_m      DOUBLE PRECISION,
  meta         JSONB
);
CREATE TABLE observation (
  station_id   TEXT REFERENCES station,
  ts           TIMESTAMPTZ NOT NULL,
  var          TEXT NOT NULL,             -- hs|tp|dir|sst|ssh|u10|v10|slp
  value        DOUBLE PRECISION,
  qc_flag      SMALLINT NOT NULL DEFAULT 0,
  PRIMARY KEY (station_id, ts, var)
);  -- TimescaleDB hypertable 후보
CREATE TABLE forecast_run (
  run_id       UUID PRIMARY KEY,
  cycle        TIMESTAMPTZ NOT NULL,      -- 00/06/12/18Z
  engine       TEXT NOT NULL,             -- spectral_wave|swe|blend
  domain       TEXT NOT NULL,
  zarr_uri     TEXT NOT NULL,
  status       TEXT NOT NULL,
  metrics      JSONB                      -- 사이클별 검증 요약
);
```
격자 데이터 자체는 DB가 아닌 **Zarr 스토어**에 저장하고 DB는 카탈로그 역할만 한다(운영기관 표준 관행).

---

## 10. 테스트 전략

| 계층 | 내용 | 예시 |
|---|---|---|
| Unit | 순수 함수 검증 | 분산관계 solver가 `σ²=gk tanh(kh)` 잔차 <1e-12 |
| Scientific (해석해) | 수치해 ↔ 폐형해 | SWE: lake-at-rest (well-balance, 오차 ≈ 기계정밀도), 1D 댐붕괴 (Stoker 해), Thacker 진동 접시; 파랑: 단색파 심수 전파 후 굴절 (Snell 법칙) |
| Scientific (경험식) | 페치제한 성장 → JONSWAP ε–ν 관계 ±10% | |
| 수렴성 | 격자 세분화 시 관측 수렴차수 = 이론차수 | FVM 2차 기법 → 실측 차수 ≥1.8 |
| Integration | 파이프라인 E2E | GFS-Wave 사이클 수집→regrid→저장→API 응답 |
| Regression | hindcast 고정 케이스 통계 고정 | 태풍 사례 1건 Hs 필드 해시 비교 |
| Benchmark | 성능 회귀 감지 | WAE 1스텝 시간, 격자당 처리량 |
| Validation (운영) | 매 사이클 부이 대비 지표 자동 산출 | RMSE, bias, SI, CRPS → DB 저장 |

---

## 11. 성능 목표 (1차, 단일 GPU 워크스테이션 기준)

| 항목 | 목표 |
|---|---|
| L1 파랑 도메인 (동아시아 0.05°, 700×700, 32주파수×36방향) | 72h 예보를 벽시계 10분 이내 (GPU) |
| L2 연안 SWE (500 m, 1000×1000) | 실시간 대비 ≥100× 가속 |
| 데이터 수집 사이클 | 예보 자료 가용 후 15분 내 완료 |
| API p95 지연 | 점 예보 <200 ms, 타일 <500 ms |
| AI 보정 추론 | 전 도메인 <10 s |
| 검증 목표 (M10) | Hs RMSE ≤ 0.35 m (외해 부이, 24h 리드), bias |≤0.1| m — GFS-Wave 공개 성능과 동급 이상 |

---

## 12. 리스크 분석

| 리스크 | 확률 | 영향 | 대응 |
|---|---|---|---|
| 데이터 소스 API 변경·중단 (NOMADS, CMEMS 인증) | 높음 | 높음 | 소스별 어댑터 격리, 이중화 (GFS↔ECMWF Open Data), 캐시 |
| DIA 비선형 항의 구현 오류 (가장 난해한 항) | 중간 | 높음 | WW3 문서의 검증 케이스 재현, 학술 스펙트럼 성장 곡선 대조 |
| 단일 머신 자원 한계로 도메인 축소 필요 | 중간 | 중간 | 해상도·도메인 파라미터화, 다계층 설계로 우아한 축소 |
| GPU 수치 정밀도 (fp32) 로 인한 보존량 드리프트 | 중간 | 중간 | 보존량 감시 테스트, 민감 항만 fp64 |
| AI 보정 과적합 (관측 부이 희소) | 중간 | 중간 | 공간 교차검증(leave-buoy-out), 정규화, 물리 예측 폴백 |
| CMEMS/ECMWF 라이선스 준수 | 낮음 | 높음 | 재배포 조건 검토, 파생물만 공개 |
| 범위 폭주 (모든 물리 동시 구현 시도) | 높음 | 높음 | 본 문서의 계층·Phase 게이트 엄수, 각 Phase 완료 기준 통과 전 다음 단계 금지 |

---

## 13. 다음 단계: Phase 2 — Architecture

Phase 2 산출물 (본 문서의 3·4·8·9절을 확정 수준으로 상세화):

1. 모듈별 인터페이스 정의 (타입 시그니처 수준) 및 ADR 5건 이상
   - ADR-001 언어·GPU 스택 (JAX vs CuPy vs Julia 비교표)
   - ADR-002 격자 체계 (정규 위경도 vs 비정형 SMC 격자)
   - ADR-003 데이터 레이크 레이아웃 (Zarr 청크 전략)
   - ADR-004 예보 사이클 상태기계
   - ADR-005 프런트엔드 렌더링 파이프라인 (Cesium+deck.gl+WebGPU 통합)
2. 시퀀스 다이어그램 (예보 사이클, 관측 동화, 사용자 조회)
3. OpenAPI 3.1 명세 초안
4. 물리 엔진 수치 설계서 (이산화 스텐실, 시간적분, 안정조건 CFL 도출)
5. 개발 환경·CI 파이프라인 정의

**Phase 2 진입 조건:** 본 Phase 1 문서에 대한 승인 (특히 §0의 다계층 전략, §6의 기술 선택).
