"""공통 도메인 타입 (PHASE2 §3.5)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

CYCLE_HOURS = (0, 6, 12, 18)


@dataclass(frozen=True, slots=True)
class Cycle:
    """예보 사이클 (UTC, 00/06/12/18Z)."""

    t0: datetime

    def __post_init__(self) -> None:
        if self.t0.tzinfo is None or self.t0.utcoffset() != timedelta(0):
            raise ValueError("Cycle.t0 must be timezone-aware UTC")
        if self.t0.hour not in CYCLE_HOURS or self.t0.minute or self.t0.second:
            raise ValueError(f"Cycle hour must be one of {CYCLE_HOURS}, got {self.t0}")

    @classmethod
    def latest(cls, now: datetime | None = None,
               availability_lag: timedelta = timedelta(hours=5, minutes=30)) -> Cycle:
        """현재 시각 기준, 산출물이 이미 공개되었을 가장 최근 사이클."""
        now = now or datetime.now(timezone.utc)
        t = now - availability_lag
        hour = max(h for h in CYCLE_HOURS if h <= t.hour)
        return cls(t.replace(hour=hour, minute=0, second=0, microsecond=0))

    def previous(self) -> Cycle:
        return Cycle(self.t0 - timedelta(hours=6))

    @property
    def ymd(self) -> str:
        return self.t0.strftime("%Y%m%d")

    @property
    def hh(self) -> str:
        return self.t0.strftime("%H")

    @property
    def label(self) -> str:
        return self.t0.strftime("%Y%m%dT%H")


@dataclass(frozen=True, slots=True)
class BBox:
    """경위도 박스 (도 단위, west < east, south < north)."""

    west: float
    south: float
    east: float
    north: float

    def __post_init__(self) -> None:
        if not (-180 <= self.west < self.east <= 360):
            raise ValueError(f"invalid lon range: {self.west}..{self.east}")
        if not (-90 <= self.south < self.north <= 90):
            raise ValueError(f"invalid lat range: {self.south}..{self.north}")

    def contains(self, lat: float, lon: float) -> bool:
        return self.south <= lat <= self.north and self.west <= lon <= self.east


EAST_ASIA = BBox(west=100.0, south=15.0, east=150.0, north=50.0)
