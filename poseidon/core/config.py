"""전역 설정. 환경변수 POSEIDON_* 로 재정의."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from poseidon.core.types import EAST_ASIA, BBox

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    data_root: Path = field(
        default_factory=lambda: Path(os.environ.get("POSEIDON_DATA_ROOT", _PROJECT_ROOT / "data"))
    )
    domain_name: str = "east-asia"
    domain_bbox: BBox = EAST_ASIA
    forecast_steps: tuple[int, ...] = tuple(range(0, 73, 3))  # 0..72h, 3h 간격
    http_timeout_s: float = 90.0
    http_retries: int = 3
    http_concurrency: int = 3

    @property
    def zarr_root(self) -> Path:
        return self.data_root / "zarr"

    @property
    def parquet_root(self) -> Path:
        return self.data_root / "parquet"

    @property
    def catalog_path(self) -> Path:
        return self.data_root / "catalog.sqlite"


settings = Settings()
