"""Read all observation month partitions overlapping a UTC query window."""

from pathlib import Path

import numpy as np
import pandas as pd


def latest_observations(base: Path, var: str, now: pd.Timestamp, hours: float) -> pd.DataFrame:
    cutoff = now - pd.Timedelta(hours=hours)
    start = cutoff.normalize().replace(day=1)
    frames = []
    for month in pd.date_range(start, now, freq="MS"):
        path = base / f"{month.year:04d}" / f"{month.month:02d}.parquet"
        if path.exists():
            frames.append(pd.read_parquet(path))
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["ts"] = pd.to_datetime(df["ts"], utc=True, errors="coerce")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df[(df["var"] == var) & (df["qc_flag"] == 0)
            & (df["ts"] > cutoff) & (df["ts"] <= now) & np.isfinite(df["value"])]
    return df.sort_values("ts", kind="stable").groupby("station_id").tail(1)
