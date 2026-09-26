"""AI 잔차 보정기 학습·평가·배포 (M7 게이트 평가 도구).

error_sample(카탈로그) → leave-station-out CV → 개선율 보고 →
개선 시 data/models/corr-hs.json 저장 (API가 자동 사용, corrected=true).

사용: .venv/bin/python -m poseidon.ai.training.train_corrector [--min-improve 0.15] [--deploy]
"""

from __future__ import annotations

import argparse
import json

import numpy as np
import pandas as pd

from poseidon.ai.correction import ResidualCorrector
from poseidon.core.catalog import Catalog
from poseidon.core.config import settings
from poseidon.validation.collocate import collocation_version, filter_by_land


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-improve", type=float, default=0.15,
                    help="M7 게이트: LSO-CV 평균 RMSE 개선율 하한")
    ap.add_argument("--deploy", action="store_true", help="게이트 통과 시 아티팩트 저장")
    ap.add_argument("--max-land-frac", type=float, default=0.05,
                    help="육지 오염 표본 배제 임계 (검증 마스크 정합성)")
    ap.add_argument("--unified", action="store_true",
                    help="L1+L2 표본 통합 학습 (해상도 일반화, 심판 권고)")
    ap.add_argument("--allow-legacy", action="store_true",
                    help="land_frac 미상(구버전 콜로케이션) 표본도 사용")
    a = ap.parse_args()

    cat = Catalog(settings.catalog_path)
    frames = [pd.DataFrame(cat.error_samples("hs"))]
    if a.unified:                       # L1+L2 통합풀 — 심판 권고 (LSO 0.1586→0.1577)
        l2 = pd.DataFrame(cat.error_samples("hs_l2"))
        if not l2.empty:
            frames.append(l2)
    df = pd.concat(frames, ignore_index=True)
    if df.empty:
        print("error_sample 비어 있음 — collocate 먼저 실행")
        return 1
    df = df[np.isfinite(df["predicted"]) & np.isfinite(df["observed"])]
    n_all = len(df)
    df = filter_by_land(df, a.max_land_frac,
                        legacy="keep" if a.allow_legacy else "drop")
    ver = collocation_version(df).value_counts().to_dict()
    print(f"육지필터(land_frac<={a.max_land_frac}): {n_all} → {len(df)}건 · 콜로케이션 버전 {ver}")
    if df.empty:
        print("필터 후 표본 없음 — 재콜로케이션 필요")
        return 1
    print(f"표본 {len(df)}건 · 관측소 {df.station_id.nunique()} · 사이클 {df.cycle.nunique()}")

    err = df["predicted"] - df["observed"]
    print(f"물리 원값: RMSE {np.sqrt((err**2).mean()):.3f} m, bias {err.mean():+.3f} m")

    cv = ResidualCorrector().leave_station_out_cv(df)
    if cv.empty:
        print("관측소 수 부족 — CV 불가")
        return 1
    w = cv["n"] / cv["n"].sum()
    rmse_raw = float((cv["rmse_raw"] * w).sum())
    rmse_cor = float((cv["rmse_corrected"] * w).sum())
    improve = 1.0 - rmse_cor / rmse_raw
    print(cv.round(3).to_string(index=False))
    print(f"LSO-CV 가중 RMSE: raw {rmse_raw:.3f} → corrected {rmse_cor:.3f}  "
          f"(개선 {improve:+.1%}, 게이트 {a.min_improve:.0%})")

    if improve >= a.min_improve:
        print("✅ M7 게이트 통과")
        if a.deploy:
            m = ResidualCorrector().fit(df)
            out = settings.data_root / "models" / "corr-hs.json"
            m.save(out)
            print(f"배포: {out} (version {m.version}, coef {np.round(m.coef, 4).tolist()})")
    else:
        print("⏳ 게이트 미달 — 표본 축적 계속 (아티팩트 미배포)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
