# Project Poseidon — Phase 4: Physics Engine — 천수방정식 (SWE)

**문서 버전:** 1.0 · **작성일:** 2026-08-04 · **상태:** **M4 게이트 통과** (해석해 검증 완료)
**선행:** [PHASE2_ARCHITECTURE.md](PHASE2_ARCHITECTURE.md) §7.2, [PHASE3_DATA_PIPELINE.md](PHASE3_DATA_PIPELINE.md)

---

## 1. 과학적 설명

조석·폭풍해일·쓰나미(L2 계층)의 지배 방정식인 2D 천수방정식을 보존형으로 푸는
유한체적 엔진의 NumPy 참조 구현(ADR-001의 1단계). 핵심 난제와 채택 해법:

| 난제 | 해법 (문헌) |
|---|---|
| 정지 상태 보존 (C-property) — 경사 지형에서 가짜 유속 발생 금지 | **정수압 재구성** (Audusse et al. 2004): w·h 개별 MUSCL 재구성, z±=w±−h±, h*=max(0, w±−max(z_L,z_R)) + 셀별 압력 보정 — 습윤/건조 전선 포함 완전 균형 증명 |
| 리만 솔버 없는 견고한 플럭스 | **central-upwind** (Kurganov 계열): 국소 파속 a±만 필요 |
| 이동 습윤/건조 경계 (범람·간석지) | 별표 상태 자동 건조화 + **속도 탈특이화** (KP07 식 2.17, ε=h_dry⁴) |
| 박수층 마찰 강성 | 반음해 적분 u/(1+ΔtC_f|u|/h) — 무조건 안정 |
| 관성진동 에너지 드리프트 | 코리올리 **정확 회전 적분** (속력 기계정밀도 보존) |
| 충격파(단파) 양·비진동 포착 | 일반화 minmod(θ=1.3) MUSCL + SSP-RK2 |

**구현 과정의 과학적 기록:** 최초 구현은 KP07의 양수성 기울기 보정을 사용했으나,
부분 침수 셀(섬 해안선)에서 정지호수 균형이 깨지는 현상을 검증 테스트가 즉시 포착했다
(수면 오차 1.6×10⁻²). 이는 KP07의 알려진 한계로, Audusse 정수압 재구성으로 교체하여
해소했다(오차 <10⁻¹⁰). — 검증 우선 개발이 작동한 사례.

## 2. 연구 참고문헌 (Phase 4 추가)

- Audusse, E., Bouchut, F., Bristeau, M.-O., Klein, R., Perthame, B. (2004). "A Fast and Stable Well-Balanced Scheme with Hydrostatic Reconstruction for Shallow Water Flows." *SIAM J. Sci. Comput.*, 25(6), 2050–2065.
- Kurganov, A., Petrova, G. (2007). *Commun. Math. Sci.*, 5(1), 133–160. (central-upwind, 탈특이화)
- Gottlieb, S., Shu, C.-W., Tadmor, E. (2001). *SIAM Review*, 43, 89–112. (SSP-RK2)
- Stoker, J.J. (1957). *Water Waves.* Interscience. (댐붕괴 해석해)
- Thacker, W.C. (1981). "Some exact solutions to the nonlinear shallow-water wave equations." *J. Fluid Mech.*, 107, 499–508.
- Bollermann, A., Chen, G., Kurganov, A., Noelle, S. (2013). *J. Sci. Comput.*, 56, 267–290. (KP07 습윤/건조 한계의 문헌 근거)

## 3–4. 아키텍처·구조

```
poseidon/physics/constants.py            # G_STANDARD, Ω_earth, coriolis_f(위도)
poseidon/engines/shallow_water/
  ops.py        # minmod3, slopes, 탈특이화, 코리올리 회전, 반음해 마찰 (순수 함수)
  solver.py     # SWESolver(step/run), SWEState(w,hu,hv,t) — Engine 프로토콜 정합
  analytic.py   # Stoker·Thacker 해석해 (검증 기준값)
tests/unit/test_swe_ops.py               # 연산 단위 검증 5종
tests/scientific/test_swe_analytic.py    # M4 게이트 4종
```
경계조건: wall(경면 반사)·open(구배 0). 시간간격: CFL 자동 (기본 0.2).

## 5. 마일스톤 — **M4 통과 (실측값)**

| 게이트 | 기준 | 실측 |
|---|---|---|
| 정지호수 (섬+둔덕 지형, 200스텝) | 수면 오차 <10⁻¹⁰ | **<10⁻¹⁰** (내부 ~10⁻¹⁶) ✅ |
| 〃 질량 보존 | <10⁻¹³ | ✅ |
| Stoker 댐붕괴 nx=400, t=0.7 s | 평균 L1 <0.01 m | **0.00112 m** ✅ |
| 〃 수렴차수 (100→200→400) | 오차 감소 | **0.94, 1.11** (충격파 문제 기대치 ~1) ✅ |
| 〃 충격파 위치 | ±3% | ✅ |
| Thacker 1주기 (100², 습윤/건조) | 상대 L1 <5% | **0.20%** ✅ |
| 〃 질량 보존 | <10⁻¹⁰ | **2.1×10⁻¹⁶** ✅ |
| 〃 양수성 | h ≥ 0 | ✅ |
| 단위 5종 (minmod·탈특이화·코리올리·마찰) | 전부 | ✅ (전체 22/22) |

## 6. 구현 계획 (다음 연결점)

Phase 5–6에서 이 엔진에 연결할 항목 (인터페이스는 이미 존재):
조석 개방경계(EOT20 조화합성 → `bc="open"` 확장 Flather 조건), 바람응력·기압 강제항,
GEBCO 수심 → b_corner 로더, JAX 포팅(성능 필요 시점에).

## 7. 수학적 모델

PHASE2 §7.2와 동일. 이산화 요약: 재구성 w,h,hu,hv → z±=w±−h± → h*=max(0,w±−max(z_L,z_R))
→ central-upwind 플럭스 + 셀별 압력 보정 g/2(h±²−h*²) + 중심 소스 g·(h_E+h_W)/2·(z_W−z_E)/Δx
→ SSP-RK2 → (코리올리 회전 → 반음해 마찰) 분할.

## 8–9. API·DB — 해당 없음 (엔진 내부 단계)

## 10. 테스트 전략

과학 테스트가 회귀 게이트로 CI nightly에 편입(`-m scientific`). 새 기능(조석 경계 등)은
반드시 해당 해석해/기준 사례 추가 후 병합.

## 11. 성능 (NumPy 참조 구현 실측)

Thacker 100×100, 1주기(1,346 s 시뮬레이션, ~800 스텝×RK2): **5.4 s** (M1 Mac, 단일 코어).
→ 실시간 대비 ~250×. 목표(PHASE2 §11: 500 m 해일 도메인 100×실시간)는 JAX/GPU 포팅으로 달성 예정 —
참조 구현으로는 충분한 속도이며 최적화는 Phase 9 이전 금지(정확성 우선).

## 12. 리스크

| 리스크 | 대응 |
|---|---|
| open 경계의 반사 (조석 강제 시 오염) | Phase 6에서 Flather 방사 조건 구현 + 단순 파 전파 테스트 추가 |
| 참조 구현·JAX 구현 결과 발산 | PHASE2 §10의 NumPy↔JAX 일치 테스트로 게이트 |
| 대격자(1000²+)에서 NumPy 속도 한계 | Phase 9 JAX 포팅. 현 단계 문제없음 |

## 13. 다음 단계: Phase 5 — Ocean Engine (스펙트럼 파랑)

파작용 평형방정식 엔진(PHASE2 §7.1): 구면 전파(1차 상향) + 스펙트럼 공간 굴절·천수화
+ ST4 계열 소스항 + DIA. **게이트 M5 = 페치제한 성장곡선의 JONSWAP ±10% 재현**
+ 심수 단색파 전파·Snell 굴절 검증. 가장 난도 높은 단계이므로 소스항별 단위 검증을 세분화한다.
