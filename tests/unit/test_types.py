from datetime import datetime, timedelta, timezone

import pytest

from poseidon.core.types import BBox, Cycle


def test_cycle_rejects_naive_and_bad_hour():
    with pytest.raises(ValueError):
        Cycle(datetime(2026, 8, 3, 6))  # naive
    with pytest.raises(ValueError):
        Cycle(datetime(2026, 8, 3, 7, tzinfo=timezone.utc))  # not a cycle hour


def test_cycle_latest_respects_availability_lag():
    now = datetime(2026, 8, 3, 15, 42, tzinfo=timezone.utc)
    c = Cycle.latest(now=now, availability_lag=timedelta(hours=5, minutes=30))
    # 15:42 - 5:30 = 10:12 → 최근 사이클 06Z
    assert c.t0 == datetime(2026, 8, 3, 6, tzinfo=timezone.utc)
    assert c.label == "20260803T06"
    assert c.previous().t0.hour == 0


def test_cycle_latest_crosses_midnight():
    now = datetime(2026, 8, 3, 2, 0, tzinfo=timezone.utc)
    c = Cycle.latest(now=now, availability_lag=timedelta(hours=5, minutes=30))
    assert c.t0 == datetime(2026, 8, 2, 18, tzinfo=timezone.utc)


def test_bbox_validation_and_contains():
    with pytest.raises(ValueError):
        BBox(150, 15, 100, 50)  # west > east
    b = BBox(100, 15, 150, 50)
    assert b.contains(35.1, 129.0)      # 부산 근해
    assert not b.contains(35.1, 170.0)
