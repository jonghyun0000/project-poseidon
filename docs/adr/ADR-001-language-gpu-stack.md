# ADR-001: 언어 및 GPU 수치 스택

**상태:** 승인 대기 · **날짜:** 2026-08-04 · **단계:** Phase 2

## 맥락
파랑작용 평형방정식(WAE)·천수방정식(SWE) 솔버를 자체 구현하고 GPU로 가속해야 하며,
4DVAR·PINN을 위해 수반(adjoint) 모델이 필요하다. AI 보정 계층과의 마찰 없는 통합도 요구된다.

## 검토한 대안
| 대안 | 장점 | 단점 |
|---|---|---|
| A. 기존 Fortran 모델 내장 (WW3/SWAN/ROMS 소스 빌드) | 검증된 물리, 즉시 정확도 확보 | GPU 미지원(WW3는 MPI 전용), 빌드 지옥, 자동미분 불가, 수정·학습 통합 곤란. G-1의 `swan_core/roms_core`가 이 방향이었음 |
| B. C++/CUDA 직접 구현 | 최고 성능 | 개발 속도 최악, adjoint 수동 유도, 인력 1인 프로젝트에 부적합 |
| C. Julia (Oceananigans 생태계) | 우아한 수치, GPU 지원 | 파랑 스펙트럼 모델 생태계 부재, 배포·AI 스택 연계 약함 |
| D. **Python 3.12 + NumPy 참조 구현 + JAX 가속** | XLA로 CPU/GPU/TPU 동일 코드, `grad`로 adjoint 자동 획득, AI 계층과 동일 언어·동일 배열, 검증(테스트)이 NumPy 참조와 1:1 대조 가능 | 순수 Python 대비 JAX 함수형 제약(불변 배열), 동적 wetting/drying 등에서 마스크 기법 필요 |

## 결정
**대안 D.** 모든 솔버는 (1) NumPy 참조 구현(가독성·검증용) → (2) JAX 구현(운영·GPU) 순으로 작성하고,
두 구현의 결과 일치(허용오차 내)를 CI의 과학 테스트로 강제한다.
기존 Fortran 모델은 내장하지 않고 **결과물(IFREMER WW3 후측, GFS-Wave)** 만 소비한다.

## 결과
- G-1의 `physics_engine/{swan_core,roms_core,python_bindings}`, `weather_engine/wrf_subsystem`, `coupler_esmf`는 **폐기**.
- 정밀도 정책: 상태 배열 fp32 기본, 보존량 검사·질량 플럭스 누적은 fp64 (PHASE2 §7.4).
- 의존성 고정: `numpy`, `jax[cuda|metal]`, `xarray`, `zarr` — 솔버 코어에는 이 외 의존 금지.
