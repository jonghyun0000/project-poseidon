import pandas as pd

from poseidon.ingest.qc import apply_qc


def _frame(values, var="hs", freq="10min"):
    ts = pd.date_range("2026-08-01", periods=len(values), freq=freq, tz="UTC")
    return pd.DataFrame({
        "station_id": "TEST:1", "ts": ts, "var": var, "value": values,
    })


def test_range_check_flags_bad():
    df = apply_qc(_frame([1.0, 2.0, 30.0, -0.5, 3.0]))  # hs 한계 0..25
    assert list(df["qc_flag"]) == [0, 0, 2, 2, 0]


def test_spike_check_flags_isolated_jump():
    df = apply_qc(_frame([2.0, 2.1, 9.9, 2.2, 2.3]))  # 고립 스파이크 (±7.8 > 4.0)
    assert df.loc[2, "qc_flag"] == 1
    assert (df.drop(index=2)["qc_flag"] == 0).all()


def test_spike_check_accepts_real_step_change():
    # 폭풍 급성장(단조 상승)은 고립점이 아니므로 통과해야 한다
    df = apply_qc(_frame([2.0, 2.5, 6.0, 6.2, 6.4]))
    assert (df["qc_flag"] == 0).all()


def test_stuck_check_flags_flatline():
    df = apply_qc(_frame([0.61] * 35 + [0.65], var="wl", freq="1min"))
    assert (df.loc[:34, "qc_flag"] == 1).all()
    assert df.loc[35, "qc_flag"] == 0


def test_stuck_check_skips_low_resolution_vars():
    # 기압은 분해능이 낮아 정상적으로 반복 → 정체 검사 제외
    df = apply_qc(_frame([1010.2] * 40, var="slp", freq="10min"))
    assert (df["qc_flag"] == 0).all()


def test_qc_preserves_values_and_rows():
    src = _frame([1.0, 50.0, 2.0])
    out = apply_qc(src)
    assert len(out) == len(src)
    assert (out["value"] == src["value"]).all()  # 원값 불변 원칙
