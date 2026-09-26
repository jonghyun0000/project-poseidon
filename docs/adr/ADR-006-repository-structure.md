# ADR-006: 저장소 구조 — G-1 골격 병합·단일화

**상태:** 승인 대기 · **날짜:** 2026-08-04 · **단계:** Phase 2

## 맥락
Phase 1 구조안(§4)과 외부 생성 골격 G-1(`G-1/poseidon/`, 빈 디렉토리 79개)이 병존했다.
사용자 결정: "살릴 것만 살려서 새로운 업그레이드 버전 하나로만 간다."

## G-1 처분 결정표
| G-1 요소 | 처분 | 근거 |
|---|---|---|
| `services/ingestion/quality_control` | **채택** → `poseidon/ingest/qc/` | QC를 독립 모듈로 — 카탈로그 §K-4와 일치 |
| `validation/{metrics_engine,pipelines,reports}` | **채택** → `poseidon/validation/` + `reports/` | 검증의 1급 승격, M10 직결 |
| `notebooks/{data_exploration,numerical_model_eval,ai_surrogate_prototypes}` | **채택** (동일 이름) | 탐색/프로토타입의 표준 분리 |
| `web/src/{api,components,layers,pages,shaders}` | **채택** (동일 구조) | ADR-005와 정합 |
| `scripts/{fetch_sample_data,setup_env}` | **채택** → `scripts/` | 유용한 유틸 구획 |
| `tests/{unit,integration,scientific,chaos}` | **부분 채택** — chaos → Phase 9+ | 과학 테스트 구획 유지, chaos는 시기상조 |
| `services/*` 마이크로서비스 분할 | **기각** — 단일 패키지 `poseidon/` 내 모듈로 | 단일 노드 모놀리스가 현 단계 정답, 모듈 경계는 유지하므로 추후 분리 가능 |
| `infrastructure/{terraform,helm,argocd}` | **기각** (Phase 11 재도입) | 코드 0줄 시점의 K8s 골격은 부채 |
| `ingestion/{kafka_producers,airflow_dags}` | **기각** | ADR-004 결정(자체 상태기계) |
| `physics_engine/{swan_core,roms_core,python_bindings}` | **기각** | ADR-001 결정(Fortran 미내장) |
| `weather_engine/wrf_subsystem`, `coupler_esmf` | **기각** | 자체 대기모델은 범위 외 (Phase 1 §0) |
| `prediction_engine/{ai_surrogates,error_correction,training_pipeline,inference_server,data_assimilation}` | **개념 채택** → `poseidon/ai/`·`poseidon/assimilation/` | 이름만 정리해 흡수 |

## 확정 구조 (저장소 루트 = 현 프로젝트 폴더)
```
├── docs/                        # 단일 문서 소스 (adr/, api/, db/ 포함)
├── poseidon/                    # 단일 Python 패키지 (모놀리스, 모듈 경계 엄격)
│   ├── core/                    # 공통 타입·Cycle·BBox·설정·카탈로그 클라이언트
│   ├── ingest/                  #   adapters/ · qc/ · sync.py
│   ├── datalake/                #   Zarr/Parquet 입출력, lineage
│   ├── physics/                 #   grid, 분산관계, TEOS-10, 조석퍼텐셜, 상수
│   ├── engines/                 #   spectral_wave/ · shallow_water/ · circulation/(후기)
│   ├── assimilation/            #   EnKF, 관측 연산자
│   ├── ai/                      #   correction/ · surrogates/ · training/
│   ├── twin/                    #   상태 관리, 계층(L0–L2) 블렌딩
│   ├── scheduler/               #   ADR-004 상태기계
│   ├── api/                     #   FastAPI 앱, 타일 서비스
│   ├── alerts/                  #   임계값·경보 규칙
│   └── validation/              #   metrics/ · pipelines/
├── web/                         # ADR-005 프런트엔드
├── tests/                       # unit/ scientific/ integration/ benchmark/ regression/
├── notebooks/                   # G-1 3분류 채택
├── scripts/                     # fetch_sample_data/ setup_env/
├── data/                        # gitignore (zarr/ parquet/ raw/ static/)
├── reports/                     # 검증 보고서 산출물 (M10)
├── deploy/                      # docker/ 만. K8s·IaC는 Phase 11
└── .github/workflows/           # CI
```

## 결과
- `G-1/` 디렉토리는 채택분 반영 후 **삭제** (빈 골격, 파일 0개 확인 후).
- 각 최상위 모듈에 한 단락 README를 두어 빈 디렉토리의 git 커밋 문제를 해소하고 경계 정의를 문서화.
