"""관측 QC — 원값 불변, qc_flag 부여 (PHASE2 §3.5 QCCheck 규약).

플래그: 0 good · 1 suspect · 2 bad (GTSPP 계열 축약).
검사: 물리 범위(range) · 스파이크(이웃 대비 급변) · 정체(stuck, 장시간 동일값).
"""

from __future__ import annotations

import pandas as pd

# 물리적 허용 범위 (단위: hs m, tp/ta s, 방향 deg, wspd m/s, slp hPa, 수온/기온 °C, wl m)
RANGE_LIMITS: dict[str, tuple[float, float]] = {
    "hs": (0.0, 25.0), "tp": (1.0, 30.0), "ta": (1.0, 25.0),
    "dir": (0.0, 360.0), "wdir": (0.0, 360.0),
    "wspd": (0.0, 75.0), "gust": (0.0, 100.0),
    "slp": (870.0, 1085.0), "at": (-40.0, 50.0), "sst": (-2.5, 40.0),
    "wl": (-15.0, 15.0),
}

# 스파이크 임계 (연속 관측 간 최대 허용 변화량; 방향류는 순환 특성상 제외)
SPIKE_LIMITS: dict[str, float] = {
    "hs": 4.0, "tp": 12.0, "ta": 8.0, "wspd": 25.0,
    "slp": 20.0, "at": 10.0, "sst": 5.0, "wl": 1.5,
}

# 정체(stuck) 임계 — 변수별 연속 동일값 반복 횟수. 센서 분해능이 낮은 변수(기압·기온)는
# 정상적으로 값이 반복되므로 검사하지 않는다. 목록에 없는 변수는 정체 검사 생략.
STUCK_REPEATS: dict[str, int] = {
    "wl": 30,   # 1분 조위 자료 30분 정체 (검조기 걸림 감지)
    "hs": 12,   # 10분~1시간 파고 자료의 장시간 완전 동일값
    "wspd": 12,
}


def apply_qc(df: pd.DataFrame) -> pd.DataFrame:
    """long-format [station_id, ts, var, value] → qc_flag 열 추가본 반환 (원본 불변)."""
    out = df.copy()
    out["qc_flag"] = 0

    for (_, var), idx in out.groupby(["station_id", "var"]).groups.items():
        g = out.loc[idx].sort_values("ts")
        v = g["value"]

        # 1) 범위 검사 → bad
        lim = RANGE_LIMITS.get(str(var))
        if lim is not None:
            bad = (v < lim[0]) | (v > lim[1])
            out.loc[g.index[bad], "qc_flag"] = 2

        # 2) 스파이크 → suspect (양쪽 이웃 대비 모두 급변한 고립점)
        sp = SPIKE_LIMITS.get(str(var))
        if sp is not None and len(v) >= 3:
            d_prev = v.diff().abs()
            d_next = v.diff(-1).abs()
            spike = (d_prev > sp) & (d_next > sp)
            tgt = g.index[spike.fillna(False)]
            out.loc[tgt, "qc_flag"] = out.loc[tgt, "qc_flag"].clip(lower=1)

        # 3) 정체 → suspect (STUCK_REPEATS에 정의된 변수만)
        n_rep = STUCK_REPEATS.get(str(var))
        if n_rep is not None and len(v) >= n_rep:
            runs = v.groupby((v != v.shift()).cumsum()).transform("size")
            stuck = (runs >= n_rep) & v.notna()
            tgt = g.index[stuck]
            out.loc[tgt, "qc_flag"] = out.loc[tgt, "qc_flag"].clip(lower=1)

    return out
