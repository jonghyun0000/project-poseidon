"""데이터 레이크 기록 (ADR-003).

격자 → Zarr (`data/zarr/{collection}/{domain}/{cycle}.zarr`, 청크 time=1, y·x≤256)
관측 → Parquet (`data/parquet/obs/{provider}/{YYYY}/{MM}.parquet`, 월 파티션 병합)
모든 기록은 카탈로그에 lineage와 함께 등록된다.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pandas as pd
import xarray as xr

from poseidon.core.catalog import Catalog
from poseidon.core.config import settings


def write_grid(ds: xr.Dataset, *, collection: str, domain: str, cycle_label: str,
               catalog: Catalog, source_id: str) -> Path:
    path = settings.zarr_root / collection / domain / f"{cycle_label}.zarr"
    path.parent.mkdir(parents=True, exist_ok=True)

    ny = ds.sizes.get("latitude", 1)
    nx = ds.sizes.get("longitude", 1)
    chunks = (1, min(256, ny), min(256, nx))  # ADR-003: time=1, y·x≤256
    encoding = {
        v: {"chunks": chunks}
        for v in ds.data_vars
        if ds[v].dims == ("time", "latitude", "longitude")
    }
    ds.to_zarr(path, mode="w", consolidated=False, encoding=encoding)

    retrieval = {}
    if "poseidon_lineage" in ds.attrs:
        try:
            retrieval = ast.literal_eval(ds.attrs["poseidon_lineage"])
        except (ValueError, SyntaxError):
            retrieval = {"raw": ds.attrs["poseidon_lineage"]}
    catalog.register_dataset(collection=collection, domain=domain, cycle=cycle_label,
                             uri=str(path), source_id=source_id, retrieval=retrieval)
    return path


def write_obs(df: pd.DataFrame, *, provider: str, catalog: Catalog) -> list[Path]:
    """long-format 관측(qc_flag 포함)을 월 파티션에 병합 (중복 제거, 멱등)."""
    required = {"station_id", "ts", "var", "value", "qc_flag"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"obs frame missing columns: {missing}")

    written: list[Path] = []
    df = df.copy()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    for (yyyy, mm), part in df.groupby([df["ts"].dt.year, df["ts"].dt.month]):
        pdir = settings.parquet_root / "obs" / provider / f"{yyyy:04d}"
        pdir.mkdir(parents=True, exist_ok=True)
        path = pdir / f"{mm:02d}.parquet"
        if path.exists():
            prev = pd.read_parquet(path)
            part = pd.concat([prev, part], ignore_index=True)
        part = (part.drop_duplicates(subset=["station_id", "ts", "var"], keep="last")
                    .sort_values(["station_id", "var", "ts"]))
        part.to_parquet(path, index=False)
        catalog.register_dataset(collection="obs", domain=provider,
                                 cycle=f"{yyyy:04d}-{mm:02d}", uri=str(path),
                                 source_id=provider,
                                 retrieval={"rows_appended": int(len(df))})
        written.append(path)
    return written
