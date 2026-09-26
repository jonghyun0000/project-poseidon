"""관측 동화 파라미터 — 전량 본 프로젝트 error_sample 실측에서 유도 (2026-08-24).

출처 표본: `error_sample` var='hs', land_frac<=0.05, collocation='sea-norm-v1',
태그 (advection=uno2, gse=none). 리드 0 기준 n=230, 31개소, 10사이클
(2026-08-04 ~ 2026-08-22). 관측 Hs 0.1~2.8 m, 평균 0.79 m.

**이 값들은 "여름 저파고 레짐" 조건부다.** Hs>=2 m 표본은 58건(7개소), Hs>=3 m는 1건뿐이며
고파랑 조건에서의 타당성은 **검증 불가**다 (CLAUDE.md 규칙 5).

문헌 대조
- Lionello, Gunther & Janssen (1992) JGR 97(C9), 14453 — 지구 규모 Hs OI, 지수 상관 L~1650 km
- Greenslade & Young (2004) Ocean Modelling 6, 97 — SOAR (1+ar)e^{-ar}, L 300~750 km,
  sigma_b 0.45~0.89 m (전지구 외해 · 자유 12 h 예보 배경)
- ECMWF IFS Cy45r1 Part VII Ch.4 — 운영 Hs OI, 지수 상관 L=300 km
- Hollingsworth & Lonnberg (1986) Tellus 38A, 111 — 혁신 공분산의 배경/관측 분해

본 프로젝트가 문헌보다 sigma_b가 한 자릿수 작은 이유 (반드시 함께 보고할 것):
  (1) 여름 저파고 표본(평균 Hs 0.79 m)
  (2) 우리 리드 0 배경은 자유 예보가 아니라 GFS-Wave 분석장 적분량의 재구성이라
      이미 관측에 가깝다 (리드 0 자체<->GFS-Wave 상관 0.9939)
"""

from __future__ import annotations

from dataclasses import dataclass

# ── 관측오차 R ────────────────────────────────────────────────────────────────
# 관측 시계열 구조함수 D(tau)=E[(Hs(t+tau)-Hs(t))^2] 의 120~360분 선형 외삽 nugget
# 0.00847 m^2 -> 단일 보고 백색잡음 0.065 m (KMA 0.1 m 양자화 기여 0.1/sqrt(12)=0.029 m 포함).
# collocate 는 +-30분 창 평균을 쓰므로(obs_window_min=30) 창 내 k개 평균으로 1/sqrt(k),
# 시간 불일치 신호오차 sqrt(S(30)/2)=0.047 m 가 남는다 -> 0.053(10분국)~0.060(30분국).
SIGMA_O_RANDOM = 0.060
"""관측 기기·표본·시간불일치 오차 [m] (콜로케이션 후)."""

# 정적 대표성 오차: 리드 6~24 h 의 관측소 지속 성분 바닥값 0.084~0.099 m.
# 대표성(부이 점 vs 25 km 셀)은 리드에 무관해야 한다는 가정에 의존한다. [가설]
SIGMA_O_REPR = 0.090
"""관측소 편차 미보정 시의 대표성 오차 [m]."""

SIGMA_O_REPR_DEBIASED = 0.050
"""관측소 편차 보정 후 잔존 대표성 오차 [m].

실측 sigma_o_total(보정 후) 0.078 m 에서 SIGMA_O_RANDOM 을 뺀 값
sqrt(0.078^2 - 0.060^2) = 0.050 m.
"""

# ── 배경오차 B ────────────────────────────────────────────────────────────────
# 리드 0 국소 무작위 성분 sqrt(0.1302^2 - 0.060^2) = 0.116 m -> sigma_o 불확실성
# (0.05~0.09) 반영 후 0.10 m 로 반올림.
SIGMA_B_LOCAL0 = 0.10
"""배경오차의 Hs 무관(가법) 성분 [m]."""

SIGMA_B_LOCAL_REL = 0.06
"""배경오차의 Hs 비례 성분 [무차원].

sigma_res(Hs) = sqrt(0.1148^2 + (0.0745 Hs)^2) 최대우도 적합(NLL -704.3, 순수 승법 대비
dNLL 257)에서 R 의 비례분을 제거한 값. 90% 부트스트랩 CI [0.00, 0.105] — 유의하지 않다.
**가법이 지배하므로 로그 변환 동화는 쓰지 않는다.**
"""

SIGMA_B_STATIC = 0.131
"""관측소 편차를 보정하지 않을 때 B 에 추가로 얹히는 지속 성분 [m].

sqrt(0.1588^2 - 0.090^2). 이것을 B 에 남기면 매 사이클 같은 격자점에 같은 부호의
증분이 찍힌다(R-1 얼룩). 편차 보정 사용을 권고하는 이유다.
"""

SIGMA_B_UNIFORM = 0.029
"""영역 균일(사이클) 성분 [m] — 현 구현에서는 국소 성분에 흡수. 미사용."""

# ── 상관 구조 ─────────────────────────────────────────────────────────────────
# **L 은 본 관측망으로 결정 불가하다.** 31개소 465쌍 중 50 km 미만이 2쌍뿐이고,
# 원혁신의 단거리 상관 0.405 는 관측소 지속 편차를 빼면 0.150 (90% CI [-0.07, 0.44])로
# 소멸한다 — 상관이 아니라 편차 패턴이었다 (CLAUDE.md 함정 2 와 같은 구조).
# 독립 추정 3건: SOAR 적합 183 km / 지수 적합 338 km / LSQ 지수 70 km, 상한 ~150 km.
# 기본값 150 km 는 이들의 보수적 중앙 근처이며 **잠정값이다.**
# 운영 승급 전 L in {50,100,150,250,400} 민감도 스캔이 의무다 (검증 설계 V-1).
CORR_LEN_KM_DEFAULT = 150.0
CORR_SHAPE_DEFAULT = "soar"
"""SOAR (1+r/L) exp(-r/L) — Greenslade & Young (2004) 채택형.

형상 판별력 없음(지수/가우시안/SOAR 가중RMS 0.0051/0.0055/0.0053, 차이가 잡음 이하).
"""

LOCALIZATION_CUTOFF_FACTOR = 3.0
"""국지화 지지반경 = 3L (Gaspari & Cohn 1999 5차 조각다항식의 지지반경)."""

# ── 관측소 지속 편차 보정 ─────────────────────────────────────────────────────
BIAS_SHRINK_K = 3.0
"""경험적 베이즈 수축 상수 k: b_s = n/(n+k) * (관측소 평균 혁신 - 전역 평균 혁신).

관측소당 표본이 4~10건뿐이라 원 평균은 잡음이 크다. k=3 은 표본 4건에서 57% 수축.
"""

BIAS_MIN_CYCLES = 2
"""편차 추정에 요구하는 최소 사이클 수 (leave-one-cycle-out 이후 기준)."""

# ── 증분 제한 ─────────────────────────────────────────────────────────────────
Q_MIN, Q_MAX = 0.5, 2.0
"""에너지 배율 q = Hs_a/Hs_b 의 클립 범위. 스펙트럼 재척도가 물리 범위를 벗어나지 않게 한다."""

HS_B_FLOOR = 0.05
"""Hs_b 가 이보다 작은 셀은 q=1 (0 나눗셈·무한 증폭 차단)."""

GAMMA_BJ = 0.73
"""쇄파 한계 Hs_a <= gamma * depth — 엔진 depth_breaking 과 동일 상수
(Battjes & Janssen 1978; poseidon/engines/spectral_wave/sources.py)."""

WINDSEA_ENERGY_FRAC = 0.75
"""셀을 WINDSEA 로 분류하는 풍파 에너지 비율 임계 (ECMWF IFS Cy45r1 Part VII 4.2.1).

민감도 스캔 {0.60, 0.75, 0.90} 이 검증 설계에서 요구된다 (CLAUDE.md 함정 2).
"""

MIN_SEA_WEIGHT = 0.05
"""관측 연산자 H 의 해양 가중 하한 (collocate.MIN_SEA_WEIGHT 와 동일 규약)."""

MAX_LAND_FRAC = 0.05
"""관측 채택 상한 육지 가중 (collocate.LAND_FRAC_MAX 와 동일)."""

OBS_WINDOW_MIN = 30.0
"""관측 시각 창 [분]. collocate.collocate_wave 의 obs_window_min 과 반드시 같아야 한다
— SIGMA_O_RANDOM 이 이 창 폭 가정에서 유도됐다."""

METHOD_TAG = "oi-hs-v1"
"""산출물에 각인되는 방법 태그. 알고리즘이 바뀌면 반드시 올린다."""


@dataclass(frozen=True)
class AssimConfig:
    """동화 설정. 전 필드가 산출물 attrs 에 기록된다 (재현성)."""

    method: str = METHOD_TAG
    corr_shape: str = CORR_SHAPE_DEFAULT          # "soar" | "exp" | "gauss"
    corr_len_km: float = CORR_LEN_KM_DEFAULT
    loc_factor: float = LOCALIZATION_CUTOFF_FACTOR
    sigma_o_random: float = SIGMA_O_RANDOM
    sigma_b_local0: float = SIGMA_B_LOCAL0
    sigma_b_local_rel: float = SIGMA_B_LOCAL_REL
    # [2026-08-25 실측] 기본값 False — 6사이클 중 4건에서 False가 더 나았고
    # (20260821T00 −22.6 vs −18.9%, 20260822T00 −36.5 vs −29.9%), chi2도 False에서
    # 1.12(목표 1.0±0.3 내)인 반면 True에서 1.42로 벗어난다. SIGMA_O_REPR_DEBIASED가
    # 과소 설정됐다는 뜻이며, True를 기본으로 되돌리려면 다사이클 근거가 새로 필요하다.
    bias_correction: bool = False                 # 관측소 지속 편차 LOCO-EB 제거
    gross_k: float = 3.5                          # 조대오차 폐기 임계 |d| > k·sqrt(σb²+σo²)
    chi2_max: float = 4.0                         # 자기일관성 상한 — 초과 시 동화 포기
    bias_shrink_k: float = BIAS_SHRINK_K
    rescale: str = "ecmwf"                        # "ecmwf" | "energy"
    windsea_frac: float = WINDSEA_ENERGY_FRAC
    line_of_sight: bool = True                    # 육지 가로지르는 영향 차단
    smooth_passes: int = 2                        # ln q 1-2-1 평활 횟수
    obs_window_min: float = OBS_WINDOW_MIN
    max_land_frac: float = MAX_LAND_FRAC
    q_min: float = Q_MIN
    q_max: float = Q_MAX
    exclude_stations: tuple[str, ...] = ()        # 교차검증 홀드아웃용

    def sigma_o(self) -> float:
        """관측오차 표준편차 [m] — 편차 보정 여부에 따라 대표성 항이 달라진다."""
        repr_ = SIGMA_O_REPR_DEBIASED if self.bias_correction else SIGMA_O_REPR
        return float((self.sigma_o_random ** 2 + repr_ ** 2) ** 0.5)

    def sigma_b(self, hs: float | object) -> object:
        """배경오차 표준편차 [m]. 가법+비례 혼합 (로그 변환 금지 — params 문서 참조)."""
        import numpy as _np

        h = _np.maximum(_np.asarray(hs, dtype=_np.float64), 0.0)
        var = self.sigma_b_local0 ** 2 + (self.sigma_b_local_rel * h) ** 2
        if not self.bias_correction:
            var = var + SIGMA_B_STATIC ** 2
        return _np.sqrt(var)

    def as_attrs(self) -> dict[str, object]:
        from dataclasses import asdict

        d = asdict(self)
        d["exclude_stations"] = ",".join(self.exclude_stations)
        d["sigma_o_effective"] = self.sigma_o()
        return d
