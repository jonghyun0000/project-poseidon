"""기상청 해양관측 백필: 기간 자료(kma_buoy2/lhaws2) → QC → parquet(provider=kma) + 카탈로그.

사용: POSEIDON_KMA_AUTHKEY=... .venv/bin/python scripts/backfill_kma.py 2026-08-20 2026-08-23
"""
import asyncio
import sys
from datetime import datetime, timezone, timedelta

from poseidon.core.catalog import Catalog
from poseidon.core.config import settings
from poseidon.datalake.store import write_obs
from poseidon.ingest.adapters.kma import KMAMarineAdapter
from poseidon.ingest.qc import apply_qc

KST = timezone(timedelta(hours=9))
start = datetime.strptime(sys.argv[1], "%Y-%m-%d").replace(tzinfo=KST)
end = datetime.strptime(sys.argv[2], "%Y-%m-%d").replace(tzinfo=KST)
df = asyncio.run(KMAMarineAdapter().fetch_range(start, end))
df = apply_qc(df)
paths = write_obs(df, provider="kma", catalog=Catalog(settings.catalog_path))
print(f"backfill {start.date()}→{end.date()}: {len(df)} rows, "
      f"stations={df.station_id.nunique()}, flagged={(df.qc_flag>0).sum()} → {[p.name for p in paths]}")
