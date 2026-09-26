import numpy as np
import pandas as pd
import pytest

from poseidon.physics.tides import CONSTITUENT_SPEEDS_DEG_PER_HR, HarmonicTide


def _synthetic(days=30.0, step_min=10, noise=0.0, seed=0):
    truth = {"M2": (1.20, 40.0), "S2": (0.50, 75.0), "K1": (0.25, 130.0),
             "O1": (0.18, 200.0), "N2": (0.22, 20.0)}
    times = pd.date_range("2026-06-01", periods=int(days * 24 * 60 / step_min),
                          freq=f"{step_min}min", tz="UTC")
    t = (times - times[0]).total_seconds().to_numpy() / 3600.0
    v = np.full(t.shape, 0.7)  # Z0
    for n, (a, p) in truth.items():
        w = np.radians(CONSTITUENT_SPEEDS_DEG_PER_HR[n])
        v += a * np.cos(w * t - np.radians(p))
    rng = np.random.default_rng(seed)
    v += noise * rng.standard_normal(t.shape)
    return times, v, truth


def test_harmonic_fit_recovers_known_constants():
    times, v, truth = _synthetic()
    ht = HarmonicTide(("M2", "S2", "N2", "K1", "O1")).fit(times, v)
    assert abs(ht.z0 - 0.7) < 1e-6
    for n, (a, p) in truth.items():
        assert abs(ht.amp[n] - a) < 1e-6, n
        assert abs((ht.phase_deg[n] - p + 180) % 360 - 180) < 1e-4, n


def test_prediction_beyond_record_with_noise():
    times, v, _ = _synthetic(days=30, noise=0.05)
    ht = HarmonicTide(("M2", "S2", "N2", "K1", "O1")).fit(times, v)
    fut = pd.date_range(times[-1], periods=5 * 144, freq="10min", tz="UTC")
    times2, v2, _ = _synthetic(days=30.5)
    ref = ht.predict(fut)
    # 진실 신호 재구성과 비교 (노이즈 있는 25일 기록으로 5일 외삽)
    t = (fut - times2[0]).total_seconds().to_numpy() / 3600.0
    vt = np.full(t.shape, 0.7)
    for n, (a, p) in {"M2": (1.2, 40.), "S2": (.5, 75.), "K1": (.25, 130.),
                      "O1": (.18, 200.), "N2": (.22, 20.)}.items():
        w = np.radians(CONSTITUENT_SPEEDS_DEG_PER_HR[n])
        vt += a * np.cos(w * t - np.radians(p))
    rmse = float(np.sqrt(np.mean((ref - vt) ** 2)))
    assert rmse < 0.02


def test_rayleigh_criterion_enforced():
    times, v, _ = _synthetic(days=10)
    with pytest.raises(ValueError, match="Rayleigh"):
        # K2는 S2와 분리에 ~183일 필요 — 10일 기록이면 거부해야 한다
        HarmonicTide(("M2", "S2", "K2")).fit(times, v)
