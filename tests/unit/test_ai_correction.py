import json

import numpy as np
import pandas as pd
import pytest

from poseidon.ai.correction import ResidualCorrector


def _synthetic(n_per=120, stations=("A", "B", "C"), seed=1):
    """진실 Hs에 곱·상수 편향과 잡음을 가한 가짜 물리 예측."""
    rng = np.random.default_rng(seed)
    rows = []
    for sid in stations:
        truth = rng.uniform(0.5, 6.0, n_per)
        lead = rng.choice([3.0, 6.0, 12.0, 24.0], n_per)
        u10 = rng.uniform(2, 20, n_per)
        fc = 0.82 * truth - 0.15 + 0.05 * rng.standard_normal(n_per)
        for t, ld, u, f in zip(truth, lead, u10, fc):
            rows.append({"station_id": sid, "predicted": max(f, 0.01),
                         "observed": t, "lead_h": ld,
                         "features": json.dumps({"u10": u})})
    return pd.DataFrame(rows)


def test_corrector_removes_systematic_bias():
    df = _synthetic()
    m = ResidualCorrector().fit(df)
    hs = df["predicted"].to_numpy()
    obs = df["observed"].to_numpy()
    out = m.apply(hs, df["lead_h"].to_numpy(),
                  np.array([json.loads(f)["u10"] for f in df["features"]]))
    rmse_raw = np.sqrt(np.mean((hs - obs) ** 2))
    rmse_cor = np.sqrt(np.mean((out["corrected"] - obs) ** 2))
    assert rmse_cor < 0.5 * rmse_raw                 # 체계 편향 대부분 제거
    assert (out["physics_raw"] == hs).all()          # 물리 원값 보존 (투명성)
    assert (out["q05"] <= out["corrected"] + 1e-9).all()
    assert (out["q95"] >= out["corrected"] - 1e-9).all()


def test_leave_station_out_generalizes():
    df = _synthetic()
    cv = ResidualCorrector().leave_station_out_cv(df)
    assert len(cv) == 3
    assert (cv["rmse_corrected"] < cv["rmse_raw"]).all()   # 미학습 관측소에서도 개선


def test_artifact_roundtrip(tmp_path):
    df = _synthetic()
    m = ResidualCorrector().fit(df)
    p = tmp_path / "model.json"
    m.save(p)
    m2 = ResidualCorrector.load(p)
    hs = np.array([1.0, 3.0])
    a = m.apply(hs, np.array([6.0, 6.0]), np.array([10.0, 10.0]))
    b = m2.apply(hs, np.array([6.0, 6.0]), np.array([10.0, 10.0]))
    np.testing.assert_allclose(a["corrected"], b["corrected"])
