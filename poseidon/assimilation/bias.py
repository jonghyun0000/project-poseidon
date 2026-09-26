"""관측소 지속 편차 b_s 의 경험적 베이즈 추정 (leave-one-cycle-out).

**왜 필요한가 (이 모듈의 존재 이유)**
리드 0 혁신 분산의 59%가 "관측소 고정 성분"이다(실측, params.py 참조). 이것은 모델
상태 오차가 아니라 대표성 오차(부이 점 vs 0.25° 셀 · 차폐 · 국지 수심)가 지배한다.
그대로 OI 에 넣으면 부이 좌표마다 **매 사이클 같은 부호의 증분**이 찍혀 지름 2L 의
얼룩이 상태에 각인되고, 이류로 하류에 실려 없던 구조물을 만든다.

**무엇을 빼고 무엇을 남기는가**
전역 평균 혁신(리드 0 에서 +0.1155 m, 모델 과소)은 **빼지 않는다.** 이 성분은 리드가
늘면 +0.116 -> +0.013 으로 감쇠하므로 대표성이 아니라 초기장 고유의 계통 결함이고,
동화가 교정해야 할 실체다. 빼는 것은 "전역 평균으로부터의 관측소별 이탈"뿐이다.

**누출 차단**: 현재 사이클의 표본은 추정에서 제외한다(leave-one-cycle-out).
그렇게 하지 않으면 동화가 자기 검증 표본을 미리 본 것이 된다.

수축: b_s = n_s/(n_s + k) * (관측소 평균 이탈), k=3 (params.BIAS_SHRINK_K).
Efron & Morris (1975) JASA 70, 311 계열의 축소 추정 — 관측소당 표본이 4~10건뿐이라
원 평균은 잡음이 크다.
"""

from __future__ import annotations

import json
import logging

import numpy as np
import pandas as pd

from poseidon.assimilation.params import BIAS_MIN_CYCLES, BIAS_SHRINK_K
from poseidon.core.catalog import Catalog
from poseidon.core.config import settings
from poseidon.validation.collocate import filter_by_land

log = logging.getLogger("poseidon.assimilation.bias")

LEAD0_MAX_H = 0.5
CLEAN_TAG = "sea-norm-v1"


def _features(df: pd.DataFrame) -> pd.DataFrame:
    parsed = []
    for f in df.get("features", pd.Series([None] * len(df), index=df.index)):
        try:
            d = json.loads(f) if isinstance(f, str) else (f or {})
        except (json.JSONDecodeError, TypeError):
            d = {}
        parsed.append(d if isinstance(d, dict) else {})
    return pd.DataFrame(parsed, index=df.index)


def load_lead0_innovations(
    *, var: str = "hs", max_land_frac: float = 0.05,
    catalog: Catalog | None = None,
) -> pd.DataFrame:
    """청정 리드 0 혁신 표본 (cycle, station_id, innov=observed-predicted).

    필터: land_frac<=max, collocation=='sea-norm-v1', lead_h<0.5,
          동화가 적용된 표본 제외(features.assim 가 'none' 이 아닌 것).

    **주의**: 현재 collocate.py 는 features 에 assim 태그를 쓰지 않는다. 즉 동화 예보로
    콜로케이션한 표본이 들어오면 여기서 구분할 수 없다 (CLAUDE.md 함정 9). 그래서
    wave_cycle 은 동화 예보를 다른 source_id/파일명으로 기록해 collocate 가 아예 집지
    않게 한다. 이 필터는 그 규약이 깨졌을 때의 2차 방어선이다.
    """
    cat = catalog or Catalog(settings.catalog_path)
    rows = cat.error_samples(var=var)
    if not rows:
        return pd.DataFrame(columns=["cycle", "station_id", "innov"])
    df = pd.DataFrame(rows)
    df = df[pd.to_numeric(df["lead_h"], errors="coerce") < LEAD0_MAX_H]
    df = filter_by_land(df, max_land_frac, legacy="drop")
    if df.empty:
        return pd.DataFrame(columns=["cycle", "station_id", "innov"])
    feat = _features(df)
    keep = feat.get("collocation", pd.Series("legacy", index=df.index)) == CLEAN_TAG
    assim = feat.get("assim", pd.Series(None, index=df.index))
    keep &= assim.isna() | (assim.astype(str) == "none")
    df = df[keep.fillna(False)]
    if df.empty:
        return pd.DataFrame(columns=["cycle", "station_id", "innov"])
    df = df.assign(innov=pd.to_numeric(df["observed"], errors="coerce")
                   - pd.to_numeric(df["predicted"], errors="coerce"))
    df = df[np.isfinite(df["innov"])]
    # 사이클x관측소 단위로 축약 (한 사이클에 여러 표본이 있어도 1표로 센다)
    return (df.groupby(["cycle", "station_id"], as_index=False)["innov"].mean())


def station_bias_loco(
    cycle: str, *, shrink_k: float = BIAS_SHRINK_K,
    innovations: pd.DataFrame | None = None,
    min_cycles: int = BIAS_MIN_CYCLES,
) -> tuple[dict[str, float], dict[str, object]]:
    """현재 사이클을 제외한 표본으로 추정한 관측소별 편차 b_s [m].

    반환 (b_s 사전, 진단). b_s 는 **관측에서 뺄 값**이다:
        Hs_used = Hs_obs - b_s
    b_s > 0 이면 그 관측소가 전역 평균보다 모델을 더 크게 웃돈다는 뜻.
    """
    df = load_lead0_innovations() if innovations is None else innovations
    diag: dict[str, object] = {"n_samples_total": int(len(df))}
    if df.empty:
        return {}, {**diag, "status": "no-samples"}
    # 인과성: 현재 사이클만 빼면 **미래 사이클이 섞인다**(실측 210건 중 78건=37%가 미래).
    # 운영에서는 존재할 수 없는 정보이므로 과거 사이클만 쓴다.
    past = df[df["cycle"].astype(str) < str(cycle)]
    diag["n_samples_loco"] = int(len(past))
    diag["n_cycles_loco"] = int(past["cycle"].nunique())
    if past.empty or past["cycle"].nunique() < min_cycles:
        return {}, {**diag, "status": "insufficient-cycles"}
    mu = float(past["innov"].mean())          # 전역 평균 혁신 — 제거하지 않는다
    g = past.groupby("station_id")["innov"]
    n = g.count().astype(float)
    dev = g.mean() - mu
    b = (n / (n + float(shrink_k))) * dev
    b = b[n >= 1]
    diag.update({
        "status": "ok", "mu_global": mu, "n_stations": int(len(b)),
        "shrink_k": float(shrink_k),
        "b_abs_mean": float(np.abs(b).mean()), "b_abs_max": float(np.abs(b).max()),
    })
    log.info("station bias LOCO(%s): %d개소, mu=%+.4f m, |b| 평균 %.4f m",
             cycle, len(b), mu, float(np.abs(b).mean()))
    return {str(k): float(v) for k, v in b.items()}, diag
