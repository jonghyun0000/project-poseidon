"""물리 상수 (CODATA/WMO 표준값). 추측 금지 — 출처 명기."""

from __future__ import annotations

import math

G_STANDARD = 9.80665          # 표준 중력가속도 m/s² (WMO/ISO 80000-3)
OMEGA_EARTH = 7.292115e-5     # 지구 자전 각속도 rad/s (IERS)
R_EARTH = 6_371_000.0         # 평균 지구 반경 m (IUGG)
RHO_SEAWATER = 1025.0         # 대표 해수 밀도 kg/m³ (TEOS-10 도입 전 근사)
RHO_AIR = 1.225               # 표준 대기 밀도 kg/m³ (ISA 해면)
NU_AIR = 1.4e-5               # 공기 동점성계수 m²/s. WAVEWATCH III `constants.F90`
                              # 의 NU_AIR 와 같은 값 (약 15 °C 기준)


def coriolis_f(lat_deg: float) -> float:
    """코리올리 매개변수 f = 2Ω sin φ [rad/s]."""
    return 2.0 * OMEGA_EARTH * math.sin(math.radians(lat_deg))
