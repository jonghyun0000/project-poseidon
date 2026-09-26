"""조석 조화분석·합성 (Doodson/Darwin 전통의 최소제곱 구현).

η(t) = Z₀ + Σᵢ Aᵢ cos(ωᵢ t − φᵢ)

- 분조 각속도는 천문 상수에서 유도된 표준값 (Schureman 1958, Pugh & Woodworth 2014).
- 관측 시계열에서 조화상수를 최소제곱으로 추정(분석)하고, 임의 시각으로 외삽(합성)한다.
- 분조 분리 가능성은 Rayleigh 기준을 따른다: 기록 길이 T ≥ 1/|f₁−f₂|
  (S2/K2 분리엔 ~183일 필요 → 짧은 기록에서는 K2 제외 권장).
- 절점(nodal) 보정은 18.61년 주기 진폭 변조로, 수 주 기록의 예측 오차 기여는 수 % 수준 —
  1차 구현에서는 생략하고 문서화한다 (EOT20 전지구 상수 도입 시 함께 추가).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from numpy.typing import NDArray

Arr = NDArray[np.float64]

# 분조 각속도 [deg/hour] — Schureman (1958) 표준값
CONSTITUENT_SPEEDS_DEG_PER_HR: dict[str, float] = {
    "M2": 28.9841042,   # 주 태음 반일주조
    "S2": 30.0000000,   # 주 태양 반일주조
    "N2": 28.4397295,   # 태음 타원 반일주조
    "K2": 30.0821373,   # 태음태양 반일주조 (S2와 분리에 ~183일 필요)
    "K1": 15.0410686,   # 태음태양 일주조
    "O1": 13.9430356,   # 주 태음 일주조
    "P1": 14.9589314,   # 주 태양 일주조 (K1과 분리에 ~183일 필요)
    "Q1": 13.3986609,   # 태음 타원 일주조
    "M4": 57.9682084,   # 천해 배조 (M2 자기상호작용)
    "MS4": 58.9841042,  # 천해 복합조 (M2+S2)
}

DEFAULT_SHORT_RECORD = ("M2", "S2", "N2", "K1", "O1", "Q1", "M4", "MS4")


def _design_matrix(t_hours: Arr, names: tuple[str, ...]) -> Arr:
    cols = [np.ones_like(t_hours)]
    for n in names:
        w = np.radians(CONSTITUENT_SPEEDS_DEG_PER_HR[n])
        cols += [np.cos(w * t_hours), np.sin(w * t_hours)]
    return np.column_stack(cols)


class HarmonicTide:
    """관측 → 조화상수(분석) → 임의 시각 조위(합성)."""

    def __init__(self, names: tuple[str, ...] = DEFAULT_SHORT_RECORD) -> None:
        self.names = names
        self.z0: float = 0.0
        self.amp: dict[str, float] = {}
        self.phase_deg: dict[str, float] = {}
        self._epoch: pd.Timestamp | None = None

    def fit(self, times: pd.DatetimeIndex, values: Arr) -> "HarmonicTide":
        if len(times) < 2 * (2 * len(self.names) + 1):
            raise ValueError("record too short for requested constituents")
        span_h = (times[-1] - times[0]).total_seconds() / 3600.0
        for a, b in ((a, b) for i, a in enumerate(self.names)
                     for b in self.names[i + 1:]):
            df = abs(CONSTITUENT_SPEEDS_DEG_PER_HR[a]
                     - CONSTITUENT_SPEEDS_DEG_PER_HR[b]) / 360.0  # cycles/hr
            if span_h < 1.0 / max(df, 1e-12):
                raise ValueError(
                    f"Rayleigh criterion violated for {a}/{b}: need "
                    f"{1.0/df/24:.0f} d, have {span_h/24:.1f} d")

        self._epoch = times[0]
        t = (times - self._epoch).total_seconds().to_numpy() / 3600.0
        v = np.asarray(values, dtype=np.float64)
        good = np.isfinite(v)
        A = _design_matrix(t[good], self.names)
        coef, *_ = np.linalg.lstsq(A, v[good], rcond=None)

        self.z0 = float(coef[0])
        for i, n in enumerate(self.names):
            c, s = coef[1 + 2 * i], coef[2 + 2 * i]
            self.amp[n] = float(np.hypot(c, s))
            self.phase_deg[n] = float(np.degrees(np.arctan2(s, c)) % 360.0)
        return self

    def to_dict(self) -> dict:
        assert self._epoch is not None
        return {"names": list(self.names), "z0": self.z0, "amp": self.amp,
                "phase_deg": self.phase_deg, "epoch": self._epoch.isoformat()}

    @classmethod
    def from_dict(cls, d: dict) -> "HarmonicTide":
        ht = cls(tuple(d["names"]))
        ht.z0, ht.amp, ht.phase_deg = d["z0"], d["amp"], d["phase_deg"]
        ht._epoch = pd.Timestamp(d["epoch"])
        return ht

    def predict(self, times: pd.DatetimeIndex) -> Arr:
        if self._epoch is None:
            raise RuntimeError("fit() first")
        t = (times - self._epoch).total_seconds().to_numpy() / 3600.0
        out = np.full(t.shape, self.z0)
        for n in self.names:
            w = np.radians(CONSTITUENT_SPEEDS_DEG_PER_HR[n])
            ph = np.radians(self.phase_deg[n])
            out += self.amp[n] * np.cos(w * t - ph)
        return out
