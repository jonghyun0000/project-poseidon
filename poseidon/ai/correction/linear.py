"""AI 오차 보정 1단계 — 릿지 잔차 회귀 (M7 인프라).

원칙 (PHASE2 §1): 물리 예측을 대체하지 않고 잔차 r = 관측 − 예측 을 학습해 후처리한다.
보정 여부·모델 버전·물리 원값은 산출물에 항상 보존된다 (API 투명성 계약).

- 피처: [1, hs_fc, hs_fc², lead/24, u10/20] — 소표본에서 과적합을 피하는 최소 구성.
  표본이 축적되면 FNO/GBM 계열로 승급 (같은 인터페이스, docs/PHASE5 §12 백로그).
- 검증: leave-one-station-out 교차검증 (공간 일반화 강제, 카탈로그 §K 리스크 대응)
- 불확실성: 잔차 분위수(q05/q95) — 훈련 잔차 기반 1차 예측구간.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

FEATURE_NAMES = ("const", "hs", "hs2", "lead", "u10", "logdepth", "hs_gfsw")


def _design(hs: np.ndarray, lead_h: np.ndarray, u10: np.ndarray,
            depth: np.ndarray | None = None, hs_gfsw: np.ndarray | None = None) -> np.ndarray:
    hs = np.asarray(hs, float)
    depth = np.full_like(hs, 50.0) if depth is None else np.asarray(depth, float)
    hs_gfsw = hs.copy() if hs_gfsw is None else np.asarray(hs_gfsw, float)
    return np.column_stack([
        np.ones_like(hs), hs, hs ** 2, np.asarray(lead_h, float) / 24.0,
        np.asarray(u10, float) / 20.0, np.log10(np.maximum(depth, 1.0)) / 3.0,
        hs_gfsw - hs,                                   # 멀티모델 차이 (GFS-Wave − 자체)
    ])


def _feat(df: pd.DataFrame, key: str, default: float) -> np.ndarray:
    vals = []
    for f in df["features"]:
        try:
            v = json.loads(f).get(key) if isinstance(f, str) else None
        except json.JSONDecodeError:
            v = None
        vals.append(np.nan if v is None else float(v))
    arr = np.array(vals, float)
    fill = float(np.nanmean(arr)) if np.isfinite(arr).any() else default
    return np.nan_to_num(arr, nan=fill)


def _frame_arrays(df: pd.DataFrame):
    hs = df["predicted"].to_numpy(float)
    u10 = _feat(df, "u10", 8.0)
    depth = _feat(df, "depth", 50.0)
    g = _feat(df, "hs_gfsw", np.nan)
    g = np.where(np.isfinite(g), g, hs)
    return hs, df["lead_h"].to_numpy(float), u10, df["observed"].to_numpy(float), depth, g


@dataclass
class ResidualCorrector:
    """잔차 릿지 보정.

    릿지는 **절편을 벌점하지 않고** 피처를 표준화한 뒤 적용한다. 보정 대상이 계통
    편차(=절편)인데 이를 벌점하면 정작 고쳐야 할 항을 눌러버린다 — 교차검증에서
    LSO RMSE 0.1660 → 0.1577, 릿지 민감도도 극단적 → 평탄(0.1~0.5)으로 개선.
    (Hastie et al., *ESL* §3.4.1의 표준 관행)
    """

    version: str = "corr-hs-ridge-v2"
    ridge: float = 0.2
    coef: np.ndarray | None = None
    resid_q05: float = 0.0
    resid_q95: float = 0.0
    mu: np.ndarray | None = None
    sd: np.ndarray | None = None

    def _standardize(self, x: np.ndarray) -> np.ndarray:
        if self.mu is None or self.sd is None:
            return x
        return (x - self.mu) / self.sd

    def fit(self, df: pd.DataFrame) -> "ResidualCorrector":
        hs, lead, u10, obs, depth, g = _frame_arrays(df)
        x = _design(hs, lead, u10, depth, g)
        r = obs - hs
        mu, sd = x.mean(0), x.std(0)
        mu[0], sd[0] = 0.0, 1.0                       # 절편 열은 변환하지 않는다
        sd = np.where(sd < 1e-9, 1.0, sd)
        self.mu, self.sd = mu, sd
        z = (x - mu) / sd
        pen = np.eye(x.shape[1])
        pen[0, 0] = 0.0                               # 절편 비벌점
        self.coef = np.linalg.solve(z.T @ z + self.ridge * len(df) * pen, z.T @ r)
        resid_after = r - z @ self.coef
        self.resid_q05 = float(np.quantile(resid_after, 0.05))
        self.resid_q95 = float(np.quantile(resid_after, 0.95))
        return self

    def apply(self, hs: np.ndarray, lead_h: np.ndarray, u10: np.ndarray,
              depth: np.ndarray | None = None,
              hs_gfsw: np.ndarray | None = None) -> dict[str, np.ndarray]:
        if self.coef is None:
            raise RuntimeError("fit() first")
        dr = self._standardize(
            _design(np.asarray(hs, float), np.asarray(lead_h, float),
                    np.asarray(u10, float), depth, hs_gfsw)) @ self.coef
        corrected = np.maximum(np.asarray(hs) + dr, 0.0)
        return {"corrected": corrected, "physics_raw": np.asarray(hs, float),
                "q05": np.maximum(corrected + self.resid_q05, 0.0),
                "q95": corrected + self.resid_q95}

    # ── 평가 ─────────────────────────────────────────────────────
    @staticmethod
    def _rmse(a: np.ndarray, b: np.ndarray) -> float:
        return float(np.sqrt(np.mean((a - b) ** 2)))

    def leave_station_out_cv(self, df: pd.DataFrame) -> pd.DataFrame:
        """관측소 하나를 통째로 제외하고 학습→그 관측소에서 평가 (공간 일반화)."""
        rows = []
        for sid in df["station_id"].unique():
            tr, te = df[df["station_id"] != sid], df[df["station_id"] == sid]
            if len(tr) < 3 * len(FEATURE_NAMES) or te.empty:
                continue
            m = ResidualCorrector(ridge=self.ridge).fit(tr)
            hs, lead, u10, obs, depth, g = _frame_arrays(te)
            corr = m.apply(hs, lead, u10, depth, g)["corrected"]
            rows.append({"station_id": sid, "n": len(te),
                         "rmse_raw": self._rmse(hs, obs),
                         "rmse_corrected": self._rmse(corr, obs)})
        return pd.DataFrame(rows)

    # ── 아티팩트 ──────────────────────────────────────────────────
    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "version": self.version, "ridge": self.ridge,
            "features": list(FEATURE_NAMES),
            "coef": list(map(float, self.coef)),          # type: ignore[arg-type]
            "mu": None if self.mu is None else list(map(float, self.mu)),
            "sd": None if self.sd is None else list(map(float, self.sd)),
            "resid_q05": self.resid_q05, "resid_q95": self.resid_q95,
        }, indent=2))

    @classmethod
    def load(cls, path: Path) -> "ResidualCorrector":
        d = json.loads(path.read_text())
        m = cls(version=d["version"], ridge=d["ridge"])
        m.coef = np.array(d["coef"])
        k = len(m.coef)
        m.mu = np.array(d["mu"]) if d.get("mu") else np.zeros(k)      # 구 아티팩트 호환
        m.sd = np.array(d["sd"]) if d.get("sd") else np.ones(k)
        m.resid_q05, m.resid_q95 = d["resid_q05"], d["resid_q95"]
        return m
