"""L1 vs L2 콜로케이션 비교 (동일 관측소·시각 짝), 수심 구간별 — 다중 사이클 집계.
사용: .venv/bin/python scripts/compare_l1_l2.py 20260821T00 [20260820T00 ...]   (인자 없으면 L2 있는 전 사이클)"""
import json, sys
import numpy as np, pandas as pd
from poseidon.core.catalog import Catalog
from poseidon.core.config import settings
cat = Catalog(settings.catalog_path)
a = pd.DataFrame(cat.error_samples("hs")); b = pd.DataFrame(cat.error_samples("hs_l2"))
cycles = sys.argv[1:] or sorted(b.cycle.unique())
a = a[a.cycle.isin(cycles)]; b = b[b.cycle.isin(cycles)]
m = a.merge(b, on=["cycle", "station_id", "valid_time"], suffixes=("_l1", "_l2"))
m["depth"] = m["features_l1"].apply(lambda f: json.loads(f).get("depth", np.nan))
r = lambda x, y: float(np.sqrt(np.mean((x - y) ** 2)))
print(f"사이클 {cycles} · 짝 {len(m)}건 · 관측소 {m.station_id.nunique()}")
def rep(d, tag):
    if len(d) == 0: return
    print(f"{tag:12s} n={len(d):4d} | L1 RMSE {r(d.predicted_l1,d.observed_l1):.3f} bias {(d.predicted_l1-d.observed_l1).mean():+.3f}"
          f" | L2 RMSE {r(d.predicted_l2,d.observed_l2):.3f} bias {(d.predicted_l2-d.observed_l2).mean():+.3f}"
          f" | ΔRMSE {100*(r(d.predicted_l2,d.observed_l2)/r(d.predicted_l1,d.observed_l1)-1):+.0f}%")
rep(m, "전체"); rep(m[m.depth < 30], "천해 <30m"); rep(m[(m.depth>=30)&(m.depth<100)], "30-100m"); rep(m[m.depth >= 100], "≥100m")
