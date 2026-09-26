# Project Poseidon — Phase 6: 2D 지역 파랑 모델 + 실데이터 결합 (M6)

**문서 버전:** 1.0 · **작성일:** 2026-08-04 · **상태:** **M6 핵심 실증 완료** — 실제 GFS 사이클로 지역 예보 생산
**선행:** [PHASE5_WAVE_ENGINE.md](PHASE5_WAVE_ENGINE.md)

---

## 1. 과학적 설명

Phase 5의 소스항(보정 유지)에 **2D 구면 전파와 유한수심 물리**를 결합해 지역 파랑 모델
(`RegionalWaveModel`)을 완성하고, Phase 3 데이터 레이크의 **실제 GFS 강제장**으로 첫 예보를
생산했다.

| 구성 요소 | 정식화 |
|---|---|
| 지리 전파 | 유한수심 군속도, 보존형 도너셀 상향, 구면 메트릭 cos φ |
| 방향 전파 | 수심 굴절 θ̇=(σ/sinh 2kh)(sinθ·∂h/∂x−cosθ·∂h/∂y) + 대권 전환 −(c_g/R)cosθ·tanφ |
| 해저마찰 | JONSWAP (Hasselmann 1973), C_b=0.038 m²s⁻³ |
| 수심 쇄파 | Battjes & Janssen (1978), γ=0.73, Q_b 고정점 반복 |
| 천해 DIA | WAM 배율 R(k̃h) (WAMDI 1988), 상한 5 |
| σ축 | 이동 없음 — 정상 수심·무해류에서 σ는 파선 보존 (해류 결합 시 k̇ 도입) |
| 경계 | 면(W/E/S/N) 선택형 링 클램프, 육지 흡수 |
| 조석 | **조화분석 모듈** (Schureman 각속도, 최소제곱, Rayleigh 기준 강제) |

### 구현 중 발견·해결 (검증 우선 개발 기록)
1. **경계 클램프 오염:** 4면 일괄 클램프가 Snell 실험에서 얕은 남쪽 경계에 심해 스펙트럼을
   주입 → 면 선택형 경계(`sides="W"`)로 해결. 운영 중첩(nesting)에도 동일 옵션 사용.
2. **θ-CFL 붕괴:** 대륙붕단 급경사에서 굴절 속도가 지리 dt를 23 s로 강제(스텝 27배 증가)
   → WW3 관행대로 **θ 이류만 서브사이클링**, 지리 dt 유지. 0.25° 운영 격자는 수심 하한 10 m
   (쇄파대 미해상 격자의 표준 처리).
3. **NCEI NCSS 서버 버그:** 정수 경계값 요청 시 부동소수점 검증 실패(52→52.000000000000014)
   — 반 셀 안쪽 요청으로 회피, 스크립트에 주석 기록.
4. **EOT20 보류 결정:** 2.33 GB 다운로드가 현 디스크에 부담 → **검조소 조화분석**으로 대체
   (아래 §5 성적). EOT20은 외해 조석·전지구 확장 시점에 도입 (fetch_static.py에 준비됨).

## 2. 참고문헌 (Phase 6 추가)

- Battjes, J.A., Janssen, J.P.F.M. (1978). *Proc. 16th ICCE*, 569–587.
- Hasselmann, K. et al. (1973). JONSWAP. *Dtsch. Hydrogr. Z.*, A8(12). (해저마찰 Γ)
- Schureman, P. (1958). *Manual of Harmonic Analysis and Prediction of Tides.* US C&GS SP-98.
- Pugh, D., Woodworth, P. (2014). *Sea-Level Science.* Cambridge Univ. Press.
- NOAA NCEI (2022). *ETOPO 2022 15/30/60 Arc-Second Global Relief Model.*

## 3–4. 구조 (신규)

```
poseidon/physics/tides.py                    # 조화분석·합성 (HarmonicTide)
poseidon/engines/spectral_wave/regional.py   # RegionalWaveModel (2D 구면)
poseidon/scheduler/wave_cycle.py             # L1 예보 사이클 (카탈로그→모델→forecast 컬렉션)
scripts/fetch_sample_data/fetch_bathy.py     # ETOPO 2022 동아시아 서브셋 (30 MB, 무인증)
tests/scientific/test_wave_2d.py             # 전파·굴절·쇄파·마찰 4종
tests/unit/test_tides.py                     # 조화분석 3종
```

## 5. 마일스톤 — 실측 성적

### 물리 검증 (신규 4+3종, 전체 테스트 41/41)
| 검증 | 기준 | 실측 |
|---|---|---|
| 심해 스웰 24 h 전파 (군속도) | ±10% | ✅ |
| 〃 에너지 보존 (구면 메트릭) | <2% | ✅ |
| Snell 굴절 (h=10 m, 6 m) | ±5° | ✅ |
| B-J 쇄파 Hs ≤ γh | 상한 +15% 내 | ✅ |
| 해저마찰 지수 감쇠율 | 해석값 ±5% | ✅ |
| 조화분석 상수 복원 (합성 신호) | 10⁻⁶ | ✅ |

### 조석 — 부산 검조소 실증 (IOC 실측 36일)
- 30일 적합 → **5일 홀드아웃 예측 RMSE 3.9 cm** (조차 1.15 m의 3.4%)
- M2 진폭 0.343 m 등 8분조 추정, Rayleigh 기준 코드 강제

### M6 — 실데이터 지역 파랑 예보 (사이클 20260803T06, 태풍 상황)
- 도메인: 120–148°E, 20–46°N, 0.25° (105×113), 수심 ETOPO 2022
- 강제: 수집된 GFS 바람(3 h 간격), 경계·초기: GFS-Wave 적분 파라미터 재구성
- 6 h 예보 vs GFS-Wave 자체 6 h (해양 8,923셀):
  **상관 0.982 · 편향 +0.02 m · RMSE 0.44 m** · 태풍 핵 Hs 17.1 m (참조 15.0 m)
- 실행: dt=619 s, 35 스텝, **~4.7분** (NumPy 단일 코어)

**판정:** 파이프라인→엔진→예보 사슬 연결 실증 완료. RMSE 0.44 m는 태풍 급경사역 포함
0.25° 무보정 첫 실행으로서 합리적 수준 — 72 h 전 리드 사이클·검증 통계 자동화(M6 완결)와
AI 보정(M7)은 Phase 7에서 완성한다.

## 6–7. 계획·수학 — §1 표 및 PHASE2 §7.1 참조

## 8–9. API·DB — forecast 컬렉션에 산출물 등록 (dataset lineage에 검증 지표 포함)

## 10. 테스트 — 신규 7종 포함 41/41, 과학 테스트 nightly

## 11. 성능

6 h/0.25° = 4.7분 → 72 h 환산 ~56분 (0.25°). PHASE2 목표(0.05°, 10분)까지 필요 가속 ~7×(해상도 환산 시 ~150×) — Phase 9 JAX/GPU 포팅 필요성 재확인. 스텝 비용의 ~40%는 θ 서브사이클(급경사 셀) — GPU에서 자연 해소.

## 12. 리스크

| 리스크 | 대응 |
|---|---|
| 태풍 핵 Hs 과대(+2 m) — 고풍속 Cd 외삽 | Wu(1982)는 허리케인 영역에서 과대 경향(문헌 기지) — Cd 상한(포화) 도입을 Phase 7 백로그로 |
| DIRPW 방향 규약(from/toward) 오독 가능성 | corr 0.98이 방증하나, NDBC 파향 관측과 교차 확인을 Phase 7 검증 파이프라인에 포함 |
| 경계 1시간 갱신의 계단 효과 | 스텝별 선형 보간으로 개선 (소규모) |

## 13. 다음 단계: Phase 7 — Prediction Engine (M6 완결 + M7)

(a) 72 h 전 리드 사이클을 상태기계에 편입(RUNNING_L1→PUBLISHED), 매 사이클 자동 실행
(b) 검증 파이프라인: 예보-관측(NDBC·IOC) 콜로케이션 → error_sample 적재 → 리드별 RMSE/bias
(c) **AI 보정(M7):** error_sample 기반 잔차 학습(leave-buoy-out), 예측구간, corrected 플래그
(d) SWE 해일 결합: 조화 조석 경계 + GFS 기압·바람 → 부산 해일 성분 검증
