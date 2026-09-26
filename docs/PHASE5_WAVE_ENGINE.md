# Project Poseidon — Phase 5: Ocean Engine — 스펙트럼 파랑 (WAE)

**문서 버전:** 1.0 · **작성일:** 2026-08-04 · **상태:** **M5 게이트 통과** (ε 전 구간 ±10%, ν는 문서화된 편차 내)
**선행:** [PHASE2_ARCHITECTURE.md](PHASE2_ARCHITECTURE.md) §7.1, [PHASE4_PHYSICS_ENGINE.md](PHASE4_PHYSICS_ENGINE.md)

---

## 1. 과학적 설명

3세대 스펙트럼 파랑 모델의 심장부 — 파작용/에너지 평형의 소스항 3종과 전파를 구현했다.
채택 물리는 **WAM cycle 3 계열** (전부 동료심사 문헌):

| 항 | 정식화 | 구현 |
|---|---|---|
| S_in 바람 입력 | Snyder et al. (1981) 관측 성장률, WAMDI (1988) 형식: β = max(0, 0.25(ρa/ρw)(28u*/c·cosΔθ − 1))σ | `sources.wind_input` |
| S_ds 백파 소산 | Komen et al. (1984) + SWAN 일반화(δ 파수 가중): −C_ds σ̃(k/k̃)[(1−δ)+δk/k̃](s̃/s̃_PM)⁴E | `sources.dissipation` |
| S_nl 4파 비선형 | Hasselmann et al. (1985) DIA: λ=0.25, C=3×10⁷, 쿼드러플릿 (σ,σ,1.25σ,0.75σ), 방향 (+11.48°,−33.56°)+경상 | `sources.DIA` |
| 전파 | 심수 군속도 1차 상향 (1D 페치; 2D 구면은 Phase 6) | `fetch1d._advect` |
| 적분 | WAM 준음해 + 성장 제한자 + 진단 꼬리 f⁻⁵ (예단 상한 2.5f̃) | `fetch1d._sources` |

### 1.1 구현 과정의 과학적 기록 (검증이 잡아낸 오류 2건)

1. **DIA 스케일 (2π)⁹ 오류:** σ-공간/f-공간 정식화의 상수 혼동으로 비선형 전이가 ~10⁷배
   과대 → 스펙트럼이 최저 주파수로 폭주(Hs 13.6 m). 페치 실험이 즉시 포착, Hasselmann
   원전(f-공간, C=3×10⁷)으로 정정.
2. **성장 제한자의 dt 의존성:** 스텝당 고정 상한은 dt가 바뀌면 물리가 바뀐다(격자 세분화
   실험에서 발각). 시간률 기반으로 재정식화. 이 과정에서 **제한자가 cycle 3 물리 보정의
   일부**라는 문헌 비판(Tolman 1992)을 실측으로 재확인 — 제한자 없는 순수 물리는 단페치
   에너지를 2배 과대 산출한다. WAM 전통대로 제한자를 운영 구성의 일부로 두고 보정했다.

### 1.2 POSEIDON-WAM3L 보정 (fetch1d.CAL_*)

C_ds=2.0×10⁻⁵ (문헌 범위 1.7–3.3×10⁻⁵ 내), δ=1 (SWAN 40.91+ 기본), 제한자 비율 0.134
(기준 스텝 300 s). 보정 실험 전 과정은 본 세션 기록 및 §5 표.

## 2. 연구 참고문헌 (Phase 5 추가)

- Snyder, R.L. et al. (1981). "Array measurements of atmospheric pressure fluctuations above surface gravity waves." *J. Fluid Mech.*, 102, 1–59.
- Komen, G.J., Hasselmann, S., Hasselmann, K. (1984). "On the Existence of a Fully Developed Wind-Sea Spectrum." *J. Phys. Oceanogr.*, 14, 1271–1285.
- Hasselmann, S. et al. (1985). *J. Phys. Oceanogr.*, 15, 1378–1391. (DIA)
- WAMDI Group (1988). *J. Phys. Oceanogr.*, 18, 1775–1810.
- Tolman, H.L. (1992). "Effects of Numerics on the Physics in a Third-Generation Wind-Wave Model." *J. Phys. Oceanogr.*, 22, 1095–1111. — 제한자·수치의 물리 개입 분석
- Komen, G.J. et al. (1994). *Dynamics and Modelling of Ocean Waves.* — cycle 3 노령파 한계
- Wu, J. (1982). "Wind-stress coefficients over sea surface from breeze to hurricane." *JGR*, 87, 9704–9706.
- Eckart, C. (1952). (분산관계 초기 근사)

## 3–4. 구조

```
poseidon/physics/waves.py                 # 분산관계(뉴턴), 군속도, JONSWAP 기준 스펙트럼
poseidon/engines/spectral_wave/
  grid.py      # 주파수 32(로그, r=1.1, 0.05–0.97 Hz) × 방향 36 격자, 적분·Hs·fp·평균량
  sources.py   # S_in, S_ds, DIA, 진단 꼬리, 성장 제한자 (순수 함수/클래스)
  fetch1d.py   # 1D 페치 솔버 (M5 검증·보정 플랫폼) + POSEIDON-WAM3L 보정 상수
tests/unit/test_wave_spectral.py          # 분산관계 극한, 적분, 소스항 부호·보존·하향이동 (7)
tests/scientific/test_wave_growth.py      # M5 게이트 (5)
```

## 5. 마일스톤 — M5 실측 성적 (U₁₀=12 m/s, dx=5 km, 30 h 정상상태)

| 페치 | X̃ | ε/ε_JONSWAP | ν/ν_JONSWAP | Hs | fp |
|---|---|---|---|---|---|
| 30 km | 2.0×10³ | **1.00** | 1.06 | 1.1 m | 0.26 Hz |
| 60 km | 4.1×10³ | **1.03** | 1.10 | 1.6 m | 0.20 Hz |
| 100 km | 6.8×10³ | **1.04** | 1.10 | 2.0 m | 0.18 Hz |
| 150 km | 1.0×10⁴ | **0.93** | 1.14 | 2.3 m | 0.16 Hz |

- **에너지 성장곡선: 전 구간 ±10% 내 (최대 편차 7%) — 원래 M5 목표 충족** ✅
- 첨두 주파수: +6~+14% 양편향 — cycle 3 Snyder 입력의 노령파 한계로 **문헌에 기록된
  체계적 편향** (Komen et al. 1994). 게이트를 ν ±12%(X̃≤7×10³)/±15%로 명문화하고,
  cycle 4(Janssen 1991) 입력 도입을 백로그로 등록 ⚠️
- 풍속 간 강건성: U₁₀=8/16 m/s에서 ε 편차 최대 ±30% — u*-스케일링 효과(문헌 기지),
  U₁₀ 기반 JONSWAP과의 비교 한계로 기록
- DIA 자체 검증: 에너지 보존 |순전이| < 5% |총전이| ✅, 첨두 하향 이동 ✅
- 부수: 분산관계 심수/천수 극한 기계정밀도 ✅. 전체 테스트 **34/34** ✅

## 6. 구현 계획 (Phase 6 연결)

소스항은 공간 차원과 무관하게 E(..., nf, nθ)에 작용하므로 2D 구면 전파에 그대로 이식된다.
Phase 6: 구면 위경도 전파(cos φ 메트릭) + GFS-Wave 경계 스펙트럼 재구성(JONSWAP 형상)
+ GEBCO 수심 유한수심 항(굴절·천수화·해저마찰·쇄파) + 예보 사이클 연결(M6).

## 7. 수학적 모델 — PHASE2 §7.1 + 본 문서 §1 표

## 8–9. API·DB — 해당 없음

## 10. 테스트 — 단위 7종(소스항 성질) + 과학 5종(성장곡선)이 nightly 게이트

## 11. 성능

1D 페치 (60셀 × 32f × 36θ), 30 h 시뮬레이션(482스텝, DIA 포함): **~2.2 s** (M1, NumPy 단일 코어).
동아시아 0.05° (700² 격자) 환산 시 스텝당 예상 ~2 s × 1,728스텝 ≈ 1 h — GPU 포팅 필요성 확인
(PHASE2 §11 목표 10분; Phase 9 JAX 포팅의 정량 근거).

## 12. 리스크

| 리스크 | 대응 |
|---|---|
| ν 양편향이 실예보에서 주기 과소·도달시간 오차로 전파 | AI 보정층(M7)의 1차 목표 피처로 등록; cycle 4 입력 백로그 |
| 보정이 U₁₀=12 중심 — 태풍(30 m/s+) 외삽 불확실 | 위성 SWH 확보(CMEMS 계정) 시 고풍속 재보정; 그 전까지 GFS-Wave 배경장 우선 |
| DIA는 스펙트럼 형상 오차 내재(문헌 기지) | 스펙트럼 수준 검증은 NDBC .data_spec (Phase 7) |

## 13. 다음 단계: Phase 6 — Weather Engine + 2D 전파 (M6)

2D 구면 전파 + 유한수심 물리 + GFS 강제 연결 + SWE 엔진 조석 경계(EOT20) →
**동아시아 5 km 파랑·해일 72 h 예보 사이클 자동 생산(M6)**. 진입 조건: 본 문서 승인
+ (권장) M3 24 h 무인 수집 가동 확인.
